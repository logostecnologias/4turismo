"""Teste das Saídas (passeios, vagas e reservas), com Mongo falso.

O que este arquivo cobre é o que falha em silêncio: vaga que continua
aparecendo depois de vendida, grupo que passa da capacidade da van, lugar que
não volta quando a reserva é cancelada (ou volta duas vezes), solicitação
esquecida trancando a saída, feriado que não fecha, e a resposta ao lembrete
mexendo na reserva errada.

O contador de lugares é a peça central, e a invariante que quase toda etapa
confere é a mesma: **o contador de cada saída é a soma das pessoas das reservas
que ocupam lugar**. Se uma transição esquece de mexer nele, a saída aparece
lotada com lugar sobrando, ou aceita gente além da capacidade.

O que este arquivo **não** cobre, e continua exigindo teste de fumaça com o
banco no ar: a corrida real entre duas reservas simultâneas (o Mongo falso
nunca suspende a execução, então duas tasks rodam uma inteira depois da outra)
e a unicidade do índice de `saidas`, que só o Mongo real aplica. O que dá para
conferir aqui é o mecanismo: o `$inc` condicional e o caminho de
`DuplicateKeyError`.
"""
import asyncio
import os
import sys
from datetime import datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

from fakedb import FakeDB
from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import agenda as dominio
import agenda_flow
import agenda_worker
import hoje
import ai_tools
import saidas
import routes.scheduling as rs

_MODULOS_FAKE = (rs, agenda_worker, agenda_flow)


def _conectar_fake():
    """(Re)aponta o `get_db` de cada módulo para o Mongo falso DESTE arquivo.

    Chamado no início do teste, não só no import: o pytest importa todos os
    módulos de teste no mesmo processo, e `get_db` é atributo de módulo
    compartilhado, sem isto o último arquivo importado ficaria com o banco de
    todos.
    """
    db_mod.get_db = lambda: DB
    for m in _MODULOS_FAKE:
        m.get_db = lambda: DB


TENANT = "t-1"
OUTRO = "t-2"
ADMIN = {"id": "u-admin", "tenant_id": TENANT, "role": "tenant_admin",
         "name": "Dona da Agência", "email": "a@x.com"}
ATENDENTE = {"id": "u-rec", "tenant_id": TENANT, "role": "agent",
             "name": "Atendimento", "email": "r@x.com"}

TZ = ZoneInfo("America/Sao_Paulo")
ok = 0


