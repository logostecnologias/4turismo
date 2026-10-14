"""API do funil de negócios: funis, etapas, negócios e relatório."""
import re
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import atividades
import datas
import deals as dominio
from auth import require_tenant_admin, require_tenant_member
from db import get_db

router = APIRouter(prefix="/api", tags=["deals"])

# Teto de cartões devolvidos por coluna. O quadro precisa abrir rápido, e uma
# coluna "Novo lead" com 4.000 negócios não caberia na tela nem na resposta. O
# total real vai junto (`n`), para a tela dizer "mostrando 100 de 4.000" em vez
# de fingir que aquilo é a coluna inteira.
POR_COLUNA = 100


def _now():
    return datetime.now(timezone.utc).isoformat()


class EtapaIn(BaseModel):
    # `id` ausente = etapa nova. Vindo preenchido, é edição da que já existe —
    # e é o id que mantém os negócios ligados a ela depois de um rename.
    id: str | None = None
    nome: str
    cor: str = Field(default="#4F6B78", pattern=r"^#[0-9A-Fa-f]{6}$")
    tipo: str = Field(default="aberta", pattern="^(aberta|ganho|perdido)$")


class PipelineIn(BaseModel):
    nome: str
    etapas: list[EtapaIn] | None = None


class DealIn(BaseModel):
    titulo: str
    contact_id: str
    pipeline_id: str
    etapa_id: str | None = None
    valor: float = 0
    responsavel_id: str | None = None
    previsao_fechamento: str | None = None
    session_id: str | None = None
    origem: str | None = None


class DealPatch(BaseModel):
    titulo: str | None = None
    valor: float | None = None
    etapa_id: str | None = None
    motivo_perda: str | None = None
    responsavel_id: str | None = None
    previsao_fechamento: str | None = None
    origem: str | None = None
    ordem: int | None = None


