"""Teste dos relatorios e das permissoes do atendente."""
import asyncio
import os
import sys
from datetime import date, datetime, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

from fakedb import FakeDB
from fastapi import HTTPException

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import inspect

import relatorios
import routes.reports as rr
import routes.contacts as rc
import routes.deals as rd
import routes.activities as ra

for mod in (rr, rc, rd, ra):
    mod.get_db = lambda: DB

_MODULOS_FAKE = (ra, rc, rd, rr)


def _conectar_fake():
    """(Re)aponta o `get_db` de cada módulo para o Mongo falso DESTE arquivo.

    Precisa ser chamado no início do teste, não só no import: o pytest importa
    todos os módulos de teste no mesmo processo, e `get_db` é atributo de módulo
    compartilhado — sem isto, o último arquivo importado ficaria com o banco de
    todos, e os testes anteriores rodariam contra dados de outra suíte.
    """
    db_mod.get_db = lambda: DB
    for _m in _MODULOS_FAKE:
        _m.get_db = lambda: DB


TENANT = "t-1"
CONN_A, CONN_B = "conn-a", "conn-b"
ADMIN = {"id": "u-admin", "tenant_id": TENANT, "role": "tenant_admin",
         "name": "Marcos", "email": "marcos@x.com"}
# Atendente com acesso só à conexão A.
AGENTE = {"id": "u-bia", "tenant_id": TENANT, "role": "agent", "name": "Bia",
          "email": "bia@x.com"}
AGENTE2 = {"id": "u-caio", "tenant_id": TENANT, "role": "agent", "name": "Caio",
           "email": "caio@x.com"}
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


def iso(**delta):
    return (datetime.now(timezone.utc) + timedelta(**delta)).isoformat()


DE, ATE = iso(days=-30), iso(days=1)


async def semear():
    DB._cols.clear()
    await DB.tenants.insert_one({"id": TENANT})
    for u in (ADMIN, AGENTE, AGENTE2):
        await DB.users.insert_one(dict(u))
    await DB.whatsapp_connections.insert_one({
        "id": CONN_A, "tenant_id": TENANT, "nome": "Comercial", "status": "conectado",
        "phone_number_id": "pn-teste", "waba_id": "waba-teste"})
    await DB.whatsapp_connections.insert_one({
        "id": CONN_B, "tenant_id": TENANT, "nome": "Suporte", "status": "conectado",
        "phone_number_id": "pn-teste", "waba_id": "waba-teste"})
    # Bia so alcanca a conexao A
    await DB.user_connection_access.insert_one({
        "tenant_id": TENANT, "user_id": "u-bia", "whatsapp_connection_id": CONN_A})
    await DB.departments.insert_one({"id": "dep-1", "tenant_id": TENANT, "nome": "Vendas",
                                     "cor": "#2563EB"})
    await DB.contacts.insert_one({"id": "c1", "tenant_id": TENANT, "nome": "Maria",
                                  "numero_whatsapp": "5511900000001"})


async def sessao(sid, conn, *, dias, espera_s=None, atendimento_s=None,
                 atendente=None, dep=None, origem=None, status="finalizado"):
    inicio = datetime.now(timezone.utc) - timedelta(days=dias)
    aguardando = inicio + timedelta(seconds=30)
    assumido = aguardando + timedelta(seconds=espera_s) if espera_s is not None else None
    fim = assumido + timedelta(seconds=atendimento_s) if (assumido and atendimento_s) else None
    await DB.conversation_sessions.insert_one({
        "id": sid, "tenant_id": TENANT, "contact_id": "c1",
        "whatsapp_connection_id": conn, "departamento_id": dep,
        "atendente_id": atendente, "origem": origem,
        "status_atendimento": status, "status": "ativa",
        "created_at": inicio.isoformat(),
        "aguardando_desde": aguardando.isoformat(),
        "assumido_em": assumido.isoformat() if assumido else None,
        "finalizado_em": fim.isoformat() if fim else None,
        "updated_at": inicio.isoformat(),
    })
    return inicio


async def mensagem(sid, direcao, dias):
    quando = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    await DB.messages_log.insert_one({
        "id": f"m{sid}{direcao}{dias}", "session_id": sid, "tenant_id": TENANT,
        "direction": direcao, "content": "x", "type": "text", "meta": {},
        "timestamp": quando})


