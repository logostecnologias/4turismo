# -*- coding: utf-8 -*-
"""Teste de fumaça da agenda contra o Mongo REAL.

    cd backend && PYTHONPATH=. python tests/fumaca_real.py

**Não é coletado pelo pytest** (o nome não casa com `test_*.py`), e é de
propósito: ele exige o túnel SSH do Mongo de pé, e a suíte normal precisa rodar
em máquina nova sem depender disso. Vive aqui, e não num scratchpad, porque a
pendência "nunca foi exercitado contra o banco de verdade" volta a cada entrega
e refazer o roteiro do zero é o que faz ela nunca ser fechada.

O que só este arquivo prova, e o Mongo falso (`fakedb.py`) não:

  - os índices de `appointments` são **aceitos** pelo Mongo;
  - as consultas quentes de fato **usam** índice, em vez de varrer a coleção
    (conferido por `explain`, que é a única forma de saber);
  - a agregação de `/agenda/summary` devolve o mesmo que o falso devolvia;
  - o `awaiting` do nó de Agendamento: um dicionário aninhado com as escolhas
    já feitas: sobrevive à ida e volta pelo banco entre uma mensagem do contato
    e a seguinte. Se não sobrevivesse, o bot recomeçaria a conversa a cada
    resposta, e o sintoma seria "ele fica perguntando a mesma coisa".

Tudo roda em tenants próprios (`smoke-agenda`, `smoke-fluxo`) e é **apagado no
fim**. O envio para a Cloud API é dublado: não há como mandar mensagem de verdade
sem ocupar um dos dois slots de gatilho da conexão do cliente, que atendem
gente. O que continua faltando, e exige uma conexão livre, é o toque de um dedo
humano num menu enviado por um fluxo publicado de verdade.
"""
import asyncio
import os
import sys
import uuid
from datetime import datetime, time, timedelta
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from dotenv import load_dotenv
load_dotenv(os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), ".env"))

from db import get_db, close_db
from fastapi import HTTPException

import agenda
import agenda_worker
import ai_tools
import flow_runtime
import routes.flows as rf
import routes.scheduling as rs
import server

TZ = ZoneInfo("America/Sao_Paulo")
T_AGENDA = "smoke-agenda"
T_FLUXO = "smoke-fluxo"
NUMERO = "5500000000002"

COLECOES = ("appointments", "services", "resources", "scheduling_settings",
            "contacts", "conversation_sessions", "messages_log", "bots", "flows",
            "whatsapp_connections", "flow_versions")

ENVIADAS = []
ok = falhas = 0


def check(nome, cond, extra=""):
    global ok, falhas
    if cond:
        ok += 1
        print(f"  OK    {nome}")
    else:
        falhas += 1
        print(f"  FALHA {nome} -> {extra}")


def admin(tenant):
    return {"id": "smoke-admin", "tenant_id": tenant, "role": "tenant_admin",
            "name": "Fumaça", "email": "fumaca@exemplo.invalid"}


def segunda_futura(semanas=3):
    d = datetime.now(TZ).date() + timedelta(weeks=semanas)
    return d + timedelta(days=(0 - d.weekday()) % 7)


SEGUNDA = segunda_futura()


def instante(d, hh, mm=0):
    return agenda._iso(datetime.combine(d, time(hh, mm), tzinfo=TZ))


async def limpar(db, *tenants):
    total = 0
    for c in COLECOES:
        r = await db[c].delete_many({"tenant_id": {"$in": list(tenants)}})
        total += r.deleted_count
    await db.processed_messages.delete_many({"message_id": {"$regex": "^smoke-"}})
    return total


# --- dublês de envio -------------------------------------------------------

async def _texto(cred, numero, texto, *a, **kw):
    ENVIADAS.append(("texto", texto))
    return "wamid.fumaca"


async def _lista(cred, numero, corpo, botao, itens, *a, **kw):
    ENVIADAS.append(("lista", corpo, [i["label"] for i in itens],
                     [i["id"] for i in itens]))
    return "wamid.fumaca"


async def _botoes(cred, numero, corpo, itens, *a, **kw):
    ENVIADAS.append(("botoes", corpo, [i["label"] for i in itens]))
    return "wamid.fumaca"


# ---------------------------------------------------------------------------
# 1. Índices
# ---------------------------------------------------------------------------

