"""Teste de atividades e follow-up, com Mongo falso."""
import asyncio
import os
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

from fakedb import FakeDB
from fastapi import HTTPException

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import atividades as dominio
import routes.activities as ra
import routes.deals as rd

ra.get_db = lambda: DB
rd.get_db = lambda: DB

_MODULOS_FAKE = (ra, rd)


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
ADMIN = {"id": "u-admin", "tenant_id": TENANT, "name": "Marcos Vendas", "email": "m@x.com"}
VEND = {"id": "u-vend", "tenant_id": TENANT, "name": "Bia Vendas", "email": "b@x.com"}
OUTRO = {"id": "u-b", "tenant_id": "t-2", "name": "Admin B", "email": "b2@x.com"}
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


async def semear():
    DB._cols.clear()
    await DB.tenants.insert_one({"id": TENANT})
    await DB.users.insert_one(dict(ADMIN))
    await DB.users.insert_one(dict(VEND))
    await DB.users.insert_one({"id": "u-outra", "tenant_id": "t-2", "name": "Fora"})
    await DB.contacts.insert_one({"id": "c1", "tenant_id": TENANT, "nome": "Maria Silva",
                                  "numero_whatsapp": "5511900000001"})
    await DB.contacts.insert_one({"id": "c2", "tenant_id": TENANT, "nome": "Joao Souza",
                                  "numero_whatsapp": "5511900000002"})
    funil = (await rd.list_pipelines(ADMIN))[0]
    d = await rd.create_deal(rd.DealIn(titulo="Plano anual", contact_id="c1",
                                       pipeline_id=funil["id"], valor=12000), ADMIN)
    return funil, d


async def etapa_criacao():
    print("\n[1] Criar atividade")
    funil, deal = await semear()
    a = await ra.create_activity(ra.AtividadeIn(
        titulo="Ligar para Maria", tipo="ligacao", vence_em=iso(hours=3),
        contact_id="c1", deal_id=deal["id"]), ADMIN)
    check("nasce pendente", a["concluida_em"] is None)
    check("nao esta atrasada", a["atrasada"] is False, a["atrasada"])
    check("responsavel cai em quem criou", a["responsavel_id"] == "u-admin", a["responsavel_id"])
    check("ligada ao contato e ao negocio",
          a["contact_id"] == "c1" and a["deal_id"] == deal["id"], a)
    check("vencimento normalizado para UTC", a["vence_em"].endswith("+00:00"), a["vence_em"])

    st, det = await erro(ra.create_activity(ra.AtividadeIn(
        titulo="   ", vence_em=iso(hours=1)), ADMIN))
    check("titulo vazio recusado", st == 400, (st, det))

    st, det = await erro(ra.create_activity(ra.AtividadeIn(
        titulo="x", vence_em="30/09/2026"), ADMIN))
    check("data em formato errado recusada", st == 400, (st, det))

    st, det = await erro(ra.create_activity(ra.AtividadeIn(
        titulo="x", vence_em=iso(hours=1), responsavel_id="u-outra"), ADMIN))
    check("responsavel de outra empresa recusado", st == 400, (st, det))

    st, det = await erro(ra.create_activity(ra.AtividadeIn(
        titulo="x", vence_em=iso(hours=1), contact_id="c-de-fora"), ADMIN))
    check("contato invalido recusado", st == 400, (st, det))

    st, det = await erro(ra.create_activity(ra.AtividadeIn(
        titulo="x", vence_em=iso(hours=1), deal_id="d-de-fora"), ADMIN))
    check("negocio invalido recusado", st == 400, (st, det))
    return funil, deal, a


async def etapa_atraso_e_janelas(deal):
    print("\n[2] Atrasada, hoje e semana")
    atrasada = await ra.create_activity(ra.AtividadeIn(
        titulo="Cobrar proposta", vence_em=iso(days=-2), contact_id="c2"), ADMIN)
    check("vencida ja vem marcada como atrasada", atrasada["atrasada"] is True, atrasada)

    await ra.create_activity(ra.AtividadeIn(titulo="Reuniao semana que vem",
                                            vence_em=iso(days=5)), ADMIN)
    await ra.create_activity(ra.AtividadeIn(titulo="Longe", vence_em=iso(days=40)), ADMIN)

    so_atrasadas = await ra.list_activities(atrasadas=True, user=ADMIN)
    check("filtro de atrasadas traz uma", so_atrasadas["total"] == 1, so_atrasadas["total"])
    check("e a certa", so_atrasadas["items"][0]["titulo"] == "Cobrar proposta",
          so_atrasadas["items"][0]["titulo"])

    ate_semana = iso(days=7)
    da_semana = await ra.list_activities(de=iso(seconds=1), ate=ate_semana, user=ADMIN)
    titulos = [a["titulo"] for a in da_semana["items"]]
    check("janela da semana traz 2 (nao a de 40 dias)", da_semana["total"] == 2, titulos)
    check("a de 40 dias ficou fora", "Longe" not in titulos, titulos)

    r = await ra.summary(hoje_ate=iso(hours=12), semana_ate=ate_semana, user=ADMIN)
    check("resumo conta 4 pendentes", r["pendentes"] == 4, r)
    check("resumo conta 1 atrasada", r["atrasadas"] == 1, r)
    check("resumo conta 1 para hoje", r["hoje"] == 1, r)
    check("resumo conta 2 para a semana", r["semana"] == 2, r)

    check("pendentes vem da mais urgente para a menos",
          [a["titulo"] for a in (await ra.list_activities(user=ADMIN))["items"]][0]
          == "Cobrar proposta")
    return atrasada


