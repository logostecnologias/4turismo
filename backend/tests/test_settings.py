"""Teste da tela de Configuracoes: troca de senha, nome, e o resumo de uso.

Onde estao os riscos deste modulo, e por isso o que se testa aqui:

- **A troca de senha** e a unica escrita de credencial que parte do proprio
  usuario. Errar a conferencia da senha atual, deixar o hash antigo valendo, ou
  permitir que o modo suporte troque a senha do Super Admin achando que troca a
  do cliente, sao falhas que nao dao erro nenhum — dao acesso.
- **O resumo de uso** so vale se disser o mesmo que o 403. Um painel que mostra
  "3 de 5" enquanto a criacao e recusada e pior que painel nenhum, porque o
  cliente para de acreditar nos dois. Por isso a etapa de coerencia compara o
  numero da tela com o comportamento do enforcer, em vez de so conferir a conta.
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

from fakedb import FakeDB
from fastapi import HTTPException

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import auth
import plan_limits
import routes.settings as rset

_MODULOS_FAKE = (rset, auth)


def _conectar_fake():
    """Reaplicado a cada teste, nao so no import: o pytest carrega todos os
    modulos de teste no mesmo processo e `get_db` e atributo de modulo."""
    db_mod.get_db = lambda: DB
    for _m in _MODULOS_FAKE:
        _m.get_db = lambda: DB


TENANT_A, TENANT_B = "t-a", "t-b"
PLANO_A = "p-basico"
SENHA_A = "senha-original-1"

ADMIN_A = {"id": "u-a", "tenant_id": TENANT_A, "role": "tenant_admin",
           "name": "Ana", "email": "ana@x.com"}
AGENTE_A = {"id": "u-ag", "tenant_id": TENANT_A, "role": "agent",
            "name": "Aline", "email": "aline@x.com"}
ADMIN_B = {"id": "u-b", "tenant_id": TENANT_B, "role": "tenant_admin",
           "name": "Bruno", "email": "bruno@x.com"}

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


async def etapa_semear():
    print("\n[semear]")
    await DB.plans.insert_one({
        "id": PLANO_A, "nome": "Basico", "preco_mensal": 97.0,
        "max_bots": 2, "max_conexoes": 1, "max_usuarios": 3,
        "max_tokens_ia_mes": 50_000,
        "retencao_midia_dias": 30, "status": "active",
    })
    await DB.tenants.insert_one({"id": TENANT_A, "name": "Logos Tecnologias",
                                 "plan_id": PLANO_A, "status": "active",
                                 "created_at": "2026-03-12T10:00:00+00:00"})
    # Empresa sem plano: o caso "tudo ilimitado".
    await DB.tenants.insert_one({"id": TENANT_B, "name": "Sem Plano ME",
                                 "plan_id": None, "status": "active",
                                 "created_at": "2026-05-01T10:00:00+00:00"})
    for u, senha in ((ADMIN_A, SENHA_A), (AGENTE_A, "outra-senha-9"), (ADMIN_B, "b-senha-77")):
        await DB.users.insert_one({**u, "status": "active",
                                   "password_hash": auth.hash_password(senha)})
    check("semeadura pronta", await DB.users.count_documents({}) == 3)


# ---------------------------------------------------------------------------
# Senha
# ---------------------------------------------------------------------------

async def etapa_troca_de_senha():
    print("\n[troca de senha]")
    NOVA = "nova-senha-forte"

    st, det = await erro(rset.trocar_senha(
        rset.SenhaIn(senha_atual="chute-errado", nova_senha=NOVA), ADMIN_A))
    check("senha atual errada e recusada", st == 400, (st, det))
    doc = await DB.users.find_one({"id": ADMIN_A["id"]})
    check("e o hash nao foi tocado na tentativa recusada",
          auth.verify_password(SENHA_A, doc["password_hash"]))

    st, det = await erro(rset.trocar_senha(
        rset.SenhaIn(senha_atual=SENHA_A, nova_senha="curta1"), ADMIN_A))
    check("nova senha curta e recusada", st == 400, (st, det))
    check("e a recusa diz o minimo", str(rset.SENHA_MINIMA) in (det or ""), det)

    st, det = await erro(rset.trocar_senha(
        rset.SenhaIn(senha_atual=SENHA_A, nova_senha=SENHA_A), ADMIN_A))
    check("repetir a senha atual e recusado", st == 400, (st, det))

    r = await rset.trocar_senha(rset.SenhaIn(senha_atual=SENHA_A, nova_senha=NOVA), ADMIN_A)
    check("troca valida passa", r["ok"] is True, r)
    doc = await DB.users.find_one({"id": ADMIN_A["id"]})
    check("a senha nova vale agora", auth.verify_password(NOVA, doc["password_hash"]))
    check("e a antiga parou de valer",
          not auth.verify_password(SENHA_A, doc["password_hash"]))

    # Trocar a senha de A nao pode encostar em ninguem mais.
    outro = await DB.users.find_one({"id": AGENTE_A["id"]})
    check("a senha do colega ficou intacta",
          auth.verify_password("outra-senha-9", outro["password_hash"]))

    # E o caminho volta ao normal para as etapas seguintes.
    await rset.trocar_senha(rset.SenhaIn(senha_atual=NOVA, nova_senha=SENHA_A), ADMIN_A)
    check("voltou para a senha original", auth.verify_password(
        SENHA_A, (await DB.users.find_one({"id": ADMIN_A["id"]}))["password_hash"]))


async def etapa_modo_suporte():
    print("\n[modo suporte]")
    # Em impersonation o token e do Super Admin com papel trocado: `user["id"]`
    # continua sendo o dele. Sem esta guarda, o suporte trocaria a senha do
    # Super Admin achando que troca a do cliente.
    sa_no_cliente = {**ADMIN_A, "id": "u-super", "impersonation_id": "imp-1"}
    st, det = await erro(rset.trocar_senha(
        rset.SenhaIn(senha_atual=SENHA_A, nova_senha="qualquer-coisa-8"), sa_no_cliente))
    check("modo suporte nao troca senha", st == 403, (st, det))
    st, det = await erro(rset.atualizar_conta(rset.ContaIn(name="Invasor"), sa_no_cliente))
    check("modo suporte nao troca nome", st == 403, (st, det))
    doc = await DB.users.find_one({"id": ADMIN_A["id"]})
    check("e a conta do cliente ficou como estava", doc["name"] == "Ana", doc.get("name"))


async def etapa_nome():
    print("\n[nome]")
    r = await rset.atualizar_conta(rset.ContaIn(name="  Ana Paula  "), ADMIN_A)
    check("nome e aparado", r["name"] == "Ana Paula", r)
    doc = await DB.users.find_one({"id": ADMIN_A["id"]})
    check("e gravado", doc["name"] == "Ana Paula", doc.get("name"))
    st, _ = await erro(rset.atualizar_conta(rset.ContaIn(name="   "), ADMIN_A))
    check("nome so de espaco e recusado", st == 400, st)


# ---------------------------------------------------------------------------
# Empresa e uso
# ---------------------------------------------------------------------------

async def etapa_empresa():
    print("\n[empresa]")
    r = await rset.empresa(ADMIN_A)
    check("devolve o nome da empresa, nao o id",
          r["empresa"]["nome"] == "Logos Tecnologias", r["empresa"])
    check("e o plano com preco", r["plano"]["nome"] == "Basico"
          and r["plano"]["preco_mensal"] == 97.0, r["plano"])
    check("retencao do plano vem junto", r["plano"]["retencao_midia_dias"] == 30, r["plano"])

    # Isolamento: cada admin so ve a propria empresa.
    rb = await rset.empresa(ADMIN_B)
    check("admin de B ve a empresa de B", rb["empresa"]["nome"] == "Sem Plano ME", rb["empresa"])
    check("empresa sem plano devolve plano nulo", rb["plano"] is None, rb["plano"])
    check("e todos os limites dela ficam ilimitados",
          all(l["limite"] is None for l in rb["uso"]), rb["uso"])


async def etapa_uso():
    print("\n[uso do plano]")
    await DB.bots.insert_one({"id": "b1", "tenant_id": TENANT_A})
    await DB.bots.insert_one({"id": "b2", "tenant_id": TENANT_A})
    await DB.bots.insert_one({"id": "b-de-outra", "tenant_id": TENANT_B})
    await DB.whatsapp_connections.insert_one({"id": "c1", "tenant_id": TENANT_A})

    linhas = {l["chave"]: l for l in (await rset.empresa(ADMIN_A))["uso"]}
    check("as quatro linhas aparecem", set(linhas) == {
        "max_bots", "max_conexoes", "max_usuarios", "max_tokens_ia_mes"}, set(linhas))
    check("robos: 2 de 2", (linhas["max_bots"]["usado"], linhas["max_bots"]["limite"]) == (2, 2),
          linhas["max_bots"])
    check("o bot da outra empresa nao entra na conta", linhas["max_bots"]["usado"] == 2)
    check("usuarios: 2 de 3 (A tem admin e atendente)",
          (linhas["max_usuarios"]["usado"], linhas["max_usuarios"]["limite"]) == (2, 3),
          linhas["max_usuarios"])
    check("tokens sao por janela de tempo",
          linhas["max_tokens_ia_mes"]["janela"] == "mês", linhas)


async def etapa_coerencia_com_o_enforcer():
    print("\n[coerencia painel x 403]")
    # O painel diz "2 de 2" para robos. O enforcer tem que recusar o proximo —
    # se um dos dois estiver errado, e aqui que aparece.
    st, det = await erro(plan_limits.enforce_plan_limit(
        DB, TENANT_A, "bots", "max_bots", "robôs"))
    check("com o painel no teto, criar robo e recusado", st == 403, (st, det))

    # Conexoes: painel diz 1 de 1, enforcer recusa.
    st, _ = await erro(plan_limits.enforce_plan_limit(
        DB, TENANT_A, "whatsapp_connections", "max_conexoes", "conexões WhatsApp"))
    check("com o painel no teto, criar conexao e recusada", st == 403, st)

    # Usuarios: painel diz 2 de 3, enforcer deixa passar.
    st, _ = await erro(plan_limits.enforce_plan_limit(
        DB, TENANT_A, "users", "max_usuarios", "usuários"))
    check("abaixo do teto, o enforcer deixa passar", st is None, st)


async def etapa_conta_propria_de_ia():
    print("\n[ia: conta da empresa nao tem teto nosso]")
    linhas = {l["chave"]: l for l in (await rset.empresa(ADMIN_A))["uso"]}
    check("sem conexao propria, o teto do plano vale",
          linhas["max_tokens_ia_mes"]["limite"] == 50_000, linhas["max_tokens_ia_mes"])

    await DB.ai_connections.insert_one(
        {"id": "conn-1", "tenant_id": TENANT_A, "status": "conectada",
         "provedor": "anthropic", "modelo": "claude-haiku-4-5-20251001"})
    linhas = {l["chave"]: l for l in (await rset.empresa(ADMIN_A))["uso"]}
    check("com a conta da empresa conectada, some o teto",
          linhas["max_tokens_ia_mes"]["limite"] is None, linhas["max_tokens_ia_mes"])
    check("e a tela diz de quem e a conta",
          linhas["max_tokens_ia_mes"]["nota"] == "conta da empresa",
          linhas["max_tokens_ia_mes"])

    # Conexao em erro nao conta como conta propria: `resolver_credencial` cai na
    # plataforma, entao o teto do plano volta a valer.
    await DB.ai_connections.update_one({"id": "conn-1"}, {"$set": {"status": "erro"}})
    linhas = {l["chave"]: l for l in (await rset.empresa(ADMIN_A))["uso"]}
    check("conexao em erro nao remove o teto",
          linhas["max_tokens_ia_mes"]["limite"] == 50_000, linhas["max_tokens_ia_mes"])


async def etapa_barreira_de_permissao():
    print("\n[barreira de permissao]")
    # Chamar a funcao da rota direto NAO executa o Depends — a guarda so pode
    # ser conferida onde ela e declarada, na assinatura do endpoint.
    def deps_de(fn):
        return [p.default.dependency for p in inspect.signature(fn).parameters.values()
                if hasattr(p.default, "dependency")]

    check("empresa exige tenant_admin",
          auth.require_tenant_admin in deps_de(rset.empresa), deps_de(rset.empresa))
    # A paleta e de toda a clinica: so o admin troca.
    check("paleta exige tenant_admin",
          auth.require_tenant_admin in deps_de(rset.salvar_aparencia),
          deps_de(rset.salvar_aparencia))
    # Conta e senha sao do proprio usuario: qualquer pessoa autenticada, porque
    # a guarda ali e "so mexe em si mesmo", nao o papel.
    for fn in (rset.atualizar_conta, rset.trocar_senha, rset.salvar_preferencias):
        check(f"{fn.__name__} exige apenas autenticacao",
              auth.get_current_user in deps_de(fn), deps_de(fn))

    st, _ = await erro(auth.require_tenant_admin(
        {"id": "x", "role": "agent", "tenant_id": TENANT_A}))
    check("atendente e barrado por require_tenant_admin", st == 403, st)


async def etapa_aparencia():
    print("\n[aparencia: paleta da empresa e modo do usuario]")
    # Empresa que nunca escolheu: a tela recebe a padrao, nao nulo.
    me = await auth.me(ADMIN_A)
    check("sem escolha, a empresa le a paleta padrao",
          me["paleta"] == "oficial", me)
    check("e o modo vem nulo, para nao apagar a escolha local do navegador",
          me["modo"] is None, me)

    r = await rset.salvar_aparencia(rset.AparenciaIn(paleta="sol"), ADMIN_A)
    check("admin troca a paleta", r["paleta"] == "sol", r)
    t = await DB.tenants.find_one({"id": TENANT_A})
    check("gravada na empresa", (t.get("aparencia") or {}).get("paleta") == "sol", t)
    me_ag = await auth.me(AGENTE_A)
    check("a equipe inteira passa a ver a paleta nova",
          me_ag["paleta"] == "sol", me_ag)
    me_b = await auth.me(ADMIN_B)
    check("e a outra empresa continua com a dela",
          me_b["paleta"] == "oficial", me_b)

    try:
        rset.AparenciaIn(paleta="neon")
        recusou = False
    except Exception:
        recusou = True
    check("paleta fora da lista e recusada na entrada", recusou)

    # Id estranho que chegou ao banco por fora: a tela recebe a padrao, nunca
    # um id que o CSS nao conhece.
    await DB.tenants.update_one({"id": TENANT_B}, {"$set": {"aparencia.paleta": "neon"}})
    me_b = await auth.me(ADMIN_B)
    check("id desconhecido no banco e lido como a padrao", me_b["paleta"] == "oficial", me_b)

    await rset.salvar_preferencias(rset.PreferenciasIn(modo="escuro"), AGENTE_A)
    u = await DB.users.find_one({"id": AGENTE_A["id"]})
    check("modo gravado no usuario", (u.get("preferencias") or {}).get("modo") == "escuro", u)
    me_ag = await auth.me({**AGENTE_A, "preferencias": u["preferencias"]})
    check("e devolvido no /me", me_ag["modo"] == "escuro", me_ag)
    me_a = await auth.me(ADMIN_A)
    check("o modo de um nao muda o do colega", me_a["modo"] is None, me_a)

    # Super admin fora do modo suporte nao tem empresa.
    me_sa = await auth.me({"id": "sa", "role": "super_admin", "tenant_id": None,
                           "name": "SA", "email": "sa@x.com"})
    check("super admin sem empresa recebe paleta nula", me_sa["paleta"] is None, me_sa)


# ---------------------------------------------------------------------------

def test_suite():
    _conectar_fake()

    async def _tudo():
        await etapa_semear()
        await etapa_troca_de_senha()
        await etapa_modo_suporte()
        await etapa_nome()
        await etapa_empresa()
        await etapa_uso()
        await etapa_coerencia_com_o_enforcer()
        await etapa_conta_propria_de_ia()
        await etapa_aparencia()
        await etapa_barreira_de_permissao()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
