"""Central de Atendimento — lista de conversas + ações."""
import asyncio
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

import media_store
from db import get_db
from auth import accessible_connection_ids, get_current_user
from flow_runtime import (JanelaFechada, log_message, send_human_message,
                          send_human_media)

router = APIRouter(prefix="/api/atendimento", tags=["atendimento"])


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _accessible_connection_ids(db, user: dict) -> list[str] | None:
    """None (=todas) para admin/super, ou lista explícita para o atendente.
    A regra mora em auth.py: ela vale em toda tela que envie mensagem."""
    return await accessible_connection_ids(db, user)


async def _enrich(db, sessions: list[dict]):
    if not sessions:
        return sessions
    contact_ids = list({s["contact_id"] for s in sessions if s.get("contact_id")})
    dept_ids = list({s["departamento_id"] for s in sessions if s.get("departamento_id")})
    agent_ids = list({s["atendente_id"] for s in sessions if s.get("atendente_id")})
    conn_ids = list({s["whatsapp_connection_id"] for s in sessions if s.get("whatsapp_connection_id")})
    contacts = {c["id"]: c async for c in db.contacts.find({"id": {"$in": contact_ids}}, {"_id": 0})}
    depts = {d["id"]: d async for d in db.departments.find({"id": {"$in": dept_ids}}, {"_id": 0})}
    agents = {u["id"]: u async for u in db.users.find({"id": {"$in": agent_ids}}, {"_id": 0, "password_hash": 0})}
    conns = {c["id"]: c async for c in db.whatsapp_connections.find(
        {"id": {"$in": conn_ids}}, {"_id": 0, "id": 1, "nome": 1, "numero": 1}
    )}
    # `last_message` não é buscado aqui: ele vem desnormalizado no próprio
    # documento da sessão, gravado por flow_runtime.log_message. Antes era um
    # aggregate que ordenava todas as mensagens das até 200 conversas da tela
    # para pegar a primeira de cada — dezenas de milhares de documentos
    # ordenados em memória, a cada atualização, por atendente conectado.
    for s in sessions:
        s["contact"] = contacts.get(s.get("contact_id"))
        s["department"] = depts.get(s.get("departamento_id"))
        s["agent"] = agents.get(s.get("atendente_id"))
        s["connection"] = conns.get(s.get("whatsapp_connection_id"))
        s.setdefault("last_message", None)
    return sessions


@router.get("/sessions")
async def list_sessions(
    user: dict = Depends(get_current_user),
    status: str | None = None,
    department_id: str | None = None,
    connection_id: str | None = None,
    limit: int = 200,
):
    if not user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Usuário sem agência vinculada")
    db = get_db()
    q: dict = {"tenant_id": user["tenant_id"]}
    if status:
        q["status_atendimento"] = status
    if department_id:
        q["departamento_id"] = department_id

    accessible = await _accessible_connection_ids(db, user)
    if accessible is not None:
        if connection_id and connection_id not in accessible:
            raise HTTPException(status_code=403, detail="Sem acesso a esta conexão")
        q["whatsapp_connection_id"] = connection_id if connection_id else {"$in": accessible}
    elif connection_id:
        q["whatsapp_connection_id"] = connection_id

    # Teto (ZZ-19): as outras listas do sistema já aplicavam isto; esta ficou de
    # fora, então `?limit=100000` puxava tudo e ainda passava pelo _enrich caro.
    limit = max(1, min(limit, 500))
    sessions = await db.conversation_sessions.find(q, {"_id": 0}).sort("updated_at", -1).limit(limit).to_list(limit)
    return await _enrich(db, sessions)


@router.get("/unread-count")
async def unread_count(user: dict = Depends(get_current_user)):
    """Soma leve de unread_count + tamanho da fila humana, sem o enrich caro de
    /sessions — usada pelo polling do sino/som de notificação e pelo badge de
    fila no menu lateral, que rodam a cada poucos segundos."""
    if not user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Usuário sem agência vinculada")
    db = get_db()
    scope: dict = {"tenant_id": user["tenant_id"]}
    accessible = await _accessible_connection_ids(db, user)
    if accessible is not None:
        scope["whatsapp_connection_id"] = {"$in": accessible}

    async def _soma_nao_lidas() -> int:
        # A soma acontece no servidor. Antes cada conversa não lida vinha pela
        # rede só para ter um número somado aqui.
        async for linha in db.conversation_sessions.aggregate([
            {"$match": {**scope, "unread_count": {"$gt": 0}}},
            {"$group": {"_id": None, "total": {"$sum": "$unread_count"}}},
        ]):
            return linha.get("total") or 0
        return 0

    # As duas em paralelo: são independentes, e o que pesa aqui é a ida e
    # volta até o banco. Continuam separadas de propósito — juntá-las num
    # `$facet` obrigaria o `$match` inicial a pegar todas as conversas do
    # tenant, perdendo os dois filtros seletivos que hoje usam índice.
    total, aguardando = await asyncio.gather(
        _soma_nao_lidas(),
        db.conversation_sessions.count_documents(
            {**scope, "status_atendimento": "aguardando"}
        ),
    )
    return {"total": total, "aguardando": aguardando}