def check(nome, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  FALHOU  {nome}  -> {extra}")
        raise AssertionError(nome)


async def erro(coro, status=None):
    """Roda esperando HTTPException. Devolve o detalhe."""
    try:
        await coro
    except HTTPException as e:
        if status is not None and e.status_code != status:
            raise AssertionError(f"esperava {status}, veio {e.status_code}: {e.detail}")
        return e.detail
    raise AssertionError("esperava HTTPException e não veio")


# ---------------------------------------------------------------------------
# Datas de referência
# ---------------------------------------------------------------------------
#
# Todas as datas caem numa segunda-feira futura. Datas relativas ao dia da
# execução fariam o teste passar numa terça e falhar num domingo. Quatro
# semanas, e não oito: a antecedência máxima padrão é de 60 dias.

def _segunda_futura(semanas: int = 4):
    d = datetime.now(TZ).date() + timedelta(weeks=semanas)
    return d + timedelta(days=(0 - d.weekday()) % 7)


SEGUNDA = _segunda_futura()
TERCA = SEGUNDA + timedelta(days=1)
QUARTA = SEGUNDA + timedelta(days=2)
DOMINGO = SEGUNDA + timedelta(days=6)


def instante(d, hh, mm=0) -> str:
    return dominio._iso(datetime.combine(d, time(hh, mm), tzinfo=TZ))


def grade_seg_a_sab(*saidas_do_dia):
    dia = [{"hora": h, "capacidade": c} for h, c in saidas_do_dia]
    return [list(dia) for _ in range(6)] + [[]]


async def contador(passeio_id, inicio) -> int:
    c = await DB.saidas.find_one({"tenant_id": TENANT, "service_id": passeio_id,
                                  "inicio": inicio})
    return int((c or {}).get("reservados") or 0)


async def soma_ocupando(passeio_id, inicio) -> int:
    total = 0
    async for a in DB.appointments.find({"tenant_id": TENANT, "service_id": passeio_id,
                                         "inicio": inicio,
                                         "status": {"$in": list(dominio.OCUPAM)}}):
        total += int(a.get("pessoas") or 0)
    return total


async def invariante(nome, passeio_id, inicio):
    c, s = await contador(passeio_id, inicio), await soma_ocupando(passeio_id, inicio)
    check(f"contador = pessoas que ocupam ({nome})", c == s, (c, s))


async def reservar(ctx, passeio="lagoa", dia=None, hh=8, adultos=1, criancas=0,
                   contato="c-1", **kw):
    contato_doc = await DB.contacts.find_one({"id": contato}, {"_id": 0})
    config = await dominio.configuracao(DB, TENANT)
    return await saidas.reservar(DB, TENANT, passeio=ctx[passeio], contato=contato_doc,
                                 inicio=instante(dia or SEGUNDA, hh), adultos=adultos,
                                 criancas=criancas, config=config, **kw)


# ---------------------------------------------------------------------------
# Etapas
# ---------------------------------------------------------------------------

async def etapa_semear():
    _conectar_fake()
    await DB.tenants.insert_one({"id": TENANT, "nome": "Agência Teste"})
    await DB.users.insert_one(dict(ADMIN))
    await DB.users.insert_one(dict(ATENDENTE))
    for i, nome in enumerate(["Maria Souza", "João Lima", "Ana Prado"], start=1):
        await DB.contacts.insert_one({
            "id": f"c-{i}", "tenant_id": TENANT, "nome": nome,
            "numero_whatsapp": f"551199999000{i}"})

    # Lagoa Azul: 10 lugares às 8h e a van menor, de 4, às 14h. Segunda a sábado.
    lagoa = await rs.criar_servico(rs.ServicoIn(
        nome="Lagoa Azul", duracao_min=480, preco=150.0, preco_crianca=75.0,
        capacidade=10, grade_saidas=grade_seg_a_sab(("14:00", 4), ("08:00", None))), ADMIN)
    # Circuito: 6 lugares às 9h, todo dia, criança paga igual.
    circuito = await rs.criar_servico(rs.ServicoIn(
        nome="Circuito Lagoa Bonita", duracao_min=360, preco=200.0, capacidade=6,
        grade_saidas=[[{"hora": "09:00"}]] * 7), ADMIN)
    # Passeio sem saída nenhuma: existe, mas não pode ser oferecido.
    sem = await rs.criar_servico(rs.ServicoIn(nome="Passeio de barco", duracao_min=120,
                                              capacidade=8), ADMIN)

    check("a grade sai ordenada por hora",
          [s["hora"] for s in lagoa["grade_saidas"][0]] == ["08:00", "14:00"],
          lagoa["grade_saidas"][0])
    check("capacidade vazia na saída fica nula (usa a do passeio)",
          lagoa["grade_saidas"][0][0]["capacidade"] is None)
    check("grade ausente vira sete dias vazios, não erro",
          sem["grade_saidas"] == [[]] * 7, sem["grade_saidas"])
    com_saida = {p["id"] for p in await saidas.passeios_com_saida(DB, TENANT)}
    check("passeio sem saída não entra no que bot e IA oferecem",
          com_saida == {lagoa["id"], circuito["id"]}, com_saida)
    return {"lagoa": lagoa, "circuito": circuito, "sem": sem}


async def etapa_grade_invalida():
    _conectar_fake()
    detalhe = await erro(rs.criar_servico(rs.ServicoIn(
        nome="Repetido", duracao_min=60, grade_saidas=[[{"hora": "08:00"}, {"hora": "08:00"}],
                                                        [], [], [], [], [], []]), ADMIN), 400)
    check("a mesma hora duas vezes no dia é recusada, com o dia no texto",
          "Segunda" in detalhe and "duas vezes" in detalhe, detalhe)
    detalhe = await erro(rs.criar_servico(rs.ServicoIn(
        nome="Zerado", duracao_min=60,
        grade_saidas=[[{"hora": "08:00", "capacidade": 0}], [], [], [], [], [], []]), ADMIN), 400)
    check("capacidade zero na saída é recusada", "capacidade" in detalhe, detalhe)
    await erro(rs.criar_servico(rs.ServicoIn(
        nome="Curto", duracao_min=60, grade_saidas=[[], []]), ADMIN), 400)
    check("grade que não tem 7 dias é recusada", True)
    await erro(rs.criar_servico(rs.ServicoIn(
        nome="Hora torta", duracao_min=60,
        grade_saidas=[[{"hora": "8h"}], [], [], [], [], [], []]), ADMIN), 400)
    check("hora fora do formato HH:MM é recusada", True)
    detalhe = await erro(rs.criar_servico(rs.ServicoIn(
        nome="Exceção dupla", duracao_min=60, excecoes_saidas=[
            {"data": SEGUNDA.isoformat()}, {"data": SEGUNDA.isoformat()}]), ADMIN), 400)
    check("duas exceções para o mesmo dia são recusadas", "duas exceções" in detalhe, detalhe)
    await erro(rs.salvar_config(rs.ConfigIn(fuso="Marte/Olympus"), ADMIN), 400)
    check("fuso desconhecido é recusado na entrada", True)


async def etapa_saidas_da_janela(ctx):
    _conectar_fake()
    r = await rs.listar_saidas(de=SEGUNDA.isoformat(), ate=DOMINGO.isoformat(),
                               service_id=None, user=ATENDENTE)
    check("a janela devolve um dia por data", len(r["dias"]) == 7, len(r["dias"]))
    seg = r["dias"][0]
    check("segunda tem as duas saídas da Lagoa e a do Circuito, em ordem",
          [(s["hora"], s["service_nome"]) for s in seg["saidas"]] ==
          [("08:00", "Lagoa Azul"), ("09:00", "Circuito Lagoa Bonita"),
           ("14:00", "Lagoa Azul")], [(s["hora"], s["service_nome"]) for s in seg["saidas"]])
    oito = seg["saidas"][0]
    check("saída sem capacidade própria herda a do passeio",
          oito["capacidade"] == 10 and oito["vagas"] == 10, oito)
    check("saída com capacidade própria usa a dela", seg["saidas"][2]["capacidade"] == 4)
    check("a hora é a de parede da agência e o instante é UTC",
          oito["inicio"] == instante(SEGUNDA, 8) and oito["inicio"].endswith("+00:00"), oito)
    check("saída dentro da antecedência e do teto está aberta", oito["aberta"])
    dom = r["dias"][6]
    check("domingo só tem o Circuito (a Lagoa não sai)",
          [s["service_nome"] for s in dom["saidas"]] == ["Circuito Lagoa Bonita"], dom)
    await erro(rs.listar_saidas(de=SEGUNDA.isoformat(),
                                ate=(SEGUNDA + timedelta(days=90)).isoformat(),
                                service_id=None, user=ATENDENTE), 400)
    check("janela grande demais é recusada em vez de varrer meses", True)


async def etapa_reservar_ocupa(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    doc = await reservar(ctx, adultos=3, criancas=1)
    check("a reserva da agência nasce Confirmada", doc["status"] == "confirmado", doc["status"])
    check("o grupo fica gravado", (doc["adultos"], doc["criancas"], doc["pessoas"]) == (3, 1, 4))
    check("o total usa o preço de criança", doc["preco"] == 3 * 150 + 75, doc["preco"])
    check("e os preços unitários ficam gravados (o combinado não muda depois)",
          doc["preco_unitario"] == 150 and doc["preco_crianca"] == 75)
    check("reserva da agência não expira", doc["expira_em"] is None)
    check("a reserva aponta para o contador da saída", bool(doc["saida_id"]))
    await invariante("depois da primeira reserva", lagoa["id"], instante(SEGUNDA, 8))

    r = await rs.listar_saidas(de=SEGUNDA.isoformat(), ate=SEGUNDA.isoformat(),
                               service_id=lagoa["id"], user=ATENDENTE)
    oito = r["dias"][0]["saidas"][0]
    check("a tela vê 4 de 10 e 6 vagas", (oito["reservados"], oito["vagas"]) == (4, 6), oito)

    circ = await reservar(ctx, "circuito", hh=9, adultos=2, criancas=2)
    check("sem preço de criança, criança paga como adulto", circ["preco"] == 800, circ["preco"])
    ctx["primeira"] = doc


async def etapa_capacidade(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    inicio = instante(SEGUNDA, 8)
    await reservar(ctx, adultos=6, contato="c-2")
    await invariante("saída cheia", lagoa["id"], inicio)
    detalhe = await erro(reservar(ctx, adultos=1, contato="c-3"), 409)
    check("saída cheia recusa até uma pessoa, dizendo que lotou", "lotada" in detalhe, detalhe)
    await invariante("recusa não mexe no contador", lagoa["id"], inicio)

    detalhe = await erro(reservar(ctx, hh=14, adultos=5), 409)
    check("grupo maior que a van recusado dizendo quantos lugares restam",
          "restam 4" in detalhe and "grupo tem 5" in detalhe, detalhe)
    check("e a recusa não cria vaga fantasma no contador",
          await contador(lagoa["id"], instante(SEGUNDA, 14)) == 0)

    await reservar(ctx, hh=14, adultos=4)
    check("o grupo exato na van de 4 cabe", await contador(lagoa["id"], instante(SEGUNDA, 14)) == 4)

    detalhe = await erro(reservar(ctx, hh=10), 409)
    check("hora que não é saída do passeio é recusada", "não tem saída" in detalhe, detalhe)
    detalhe = await erro(reservar(ctx, adultos=0, criancas=0, dia=TERCA), 400)
    check("reserva sem ninguém é recusada", "pelo menos uma" in detalhe, detalhe)


async def etapa_duplicate_key(ctx):
    """Duas conversas criando o contador da mesma saída ao mesmo tempo.

    No Mongo real a segunda bate no índice único e leva `DuplicateKeyError`. O
    que precisa acontecer é ela seguir para o `$inc` condicional, não falhar a
    reserva do turista.
    """
    _conectar_fake()
    original = DB.saidas.update_one

    async def bate_no_indice(filtro, update, upsert=False, **kw):
        if upsert:
            await original(filtro, update, upsert=True)
            raise DuplicateKeyError("E11000 duplicate key")
        return await original(filtro, update, upsert=upsert, **kw)

    DB.saidas.update_one = bate_no_indice
    try:
        doc = await reservar(ctx, dia=TERCA, adultos=2)
    finally:
        DB.saidas.update_one = original
    check("DuplicateKeyError no upsert não derruba a reserva", doc["pessoas"] == 2)
    await invariante("depois do DuplicateKeyError", ctx["lagoa"]["id"], instante(TERCA, 8))
    n = await DB.saidas.count_documents({"tenant_id": TENANT, "service_id": ctx["lagoa"]["id"],
                                         "inicio": instante(TERCA, 8)})
    check("e continua existindo um contador só para a saída", n == 1, n)


async def etapa_cancelar_libera(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    inicio = instante(SEGUNDA, 8)
    antes = await contador(lagoa["id"], inicio)
    await saidas.cancelar(DB, TENANT, ctx["primeira"]["id"], por="u-rec", motivo="choveu")
    check("cancelar devolve os 4 lugares", await contador(lagoa["id"], inicio) == antes - 4)
    await saidas.cancelar(DB, TENANT, ctx["primeira"]["id"])
    check("cancelar de novo não devolve duas vezes",
          await contador(lagoa["id"], inicio) == antes - 4)
    await invariante("depois do cancelamento", lagoa["id"], inicio)
    doc = await DB.appointments.find_one({"id": ctx["primeira"]["id"]})
    check("o registro fica, com o motivo", doc["status"] == "cancelado"
          and doc["motivo_cancelamento"] == "choveu")
    check("a agenda antiga delega para o contador",
          dominio.cancelar.__module__ == "agenda")  # o nome continua; a decisão é de saidas


async def etapa_status(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    inicio = instante(QUARTA, 8)
    doc = await reservar(ctx, dia=QUARTA, adultos=8)
    await saidas.mudar_status(DB, TENANT, doc["id"], "faltou")
    check("'não compareceu' devolve o lugar", await contador(lagoa["id"], inicio) == 0)
    outro = await reservar(ctx, dia=QUARTA, adultos=5, contato="c-2")
    detalhe = await erro(saidas.mudar_status(DB, TENANT, doc["id"], "concluido"), 409)
    check("desfazer o 'não compareceu' passa pela capacidade (o lugar foi vendido)",
          "restam 5" in detalhe, detalhe)
    await invariante("recusa de reativação", lagoa["id"], inicio)
    await saidas.cancelar(DB, TENANT, outro["id"])
    await saidas.mudar_status(DB, TENANT, doc["id"], "concluido")
    check("com lugar de volta, reativar ocupa de novo", await contador(lagoa["id"], inicio) == 8)
    await invariante("depois de reativar", lagoa["id"], inicio)
    await erro(saidas.mudar_status(DB, TENANT, doc["id"], "agendado"), 400)
    check("reserva não volta a ser solicitação", True)
    await erro(saidas.cancelar(DB, TENANT, doc["id"]), 409)
    check("passeio que já aconteceu não é cancelado", True)
    await erro(saidas.mudar_status(DB, TENANT, doc["id"], "inventado"), 400)
    check("status desconhecido é recusado", True)


async def etapa_alterar_pessoas(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    inicio = instante(TERCA, 8)
    doc = await DB.appointments.find_one({"tenant_id": TENANT, "inicio": inicio,
                                          "status": "confirmado"})
    novo = await saidas.alterar_pessoas(DB, TENANT, doc["id"], adultos=4, criancas=2)
    check("aumentar o grupo cabendo", novo["pessoas"] == 6 and novo["preco"] == 4 * 150 + 2 * 75,
          novo)
    await invariante("depois de aumentar", lagoa["id"], inicio)
    detalhe = await erro(saidas.alterar_pessoas(DB, TENANT, doc["id"], adultos=11, criancas=0), 409)
    check("aumentar além da capacidade é recusado dizendo o que resta",
          "restam 4" in detalhe, detalhe)
    await invariante("depois da recusa", lagoa["id"], inicio)
    await saidas.alterar_pessoas(DB, TENANT, doc["id"], adultos=1, criancas=0)
    check("diminuir devolve lugar", await contador(lagoa["id"], inicio) == 1)
    await invariante("depois de diminuir", lagoa["id"], inicio)

    # O preço usado é o gravado na reserva: reajustar o passeio depois não muda
    # o que o turista combinou.
    await DB.services.update_one({"id": lagoa["id"]}, {"$set": {"preco": 999.0}})
    again = await saidas.alterar_pessoas(DB, TENANT, doc["id"], adultos=2, criancas=0)
    check("mudar o grupo recalcula pelo preço combinado, não pelo de hoje",
          again["preco"] == 300, again["preco"])
    await DB.services.update_one({"id": lagoa["id"]}, {"$set": {"preco": 150.0}})
    ctx["lagoa"] = await DB.services.find_one({"id": lagoa["id"]}, {"_id": 0})


async def etapa_solicitacao_expira(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    sexta = SEGUNDA + timedelta(days=4)
    inicio = instante(sexta, 14)
    pedido = await reservar(ctx, dia=sexta, hh=14, adultos=3, origem="bot")
    check("o bot cria Solicitada", pedido["status"] == "agendado")
    check("e ela tem prazo", pedido["expira_em"] is not None)
    await erro(reservar(ctx, dia=sexta, hh=14, adultos=1, origem="ia", status="confirmado"), 400)
    check("bot e IA nunca criam reserva Confirmada", True)

    # Antes do prazo nada acontece.
    n = await saidas.expirar_solicitacoes(DB, dominio._agora())
    check("solicitação dentro do prazo não expira", n == 0 and
          await contador(lagoa["id"], inicio) == 3)

    # Depois do prazo, uma reserva nova na saída cheia libera o lugar preso.
    await reservar(ctx, dia=sexta, hh=14, adultos=1, contato="c-2")
    check("saída de 4 com 3 solicitados e 1 confirmado está cheia",
          await contador(lagoa["id"], inicio) == 4)
    await DB.appointments.update_one({"id": pedido["id"]},
                                     {"$set": {"expira_em": dominio._iso(dominio._agora()
                                                                         - timedelta(minutes=1))}})
    doc = await reservar(ctx, dia=sexta, hh=14, adultos=3, contato="c-3")
    check("na hora de reservar, a solicitação vencida devolve o lugar", doc["pessoas"] == 3)
    vencida = await DB.appointments.find_one({"id": pedido["id"]})
    check("e é cancelada com o motivo à vista", vencida["status"] == "cancelado"
          and "prazo" in vencida["motivo_cancelamento"], vencida)
    await invariante("depois da expiração", lagoa["id"], inicio)

    # O worker varre as outras.
    outro = await reservar(ctx, dia=sexta, hh=8, adultos=2, origem="ia")
    futuro = dominio._agora() + timedelta(days=2)
    n = await saidas.expirar_solicitacoes(DB, futuro)
    check("a varredura do worker cancela a vencida", n == 1, n)
    check("e devolve os lugares", await contador(lagoa["id"], instante(sexta, 8)) == 0)

    # Confirmar tira o prazo: a reserva confirmada não vence.
    pedido2 = await reservar(ctx, dia=sexta, hh=8, adultos=2, origem="bot")
    conf = await saidas.mudar_status(DB, TENANT, pedido2["id"], "confirmado")
    check("confirmar tira o prazo", conf["expira_em"] is None and conf["confirmado_em"])
    check("e a varredura não mexe nela", await saidas.expirar_solicitacoes(DB, futuro) == 0)

    # O prazo nunca passa da própria saída.
    perto = dominio._agora() + timedelta(hours=5)
    await DB.services.update_one({"id": ctx["circuito"]["id"]}, {"$set": {"excecoes_saidas": [{
        "data": perto.astimezone(TZ).date().isoformat(), "fechado": False, "motivo": None,
        "saidas": [{"hora": f"{perto.astimezone(TZ).hour:02d}:00", "capacidade": None}]}]}})
    circ = await DB.services.find_one({"id": ctx["circuito"]["id"]}, {"_id": 0})
    contato = await DB.contacts.find_one({"id": "c-1"}, {"_id": 0})
    config = {**await dominio.configuracao(DB, TENANT), "antecedencia_min_horas": 0}
    inicio_perto = dominio._iso(perto.astimezone(TZ).replace(minute=0, second=0, microsecond=0))
    p = await saidas.reservar(DB, TENANT, passeio=circ, contato=contato, inicio=inicio_perto,
                              adultos=1, config=config, origem="bot")
    check("solicitação para daqui a 5h vence na hora da saída, não 24h depois",
          p["expira_em"] == inicio_perto, (p["expira_em"], inicio_perto))
    await DB.services.update_one({"id": ctx["circuito"]["id"]}, {"$set": {"excecoes_saidas": []}})
    await saidas.cancelar(DB, TENANT, p["id"])

    sem_prazo = {**config, "prazo_solicitacao_horas": 0}
    p = await saidas.reservar(DB, TENANT, passeio=ctx["lagoa"], contato=contato,
                              inicio=instante(sexta, 8), adultos=1, config=sem_prazo,
                              origem="bot")
    check("prazo 0 = solicitação não expira", p["expira_em"] is None)
    await saidas.cancelar(DB, TENANT, p["id"])
    ctx["sexta"] = sexta

async def etapa_aviso_expiracao(ctx):
    """O turista é avisado quando a solicitação vence, uma vez só, pelo worker."""
    _conectar_fake()
    sexta = ctx["sexta"]
    pendentes = await DB.appointments.find({"aviso_expiracao": "pendente"}).to_list(None)
    check("as duas expiradas (na reserva e na varredura) ficam com aviso pendente",
          len(pendentes) == 2, len(pendentes))

    avisos = []

    async def avisar_falso(db, tenant_id, doc, tipo, config=None, marcar_espera=False, **kw):
        avisos.append((tipo, doc["id"]))
        return True

    original = agenda_worker.avisar
    agenda_worker.avisar = avisar_falso
    try:
        agora = dominio._agora()
        await agenda_worker._avisar_expiradas(DB, agora)
        check("o worker avisa cada uma", sorted(avisos) ==
              sorted(("expiracao", p["id"]) for p in pendentes), avisos)
        marcas = [(await DB.appointments.find_one({"id": p["id"]}))["aviso_expiracao"]
                  for p in pendentes]
        check("e marca como enviado", marcas == ["enviado", "enviado"], marcas)
        await agenda_worker._avisar_expiradas(DB, agora)
        check("a passada seguinte não repete o aviso", len(avisos) == 2, avisos)

        # Teto da hora estourado: nada sai, e o aviso volta a ficar pendente.
        p = await reservar(ctx, dia=sexta, hh=8, adultos=1, origem="bot")
        await DB.appointments.update_one({"id": p["id"]}, {"$set": {
            "expira_em": dominio._iso(agora - timedelta(minutes=1))}})
        await saidas.expirar_solicitacoes(DB, agora)

        async def adiado(db, tenant_id, doc, tipo, config=None, marcar_espera=False, **kw):
            return None
        agenda_worker.avisar = adiado
        await agenda_worker._avisar_expiradas(DB, agora)
        agenda_worker.avisar = avisar_falso
        doc = await DB.appointments.find_one({"id": p["id"]})
        check("aviso adiado pelo teto volta a ficar pendente",
              doc["aviso_expiracao"] == "pendente", doc.get("aviso_expiracao"))
        await agenda_worker._avisar_expiradas(DB, agora)
        check("e sai na passada seguinte", ("expiracao", p["id"]) in avisos, avisos)
        n_avisos = len(avisos)

        # Desligado na configuração: dispensa sem mandar.
        await DB.scheduling_settings.update_one(
            {"tenant_id": TENANT}, {"$set": {"avisar_expiracao": False}}, upsert=True)
        p = await reservar(ctx, dia=sexta, hh=8, adultos=1, origem="bot")
        await DB.appointments.update_one({"id": p["id"]}, {"$set": {
            "expira_em": dominio._iso(agora - timedelta(minutes=1))}})
        await saidas.expirar_solicitacoes(DB, agora)
        await agenda_worker._avisar_expiradas(DB, agora)
        doc = await DB.appointments.find_one({"id": p["id"]})
        check("com o aviso desligado, nada sai", len(avisos) == n_avisos
              and doc["aviso_expiracao"] == "dispensado", doc.get("aviso_expiracao"))
        await DB.scheduling_settings.update_one(
            {"tenant_id": TENANT}, {"$set": {"avisar_expiracao": True}})

        # Passeio que já passou há muito (servidor parado): notícia velha, dispensa.
        p = await reservar(ctx, dia=sexta, hh=8, adultos=1, origem="bot")
        await DB.appointments.update_one({"id": p["id"]}, {"$set": {
            "expira_em": dominio._iso(agora - timedelta(minutes=1))}})
        await saidas.expirar_solicitacoes(DB, agora)
        depois = dominio._instante(p["inicio"]) + timedelta(hours=13)
        await agenda_worker._avisar_expiradas(DB, depois)
        doc = await DB.appointments.find_one({"id": p["id"]})
        check("aviso de passeio que já aconteceu é dispensado", len(avisos) == n_avisos
              and doc["aviso_expiracao"] == "dispensado", doc.get("aviso_expiracao"))
    finally:
        agenda_worker.avisar = original

    texto = agenda_worker.texto_de("expiracao", {**pendentes[0], "contact_nome": "Ana Souza"},
                                   await dominio.configuracao(DB, TENANT))
    check("o texto padrão não diz que estava confirmada", "Ana" in texto
          and "confirmad" not in texto.lower().replace("confirmar", ""), texto)


async def etapa_feriado_e_excecao(ctx):
    _conectar_fake()
    await rs.salvar_config(rs.ConfigIn(bloqueios=[{"data": TERCA.isoformat(),
                                                   "motivo": "Feriado municipal"}]), ADMIN)
    r = await rs.listar_saidas(de=TERCA.isoformat(), ate=TERCA.isoformat(),
                               service_id=None, user=ATENDENTE)
    dia = r["dias"][0]
    check("feriado da agência fecha todas as saídas e diz o motivo",
          dia["saidas"] == [] or all(s["extra"] for s in dia["saidas"]), dia)
    check("o motivo vai junto", dia["bloqueio"] == "Feriado municipal")
    await erro(reservar(ctx, dia=TERCA, adultos=1, contato="c-3"), 409)
    check("e não dá para reservar nele", True)
    await rs.salvar_config(rs.ConfigIn(bloqueios=[]), ADMIN)

    corpo = {k: v for k, v in ctx["lagoa"].items()
             if k in rs.ServicoIn.model_fields}
    corpo["excecoes_saidas"] = [
        {"data": SEGUNDA.isoformat(), "fechado": True, "motivo": "Lagoa seca"},
        {"data": QUARTA.isoformat(), "fechado": False,
         "saidas": [{"hora": "06:00", "capacidade": 20}]},
    ]
    lagoa = await rs.editar_servico(ctx["lagoa"]["id"], rs.ServicoIn(**corpo), ADMIN)
    r = await rs.listar_saidas(de=SEGUNDA.isoformat(), ate=QUARTA.isoformat(),
                               service_id=lagoa["id"], user=ATENDENTE)
    seg, qua = r["dias"][0], r["dias"][2]
    check("dia fechado no passeio some da oferta, mas a saída com reserva continua visível",
          all(s["extra"] for s in seg["saidas"]) and seg["saidas"], seg["saidas"])
    check("exceção com saídas próprias troca a grade daquele dia",
          [(s["hora"], s["capacidade"], s["extra"]) for s in qua["saidas"] if not s["extra"]]
          == [("06:00", 20, False)], qua["saidas"])
    check("e a saída das 8h com gente de antes aparece como extra",
          any(s["hora"] == "08:00" and s["extra"] for s in qua["saidas"]), qua["saidas"])
    corpo["excecoes_saidas"] = []
    ctx["lagoa"] = await rs.editar_servico(ctx["lagoa"]["id"], rs.ServicoIn(**corpo), ADMIN)


async def etapa_antecedencia_e_encaixe(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    contato = await DB.contacts.find_one({"id": "c-3"}, {"_id": 0})
    config = await dominio.configuracao(DB, TENANT)
    amanha_cedo = dominio._agora().astimezone(TZ) + timedelta(hours=1)
    inicio = dominio._iso(amanha_cedo.replace(minute=0, second=0, microsecond=0))
    detalhe = await erro(saidas.reservar(DB, TENANT, passeio=lagoa, contato=contato,
                                         inicio=inicio, adultos=1, config=config,
                                         forcar=False), None)
    check("fora da grade ou dentro da antecedência, sem encaixe é recusado", detalhe)

    doc = await saidas.reservar(DB, TENANT, passeio=lagoa, contato=contato,
                                inicio=instante(QUARTA, 11), adultos=3, config=config,
                                forcar=True)
    check("encaixe aceita uma saída que a grade não tem", doc["pessoas"] == 3)
    r = await rs.listar_saidas(de=QUARTA.isoformat(), ate=QUARTA.isoformat(),
                               service_id=lagoa["id"], user=ATENDENTE)
    extra = [s for s in r["dias"][0]["saidas"] if s["hora"] == "11:00"]
    check("a saída de encaixe aparece na tela marcada como extra",
          extra and extra[0]["extra"] and extra[0]["reservados"] == 3, r["dias"][0]["saidas"])
    vagas = await saidas.saidas_com_vaga(DB, TENANT, passeio=lagoa, dia=QUARTA.isoformat(),
                                         pessoas=1, config=config)
    check("mas não é oferecida ao turista", all(s["hora"] != "11:00" for s in vagas), vagas)
    await erro(saidas.reservar(DB, TENANT, passeio=lagoa, contato=contato,
                               inicio=instante(QUARTA, 11), adultos=8, config=config,
                               forcar=True), 409)
    check("encaixe nunca passa da capacidade", True)
    await invariante("encaixe", lagoa["id"], instante(QUARTA, 11))


async def etapa_proximos_dias(ctx):
    _conectar_fake()
    config = await dominio.configuracao(DB, TENANT)
    dias = await saidas.proximos_dias(DB, TENANT, passeio=ctx["lagoa"], pessoas=2,
                                      config=config, a_partir_de=SEGUNDA.isoformat(),
                                      limite_dias=6)
    check("domingo não aparece (a Lagoa não sai)",
          DOMINGO.isoformat() not in [d["dia"] for d in dias], dias)
    check("a descrição diz a primeira saída do dia", dias and ":" in dias[0]["primeira"], dias)
    grandes = await saidas.proximos_dias(DB, TENANT, passeio=ctx["lagoa"], pessoas=10,
                                         config=config, a_partir_de=SEGUNDA.isoformat(),
                                         limite_dias=10)
    check("com grupo de 10, só os dias em que a saída das 8h está vazia",
          all(d["primeira"] == "08:00" for d in grandes)
          and SEGUNDA.isoformat() not in [d["dia"] for d in grandes], grandes)


async def etapa_isolamento(ctx):
    _conectar_fake()
    estranho = {"id": "u-x", "tenant_id": OUTRO, "role": "tenant_admin"}
    await erro(rs.listar_saidas(de=SEGUNDA.isoformat(), ate=SEGUNDA.isoformat(),
                                service_id=ctx["lagoa"]["id"], user=estranho), 404)
    check("outra empresa não enxerga as saídas de um passeio que não é dela", True)
    r = await rs.listar_saidas(de=SEGUNDA.isoformat(), ate=SEGUNDA.isoformat(),
                               service_id=None, user=estranho)
    check("e a janela dela vem vazia", all(not d["saidas"] for d in r["dias"]), r)
    a = await DB.appointments.find_one({"tenant_id": TENANT}, {"_id": 0})
    await erro(saidas.cancelar(DB, OUTRO, a["id"]), 404)
    check("e não cancela reserva de outra empresa", True)


async def etapa_exclusao_protegida(ctx):
    _conectar_fake()
    detalhe = await erro(rs.remover_servico(ctx["lagoa"]["id"], ADMIN), 409)
    check("passeio com reserva à frente não é excluído", "desative" in detalhe, detalhe)
    await rs.remover_servico(ctx["sem"]["id"], ADMIN)
    check("passeio sem reserva sai", not await DB.services.find_one({"id": ctx["sem"]["id"]}))


async def etapa_rotas(ctx):
    _conectar_fake()
    avisos = []

    async def avisar_falso(db, tenant_id, doc, tipo, config=None, marcar_espera=False, **kw):
        avisos.append((tipo, doc["id"]))
        return True

    original = agenda_worker.avisar
    agenda_worker.avisar = avisar_falso
    try:
        novo = await rs.marcar(rs.AgendamentoIn(
            service_id=ctx["circuito"]["id"], inicio=instante(TERCA, 9), adultos=2,
            contact_nome="Carlos", contact_numero="(11) 98888-7777"), ATENDENTE)
        contato = await DB.contacts.find_one({"id": novo["contact_id"]})
        check("o atendente reserva (não é rota de admin)", novo["status"] == "confirmado")
        check("número do contato novo passa por normaliza_numero",
              contato["numero_whatsapp"] == "5511988887777", contato["numero_whatsapp"])
        check("reserva Confirmada pela tela avisa o turista", ("confirmacao", novo["id"]) in avisos)

        pedido = await rs.marcar(rs.AgendamentoIn(
            service_id=ctx["circuito"]["id"], inicio=instante(TERCA, 9), adultos=1,
            contact_numero="11988887777", status="agendado"), ATENDENTE)
        n = await DB.contacts.count_documents({"tenant_id": TENANT,
                                               "numero_whatsapp": "5511988887777"})
        check("o mesmo número não cria uma segunda ficha", n == 1, n)
        check("Solicitada pela tela não manda 'confirmada'",
              ("confirmacao", pedido["id"]) not in avisos and pedido["expira_em"])

        await rs.mudar_status(pedido["id"], rs.StatusIn(status="confirmado"), ATENDENTE)
        check("confirmar a solicitação avisa o turista", ("confirmacao", pedido["id"]) in avisos)

        await erro(rs.marcar(rs.AgendamentoIn(
            service_id=ctx["circuito"]["id"], inicio=instante(TERCA, 15), adultos=1,
            contact_id="c-1", forcar=True), ATENDENTE), 403)
        check("encaixe é só do admin", True)

        await rs.mudar_pessoas(pedido["id"], rs.PessoasIn(adultos=2, criancas=1), ATENDENTE)
        atual = await DB.appointments.find_one({"id": pedido["id"]})
        check("a rota de pessoas muda o grupo", atual["pessoas"] == 3)

        det = await rs.reservas_da_saida(ctx["circuito"]["id"], instante(TERCA, 9), ATENDENTE)
        check("a saída devolve as reservas dela, com a ocupação",
              len(det["reservas"]) == 2 and det["saida"]["reservados"] == 5, det["saida"])

        lista = await rs.listar_agendamentos(de=instante(SEGUNDA, 0), ate=instante(DOMINGO, 0),
                                             user=ATENDENTE)
        check("a listagem da janela vem ordenada por início",
              [a["inicio"] for a in lista["items"]] == sorted(a["inicio"] for a in lista["items"]))

        await erro(rs.reenviar_lembrete(pedido["id"] if False else
                                        (await DB.appointments.find_one(
                                            {"tenant_id": TENANT, "status": "agendado",
                                             "inicio": {"$gte": dominio._iso(dominio._agora())}}
                                        ) or {"id": "nenhum"})["id"], ATENDENTE), None)
        check("lembrete manual não sai para solicitação (nem para id que não existe)", True)
    finally:
        agenda_worker.avisar = original


async def etapa_guardas():
    _conectar_fake()
    import inspect
    from auth import require_tenant_admin, require_tenant_member

    # O `Depends` não roda ao chamar a função da rota direto: a guarda é
    # conferida onde ela é declarada.
    def guarda(fn):
        for p in inspect.signature(fn).parameters.values():
            dep = getattr(p.default, "dependency", None)
            if dep in (require_tenant_admin, require_tenant_member):
                return dep
        return None

    so_admin = [rs.salvar_config, rs.criar_servico, rs.editar_servico,
                rs.remover_servico, rs.criar_recurso, rs.editar_recurso,
                rs.remover_recurso]
    equipe = [rs.ver_config, rs.listar_servicos, rs.listar_recursos, rs.listar_saidas,
              rs.reservas_da_saida, rs.listar_agendamentos, rs.resumo, rs.marcar,
              rs.cancelar_agendamento, rs.mudar_status, rs.mudar_pessoas]
    check("configurar as Saídas é só do admin",
          all(guarda(f) is require_tenant_admin for f in so_admin),
          [f.__name__ for f in so_admin if guarda(f) is not require_tenant_admin])
    check("reservar e cancelar é de quem atende",
          all(guarda(f) is require_tenant_member for f in equipe),
          [f.__name__ for f in equipe if guarda(f) is not require_tenant_member])
    await erro(require_tenant_admin(ATENDENTE), 403)
    check("require_tenant_admin recusa o atendente de verdade", True)


# ---------------------------------------------------------------------------
# Lembrete
# ---------------------------------------------------------------------------

async def etapa_lembrete(ctx):
    _conectar_fake()
    config = await dominio.configuracao(DB, TENANT)
    contato = await DB.contacts.find_one({"id": "c-1"}, {"_id": 0})
    perto = (dominio._agora() + timedelta(hours=10)).astimezone(TZ).replace(
        minute=0, second=0, microsecond=0)
    confirmada = await saidas.reservar(DB, TENANT, passeio=ctx["circuito"], contato=contato,
                                       inicio=dominio._iso(perto), adultos=2, criancas=1,
                                       config=config, forcar=True)
    solicitada = await saidas.reservar(DB, TENANT, passeio=ctx["circuito"], contato=contato,
                                       inicio=dominio._iso(perto), adultos=1, config=config,
                                       forcar=True, status="agendado")
    pendentes = [a["id"] for a in await agenda_worker._pendentes(DB, dominio._agora())]
    check("a varredura pega a reserva Confirmada", confirmada["id"] in pendentes, pendentes)
    check("e ignora a Solicitada (lembrar dela faria o turista achar que está certo)",
          solicitada["id"] not in pendentes, pendentes)

    texto = agenda_worker.texto_de("lembrete", confirmada, config)
    check("o lembrete traz passeio, pessoas e o que responder",
          "Circuito Lagoa Bonita" in texto and "2 adultos e 1 criança" in texto
          and "CONFIRMAR" in texto, texto)
    check("o placeholder do nome usa o primeiro nome", "Maria" in texto, texto)
    check("o lembrete não fala de guia", "Guia" not in texto, texto)
    modelo = agenda_worker.montar("Oi {contato}, {inexistente} {valor}", confirmada, config)
    check("placeholder desconhecido fica como está, sem derrubar o envio",
          "{inexistente}" in modelo, modelo)
    check("{valor} sai em reais", "R$ 600,00" in modelo, modelo)
    ctx["lembrado"] = confirmada
    await saidas.cancelar(DB, TENANT, solicitada["id"])

    # Teto da hora do número estourado (`ritmo_envio`): `avisar` devolve None,
    # nada saiu, e o lembrete tem de voltar para a próxima varredura em vez de
    # ficar marcado como enviado.
    chamadas = []

    async def avisar_adiado(db, tenant_id, doc, tipo, config=None, marcar_espera=False, **kw):
        chamadas.append((tipo, kw.get("ritmo")))
        return None

    original = agenda_worker.avisar
    agenda_worker.avisar = avisar_adiado
    try:
        await agenda_worker._varrer()
    finally:
        agenda_worker.avisar = original
    check("a varredura manda o lembrete com o ritmo de lote (adiável)",
          ("lembrete", agenda_worker.RITMO_ADIAVEL) in chamadas, chamadas)
    doc = await DB.appointments.find_one({"id": confirmada["id"]})
    check("lembrete adiado pelo teto volta a ficar pendente",
          doc["lembrete_enviado_em"] is None, doc.get("lembrete_enviado_em"))


async def etapa_resposta_do_lembrete(ctx):
    _conectar_fake()
    session = {"id": "sess-1", "tenant_id": TENANT, "contact_id": "c-1",
               "agenda_aguardando": ctx["lembrado"]["id"]}
    await DB.conversation_sessions.insert_one(dict(session))
    enviados = []

    async def _fake_send(tenant_id, session_id, content):
        enviados.append(content)
        return True

    import flow_runtime
    original = flow_runtime.send_human_message
    flow_runtime.send_human_message = _fake_send
    try:
        r = await agenda_worker.responder_lembrete(
            DB, TENANT, dict(session), "quero cancelar meu pedido de ontem")
        check("cotejo é com a mensagem inteira, nunca 'contém'", r is None, r)

        r = await agenda_worker.responder_lembrete(DB, TENANT, dict(session), "Confirmar")
        check("CONFIRMAR é entendido", r == "confirmado", r)
        atual = await DB.appointments.find_one({"id": ctx["lembrado"]["id"]})
        check("e registra a presença sem mexer no status (quem confirma é a agência)",
              atual["presenca_confirmada_em"] and atual["status"] == "confirmado", atual)
        check("o turista recebe a resposta", enviados and "Presença confirmada" in enviados[-1],
              enviados)

        r = await agenda_worker.responder_lembrete(
            DB, TENANT, {"id": "sess-1", "tenant_id": TENANT}, "sim")
        check("sem lembrete pendente, 'sim' não faz nada", r is None, r)

        inicio = ctx["lembrado"]["inicio"]
        antes = await contador(ctx["circuito"]["id"], inicio)
        session2 = {"id": "sess-2", "tenant_id": TENANT, "contact_id": "c-1",
                    "agenda_aguardando": ctx["lembrado"]["id"]}
        await DB.conversation_sessions.insert_one(dict(session2))
        r = await agenda_worker.responder_lembrete(DB, TENANT, dict(session2), "cancelar")
        check("CANCELAR cancela a reserva", r == "cancelado", r)
        check("e os lugares voltam para a saída",
              await contador(ctx["circuito"]["id"], inicio) == antes - 3)
        await invariante("cancelado pelo turista", ctx["circuito"]["id"], inicio)
    finally:
        flow_runtime.send_human_message = original


async def etapa_resposta_solta_ao_lembrete(ctx):
    """O turista responde ao lembrete outra coisa que não CONFIRMAR/CANCELAR.

    A conversa aberta pelo aviso nasce sem bot, então ela cai no ramo da fila
    humana. Dizer "bot indisponível" ali descreveria um defeito onde não há
    nenhum: é o que este teste tranca.
    """
    _conectar_fake()
    import flow_runtime

    async def fake_send(token, numero, texto, *a, **kw):
        return {"ok": True}

    originais = (flow_runtime.get_db, flow_runtime.whatsapp.send_text)
    flow_runtime.get_db = lambda: DB
    flow_runtime.whatsapp.send_text = fake_send
    try:
        await DB.whatsapp_connections.insert_one({
            "id": "conn-ag", "tenant_id": TENANT, "phone_number_id": "pn-teste",
            "waba_id": "waba-teste", "status": "conectado"})
        await DB.conversation_sessions.insert_one({
            "id": "sess-ag", "tenant_id": TENANT, "contact_id": "c-1",
            "whatsapp_connection_id": "conn-ag", "status": "ativa",
            "status_atendimento": "bot", "bot_id": None, "flow_id": None,
            "origem": "agenda", "awaiting": None, "variables": {},
            "ia_respostas": 0, "unread_count": 0})

        await flow_runtime.handle_incoming_message(
            TENANT, "5511999990001", "posso remarcar?", "msg-ag-1", connection_id="conn-ag")
        sistema = await DB.messages_log.find(
            {"session_id": "sess-ag", "direction": "sistema"}).to_list(10)
        textos = [m["content"] for m in sistema]
        check("resposta solta a um lembrete vai para a fila humana",
              any("atendimento humano" in t for t in textos), textos)
        check("e o motivo diz que foi um aviso, não 'bot indisponível'",
              any("aviso de agendamento" in t for t in textos)
              and not any("Bot da conversa indisponível" in t for t in textos), textos)

        await flow_runtime.handle_incoming_message(
            TENANT, "5511999990001", "quero cancelar meu pedido", "msg-ag-2",
            connection_id="conn-ag")
        contato = await DB.contacts.find_one({"id": "c-1", "tenant_id": TENANT})
        check("frase com 'cancelar' no meio não é opt-out", not contato.get("opt_out"), contato)
        await flow_runtime.handle_incoming_message(
            TENANT, "5511999990001", "SAIR!", "msg-ag-3", connection_id="conn-ag")
        contato = await DB.contacts.find_one({"id": "c-1", "tenant_id": TENANT})
        check("SAIR marca o opt-out", contato.get("opt_out") is True)
        await DB.contacts.update_one({"id": "c-1"}, {"$set": {"opt_out": False}})
    finally:
        (flow_runtime.get_db, flow_runtime.whatsapp.send_text) = originais


async def etapa_duplicata(ctx):
    """Nome repetido é recusado, que é a trava contra o duplo clique."""
    _conectar_fake()
    detalhe = await erro(rs.criar_servico(rs.ServicoIn(nome="Lagoa Azul", duracao_min=30),
                                          ADMIN), 409)
    check("passeio com nome repetido é recusado com 409", "Já existe" in detalhe, detalhe)
    await erro(rs.criar_servico(rs.ServicoIn(nome="  lagoa AZUL  ", duracao_min=30), ADMIN), 409)
    check("a comparação ignora caixa e espaço em volta", True)
    especial = await rs.criar_servico(rs.ServicoIn(nome="Lagoa (pôr do sol)", duracao_min=20),
                                      ADMIN)
    await erro(rs.criar_servico(rs.ServicoIn(nome="Lagoa (pôr do sol)", duracao_min=20),
                                ADMIN), 409)
    check("nome com caractere de regex é comparado, não executado", True)
    editado = await rs.editar_servico(especial["id"], rs.ServicoIn(
        nome="Lagoa (pôr do sol)", duracao_min=25), ADMIN)
    check("salvar o mesmo registro sem trocar o nome continua funcionando",
          editado["duracao_min"] == 25)
    outra = await rs.criar_servico(rs.ServicoIn(nome="Lagoa Azul", duracao_min=30),
                                   {"id": "u-x", "tenant_id": OUTRO, "role": "tenant_admin"})
    check("o mesmo nome noutra empresa é permitido", outra["id"])
    await DB.services.delete_one({"id": outra["id"]})
    await DB.services.delete_one({"id": especial["id"]})


async def etapa_corrida_de_mensagens(ctx):
    """Uma conversa é processada uma mensagem por vez.

    O teste mira o **mecanismo**, não uma corrida encenada: o Mongo falso nunca
    suspende a execução, então duas tasks passadas ao `gather` rodam uma inteira
    depois da outra e a corrida jamais acontece.
    """
    _conectar_fake()
    import flow_runtime

    dentro, pico = [], 0

    async def tarefa(chave):
        nonlocal pico
        async with flow_runtime._uma_por_vez(chave):
            dentro.append(chave)
            pico = max(pico, len(dentro))
            await asyncio.sleep(0.01)
            dentro.pop()

    await asyncio.gather(*(tarefa("conversa-a") for _ in range(4)))
    check("na mesma conversa, uma mensagem por vez", pico == 1, pico)
    pico = 0
    await asyncio.gather(*(tarefa(f"conversa-{i}") for i in range(4)))
    check("conversas diferentes continuam andando em paralelo", pico == 4, pico)
    check("o dicionário de travas não vaza uma entrada por contato",
          flow_runtime._travas_por_conversa == {}, flow_runtime._travas_por_conversa)


# ---------------------------------------------------------------------------
# Ferramentas do agente de IA
# ---------------------------------------------------------------------------

async def etapa_ferramentas_ia(ctx):
    _conectar_fake()
    # Contato só desta etapa: as anteriores deixaram reservas nos outros, e
    # "cancelar a única reserva" precisa de um turista com uma só.
    await DB.contacts.insert_one({"id": "c-ia", "tenant_id": TENANT, "nome": "Turista IA",
                                  "numero_whatsapp": "5511999990099"})
    contato = await DB.contacts.find_one({"id": "c-ia"}, {"_id": 0})
    executar = ai_tools.executor(DB, TENANT, contato, {"id": "s-ia"})
    sabado = SEGUNDA + timedelta(days=5)

    r = await executar("consultar_vagas", {"passeio": "lagoa azul", "pessoas": 2})
    check("nome em minúsculas casa com o passeio", "Lagoa Azul" in r, r)
    check("sem dia, devolve os próximos dias com vaga", "Próximos dias" in r, r)
    r = await executar("consultar_vagas", {"passeio": "Passeio de barco"})
    check("passeio sem saída (ou inexistente) devolve a lista dos que existem",
          "Lagoa Azul" in r and "Circuito" in r, r)
    r = await executar("consultar_vagas", {"passeio": "Lagoa Azul", "dia": sabado.isoformat(),
                                           "pessoas": 5})
    check("com dia e 5 pessoas, só a saída das 8h (a das 14h tem 4 lugares)",
          "08:00" in r and "14:00" not in r, r)

    r = await executar("solicitar_reserva", {"passeio": "Lagoa Azul", "dia": sabado.isoformat(),
                                             "hora": "08:00", "adultos": 2, "criancas": 1,
                                             "observacao": "Hotel Gran Lençóis"})
    check("solicitar grava e manda dizer que a equipe confirma",
          r.startswith(ai_tools.PREFIXO_RESERVADO) and "vai confirmar" in r, r)
    check("e o resumo traz pessoas e total", "2 adultos e 1 criança" in r and "R$ 375,00" in r, r)
    doc = await DB.appointments.find_one({"tenant_id": TENANT, "contact_id": "c-ia",
                                          "origem": "ia"})
    check("a reserva da IA nasce Solicitada, com prazo e amarrada à conversa",
          doc["status"] == "agendado" and doc["expira_em"] and doc["session_id"] == "s-ia", doc)
    check("a observação do turista fica gravada", doc["observacoes"] == "Hotel Gran Lençóis")

    r = await executar("solicitar_reserva", {"passeio": "Lagoa Azul", "dia": sabado.isoformat(),
                                             "hora": "14:00", "adultos": 6})
    check("grupo maior que a saída devolve alternativas do mesmo dia",
          "não tem vaga" in r and "08:00" in r, r)
    r = await executar("solicitar_reserva", {"passeio": "Lagoa Azul", "dia": sabado.isoformat(),
                                             "hora": "08:00"})
    check("sem dizer quantos vão, pede para perguntar", r.startswith("ERRO"), r)

    await executar("solicitar_reserva", {"passeio": "Circuito Lagoa Bonita",
                                         "dia": sabado.isoformat(), "hora": "09:00",
                                         "adultos": 1})
    r = await executar("cancelar_reserva", {})
    check("com mais de uma reserva, cancelar pergunta qual", "mais de uma reserva" in r, r)
    r = await executar("cancelar_reserva", {"dia": sabado.isoformat()})
    check("com duas no mesmo dia, continua perguntando em vez de chutar",
          "mais de uma reserva" in r, r)
    circ = await DB.appointments.find_one({"tenant_id": TENANT, "contact_id": "c-ia",
                                           "service_id": ctx["circuito"]["id"], "origem": "ia"})
    await saidas.cancelar(DB, TENANT, circ["id"])
    r = await executar("cancelar_reserva", {"motivo": "mudou de ideia"})
    check("com uma só, cancelar age sobre a reserva do próprio turista", r.startswith("CANCELADO"), r)
    atual = await DB.appointments.find_one({"id": doc["id"]})
    check("o cancelamento é gravado com o motivo e devolve os lugares",
          atual["status"] == "cancelado" and "mudou de ideia" in atual["motivo_cancelamento"])
    await invariante("IA cancelou", ctx["lagoa"]["id"], instante(sabado, 8))
    r = await executar("cancelar_reserva", {})
    check("sem reserva, cancelar avisa em vez de estourar", "não tem nenhuma reserva" in r, r)
    r = await executar("ferramenta_inventada", {})
    check("ferramenta desconhecida vira texto, nunca exceção", r.startswith("ERRO"), r)


async def etapa_ia_simulada(ctx):
    _conectar_fake()
    contato = await DB.contacts.find_one({"id": "c-2"}, {"_id": 0})
    executar = ai_tools.executor(DB, TENANT, contato, None, simulado=True)
    antes = await DB.appointments.count_documents({"tenant_id": TENANT})
    r = await executar("solicitar_reserva", {"passeio": "Lagoa Azul",
                                             "dia": (SEGUNDA + timedelta(days=5)).isoformat(),
                                             "hora": "08:00", "adultos": 2})
    depois = await DB.appointments.count_documents({"tenant_id": TENANT})
    check("no chat de teste a consulta roda e a reserva não grava",
          "simulação" in r and antes == depois, (r, antes, depois))


async def etapa_prompt_da_ia(ctx):
    _conectar_fake()
    import ai_agents

    agente = {"nome": "Bia", "tipo": "agendamento", "empresa_nome": "Agência Teste"}
    bloco, ferramentas, _ = await ai_agents.preparar_agenda(DB, TENANT, agente)
    check("agente sem agenda_ativa não recebe catálogo nem ferramentas",
          bloco == "" and ferramentas is None)
    instr = ai_agents.montar_instrucoes(agente)
    check("sem Saídas, o agente é avisado que não pode reservar",
          "NÃO tem acesso à agenda" in instr, instr[:400])

    agente["agenda_ativa"] = True
    bloco, ferramentas, _ = await ai_agents.preparar_agenda(
        DB, TENANT, agente, await DB.contacts.find_one({"id": "c-1"}, {"_id": 0}))
    check("com Saídas ligadas, os passeios entram no prompt com preço e horários",
          "Lagoa Azul" in bloco and "R$ 150,00 por adulto" in bloco and "08:00" in bloco, bloco)
    check("e o preço de criança diferente aparece", "R$ 75,00 por criança" in bloco, bloco)
    check("as três ferramentas são registradas",
          {f.nome for f in ferramentas} == {"consultar_vagas", "solicitar_reserva",
                                            "cancelar_reserva"})
    check("o prompt diz que é pedido, não confirmação", "nunca diga que a reserva está "
          "confirmada" in bloco, bloco)
    check("vagas não entram no prompt (mudam a cada reserva e quebrariam o cache)",
          "vagas)" not in bloco and "reservados" not in bloco, bloco)
    check("o catálogo não carrega nada do contato", "Maria" not in bloco, bloco)

    # Passeio desativado e sem grade: a agência sem nada para oferecer.
    agente_vazio = {**agente}
    await DB.services.update_many({"tenant_id": TENANT}, {"$set": {"ativo": False}})
    bloco, ferramentas, _ = await ai_agents.preparar_agenda(DB, TENANT, agente_vazio, {"id": "c-1"})
    check("sem passeio com saída, nada de ferramenta (e volta a regra do 'não tem acesso')",
          bloco == "" and ferramentas is None)
    await DB.services.update_many({"tenant_id": TENANT}, {"$set": {"ativo": True}})


async def etapa_trava_confirmacao(ctx):
    """A trava `confirmou_sem_agenda` no modelo de solicitação.

    O agente registra pedido, não confirma. "Sua reserva está confirmada" com só
    uma solicitação gravada é mentira e tem de ser barrado; "registrei seu
    pedido" é verdade e passa.
    """
    _conectar_fake()
    import ai_agents
    contato = await DB.contacts.find_one({"id": "c-3"}, {"_id": 0})
    await DB.appointments.delete_many({"tenant_id": TENANT, "contact_id": "c-3"})

    pedido = await reservar(ctx, dia=SEGUNDA + timedelta(days=5), hh=8, adultos=1,
                            contato="c-3", origem="ia")
    check("com só a solicitação, 'está confirmada' é barrado",
          await ai_agents.confirmou_sem_agenda(DB, TENANT, contato,
                                               "Pronto! Sua reserva está confirmada.", {"ok": False}))
    check("mesmo logo depois de solicitar nesta resposta",
          await ai_agents.confirmou_sem_agenda(DB, TENANT, contato,
                                               "Reserva confirmada para sábado!", {"ok": True}))
    check("'reservei' logo depois de solicitar passa",
          not await ai_agents.confirmou_sem_agenda(DB, TENANT, contato,
                                                   "Reservei para sábado, a equipe confirma.",
                                                   {"ok": True}))
    check("pergunta não trava",
          not await ai_agents.confirmou_sem_agenda(DB, TENANT, contato,
                                                   "Quer que eu reserve o passeio?", {"ok": False}))
    await saidas.mudar_status(DB, TENANT, pedido["id"], "confirmado")
    check("com a reserva Confirmada pela agência, dizer que está confirmada passa",
          not await ai_agents.confirmou_sem_agenda(DB, TENANT, contato,
                                                   "Sua reserva está confirmada!", {"ok": False}))
    await saidas.cancelar(DB, TENANT, pedido["id"])


# ---------------------------------------------------------------------------
# Nó de Agendamento do editor de fluxos
# ---------------------------------------------------------------------------

async def etapa_no_de_fluxo(ctx):
    _conectar_fake()
    no = {"id": "n-ag", "type": "schedule", "data": {"dias_oferecidos": 5, "formato": "lista"}}
    session = {"id": "s-fluxo", "tenant_id": TENANT, "contact_id": "c-1",
               "whatsapp_connection_id": "conn-1", "variables": {}, "awaiting": None}

    outputs = []
    saida = await agenda_flow.avancar(no, session, outputs)
    check("a primeira pergunta é o passeio", saida is None and len(outputs) == 1)
    menu = outputs[0]
    check("a pergunta sai como Menu, pelo caminho de envio que já existe",
          menu["type"] == "menu" and menu["formato"] == "lista")
    check("as opções são os passeios com saída",
          {i["label"] for i in menu["itens"]} == {"Lagoa Azul", "Circuito Lagoa Bonita"},
          menu["itens"])
    check("a descrição traz duração e preço por pessoa",
          any("por pessoa" in i["descricao"] for i in menu["itens"]), menu["itens"])

    outputs = []
    await agenda_flow.avancar(no, session, outputs, escolha_id=ctx["lagoa"]["id"])
    check("depois do passeio vem quantas pessoas", session["awaiting"]["etapa"] == "pessoas")
    check("até 10 opções", len(outputs[0]["itens"]) == 10, outputs[0]["itens"])

    outputs = []
    await agenda_flow.avancar(no, session, outputs, resposta="6")
    check("digitar o número escolhe aquele número de pessoas",
          session["awaiting"]["dados"]["pessoas"] == "6", session["awaiting"])
    check("depois vem o dia", session["awaiting"]["etapa"] == "dia")
    check("a descrição diz a primeira saída, nunca quantas vagas sobram",
          all(i["descricao"].startswith("saída a partir das") for i in outputs[0]["itens"]),
          [i["descricao"] for i in outputs[0]["itens"]])
    check("e com 6 pessoas a primeira saída é a das 8h (a das 14h tem 4 lugares)",
          all(i["descricao"].endswith("08:00") for i in outputs[0]["itens"]),
          [i["descricao"] for i in outputs[0]["itens"]])

    # Escolhido o dia, só cabe a saída das 8h: o nó não pergunta o óbvio.
    outputs = []
    saida = await agenda_flow.avancar(no, session, outputs, resposta="1")
    check("com uma saída só no dia, o nó reserva sem perguntar a hora",
          saida == agenda_flow.SAIDA_OK, (saida, outputs))
    texto = next(o["content"] for o in outputs if o["type"] == "text")
    check("o turista recebe 'pedido recebido', nunca 'confirmado'",
          "Recebemos seu pedido" in texto and "6 pessoas" in texto
          and "confirmad" not in texto.split("vai confirmar")[0].lower(), texto)
    check("o awaiting é limpo ao terminar", session["awaiting"] is None)
    v = session["variables"]
    check("as variáveis da reserva ficam disponíveis ao resto do fluxo",
          v["agendamento_servico"] == "Lagoa Azul" and v["agendamento_pessoas"] == "6"
          and v["agendamento_valor"].startswith("R$"), v)
    doc = await DB.appointments.find_one({"id": v["agendamento_id"]})
    check("a reserva do bot nasce Solicitada, com origem 'bot' e amarrada à conversa",
          doc["status"] == "agendado" and doc["origem"] == "bot"
          and doc["session_id"] == "s-fluxo", doc)

    # Com 2 pessoas, a maioria dos dias tem duas saídas: aí sim pergunta a hora.
    session = {"id": "s-fluxo2", "tenant_id": TENANT, "contact_id": "c-2",
               "variables": {}, "awaiting": None}
    await agenda_flow.avancar(no, session, [])
    await agenda_flow.avancar(no, session, [], escolha_id=ctx["lagoa"]["id"])
    dias_out = []
    await agenda_flow.avancar(no, session, dias_out, escolha_id="2")
    # O último dia oferecido, e não o primeiro: o primeiro pode ser hoje, e
    # conforme a hora da execução a saída das 14h já está dentro da
    # antecedência mínima. Sobraria uma saída só, o nó reservaria direto e o
    # teste dependeria do relógio.
    outputs = []
    await agenda_flow.avancar(no, session, outputs, escolha_id=dias_out[0]["itens"][-1]["id"])
    check("com duas saídas que cabem, o nó pergunta qual",
          session["awaiting"]["etapa"] == "hora"
          and [i["label"] for i in outputs[0]["itens"]] == ["08:00", "14:00"], outputs)
    dia_escolhido = dominio._dia(session["awaiting"]["dados"]["dia"])
    outputs = []
    saida = await agenda_flow.avancar(no, session, outputs,
                                      escolha_id=instante(dia_escolhido, 14))
    check("tocar na saída das 14h reserva nela", saida == agenda_flow.SAIDA_OK, outputs)
    reservada = await DB.appointments.find_one({"id": session["variables"]["agendamento_id"]})
    check("e a reserva é na saída escolhida", reservada["inicio"] == instante(dia_escolhido, 14))
    await invariante("bot reservou", ctx["lagoa"]["id"], reservada["inicio"])


async def etapa_no_resposta_invalida(ctx):
    _conectar_fake()
    no = {"id": "n-ag", "type": "schedule", "data": {"formato": "lista"}}
    session = {"id": "s-inv", "tenant_id": TENANT, "contact_id": "c-1",
               "variables": {}, "awaiting": None}
    await agenda_flow.avancar(no, session, [])
    for i in range(agenda_flow.MAX_TENTATIVAS - 1):
        outputs = []
        saida = await agenda_flow.avancar(no, session, outputs, resposta="oi tudo bem?")
        check(f"resposta fora das opções repete a pergunta ({i + 1})",
              saida is None and outputs[0]["content"].startswith("Não entendi"), outputs)
    outputs = []
    saida = await agenda_flow.avancar(no, session, outputs, resposta="???")
    check("depois de insistir, o nó sai por 'não reservou' em vez de travar",
          saida == agenda_flow.SAIDA_FALHA, saida)
    check("o motivo vai para o histórico e para uma variável",
          any(o["type"] == "system" and "não escolheu" in o["content"] for o in outputs)
          and "não escolheu" in session["variables"]["agendamento_falha"], outputs)

    # "11 pessoas" não é opção: o nó oferece até 10, e o número fora vira inválido.
    session = {"id": "s-inv2", "tenant_id": TENANT, "contact_id": "c-1",
               "variables": {}, "awaiting": None}
    await agenda_flow.avancar(no, session, [])
    await agenda_flow.avancar(no, session, [], escolha_id=ctx["circuito"]["id"])
    outputs = []
    await agenda_flow.avancar(no, session, outputs, resposta="11")
    check("número de pessoas acima do teto do nó repete a pergunta",
          session["awaiting"]["etapa"] == "pessoas"
          and outputs[0]["content"].startswith("Não entendi"), outputs)

    # Grupo que não cabe em saída nenhuma: sai por "não reservou" dizendo o porquê.
    no7 = {"id": "n-ag", "type": "schedule", "data": {"formato": "lista", "max_pessoas": 10}}
    session = {"id": "s-inv3", "tenant_id": TENANT, "contact_id": "c-1",
               "variables": {}, "awaiting": None}
    await agenda_flow.avancar(no7, session, [])
    await agenda_flow.avancar(no7, session, [], escolha_id=ctx["circuito"]["id"])
    outputs = []
    saida = await agenda_flow.avancar(no7, session, outputs, escolha_id="7")
    check("7 pessoas no Circuito de 6 lugares sai por 'não reservou'",
          saida == agenda_flow.SAIDA_FALHA
          and any("7 pessoa" in o["content"] for o in outputs if o["type"] == "system"),
          outputs)


async def etapa_no_simulado(ctx):
    _conectar_fake()
    no = {"id": "n-ag", "type": "schedule", "data": {}}
    session = {"id": "s-teste", "tenant_id": TENANT, "variables": {},
               "awaiting": None, "simulado": True}
    antes = await DB.appointments.count_documents({"tenant_id": TENANT})
    contadores_antes = [dict(c) for c in DB.saidas.docs]
    await agenda_flow.avancar(no, session, [])
    await agenda_flow.avancar(no, session, [], escolha_id=ctx["circuito"]["id"])
    await agenda_flow.avancar(no, session, [], escolha_id="1")
    outputs = []
    saida = await agenda_flow.avancar(no, session, outputs, resposta="1")
    check("o chat de teste do editor percorre o nó até o fim",
          saida == agenda_flow.SAIDA_OK, (saida, outputs))
    check("mas não grava reserva nem mexe em contador",
          antes == await DB.appointments.count_documents({"tenant_id": TENANT})
          and [dict(c) for c in DB.saidas.docs] == contadores_antes)
    check("e diz que foi simulação",
          any("simulação" in o.get("content", "") for o in outputs), outputs)


async def etapa_validacao_do_no():
    _conectar_fake()
    check("botões não comportam reserva",
          any("não cabe em botões" in p for p in agenda_flow.validar({"formato": "botoes"})))
    check("teto de pessoas fora de 1 a 10 é reprovado",
          any("pessoas" in p for p in agenda_flow.validar({"max_pessoas": 20})))
    check("configuração válida passa",
          agenda_flow.validar({"formato": "lista", "dias_oferecidos": 5, "max_pessoas": 8}) == [])
    check("nó antigo com campos de guia continua publicável (não quebra fluxo existente)",
          agenda_flow.validar({"modo_profissional": "fixo", "formato": "lista"}) == [])


# ---------------------------------------------------------------------------
# Fase 2: embarque
# ---------------------------------------------------------------------------

QUINTA = SEGUNDA + timedelta(days=3)


async def etapa_pontos_embarque(ctx):
    _conectar_fake()
    det = await erro(rs.salvar_config(rs.ConfigIn(pontos_embarque=[
        {"nome": "Pousada do Rio"}, {"nome": "pousada do rio "}]), ADMIN), 400)
    check("ponto com nome repetido (sem caixa) é recusado", "duas vezes" in det, det)
    await erro(rs.salvar_config(rs.ConfigIn(pontos_embarque=[{"nome": "  "}]), ADMIN), 400)
    check("ponto sem nome é recusado", True)

    cfg = await rs.salvar_config(rs.ConfigIn(pontos_embarque=[
        {"nome": "Pousada do Rio", "endereco": "Rua das Dunas, 12", "minutos_antes": 30},
        {"nome": "Hotel Gran Lençóis", "minutos_antes": 45},
        {"nome": "Píer de Atins", "minutos_antes": 0, "ativo": False},
    ]), ADMIN)
    pontos = cfg["pontos_embarque"]
    check("os pontos ganham id e mantêm a ordem",
          [p["nome"] for p in pontos] == ["Pousada do Rio", "Hotel Gran Lençóis", "Píer de Atins"]
          and all(p["id"] for p in pontos), pontos)
    check("minutos sem valor viram o padrão de 30", pontos[0]["minutos_antes"] == 30)
    ids = [p["id"] for p in pontos]
    cfg = await rs.salvar_config(rs.ConfigIn(pontos_embarque=[
        {**pontos[1], "minutos_antes": 40}, pontos[0], pontos[2]]), ADMIN)
    check("salvar de novo preserva os ids (reordenado e editado)",
          [p["id"] for p in cfg["pontos_embarque"]] == [ids[1], ids[0], ids[2]]
          and cfg["pontos_embarque"][0]["minutos_antes"] == 40, cfg["pontos_embarque"])
    ctx["pousada"], ctx["hotel"], ctx["pier"] = (
        cfg["pontos_embarque"][1], cfg["pontos_embarque"][0], cfg["pontos_embarque"][2])
    config = await dominio.configuracao(DB, TENANT)
    check("só os ativos entram na oferta",
          [p["nome"] for p in saidas.pontos_ativos(config)] == ["Hotel Gran Lençóis", "Pousada do Rio"])


async def etapa_embarque_na_reserva(ctx):
    _conectar_fake()
    config = await dominio.configuracao(DB, TENANT)
    r = await reservar(ctx, dia=QUINTA, adultos=2, ponto_embarque_id=ctx["pousada"]["id"])
    check("ponto cadastrado sem hora: a busca é a saída menos os minutos do ponto",
          r["horario_busca"] == instante(QUINTA, 7, 30) and r["ponto_embarque_nome"] == "Pousada do Rio"
          and r["ponto_embarque_endereco"] == "Rua das Dunas, 12", r)
    check("{busca} sai sem preposição (o gênero do nome é da agência)",
          saidas.rotulo_busca(r, config) == "07:30 – Pousada do Rio", saidas.rotulo_busca(r, config))
    texto = agenda_worker.texto_de("lembrete", r, config)
    check("o lembrete é de embarque: diz a hora e o lugar da busca",
          "Busca: 07:30 – Pousada do Rio" in texto, texto)

    livre = await reservar(ctx, dia=QUINTA, adultos=1, contato="c-2",
                           ponto_embarque_nome="Casa da tia, Rua 7")
    check("lugar fora da lista fica com o nome e a hora a combinar",
          livre["ponto_embarque_nome"] == "Casa da tia, Rua 7" and livre["horario_busca"] is None)
    check("e o texto diz isso em vez de inventar hora",
          "a combinar" in saidas.rotulo_busca(livre, config), saidas.rotulo_busca(livre, config))
    sem = await reservar(ctx, dia=QUINTA, adultos=1, contato="c-3")
    check("sem embarque nenhum, {busca} é 'a combinar com a nossa equipe'",
          saidas.rotulo_busca(sem, config) == "a combinar com a nossa equipe")

    det = await erro(reservar(ctx, dia=QUINTA, adultos=1, contato="c-3", horario_busca="08:30"),
                     400)
    check("busca depois da saída é recusada", "depois da saída" in det, det)
    await erro(reservar(ctx, dia=QUINTA, adultos=1, contato="c-3", ponto_embarque_id="inventado"),
               400)
    check("ponto que não existe é recusado", True)
    check("e as recusas não deixaram lugar preso no contador",
          await contador(ctx["lagoa"]["id"], instante(QUINTA, 8)) == 4)
    await invariante("recusas de embarque", ctx["lagoa"]["id"], instante(QUINTA, 8))

    novo = await rs.mudar_embarque(sem["id"], rs.EmbarqueIn(
        ponto_embarque_id=ctx["hotel"]["id"], horario_busca="07:10", embarque_obs="Apto 12"),
        ATENDENTE)
    check("a rota de embarque define ponto, hora informada e observação",
          novo["ponto_embarque_nome"] == "Hotel Gran Lençóis"
          and novo["horario_busca"] == instante(QUINTA, 7, 10) and novo["embarque_obs"] == "Apto 12",
          novo)
    limpo = await rs.mudar_embarque(sem["id"], rs.EmbarqueIn(), ATENDENTE)
    check("mandar tudo vazio limpa o embarque",
          limpo["ponto_embarque_nome"] is None and limpo["horario_busca"] is None)
    await rs.mudar_embarque(sem["id"], rs.EmbarqueIn(ponto_embarque_id=ctx["hotel"]["id"]), ATENDENTE)

    # Renomear o ponto depois não muda o que a reserva guardou.
    cfg = await dominio.configuracao(DB, TENANT)
    pts = [{**p, "nome": "Pousada do Rio (nova)"} if p["id"] == ctx["pousada"]["id"] else p
           for p in cfg["pontos_embarque"]]
    await rs.salvar_config(rs.ConfigIn(pontos_embarque=pts), ADMIN)
    guardada = await DB.appointments.find_one({"id": r["id"]})
    check("renomear o ponto não reescreve a reserva (a lista de amanhã não muda sozinha)",
          guardada["ponto_embarque_nome"] == "Pousada do Rio")
    pts = [{**p, "nome": "Pousada do Rio"} if p["id"] == ctx["pousada"]["id"] else p for p in pts]
    await rs.salvar_config(rs.ConfigIn(pontos_embarque=pts), ADMIN)
    ctx["quinta_8"] = instante(QUINTA, 8)


async def etapa_equipe_da_saida(ctx):
    _conectar_fake()
    guia = await rs.criar_recurso(rs.RecursoIn(nome="Guia João", tipo="pessoa",
                                               telefone="(98) 98888-1111"), ADMIN)
    van = await rs.criar_recurso(rs.RecursoIn(nome="Van 15", tipo="sala", capacidade=15), ADMIN)
    check("guia e veículo são cadastrados com telefone e capacidade",
          guia["telefone"] and van["capacidade"] == 15)

    # Saída ainda sem reserva: a escala vem antes da venda.
    sexta14 = instante(SEGUNDA + timedelta(days=11), 14)
    linha = await rs.escalar_equipe(rs.EquipeIn(
        service_id=ctx["lagoa"]["id"], inicio=sexta14, guia_id=guia["id"], veiculo_id=van["id"],
        observacoes="Sai do píer"), ATENDENTE)
    check("escalar guia numa saída sem reserva funciona",
          linha["guia_nome"] == "Guia João" and linha["veiculo_nome"] == "Van 15"
          and linha["equipe_obs"] == "Sai do píer" and linha["reservados"] == 0, linha)
    check("e não cria lugar ocupado", await contador(ctx["lagoa"]["id"], sexta14) == 0)

    linha = await rs.escalar_equipe(rs.EquipeIn(
        service_id=ctx["lagoa"]["id"], inicio=ctx["quinta_8"], guia_id=guia["id"]), ATENDENTE)
    check("escalar numa saída com reserva mantém o contador",
          linha["guia_nome"] == "Guia João" and linha["reservados"] == 4, linha)
    await erro(rs.escalar_equipe(rs.EquipeIn(
        service_id=ctx["lagoa"]["id"], inicio=instante(QUINTA, 10), guia_id=guia["id"]),
        ATENDENTE), 404)
    check("hora que não é saída não pode ser escalada", True)
    await erro(rs.escalar_equipe(rs.EquipeIn(
        service_id=ctx["lagoa"]["id"], inicio=ctx["quinta_8"], guia_id="de-outra-empresa"),
        ATENDENTE), 404)
    check("guia que não é da empresa é recusado", True)
    ctx["guia"], ctx["van"] = guia, van


async def etapa_lista_de_embarque(ctx):
    _conectar_fake()
    # Uma solicitação ainda não confirmada entra marcada.
    await reservar(ctx, dia=QUINTA, adultos=1, criancas=1, contato="c-3", origem="bot",
                   ponto_embarque_id=ctx["pousada"]["id"])
    lista = await rs.lista_de_embarque(ctx["lagoa"]["id"], ctx["quinta_8"], ATENDENTE)
    horas = [(g["hora_busca"], g["ponto_embarque_nome"]) for g in lista["grupos"]]
    check("ordem pela hora da busca, com 'a combinar' por último",
          horas == [("07:20", "Hotel Gran Lençóis"), ("07:30", "Pousada do Rio"),
                    ("07:30", "Pousada do Rio"), (None, "Casa da tia, Rua 7")], horas)
    check("os totais somam pessoas, adultos e crianças",
          lista["totais"] == {"grupos": 4, "pessoas": 6, "adultos": 5, "criancas": 1,
                              "sem_ponto": 0, "solicitadas": 1}, lista["totais"])
    pp = {(x["hora_busca"], x["ponto"]): x for x in lista["por_ponto"]}
    check("por ponto agrupa grupos e pessoas",
          pp[("07:30", "Pousada do Rio")]["grupos"] == 2
          and pp[("07:30", "Pousada do Rio")]["pessoas"] == 4, lista["por_ponto"])
    check("o telefone sai formatado", lista["grupos"][0]["telefone"] == "(11) 99999-0003",
          lista["grupos"][0])
    t = lista["texto"]
    check("o texto para o WhatsApp tem cabeçalho com guia",
          t.startswith("*Lagoa Azul*") and "Guia: Guia João" in t, t)
    check("e os blocos por busca, com a não confirmada sinalizada",
          "*07:30 – Pousada do Rio*" in t and "*Casa da tia, Rua 7*" in t
          and "⚠️ não confirmada" in t, t)
    canceladas = await DB.appointments.count_documents(
        {"tenant_id": TENANT, "inicio": ctx["quinta_8"], "status": "cancelado"})
    check("só quem ocupa lugar entra na lista", lista["totais"]["grupos"] + canceladas
          == await DB.appointments.count_documents({"tenant_id": TENANT, "inicio": ctx["quinta_8"]}))


async def etapa_cancelar_saida(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    inicio = ctx["quinta_8"]
    avisos = []

    async def avisar_falso(db, tenant_id, doc, tipo, config=None, marcar_espera=False, **kw):
        avisos.append((tipo, doc["id"], doc.get("motivo_cancelamento")))
        return doc["contact_id"] != "c-3"  # um grupo sem WhatsApp

    original = agenda_worker.avisar
    agenda_worker.avisar = avisar_falso
    try:
        r = await rs.cancelar_saida(rs.CancelarSaidaIn(
            service_id=lagoa["id"], inicio=inicio, motivo="Chuva forte"), ADMIN)
        check("a resposta volta antes dos avisos (que saem espaçados)",
              r["avisando"] == 4 and rs._AVISOS_EM_ANDAMENTO, r)
        await asyncio.gather(*list(rs._AVISOS_EM_ANDAMENTO))
    finally:
        agenda_worker.avisar = original
    check("cancelar a saída cancela todos os grupos dela", r["canceladas"] == 4, r)
    check("e avisa cada um com a mensagem de saída cancelada e o motivo",
          len(avisos) == 4 and all(t == "cancelamento_saida" and m == "Chuva forte"
                                   for t, _, m in avisos), avisos)
    marcas = sorted([(await DB.appointments.find_one({"id": i}))["aviso_saida"]
                     for _, i, _ in avisos])
    check("cada reserva guarda se o aviso saiu",
          marcas == ["enviado", "enviado", "sem_aviso", "sem_aviso"], marcas)
    painel = await hoje.montar(DB, ADMIN)
    alertas = [a for a in painel["alertas"] if a["tipo"] == "aviso_saida"]
    check("quem ficou sem aviso aparece na tela Hoje para a agência ligar",
          len(alertas) == 2 and all("Avise por telefone" in a["texto"] for a in alertas), alertas)
    check("a saída aparece cancelada, sem vaga e fechada, com o motivo",
          r["saida"]["cancelada"] and r["saida"]["vagas"] == 0 and not r["saida"]["aberta"]
          and r["saida"]["motivo_cancelamento"] == "Chuva forte", r["saida"])
    check("os lugares voltaram todos", await contador(lagoa["id"], inicio) == 0)
    await invariante("saída cancelada", lagoa["id"], inicio)
    marcada = await DB.appointments.find_one({"id": avisos[0][1]})
    check("a reserva guarda que foi cancelada pela saída",
          marcada["cancelada_pela_saida"] and marcada["status"] == "cancelado")

    config = await dominio.configuracao(DB, TENANT)
    texto = agenda_worker.texto_de("cancelamento_saida", marcada, config)
    check("a mensagem ao turista diz o motivo", "cancelada: Chuva forte." in texto, texto)

    det = await erro(reservar(ctx, dia=QUINTA, adultos=1), 409)
    check("ninguém reserva numa saída cancelada, e o 409 diz por quê",
          det == "Saída cancelada: Chuva forte", det)
    check("o próprio `$inc` recusa (a garantia contra a corrida com o bot)",
          await saidas._ocupa(DB, TENANT, lagoa["id"], inicio, 1, 99) is None)
    vagas = await saidas.saidas_com_vaga(DB, TENANT, passeio=lagoa, dia=QUINTA.isoformat(),
                                         pessoas=1, config=config)
    check("bot e IA deixam de oferecer a saída", all(s["inicio"] != inicio for s in vagas), vagas)

    await erro(rs.cancelar_saida(rs.CancelarSaidaIn(
        service_id=lagoa["id"], inicio=inicio, motivo=" "), ADMIN), 400)
    check("cancelar sem motivo é recusado", True)

    linha = await rs.reabrir_saida(rs.SaidaRefIn(service_id=lagoa["id"], inicio=inicio), ADMIN)
    check("reabrir devolve a saída à venda", not linha["cancelada"] and linha["vagas"] == 10, linha)
    check("mas as reservas canceladas continuam canceladas",
          (await DB.appointments.find_one({"id": avisos[0][1]}))["status"] == "cancelado")
    novo = await reservar(ctx, dia=QUINTA, adultos=2)
    check("e dá para reservar de novo", novo["pessoas"] == 2)
    await invariante("reaberta", lagoa["id"], inicio)


async def etapa_no_com_embarque(ctx):
    _conectar_fake()
    no = {"id": "n-ag", "type": "schedule", "data": {"formato": "lista"}}
    session = {"id": "s-emb", "tenant_id": TENANT, "contact_id": "c-1",
               "whatsapp_connection_id": "conn-1", "variables": {}, "awaiting": None}
    await agenda_flow.avancar(no, session, [])
    await agenda_flow.avancar(no, session, [], escolha_id=ctx["lagoa"]["id"])
    await agenda_flow.avancar(no, session, [], escolha_id="6")
    outputs = []
    saida = await agenda_flow.avancar(no, session, outputs, resposta="1")
    check("com pontos cadastrados, o nó pergunta onde buscar antes de gravar",
          saida is None and session["awaiting"]["etapa"] == "embarque", (saida, outputs))
    itens = outputs[0]["itens"]
    check("a lista traz os pontos ativos com a hora da busca e termina em 'Outro lugar'",
          [i["label"] for i in itens] == ["Hotel Gran Lençóis", "Pousada do Rio", "Outro lugar"]
          and itens[0]["descricao"].startswith("busca às 07:20"), itens)
    check("a saída escolhida fica guardada enquanto espera",
          session["awaiting"]["dados"]["hora"].endswith("+00:00"))

    outputs = []
    saida = await agenda_flow.avancar(no, session, outputs, escolha_id=ctx["pousada"]["id"])
    check("escolhido o ponto, o nó grava a solicitação", saida == agenda_flow.SAIDA_OK, outputs)
    doc = await DB.appointments.find_one({"id": session["variables"]["agendamento_id"]})
    check("com o ponto e a hora da busca", doc["ponto_embarque_nome"] == "Pousada do Rio"
          and doc["horario_busca"], doc)
    texto = next(o["content"] for o in outputs if o["type"] == "text")
    check("o texto de sucesso diz a busca", "Busca: 07:30 – Pousada do Rio" in texto, texto)
    check("e a variável vai para os nós seguintes",
          session["variables"]["agendamento_busca"] == "07:30 – Pousada do Rio")

    # "Outro lugar" reserva com a busca a combinar.
    session = {"id": "s-emb2", "tenant_id": TENANT, "contact_id": "c-2",
               "variables": {}, "awaiting": None}
    await agenda_flow.avancar(no, session, [])
    await agenda_flow.avancar(no, session, [], escolha_id=ctx["lagoa"]["id"])
    await agenda_flow.avancar(no, session, [], escolha_id="6")
    await agenda_flow.avancar(no, session, [], resposta="1")
    outputs = []
    saida = await agenda_flow.avancar(no, session, outputs, resposta="3")
    doc = await DB.appointments.find_one({"id": session["variables"]["agendamento_id"]})
    check("'Outro lugar' reserva com a busca a combinar e o recado para a equipe",
          saida == agenda_flow.SAIDA_OK and doc["ponto_embarque_nome"] is None
          and "combinar" in doc["embarque_obs"], doc)

    # Nó com a pergunta desligada não pergunta.
    sem = {"id": "n-ag", "type": "schedule", "data": {"formato": "lista",
                                                      "perguntar_embarque": False}}
    session = {"id": "s-emb3", "tenant_id": TENANT, "contact_id": "c-3",
               "variables": {}, "awaiting": None}
    await agenda_flow.avancar(sem, session, [])
    await agenda_flow.avancar(sem, session, [], escolha_id=ctx["lagoa"]["id"])
    await agenda_flow.avancar(sem, session, [], escolha_id="6")
    saida = await agenda_flow.avancar(sem, session, [], resposta="1")
    check("com perguntar_embarque desligado, o nó grava direto", saida == agenda_flow.SAIDA_OK)


async def etapa_ia_com_embarque(ctx):
    _conectar_fake()
    import ai_agents
    await DB.contacts.insert_one({"id": "c-emb", "tenant_id": TENANT, "nome": "Turista Hotel",
                                  "numero_whatsapp": "5511999990077"})
    contato = await DB.contacts.find_one({"id": "c-emb"}, {"_id": 0})
    executar = ai_tools.executor(DB, TENANT, contato, {"id": "s-ia2"})
    sabado = SEGUNDA + timedelta(days=12)
    r = await executar("solicitar_reserva", {"passeio": "Lagoa Azul", "dia": sabado.isoformat(),
                                             "hora": "08:00", "adultos": 2,
                                             "ponto_embarque": "gran lençóis"})
    check("o ponto dito pelo turista casa com o cadastrado", "07:20 – Hotel Gran Lençóis" in r, r)
    doc = await DB.appointments.find_one({"tenant_id": TENANT, "contact_id": "c-emb"})
    check("e é gravado com o id do ponto", doc["ponto_embarque_id"] == ctx["hotel"]["id"], doc)
    r = await executar("solicitar_reserva", {"passeio": "Circuito Lagoa Bonita",
                                             "dia": sabado.isoformat(), "hora": "09:00",
                                             "adultos": 1, "ponto_embarque": "Airbnb da Rua 3"})
    check("lugar fora da lista vira texto com a busca a combinar",
          "Airbnb da Rua 3 (horário a combinar" in r, r)

    agente = {"nome": "Bia", "tipo": "agendamento", "agenda_ativa": True}
    bloco, _, _ = await ai_agents.preparar_agenda(DB, TENANT, agente, contato)
    check("o prompt lista os pontos ativos com a antecedência",
          "Hotel Gran Lençóis (busca 40 min antes da saída)" in bloco and "Píer de Atins" not in bloco,
          bloco)


async def etapa_guardas_fase2():
    _conectar_fake()
    import inspect
    from auth import require_tenant_admin, require_tenant_member

    def guarda(fn):
        for p in inspect.signature(fn).parameters.values():
            dep = getattr(p.default, "dependency", None)
            if dep in (require_tenant_admin, require_tenant_member):
                return dep
        return None

    check("cancelar e reabrir saída são só do admin",
          guarda(rs.cancelar_saida) is require_tenant_admin
          and guarda(rs.reabrir_saida) is require_tenant_admin)
    check("escalar equipe, lista de embarque e embarque da reserva são da equipe",
          all(guarda(f) is require_tenant_member
              for f in (rs.escalar_equipe, rs.lista_de_embarque, rs.mudar_embarque)))


# ---------------------------------------------------------------------------

async def _suite():
    print("\n=== saídas ===")
    ctx = await etapa_semear()
    await etapa_grade_invalida()
    await etapa_saidas_da_janela(ctx)
    await etapa_reservar_ocupa(ctx)
    await etapa_capacidade(ctx)
    await etapa_duplicate_key(ctx)
    await etapa_cancelar_libera(ctx)
    await etapa_status(ctx)
    await etapa_alterar_pessoas(ctx)
    await etapa_solicitacao_expira(ctx)
    await etapa_aviso_expiracao(ctx)
    await etapa_feriado_e_excecao(ctx)
    await etapa_antecedencia_e_encaixe(ctx)
    await etapa_proximos_dias(ctx)
    await etapa_isolamento(ctx)
    await etapa_exclusao_protegida(ctx)
    await etapa_rotas(ctx)
    await etapa_guardas()
    await etapa_lembrete(ctx)
    await etapa_resposta_do_lembrete(ctx)
    await etapa_resposta_solta_ao_lembrete(ctx)
    await etapa_duplicata(ctx)
    await etapa_corrida_de_mensagens(ctx)
    await etapa_ferramentas_ia(ctx)
    await etapa_ia_simulada(ctx)
    await etapa_prompt_da_ia(ctx)
    await etapa_trava_confirmacao(ctx)
    await etapa_no_de_fluxo(ctx)
    await etapa_no_resposta_invalida(ctx)
    await etapa_no_simulado(ctx)
    await etapa_validacao_do_no()
    # Fase 2: embarque
    await etapa_pontos_embarque(ctx)
    await etapa_embarque_na_reserva(ctx)
    await etapa_equipe_da_saida(ctx)
    await etapa_lista_de_embarque(ctx)
    await etapa_cancelar_saida(ctx)
    await etapa_no_com_embarque(ctx)
    await etapa_ia_com_embarque(ctx)
    await etapa_guardas_fase2()
    print(f"\n{ok} verificações OK")


def test_suite():
    asyncio.run(_suite())