async def etapa_atendimento():
    print("\n[1] Relatorio de atendimento")
    await semear()
    await sessao("s1", CONN_A, dias=3, espera_s=60, atendimento_s=600,
                 atendente="u-bia", dep="dep-1")
    await sessao("s2", CONN_A, dias=3, espera_s=120, atendimento_s=1200,
                 atendente="u-bia", dep="dep-1")
    await sessao("s3", CONN_B, dias=2, espera_s=1800, atendimento_s=300,
                 atendente="u-caio")
    await sessao("s4", CONN_A, dias=1, espera_s=None, atendimento_s=None,
                 status="aguardando")
    for sid, dias in (("s1", 3), ("s2", 3), ("s3", 2)):
        await mensagem(sid, "recebida", dias)
        await mensagem(sid, "enviada", dias)
    await mensagem("s4", "recebida", 1)

    r = await rr.atendimento(de=DE, ate=ATE, user=ADMIN)
    res = r["resumo"]
    check("4 conversas na janela", res["conversas"] == 4, res)
    check("3 finalizadas", res["finalizadas"] == 3, res)
    check("1 em aberto", res["em_aberto"] == 1, res)
    check("4 mensagens recebidas", res["mensagens_recebidas"] == 4, res)
    check("3 mensagens enviadas", res["mensagens_enviadas"] == 3, res)

    t = r["tempos"]
    check("espera media = (60+120+1800)/3 = 660s", t["espera_media_s"] == 660, t)
    check("p90 da espera pega a cauda (1800)", t["espera_p90_s"] == 1800, t)
    check("amostra da espera e 3", t["espera_amostra"] == 3, t)
    check("atendimento medio = (600+1200+300)/3 = 700s",
          t["atendimento_medio_s"] == 700, t)
    check("conversa sem assumir nao entra na media", t["atendimento_amostra"] == 3, t)

    check("status agrupado", r["por_status"]["finalizado"] == 3
          and r["por_status"]["aguardando"] == 1, r["por_status"])

    dias = {d["dia"]: d for d in r["por_dia"]}
    check("serie por dia tem 3 dias", len(r["por_dia"]) == 3, list(dias))
    check("serie ordenada", [d["dia"] for d in r["por_dia"]] == sorted(dias), list(dias))
    dia3 = (datetime.now(timezone.utc) - timedelta(days=3)).isoformat()[:10]
    check("duas conversas no dia de 3 dias atras", dias[dia3]["conversas"] == 2, dias[dia3])
    check("mensagens contadas no dia certo", dias[dia3]["recebidas"] == 2, dias[dia3])

    bia = next(a for a in r["por_atendente"] if a["atendente_id"] == "u-bia")
    check("Bia com 2 conversas assumidas", bia["assumidas"] == 2, bia)
    check("nome do atendente resolvido", bia["nome"] == "Bia", bia)
    check("tempo medio por atendente", bia["atendimento_medio_s"] == 900, bia)
    check("atendente ordenado por volume",
          r["por_atendente"][0]["atendente_id"] == "u-bia", r["por_atendente"])

    check("departamento com nome e cor",
          r["por_departamento"][0]["nome"] == "Vendas"
          and r["por_departamento"][0]["conversas"] == 2, r["por_departamento"])
    conns = {c["nome"]: c["conversas"] for c in r["por_conexao"]}
    check("por conexao separa A e B", conns == {"Comercial": 3, "Suporte": 1}, conns)


async def etapa_tempos_estranhos():
    print("\n[2] Dados inconsistentes nao contaminam a media")
    check("duracao negativa vira ausente",
          relatorios._segundos(iso(days=1), iso(days=-1)) is None)
    check("data faltando vira ausente", relatorios._segundos(None, iso()) is None)
    check("texto invalido vira ausente", relatorios._segundos("ontem", iso()) is None)
    check("media de lista vazia e None", relatorios._media([]) is None)
    check("p90 de lista vazia e None", relatorios._percentil([], 0.9) is None)
    check("p90 de um valor e ele mesmo", relatorios._percentil([5.0], 0.9) == 5.0)
    check("p90 de 1..10 e 9 ou 10",
          relatorios._percentil([float(i) for i in range(1, 11)], 0.9) in (9.0, 10.0))


