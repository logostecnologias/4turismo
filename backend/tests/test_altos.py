"""Regressão dos 6 Altos (ZZ-04 a ZZ-09).

Cobre o que é determinístico sem rede nem provedor:
  ZZ-04/05  net_guard recusa destino interno e esquema não-http.
  ZZ-05     media_store.classify manda SVG para 'document' (também ZZ-07).
  ZZ-06     whatsapp._serialize esconde token/user_id; QR só para admin.
  ZZ-08     _cookie_secure segue a env, seguro por padrão.
  ZZ-09     get_current_user derruba token com tv velho, conta suspensa e
            empresa suspensa.
"""
import asyncio

from fakedb import FakeDB
from fastapi import HTTPException
from starlette.requests import Request

import db as db_mod
import auth
import net_guard
import media_store
import routes.whatsapp as rw

DB = FakeDB()
db_mod.get_db = lambda: DB
auth.get_db = lambda: DB

ok = 0


def check(nome, cond, *extra):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  XX  {nome}  {extra}")
    assert cond, nome


def _conectar_fake():
    global DB
    DB = FakeDB()
    db_mod.get_db = lambda: DB
    auth.get_db = lambda: DB


def _req(token: str) -> Request:
    return Request({"type": "http", "headers": [(b"cookie", f"access_token={token}".encode())]})


async def etapa_net_guard():
    print("\n[ZZ-04/05] net_guard")
    from net_guard import _checar_esquema_e_host, _validar_host, SSRFError
    async def bloqueia(url):
        try:
            h, p = _checar_esquema_e_host(url)
            await _validar_host(h, p)
            return False
        except SSRFError:
            return True
    check("bloqueia loopback (gateway)", await bloqueia("http://127.0.0.1:8090/"))
    check("bloqueia metadados de nuvem", await bloqueia("http://169.254.169.254/"))
    check("bloqueia rede privada", await bloqueia("http://10.1.2.3/"))
    check("bloqueia 'localhost'", await bloqueia("http://localhost:8000/"))
    check("bloqueia esquema file://", await bloqueia("file:///etc/passwd"))


def etapa_classify_svg():
    print("\n[ZZ-05/07] classify")
    check("SVG vira document (anexo)", media_store.classify("image/svg+xml", "x.svg") == "document")
    check("PNG continua image", media_store.classify("image/png", "x.png") == "image")


def etapa_serialize_conexao():
    print("\n[ZZ-06] whatsapp._serialize")
    conn = {"_id": 1, "id": "c1", "nome": "cx", "status": "conectado",
            "phone_number_id": "pn-1", "waba_id": "waba-1",
            "token_cifrado": {"cifrada": "SEGREDO", "chave_versao": 1},
            "token_mascara": "EAAGm0P…7xQz"}
    adm = rw._serialize(conn, True)
    # O token da Cloud API e a credencial de envio da conta do cliente: quem o
    # copia manda mensagem em nome da empresa para a base inteira de contatos.
    check("admin: sem token_cifrado", "token_cifrado" not in adm)
    check("admin: sem _id", "_id" not in adm)
    check("admin: COM a mascara, para reconhecer qual token foi cadastrado",
          adm.get("token_mascara") == "EAAGm0P…7xQz")
    check("admin: COM os ids da Meta, que a tela de conexao mostra",
          adm.get("phone_number_id") == "pn-1" and adm.get("waba_id") == "waba-1")
    agente = rw._serialize(conn, False)
    check("agente: sem token_cifrado", "token_cifrado" not in agente)
    check("agente: sem mascara do token", "token_mascara" not in agente)
    check("agente: sem os ids da Meta", "phone_number_id" not in agente
          and "waba_id" not in agente)


def etapa_cookie_secure():
    print("\n[ZZ-08] _cookie_secure")
    import os
    old = os.environ.pop("COOKIE_SECURE", None)
    check("seguro por padrao (var ausente)", auth._cookie_secure() is True)
    os.environ["COOKIE_SECURE"] = "false"
    check("dev pode desligar", auth._cookie_secure() is False)
    os.environ["COOKIE_SECURE"] = "true"
    check("'true' liga", auth._cookie_secure() is True)
    if old is None:
        os.environ.pop("COOKIE_SECURE", None)
    else:
        os.environ["COOKIE_SECURE"] = old


async def etapa_get_current_user():
    print("\n[ZZ-09] get_current_user: revogacao e suspensao")
    await DB.users.insert_one({"id": "u1", "email": "u@x.com", "role": "agent",
                               "tenant_id": "t1", "status": "active", "token_version": 0,
                               "password_hash": "x"})
    await DB.tenants.insert_one({"id": "t1", "status": "active"})

    tok = auth.create_access_token("u1", "u@x.com", "agent", "t1", token_version=0)
    u = await auth.get_current_user(_req(tok))
    check("token valido + conta ativa: passa", u["id"] == "u1")

    # tv velho (senha foi trocada -> token_version subiu no banco)
    await DB.users.update_one({"id": "u1"}, {"$inc": {"token_version": 1}})
    try:
        await auth.get_current_user(_req(tok)); passou = True
    except HTTPException as e:
        passou = (e.status_code == 401)
    check("token com tv velho: 401", passou)

    # emite token novo (tv=1) e suspende a conta
    tok2 = auth.create_access_token("u1", "u@x.com", "agent", "t1", token_version=1)
    check("token novo (tv atual): passa", (await auth.get_current_user(_req(tok2)))["id"] == "u1")
    await DB.users.update_one({"id": "u1"}, {"$set": {"status": "suspended"}})
    try:
        await auth.get_current_user(_req(tok2)); code = 200
    except HTTPException as e:
        code = e.status_code
    check("conta suspensa: 403", code == 403)

    # reativa a conta, suspende a EMPRESA
    await DB.users.update_one({"id": "u1"}, {"$set": {"status": "active"}})
    await DB.tenants.update_one({"id": "t1"}, {"$set": {"status": "suspended"}})
    try:
        await auth.get_current_user(_req(tok2)); code = 200
    except HTTPException as e:
        code = e.status_code
    check("empresa suspensa: 403", code == 403)


def test_suite():
    _conectar_fake()

    async def _tudo():
        await etapa_net_guard()
        etapa_classify_svg()
        etapa_serialize_conexao()
        etapa_cookie_secure()
        await etapa_get_current_user()
        print(f"\n=== {ok} verificacoes passaram ===")

    asyncio.run(_tudo())