async def etapa_dados_embutidos(deal):
    print("\n[3] Agenda mostra contato e negocio na propria linha")
    lista = await ra.list_activities(deal_id=deal["id"], user=ADMIN)
    check("filtro por negocio funciona", lista["total"] == 1, lista["total"])
    item = lista["items"][0]
    check("contato embutido", item["contact"]["nome"] == "Maria Silva", item.get("contact"))
    check("negocio embutido", item["deal"]["titulo"] == "Plano anual", item.get("deal"))
    check("valor do negocio embutido", item["deal"]["valor"] == 12000, item["deal"])

    por_contato = await ra.list_activities(contact_id="c2", user=ADMIN)
    check("filtro por contato funciona", por_contato["total"] == 1, por_contato["total"])
    check("atividade sem vinculo nao quebra a listagem",
          all("contact" in a for a in (await ra.list_activities(user=ADMIN))["items"]))


async def etapa_follow_up(atividade, deal):
    print("\n[4] Concluir e ja agendar o proximo passo")
    res = await ra.complete_activity(atividade["id"], ra.ConcluirIn(
        proxima=ra.ProximaIn(titulo="Ligar de novo", tipo="ligacao", vence_em=iso(days=3))), ADMIN)
    check("atividade concluida", res["atividade"]["concluida_em"] is not None)
    check("registra quem concluiu", res["atividade"]["concluida_por"] == "u-admin")
    check("concluida nao aparece mais como atrasada",
          res["atividade"]["atrasada"] is False, res["atividade"])
    check("proxima criada", res["proxima"] is not None)
    check("proxima herdou o contato", res["proxima"]["contact_id"] == atividade["contact_id"],
          res["proxima"])
    check("proxima guarda a origem", res["proxima"]["origem_id"] == atividade["id"], res["proxima"])
    check("proxima herdou o responsavel",
          res["proxima"]["responsavel_id"] == atividade["responsavel_id"], res["proxima"])

    st, det = await erro(ra.complete_activity(atividade["id"], None, ADMIN))
    check("concluir duas vezes da 409", st == 409, (st, det))

    st, det = await erro(ra.complete_activity(res["proxima"]["id"], ra.ConcluirIn(
        proxima=ra.ProximaIn(titulo="  ", vence_em=iso(days=1))), ADMIN))
    check("proxima sem descricao recusada", st == 400, (st, det))

    concluidas = await ra.list_activities(concluidas=True, user=ADMIN)
    check("lista de concluidas traz a que fechou", concluidas["total"] == 1, concluidas["total"])

    reaberta = await ra.reopen_activity(atividade["id"], ADMIN)
    check("reabrir limpa a conclusao", reaberta["concluida_em"] is None, reaberta)
    st, det = await erro(ra.reopen_activity(atividade["id"], ADMIN))
    check("reabrir o que esta aberto da 409", st == 409, (st, det))
    return res["proxima"]


async def etapa_sem_proximo_passo(funil, deal):
    print("\n[5] Cartao sem proximo passo no quadro")
    # deal tem atividade pendente (a reaberta + a proxima)
    quadro = await rd.board(funil["id"], user=ADMIN)
    cartao = next(c for col in quadro["colunas"] for c in col["deals"] if c["id"] == deal["id"])
    check("negocio com atividade pendente nao e sinalizado",
          cartao["sem_proximo_passo"] is False, cartao["sem_proximo_passo"])

    # negocio novo, sem atividade nenhuma
    d2 = await rd.create_deal(rd.DealIn(titulo="Sem follow-up", contact_id="c2",
                                        pipeline_id=funil["id"], valor=800), ADMIN)
    quadro = await rd.board(funil["id"], user=ADMIN)
    cartao2 = next(c for col in quadro["colunas"] for c in col["deals"] if c["id"] == d2["id"])
    check("negocio sem atividade e sinalizado", cartao2["sem_proximo_passo"] is True, cartao2)

    # concluir tudo do primeiro negocio deixa ele sem proximo passo tambem
    pendentes = await DB.activities.find(
        {"deal_id": deal["id"], "concluida_em": None}).to_list(None)
    for p in pendentes:
        await ra.complete_activity(p["id"], None, ADMIN)
    quadro = await rd.board(funil["id"], user=ADMIN)
    cartao = next(c for col in quadro["colunas"] for c in col["deals"] if c["id"] == deal["id"])
    check("concluir tudo passa a sinalizar o cartao",
          cartao["sem_proximo_passo"] is True, cartao["sem_proximo_passo"])

    # negocio fechado nao cobra proximo passo
    ganho = next(e for e in funil["etapas"] if e["tipo"] == "ganho")
    await rd.update_deal(d2["id"], rd.DealPatch(etapa_id=ganho["id"]), ADMIN)
    quadro = await rd.board(funil["id"], user=ADMIN)
    cartao2 = next(c for col in quadro["colunas"] for c in col["deals"] if c["id"] == d2["id"])
    check("negocio ganho nao e cobrado por follow-up",
          cartao2["sem_proximo_passo"] is False, cartao2["sem_proximo_passo"])


