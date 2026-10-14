import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_db
from auth import require_tenant_admin
from plan_limits import enforce_plan_limit
from bot_binding import assert_slot_free, trigger_from_flow

router = APIRouter(prefix="/api/bots", tags=["bots"])


class BotIn(BaseModel):
    name: str
    connection_id: str | None = None


class BotUpdate(BaseModel):
    name: str | None = None
    status: str | None = None
    connection_id: str | None = None


def _now():
    return datetime.now(timezone.utc).isoformat()


@router.get("")
async def list_bots(user=Depends(require_tenant_admin)):
    db = get_db()
    bots = await db.bots.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("created_at", -1).to_list(200)
    # `trigger` fica espelhado no bot (ver bot_binding), mas a listagem lê do
    # fluxo mesmo assim: é o dado de origem, e assim bots salvos antes do
    # espelhamento existir aparecem certos na tela.
    flow_ids = [b["main_flow_id"] for b in bots if b.get("main_flow_id")]
    flows = {
        f["id"]: f
        async for f in db.flows.find({"id": {"$in": flow_ids}}, {"_id": 0, "id": 1, "nodes": 1, "published_at": 1})
    }
    conns = {
        c["id"]: c
        async for c in db.whatsapp_connections.find({"tenant_id": user["tenant_id"]}, {"_id": 0, "id": 1, "nome": 1})
    }
    for b in bots:
        flow = flows.get(b.get("main_flow_id"))
        b["trigger"], b["trigger_keyword"] = trigger_from_flow(flow)
        b["publicado"] = bool(flow and flow.get("published_at"))
        b["connection"] = conns.get(b.get("connection_id"))
    return bots


@router.post("")
async def create_bot(body: BotIn, user=Depends(require_tenant_admin)):
    db = get_db()
    await enforce_plan_limit(db, user["tenant_id"], "bots", "max_bots", "fluxos automáticos")
    bot_id = str(uuid.uuid4())
    flow_id = str(uuid.uuid4())
    now = _now()
    # Bot nasce como primeira mensagem (gatilho padrão do nó de início criado
    # abaixo), então já concorre pelo slot desse tipo na conexão escolhida.
    if body.connection_id:
        if not await db.whatsapp_connections.find_one(
            {"id": body.connection_id, "tenant_id": user["tenant_id"]}
        ):
            raise HTTPException(status_code=400, detail="Conexão inválida")
        await assert_slot_free(db, user["tenant_id"], body.connection_id, "first_message", bot_id)
    bot = {
        "id": bot_id,
        "tenant_id": user["tenant_id"],
        "name": body.name,
        "status": "rascunho",
        "created_at": now,
        "main_flow_id": flow_id,
        "connection_id": body.connection_id,
        "trigger": "first_message",
        "trigger_keyword": "",
    }
    # Create an empty starter flow with a single Start node
    start_node_id = str(uuid.uuid4())
    flow = {
        "id": flow_id,
        "bot_id": bot_id,
        "tenant_id": user["tenant_id"],
        "name": "Fluxo Principal",
        "version": 1,
        "is_main": True,
        "published_at": None,
        "nodes": [{
            "id": start_node_id,
            "type": "start",
            "position": {"x": 100, "y": 200},
            "data": {"label": "Início", "trigger": "first_message"},
        }],
        "edges": [],
        "created_at": now,
        "updated_at": now,
    }
    await db.bots.insert_one(bot)
    await db.flows.insert_one(flow)
    bot.pop("_id", None)
    return bot


@router.get("/{bot_id}")
async def get_bot(bot_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    bot = await db.bots.find_one({"id": bot_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not bot:
        raise HTTPException(status_code=404, detail="Fluxo automático não encontrado")
    return bot


@router.patch("/{bot_id}")
async def update_bot(bot_id: str, body: BotUpdate, user=Depends(require_tenant_admin)):
    db = get_db()
    bot = await db.bots.find_one({"id": bot_id, "tenant_id": user["tenant_id"]})
    if not bot:
        raise HTTPException(status_code=404, detail="Fluxo automático não encontrado")
    # exclude_unset pra distinguir "não mandou o campo" de "mandou null":
    # connection_id null é como se desvincula o bot da conexão.
    upd = body.model_dump(exclude_unset=True)
    upd = {k: v for k, v in upd.items() if v is not None or k == "connection_id"}

    if upd.get("connection_id") and upd["connection_id"] != bot.get("connection_id"):
        if not await db.whatsapp_connections.find_one(
            {"id": upd["connection_id"], "tenant_id": user["tenant_id"]}
        ):
            raise HTTPException(status_code=400, detail="Conexão inválida")
        flow = await db.flows.find_one({"id": bot.get("main_flow_id")}, {"_id": 0, "nodes": 1})
        trigger, keyword = trigger_from_flow(flow)
        await assert_slot_free(db, user["tenant_id"], upd["connection_id"], trigger, bot_id)
        # Aproveita pra espelhar o gatilho, cobrindo bots salvos antes disso existir.
        upd["trigger"], upd["trigger_keyword"] = trigger, keyword

    if upd:
        await db.bots.update_one(
            {"id": bot_id, "tenant_id": user["tenant_id"]},
            {"$set": upd},
        )
    return await db.bots.find_one({"id": bot_id, "tenant_id": user["tenant_id"]}, {"_id": 0})


@router.post("/{bot_id}/duplicate")
async def duplicate_bot(bot_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await enforce_plan_limit(db, user["tenant_id"], "bots", "max_bots", "fluxos automáticos")
    bot = await db.bots.find_one({"id": bot_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not bot:
        raise HTTPException(status_code=404, detail="Fluxo automático não encontrado")
    flow = await db.flows.find_one({"id": bot["main_flow_id"]}, {"_id": 0})
    new_bot_id = str(uuid.uuid4())
    new_flow_id = str(uuid.uuid4())
    now = _now()
    # A cópia nasce sem conexão: herdar o vínculo colocaria dois bots do mesmo
    # tipo na mesma conexão, que é exatamente o que a regra proíbe.
    new_bot = {**bot, "id": new_bot_id, "name": f"{bot['name']} (Cópia)", "status": "rascunho",
               "created_at": now, "main_flow_id": new_flow_id, "connection_id": None}
    new_flow = {**flow, "id": new_flow_id, "bot_id": new_bot_id, "tenant_id": user["tenant_id"],
                "version": 1, "published_at": None, "created_at": now, "updated_at": now}
    await db.bots.insert_one(new_bot)
    await db.flows.insert_one(new_flow)
    new_bot.pop("_id", None)
    return new_bot


@router.delete("/{bot_id}")
async def delete_bot(bot_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await db.bots.delete_one({"id": bot_id, "tenant_id": user["tenant_id"]})
    await db.flows.delete_many({"bot_id": bot_id})
    return {"ok": True}
