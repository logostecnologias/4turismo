import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel
from typing import Any

import agenda_flow
from db import get_db
from auth import require_tenant_admin
from flow_engine import run_test_message, validar_menu
from bot_binding import sync_bot_trigger

router = APIRouter(prefix="/api/flows", tags=["flows"])


class FlowSave(BaseModel):
    nodes: list[dict]
    edges: list[dict]
    name: str | None = None


def _now():
    return datetime.now(timezone.utc).isoformat()


@router.get("/{flow_id}")
async def get_flow(flow_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    flow = await db.flows.find_one({"id": flow_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not flow:
        raise HTTPException(status_code=404, detail="Fluxo não encontrado")
    return flow


@router.put("/{flow_id}")
async def save_flow(flow_id: str, body: FlowSave, user=Depends(require_tenant_admin)):
    db = get_db()
    flow = await db.flows.find_one({"id": flow_id, "tenant_id": user["tenant_id"]})
    if not flow:
        raise HTTPException(status_code=404, detail="Fluxo não encontrado")
    update = {
        "nodes": body.nodes,
        "edges": body.edges,
        "updated_at": _now(),
    }
    if body.name is not None:
        update["name"] = body.name
    # Antes de gravar: se o gatilho mudou no nó de início e a conexão do bot já
    # tem outro bot desse tipo, a edição é recusada em vez de criar o conflito.
    await sync_bot_trigger(db, user["tenant_id"], flow["bot_id"], {"nodes": body.nodes})
    await db.flows.update_one({"id": flow_id}, {"$set": update})
    # Save history entry
    await db.flow_versions.insert_one({
        "id": str(uuid.uuid4()),
        "flow_id": flow_id,
        "tenant_id": user["tenant_id"],
        "version": flow.get("version", 1),
        "nodes": flow.get("nodes", []),
        "edges": flow.get("edges", []),
        "saved_at": _now(),
    })
    return await db.flows.find_one({"id": flow_id}, {"_id": 0})


@router.post("/{flow_id}/publish")
async def publish_flow(flow_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    flow = await db.flows.find_one({"id": flow_id, "tenant_id": user["tenant_id"]})
    if not flow:
        raise HTTPException(status_code=404, detail="Fluxo não encontrado")
    # Validation: must have exactly one start node
    starts = [n for n in flow.get("nodes", []) if n.get("type") == "start"]
    if len(starts) != 1:
        raise HTTPException(status_code=400, detail="O fluxo deve ter exatamente um nó de Início")
    # every non-end node should have at least one outgoing edge
    edges = flow.get("edges", [])
    src_ids = {e["source"] for e in edges}
    dangling = [n["id"] for n in flow["nodes"] if n["type"] not in ("end", "human_handoff") and n["id"] not in src_ids]
    if dangling:
        raise HTTPException(status_code=400, detail=f"Existem {len(dangling)} nó(s) sem conexão de saída")

    # Menu em formato interativo (botões/lista) fora dos tetos do WhatsApp: a
    # publicação é recusada aqui porque o WhatsApp **não** devolve erro nesse
    # caso — ele descarta a mensagem, e o fluxo fica esperando uma resposta que
    # o contato nunca foi convidado a dar. Recusar na publicação é o último
    # ponto em que isso ainda não chegou a um contato real. (No envio existe a
    # rede de segurança de cair para texto numerado, para o fluxo publicado
    # antes desta checagem — ou editado por fora — não perder a pergunta.)
    problemas = []
    for n in flow.get("nodes", []):
        dados = n.get("data") or {}
        if n.get("type") == "menu":
            nome = dados.get("label") or "Menu"
            problemas += [f"{nome}: {p}" for p in validar_menu(dados)]
        elif n.get("type") == "schedule":
            # O nó de Agendamento monta as próprias mensagens interativas em
            # tempo de envio, então o teto do WhatsApp já é respeitado na
            # montagem. O que se confere aqui é a configuração do nó: um
            # "profissional fixo" sem profissional escolhido, por exemplo,
            # perguntaria a coisa errada a um contato real.
            nome = dados.get("label") or "Agendamento"
            problemas += [f"{nome}: {p}" for p in agenda_flow.validar(dados)]
    if problemas:
        raise HTTPException(status_code=400, detail=" · ".join(problemas))

    await db.flows.update_one(
        {"id": flow_id},
        {"$set": {"published_at": _now(), "version": flow.get("version", 1) + 1}},
    )
    # Set bot status to ativo
    await db.bots.update_one({"id": flow["bot_id"]}, {"$set": {"status": "ativo"}})
    return await db.flows.find_one({"id": flow_id}, {"_id": 0})


@router.get("/{flow_id}/versions")
async def flow_versions(flow_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    versions = await db.flow_versions.find(
        {"flow_id": flow_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("saved_at", -1).limit(20).to_list(20)
    return versions


class TestIn(BaseModel):
    session_id: str | None = None
    message: str | None = None
    start: bool = False
    # Id da opção quando o teste "toca" num botão em vez de digitar — é o que
    # faz o chat do editor exercitar o mesmo caminho de uma resposta real de
    # botão, em vez de só o de quem digita o número.
    escolha_id: str | None = None


@router.post("/{flow_id}/test")
async def test_flow(flow_id: str, body: TestIn, user=Depends(require_tenant_admin)):
    """Simulated flow execution for the in-editor test chat."""
    db = get_db()
    flow = await db.flows.find_one({"id": flow_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not flow:
        raise HTTPException(status_code=404, detail="Fluxo não encontrado")
    return await run_test_message(flow, body.session_id, body.message, body.start,
                                  body.escolha_id)
