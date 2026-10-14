"""Teste do receptor de webhook da Cloud API oficial.

É o ponto por onde entra TODA mensagem do sistema, e ele mudou de forma
estrutural: deixou de haver uma URL por conexão e passou a haver um endereço
só, para todas as empresas, com o roteamento saindo dos metadados do payload.
Três coisas precisam ficar provadas aqui, porque as três falham em silêncio:

  - **O roteamento por `phone_number_id`.** Errar isso entrega a mensagem de um
    cliente na conversa de outro, que é a pior falha possível num sistema
    multi-empresa. Número desconhecido tem de ser 200 e ignorado: qualquer
    outra resposta faz a Meta repetir o mesmo evento por sete dias.

  - **A verificação da URL** (`hub.challenge`). Ela acontece uma vez, no
    cadastro do webhook, e devolver JSON no lugar de texto puro faz a Meta
    recusar a URL sem dizer por quê.

  - **O status de entrega.** É o que a API não oficial nunca deu. Ele casa com
    a bolha pelo `wamid`, e a ordem de chegada não é garantida: um `delivered`
    atrasado não pode rebaixar uma mensagem que já foi lida.

Nada é enviado para a rede: `handle_incoming_message` é substituído por um
gravador, que é justamente a fronteira entre o webhook e o motor de conversas.
"""
import asyncio
import hashlib
import hmac
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

import json as jsonlib

from fastapi import HTTPException

from fakedb import FakeDB

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import routes.webhooks as w

SEGREDO = "app-secret-de-teste"
VERIFICA = "token-de-verificacao"

RECEBIDAS: list[tuple] = []


async def fake_handle(tenant_id, numero, texto, wamid, connection_id=None,
                      push_name=None, media=None, escolha=None):
    RECEBIDAS.append({"tenant_id": tenant_id, "numero": numero, "texto": texto,
                      "wamid": wamid, "connection_id": connection_id,
                      "push_name": push_name, "media": media, "escolha": escolha})


class _BG:
    """BackgroundTasks o bastante para o teste: roda a tarefa na hora.

    O de verdade só executa depois de a resposta sair, e o que interessa aqui é
    o que foi enfileirado, não quando.
    """

    def __init__(self):
        self.tarefas = []

    def add_task(self, func, *a, **kw):
        self.tarefas.append((func, a, kw))
        asyncio.get_event_loop().create_task(func(*a, **kw))


class _Req:
    """Request o bastante para o receptor: corpo cru, json e cabeçalhos.

    O corpo cru importa: a assinatura da Meta é calculada sobre os bytes, e
    reserializar o JSON antes de conferir quebraria a checagem.
    """

    def __init__(self, payload: dict, assinar=True, params=None):
        self._corpo = jsonlib.dumps(payload).encode()
        assinatura = hmac.new(SEGREDO.encode(), self._corpo, hashlib.sha256).hexdigest()
        self.headers = {"x-hub-signature-256": f"sha256={assinatura}" if assinar else "sha256=errada"}
        self.query_params = params or {}

    async def body(self):
        return self._corpo

    async def json(self):
        return jsonlib.loads(self._corpo)


TENANT = "t-1"
CONN = "conn-a"
PNID = "1055500011"

ok = 0


