"""Funil de negócios — a peça que faltava para o produto ser um CRM.

O Kanban da Central mostra **estado de conversa** (`bot` → `aguardando` →
`em_atendimento` → `finalizado`). Isso não é funil: não tem valor, não tem
previsão, não tem dono e não converte em nada. Um negócio é outra entidade,
com etapa de venda, valor, responsável e um desfecho — e é dela que saem as
únicas contas que uma agência cobra: taxa de conversão, ticket médio e ciclo de
venda.

Três decisões moldam este módulo:

  - **As etapas ficam embutidas no funil** (`pipelines.etapas`), não numa
    coleção separada. São poucas, ordenadas, e nunca são lidas sem o funil —
    uma coleção à parte só acrescentaria uma consulta a cada abertura do
    quadro. Cada etapa tem `tipo` (`aberta`/`ganho`/`perdido`), que é o que
    diz ao sistema quando o negócio fechou.

  - **`status` é gravado no negócio, mesmo sendo derivado da etapa.** Ele
    duplica informação de propósito: "negócios abertos" e "ganhos no mês" são
    as consultas quentes, e não há como indexar um valor que mora dentro de um
    array de outra coleção. Quem muda a etapa passa por `aplicar_etapa`, que
    mantém os dois lados coerentes.

  - **Toda mudança gera evento** (`deal_events`). Sem o histórico não existe
    ciclo de venda nem tempo por etapa — e "por que perdemos" viraria opinião
    em vez de relatório.
"""
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("turismo.deals")

TIPOS_ETAPA = ("aberta", "ganho", "perdido")

# Espelham `tipo` da etapa. `aberto` é o único estado em que o negócio conta
# como pipeline (valor em jogo); os outros dois são desfecho.
STATUS = ("aberto", "ganho", "perdido")

# Funil semeado junto com a empresa que cria o primeiro: quem chega numa tela
# de Kanban vazia não tem como saber que precisa inventar as colunas antes.
ETAPAS_PADRAO = [
    ("Novo cliente", "#4F6B78", "aberta"),
    ("Passeio escolhido", "#16B3AC", "aberta"),
    ("Orçamento enviado", "#F5B841", "aberta"),
    ("Em negociação", "#0B6E8A", "aberta"),
    ("Aprovado", "#10B981", "ganho"),
    ("Não aprovado", "#EF4444", "perdido"),
]


def _now():
    return datetime.now(timezone.utc).isoformat()


def etapas_padrao() -> list[dict]:
    return [
        {"id": str(uuid.uuid4()), "nome": nome, "cor": cor, "tipo": tipo, "ordem": i}
        for i, (nome, cor, tipo) in enumerate(ETAPAS_PADRAO)
    ]


def achar_etapa(pipeline: dict, etapa_id: str) -> dict | None:
    for e in pipeline.get("etapas") or []:
        if e["id"] == etapa_id:
            return e
    return None


def status_da_etapa(etapa: dict) -> str:
    tipo = (etapa or {}).get("tipo", "aberta")
    return {"ganho": "ganho", "perdido": "perdido"}.get(tipo, "aberto")


def aplicar_etapa(campos: dict, etapa: dict, motivo_perda: str | None = None) -> dict:
    """Preenche em `campos` tudo que depende da etapa de destino.

    Um só lugar decide isso porque são três campos que precisam concordar
    (`etapa_id`, `status`, `fechado_em`): mover para "Ganho" sem gravar a data
    de fechamento deixaria o negócio fora de todo relatório de ciclo de venda,
    e voltar de "Perdido" para uma etapa aberta sem limpar a data manteria o
    negócio contado como fechado para sempre.
    """
    status = status_da_etapa(etapa)
    campos["etapa_id"] = etapa["id"]
    campos["status"] = status
    if status == "aberto":
        campos["fechado_em"] = None
        campos["motivo_perda"] = None
    else:
        campos["fechado_em"] = _now()
        # O motivo só existe na perda. Gravá-lo num ganho seria ruído no
        # relatório de motivos de perda.
        campos["motivo_perda"] = (motivo_perda or None) if status == "perdido" else None
    return campos


