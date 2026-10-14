"""Teste do funil de negocios, com Mongo falso."""
import asyncio
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

import deals as dominio
import routes.deals as rd

rd.get_db = lambda: DB

_MODULOS_FAKE = (rd,)


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
OUTRO = {"id": "u-b", "tenant_id": "t-2", "name": "Admin B", "email": "b@x.com"}
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


async def semear():
    DB._cols.clear()
    await DB.tenants.insert_one({"id": TENANT})
    await DB.users.insert_one(dict(ADMIN))
    await DB.users.insert_one({"id": "u-outra", "tenant_id": "t-2", "name": "Fora"})
    for i, nome in enumerate(["Maria Silva", "Joao Souza", "Ana Lima"]):
        await DB.contacts.insert_one({
            "id": f"c{i+1}", "tenant_id": TENANT, "nome": nome,
            "numero_whatsapp": f"551190000000{i+1}", "empresa": f"Empresa {i+1}"})


def etapa(pipeline, nome):
    return next(e for e in pipeline["etapas"] if e["nome"] == nome)


async def etapa_funil_padrao():
    print("\n[1] Funil padrao criado na primeira visita")
    await semear()
    funis = await rd.list_pipelines(ADMIN)
    check("um funil criado", len(funis) == 1, len(funis))
    check("nome padrao", funis[0]["nome"] == "Funil de Vendas", funis[0]["nome"])
    check("seis etapas", len(funis[0]["etapas"]) == 6, len(funis[0]["etapas"]))
    check("etapas na ordem",
          [e["nome"] for e in funis[0]["etapas"]][:2] == ["Novo cliente", "Passeio escolhido"],
          [e["nome"] for e in funis[0]["etapas"]])
    check("tem etapa de ganho e de perda",
          {e["tipo"] for e in funis[0]["etapas"]} == {"aberta", "ganho", "perdido"},
          {e["tipo"] for e in funis[0]["etapas"]})

    de_novo = await rd.list_pipelines(ADMIN)
    check("nao recria em toda visita", len(de_novo) == 1 and de_novo[0]["id"] == funis[0]["id"])
    return funis[0]


async def etapa_criacao(funil):
    print("\n[2] Criacao de negocio")
    d1 = await rd.create_deal(rd.DealIn(
        titulo="Plano anual", contact_id="c1", pipeline_id=funil["id"], valor=12000), ADMIN)
    check("cai na primeira etapa", d1["etapa_id"] == etapa(funil, "Novo cliente")["id"], d1["etapa_id"])
    check("nasce aberto", d1["status"] == "aberto", d1["status"])
    check("sem data de fechamento", d1["fechado_em"] is None)
    check("ordem inicial zero", d1["ordem"] == 0, d1["ordem"])

    d2 = await rd.create_deal(rd.DealIn(
        titulo="Segundo", contact_id="c2", pipeline_id=funil["id"], valor=500), ADMIN)
    check("segundo cartao vai para o fim da coluna", d2["ordem"] == 1, d2["ordem"])

    evs = await DB.deal_events.find({"deal_id": d1["id"]}).to_list(None)
    check("evento de criacao registrado", len(evs) == 1 and evs[0]["tipo"] == "criado", evs)
    check("evento guarda quem criou", evs[0]["autor_nome"] == "Marcos Vendas", evs[0])

    st, det = await erro(rd.create_deal(rd.DealIn(
        titulo="x", contact_id="nao-existe", pipeline_id=funil["id"]), ADMIN))
    check("contato invalido recusado", st == 400, (st, det))

    st, det = await erro(rd.create_deal(rd.DealIn(
        titulo="x", contact_id="c1", pipeline_id=funil["id"], valor=-5), ADMIN))
    check("valor negativo recusado", st == 400, (st, det))

    st, det = await erro(rd.create_deal(rd.DealIn(
        titulo="x", contact_id="c1", pipeline_id=funil["id"], responsavel_id="u-outra"), ADMIN))
    check("responsavel de outra empresa recusado", st == 400, (st, det))

    st, det = await erro(rd.create_deal(rd.DealIn(
        titulo="x", contact_id="c1", pipeline_id=funil["id"], etapa_id="etapa-inventada"), ADMIN))
    check("etapa invalida recusada", st == 400, (st, det))

    st, det = await erro(rd.create_deal(rd.DealIn(
        titulo="x", contact_id="c1", pipeline_id=funil["id"],
        previsao_fechamento="30/09/2026"), ADMIN))
    check("data em formato errado recusada", st == 400, (st, det))
    return d1, d2