def check(nome, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  FALHOU  {nome}  -> {extra}")
        raise AssertionError(nome)


def _evento(mensagens=None, statuses=None, pnid=PNID, contatos=None):
    valor = {"messaging_product": "whatsapp",
             "metadata": {"display_phone_number": "5511999998888",
                          "phone_number_id": pnid}}
    if contatos:
        valor["contacts"] = contatos
    if mensagens:
        valor["messages"] = mensagens
    if statuses:
        valor["statuses"] = statuses
    return {"object": "whatsapp_business_account",
            "entry": [{"id": "waba-1", "changes": [{"field": "messages", "value": valor}]}]}


async def semear():
    DB._cols.clear()
    RECEBIDAS.clear()
    os.environ["META_APP_SECRET"] = SEGREDO
    os.environ["META_WEBHOOK_VERIFY_TOKEN"] = VERIFICA
    db_mod.get_db = lambda: DB
    w.get_db = lambda: DB
    w.handle_incoming_message = fake_handle
    await DB.tenants.insert_one({"id": TENANT})
    await DB.whatsapp_connections.insert_one({
        "id": CONN, "tenant_id": TENANT, "nome": "Comercial",
        "status": "conectado", "phone_number_id": PNID, "waba_id": "waba-1"})


# ---------------------------------------------------------------------------

async def etapa_verificacao():
    print("\n[verificação da URL no painel da Meta]")
    await semear()

    resp = await w.verificar_webhook(_Req({}, params={
        "hub.mode": "subscribe", "hub.verify_token": VERIFICA, "hub.challenge": "1158201444"}))
    # Texto puro: devolvido como JSON, com aspas em volta, a Meta recusa a URL
    # e o painel não diz por quê.
    check("devolve o challenge em texto puro",
          resp.body == b"1158201444" and resp.media_type == "text/plain",
          (resp.body, resp.media_type))

    try:
        await w.verificar_webhook(_Req({}, params={
            "hub.mode": "subscribe", "hub.verify_token": "chutado",
            "hub.challenge": "x"}))
        codigo = 200
    except HTTPException as e:
        codigo = e.status_code
    check("token de verificação errado -> 403", codigo == 403, codigo)

    guardado = os.environ.pop("META_WEBHOOK_VERIFY_TOKEN")
    try:
        await w.verificar_webhook(_Req({}, params={"hub.mode": "subscribe"}))
        codigo = 200
    except HTTPException as e:
        codigo = e.status_code
    check("sem token configurado -> 503, não passa em silêncio", codigo == 503, codigo)
    os.environ["META_WEBHOOK_VERIFY_TOKEN"] = guardado


async def etapa_mensagem_de_texto():
    print("\n[mensagem de texto: roteada pelo phone_number_id]")
    await semear()
    bg = _BG()
    payload = _evento(
        contatos=[{"profile": {"name": "Maria Silva"}, "wa_id": "5511900000001"}],
        mensagens=[{"from": "5511900000001", "id": "wamid.AAA", "timestamp": "1749416383",
                    "type": "text", "text": {"body": "quanto custa?"}}])
    resp = await w.receber_webhook(_Req(payload), bg)
    await asyncio.sleep(0)

    check("respondeu 200 com o evento contado", resp == {"ok": True, "eventos": 1}, resp)
    check("uma mensagem entregue ao motor", len(RECEBIDAS) == 1, RECEBIDAS)
    m = RECEBIDAS[0]
    check("achou a empresa e a conexão pelo phone_number_id",
          (m["tenant_id"], m["connection_id"]) == (TENANT, CONN), m)
    check("texto e número passados adiante",
          (m["numero"], m["texto"]) == ("5511900000001", "quanto custa?"), m)
    check("o wamid da Meta é o id de deduplicação", m["wamid"] == "wamid.AAA", m)
    # O nome vem uma vez por evento, fora da mensagem, e casa pelo wa_id: é o
    # unico lugar onde ele aparece, e buscá-lo custaria uma chamada por conversa.
    check("nome do perfil aproveitado", m["push_name"] == "Maria Silva", m)

    conn = await DB.whatsapp_connections.find_one({"id": CONN})
    check("a conexão registra que recebeu evento", bool(conn.get("last_webhook_event_at")))


async def etapa_numero_desconhecido():
    print("\n[phone_number_id que não é nosso: 200 e ignora]")
    await semear()
    bg = _BG()
    payload = _evento(pnid="99999999999",
                      mensagens=[{"from": "5511900000001", "id": "wamid.BBB",
                                  "type": "text", "text": {"body": "oi"}}])
    resp = await w.receber_webhook(_Req(payload), bg)
    # Recusar faria a Meta repetir o mesmo evento com frequência decrescente
    # por sete dias, para um número que nunca vai casar com nada.
    check("respondeu 200 mesmo assim", resp["ok"] is True, resp)
    check("nada foi para o motor", RECEBIDAS == [], RECEBIDAS)


async def etapa_assinatura_e_objeto():
    print("\n[assinatura inválida e objeto de outro produto]")
    await semear()
    bg = _BG()
    try:
        await w.receber_webhook(_Req(_evento(), assinar=False), bg)
        codigo = 200
    except HTTPException as e:
        codigo = e.status_code
    check("assinatura errada -> 401", codigo == 401, codigo)

    resp = await w.receber_webhook(_Req({"object": "instagram", "entry": []}), bg)
    check("evento de outro produto é ignorado", resp.get("ignored") == "instagram", resp)


async def etapa_varias_mensagens_e_campos():
    print("\n[um payload pode trazer várias mensagens]")
    await semear()
    bg = _BG()
    payload = _evento(mensagens=[
        {"from": "5511900000001", "id": "wamid.1", "type": "text", "text": {"body": "oi"}},
        {"from": "5511900000002", "id": "wamid.2", "type": "interactive",
         "interactive": {"type": "button_reply",
                         "button_reply": {"id": "op_sim", "title": "Sim"}}},
        # Sem texto, sem anexo e sem escolha: não vira bolha vazia no histórico.
        {"from": "5511900000003", "id": "wamid.3", "type": "system"},
    ])
    payload["entry"][0]["changes"].append(
        {"field": "message_template_status_update", "value": {"event": "APPROVED"}})
    resp = await w.receber_webhook(_Req(payload), bg)
    await asyncio.sleep(0)

    check("as duas mensagens úteis passaram", len(RECEBIDAS) == 2, RECEBIDAS)
    check("a escolha do botão veio montada",
          RECEBIDAS[1]["escolha"] == {"id": "op_sim", "titulo": "Sim"}, RECEBIDAS[1])
    check("e o texto da bolha é o rótulo, não o id",
          RECEBIDAS[1]["texto"] == "Sim", RECEBIDAS[1])
    # Campo que não é `messages` (qualidade do número, status de modelo) chega
    # no mesmo endereço e é ignorado sem derrubar o resto do payload.
    check("campo de outro tipo não atrapalha", resp["eventos"] == 2, resp)


async def etapa_status_de_entrega():
    print("\n[status de entrega: casa com a bolha pelo wamid]")
    await semear()
    await DB.messages_log.insert_one({
        "id": "m-1", "tenant_id": TENANT, "session_id": "s-1", "direction": "enviada",
        "content": "Oi!", "type": "text", "meta": {"wamid": "wamid.ZZZ"},
        "timestamp": "2026-08-27T10:00:00+00:00"})

    await w._registrar_status(TENANT, {"id": "wamid.ZZZ", "status": "delivered",
                                       "timestamp": "1749416400"})
    msg = await DB.messages_log.find_one({"id": "m-1"})
    check("entregue gravado na mensagem", msg["meta"]["entrega"] == "entregue", msg["meta"])

    await w._registrar_status(TENANT, {"id": "wamid.ZZZ", "status": "read"})
    msg = await DB.messages_log.find_one({"id": "m-1"})
    check("lida sobrescreve entregue", msg["meta"]["entrega"] == "lida", msg["meta"])

    # A Meta não garante ordem: um `delivered` atrasado chegando depois do
    # `read` não pode rebaixar a bolha para "entregue".
    await w._registrar_status(TENANT, {"id": "wamid.ZZZ", "status": "delivered"})
    msg = await DB.messages_log.find_one({"id": "m-1"})
    check("status fora de ordem não rebaixa", msg["meta"]["entrega"] == "lida", msg["meta"])

    await w._registrar_status(TENANT, {
        "id": "wamid.ZZZ", "status": "failed",
        "errors": [{"code": 131026, "title": "Message undeliverable",
                    "error_data": {"details": "Receiver is incapable of receiving this message"}}]})
    msg = await DB.messages_log.find_one({"id": "m-1"})
    check("falha entra mesmo depois de lida", msg["meta"]["entrega"] == "falha", msg["meta"])
    check("com o código da Meta, que diz o que aconteceu",
          msg["meta"]["entrega_erro"]["codigo"] == 131026, msg["meta"])
    check("com o motivo", "incapable" in (msg["meta"]["entrega_erro"]["detalhe"] or ""),
          msg["meta"])


async def etapa_status_de_mensagem_desconhecida():
    print("\n[status de mensagem que não é nossa]")
    await semear()
    await w._registrar_status(TENANT, {"id": "wamid.NAO-EXISTE", "status": "delivered"})
    check("não cria mensagem do nada",
          await DB.messages_log.count_documents({}) == 0)


async def _tudo():
    await etapa_verificacao()
    await etapa_mensagem_de_texto()
    await etapa_numero_desconhecido()
    await etapa_assinatura_e_objeto()
    await etapa_varias_mensagens_e_campos()
    await etapa_status_de_entrega()
    await etapa_status_de_mensagem_desconhecida()
    print(f"\n{ok} verificações passaram")


def test_suite():
    asyncio.run(_tudo())


if __name__ == "__main__":
    test_suite()