async def etapa_escopo_do_atendente():
    print("\n[3] Atendente ve so as conexoes dele")
    r = await rr.atendimento(de=DE, ate=ATE, user=AGENTE)
    check("Bia ve so as 3 conversas da conexao A", r["resumo"]["conversas"] == 3,
          r["resumo"])
    conns = [c["nome"] for c in r["por_conexao"]]
    check("conexao Suporte fica fora", conns == ["Comercial"], conns)
    check("mensagens tambem sao do recorte", r["resumo"]["mensagens_recebidas"] == 3,
          r["resumo"])

    r_admin = await rr.atendimento(de=DE, ate=ATE, user=ADMIN)
    check("admin continua vendo tudo", r_admin["resumo"]["conversas"] == 4,
          r_admin["resumo"])

    st, det = await erro(rr.atendimento(de=DE, ate=ATE, connection_id=CONN_B, user=AGENTE))
    check("pedir conexao fora do escopo da 403", st == 403, (st, det))

    r = await rr.atendimento(de=DE, ate=ATE, connection_id=CONN_A, user=AGENTE)
    check("pedir a propria conexao funciona", r["resumo"]["conversas"] == 3, r["resumo"])

    r = await rr.atendimento(de=DE, ate=ATE, departamento_id="dep-1", user=ADMIN)
    check("filtro por departamento aplica", r["resumo"]["conversas"] == 2, r["resumo"])

    # atendente sem nenhuma conexao liberada nao ve nada
    r = await rr.atendimento(de=DE, ate=ATE, user=AGENTE2)
    check("atendente sem acesso ve zero", r["resumo"]["conversas"] == 0, r["resumo"])


async def etapa_janela_e_teto():
    print("\n[4] Janela e teto")
    st, det = await erro(rr.atendimento(de=None, ate=ATE, user=ADMIN))
    check("sem periodo da 400", st == 400, (st, det))
    st, det = await erro(rr.atendimento(de=ATE, ate=DE, user=ADMIN))
    check("data invertida da 400", st == 400 and "posterior" in det, (st, det))
    st, det = await erro(rr.atendimento(de="ontem", ate=ATE, user=ADMIN))
    check("data ilegivel da 400", st == 400, (st, det))

    r = await rr.atendimento(de=iso(days=-2, hours=-1), ate=ATE, user=ADMIN)
    check("janela curta corta as conversas antigas", r["resumo"]["conversas"] == 2,
          r["resumo"])

    original = relatorios.MAX_SESSOES
    relatorios.MAX_SESSOES = 2
    try:
        st, det = await erro(rr.atendimento(de=DE, ate=ATE, user=ADMIN))
        check("acima do teto da 413 dizendo o numero", st == 413 and "4 conversas" in det,
              (st, det))
    finally:
        relatorios.MAX_SESSOES = original


# ---------------------------------------------------------------------------
# Agenda
#
# O que este bloco protege, e por que cada coisa esta aqui:
#
#   - **O corte de dia e no fuso da empresa.** Um horario das 21h em Brasilia
#     esta gravado como 00:00Z do dia seguinte. Cortar o dia pelo prefixo do ISO
#     (o que os outros relatorios fazem, e de proposito) jogaria esse
#     atendimento para o dia seguinte, e "faltas de sexta" apareceriam no
#     sabado. E o unico jeito de pegar isso e com um horario de noite.
#   - **A taxa de falta e sobre horarios decididos.** Marcacao futura nao e
#     acerto nem erro: contando-a no denominador, a taxa cairia so porque a
#     agenda da semana que vem esta cheia.
#   - **A ocupacao tem um denominador que nao existe gravado.** Ele sai de somar
#     a grade dia a dia, e por isso o teste conta os dias uteis por conta
#     propria, em vez de chamar a mesma funcao que o codigo usa.
#   - **Cancelado e falta liberam o horario**, entao nao podem contar como
#     ocupacao: uma agenda cheia de faltas mostraria 90% de ocupacao com a sala
#     vazia.
# ---------------------------------------------------------------------------