async def etapa_edicao_e_agenda_de_outro():
    print("\n[6] Reagendar, trocar responsavel e agenda por pessoa")
    a = await ra.create_activity(ra.AtividadeIn(
        titulo="Enviar contrato", vence_em=iso(days=1), responsavel_id="u-vend"), ADMIN)
    check("criada para outra pessoa", a["responsavel_id"] == "u-vend", a["responsavel_id"])

    minha = await ra.list_activities(responsavel_id="u-vend", user=ADMIN)
    check("agenda da Bia tem a atividade", minha["total"] == 1, minha["total"])
    r = await ra.summary(hoje_ate=iso(hours=2), semana_ate=iso(days=7),
                         responsavel_id="u-vend", user=ADMIN)
    check("resumo por pessoa conta so o dela", r["pendentes"] == 1, r)

    remarcada = await ra.update_activity(a["id"], ra.AtividadePatch(
        vence_em=iso(days=-1), titulo="Enviar contrato revisado"), ADMIN)
    check("reagendou", remarcada["titulo"] == "Enviar contrato revisado", remarcada["titulo"])
    check("agora esta atrasada", remarcada["atrasada"] is True, remarcada)

    # limpar o responsavel nao pode deixar a tarefa orfa
    sem_dono = await ra.update_activity(a["id"], ra.AtividadePatch(responsavel_id=""), ADMIN)
    check("responsavel vazio cai em quem editou",
          sem_dono["responsavel_id"] == "u-admin", sem_dono["responsavel_id"])

    st, det = await erro(ra.update_activity(a["id"], ra.AtividadePatch(), ADMIN))
    check("patch vazio recusado", st == 400, (st, det))
    st, det = await erro(ra.update_activity("nao-existe", ra.AtividadePatch(titulo="x"), ADMIN))
    check("atividade inexistente da 404", st == 404, (st, det))
    return a


async def etapa_exclusao_e_isolamento(atividade):
    print("\n[7] Exclusao e isolamento entre empresas")
    filha = await ra.create_activity(ra.AtividadeIn(titulo="Filha", vence_em=iso(days=2)), ADMIN)
    await DB.activities.update_one({"id": filha["id"]}, {"$set": {"origem_id": atividade["id"]}})

    await ra.delete_activity(atividade["id"], ADMIN)
    check("atividade apagada", await DB.activities.count_documents({"id": atividade["id"]}) == 0)
    ainda = await DB.activities.find_one({"id": filha["id"]})
    check("a proxima da corrente sobrevive", ainda is not None)
    check("mas perde a referencia da origem", ainda["origem_id"] is None, ainda["origem_id"])

    st, det = await erro(ra.delete_activity(filha["id"], OUTRO))
    check("delete de outra empresa da 404", st == 404, (st, det))
    st, det = await erro(ra.update_activity(filha["id"], ra.AtividadePatch(titulo="x"), OUTRO))
    check("patch de outra empresa da 404", st == 404, (st, det))
    st, det = await erro(ra.complete_activity(filha["id"], None, OUTRO))
    check("concluir de outra empresa da 404", st == 404, (st, det))
    lista = await ra.list_activities(user=OUTRO)
    check("lista de outra empresa vem vazia", lista["total"] == 0, lista["total"])
    r = await ra.summary(user=OUTRO)
    check("resumo de outra empresa vem zerado", r["pendentes"] == 0, r)



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
        funil, deal, a = await etapa_criacao()
        await etapa_atraso_e_janelas(deal)
        await etapa_dados_embutidos(deal)
        await etapa_follow_up(a, deal)
        await etapa_sem_proximo_passo(funil, deal)
        outra = await etapa_edicao_e_agenda_de_outro()
        await etapa_exclusao_e_isolamento(outra)
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