async def suite_indices(db):
    print("\n--- índices ---")
    # Mesmo caminho do startup: se um índice novo conflitar com um já existente,
    # é aqui que o boot morreria.
    await server._ensure_indexes(db)
    check(f"os {len(server.INDEXES)} índices da tabela são aceitos pelo Mongo", True)
    for colecao, esperados in (("appointments", 5), ("services", 1),
                               ("resources", 1), ("scheduling_settings", 1)):
        idx = await db[colecao].index_information()
        check(f"{colecao} tem os índices declarados",
              len(idx) >= esperados + 1, sorted(idx))  # +1 pelo _id_


async def usa_indice(db, colecao, filtro, sort=None):
    cmd = {"find": colecao, "filter": filtro}
    if sort:
        cmd["sort"] = sort
    r = await db.command({"explain": cmd, "verbosity": "queryPlanner"})
    texto = str(r["queryPlanner"]["winningPlan"])
    return "IXSCAN" in texto, texto[:160]


# ---------------------------------------------------------------------------
# 2. Agenda: disponibilidade, conflito, agregação
# ---------------------------------------------------------------------------

async def suite_agenda(db):
    print("\n--- agenda ---")
    ADM = admin(T_AGENDA)
    await limpar(db, T_AGENDA)

    corte = await rs.criar_servico(rs.ServicoIn(
        nome="Corte fumaça", duracao_min=30, preco=50.0), ADM)
    longo = await rs.criar_servico(rs.ServicoIn(
        nome="Coloração fumaça", duracao_min=120, preco=250.0), ADM)
    ana = await rs.criar_recurso(rs.RecursoIn(nome="Ana Fumaça"), ADM)
    bruno = await rs.criar_recurso(rs.RecursoIn(nome="Bruno Fumaça"), ADM)
    contato = {"id": str(uuid.uuid4()), "tenant_id": T_AGENDA,
               "nome": "Cliente Fumaça", "numero_whatsapp": "5500000000001"}
    await db.contacts.insert_one(dict(contato))

    config = await agenda.configuracao(db, T_AGENDA)
    check("configuração sem documento devolve os padrões",
          config["fuso"] == "America/Sao_Paulo" and config["passo_min"] == 30)

    livres = await agenda.horarios_livres(db, T_AGENDA, recurso=ana, servico=corte,
                                          dia=SEGUNDA.isoformat(), config=config)
    check("grade padrão gera 16 horários num dia útil", len(livres) == 16,
          [h["hora"] for h in livres])

    doc = await agenda.agendar(db, T_AGENDA, recurso=ana, servico=corte,
                               contato=contato, inicio=instante(SEGUNDA, 14),
                               config=config, origem="manual", criado_por=ADM["id"])
    horas = [h["hora"] for h in await agenda.horarios_livres(
        db, T_AGENDA, recurso=ana, servico=corte, dia=SEGUNDA.isoformat(),
        config=config)]
    check("o horário marcado some da lista de livres", "14:00" not in horas, horas)

    # A ocupação compara datas guardadas como STRING ISO: é o que o falso não prova.
    longos = [h["hora"] for h in await agenda.horarios_livres(
        db, T_AGENDA, recurso=ana, servico=longo, dia=SEGUNDA.isoformat(),
        config=config)]
    check("serviço de 2h que atravessaria as 14h não é oferecido às 13h",
          "13:00" not in longos and "13:30" not in longos, longos)

    try:
        await agenda.agendar(db, T_AGENDA, recurso=ana, servico=corte,
                             contato=contato, inicio=instante(SEGUNDA, 14),
                             config=config)
        check("marcação sobreposta é recusada", False, "não levantou")
    except HTTPException as e:
        check("marcação sobreposta é recusada com 409", e.status_code == 409, e.detail)
    check("a recusa não deixou agendamento órfão gravado",
          await db.appointments.count_documents({"tenant_id": T_AGENDA}) == 1)

    equipe = await agenda.horarios_do_dia(db, T_AGENDA, recursos=[ana, bruno],
                                          servico=corte, dia=SEGUNDA.isoformat(),
                                          config=config)
    catorze = [h for h in equipe if h["hora"] == "14:00"]
    check("14h aparece uma vez só e cai no profissional livre",
          len(catorze) == 1 and catorze[0]["resource_id"] == bruno["id"], catorze)

    resumo = await rs.resumo(de=instante(SEGUNDA, 0),
                             ate=instante(SEGUNDA + timedelta(days=1), 0), user=ADM)
    check("a agregação de /agenda/summary roda no Mongo real",
          resumo["agendado"] == 1 and resumo["total"] == 1, resumo)

    lista = await rs.listar_agendamentos(
        de=instante(SEGUNDA, 0), ate=instante(SEGUNDA + timedelta(days=1), 0), user=ADM)
    check("a listagem da janela devolve o agendamento e o fuso",
          lista["total"] == 1 and lista["fuso"] == "America/Sao_Paulo", lista["total"])

    # --- as consultas quentes realmente usam índice? -----------------------
    usa, txt = await usa_indice(db, "appointments", {
        "tenant_id": T_AGENDA, "resource_id": ana["id"],
        "status": {"$in": list(agenda.OCUPAM)},
        "fim": {"$gt": instante(SEGUNDA, 0)}, "inicio": {"$lt": instante(SEGUNDA, 23)}})
    check("a consulta de ocupação (a mais quente) usa índice", usa, txt)

    usa, txt = await usa_indice(db, "appointments",
                                {"tenant_id": T_AGENDA,
                                 "inicio": {"$gte": instante(SEGUNDA, 0)}},
                                {"inicio": 1, "_id": 1})
    check("a listagem da tela usa índice, inclusive no sort", usa, txt)

    usa, txt = await usa_indice(db, "appointments", {
        "lembrete_enviado_em": None,
        "status": {"$in": [agenda.STATUS_AGENDADO, agenda.STATUS_CONFIRMADO]},
        "inicio": {"$gt": instante(SEGUNDA, 0)}})
    check("a varredura de lembretes usa índice (roda para todas as empresas)", usa, txt)

    # --- lembrete e ferramentas da IA --------------------------------------
    perto = agenda._agora() + timedelta(hours=10)
    prox = await agenda.agendar(db, T_AGENDA, recurso=bruno, servico=corte,
                                contato=contato, inicio=agenda._iso(perto),
                                config=config, forcar=True)
    pendentes = [a for a in await agenda_worker._pendentes(db, agenda._agora())
                 if a["tenant_id"] == T_AGENDA]
    check("a varredura de lembretes encontra o horário próximo",
          any(a["id"] == prox["id"] for a in pendentes))
    texto = agenda_worker.texto_de("lembrete", prox, config)
    check("o texto do lembrete traz serviço e o que responder",
          "Corte fumaça" in texto and "CONFIRMAR" in texto, texto[:80])

    executar = ai_tools.executor(db, T_AGENDA, contato, {"id": "smoke-sessao"})
    r = await executar("consultar_horarios",
                       {"servico": "corte", "dia": SEGUNDA.isoformat()})
    check("a ferramenta consultar_horarios responde com horas reais",
          "com Ana Fumaça" in r or "com Bruno Fumaça" in r, r[:120])
    r = await executar("agendar", {"servico": "Corte fumaça",
                                   "dia": SEGUNDA.isoformat(), "hora": "16:30"})
    check("a ferramenta agendar marca no Mongo real", r.startswith("MARCADO"), r[:120])
    marcado = await db.appointments.find_one({"tenant_id": T_AGENDA, "origem": "ia"})
    check("e o agendamento fica com origem ia e vínculo com a conversa",
          marcado and marcado["session_id"] == "smoke-sessao")

    await agenda.cancelar(db, T_AGENDA, doc["id"], por=ADM["id"], motivo="fumaça")
    horas = [h["hora"] for h in await agenda.horarios_livres(
        db, T_AGENDA, recurso=ana, servico=corte, dia=SEGUNDA.isoformat(),
        config=config)]
    check("cancelar devolve o horário para a lista de livres", "14:00" in horas, horas)
    guardado = await db.appointments.find_one({"id": doc["id"]})
    check("e o registro cancelado continua gravado, com o motivo",
          guardado["status"] == "cancelado" and guardado["motivo_cancelamento"] == "fumaça")