async def registrar_evento(db, deal: dict, tipo: str, autor: dict, **extra):
    """Uma linha do histórico do negócio.

    Nunca levanta: perder um evento de auditoria é ruim, mas impedir a venda de
    avançar porque o log falhou é pior.
    """
    try:
        await db.deal_events.insert_one({
            "id": str(uuid.uuid4()),
            "tenant_id": deal["tenant_id"],
            "deal_id": deal["id"],
            "tipo": tipo,
            "autor_id": (autor or {}).get("id"),
            "autor_nome": (autor or {}).get("name") or (autor or {}).get("email"),
            "created_at": _now(),
            **extra,
        })
    except Exception as e:
        logger.warning("Falha ao registrar evento %s do negócio %s: %s", tipo, deal.get("id"), e)


# ---------- Relatórios ----------


async def por_etapa(db, tenant_id: str, pipeline_id: str) -> dict[str, dict]:
    """Contagem e soma de valor por etapa, numa consulta agrupada.

    Agrupada e não uma consulta por coluna: o quadro tem seis colunas e o banco
    está atrás de um túnel SSH — o que domina é a ida e volta, não o volume.
    """
    cursor = db.deals.aggregate([
        {"$match": {"tenant_id": tenant_id, "pipeline_id": pipeline_id}},
        {"$group": {"_id": "$etapa_id", "n": {"$sum": 1}, "valor": {"$sum": "$valor"}}},
    ])
    return {row["_id"]: {"n": row["n"], "valor": row.get("valor") or 0} async for row in cursor}


async def metricas(db, tenant_id: str, pipeline_id: str, desde: str | None = None) -> dict:
    """Números do funil: conversão, ticket médio e ciclo."""
    base: dict = {"tenant_id": tenant_id, "pipeline_id": pipeline_id}
    fechados = dict(base)
    if desde:
        # A janela filtra pelo fechamento, não pela criação: "ganhamos R$ X em
        # agosto" é sobre o que fechou em agosto, mesmo que tenha nascido antes.
        fechados["fechado_em"] = {"$gte": desde}

    resumo = {"aberto": {"n": 0, "valor": 0}, "ganho": {"n": 0, "valor": 0},
              "perdido": {"n": 0, "valor": 0}}
    cursor = db.deals.aggregate([
        {"$match": base},
        {"$group": {"_id": "$status", "n": {"$sum": 1}, "valor": {"$sum": "$valor"}}},
    ])
    async for row in cursor:
        if row["_id"] in resumo:
            resumo[row["_id"]] = {"n": row["n"], "valor": row.get("valor") or 0}

    janela = {"ganho": {"n": 0, "valor": 0}, "perdido": {"n": 0, "valor": 0}}
    cursor = db.deals.aggregate([
        {"$match": {**fechados, "status": {"$in": ["ganho", "perdido"]}}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}, "valor": {"$sum": "$valor"},
                    "ciclo": {"$avg": "$ciclo_dias"}}},
    ])
    async for row in cursor:
        if row["_id"] in janela:
            janela[row["_id"]] = {"n": row["n"], "valor": row.get("valor") or 0,
                                  "ciclo_medio": row.get("ciclo")}

    ganhos, perdidos = janela["ganho"]["n"], janela["perdido"]["n"]
    decididos = ganhos + perdidos

    motivos = []
    cursor = db.deals.aggregate([
        {"$match": {**fechados, "status": "perdido"}},
        {"$group": {"_id": "$motivo_perda", "n": {"$sum": 1}, "valor": {"$sum": "$valor"}}},
    ])
    async for row in cursor:
        motivos.append({"motivo": row["_id"] or "(não informado)", "n": row["n"],
                        "valor": row.get("valor") or 0})
    motivos.sort(key=lambda x: x["n"], reverse=True)

    return {
        "resumo": resumo,
        "janela": janela,
        # Conversão sobre o que foi **decidido**, não sobre o total: contar os
        # negócios ainda abertos como perda faria a taxa despencar só porque o
        # funil está cheio.
        "conversao": round(ganhos / decididos * 100, 1) if decididos else None,
        "ticket_medio": round(janela["ganho"]["valor"] / ganhos, 2) if ganhos else None,
        "ciclo_medio_dias": (round(janela["ganho"]["ciclo_medio"], 1)
                             if janela["ganho"].get("ciclo_medio") else None),
        "motivos_perda": motivos,
    }


