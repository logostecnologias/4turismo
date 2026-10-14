"""Teste do cadastro de empresa: criacao, senha inicial e exclusao.

Onde estao os riscos deste modulo, e por isso o que se testa aqui:

- **A empresa e gravada antes do admin.** Se o insert do usuario falhar, sobra
  uma empresa sem ninguem dentro: ela aparece na lista, ninguem entra nela, e
  nem o cliente nem o suporte descobrem por que. Nao da erro visivel, so uma
  empresa morta. A etapa de rollback exercita exatamente esse caminho.
- **A senha inicial nao tem troca obrigatoria no primeiro acesso**, entao a que
  e digitada aqui e a que o cliente usa. O piso de tamanho e a unica barreira,
  e ele vive no modelo pydantic.
- **A exclusao e a operacao mais destrutiva do sistema e nao deixa rastro.** O
  jeito de ela ficar defasada e criar uma colecao nova em outro modulo e nao
  lembrar da rota, que foi o que aconteceu com a agenda, os agentes de IA, as
  midias, os departamentos e o proprio historico de mensagens. Por isso a ultima
  etapa nao confere so a lista contra si mesma: ela varre o codigo do backend
  procurando colecao com `tenant_id` que ninguem apaga.
"""
import asyncio
import inspect
import os
import pathlib
import re
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

from fakedb import FakeDB
from fastapi import HTTPException
from pydantic import ValidationError
from pymongo.errors import DuplicateKeyError

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import auth
import routes.tenants as rt

BACKEND = pathlib.Path(__file__).resolve().parent.parent


def _conectar_fake():
    """Reaplicado a cada teste, nao so no import: o pytest carrega todos os
    modulos de teste no mesmo processo e `get_db` e atributo de modulo."""
    db_mod.get_db = lambda: DB
    rt.get_db = lambda: DB


PLANO = "p-pro"
FICA, SAI = "t-fica", "t-sai"

ok = 0