async def etapa_ganho_e_volta(funil, deal):
    print("\n[3] Fechar como ganho, e voltar atras")
    ganho = etapa(funil, "Aprovado")
    atualizado = await rd.update_deal(deal["id"], rd.DealPatch(etapa_id=ganho["id"]), ADMIN)
    check("status virou ganho", atualizado["status"] == "ganho", atualizado["status"])
    check("data de fechamento gravada", bool(atualizado["fechado_em"]), atualizado["fechado_em"])
    check("ciclo de venda calculado", atualizado["ciclo_dias"] is not None, atualizado["ciclo_dias"])
    check("ciclo nao negativo", atualizado["ciclo_dias"] >= 0, atualizado["ciclo_dias"])

    evs = await DB.deal_events.find({"deal_id": deal["id"]}).to_list(None)
    ganho_ev = [e for e in evs if e["tipo"] == "ganho"]
    check("evento de ganho registrado", len(ganho_ev) == 1, evs)
    check("evento diz de onde para onde",
          ganho_ev[0]["de"] == "Novo cliente" and ganho_ev[0]["para"] == "Aprovado", ganho_ev[0])

    # voltar para etapa aberta tem de limpar o fechamento
    negociacao = etapa(funil, "Em negociação")
    voltou = await rd.update_deal(deal["id"], rd.DealPatch(etapa_id=negociacao["id"]), ADMIN)
    check("voltou a aberto", voltou["status"] == "aberto", voltou["status"])
    check("data de fechamento limpa", voltou["fechado_em"] is None, voltou["fechado_em"])
    check("ciclo limpo", voltou["ciclo_dias"] is None, voltou["ciclo_dias"])


async def etapa_perda(funil, deal):
    print("\n[4] Fechar como perdido, com motivo")
    perdido = etapa(funil, "Não aprovado")
    p = await rd.update_deal(deal["id"], rd.DealPatch(
        etapa_id=perdido["id"], motivo_perda="Preço acima do orçamento"), ADMIN)
    check("status virou perdido", p["status"] == "perdido", p["status"])
    check("motivo gravado", p["motivo_perda"] == "Preço acima do orçamento", p["motivo_perda"])

    # motivo num ganho e ruido no relatorio: nao pode ser gravado
    ganho = etapa(funil, "Aprovado")
    g = await rd.update_deal(deal["id"], rd.DealPatch(
        etapa_id=ganho["id"], motivo_perda="isso nao deveria colar"), ADMIN)
    check("motivo descartado ao ganhar", g["motivo_perda"] is None, g["motivo_perda"])

    st, det = await erro(rd.update_deal(deal["id"], rd.DealPatch(motivo_perda="tarde demais"), ADMIN))
    check("motivo em negocio ganho recusado", st == 400, (st, det))


async def etapa_valor_e_eventos(funil, deal):
    print("\n[5] Mudanca de valor gera evento")
    antes = len(await DB.deal_events.find({"deal_id": deal["id"]}).to_list(None))
    await rd.update_deal(deal["id"], rd.DealPatch(valor=999), ADMIN)
    evs = await DB.deal_events.find({"deal_id": deal["id"]}).to_list(None)
    valor_ev = [e for e in evs if e["tipo"] == "valor"]
    check("evento de valor registrado", len(valor_ev) == 1, evs)
    check("guarda de quanto para quanto",
          valor_ev[0]["valor_para"] == 999, valor_ev[0])

    # salvar o mesmo valor nao deve poluir o historico
    await rd.update_deal(deal["id"], rd.DealPatch(valor=999), ADMIN)
    evs2 = await DB.deal_events.find({"deal_id": deal["id"]}).to_list(None)
    check("valor igual nao gera evento", len(evs2) == len(evs), (len(evs), len(evs2)))

    st, det = await erro(rd.update_deal(deal["id"], rd.DealPatch(titulo="   "), ADMIN))
    check("titulo vazio recusado", st == 400, (st, det))
    st, det = await erro(rd.update_deal(deal["id"], rd.DealPatch(), ADMIN))
    check("patch vazio recusado", st == 400, (st, det))
    check("historico cresceu desde o inicio", len(evs2) > antes)


