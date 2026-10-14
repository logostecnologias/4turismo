"""Vínculo bot ↔ conexão e a regra de um bot de cada tipo por conexão.

O tipo do bot (gatilho) é configurado no nó de início do fluxo, mas fica
espelhado no documento do bot: o motor precisa achar "o bot de palavra-chave da
conexão X" a cada mensagem recebida, e sem essa cópia teria que abrir o JSON do
fluxo de todo bot da conexão só pra descobrir o tipo de cada um.

Como o gatilho pode mudar a qualquer momento no editor, a unicidade é checada
nos dois pontos em que ela pode ser quebrada: ao salvar o fluxo e ao trocar a
conexão do bot.
"""
from fastapi import HTTPException

TRIGGER_LABELS = {
    "first_message": "primeira mensagem",
    "keyword": "palavra-chave",
    "no_response": "sem resposta",
}


def trigger_from_flow(flow: dict | None) -> tuple[str, str]:
    """(trigger, trigger_keyword) lidos do nó de início. Fluxos criados antes
    dos gatilhos existirem não têm o campo e valem como primeira mensagem."""
    start = next((n for n in (flow or {}).get("nodes", []) if n.get("type") == "start"), None)
    data = (start or {}).get("data", {})
    return data.get("trigger", "first_message"), (data.get("trigger_keyword") or "")


async def assert_slot_free(db, tenant_id: str, connection_id: str | None, trigger: str, bot_id: str):
    """Recusa se a conexão já tiver outro bot do mesmo tipo."""
    if not connection_id:
        return
    clash = await db.bots.find_one({
        "tenant_id": tenant_id,
        "connection_id": connection_id,
        "trigger": trigger,
        "id": {"$ne": bot_id},
    })
    if not clash:
        return
    conn = await db.whatsapp_connections.find_one({"id": connection_id, "tenant_id": tenant_id})
    raise HTTPException(
        status_code=400,
        detail=(
            f'A conexão "{(conn or {}).get("nome", connection_id)}" já tem um fluxo automático de '
            f'{TRIGGER_LABELS.get(trigger, trigger)}: {clash["name"]}'
        ),
    )


async def sync_bot_trigger(db, tenant_id: str, bot_id: str, flow: dict):
    """Espelha o gatilho do fluxo no bot, validando a unicidade antes."""
    bot = await db.bots.find_one({"id": bot_id, "tenant_id": tenant_id})
    if not bot:
        return
    trigger, keyword = trigger_from_flow(flow)
    await assert_slot_free(db, tenant_id, bot.get("connection_id"), trigger, bot_id)
    await db.bots.update_one(
        {"id": bot_id},
        {"$set": {"trigger": trigger, "trigger_keyword": keyword}},
    )
