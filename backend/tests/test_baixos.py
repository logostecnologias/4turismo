"""Regressão dos Baixos (ZZ-18, ZZ-20, ZZ-21).

  ZZ-18  bcrypt trunca em 72 bytes: senha longa não vira 500, hash malformado
         não levanta.
  ZZ-20  /refresh revogável; _conexoes_validas filtra ids de outra empresa.
  ZZ-21  audit.registrar grava e nunca levanta; rotação da chave de IA decifra
         com a chave anterior.

ZZ-19 (teto do limit) é uma linha e é verificado ao vivo.
"""
import asyncio
import os

from cryptography.fernet import Fernet
from fastapi import HTTPException
from starlette.requests import Request
from starlette.responses import Response

from fakedb import FakeDB
import db as db_mod
import auth

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


def _req_cookie(nome, valor) -> Request:
    return Request({"type": "http", "headers": [(b"cookie", f"{nome}={valor}".encode())]})


def etapa_bcrypt_72():
    print("\n[ZZ-18] bcrypt > 72 bytes")
    longa = "A" * 200
    h = auth.hash_password(longa)          # não levanta
    check("hash de senha longa não levanta", isinstance(h, str) and h)
    check("verify da senha longa casa (truncada)", auth.verify_password(longa, h))
    check("verify de senha errada não casa", not auth.verify_password("outra", h))
    check("hash malformado no banco não levanta (retorna False)",
          auth.verify_password("x", "nao-e-um-hash") is False)


async def etapa_refresh():
    print("\n[ZZ-20] /refresh revogável")
    await DB.users.insert_one({"id": "u1", "email": "u@x.com", "role": "agent",
                               "tenant_id": "t1", "status": "active", "token_version": 0,
                               "password_hash": "x"})
    await DB.tenants.insert_one({"id": "t1", "status": "active"})

    rt = auth.create_refresh_token("u1", 0)
    r = await auth.refresh(_req_cookie("refresh_token", rt), Response())
    check("refresh valido renova (ok)", r.get("ok"))

    # um access token no lugar do refresh não serve
    at = auth.create_access_token("u1", "u@x.com", "agent", "t1", token_version=0)
    try:
        await auth.refresh(_req_cookie("refresh_token", at), Response()); code = 200
    except HTTPException as e:
        code = e.status_code
    check("token do tipo errado -> 401", code == 401)

    # trocar a senha (tv sobe) derruba o refresh antigo
    await DB.users.update_one({"id": "u1"}, {"$inc": {"token_version": 1}})
    try:
        await auth.refresh(_req_cookie("refresh_token", rt), Response()); code = 200
    except HTTPException as e:
        code = e.status_code
    check("refresh com tv velho -> 401", code == 401)


async def etapa_conexoes_validas():
    print("\n[ZZ-20] _conexoes_validas")
    import routes.users as ru
    await DB.whatsapp_connections.insert_one({"id": "c1", "tenant_id": "t1"})
    await DB.whatsapp_connections.insert_one({"id": "c2", "tenant_id": "t1"})
    await DB.whatsapp_connections.insert_one({"id": "cX", "tenant_id": "OUTRA"})
    validas = await ru._conexoes_validas(DB, "t1", ["c1", "cX", "c2", "naoexiste"])
    check("mantém só as conexões da empresa, na ordem", validas == ["c1", "c2"], validas)


async def etapa_audit():
    print("\n[ZZ-21] auditoria")
    import audit
    await audit.registrar(DB, ator={"id": "a", "email": "a@x.com", "role": "super_admin"},
                          acao="tenant.delete", tenant_id="t1", alvo="Empresa X", meta={"n": 3})
    linhas = await DB.audit_log.find({"tenant_id": "t1"}, {"_id": 0}).to_list(10)
    check("grava a linha de auditoria", len(linhas) == 1 and linhas[0]["acao"] == "tenant.delete")

    class DBQuebrado:
        class _Col:
            async def insert_one(self, *a, **k):
                raise RuntimeError("banco fora")
        audit_log = _Col()
    # nunca levanta, mesmo se o insert falhar
    await audit.registrar(DBQuebrado(), ator=None, acao="x")
    check("registrar nunca levanta (mesmo com banco fora)", True)


def etapa_rotacao_ia():
    print("\n[ZZ-21] rotação da chave mestra das credenciais")
    # A mesma cifra guarda a chave de IA e o token da Cloud API do cliente.
    # `ai_credentials` continua sendo a porta de entrada do lado da IA; a
    # lógica mora em `cripto`, e é a chave dele que este teste gira.
    import ai_credentials as ac
    guardado = os.environ.get("CREDENTIALS_KEY")
    A = Fernet.generate_key().decode()
    B = Fernet.generate_key().decode()
    os.environ["CREDENTIALS_KEY"] = A
    os.environ.pop("CREDENTIALS_KEY_PREV", None)
    blob = ac.cifrar("sk-ant-ROT")
    os.environ["CREDENTIALS_KEY"] = B
    os.environ["CREDENTIALS_KEY_PREV"] = A
    check("decifra a cred antiga após rotação (B primária, A em PREV)",
          ac.decifrar(blob) == "sk-ant-ROT")
    os.environ.pop("CREDENTIALS_KEY_PREV", None)
    try:
        ac.decifrar(blob); falhou = False
    except ac.CredencialError:
        falhou = True
    check("sem a chave anterior, a cred antiga não decifra", falhou)
    if guardado:
        os.environ["CREDENTIALS_KEY"] = guardado
    else:
        os.environ.pop("CREDENTIALS_KEY", None)


def test_suite():
    _conectar_fake()

    async def _tudo():
        etapa_bcrypt_72()
        await etapa_refresh()
        await etapa_conexoes_validas()
        await etapa_audit()
        etapa_rotacao_ia()
        print(f"\n=== {ok} verificacoes passaram ===")

    asyncio.run(_tudo())