TZ_EMPRESA = ZoneInfo("America/Sao_Paulo")
ANA, BRUNO, SALA, CARLA = "r-ana", "r-bruno", "r-sala", "r-carla"
FERIADO = "2026-08-07"          # uma sexta-feira
AGO_DE = datetime(2026, 8, 1, 0, 0, tzinfo=TZ_EMPRESA).astimezone(timezone.utc).isoformat()
AGO_ATE = datetime(2026, 8, 31, 23, 59, 59, tzinfo=TZ_EMPRESA).astimezone(timezone.utc).isoformat()


def parede(dia, hh, mm=0):
    """Hora de parede em Sao Paulo, gravada como o sistema grava: UTC."""
    return datetime(2026, 8, dia, hh, mm, tzinfo=TZ_EMPRESA).astimezone(timezone.utc).isoformat()


def uteis(primeiro, ultimo, tirando=()):
    """Dias uteis do intervalo, contados aqui e nao pela funcao sob teste."""
    return [d for d in range(primeiro, ultimo + 1)
            if date(2026, 8, d).weekday() < 5 and f"2026-08-{d:02d}" not in tirando]


def grade_semanal(inicio, fim):
    """Grade de segunda a sexta, no formato de `resources.grade` (7 posicoes)."""
    return [[{"inicio": inicio, "fim": fim}]] * 5 + [[], []]