async def _get_pipeline(db, pipeline_id: str, tenant_id: str) -> dict:
    doc = await db.pipelines.find_one({"id": pipeline_id, "tenant_id": tenant_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Quadro de orçamentos não encontrado")
    return doc


async def _get_deal(db, deal_id: str, tenant_id: str) -> dict:
    doc = await db.deals.find_one({"id": deal_id, "tenant_id": tenant_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Orçamento não encontrado")
    return doc


async def _valida_responsavel(db, tenant_id: str, user_id: str | None):
    if not user_id:
        return
    # Sem esta checagem, um id de usuário de outra empresa colado no corpo
    # apareceria como responsável do negócio.
    if not await db.users.find_one({"id": user_id, "tenant_id": tenant_id}, {"_id": 0, "id": 1}):
        raise HTTPException(status_code=400, detail="Responsável inválido")


# ---------- Funis ----------


@router.get("/pipelines")
async def list_pipelines(user=Depends(require_tenant_member)):
    """Funis da empresa. Cria o padrão na primeira visita.

    Semear aqui, e não no cadastro da empresa, cobre também as empresas que já
    existiam antes de o funil existir — elas abririam a tela num quadro sem
    coluna nenhuma, sem pista de que precisavam inventar as etapas.
    """
    db = get_db()
    funis = await db.pipelines.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", 1).to_list(50)
    if not funis:
        # Mesmo semeio de quem abre orçamento por fora da tela (o site).
        funis = [await dominio.funil_padrao(db, user["tenant_id"])]
    return funis


def _monta_etapas(entradas: list[EtapaIn]) -> list[dict]:
    return [
        {"id": e.id or str(uuid.uuid4()), "nome": e.nome.strip() or "Etapa",
         "cor": e.cor, "tipo": e.tipo, "ordem": i}
        for i, e in enumerate(entradas)
    ]


@router.post("/pipelines")
async def create_pipeline(body: PipelineIn, user=Depends(require_tenant_admin)):
    db = get_db()
    etapas = _monta_etapas(body.etapas) if body.etapas else dominio.etapas_padrao()
    if not etapas:
        raise HTTPException(status_code=400, detail="O quadro de orçamentos precisa de ao menos uma etapa")
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": user["tenant_id"],
        "nome": body.nome.strip(),
        "etapas": etapas,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.pipelines.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


@router.patch("/pipelines/{pipeline_id}")
async def update_pipeline(pipeline_id: str, body: PipelineIn, mover_para: str | None = None,
                          user=Depends(require_tenant_admin)):
    """Renomeia o funil e reescreve a lista de etapas.

    Etapa removida que ainda tem negócio **não** é apagada em silêncio: os
    negócios ficariam apontando para uma etapa inexistente e desapareceriam do
    quadro sem nunca terem sido perdidos nem ganhos. Ou se informa
    `mover_para`, ou a mudança é recusada dizendo quantos negócios estão lá.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    atual = await _get_pipeline(db, pipeline_id, tenant_id)

    campos: dict = {"nome": body.nome.strip(), "updated_at": _now()}
    if body.etapas is not None:
        novas = _monta_etapas(body.etapas)
        if not novas:
            raise HTTPException(status_code=400, detail="O quadro de orçamentos precisa de ao menos uma etapa")
        ids_novos = {e["id"] for e in novas}
        removidas = [e for e in (atual.get("etapas") or []) if e["id"] not in ids_novos]

        for etapa in removidas:
            quantos = await db.deals.count_documents(
                {"tenant_id": tenant_id, "pipeline_id": pipeline_id, "etapa_id": etapa["id"]}
            )
            if not quantos:
                continue
            destino = dominio.achar_etapa({"etapas": novas}, mover_para or "")
            if not destino:
                raise HTTPException(
                    status_code=409,
                    detail=(f"A etapa \"{etapa['nome']}\" tem {quantos} orçamento(s). "
                            "Escolha para qual etapa movê-los antes de removê-la."),
                )
            movidos = dict()
            dominio.aplicar_etapa(movidos, destino)
            movidos["updated_at"] = _now()
            await db.deals.update_many(
                {"tenant_id": tenant_id, "pipeline_id": pipeline_id, "etapa_id": etapa["id"]},
                {"$set": movidos},
            )
        campos["etapas"] = novas

    await db.pipelines.update_one({"id": pipeline_id, "tenant_id": tenant_id}, {"$set": campos})
    return await _get_pipeline(db, pipeline_id, tenant_id)


@router.delete("/pipelines/{pipeline_id}")
async def delete_pipeline(pipeline_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_pipeline(db, pipeline_id, tenant_id)
    quantos = await db.deals.count_documents({"tenant_id": tenant_id, "pipeline_id": pipeline_id})
    if quantos:
        raise HTTPException(
            status_code=409,
            detail=f"Este quadro tem {quantos} orçamento(s). Mova ou exclua antes de apagar o quadro.",
        )
    await db.pipelines.delete_one({"id": pipeline_id, "tenant_id": tenant_id})
    return {"ok": True}


# ---------- Quadro e relatório ----------
#
# Declarados antes de /deals/{deal_id}: no mesmo nível de rota, "board" e
# "metrics" seriam capturados como se fossem o id de um negócio.


@router.get("/deals/board")
async def board(pipeline_id: str, responsavel_id: str | None = None, q: str | None = None,
                user=Depends(require_tenant_member)):
    """O quadro: uma coluna por etapa, com os cartões e os totais de cada uma."""
    db = get_db()
    tenant_id = user["tenant_id"]
    pipeline = await _get_pipeline(db, pipeline_id, tenant_id)

    filtro: dict = {"tenant_id": tenant_id, "pipeline_id": pipeline_id}
    if responsavel_id:
        filtro["responsavel_id"] = responsavel_id
    if q:
        # `re.escape` porque o termo vai para dentro de uma regex do Mongo: um
        # "(" digitado na busca derrubaria a consulta com erro de sintaxe.
        filtro["titulo"] = {"$regex": re.escape(q.strip()), "$options": "i"}

    # Os totais saem de uma agregação sobre a coluna inteira, não da soma dos
    # cartões devolvidos: com o teto por coluna, somar o que veio mostraria
    # "R$ 30 mil" numa coluna que tem R$ 200 mil.
    totais = await dominio.por_etapa(db, tenant_id, pipeline_id)

    # Com filtro ativo o agregado (que ignora o filtro) mentiria, então os
    # totais da coluna passam a ser os do recorte filtrado.
    filtrando = bool(responsavel_id or q)

    colunas = []
    ids_contato = set()
    for etapa in sorted(pipeline.get("etapas") or [], key=lambda e: e.get("ordem", 0)):
        da_coluna = {**filtro, "etapa_id": etapa["id"]}
        cartoes = await db.deals.find(da_coluna, {"_id": 0}) \
            .sort([("ordem", 1), ("id", 1)]).limit(POR_COLUNA).to_list(POR_COLUNA)
        for c in cartoes:
            ids_contato.add(c.get("contact_id"))

        if filtrando:
            # Contado de novo, e não `len(cartoes)`: com mais de POR_COLUNA
            # resultados, contar o que veio esconderia o corte — a tela diria
            # "100" numa coluna de 400 e ninguém saberia que faltava algo.
            n = await db.deals.count_documents(da_coluna)
            valor = sum(c.get("valor") or 0 for c in cartoes)
        else:
            agregado = totais.get(etapa["id"], {"n": 0, "valor": 0})
            n, valor = agregado["n"], agregado["valor"]

        colunas.append({
            "etapa": etapa,
            "deals": cartoes,
            "n": n,
            "valor": valor,
            "truncada": n > len(cartoes),
            # Com filtro, a soma vale só para os cartões devolvidos; a tela não
            # deve apresentá-la como o total da coluna.
            "valor_parcial": filtrando and n > len(cartoes),
        })

    contatos = {
        c["id"]: c
        async for c in db.contacts.find(
            {"id": {"$in": [i for i in ids_contato if i]}, "tenant_id": tenant_id},
            {"_id": 0, "id": 1, "nome": 1, "numero_whatsapp": 1, "empresa": 1,
             # As duas origens de foto: a nossa (arquivo servido por
             # /api/media) e a externa. Projetar só uma deixaria o cartão
             # do funil com iniciais enquanto a Central mostra a foto.
             "foto_url": 1, "foto_media_id": 1},
        )
    }
    # Cartão sem atividade pendente é venda parada sem ninguém ter decidido
    # parar. Sai de uma agregação só sobre os ids em tela — uma consulta por
    # cartão custaria uma ida ao banco por negócio.
    em_tela = [c["id"] for coluna in colunas for c in coluna["deals"]]
    sem_passo = await atividades.negocios_sem_proximo_passo(db, tenant_id, em_tela)

    for coluna in colunas:
        for c in coluna["deals"]:
            c["contact"] = contatos.get(c.get("contact_id"))
            # Só cobra próximo passo de negócio aberto: fechado não precisa.
            c["sem_proximo_passo"] = c["id"] in sem_passo and c.get("status") == "aberto"

    return {"pipeline": pipeline, "colunas": colunas}


@router.get("/deals/metrics")
async def metrics(pipeline_id: str, desde: str | None = None, user=Depends(require_tenant_member)):
    db = get_db()
    await _get_pipeline(db, pipeline_id, user["tenant_id"])
    return await dominio.metricas(db, user["tenant_id"], pipeline_id,
                                  datas.para_utc(desde, "Data inicial"))


# ---------- Negócios ----------


@router.get("/deals")
async def list_deals(pipeline_id: str | None = None, contact_id: str | None = None,
                     status: str | None = None, responsavel_id: str | None = None,
                     skip: int = 0, limit: int = 100, user=Depends(require_tenant_member)):
    db = get_db()
    filtro: dict = {"tenant_id": user["tenant_id"]}
    for chave, valor in (("pipeline_id", pipeline_id), ("contact_id", contact_id),
                         ("status", status), ("responsavel_id", responsavel_id)):
        if valor:
            filtro[chave] = valor
    limit = max(1, min(limit, 500))
    items = await db.deals.find(filtro, {"_id": 0}).sort([("updated_at", -1), ("id", 1)]) \
        .skip(max(0, skip)).limit(limit).to_list(limit)
    return {"total": await db.deals.count_documents(filtro), "items": items}


@router.post("/deals")
async def create_deal(body: DealIn, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    pipeline = await _get_pipeline(db, body.pipeline_id, tenant_id)

    contact = await db.contacts.find_one(
        {"id": body.contact_id, "tenant_id": tenant_id}, {"_id": 0, "id": 1, "nome": 1}
    )
    if not contact:
        raise HTTPException(status_code=400, detail="Cliente inválido")
    await _valida_responsavel(db, tenant_id, body.responsavel_id)

    etapas = sorted(pipeline.get("etapas") or [], key=lambda e: e.get("ordem", 0))
    etapa = dominio.achar_etapa(pipeline, body.etapa_id) if body.etapa_id else (etapas[0] if etapas else None)
    if not etapa:
        raise HTTPException(status_code=400, detail="Etapa inválida")

    if body.valor < 0:
        raise HTTPException(status_code=400, detail="Valor não pode ser negativo")

    # A conversa de onde o negócio nasceu, se veio de uma. Conferida contra a
    # empresa para não guardar o id de uma conversa alheia.
    session_id = None
    if body.session_id and await db.conversation_sessions.find_one(
            {"id": body.session_id, "tenant_id": tenant_id}, {"_id": 0, "id": 1}):
        session_id = body.session_id
    # No fim da coluna, como em qualquer Kanban: o cartão novo não pode
    # empurrar para baixo o que alguém já tinha priorizado no topo.
    ultimo = await db.deals.find(
        {"tenant_id": tenant_id, "pipeline_id": body.pipeline_id, "etapa_id": etapa["id"]},
        {"_id": 0, "ordem": 1},
    ).sort([("ordem", -1)]).limit(1).to_list(1)

    deal = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "pipeline_id": body.pipeline_id,
        "titulo": body.titulo.strip() or f"Negócio — {contact['nome']}",
        "contact_id": body.contact_id,
        "valor": float(body.valor),
        "responsavel_id": body.responsavel_id,
        "previsao_fechamento": datas.valida_data(body.previsao_fechamento, "Previsão de fechamento"),
        "origem": (body.origem or "").strip() or None,
        "session_id": session_id,
        "motivo_perda": None,
        "fechado_em": None,
        "ciclo_dias": None,
        "ordem": ((ultimo[0].get("ordem") or 0) + 1) if ultimo else 0,
        "criado_por": user["id"],
        "created_at": _now(),
        "updated_at": _now(),
    }
    dominio.aplicar_etapa(deal, etapa)
    await db.deals.insert_one(dict(deal))
    deal.pop("_id", None)
    await dominio.registrar_evento(db, deal, "criado", user,
                                   para=etapa["nome"], valor_para=deal["valor"])
    return deal


@router.get("/deals/{deal_id}")
async def deal_detail(deal_id: str, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    deal = await _get_deal(db, deal_id, tenant_id)
    contact = await db.contacts.find_one({"id": deal["contact_id"], "tenant_id": tenant_id}, {"_id": 0})
    responsavel = None
    if deal.get("responsavel_id"):
        responsavel = await db.users.find_one(
            {"id": deal["responsavel_id"], "tenant_id": tenant_id},
            {"_id": 0, "id": 1, "name": 1, "email": 1},
        )
    # Desempate por `_id`: dois eventos do mesmo salvamento nascem com
    # `created_at` idêntico (resolução do relógio), e só com o timestamp a
    # ordem do histórico é indefinida.
    eventos = await db.deal_events.find(
        {"tenant_id": tenant_id, "deal_id": deal_id}, {"_id": 0}
    ).sort([("created_at", -1), ("_id", -1)]).to_list(200)
    return {"deal": deal, "contact": contact, "responsavel": responsavel,
            "eventos": eventos}


@router.patch("/deals/{deal_id}")
async def update_deal(deal_id: str, body: DealPatch, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    deal = await _get_deal(db, deal_id, tenant_id)
    pipeline = await _get_pipeline(db, deal["pipeline_id"], tenant_id)

    campos: dict = {}
    eventos: list[tuple] = []

    if body.titulo is not None:
        titulo = body.titulo.strip()
        if not titulo:
            raise HTTPException(status_code=400, detail="Título não pode ficar vazio")
        campos["titulo"] = titulo
    if body.valor is not None:
        if body.valor < 0:
            raise HTTPException(status_code=400, detail="Valor não pode ser negativo")
        if float(body.valor) != float(deal.get("valor") or 0):
            eventos.append(("valor", {"valor_de": deal.get("valor"), "valor_para": float(body.valor)}))
        campos["valor"] = float(body.valor)
    if body.responsavel_id is not None:
        await _valida_responsavel(db, tenant_id, body.responsavel_id or None)
        campos["responsavel_id"] = body.responsavel_id or None
    if body.previsao_fechamento is not None:
        campos["previsao_fechamento"] = datas.valida_data(body.previsao_fechamento,
                                                            "Previsão de fechamento")
    if body.origem is not None:
        campos["origem"] = body.origem.strip() or None
    if body.ordem is not None:
        campos["ordem"] = int(body.ordem)
    if body.motivo_perda is not None and body.etapa_id is None:
        # Corrigir o motivo sem mover de etapa. Só vale em negócio perdido —
        # gravar motivo de perda num negócio ganho é ruído no relatório.
        if deal.get("status") != "perdido":
            raise HTTPException(status_code=400,
                                detail="Motivo de não aprovação só se aplica a orçamento não aprovado")
        campos["motivo_perda"] = body.motivo_perda.strip() or None

    if body.etapa_id is not None and body.etapa_id != deal.get("etapa_id"):
        etapa = dominio.achar_etapa(pipeline, body.etapa_id)
        if not etapa:
            raise HTTPException(status_code=400, detail="Etapa inválida")
        anterior = dominio.achar_etapa(pipeline, deal.get("etapa_id")) or {}
        dominio.aplicar_etapa(campos, etapa, body.motivo_perda)
        # O ciclo é gravado no fechamento porque o `$avg` do Mongo não subtrai
        # duas datas guardadas como string ISO.
        campos["ciclo_dias"] = dominio.ciclo_dias({**deal, **campos})
        if "ordem" not in campos:
            ultimo = await db.deals.find(
                {"tenant_id": tenant_id, "pipeline_id": deal["pipeline_id"],
                 "etapa_id": etapa["id"]},
                {"_id": 0, "ordem": 1},
            ).sort([("ordem", -1)]).limit(1).to_list(1)
            campos["ordem"] = ((ultimo[0].get("ordem") or 0) + 1) if ultimo else 0
        eventos.append((campos["status"] if campos["status"] != "aberto" else "etapa",
                        {"de": anterior.get("nome"), "para": etapa["nome"],
                         "motivo": campos.get("motivo_perda")}))

    if not campos:
        raise HTTPException(status_code=400, detail="Nada para atualizar")
    campos["updated_at"] = _now()
    await db.deals.update_one({"id": deal_id, "tenant_id": tenant_id}, {"$set": campos})
    for tipo, extra in eventos:
        await dominio.registrar_evento(db, deal, tipo, user, **extra)
    return await _get_deal(db, deal_id, tenant_id)


@router.delete("/deals/{deal_id}")
async def delete_deal(deal_id: str, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get_deal(db, deal_id, tenant_id)
    await db.deals.delete_one({"id": deal_id, "tenant_id": tenant_id})
    await db.deal_events.delete_many({"deal_id": deal_id, "tenant_id": tenant_id})
    return {"ok": True}
