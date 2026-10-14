"""Regressão dos dois críticos de autenticação (ZZ-01 e ZZ-02).

ZZ-01 — `seed_super_admin` não tem mais senha padrão e nunca reescreve uma
conta que já existe. Antes, `admin@123` era o default e o boot regravava o
hash a cada restart, tornando a conta mais poderosa do sistema pública e
imutável.

ZZ-02 — `exit-impersonation` era a única rota de escrita sem autenticação, e
devolvia um token de Super Admin para qualquer um que apresentasse um
`impersonation_id` — reutilizável para sempre. Agora exige token, confere o
dono e encerra de forma atômica (uso único, sem replay).

Como o resto da suíte: roda contra o Mongo falso, cobre a lógica. O que ele
não cobre é o `Depends` de fato barrando a request — isso é do FastAPI, e por
isso a última etapa confere que a guarda está **declarada** no endpoint
(inspeção de assinatura), do mesmo jeito que test_settings faz com as suas.
"""
import asyncio
import inspect

from fakedb import FakeDB
from fastapi import HTTPException, Response

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
    """Reaplicado a cada teste — `get_db` é atributo de módulo compartilhado
    entre as suítes carregadas no mesmo processo (ver conftest)."""
    global DB
    DB = FakeDB()
    db_mod.get_db = lambda: DB
    auth.get_db = lambda: DB


async def etapa_seed_exige_senha():
    print("\n[ZZ-01] seed do Super Admin")
    import os
    os.environ.pop("ADMIN_PASSWORD", None)
    os.environ.pop("ADMIN_EMAIL", None)

    # 1) sem senha e sem admin: o boot aborta de propósito.
    erro = None
    try:
        await auth.seed_super_admin()
    except RuntimeError as e:
        erro = str(e)
    check("sem ADMIN_PASSWORD: boot aborta", erro is not None and "ADMIN_PASSWORD" in erro)
    check("nenhum admin foi criado nesse caso",
          await DB.users.find_one({"role": "super_admin"}) is None)

    # 2) senha curta é recusada (piso de 12).
    os.environ["ADMIN_PASSWORD"] = "curta123"
    erro = None
    try:
        await auth.seed_super_admin()
    except RuntimeError as e:
        erro = str(e)
    check("senha < 12 caracteres recusada", erro is not None)

    # 3) senha forte cria a conta, e nunca é 'admin@123'.
    os.environ["ADMIN_PASSWORD"] = "SenhaForte-123456"
    await auth.seed_super_admin()
    u = await DB.users.find_one({"role": "super_admin"})
    check("admin criado com a senha forte",
          u is not None and auth.verify_password("SenhaForte-123456", u["password_hash"]))
    check("a senha nunca é a antiga 'admin@123'",
          not auth.verify_password("admin@123", u["password_hash"]))

    # 4) segundo boot com OUTRA senha na env não sobrescreve a conta existente.
    os.environ["ADMIN_PASSWORD"] = "OutraSenha-999999"
    await auth.seed_super_admin()
    u2 = await DB.users.find_one({"role": "super_admin"})
    check("boot não reescreve conta que já existe",
          auth.verify_password("SenhaForte-123456", u2["password_hash"]))
    check("a senha nova da env é ignorada no boot",
          not auth.verify_password("OutraSenha-999999", u2["password_hash"]))
    os.environ.pop("ADMIN_PASSWORD", None)


async def etapa_exit_impersonation_autenticado():
    print("\n[ZZ-02] exit-impersonation")
    sa = {"id": "sa-1", "email": "sa@4turismo.com.br", "role": "super_admin"}
    await DB.users.insert_one({**sa, "tenant_id": None, "status": "active",
                               "password_hash": auth.hash_password("x" * 12)})
    await DB.impersonation_log.insert_one(
        {"id": "log-1", "super_admin_id": "sa-1", "finalizado_em": None})

    # A) token sem impersonation_id: não está em modo suporte.
    code = None
    try:
        await auth.exit_impersonation(Response(), user={**sa})
    except HTTPException as e:
        code = e.status_code
    check("sem impersonation_id no token: 400", code == 400)

    # B) o dono legítimo encerra: 200 e o log é finalizado.
    dono = {**sa, "impersonation_id": "log-1"}
    r = await auth.exit_impersonation(Response(), user=dono)
    log = await DB.impersonation_log.find_one({"id": "log-1"})
    check("dono encerra: ok + finalizado_em gravado", r.get("ok") and log.get("finalizado_em"))

    # C) replay do mesmo id já encerrado: 400, sem reemitir Super Admin.
    code = None
    try:
        await auth.exit_impersonation(Response(), user=dono)
    except HTTPException as e:
        code = e.status_code
    check("replay de id já encerrado: 400", code == 400)

    # D) um id que não é do usuário: 400, e o log alheio continua aberto.
    await DB.impersonation_log.insert_one(
        {"id": "log-2", "super_admin_id": "outro-sa", "finalizado_em": None})
    intruso = {"id": "sa-1", "email": "sa@4turismo.com.br", "role": "super_admin",
               "impersonation_id": "log-2"}
    code = None
    try:
        await auth.exit_impersonation(Response(), user=intruso)
    except HTTPException as e:
        code = e.status_code
    log2 = await DB.impersonation_log.find_one({"id": "log-2"})
    check("id de outro dono: 400", code == 400)
    check("log alheio permanece aberto", log2.get("finalizado_em") is None)


def etapa_guarda_declarada():
    print("\n[ZZ-02] a guarda está declarada no endpoint")
    # `Depends` não roda ao chamar a função direto — a barreira é conferida
    # onde é declarada, inspecionando a assinatura (mesma técnica do CLAUDE.md).
    p = inspect.signature(auth.exit_impersonation).parameters.get("user")
    dep = getattr(p.default, "dependency", None) if p is not None else None
    check("exit-impersonation exige get_current_user", dep is auth.get_current_user)


def test_suite():
    _conectar_fake()

    async def _tudo():
        await etapa_seed_exige_senha()
        _conectar_fake()
        await etapa_exit_impersonation_autenticado()
        etapa_guarda_declarada()
        print(f"\n=== {ok} verificacoes passaram ===")

    asyncio.run(_tudo())