async def etapa_agenda():
    print("\n[5] Relatorio de agenda")
    await DB.scheduling_settings.insert_one({
        "tenant_id": TENANT, "fuso": "America/Sao_Paulo",
        "bloqueios": [{"data": FERIADO, "motivo": "Feriado"}],
    })
    for rid, nome, ini, fim, ativo, nasceu in (
        (ANA, "Ana", "09:00", "18:00", True, "2026-07-01T12:00:00+00:00"),
        (BRUNO, "Bruno", "09:00", "13:00", True, "2026-08-15T12:00:00+00:00"),
        (SALA, "Sala 1", "08:00", "18:00", True, "2026-07-01T12:00:00+00:00"),
        (CARLA, "Carla", "09:00", "18:00", False, "2026-07-01T12:00:00+00:00"),
    ):
        await DB.resources.insert_one({
            "id": rid, "tenant_id": TENANT, "nome": nome, "ativo": ativo,
            "grade": grade_semanal(ini, fim), "excecoes": [], "created_at": nasceu,
        })

    # (dia, hora, duracao, preco, status, origem, recurso, lembrete)
    marcados = [
        ("a1", 3, 9, 60, 100, "concluido", "manual", ANA, True),
        ("a2", 4, 10, 60, 100, "concluido", "bot", ANA, True),
        ("a3", 5, 9, 60, 100, "faltou", "bot", ANA, True),
        ("a4", 6, 9, 60, 100, "faltou", "manual", ANA, False),
        ("a5", 10, 9, 60, 100, "cancelado", "ia", ANA, False),
        ("a6", 28, 9, 60, 200, "agendado", "bot", ANA, False),
        ("a7", 27, 9, 30, None, "confirmado", "manual", BRUNO, False),
        # 21h em Sao Paulo: gravado como 00:00Z do dia 11. E o caso que separa o
        # corte de dia no fuso da empresa do corte pelo texto do ISO.
        ("a8", 10, 21, 60, 50, "concluido", "ia", ANA, False),
        # Sabado: fora da grade de todo mundo (encaixe da recepcao). Ocupa tempo
        # sem que exista grade naquele dia.
        ("a9", 15, 10, 60, 80, "concluido", "manual", BRUNO, True),
    ]
    for aid, dia, hora, dur, preco, status, origem, recurso, lembrete in marcados:
        inicio = parede(dia, hora)
        await DB.appointments.insert_one({
            "id": aid, "tenant_id": TENANT, "inicio": inicio,
            "fim": parede(dia, hora + dur // 60), "duracao_min": dur, "preco": preco,
            "status": status, "origem": origem, "resource_id": recurso,
            "resource_nome": {ANA: "Ana", BRUNO: "Bruno"}[recurso],
            "service_id": "s-corte" if dur == 60 else "s-barba",
            "service_nome": "Corte" if dur == 60 else "Barba",
            "lembrete_enviado_em": "2026-08-01T12:00:00+00:00" if lembrete else None,
        })

    # Passeios com grade de saídas: é deles que sai o denominador da ocupação.
    # (id, capacidade da saída das 9h, dias com saída, ativo, nasceu)
    for sid, nome, cap, ativo, nasceu in (
        ("s-corte", "Corte", 10, True, "2026-07-01T12:00:00+00:00"),
        ("s-barba", "Barba", 4, True, "2026-08-15T12:00:00+00:00"),
        ("s-vazio", "Sem reserva", 6, True, "2026-07-01T12:00:00+00:00"),
        ("s-inativo", "Inativo", 6, False, "2026-07-01T12:00:00+00:00"),
    ):
        await DB.services.insert_one({
            "id": sid, "tenant_id": TENANT, "nome": nome, "ativo": ativo,
            "capacidade": cap, "created_at": nasceu, "excecoes_saidas": [],
            "grade_saidas": [[{"hora": "09:00", "capacidade": None}]] * 5 + [[], []],
        })

    r = await rr.agenda(de=AGO_DE, ate=AGO_ATE, user=ADMIN)
    resumo = r["resumo"]
    check("todos os horarios da janela entram", resumo["marcados"] == 9, resumo)
    check("atendidos, faltas e cancelados separados",
          (resumo["realizados"], resumo["faltas"], resumo["cancelados"]) == (4, 2, 1), resumo)
    check("e o que ainda vai acontecer fica fora dos dois",
          resumo["por_acontecer"] == 2 and resumo["confirmados"] == 1, resumo)
    # 2 faltas em 6 decididos. Com os 2 futuros no denominador daria 25%.
    check("taxa de falta e sobre os decididos", resumo["taxa_falta"] == 33.3, resumo)
    check("taxa de cancelamento e sobre o total", resumo["taxa_cancelamento"] == 11.1, resumo)
    check("o fuso da empresa vem na resposta", r["fuso"] == "America/Sao_Paulo", r["fuso"])

    check("receita realizada soma so os concluidos",
          resumo["receita_realizada"] == 330, resumo)
    check("prevista soma o que ainda vai acontecer",
          resumo["receita_prevista"] == 200, resumo)
    check("perdida soma falta e cancelamento",
          resumo["receita_perdida"] == 300, resumo)
    check("horario sem preco e contado a parte", resumo["sem_preco"] == 1, resumo)
    check("horas realizadas somam a duracao dos concluidos",
          resumo["horas_realizadas"] == 4.0, resumo)

    check("origem separa equipe, bot e IA",
          r["por_origem"] == {"manual": 4, "bot": 3, "ia": 2}, r["por_origem"])

    dias = {d["dia"]: d for d in r["por_dia"]}
    check("o horario das 21h fica no dia 10, o dia da empresa",
          dias.get("2026-08-10", {}).get("marcados") == 2, dias.get("2026-08-10"))
    check("e nao vaza para o dia 11 pelo fuso do UTC",
          "2026-08-11" not in dias, sorted(dias))
    check("no dia 10, um atendido e um cancelado",
          (dias["2026-08-10"]["realizados"], dias["2026-08-10"]["cancelados"]) == (1, 1),
          dias["2026-08-10"])
    check("quem marcou fecha com o total do dia",
          all(d["equipe"] + d["whatsapp"] == d["marcados"] for d in r["por_dia"]),
          r["por_dia"])

    lem = r["lembrete"]
    check("lembrete: 1 falta em 4 decididos com aviso",
          (lem["com"]["decididos"], lem["com"]["faltas"], lem["com"]["taxa_falta"])
          == (4, 1, 25.0), lem["com"])
    check("e 1 em 2 sem aviso",
          (lem["sem"]["decididos"], lem["sem"]["taxa_falta"]) == (2, 50.0), lem["sem"])

    check("ninguem tem 'por profissional' (a reserva por saida nao tem guia)",
          "por_profissional" not in r, sorted(r))
    # Reserva sem `pessoas` (de antes da fase 1) conta como uma pessoa.
    check("pessoas ocupando somam o que tomou lugar",
          resumo["pessoas"] == 6 and resumo["pessoas_realizadas"] == 4, resumo)

    servicos = {s["id"]: s for s in r["por_servico"]}
    check("por passeio separa marcados e atendidos",
          (servicos["s-corte"]["marcados"], servicos["s-corte"]["realizados"]) == (8, 4),
          servicos["s-corte"])
    check("e traz a duracao media", servicos["s-barba"]["duracao_media_min"] == 30,
          servicos["s-barba"])
    check("passeio inativo e sem reserva nao aparece", "s-inativo" not in servicos,
          sorted(servicos))
    check("mas o passeio ativo sem nenhuma reserva aparece, com ocupacao zero, nao nula",
          servicos["s-vazio"]["marcados"] == 0 and servicos["s-vazio"]["ocupacao"] == 0.0,
          servicos["s-vazio"])

    # Ocupacao: so o que tomou lugar (agendado, confirmado, concluido). No
    # Corte: a1, a2, a6, a8 e a9 = 5 pessoas; a3/a4 (faltou) e a5 (cancelado)
    # devolveram o lugar.
    corte = servicos["s-corte"]
    check("falta e cancelamento nao contam como ocupacao", corte["pessoas"] == 5, corte)
    dias_corte = len(uteis(1, 31, tirando=(FERIADO,)))
    check("os lugares oferecidos descontam o feriado da agencia",
          (corte["saidas_oferecidas"], corte["lugares_oferecidos"]) == (dias_corte, dias_corte * 10),
          (corte["saidas_oferecidas"], corte["lugares_oferecidos"], dias_corte))
    check("e a ocupacao e a divisao dos dois",
          corte["ocupacao"] == round(5 / (dias_corte * 10) * 100, 1), corte)
    check("taxa de falta por passeio tambem e sobre decididos",
          corte["taxa_falta"] == 33.3, corte)

    # Barba nasceu no dia 15: os 14 primeiros dias do mes nao eram oferta dela.
    barba = servicos["s-barba"]
    check("passeio cadastrado no meio do mes nao ganha saida retroativa",
          barba["lugares_oferecidos"] == len(uteis(15, 31)) * 4,
          (barba["lugares_oferecidos"], len(uteis(15, 31)) * 4))

    # Janela de um sabado: ninguem tem grade, e a divisao nao pode explodir.
    so_sabado = await rr.agenda(de=parede(15, 0), ate=parede(15, 23), user=ADMIN)
    sab = {p["id"]: p for p in so_sabado["por_servico"]}
    check("sem saida na grade no dia, ocupacao e nula em vez de erro",
          sab["s-corte"]["ocupacao"] is None and sab["s-corte"]["lugares_oferecidos"] == 0,
          sab["s-corte"])
    check("e o horario do sabado continua sendo contado",
          so_sabado["resumo"]["marcados"] == 1, so_sabado["resumo"])

    # Agenda inteira para a equipe inteira: e o oposto do relatorio de
    # atendimento, e de proposito (ver o docstring da rota).
    do_agente = await rr.agenda(de=AGO_DE, ate=AGO_ATE, user=AGENTE)
    check("o atendente ve a agenda toda, sem recorte por conexao",
          do_agente["resumo"]["marcados"] == 9, do_agente["resumo"])

    original = relatorios.MAX_AGENDAMENTOS
    relatorios.MAX_AGENDAMENTOS = 2
    try:
        st, det = await erro(rr.agenda(de=AGO_DE, ate=AGO_ATE, user=ADMIN))
        check("acima do teto da 413 dizendo o numero",
              st == 413 and "9 horários" in det, (st, det))
    finally:
        relatorios.MAX_AGENDAMENTOS = original

    vazio = await rr.agenda(de=parede(1, 0), ate=parede(1, 23), user=ADMIN)
    check("janela sem nada nao inventa taxa nenhuma",
          vazio["resumo"]["marcados"] == 0 and vazio["resumo"]["taxa_falta"] is None,
          vazio["resumo"])


async def etapa_permissoes_do_atendente():
    print("\n[7] O que o atendente pode e o que nao pode")
    # base toda
    lista = await rc.list_contacts(user=AGENTE)
    check("atendente lista a base toda", lista["total"] == 1, lista)
    ficha = await rc.contact_detail("c1", AGENTE)
    check("atendente abre a ficha", ficha["contact"]["nome"] == "Maria", ficha["contact"])
    atualizado = await rc.update_contact("c1", rc.ContactPatch(empresa="Acme"), AGENTE)
    check("atendente edita a ficha", atualizado["empresa"] == "Acme", atualizado)
    nota = await rc.create_note("c1", rc.NotaIn(texto="Ligou hoje"), AGENTE)
    check("atendente anota", nota["autor_nome"] == "Bia", nota)
    novo = await rc.create_contact(
        rc.ContactIn(nome="Novo", numero_whatsapp="5511900000099"), AGENTE)
    check("atendente cadastra contato", novo["id"] is not None)
    check("atendente ve as etiquetas", isinstance(await rc.list_contact_tags(AGENTE), list))
    check("atendente ve as definicoes de campo",
          isinstance(await rc.list_field_defs(AGENTE), list))


    # nota de colega
    nota_admin = await rc.create_note("c1", rc.NotaIn(texto="do admin"), ADMIN)
    st, det = await erro(rc.delete_note("c1", nota_admin["id"], AGENTE))
    check("atendente NAO apaga anotacao de colega", st == 404, (st, det))
    await rc.delete_note("c1", nota["id"], AGENTE)
    check("atendente apaga a propria anotacao",
          await DB.contact_notes.count_documents({"id": nota["id"]}) == 0)
    await rc.delete_note("c1", nota_admin["id"], ADMIN)
    check("admin apaga anotacao de qualquer um",
          await DB.contact_notes.count_documents({"id": nota_admin["id"]}) == 0)

    # funil e agenda
    funis = await rd.list_pipelines(AGENTE)
    check("atendente ve o funil", len(funis) == 1, funis)
    negocio = await rd.create_deal(rd.DealIn(
        titulo="Do atendente", contact_id="c1", pipeline_id=funis[0]["id"], valor=100), AGENTE)
    check("atendente cria negocio", negocio["id"] is not None)
    quadro = await rd.board(funis[0]["id"], user=AGENTE)
    check("atendente abre o quadro", len(quadro["colunas"]) == 6, len(quadro["colunas"]))

    atv = await ra.create_activity(ra.AtividadeIn(
        titulo="Ligar", vence_em=iso(days=1), contact_id="c1"), AGENTE)
    check("atendente cria atividade", atv["responsavel_id"] == "u-bia", atv)
    check("atendente ve a agenda", (await ra.list_activities(user=AGENTE))["total"] >= 1)


def _guarda(fn):
    """Nome da dependencia de permissao declarada no endpoint.

    Chamar a funcao da rota direto (como o resto deste teste faz) nao executa o
    `Depends`, entao a barreira precisa ser conferida onde ela e declarada.
    """
    for _, p in inspect.signature(fn).parameters.items():
        if hasattr(p.default, "dependency"):
            return p.default.dependency.__name__
    return None


async def etapa_barreira_de_permissao():
    print(chr(10) + "[7] A barreira em si")
    from auth import require_tenant_admin, require_tenant_member

    st, det = await erro(require_tenant_admin(AGENTE))
    check("require_tenant_admin recusa atendente com 403", st == 403, (st, det))
    check("require_tenant_admin aceita admin",
          (await require_tenant_admin(ADMIN))["id"] == "u-admin")

    check("require_tenant_member aceita admin",
          (await require_tenant_member(ADMIN))["id"] == "u-admin")
    check("require_tenant_member aceita atendente",
          (await require_tenant_member(AGENTE))["id"] == "u-bia")
    st, det = await erro(require_tenant_member(
        {"id": "sa", "role": "super_admin", "tenant_id": None}))
    check("require_tenant_member recusa super admin (nao e da empresa)", st == 403, (st, det))
    st, det = await erro(require_tenant_member(
        {"id": "x", "role": "agent", "tenant_id": None}))
    check("require_tenant_member recusa usuario sem empresa", st == 403, (st, det))

    esperado_admin = [
        (rc.create_field_def, "cria campo personalizado"),
        (rc.update_field_def, "edita campo personalizado"),
        (rc.delete_field_def, "apaga campo personalizado"),
        (rc.import_contacts, "importa CSV"),
        (rc.export_contacts, "exporta CSV"),
        (rd.create_pipeline, "cria funil"),
        (rd.update_pipeline, "edita etapas do funil"),
        (rd.delete_pipeline, "apaga funil"),
    ]
    for fn, rotulo in esperado_admin:
        check(f"so admin: {rotulo}", _guarda(fn) == "require_tenant_admin", _guarda(fn))

    esperado_equipe = [
        (rc.list_contacts, "lista contatos"),
        (rc.contact_detail, "abre a ficha"),
        (rc.update_contact, "edita a ficha"),
        (rc.create_contact, "cadastra contato"),
        (rc.create_note, "anota"),
        (rc.delete_note, "apaga anotacao"),
        (rc.send_to_contact, "envia mensagem da ficha"),
        (rc.list_field_defs, "le os campos personalizados"),
        (rd.list_pipelines, "ve o funil"),
        (rd.board, "abre o quadro"),
        (rd.create_deal, "cria negocio"),
        (rd.update_deal, "move negocio"),
        (rd.metrics, "ve o relatorio do funil"),
        (ra.list_activities, "ve a agenda"),
        (ra.create_activity, "cria atividade"),
        (ra.complete_activity, "conclui atividade"),
        (rr.atendimento, "ve o relatorio de atendimento"),
    ]
    for fn, rotulo in esperado_equipe:
        check(f"equipe: {rotulo}", _guarda(fn) == "require_tenant_member", _guarda(fn))


async def etapa_envio_respeita_conexao():
    print("\n[8] Atendente fala so pelas conexoes dele")
    contato = await DB.contacts.find_one({"id": "c1"})
    conn = await rc._resolve_conexao(DB, TENANT, contato, None, AGENTE)
    check("sem escolha, cai na conexao permitida", conn["id"] == CONN_A, conn["id"])

    st, det = await erro(rc._resolve_conexao(DB, TENANT, contato, CONN_B, AGENTE))
    check("escolher conexao proibida da 403", st == 403, (st, det))
    conn = await rc._resolve_conexao(DB, TENANT, contato, CONN_B, ADMIN)
    check("admin pode escolher qualquer conexao", conn["id"] == CONN_B, conn["id"])

    # conversa aberta numa conexao fora do alcance: recusa em vez de abrir outra
    await DB.conversation_sessions.insert_one({
        "id": "s-aberta", "tenant_id": TENANT, "contact_id": "c1",
        "whatsapp_connection_id": CONN_B, "status": "ativa",
        "status_atendimento": "bot", "updated_at": iso()})
    st, det = await erro(rc._resolve_conexao(DB, TENANT, contato, None, AGENTE))
    check("conversa aberta em conexao proibida bloqueia o envio", st == 403, (st, det))
    check("erro explica o motivo", "acesso" in (det or ""), det)
    conn = await rc._resolve_conexao(DB, TENANT, contato, None, ADMIN)
    check("para o admin, segue na conexao da conversa", conn["id"] == CONN_B, conn["id"])

    st, det = await erro(rc.send_to_contact("c1", rc.EnvioIn(texto="oi"), AGENTE))
    check("o endpoint de envio tambem barra", st == 403, (st, det))



# ---------------------------------------------------------------------------
# Entradas do pytest.
#
# Uma função por etapa, na ordem em que foram escritas: as etapas compartilham
# o estado do Mongo falso deste módulo e cada uma continua de onde a anterior
# parou. O pytest.ini usa `--dist loadscope`, que fixa o módulo inteiro num
# worker só — é o que preserva essa ordem sob execução paralela.
# ---------------------------------------------------------------------------

def test_suite():
    _conectar_fake()

    async def _tudo():
        await etapa_atendimento()
        await etapa_tempos_estranhos()
        await etapa_escopo_do_atendente()
        await etapa_janela_e_teto()
        await etapa_agenda()
        await etapa_permissoes_do_atendente()
        await etapa_barreira_de_permissao()
        await etapa_envio_respeita_conexao()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