# ---------------------------------------------------------------------------
# 3. Ciclo completo: webhook -> nó de Agendamento -> lembrete -> resposta
# ---------------------------------------------------------------------------

async def suite_fluxo(db):
    print("\n--- fluxo de ponta a ponta ---")
    ADM = admin(T_FLUXO)
    await limpar(db, T_FLUXO)

    flow_runtime.whatsapp.send_text = _texto
    flow_runtime.whatsapp.send_list = _lista
    flow_runtime.whatsapp.send_buttons = _botoes

    corte = await rs.criar_servico(rs.ServicoIn(
        nome="Corte fumaça", duracao_min=30, preco=50.0), ADM)
    ana = await rs.criar_recurso(rs.RecursoIn(nome="Ana Fumaça"), ADM)

    conn_id = str(uuid.uuid4())
    await db.whatsapp_connections.insert_one({
        "id": conn_id, "tenant_id": T_FLUXO, "nome": "Fumaça",
        "phone_number_id": "pn-teste", "waba_id": "waba-teste", "status": "conectado"})

    bot_id, flow_id = str(uuid.uuid4()), str(uuid.uuid4())
    await db.bots.insert_one({
        "id": bot_id, "tenant_id": T_FLUXO, "name": "Bot Fumaça", "status": "ativo",
        "connection_id": conn_id, "trigger": "first_message", "main_flow_id": flow_id})
    await db.flows.insert_one({
        "id": flow_id, "tenant_id": T_FLUXO, "bot_id": bot_id, "name": "Fluxo Fumaça",
        "version": 1, "published_at": agenda._now(),
        "nodes": [
            {"id": "n-start", "type": "start", "data": {"trigger": "first_message"}},
            {"id": "n-ag", "type": "schedule", "data": {
                "label": "Agendar", "service_id": corte["id"],
                "modo_profissional": "qualquer", "dias_oferecidos": 5,
                "formato": "lista"}},
            {"id": "n-ok", "type": "text", "data": {
                "text": "Te espero {agendamento_data}! Qualquer coisa, é só chamar."}},
            {"id": "n-nao", "type": "human_handoff", "data": {}},
            {"id": "n-fim", "type": "end", "data": {}},
        ],
        "edges": [
            {"id": "e1", "source": "n-start", "target": "n-ag", "sourceHandle": "out"},
            {"id": "e2", "source": "n-ag", "target": "n-ok", "sourceHandle": "agendado"},
            {"id": "e3", "source": "n-ag", "target": "n-nao",
             "sourceHandle": "nao_agendado"},
            {"id": "e4", "source": "n-ok", "target": "n-fim", "sourceHandle": "out"},
        ]})

    publicado = await rf.publish_flow(flow_id, ADM)
    check("o fluxo com nó de Agendamento passa na validação de publicação",
          publicado.get("published_at") is not None)

    # --- mensagem 1: a conversa nasce e o nó pergunta o dia ----------------
    ENVIADAS.clear()
    await flow_runtime.handle_incoming_message(
        T_FLUXO, NUMERO, "oi", "smoke-1", connection_id=conn_id,
        push_name="Cliente Fumaça")
    check("a primeira mensagem faz o nó perguntar o dia",
          ENVIADAS and ENVIADAS[-1][0] == "lista", ENVIADAS)
    dias = ENVIADAS[-1][3]
    check("e os dias oferecidos são datas reais com vaga",
          len(dias) > 0 and dias[0][:2] == "20", dias)

    sess = await db.conversation_sessions.find_one(
        {"tenant_id": T_FLUXO, "whatsapp_connection_id": conn_id})
    aw = (sess or {}).get("awaiting") or {}
    check("o awaiting do nó sobreviveu à gravação no Mongo",
          aw.get("tipo") == "agenda" and aw.get("etapa") == "dia", aw)
    check("e ele carrega o serviço já escolhido pelo nó",
          (aw.get("dados") or {}).get("servico") == corte["id"], aw.get("dados"))

    # --- mensagem 2: o contato toca no primeiro dia ------------------------
    ENVIADAS.clear()
    await flow_runtime.handle_incoming_message(
        T_FLUXO, NUMERO, "", "smoke-2", connection_id=conn_id,
        escolha={"id": dias[0], "titulo": ""})
    check("tocar no dia faz o nó perguntar a hora",
          ENVIADAS and ENVIADAS[-1][0] == "lista", ENVIADAS)
    horas = ENVIADAS[-1][3]
    check("as horas vêm como opções da lista", len(horas) > 0, ENVIADAS[-1][2][:4])

    msgs = await db.messages_log.find(
        {"tenant_id": T_FLUXO, "session_id": sess["id"], "direction": "recebida"}
    ).sort([("timestamp", 1), ("_id", 1)]).to_list(10)
    check("a escolha entrou no histórico com o RÓTULO, não com o id",
          msgs and msgs[-1]["content"] and not msgs[-1]["content"].startswith("20"),
          msgs[-1]["content"] if msgs else None)

    # --- mensagem 3: o contato toca na hora e o horário é marcado ----------
    ENVIADAS.clear()
    await flow_runtime.handle_incoming_message(
        T_FLUXO, NUMERO, "", "smoke-3", connection_id=conn_id,
        escolha={"id": horas[0], "titulo": ""})
    textos = [e[1] for e in ENVIADAS if e[0] == "texto"]
    check("o contato recebe a confirmação do agendamento",
          any("Corte fumaça" in t for t in textos), textos)
    check("e o nó seguinte do fluxo roda, usando a variável do agendamento",
          any("Te espero" in t for t in textos), textos)

    marcado = await db.appointments.find_one({"tenant_id": T_FLUXO})
    check("o agendamento foi gravado com origem bot",
          marcado and marcado["origem"] == "bot")
    check("amarrado à conversa e à conexão em que aconteceu",
          marcado and marcado["session_id"] == sess["id"]
          and marcado["whatsapp_connection_id"] == conn_id)
    check("com o contato criado pelo próprio webhook",
          marcado and marcado["contact_numero"] == NUMERO)

    sess = await db.conversation_sessions.find_one({"id": sess["id"]})
    check("o awaiting foi limpo ao terminar", sess.get("awaiting") is None)
    check("as variáveis do agendamento ficaram na conversa",
          sess["variables"].get("agendamento_id") == marcado["id"])

    config = await agenda.configuracao(db, T_FLUXO)
    livres = await agenda.horarios_do_dia(
        db, T_FLUXO, recursos=[ana], servico=corte,
        dia=agenda.local(marcado["inicio"], TZ).date().isoformat(), config=config)
    check("o horário marcado pelo bot sumiu da lista de livres",
          all(h["inicio"] != marcado["inicio"] for h in livres))

    # --- lembrete e a resposta do contato ----------------------------------
    # Pelo caminho real: o fluxo terminou no nó Fim, então aquela conversa está
    # fechada e o lembrete abre outra por `outbound.ensure_session`: que é o
    # que acontece em produção horas depois.
    ENVIADAS.clear()
    enviado = await agenda_worker.avisar(db, T_FLUXO, marcado, "lembrete", config,
                                         marcar_espera=True)
    check("o lembrete sai pelo caminho real (outbound + send_human_message)", enviado)
    check("e o texto do lembrete chega ao contato",
          any("CONFIRMAR" in e[1] for e in ENVIADAS if e[0] == "texto"), ENVIADAS[-1:])
    sess = await db.conversation_sessions.find_one(
        {"tenant_id": T_FLUXO, "agenda_aguardando": marcado["id"]})
    check("a conversa do lembrete guarda de qual agendamento a resposta fala",
          sess is not None)

    ENVIADAS.clear()
    await flow_runtime.handle_incoming_message(
        T_FLUXO, NUMERO, "quero cancelar meu pedido de ontem", "smoke-4",
        connection_id=conn_id)
    atual = await db.appointments.find_one({"id": marcado["id"]})
    check("mensagem que só CONTÉM 'cancelar' não cancela o horário",
          atual["status"] == "agendado", atual["status"])

    ENVIADAS.clear()
    await flow_runtime.handle_incoming_message(
        T_FLUXO, NUMERO, "CONFIRMAR", "smoke-5", connection_id=conn_id)
    atual = await db.appointments.find_one({"id": marcado["id"]})
    check("CONFIRMAR confirma a presença pelo caminho real do webhook",
          atual["status"] == "confirmado", atual["status"])
    check("e o contato recebe a resposta",
          any("confirmada" in e[1] for e in ENVIADAS if e[0] == "texto"), ENVIADAS)
    sess = await db.conversation_sessions.find_one({"id": sess["id"]})
    check("a espera de confirmação foi limpa",
          sess.get("agenda_aguardando") is None)


# ---------------------------------------------------------------------------

async def principal():
    db = get_db()
    await db.command("ping")
    print(f"\n=== fumaça da agenda no Mongo real ===")

    await suite_indices(db)
    await suite_agenda(db)
    await suite_fluxo(db)

    print("\n--- limpando ---")
    print(f"  {await limpar(db, T_AGENDA, T_FLUXO)} documentos de teste removidos")
    restante = sum([await db[c].count_documents(
        {"tenant_id": {"$in": [T_AGENDA, T_FLUXO]}}) for c in COLECOES])
    check("nada do teste ficou no banco", restante == 0, restante)

    print(f"\n{ok} OK, {falhas} falha(s)")
    return falhas


if __name__ == "__main__":
    try:
        codigo = asyncio.run(principal())
    except Exception as e:
        print(f"\nNAO RODOU: {type(e).__name__}: {e}")
        print("O tunel SSH do Mongo precisa estar de pe.")
        codigo = 1
    close_db()
    sys.exit(1 if codigo else 0)