MESSAGES_PAGE = 2000


@router.get("/sessions/{session_id}")
async def get_session(
    session_id: str,
    since: str | None = None,
    user: dict = Depends(get_current_user),
):
    """Conversa aberta e suas mensagens.

    `since` (timestamp ISO da última mensagem já em tela) devolve só o que
    chegou depois — a tela se atualiza de poucos em poucos segundos, e sem o
    recorte cada ciclo rebaixava o histórico inteiro, anexos inclusive.

    O corte é `$gte`, e não `$gt`, porque timestamp igual é comum e não raro:
    o relógio do Windows anda de ~15ms em ~15ms, então mensagens gravadas na
    mesma rajada (as várias falas de um menu, por exemplo) saem com o mesmo
    valor. Com `$gt` toda a rajada seguinte à mensagem do cursor sumiria da
    tela; com `$gte` ela volta repetida, e o front reconcilia por id.
    """
    db = get_db()
    s = await db.conversation_sessions.find_one(
        {"id": session_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    )
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    accessible = await _accessible_connection_ids(db, user)
    if accessible is not None and s.get("whatsapp_connection_id") not in accessible:
        raise HTTPException(status_code=403, detail="Sem acesso a esta conexão")

    q: dict = {"session_id": session_id}
    if since:
        q["timestamp"] = {"$gte": since}
    # Desempate por `_id`: ordenar só por timestamp deixa a ordem das mensagens
    # de uma mesma rajada indefinida — elas compartilham o timestamp, e o chat
    # podia exibi-las em ordem diferente a cada atualização. O ObjectId cresce
    # a cada inserção, então ele devolve a ordem real de gravação.
    msgs = await db.messages_log.find(q, {"_id": 0}).sort(
        [("timestamp", 1), ("_id", 1)]
    ).to_list(MESSAGES_PAGE)

    if s.get("unread_count"):
        await db.conversation_sessions.update_one({"id": session_id}, {"$set": {"unread_count": 0}})
        s["unread_count"] = 0
    (await _enrich(db, [s]))
    # `partial` diz ao front se `messages` completa a lista em tela ou a
    # substitui — sem isso ele não sabe distinguir "nada novo" de "conversa
    # vazia".
    return {"session": s, "messages": msgs, "partial": bool(since)}


@router.post("/sessions/{session_id}/assume")
async def assume(session_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    s = await db.conversation_sessions.find_one({"id": session_id, "tenant_id": user["tenant_id"]})
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    accessible = await _accessible_connection_ids(db, user)
    if accessible is not None and s.get("whatsapp_connection_id") not in accessible:
        raise HTTPException(status_code=403, detail="Sem acesso a esta conexão")
    if s.get("status_atendimento") not in ("aguardando", "em_atendimento"):
        raise HTTPException(status_code=400, detail="Conversa não está em fila humana")
    await db.conversation_sessions.update_one(
        {"id": session_id},
        {"$set": {
            "status_atendimento": "em_atendimento",
            "status": "aguardando_humano",
            "atendente_id": user["id"],
            "assumido_em": s.get("assumido_em") or _now(),
            "aguardando_desde": None,
            "updated_at": _now(),
        }},
    )
    await log_message(db, session_id, user["tenant_id"], "sistema",
                      f"{user['name']} assumiu a conversa", mtype="system")
    return {"ok": True}


@router.post("/sessions/{session_id}/finalize")
async def finalize(session_id: str, user: dict = Depends(get_current_user)):
    db = get_db()
    s = await db.conversation_sessions.find_one({"id": session_id, "tenant_id": user["tenant_id"]})
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if user["role"] == "agent" and s.get("atendente_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Só quem assumiu pode finalizar")
    await db.conversation_sessions.update_one(
        {"id": session_id},
        {"$set": {
            "status_atendimento": "finalizado",
            "status": "finalizada",
            "finalizado_em": _now(),
            "updated_at": _now(),
        }},
    )
    await log_message(db, session_id, user["tenant_id"], "sistema",
                      f"{user['name']} finalizou o atendimento", mtype="system")
    return {"ok": True}


class ReassignIn(BaseModel):
    department_id: str | None = None


@router.patch("/sessions/{session_id}/department")
async def reassign_department(session_id: str, body: ReassignIn, user: dict = Depends(get_current_user)):
    db = get_db()
    s = await db.conversation_sessions.find_one({"id": session_id, "tenant_id": user["tenant_id"]})
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    # Any tenant user with access can reassign
    accessible = await _accessible_connection_ids(db, user)
    if accessible is not None and s.get("whatsapp_connection_id") not in accessible:
        raise HTTPException(status_code=403, detail="Sem acesso")
    await db.conversation_sessions.update_one(
        {"id": session_id},
        {"$set": {"departamento_id": body.department_id, "updated_at": _now()}},
    )
    return {"ok": True}


async def _authorize_send(db, session_id: str, user: dict) -> dict:
    """Regra de quem pode responder nesta conversa, compartilhada por texto e
    mídia. Admin que responde sem ter assumido assume na hora."""
    s = await db.conversation_sessions.find_one({"id": session_id, "tenant_id": user["tenant_id"]})
    if not s:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    if user["role"] == "agent" and s.get("atendente_id") != user["id"]:
        raise HTTPException(status_code=403, detail="Assuma a conversa antes de enviar")
    if s.get("status_atendimento") != "em_atendimento":
        # Force-assume if admin/tenant_admin sends directly
        if user["role"] in ("tenant_admin", "super_admin"):
            await db.conversation_sessions.update_one(
                {"id": session_id},
                {"$set": {
                    "status_atendimento": "em_atendimento",
                    "status": "aguardando_humano",
                    "atendente_id": user["id"],
                    "assumido_em": _now(),
                }},
            )
        else:
            raise HTTPException(status_code=400, detail="Conversa não está em atendimento")
    return s


class SendIn(BaseModel):
    content: str


@router.post("/sessions/{session_id}/send")
async def send(session_id: str, body: SendIn, user: dict = Depends(get_current_user)):
    """Human agent sends a message through the official Cloud API.

    A janela de 24h volta como 409, e nao como falha de envio: sao coisas
    diferentes para quem esta atendendo. Falha pede tentar de novo; janela
    fechada pede outra acao (esperar o contato escrever, ou mandar um modelo),
    e um "erro ao enviar" ali faria o atendente repetir o clique para sempre.
    """
    db = get_db()
    await _authorize_send(db, session_id, user)
    try:
        await send_human_message(user["tenant_id"], session_id, body.content)
    except JanelaFechada as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao enviar: {e}")
    return {"ok": True}


@router.post("/sessions/{session_id}/send-media")
async def send_media(
    session_id: str,
    file: UploadFile = File(...),
    caption: str = Form(""),
    user: dict = Depends(get_current_user),
):
    """Human agent sends an image/video/audio/document via the Cloud API.

    The file is stored first and only then sent: if the send fails the upload
    is discarded, so nothing shows in the panel that the contact never got.
    """
    db = get_db()
    await _authorize_send(db, session_id, user)

    # Gravado direto em disco conforme chega, com o teto aplicado durante a
    # escrita: acumular o arquivo inteiro na memória para só então salvá-lo
    # custava duas cópias dele, por envio simultâneo.
    try:
        doc = await media_store.store_stream(
            db, user["tenant_id"], session_id, user["id"],
            file.filename, file.content_type, file.read,
        )
    except media_store.MediaError as e:
        raise HTTPException(status_code=400, detail=str(e))

    try:
        await send_human_media(user["tenant_id"], session_id, doc, caption)
    except Exception as e:
        await db.media_files.delete_one({"id": doc["id"]})
        media_store.file_path(doc).unlink(missing_ok=True)
        if isinstance(e, JanelaFechada):
            raise HTTPException(status_code=409, detail=str(e))
        raise HTTPException(status_code=502, detail=f"Falha ao enviar: {e}")
    return {"ok": True, "media": media_store.public_payload(doc)}


KANBAN_STATUSES = ("bot", "aguardando", "em_atendimento", "finalizado")


@router.get("/summary")
async def summary(user: dict = Depends(get_current_user)):
    """Kanban column counts respecting user access.

    Uma contagem agrupada em vez de uma consulta por coluna: eram quatro idas
    e voltas ao banco para montar quatro números.
    """
    db = get_db()
    q: dict = {"tenant_id": user["tenant_id"]}
    accessible = await _accessible_connection_ids(db, user)
    if accessible is not None:
        q["whatsapp_connection_id"] = {"$in": accessible}

    counts = {st: 0 for st in KANBAN_STATUSES}
    async for linha in db.conversation_sessions.aggregate([
        {"$match": q},
        {"$group": {"_id": "$status_atendimento", "n": {"$sum": 1}}},
    ]):
        # Conversa com status fora das quatro colunas é ignorada, não vira
        # chave nova: o front indexa o retorno pelas colunas que conhece.
        if linha["_id"] in counts:
            counts[linha["_id"]] = linha["n"]
    return counts