def check(nome, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  FALHOU  {nome}  -> {extra}")
        raise AssertionError(nome)


async def erro(coro):
    try:
        await coro
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return None, None


def corpo(**troca):
    base = {"name": "Logos Tecnologias", "plan_id": PLANO, "admin_name": "Markus",
            "admin_email": "markus@logos.com.br", "admin_password": "Kx7pvQm2rTae"}
    base.update(troca)
    return rt.TenantCreate(**base)


# ---------------------------------------------------------------------------
# Senha inicial e nome
# ---------------------------------------------------------------------------

async def etapa_semear():
    print("\n[semear]")
    await DB.plans.insert_one({
        "id": PLANO, "nome": "Pro", "preco_mensal": 249.9,
        "max_bots": 5, "max_conexoes": 2, "max_usuarios": 10,
        "max_tokens_ia_mes": 500_000,
        "retencao_midia_dias": 90, "status": "active",
    })
    check("plano semeado", await DB.plans.count_documents({}) == 1)


async def etapa_senha_curta():
    print("\n[senha inicial]")
    try:
        corpo(admin_password="1")
        check("senha de um caractere recusada", False, "passou")
    except ValidationError as e:
        check("senha de um caractere recusada", True)
        check("e a recusa fala do minimo", str(rt.SENHA_MIN) in str(e), str(e))

    try:
        corpo(admin_password="a" * (rt.SENHA_MIN - 1))
        check("senha um caractere abaixo do piso recusada", False, "passou")
    except ValidationError:
        check("senha um caractere abaixo do piso recusada", True)

    b = corpo(admin_password="a" * rt.SENHA_MIN)
    check("senha exatamente no piso passa", b.admin_password == "a" * rt.SENHA_MIN)

    # Espaco em volta nao conta como nome: `min_length` sozinho aceitaria "  ",
    # e a empresa nasceria em branco na lista.
    b = corpo(name="  Logos  ")
    check("nome e aparado", b.name == "Logos", b.name)
    try:
        corpo(name="   ")
        check("nome so de espaco recusado", False, "passou")
    except ValidationError:
        check("nome so de espaco recusado", True)


# ---------------------------------------------------------------------------
# Criacao
# ---------------------------------------------------------------------------

async def etapa_criar():
    print("\n[criar]")
    t = await rt.create_tenant(corpo())
    check("empresa criada ativa", t["status"] == "active", t)
    check("com o plano vinculado ja resolvido para a tela",
          (t.get("plan") or {}).get("nome") == "Pro", t.get("plan"))

    admin = await DB.users.find_one({"tenant_id": t["id"]})
    check("o admin nasceu junto", admin is not None)
    check("como tenant_admin", admin["role"] == "tenant_admin", admin["role"])
    check("e no tenant certo", admin["tenant_id"] == t["id"])
    # A senha em claro nao pode existir em lugar nenhum do documento.
    check("a senha foi guardada como hash",
          auth.verify_password("Kx7pvQm2rTae", admin["password_hash"]))
    check("e nao sobrou senha em claro no documento",
          "Kx7pvQm2rTae" not in str(admin), admin)

    # O e-mail e a identidade do login, e ele chega da tela como a pessoa
    # digitou. Sem normalizar, "Markus@" e "markus@" seriam duas contas.
    t2 = await rt.create_tenant(corpo(name="Outra", admin_email="MAIUSCULA@Logos.com.BR"))
    dono = await DB.users.find_one({"tenant_id": t2["id"]})
    check("e-mail guardado em minusculas", dono["email"] == "maiuscula@logos.com.br", dono["email"])

    st, det = await erro(rt.create_tenant(corpo(name="Terceira")))
    check("e-mail repetido e recusado", st == 400, (st, det))
    check("sem deixar a empresa da tentativa para tras",
          await DB.tenants.count_documents({"name": "Terceira"}) == 0)

    st, det = await erro(rt.create_tenant(corpo(admin_email="nova@x.com", plan_id="nao-existe")))
    check("plano inexistente e recusado", st == 400, (st, det))

    await DB.tenants.delete_many({})
    await DB.users.delete_many({})


async def etapa_rollback_do_admin():
    """A corrida que a checagem previa nao cobre.

    Entre o `find_one` que procura o e-mail e o `insert_one` do usuario existe
    uma janela. Duas abas, um F5 no meio do envio ou um retry de rede caem nela,
    e quem perde bate no indice unico de `users.email`. Sem o rollback, a
    empresa ja gravada fica sem admin nenhum.
    """
    print("\n[rollback do admin]")
    original = DB.users.insert_one

    async def recusa(doc):
        raise DuplicateKeyError("e-mail duplicado")

    DB.users.insert_one = recusa
    try:
        st, det = await erro(rt.create_tenant(corpo(name="Corrida", admin_email="corrida@x.com")))
    finally:
        DB.users.insert_one = original

    check("a corrida perdida devolve 400", st == 400, (st, det))
    check("e a empresa nao ficou orfa",
          await DB.tenants.count_documents({"name": "Corrida"}) == 0,
          await DB.tenants.find({}, {"_id": 0}).to_list(10))


# ---------------------------------------------------------------------------
# Exclusao
# ---------------------------------------------------------------------------

async def etapa_excluir():
    print("\n[excluir]")
    for tid in (FICA, SAI):
        await DB.tenants.insert_one({"id": tid, "name": tid, "plan_id": PLANO,
                                     "status": "active", "created_at": "2026-08-01T00:00:00+00:00"})
        for colecao in rt.COLECOES_DA_EMPRESA:
            await DB[colecao].insert_one({"id": f"{colecao}-{tid}", "tenant_id": tid})
    # Auditoria do super admin: entra na semeadura justamente para provar que a
    # exclusao NAO a leva.
    await DB.impersonation_log.insert_one({"id": "log-1", "tenant_id": SAI,
                                           "super_admin_id": "sa", "iniciado_em": "x"})

    r = await rt.delete_tenant(SAI)
    check("a rota confirma", r["ok"] is True, r)
    check("e diz o que saiu, por colecao",
          len(r["apagados"]) == len(rt.COLECOES_DA_EMPRESA), r["apagados"])

    check("a empresa saiu", await DB.tenants.count_documents({"id": SAI}) == 0)
    faltando = [c for c in rt.COLECOES_DA_EMPRESA
                if await DB[c].count_documents({"tenant_id": SAI}) != 0]
    check("nenhuma colecao da lista ficou com dado da empresa", not faltando, faltando)

    sobrou = [c for c in rt.COLECOES_DA_EMPRESA
              if await DB[c].count_documents({"tenant_id": FICA}) != 1]
    check("e a empresa vizinha ficou intacta em todas elas", not sobrou, sobrou)
    check("a empresa vizinha continua existindo",
          await DB.tenants.count_documents({"id": FICA}) == 1)

    check("a auditoria de impersonation sobreviveu",
          await DB.impersonation_log.count_documents({"tenant_id": SAI}) == 1)


# Colecoes que existem mas nao morrem com a empresa. Cada uma com o motivo,
# porque e essa lista que a varredura abaixo usa para nao gritar sem razao.
FORA_DA_EXCLUSAO = {
    "tenants": "e a propria empresa, apagada em separado",
    "plans": "e global, compartilhada por todas as empresas",
    "impersonation_log": "e auditoria, sobrevive ao objeto auditado",
    # Mesma razao, e aqui mais forte ainda: a exclusao da empresa e uma das
    # acoes que `audit.registrar` existe para gravar. Apagar a trilha junto
    # deixaria a operacao mais destrutiva do sistema sem nenhum rastro.
    "audit_log": "e a trilha de auditoria, e ela registra esta propria exclusao",
    "processed_messages": "nao tem tenant_id e expira sozinha por TTL",
    "ai_connections": "legada, substituida pela credencial por agente",
    "command": "nao e colecao, e o db.command do ping de saude",
}


async def etapa_lista_nao_defasada():
    """A varredura que existe para a lista nao apodrecer.

    Conferir a lista contra ela mesma nao prova nada: o modo de falhar e uma
    colecao nova nascer em outro modulo e ninguem lembrar desta rota. Entao aqui
    se le o codigo do backend, junta toda colecao que alguem toca, e cobra que
    cada uma esteja apagada ou explicitamente dispensada.
    """
    print("\n[lista nao defasada]")
    encontradas = set()
    for arquivo in sorted(BACKEND.rglob("*.py")):
        if "tests" in arquivo.parts or "__pycache__" in arquivo.parts:
            continue
        texto = arquivo.read_text(encoding="utf-8", errors="ignore")
        encontradas.update(re.findall(r"(?<![\w.])db\.([a-z_]+)\.", texto))

    check("a varredura achou colecao para conferir", len(encontradas) > 20, sorted(encontradas))
    orfas = sorted(encontradas - set(rt.COLECOES_DA_EMPRESA) - set(FORA_DA_EXCLUSAO))
    check("toda colecao do backend esta apagada ou dispensada com motivo",
          not orfas,
          f"colecoes sem destino na exclusao de empresa: {orfas}. "
          f"Se tiver tenant_id, entre em COLECOES_DA_EMPRESA; "
          f"se nao tiver, entre em FORA_DA_EXCLUSAO com o motivo.")

    # E o contrario: nome que nao existe mais no codigo nao deve ficar na lista,
    # senao ela vira uma colecao de fantasmas que ninguem tem coragem de tirar.
    fantasmas = sorted(set(rt.COLECOES_DA_EMPRESA) - encontradas)
    check("e a lista nao guarda colecao que o codigo nao usa mais", not fantasmas, fantasmas)


async def etapa_barreira_de_permissao():
    """Chamar a funcao da rota nao executa o `Depends`, entao a guarda e
    conferida onde ela e declarada."""
    print("\n[permissao]")

    def deps_de(fn):
        return [p.default.dependency for p in inspect.signature(fn).parameters.values()
                if hasattr(p.default, "dependency")]

    for fn in (rt.list_tenants, rt.create_tenant, rt.update_tenant,
               rt.delete_tenant, rt.impersonate, rt.global_metrics):
        check(f"{fn.__name__} exige super admin",
              auth.require_super_admin in deps_de(fn), deps_de(fn))

    st, _ = await erro(auth.require_super_admin(
        {"id": "x", "role": "tenant_admin", "tenant_id": FICA}))
    check("admin de empresa e barrado por require_super_admin", st == 403, st)


# ---------------------------------------------------------------------------

def test_suite():
    _conectar_fake()

    async def _tudo():
        await etapa_semear()
        await etapa_senha_curta()
        await etapa_criar()
        await etapa_rollback_do_admin()
        await etapa_excluir()
        await etapa_lista_nao_defasada()
        await etapa_barreira_de_permissao()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