def ciclo_dias(deal: dict) -> float | None:
    """Dias entre a criação e o fechamento. Gravado no negócio ao fechar, e não
    calculado no relatório, porque o `$avg` do Mongo não subtrai duas datas
    guardadas como string ISO — e ISO é o formato do resto do sistema."""
    if not deal.get("fechado_em") or not deal.get("created_at"):
        return None
    try:
        inicio = datetime.fromisoformat(deal["created_at"])
        fim = datetime.fromisoformat(deal["fechado_em"])
    except ValueError:
        return None
    return round((fim - inicio).total_seconds() / 86400, 2)


# ---------- Criação fora da tela ----------

async def funil_padrao(db, tenant_id: str) -> dict:
    """O primeiro funil da empresa, criando o padrão se ela ainda não tem nenhum.

    Mesmo semeio que a tela de Orçamentos faz na primeira visita: quem abre um
    orçamento sozinho (o site, quando o turista chega pelo botão) não pode
    depender de alguém já ter aberto essa tela.
    """
    funis = await db.pipelines.find({"tenant_id": tenant_id}, {"_id": 0})         .sort("created_at", 1).to_list(1)
    if funis:
        return funis[0]
    padrao = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "nome": "Funil de Vendas",
              "etapas": etapas_padrao(), "created_at": _now(), "updated_at": _now()}
    await db.pipelines.insert_one(dict(padrao))
    padrao.pop("_id", None)
    return padrao


async def criar_negocio(db, tenant_id: str, *, contato: dict, titulo: str, valor: float = 0,
                        origem: str = None, session_id: str = None, autor: dict = None,
                        pipeline: dict = None) -> dict:
    """Abre um orçamento na primeira etapa do funil padrão.

    Passa por `aplicar_etapa` e `registrar_evento`, como a rota de criação: um
    negócio criado por fora da tela sem o evento ficaria sem histórico, e o
    ciclo de venda dele seria incalculável.
    """
    pipeline = pipeline or await funil_padrao(db, tenant_id)
    etapas = sorted(pipeline.get("etapas") or [], key=lambda e: e.get("ordem", 0))
    etapa = next((e for e in etapas if e.get("tipo", "aberta") == "aberta"), etapas[0])
    ultimo = await db.deals.find(
        {"tenant_id": tenant_id, "pipeline_id": pipeline["id"], "etapa_id": etapa["id"]},
        {"_id": 0, "ordem": 1},
    ).sort([("ordem", -1)]).limit(1).to_list(1)
    deal = {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id, "pipeline_id": pipeline["id"],
        "titulo": (titulo or "").strip()[:200] or f"Negócio — {contato.get('nome')}",
        "contact_id": contato["id"], "valor": float(max(0, valor or 0)),
        "responsavel_id": None, "previsao_fechamento": None, "origem": origem,
        "session_id": session_id, "motivo_perda": None, "fechado_em": None,
        "ciclo_dias": None, "ordem": ((ultimo[0].get("ordem") or 0) + 1) if ultimo else 0,
        "criado_por": (autor or {}).get("id"), "created_at": _now(), "updated_at": _now(),
    }
    aplicar_etapa(deal, etapa)
    await db.deals.insert_one(dict(deal))
    deal.pop("_id", None)
    await registrar_evento(db, deal, "criado", autor or {}, para=etapa["nome"],
                           valor_para=deal["valor"])
    return deal