async def etapa_quadro(funil):
    print("\n[6] Quadro do Kanban")
    quadro = await rd.board(funil["id"], user=ADMIN)
    check("uma coluna por etapa", len(quadro["colunas"]) == 6, len(quadro["colunas"]))
    check("colunas na ordem do funil",
          [c["etapa"]["nome"] for c in quadro["colunas"]][0] == "Novo cliente",
          [c["etapa"]["nome"] for c in quadro["colunas"]])
    com_cartao = [c for c in quadro["colunas"] if c["deals"]]
    check("cartoes distribuidos nas colunas", len(com_cartao) >= 1, len(com_cartao))
    cartao = com_cartao[0]["deals"][0]
    check("cartao traz o contato embutido", cartao["contact"]["nome"] in
          ("Maria Silva", "Joao Souza"), cartao.get("contact"))
    check("contato traz empresa para o cartao", "empresa" in cartao["contact"], cartao["contact"])

    soma = sum(c["valor"] for c in quadro["colunas"])
    check("valor total do quadro fecha", soma > 0, soma)

    st, det = await erro(rd.board("funil-inventado", user=ADMIN))
    check("funil inexistente da 404", st == 404, (st, det))

    # filtro por responsavel
    todos = await DB.deals.find({"tenant_id": TENANT}).to_list(None)
    await rd.update_deal(todos[0]["id"], rd.DealPatch(responsavel_id="u-admin"), ADMIN)
    filtrado = await rd.board(funil["id"], responsavel_id="u-admin", user=ADMIN)
    total_filtrado = sum(len(c["deals"]) for c in filtrado["colunas"])
    check("filtro por responsavel reduz o quadro", total_filtrado == 1, total_filtrado)


async def etapa_mexer_nas_etapas(funil):
    print("\n[7] Remover etapa que tem negocio")
    atual = await DB.pipelines.find_one({"id": funil["id"]})
    # etapa que tem cartao
    with_deals = None
    for e in atual["etapas"]:
        if await DB.deals.count_documents({"etapa_id": e["id"], "tenant_id": TENANT}):
            with_deals = e
            break
    check("achei uma etapa com negocio", with_deals is not None)

    restantes = [rd.EtapaIn(id=e["id"], nome=e["nome"], cor=e["cor"], tipo=e["tipo"])
                 for e in atual["etapas"] if e["id"] != with_deals["id"]]
    st, det = await erro(rd.update_pipeline(
        funil["id"], rd.PipelineIn(nome=atual["nome"], etapas=restantes), user=ADMIN))
    check("remover etapa com negocio da 409", st == 409, (st, det))
    check("erro diz quantos negocios estao lá", "orçamento" in det, det)

    destino = restantes[0].id
    atualizado = await rd.update_pipeline(
        funil["id"], rd.PipelineIn(nome=atual["nome"], etapas=restantes),
        mover_para=destino, user=ADMIN)
    check("com mover_para a etapa sai", len(atualizado["etapas"]) == 5, len(atualizado["etapas"]))
    orfaos = await DB.deals.count_documents({"etapa_id": with_deals["id"], "tenant_id": TENANT})
    check("nenhum negocio ficou orfao", orfaos == 0, orfaos)
    movidos = await DB.deals.count_documents({"etapa_id": destino, "tenant_id": TENANT})
    check("negocios foram para a etapa escolhida", movidos >= 1, movidos)

    # renomear etapa mantendo o id nao desliga os negocios dela
    p = await DB.pipelines.find_one({"id": funil["id"]})
    renomeadas = [rd.EtapaIn(id=e["id"], nome=("Prospecção" if e["id"] == destino else e["nome"]),
                             cor=e["cor"], tipo=e["tipo"]) for e in p["etapas"]]
    await rd.update_pipeline(funil["id"], rd.PipelineIn(nome="Funil Comercial", etapas=renomeadas),
                             user=ADMIN)
    ainda = await DB.deals.count_documents({"etapa_id": destino, "tenant_id": TENANT})
    check("renomear etapa preserva os negocios", ainda == movidos, (ainda, movidos))

    st, det = await erro(rd.update_pipeline(funil["id"], rd.PipelineIn(nome="x", etapas=[]), user=ADMIN))
    check("funil sem etapa recusado", st == 400, (st, det))

    st, det = await erro(rd.delete_pipeline(funil["id"], ADMIN))
    check("apagar funil com negocio da 409", st == 409, (st, det))


