"""Painel inicial da empresa.

Reescrito porque contava pelo campo errado: usava `status` (ativa/finalizada,
o estado do motor de fluxos) para dizer "conversas ativas" e "aguardando
humano", enquanto o Kanban, a Central e os relatórios usam
`status_atendimento`. Os dois campos existem e querem dizer coisas diferentes,
então o painel mostrava números que não fechavam com nenhuma outra tela.

Agora ele resume o que o produto virou: atendimento, funil e agenda.
Cada bloco sai de **uma** agregação, não de uma contagem por número — o que
domina aqui não é o volume, é a ida e volta até o banco.
"""
from datetime import datetime, timezone, timedelta
from fastapi import APIRouter, Depends

import atividades
import hoje
from auth import require_tenant_member
from db import get_db

router = APIRouter(prefix="/api", tags=["dashboard"])

KANBAN = ("bot", "aguardando", "em_atendimento", "finalizado")


def _now():
    return datetime.now(timezone.utc).isoformat()


@router.get("/dashboard/metrics")
async def dashboard_metrics(hoje_ate: str | None = None, semana_ate: str | None = None,
                            user=Depends(require_tenant_member)):
    """Resumo da operação. `hoje_ate`/`semana_ate` vêm do navegador, que é quem
    sabe o fuso de quem está olhando — mesma regra da agenda."""
    db = get_db()
    tenant_id = user["tenant_id"]
    agora = _now()
    inicio_mes = datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0).isoformat()
    trinta_dias = (datetime.now(timezone.utc) - timedelta(days=30)).isoformat()

    # Atendimento: as quatro colunas do Kanban numa agregação só.
    conversas = {s: 0 for s in KANBAN}
    cursor = db.conversation_sessions.aggregate([
        {"$match": {"tenant_id": tenant_id}},
        {"$group": {"_id": "$status_atendimento", "n": {"$sum": 1}}},
    ])
    async for row in cursor:
        if row["_id"] in conversas:
            conversas[row["_id"]] = row["n"]

    # Funil: aberto (o que está em jogo) e ganho no mês (o que entrou).
    funil = {"aberto": {"n": 0, "valor": 0.0}, "ganho_mes": {"n": 0, "valor": 0.0}}
    cursor = db.deals.aggregate([
        {"$match": {"tenant_id": tenant_id, "status": "aberto"}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "valor": {"$sum": "$valor"}}},
    ])
    async for row in cursor:
        funil["aberto"] = {"n": row["n"], "valor": row.get("valor") or 0}
    cursor = db.deals.aggregate([
        {"$match": {"tenant_id": tenant_id, "status": "ganho",
                    "fechado_em": {"$gte": inicio_mes}}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "valor": {"$sum": "$valor"}}},
    ])
    async for row in cursor:
        funil["ganho_mes"] = {"n": row["n"], "valor": row.get("valor") or 0}

    agenda = await atividades.resumo(db, tenant_id, None, hoje_ate, semana_ate)

    return {
        "atendimento": {
            **conversas,
            # "Em aberto" é o que precisa de alguém: fila + em atendimento.
            "em_aberto": conversas["aguardando"] + conversas["em_atendimento"],
            "mensagens_30d": await db.messages_log.count_documents(
                {"tenant_id": tenant_id, "timestamp": {"$gte": trinta_dias}}
            ),
        },
        "funil": funil,
        "agenda": agenda,
        "base": {
            "contatos": await db.contacts.count_documents({"tenant_id": tenant_id}),
            "bots_ativos": await db.bots.count_documents(
                {"tenant_id": tenant_id, "status": "ativo"}
            ),
            "conexoes_conectadas": await db.whatsapp_connections.count_documents(
                {"tenant_id": tenant_id, "status": "conectado"}
            ),
        },
        "gerado_em": agora,
    }


@router.get("/dashboard/hoje")
async def dashboard_hoje(user=Depends(require_tenant_member)):
    """A tela inicial da agência. A montagem e o porquê estão em `hoje.py`."""
    return await hoje.montar(get_db(), user)
