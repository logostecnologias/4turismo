"""Painel inicial: conta pelo campo certo e resume os modulos novos."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# O backend é o pai desta pasta; resolvido pelo caminho do arquivo para o
# teste rodar de qualquer lugar (pytest na raiz do backend, ou direto).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k, v in {"MONGO_URL": "mongodb://fake/", "DB_NAME": "fake", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

from fakedb import FakeDB

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import routes.dashboard as rdash

_MODULOS_FAKE = (rdash,)


def _conectar_fake():
    """(Re)aponta o `get_db` de cada módulo para o Mongo falso DESTE arquivo.

    Precisa ser chamado no início do teste, não só no import: o pytest importa
    todos os módulos de teste no mesmo processo, e `get_db` é atributo de módulo
    compartilhado.
    """
    db_mod.get_db = lambda: DB
    for _m in _MODULOS_FAKE:
        _m.get_db = lambda: DB


TENANT = "t-1"
ADMIN = {"id": "u-admin", "tenant_id": TENANT, "role": "tenant_admin", "email": "a@x.com"}
OUTRO = {"id": "u-b", "tenant_id": "t-2", "role": "tenant_admin", "email": "b@x.com"}
ok = 0


def check(nome, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  FALHOU  {nome}  -> {extra}")
        raise AssertionError(nome)


def iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


def inicio_do_mes():
    return datetime.now(timezone.utc).replace(
        day=1, hour=0, minute=0, second=0, microsecond=0)


async def semear():
    DB._cols.clear()
    await DB.tenants.insert_one({"id": TENANT})

    # Conversas nos quatro estados do Kanban. `status` fica com um valor
    # diferente de propósito: era por ele que o painel antigo contava, e o
    # teste tem de provar que agora ele é ignorado.
    for sid, estado in (("s1", "bot"), ("s2", "aguardando"), ("s3", "aguardando"),
                        ("s4", "em_atendimento"), ("s5", "finalizado")):
        await DB.conversation_sessions.insert_one({
            "id": sid, "tenant_id": TENANT, "contact_id": "c1",
            "status": "finalizada", "status_atendimento": estado,
            "created_at": iso(days=-2), "updated_at": iso(days=-1)})
    # De outra empresa: não pode entrar em conta nenhuma.
    await DB.conversation_sessions.insert_one({
        "id": "sx", "tenant_id": "t-2", "contact_id": "cx", "status": "ativa",
        "status_atendimento": "aguardando", "created_at": iso(days=-1),
        "updated_at": iso(days=-1)})

    for i in range(4):
        await DB.messages_log.insert_one({
            "id": f"m{i}", "session_id": "s1", "tenant_id": TENANT,
            "direction": "recebida", "content": "x", "type": "text", "meta": {},
            "timestamp": iso(days=-3)})
    # Fora da janela de 30 dias.
    await DB.messages_log.insert_one({
        "id": "m-velha", "session_id": "s1", "tenant_id": TENANT,
        "direction": "recebida", "content": "x", "type": "text", "meta": {},
        "timestamp": iso(days=-45)})

    # Funil: dois abertos, um ganho neste mês, um ganho no mês passado.
    await DB.deals.insert_one({
        "id": "d1", "tenant_id": TENANT, "pipeline_id": "p1", "contact_id": "c1",
        "status": "aberto", "valor": 10000, "fechado_em": None,
        "created_at": iso(days=-10)})
    await DB.deals.insert_one({
        "id": "d2", "tenant_id": TENANT, "pipeline_id": "p1", "contact_id": "c1",
        "status": "aberto", "valor": 5000, "fechado_em": None,
        "created_at": iso(days=-9)})
    await DB.deals.insert_one({
        "id": "d3", "tenant_id": TENANT, "pipeline_id": "p1", "contact_id": "c1",
        "status": "ganho", "valor": 25000,
        "fechado_em": (inicio_do_mes() + timedelta(days=1)).isoformat(),
        "created_at": iso(days=-20)})
    await DB.deals.insert_one({
        "id": "d4", "tenant_id": TENANT, "pipeline_id": "p1", "contact_id": "c1",
        "status": "ganho", "valor": 99000,
        "fechado_em": (inicio_do_mes() - timedelta(days=3)).isoformat(),
        "created_at": iso(days=-60)})
    await DB.deals.insert_one({
        "id": "dx", "tenant_id": "t-2", "pipeline_id": "px", "contact_id": "cx",
        "status": "aberto", "valor": 777000, "fechado_em": None,
        "created_at": iso(days=-1)})

    await DB.activities.insert_one({
        "id": "a1", "tenant_id": TENANT, "titulo": "Atrasada",
        "vence_em": iso(days=-1), "concluida_em": None, "responsavel_id": "u-admin"})
    await DB.activities.insert_one({
        "id": "a2", "tenant_id": TENANT, "titulo": "Hoje",
        "vence_em": iso(hours=2), "concluida_em": None, "responsavel_id": "u-admin"})
    await DB.activities.insert_one({
        "id": "a3", "tenant_id": TENANT, "titulo": "Concluida",
        "vence_em": iso(days=-5), "concluida_em": iso(days=-4),
        "responsavel_id": "u-admin"})

    await DB.contacts.insert_one({"id": "c1", "tenant_id": TENANT, "nome": "Maria"})
    await DB.contacts.insert_one({"id": "c2", "tenant_id": TENANT, "nome": "Joao"})
    await DB.contacts.insert_one({"id": "cx", "tenant_id": "t-2", "nome": "Fora"})
    await DB.bots.insert_one({"id": "b1", "tenant_id": TENANT, "status": "ativo"})
    await DB.bots.insert_one({"id": "b2", "tenant_id": TENANT, "status": "pausado"})
    await DB.whatsapp_connections.insert_one({
        "id": "cn1", "tenant_id": TENANT, "status": "conectado"})
    await DB.whatsapp_connections.insert_one({
        "id": "cn2", "tenant_id": TENANT, "status": "desconectado"})


async def etapa_atendimento(m):
    print("\n[1] Atendimento conta por status_atendimento, nao pelo campo antigo")
    a = m["atendimento"]
    check("1 com o robo", a["bot"] == 1, a)
    check("2 na fila", a["aguardando"] == 2, a)
    check("1 em atendimento", a["em_atendimento"] == 1, a)
    check("1 finalizada", a["finalizado"] == 1, a)
    check("em aberto = fila + em atendimento", a["em_aberto"] == 3, a)
    check("conversa de outra empresa fora da conta",
          a["bot"] + a["aguardando"] + a["em_atendimento"] + a["finalizado"] == 5, a)
    check("mensagens dos 30 dias, sem a de 45", a["mensagens_30d"] == 4, a)


async def etapa_funil(m):
    print("\n[2] Funil")
    f = m["funil"]
    check("2 negocios abertos", f["aberto"]["n"] == 2, f)
    check("15 mil em aberto", f["aberto"]["valor"] == 15000, f)
    check("1 ganho no mes", f["ganho_mes"]["n"] == 1, f)
    check("25 mil ganhos no mes", f["ganho_mes"]["valor"] == 25000, f)
    check("ganho do mes passado nao entra", f["ganho_mes"]["valor"] != 124000, f)
    check("negocio de outra empresa fora", f["aberto"]["valor"] != 792000, f)


async def etapa_agenda(m):
    print("\n[3] Agenda")
    check("1 atividade atrasada", m["agenda"]["atrasadas"] == 1, m["agenda"])
    check("1 para hoje", m["agenda"]["hoje"] == 1, m["agenda"])
    check("2 pendentes (concluida nao conta)", m["agenda"]["pendentes"] == 2, m["agenda"])


async def etapa_base(m):
    print("\n[4] Base")
    b = m["base"]
    check("2 contatos", b["contatos"] == 2, b)
    check("1 bot ativo (pausado nao conta)", b["bots_ativos"] == 1, b)
    check("1 conexao conectada", b["conexoes_conectadas"] == 1, b)


async def etapa_isolamento():
    print("\n[5] Isolamento")
    m = await rdash.dashboard_metrics(hoje_ate=iso(hours=12), semana_ate=iso(days=7),
                                      user=OUTRO)
    check("outra empresa ve so o que e dela",
          m["atendimento"]["aguardando"] == 1, m["atendimento"])
    check("e o funil dela", m["funil"]["aberto"]["valor"] == 777000, m["funil"])
    check("nao ve os contatos alheios", m["base"]["contatos"] == 1, m["base"])
    check("nem as atividades", m["agenda"]["pendentes"] == 0, m["agenda"])


# ---------------------------------------------------------------------------
# Entrada do pytest.
# ---------------------------------------------------------------------------

def test_suite():
    _conectar_fake()

    async def _tudo():
        await semear()
        m = await rdash.dashboard_metrics(hoje_ate=iso(hours=12), semana_ate=iso(days=7),
                                          user=ADMIN)
        await etapa_atendimento(m)
        await etapa_funil(m)
        await etapa_agenda(m)
        await etapa_base(m)
        await etapa_isolamento()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