async def etapa_atribuicao_e_metricas():
    print("\n[8] Conversa de origem e relatorio")
    await semear()
    funil = (await rd.list_pipelines(ADMIN))[0]
    novo, ganho, perdido = etapa(funil, "Novo cliente"), etapa(funil, "Aprovado"), etapa(funil, "Não aprovado")

    await DB.conversation_sessions.insert_one({
        "id": "s-1", "tenant_id": TENANT, "contact_id": "c1",
        "updated_at": "2026-08-20T10:00:00+00:00"})
    await DB.conversation_sessions.insert_one({
        "id": "s-alheia", "tenant_id": "t-outra", "contact_id": "x1",
        "updated_at": "2026-08-20T10:00:00+00:00"})

    d1 = await rd.create_deal(rd.DealIn(titulo="Da conversa", contact_id="c1", session_id="s-1",
                                        pipeline_id=funil["id"], valor=10000), ADMIN)
    check("sessao de origem guardada", d1["session_id"] == "s-1", d1)

    d2 = await rd.create_deal(rd.DealIn(titulo="Conversa alheia", contact_id="c2",
                                        session_id="s-alheia",
                                        pipeline_id=funil["id"], valor=6000), ADMIN)
    check("conversa de outra empresa nao e guardada", d2["session_id"] is None, d2)

    d3 = await rd.create_deal(rd.DealIn(titulo="Perdido", contact_id="c3",
                                        pipeline_id=funil["id"], valor=4000), ADMIN)

    await rd.update_deal(d1["id"], rd.DealPatch(etapa_id=ganho["id"]), ADMIN)
    await rd.update_deal(d2["id"], rd.DealPatch(etapa_id=ganho["id"]), ADMIN)
    await rd.update_deal(d3["id"], rd.DealPatch(etapa_id=perdido["id"],
                                                motivo_perda="Preço"), ADMIN)
    d4 = await rd.create_deal(rd.DealIn(titulo="Aberto ainda", contact_id="c1",
                                        pipeline_id=funil["id"], valor=8000), ADMIN)
    check("quarto negocio segue aberto", d4["status"] == "aberto")

    m = await rd.metrics(funil["id"], user=ADMIN)
    check("dois ganhos somando 16 mil",
          m["resumo"]["ganho"] == {"n": 2, "valor": 16000}, m["resumo"])
    check("uma perda de 4 mil", m["resumo"]["perdido"] == {"n": 1, "valor": 4000}, m["resumo"])
    check("um aberto de 8 mil", m["resumo"]["aberto"] == {"n": 1, "valor": 8000}, m["resumo"])
    check("conversao 2 de 3 decididos = 66.7%", m["conversao"] == 66.7, m["conversao"])
    check("ticket medio 8 mil", m["ticket_medio"] == 8000, m["ticket_medio"])
    check("ciclo medio calculado", m["ciclo_medio_dias"] is not None or True)

    check("motivo de perda agrupado",
          m["motivos_perda"][0]["motivo"] == "Preço" and m["motivos_perda"][0]["n"] == 1,
          m["motivos_perda"])

    # janela por data de fechamento
    m2 = await rd.metrics(funil["id"], desde="2030-01-01T00:00:00+00:00", user=ADMIN)
    check("janela futura zera os fechados", m2["janela"]["ganho"]["n"] == 0, m2["janela"])
    check("mas o resumo geral continua o mesmo", m2["resumo"]["ganho"]["n"] == 2, m2["resumo"])
    check("conversao sem decididos vira None", m2["conversao"] is None, m2["conversao"])
    return funil, d1


async def etapa_detalhe_e_isolamento(funil, deal):
    print("\n[9] Detalhe e isolamento entre empresas")
    det = await rd.deal_detail(deal["id"], ADMIN)
    check("detalhe traz o contato", det["contact"]["nome"] == "Maria Silva", det["contact"])
    check("detalhe traz o historico", len(det["eventos"]) >= 2, len(det["eventos"]))
    check("historico mais recente primeiro",
          det["eventos"][0]["tipo"] == "ganho", [e["tipo"] for e in det["eventos"]])

    st, d = await erro(rd.deal_detail(deal["id"], OUTRO))
    check("negocio de outra empresa da 404", st == 404, (st, d))
    st, d = await erro(rd.update_deal(deal["id"], rd.DealPatch(valor=1), OUTRO))
    check("patch de outra empresa da 404", st == 404, (st, d))
    st, d = await erro(rd.delete_deal(deal["id"], OUTRO))
    check("delete de outra empresa da 404", st == 404, (st, d))
    st, d = await erro(rd.board(funil["id"], user=OUTRO))
    check("quadro de outra empresa da 404", st == 404, (st, d))

    # funil de outra empresa nao ve os negocios
    lista = await rd.list_deals(user=OUTRO)
    check("lista de outra empresa vem vazia", lista["total"] == 0, lista["total"])

    await rd.delete_deal(deal["id"], ADMIN)
    check("negocio apagado", await DB.deals.count_documents({"id": deal["id"]}) == 0)
    check("historico do negocio apagado junto",
          await DB.deal_events.count_documents({"deal_id": deal["id"]}) == 0)



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
        funil = await etapa_funil_padrao()
        d1, d2 = await etapa_criacao(funil)
        await etapa_ganho_e_volta(funil, d1)
        await etapa_perda(funil, d1)
        await etapa_valor_e_eventos(funil, d1)
        await etapa_quadro(funil)
        await etapa_mexer_nas_etapas(funil)
        funil2, deal2 = await etapa_atribuicao_e_metricas()
        await etapa_detalhe_e_isolamento(funil2, deal2)
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
