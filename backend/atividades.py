"""Atividades e follow-up — o que faz o CRM ser aberto todo dia.

Ficha e funil guardam o que **já** aconteceu. Atividade guarda o que ainda
precisa acontecer: ligar quinta, mandar a proposta, cobrar resposta. Sem isso o
próximo passo mora na cabeça do vendedor, e um CRM sem próximo passo vira
cadastro morto — bonito na tela, abandonado na prática.

Três decisões moldam este módulo:

  - **"Atrasada" não é um campo, é uma comparação.** O estado guardado é só
    `concluida_em` (nulo = pendente); atrasada é `pendente e vence_em < agora`.
    Gravar um status "atrasada" exigiria uma varredura periódica para virar a
    chave de cada atividade na hora certa — trabalho de fundo para produzir
    exatamente o que uma comparação de data já responde.

  - **Os limites do dia e da semana vêm do cliente.** "Hoje" em UTC-3 começa
    três horas antes de "hoje" em UTC, e o servidor não sabe o fuso de quem
    abriu a tela. Quem sabe é o navegador, então é ele que manda o corte
    (`ate`) e o backend só filtra. Calcular aqui faria a agenda de fim de dia
    mostrar as tarefas de amanhã.

  - **O follow-up é encadeado na conclusão.** `concluir` aceita a próxima
    atividade no mesmo pedido, porque é ali que a pessoa está pensando no
    assunto. Deixar para "depois eu agendo" é como se perde a venda.
"""
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("turismo.atividades")

# Tipos são só rótulo + ícone na tela; nenhum comportamento depende do tipo.
# Guardados como lista fechada para o relatório não virar texto livre.
TIPOS = ("ligacao", "mensagem", "reuniao", "tarefa")


def _now():
    return datetime.now(timezone.utc).isoformat()


def com_atraso(atividade: dict, agora: str | None = None) -> dict:
    """Acrescenta `atrasada` ao documento que vai para a tela."""
    agora = agora or _now()
    atividade["atrasada"] = bool(
        not atividade.get("concluida_em")
        and atividade.get("vence_em")
        and atividade["vence_em"] < agora
    )
    return atividade


async def negocios_sem_proximo_passo(db, tenant_id: str, deal_ids: list[str]) -> set[str]:
    """Dos negócios informados, quais **não** têm atividade pendente.

    É o sinal mais útil de um funil: cartão sem próximo passo é venda parada
    sem ninguém ter decidido pará-la. Vem de uma agregação só sobre os ids em
    tela — uma consulta por cartão custaria uma ida ao banco por negócio.
    """
    if not deal_ids:
        return set()
    cursor = db.activities.aggregate([
        {"$match": {"tenant_id": tenant_id, "deal_id": {"$in": deal_ids},
                    "concluida_em": None}},
        {"$group": {"_id": "$deal_id", "n": {"$sum": 1}}},
    ])
    com_pendencia = {row["_id"] async for row in cursor}
    return set(deal_ids) - com_pendencia


async def resumo(db, tenant_id: str, responsavel_id: str | None,
                 hoje_ate: str | None, semana_ate: str | None) -> dict:
    """Contadores da agenda: atrasadas, para hoje, para a semana, total aberto.

    As quatro contagens em paralelo seriam quatro idas ao banco; aqui elas
    saem de uma agregação só, com `$cond` decidindo em qual balde cada
    atividade cai. O que domina não é o volume, é a ida e volta — o banco está
    atrás de um túnel SSH.
    """
    agora = _now()
    filtro: dict = {"tenant_id": tenant_id, "concluida_em": None}
    if responsavel_id:
        filtro["responsavel_id"] = responsavel_id

    cursor = db.activities.aggregate([
        {"$match": filtro},
        {"$group": {
            "_id": None,
            "pendentes": {"$sum": 1},
            "atrasadas": {"$sum": {"$cond": [{"$lt": ["$vence_em", agora]}, 1, 0]}},
            "hoje": {"$sum": {"$cond": [
                {"$and": [{"$gte": ["$vence_em", agora]},
                          {"$lte": ["$vence_em", hoje_ate or agora]}]}, 1, 0]}},
            "semana": {"$sum": {"$cond": [
                {"$and": [{"$gte": ["$vence_em", agora]},
                          {"$lte": ["$vence_em", semana_ate or agora]}]}, 1, 0]}},
        }},
    ])
    async for row in cursor:
        return {"pendentes": row.get("pendentes", 0), "atrasadas": row.get("atrasadas", 0),
                "hoje": row.get("hoje", 0), "semana": row.get("semana", 0)}
    return {"pendentes": 0, "atrasadas": 0, "hoje": 0, "semana": 0}


def novo(tenant_id: str, autor: dict, *, titulo: str, tipo: str, vence_em: str,
         descricao: str | None = None, responsavel_id: str | None = None,
         contact_id: str | None = None, deal_id: str | None = None,
         origem_id: str | None = None) -> dict:
    """Monta o documento da atividade.

    `responsavel_id` cai em quem criou quando não é informado: atividade sem
    dono não aparece na agenda de ninguém, e é assim que uma tarefa
    desaparece.
    """
    return {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "titulo": titulo,
        "tipo": tipo if tipo in TIPOS else "tarefa",
        "descricao": descricao or None,
        "vence_em": vence_em,
        "responsavel_id": responsavel_id or autor["id"],
        "contact_id": contact_id,
        "deal_id": deal_id,
        "concluida_em": None,
        "concluida_por": None,
        # Preenchido quando esta atividade nasceu da conclusão de outra: é o
        # que permite ver a corrente de follow-up de um assunto.
        "origem_id": origem_id,
        "criado_por": autor["id"],
        "created_at": _now(),
        "updated_at": _now(),
    }
