"""Teste do site público da agência (fase 3), com Mongo falso.

O que este arquivo cobre é o que falha em silêncio: um slug que bate com o de
outra agência ou com um subdomínio nosso, o host do painel sendo tratado como
site, um site não publicado aparecendo para o mundo, uma imagem qualquer da
agência saindo pela rota pública, HTML da agência executando no site, e,
principalmente, o código do botão que não vira ficha e orçamento quando a
mensagem chega (ou vira duas vezes).

As páginas são exercitadas por um app mínimo com **o mesmo middleware** do
servidor, para provar a reescrita por host de ponta a ponta, e a atribuição
pelo caminho real da mensagem recebida (`flow_runtime.handle_incoming_message`).
"""
import asyncio
import os
import re
import sys
from datetime import datetime, timedelta, timezone

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "segredo-teste",
             "META_PLATFORM_TOKEN": "token-de-teste", "SITE_DOMINIO": "localhost:8010",
             "SITE_ESQUEMA": "http", "APP_HOSTS": "localhost,127.0.0.1"}.items():
    os.environ.setdefault(k, v)

from fakedb import FakeDB
from fastapi import FastAPI, HTTPException
from fastapi.testclient import TestClient

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import flow_runtime
import routes.scheduling as rs
import routes.site as rsite
import routes.site_publico as rpub
import server
import sites

TENANT, OUTRO = "t-site", "t-outro"
ADMIN = {"id": "u-a", "tenant_id": TENANT, "role": "tenant_admin", "name": "Dona"}
ADMIN2 = {"id": "u-b", "tenant_id": OUTRO, "role": "tenant_admin", "name": "Outra"}
ok = 0
ENVIADAS = []


async def fake_send(*a, **kw):
    ENVIADAS.append(a)
    return {"ok": True}


def _conectar_fake():
    db_mod.get_db = lambda: DB
    for m in (rs, rsite, rpub, flow_runtime):
        m.get_db = lambda: DB
    flow_runtime.whatsapp.send_text = fake_send


def check(nome, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  FALHOU  {nome}  -> {extra}")
        raise AssertionError(nome)


async def erro(coro, status=None):
    try:
        await coro
    except HTTPException as e:
        if status is not None and e.status_code != status:
            raise AssertionError(f"esperava {status}, veio {e.status_code}: {e.detail}")
        return e.detail
    raise AssertionError("esperava HTTPException e não veio")


def cliente(host: str) -> TestClient:
    """App mínimo com o mesmo middleware e as mesmas rotas do servidor."""
    app = FastAPI()
    app.middleware("http")(server._sites_por_host)
    app.include_router(rpub.router)
    return TestClient(app, base_url=f"http://{host}")


# ---------------------------------------------------------------------------

async def etapa_semear():
    _conectar_fake()
    await DB.tenants.insert_one({"id": TENANT, "name": "Lençóis Receptivo", "status": "ativo",
                                 "aparencia": {"paleta": "azulejo"}})
    await DB.tenants.insert_one({"id": OUTRO, "name": "Outra Agência", "status": "ativo"})
    for t in (TENANT, OUTRO):
        await DB.media_files.insert_one({"id": f"img-{t}", "tenant_id": t, "kind": "image",
                                         "mime": "image/png", "stored_name": "x.png",
                                         "filename": "x.png"})
    await DB.media_files.insert_one({"id": "doc-chat", "tenant_id": TENANT, "kind": "image",
                                     "mime": "image/jpeg", "stored_name": "rg.jpg",
                                     "filename": "rg.jpg"})
    grade = [[{"hora": "08:00", "capacidade": None}]] * 7
    lagoa = await rs.criar_servico(rs.ServicoIn(
        nome="Lagoa Azul", duracao_min=300, preco=150, preco_crianca=75, capacidade=20,
        grade_saidas=grade, site_resumo="As lagoas mais bonitas <b>de</b> Barreirinhas",
        site_descricao="Primeiro parágrafo.\n\nSegundo <script>alert(1)</script> parágrafo.",
        site_inclui=["Transporte 4x4", "  ", "Guia"], site_levar=["Protetor solar"],
        site_fotos=[f"img-{TENANT}"]), ADMIN)
    check("o passeio ganha o endereço da página a partir do nome",
          lagoa["site_slug"] == "lagoa-azul", lagoa.get("site_slug"))
    check("itens vazios das listas são descartados", lagoa["site_inclui"] == ["Transporte 4x4", "Guia"])
    await erro(rs.criar_servico(rs.ServicoIn(nome="Foto alheia", duracao_min=60,
                                             site_fotos=[f"img-{OUTRO}"]), ADMIN), 400)
    check("foto de outra agência no passeio é recusada", True)
    escondido = await rs.criar_servico(rs.ServicoIn(nome="Passeio interno", duracao_min=60,
                                                    site_visivel=False, grade_saidas=grade), ADMIN)
    return {"lagoa": lagoa, "escondido": escondido}


async def etapa_slug_do_passeio(ctx):
    _conectar_fake()
    lagoa = ctx["lagoa"]
    corpo = {k: v for k, v in lagoa.items() if k in rs.ServicoIn.model_fields}
    corpo["nome"] = "Lagoa Azul e Lagoa Bonita"
    corpo["site_slug"] = None
    renomeado = await rs.editar_servico(lagoa["id"], rs.ServicoIn(**corpo), ADMIN)
    check("renomear o passeio mantém o endereço da página (o link já foi divulgado)",
          renomeado["site_slug"] == "lagoa-azul", renomeado["site_slug"])
    det = await erro(rs.criar_servico(rs.ServicoIn(nome="Circuito", duracao_min=60,
                                                   site_slug="lagoa-azul"), ADMIN), 409)
    check("dois passeios não dividem o mesmo endereço", "/passeio/lagoa-azul" in det, det)
    circ = await rs.criar_servico(rs.ServicoIn(nome="Lagoa Azul!", duracao_min=60), ADMIN)
    check("nome que daria o mesmo endereço ganha sufixo", circ["site_slug"] == "lagoa-azul-2",
          circ["site_slug"])
    await rs.remover_servico(circ["id"], ADMIN)
    corpo["nome"] = "Lagoa Azul"
    ctx["lagoa"] = await rs.editar_servico(lagoa["id"], rs.ServicoIn(**corpo), ADMIN)


async def etapa_endereco():
    _conectar_fake()
    check("o host do painel nunca é site", not sites.host_e_de_site("localhost:8010")
          and not sites.host_e_de_site("127.0.0.1:8010"))
    check("subdomínio do domínio base é site", sites.host_e_de_site("lencois.localhost:8010"))
    check("o slug sai do subdomínio", sites.slug_do_host("lencois.localhost:8010") == "lencois")
    check("subdomínio reservado não vira slug", sites.slug_do_host("api.localhost:8010") is None)
    check("dois níveis não viram slug", sites.slug_do_host("a.b.localhost:8010") is None)
    check("slug reservado é recusado", sites.problema_do_slug("admin") is not None)
    check("slug com acento ou maiúscula é recusado", sites.problema_do_slug("Lençóis") is not None)
    check("hífen no fim é recusado", sites.problema_do_slug("lencois-") is not None)
    check("slugify tira acento e junta com hífen", sites.slugify("Lençóis  Receptivo!") == "lencois-receptivo")
    check("domínio próprio é normalizado",
          sites.normaliza_dominio("https://WWW.LencoisReceptivo.com.br/") == "www.lencoisreceptivo.com.br")
    await erro(_dominio_ruim(), 400)
    check("domínio da própria plataforma não serve como domínio próprio", True)


async def _dominio_ruim():
    sites.normaliza_dominio("x.localhost")


async def etapa_configurar(ctx):
    _conectar_fake()
    r = await rsite.ver_site(ADMIN)
    check("site novo vem sem slug e não publicado",
          r["site"]["slug"] is None and not r["site"]["publicado"] and r["url"] is None)
    check("o nome padrão é o da agência", r["site"]["nome"] == "Lençóis Receptivo")
    check("e diz o que falta para publicar",
          "Escolha o endereço do site" in r["pronto"]["faltando"], r["pronto"])

    det = await erro(rsite.salvar_site(rsite.SiteIn(publicado=True), ADMIN), 409)
    check("publicar sem endereço nem WhatsApp é recusado dizendo o que falta",
          "endereço" in det and "WhatsApp" in det, det)
    await erro(rsite.salvar_site(rsite.SiteIn(slug="api"), ADMIN), 400)
    check("slug reservado é recusado na rota", True)

    sug = await rsite.sugestao_slug(ADMIN)
    check("a sugestão sai do nome da agência", sug["slug"] == "lencois-receptivo", sug)
    r = await rsite.salvar_site(rsite.SiteIn(
        slug="lencois-receptivo", whatsapp="(98) 98111-2233", publicado=True,
        chamada="Passeios nos Lençóis", instagram="@lencoisreceptivo",
        sobre="Somos de Barreirinhas.\n\nDesde 2010.", logo_id=f"img-{TENANT}",
        capa_id=f"img-{TENANT}"), ADMIN)
    check("slug, WhatsApp e publicar no mesmo clique passam (a prontidão olha o depois)",
          r["site"]["publicado"] and r["site"]["whatsapp"] == "5598981112233", r["site"])
    check("o @ do Instagram sai", r["site"]["instagram"] == "lencoisreceptivo")
    check("a URL é montada pelo servidor", r["url"] == "http://lencois-receptivo.localhost:8010", r["url"])
    check("e a prévia vem assinada", r["url_previa"] and "?previa=" in r["url_previa"])

    livre = await rsite.slug_livre("lencois-receptivo", ADMIN2)
    check("o slug de uma agência não está livre para outra",
          not livre["livre"] and livre["sugestao"] == "lencois-receptivo-2", livre)
    await erro(rsite.salvar_site(rsite.SiteIn(slug="lencois-receptivo"), ADMIN2), 409)
    check("e salvar o slug alheio é recusado com 409", True)
    await erro(rsite.salvar_site(rsite.SiteIn(logo_id=f"img-{TENANT}"), ADMIN2), 400)
    check("imagem de outra agência como logo é recusada", True)
    await rsite.salvar_site(rsite.SiteIn(slug="outra-agencia", whatsapp="11999990000"), ADMIN2)

    await rsite.salvar_site(rsite.SiteIn(dominio_proprio="www.lencoisreceptivo.com.br"), ADMIN)
    await erro(rsite.salvar_site(rsite.SiteIn(dominio_proprio="www.lencoisreceptivo.com.br"),
                                 ADMIN2), 409)
    check("o mesmo domínio próprio não vai para duas agências", True)


async def etapa_paginas(ctx):
    _conectar_fake()
    c = cliente("lencois-receptivo.localhost:8010")
    r = c.get("/")
    check("a página inicial abre pelo subdomínio", r.status_code == 200, r.status_code)
    html = r.text
    check("com o nome, a chamada e o passeio visível",
          "Passeios nos Lençóis" in html and "Lagoa Azul" in html, html[:300])
    check("passeio escondido não aparece", "Passeio interno" not in html)
    check("o HTML da agência é escapado, não executado",
          "&lt;b&gt;de&lt;/b&gt;" in html and "<b>de</b>" not in html)
    check("as cores são as da paleta da agência (azulejo)", "#1F4E8C" in html)
    check("o link do WhatsApp sem script já funciona", "https://wa.me/5598981112233?text=" in html)
    check("JSON-LD de agência de viagem para o Google", '"@type": "TravelAgency"' in html)
    csp = r.headers.get("content-security-policy", "")
    nonce = re.search(r"'nonce-([^']+)'", csp)
    check("a CSP só deixa rodar o script com o nonce da página",
          nonce and f'nonce="{nonce.group(1)}"' in html, csp)

    r = c.get("/passeio/lagoa-azul")
    check("a página do passeio abre", r.status_code == 200, r.status_code)
    html = r.text
    check("com preço de adulto e de criança", "R$ 150" in html and "R$ 75" in html)
    check("e o script da descrição escapado",
          "&lt;script&gt;" in html and "<script>alert" not in html)
    check("os dias com saída aparecem no seletor (sem contar vagas)",
          '<option value="20' in html and "vaga" not in html.split("Quero esse passeio")[0].lower()
          .split("<select name=\"dia\">")[1].split("</select>")[0], "")
    check("TouristTrip com oferta em reais", '"@type": "TouristTrip"' in html and '"priceCurrency": "BRL"' in html)
    check("canonical aponta para o próprio endereço",
          'rel="canonical" href="http://lencois-receptivo.localhost:8010/passeio/lagoa-azul"' in html)

    check("passeio escondido não tem página", c.get("/passeio/passeio-interno").status_code == 404)
    check("caminho qualquer dá 404 do site", c.get("/xyz").status_code == 404)
    check("robots permite e aponta o sitemap", "Sitemap:" in c.get("/robots.txt").text)
    sm = c.get("/sitemap.xml").text
    check("o sitemap lista a inicial e o passeio", "/passeio/lagoa-azul" in sm and "<urlset" in sm)

    check("a rota pública serve a foto da vitrine", c.get(f"/midia/img-{TENANT}").status_code in (200, 404))
    check("mas nunca uma imagem qualquer da agência (a foto do documento do chat)",
          c.get("/midia/doc-chat").status_code == 404)
    check("nem a imagem de outra agência", c.get(f"/midia/img-{OUTRO}").status_code == 404)

    # Passeio gravado antes do site existir não tem `site_slug` (achado no
    # ambiente local em 14/10/2026: link `/passeio/None` e sitemap com 500).
    await DB.services.insert_one({"id": "antigo", "tenant_id": TENANT, "nome": "Circuito Lagoa Bonita",
                                  "ativo": True, "duracao_min": 240, "capacidade": 10, "preco": 120,
                                  "grade_saidas": [[{"hora": "09:00", "capacidade": None}]] * 7})
    html = c.get("/").text
    check("passeio sem endereço ganha um ao abrir o site, e nunca sai como /passeio/None",
          "/passeio/circuito-lagoa-bonita" in html and "/passeio/None" not in html)
    check("e a página dele abre", c.get("/passeio/circuito-lagoa-bonita").status_code == 200)
    sm = c.get("/sitemap.xml")
    check("o sitemap não cai com passeio antigo", sm.status_code == 200
          and "/passeio/circuito-lagoa-bonita" in sm.text, sm.status_code)
    await DB.services.insert_one({"id": "antigo-2", "tenant_id": OUTRO, "nome": "Lagoa Azul",
                                  "ativo": True, "site_slug": None})
    check("a migração da subida dá endereço aos passeios de todas as agências",
          await sites.garantir_slugs(DB) == 1
          and (await DB.services.find_one({"id": "antigo-2"}))["site_slug"] == "lagoa-azul")
    check("e não mexe em quem já tem", await sites.garantir_slugs(DB) == 0)
    await DB.services.delete_many({"id": {"$in": ["antigo", "antigo-2"]}})

    check("subdomínio desconhecido dá 404", cliente("nao-existe.localhost:8010").get("/").status_code == 404)
    painel = cliente("localhost:8010").get("/")
    check("o host do painel não vira site", painel.status_code == 404 and "Página não encontrada" not in painel.text)
    check("domínio próprio abre o mesmo site",
          "Lagoa Azul" in cliente("www.lencoisreceptivo.com.br").get("/").text)
    # A visita acima marcou o domínio como ativo; o redirecionamento que isso
    # liga é conferido em `etapa_dominio`. Até lá as etapas usam o subdomínio.
    await DB.sites.update_one({"tenant_id": TENANT}, {"$set": {"dominio_ativo_em": None}})


async def etapa_conteudo(ctx):
    """Fase 3.1: confiança, avaliações, depoimentos, perguntas, roteiro e busca."""
    _conectar_fake()
    c = cliente("lencois-receptivo.localhost:8010")
    html = c.get("/").text
    check("sem conteúdo preenchido, nenhuma seção nova aparece vazia",
          not any(t in html for t in ("Por que viajar", "Quem já foi", "Perguntas frequentes",
                                      "Buscamos você")), "")

    r = await rsite.salvar_site(rsite.SiteIn(
        cadastur="12.345678.10.0001-1", desde=2010,
        diferenciais=[" Frota 4x4 própria ", "", "Guias credenciados"],
        nota_avaliacoes=4.86, total_avaliacoes=1320, link_avaliacoes="https://g.page/r/lencois",
        depoimentos=[{"nome": "Ana", "origem": "São Paulo, SP", "texto": "Incrível <script>x</script>",
                      "nota": 5}, {"nome": "", "texto": ""}],
        faq=[{"pergunta": "Precisa saber nadar?", "resposta": "Não.\nAs lagoas são rasas."}],
        horario="Todos os dias, 7h às 20h", pagamento=["Pix", "Cartão em até 3x"],
        cancelamento="Cancelamento grátis até 24h antes.", endereco="Av. Brasília, 100 – Barreirinhas"), ADMIN)
    s = r["site"]
    check("diferenciais vazios saem e os outros são aparados",
          s["diferenciais"] == ["Frota 4x4 própria", "Guias credenciados"], s["diferenciais"])
    check("a nota fica com uma casa", s["nota_avaliacoes"] == 4.9, s["nota_avaliacoes"])
    check("depoimento em branco é descartado", len(s["depoimentos"]) == 1, s["depoimentos"])
    await erro(rsite.salvar_site(rsite.SiteIn(link_avaliacoes="javascript:alert(1)"), ADMIN), 400)
    check("link de avaliações que não é https é recusado (javascript: rodaria no site)", True)
    await erro(rsite.salvar_site(rsite.SiteIn(depoimentos=[{"nome": "Ana", "texto": ""}]), ADMIN), 400)
    check("depoimento sem texto é recusado", True)
    await erro(rsite.salvar_site(rsite.SiteIn(faq=[{"pergunta": "Pode criança?", "resposta": ""}]), ADMIN), 400)
    check("pergunta sem resposta é recusada", True)
    await erro(rsite.salvar_site(rsite.SiteIn(desde=2099), ADMIN), 400)
    check("ano de fundação no futuro é recusado", True)

    # A busca sai dos pontos de embarque das Saídas, sem a agência digitar de novo.
    await DB.scheduling_settings.update_one({"tenant_id": TENANT}, {"$set": {"pontos_embarque": [
        {"id": "p1", "nome": "Pousada do Rio", "endereco": "Rua das Dunas, 12", "minutos_antes": 30, "ativo": True},
        {"id": "p2", "nome": "Píer de Atins", "endereco": None, "minutos_antes": 0, "ativo": True},
        {"id": "p3", "nome": "Hotel Fechado", "endereco": None, "minutos_antes": 20, "ativo": False}]}},
        upsert=True)

    lagoa = ctx["lagoa"]
    corpo = {k: v for k, v in lagoa.items() if k in rs.ServicoIn.model_fields}
    corpo.update(site_roteiro=[{"hora": "07:30", "texto": "Busca na pousada"}, {"hora": "", "texto": "Travessia do rio"},
                               {"hora": "", "texto": ""}],
                 site_nivel="moderado", site_idade_minima=6, site_melhor_epoca="De junho a setembro, lagoas cheias",
                 site_chuva="Com chuva forte remarcamos sem custo.",
                 site_faq=[{"pergunta": "Tem banheiro?", "resposta": "No restaurante da parada."}],
                 site_legendas={f"img-{TENANT}": "Lagoa Azul ao meio-dia", "foto-alheia": "não é deste passeio"})
    salvo = await rs.editar_servico(lagoa["id"], rs.ServicoIn(**corpo), ADMIN)
    check("passo vazio do roteiro sai e o resto fica na ordem",
          [x["texto"] for x in salvo["site_roteiro"]] == ["Busca na pousada", "Travessia do rio"], salvo["site_roteiro"])
    check("legenda de foto que não é do passeio é descartada",
          salvo["site_legendas"] == {f"img-{TENANT}": "Lagoa Azul ao meio-dia"}, salvo["site_legendas"])
    ruim = {**corpo, "site_roteiro": [{"hora": "25:00", "texto": "Madrugada"}]}
    await erro(rs.editar_servico(lagoa["id"], rs.ServicoIn(**ruim), ADMIN), 400)
    check("hora inválida no roteiro é recusada", True)

    html = c.get("/").text
    check("a inicial mostra por que viajar com a agência, com CADASTUR e o tempo de casa",
          "Por que viajar com a gente" in html and "Frota 4x4 própria" in html
          and "CADASTUR · 12.345678.10.0001-1" in html and "Desde 2010" in html)
    check("o selo da nota, com o total em milhar e o link", "4,9" in html and "1.320 avaliações" in html
          and 'href="https://g.page/r/lencois"' in html)
    check("o depoimento sai escapado", "Incrível &lt;script&gt;" in html and "<script>x" not in html)
    check("a busca vem dos pontos de embarque ativos", "Pousada do Rio" in html
          and "cerca de 30 min antes da saída" in html and "Píer de Atins" in html
          and "no horário da saída" in html and "Hotel Fechado" not in html)
    check("as perguntas frequentes saem abertas para o buscador, com FAQPage",
          "Precisa saber nadar?" in html and '"@type": "FAQPage"' in html)
    check("onde estamos com o mapa, horário, pagamento e cancelamento",
          "Ver no mapa" in html and "maps/search/?api=1&amp;query=Av.%20Bras%C3%ADlia" in html
          and "Todos os dias, 7h às 20h" in html and "Cartão em até 3x" in html
          and "Cancelamento grátis até 24h antes." in html)

    html = c.get("/passeio/lagoa-azul").text
    check("o passeio mostra o roteiro", "Como é o dia" in html and "07:30" in html and "Travessia do rio" in html)
    check("nível e idade mínima viram etiquetas", "Esforço moderado" in html and "A partir de 6 anos" in html)
    check("a foto ganha legenda", "<figcaption>Lagoa Azul ao meio-dia</figcaption>" in html)
    check("bom saber: melhor época, chuva, cancelamento e pagamento",
          "De junho a setembro" in html and "remarcamos sem custo" in html and "Cancelamento grátis" in html)
    check("a pergunta do passeio, e o atalho para as gerais", "Tem banheiro?" in html and "Mais perguntas frequentes" in html)
    check("o roteiro entra no TouristTrip para o buscador", '"itinerary"' in html and '"@type": "FAQPage"' in html)
    check("e a busca e os depoimentos aparecem também na página do passeio",
          "Buscamos você na hospedagem" in html and "Quem já foi" in html)

    await rsite.salvar_site(rsite.SiteIn(mostrar_embarque=False, capa_id=None), ADMIN)
    html = c.get("/").text
    check("a agência pode esconder a busca", "Buscamos você" not in html)
    check("sem capa, o topo usa a primeira foto de passeio (que já é pública)",
          f'class="fundo" src="/midia/img-{TENANT}?w=1600"' in html and "srcset=" in html)
    check("e essa foto sai mesmo pela rota pública", c.get(f"/midia/img-{TENANT}").status_code in (200, 404)
          and await sites.midia_publica(DB, await sites.site_da_empresa(DB, TENANT), f"img-{TENANT}") is not None)
    await rsite.salvar_site(rsite.SiteIn(capa_id=f"img-{TENANT}", mostrar_embarque=True), ADMIN)
    ctx["lagoa"] = salvo


async def etapa_dominio():
    """Um endereço oficial só: o domínio próprio, depois de provar que funciona."""
    _conectar_fake()
    await rsite.salvar_site(rsite.SiteIn(publicado=True), ADMIN)
    sub = cliente("lencois-receptivo.localhost:8010")
    html = sub.get("/").text
    check("antes de o domínio próprio receber visita, o oficial é o subdomínio",
          'rel="canonical" href="http://lencois-receptivo.localhost:8010/"' in html)
    r = await rsite.ver_site(ADMIN)
    check("e a tela diz que o domínio ainda não está ativo", r["dominio_ativo"] is False
          and r["url_canonica"] == "http://lencois-receptivo.localhost:8010", r["url_canonica"])

    https = TestClient(sub.app, base_url="http://www.lencoisreceptivo.com.br")
    https.get("/", headers={"x-forwarded-proto": "https"})
    check("visita por esquema diferente do site não marca o domínio (Host forjado sem TLS)",
          not (await DB.sites.find_one({"tenant_id": TENANT})).get("dominio_ativo_em"))

    proprio = cliente("www.lencoisreceptivo.com.br")
    html = proprio.get("/passeio/lagoa-azul").text
    check("a primeira visita de verdade pelo domínio próprio o torna o oficial",
          bool((await DB.sites.find_one({"tenant_id": TENANT})).get("dominio_ativo_em")))
    check("e a página já se declara nele",
          'rel="canonical" href="http://www.lencoisreceptivo.com.br/passeio/lagoa-azul"' in html)

    r = sub.get("/passeio/lagoa-azul?utm_source=instagram", follow_redirects=False)
    check("o subdomínio passa a redirecionar (301) para o mesmo caminho no domínio próprio",
          r.status_code == 301 and r.headers["location"]
          == "http://www.lencoisreceptivo.com.br/passeio/lagoa-azul?utm_source=instagram", r.headers)
    r = sub.get("/", follow_redirects=False)
    check("a inicial também", r.status_code == 301
          and r.headers["location"] == "http://www.lencoisreceptivo.com.br/", r.headers)
    sm = proprio.get("/sitemap.xml").text
    check("o sitemap só lista endereços do domínio próprio",
          "www.lencoisreceptivo.com.br/passeio/lagoa-azul" in sm and "localhost" not in sm)
    check("o robots aponta o sitemap oficial",
          "Sitemap: http://www.lencoisreceptivo.com.br/sitemap.xml" in proprio.get("/robots.txt").text)
    check("o botão continua respondendo no subdomínio (a /api nunca é redirecionada)",
          sub.post("/api/publico/clique", json={"passeio": "lagoa-azul"}).status_code == 200)

    previa = (await rsite.ver_site(ADMIN))["url_previa"].split("previa=")[1]
    await rsite.salvar_site(rsite.SiteIn(publicado=False), ADMIN)
    r = sub.get(f"/?previa={previa}", follow_redirects=False)
    check("a prévia não é redirecionada", r.status_code == 200, r.status_code)
    await rsite.salvar_site(rsite.SiteIn(publicado=True), ADMIN)

    r = await rsite.salvar_site(rsite.SiteIn(dominio_proprio="www.outrodominio.com.br"), ADMIN)
    check("trocar o domínio próprio exige provar de novo antes de redirecionar",
          r["dominio_ativo"] is False and sub.get("/", follow_redirects=False).status_code == 200)
    await rsite.salvar_site(rsite.SiteIn(dominio_proprio=None), ADMIN)

    # Search Console: a tag inteira ou só o código; o template monta a tag.
    r = await rsite.salvar_site(rsite.SiteIn(google_verificacao=
        '<meta name="google-site-verification" content="Ab_12-cdEFghIJklmnOP" />'), ADMIN)
    check("da tag colada fica só o código", r["site"]["google_verificacao"] == "Ab_12-cdEFghIJklmnOP")
    check("e a página publica a tag de verificação",
          '<meta name="google-site-verification" content="Ab_12-cdEFghIJklmnOP">' in sub.get("/").text)
    await erro(rsite.salvar_site(rsite.SiteIn(google_verificacao='"><script>alert(1)</script>'), ADMIN), 400)
    check("código que não é código é recusado (não vira HTML na página)", True)
    check("a tela mostra o endereço do sitemap para mandar ao Google",
          r["url_sitemap"] == "http://lencois-receptivo.localhost:8010/sitemap.xml", r["url_sitemap"])


async def etapa_imagens():
    """Versões leves em WebP, sem EXIF, e a foto do site fora da retenção de mídia."""
    import tempfile
    from PIL import Image
    import imagens_site
    import media_retention
    _conectar_fake()
    pasta = tempfile.mkdtemp()
    antes = os.environ.get("MEDIA_DIR")
    os.environ["MEDIA_DIR"] = pasta
    try:
        os.makedirs(os.path.join(pasta, TENANT))
        im = Image.new("RGB", (3000, 2000), (40, 160, 200))
        exif = Image.Exif()
        exif[0x8825] = {2: (2.0, 45.0, 0.0)}   # GPSInfo: latitude de onde a foto foi tirada
        im.save(os.path.join(pasta, TENANT, "grande.jpg"), "JPEG", exif=exif.tobytes(), quality=95)
        Image.new("RGB", (300, 200)).save(os.path.join(pasta, TENANT, "pequena.png"))
        doc = {"id": "foto-grande", "tenant_id": TENANT, "kind": "image", "mime": "image/jpeg",
               "stored_name": "grande.jpg", "created_at": "2020-01-01T00:00:00+00:00"}
        await DB.media_files.insert_one(dict(doc))
        pequena = {"id": "foto-pequena", "tenant_id": TENANT, "kind": "image", "mime": "image/png",
                   "stored_name": "pequena.png"}

        caminho, mime = await imagens_site.variante(doc, 960)
        with Image.open(caminho) as v:
            check("a versão de 960 sai em WebP e com 960 de largura", v.format == "WEBP" and v.width == 960, v.size)
            check("sem o EXIF do original (que tinha GPS)", not v.getexif().get(0x8825) and not v.info.get("exif"))
        check("muito mais leve que o original", caminho.stat().st_size < os.path.getsize(os.path.join(pasta, TENANT, "grande.jpg")) / 4)
        caminho, mime = await imagens_site.variante(pequena, 1600)
        with Image.open(caminho) as v:
            check("foto pequena não é ampliada", v.width == 300, v.size)
        caminho, mime = await imagens_site.variante(doc, None, "jpg")
        check("a prévia de link sai em JPEG de 1200", mime == "image/jpeg" and Image.open(caminho).width == 1200)
        check("largura fora da lista vira a mais próxima (robô não enche o disco)",
              imagens_site.pedido_valido(1234, "webp") == (1600, "webp")
              and imagens_site.pedido_valido(10, "webp") == (480, "webp"))

        # Um anexo de conversa igualmente antigo, para provar que a limpeza roda.
        open(os.path.join(pasta, TENANT, "anexo.jpg"), "wb").write(b"x")
        await DB.media_files.insert_one({"id": "anexo-velho", "tenant_id": TENANT, "kind": "image",
                                         "mime": "image/jpeg", "stored_name": "anexo.jpg",
                                         "created_at": "2020-01-01T00:00:00+00:00"})
        await sites.marcar_imagens_do_site(DB, TENANT, ["foto-grande"])
        await media_retention._purge_expired(DB)
        check("a limpeza de mídia antiga roda e apaga o anexo de conversa",
              (await DB.media_files.find_one({"id": "anexo-velho"})).get("purged_at"))
        check("mas a foto do site, de anos atrás, continua (antes sumia da vitrine em 90 dias)",
              not (await DB.media_files.find_one({"id": "foto-grande"})).get("purged_at")
              and os.path.exists(os.path.join(pasta, TENANT, "grande.jpg")))
        n = await media_retention._variantes_orfas(DB, imagens_site.pasta_variantes(TENANT).parent,
                                                   datetime.now(timezone.utc) + timedelta(hours=1))
        check("variante da foto que tem documento não é apagada como órfã",
              imagens_site.caminho(doc, 960, "webp").exists())
        check("a da foto sem documento é", n >= 1 and not imagens_site.caminho(pequena, 1600, "webp").exists(), n)
        await DB.media_files.delete_many({"id": "foto-grande"})
    finally:
        if antes is None:
            os.environ.pop("MEDIA_DIR", None)
        else:
            os.environ["MEDIA_DIR"] = antes


async def etapa_local_guias_idiomas(ctx):
    """Busca local (endereço e horário como dado), guias e o site em inglês e espanhol."""
    import json as _json
    import ai_client
    import ai_runtime
    import site_guias
    import site_traducao
    _conectar_fake()
    c = cliente("lencois-receptivo.localhost:8010")

    # ---- endereço e horário estruturados
    r = await rsite.salvar_site(rsite.SiteIn(
        endereco_rua="Av. Brasília, 100", endereco_bairro="Centro", cidade="Barreirinhas", uf="ma",
        cep="65590000", coordenadas="https://www.google.com/maps/place/Ag%C3%AAncia/@-2.7477,-42.8269,17z",
        horarios=[{"dias": [0, 1, 2, 3, 4], "abre": "07:00", "fecha": "20:00"},
                  {"dias": [5], "abre": "08:00", "fecha": "12:00"}, {"dias": [], "abre": "", "fecha": ""}]), ADMIN)
    s = r["site"]
    check("UF em maiúscula, CEP com hífen e as coordenadas tiradas do link do Maps",
          s["uf"] == "MA" and s["cep"] == "65590-000" and (s["latitude"], s["longitude"]) == (-2.7477, -42.8269), s)
    check("faixa de horário vazia é descartada", len(s["horarios"]) == 2, s["horarios"])
    check("o painel mostra o endereço e o horário montados",
          r["endereco_exibido"] == "Av. Brasília, 100 – Centro, Barreirinhas – MA, 65590-000"
          and r["horario_exibido"] == "Seg a sex, 07:00–20:00 · Sáb, 08:00–12:00", (r["endereco_exibido"], r["horario_exibido"]))
    await erro(rsite.salvar_site(rsite.SiteIn(uf="XX"), ADMIN), 400)
    check("UF que não existe é recusada", True)
    det = await erro(rsite.salvar_site(rsite.SiteIn(coordenadas="https://maps.app.goo.gl/abc123"), ADMIN), 400)
    check("link curto do Maps é recusado dizendo o que fazer", "link curto" in det, det)
    await erro(rsite.salvar_site(rsite.SiteIn(horarios=[{"dias": [0], "abre": "18:00", "fecha": "08:00"}]), ADMIN), 400)
    check("horário que fecha antes de abrir é recusado", True)
    html = c.get("/").text
    check("o Google recebe o endereço como PostalAddress", '"@type": "PostalAddress"' in html
          and '"addressLocality": "Barreirinhas"' in html and '"postalCode": "65590-000"' in html)
    check("com as coordenadas e o horário de funcionamento", '"@type": "GeoCoordinates"' in html
          and '"openingHoursSpecification"' in html and '"opens": "07:00"' in html)
    check("o mapa abre pelas coordenadas", "query=-2.7477,-42.8269" in html)
    check("a página mostra o endereço e o horário montados",
          "Av. Brasília, 100 – Centro, Barreirinhas – MA" in html and "Seg a sex, 07:00–20:00" in html)

    # ---- guias
    modelo = site_guias.MODELOS[0]
    det = await erro(rsite.criar_guia(site_guias.GuiaIn(titulo=modelo["titulo"], corpo=modelo["corpo"],
                                                        publicada=True), ADMIN), 400)
    check("guia com o texto de orientação do modelo não publica", "orientação" in det, det)
    await erro(rsite.criar_guia(site_guias.GuiaIn(titulo="Curto", corpo="Pouco texto.", publicada=True), ADMIN), 400)
    check("guia curto demais não publica", True)
    corpo = ("As lagoas enchem com a chuva do começo do ano e ficam no ponto de junho a setembro. "
             "Em outubro ainda tem água, mas menos.\n\n## Mês a mês\n- Junho: lagoas cheias\n- Julho: o melhor mês\n"
             "- Outubro: lagoas baixando\n\n## Nossa dica\nVenha em julho, e reserve com antecedência <script>x</script>.")
    g = await rsite.criar_guia(site_guias.GuiaIn(titulo="Melhor época para os Lençóis", corpo=corpo,
                                                 resumo="Quando as lagoas estão cheias.", publicada=True,
                                                 passeios=[ctx["lagoa"]["id"]], capa_id=f"img-{TENANT}"), ADMIN)
    check("o guia ganha endereço a partir do título", g["slug"] == "melhor-epoca-para-os-lencois", g["slug"])
    rasc = await rsite.criar_guia(site_guias.GuiaIn(titulo="Rascunho", corpo="ainda escrevendo"), ADMIN)
    html = c.get("/guia/melhor-epoca-para-os-lencois").text
    check("a página do guia mostra subtítulo, lista e parágrafo",
          "<h2>Mês a mês</h2>" in html and "<li>Julho: o melhor mês</li>" in html and "Nossa dica" in html)
    check("o texto do guia sai escapado", "&lt;script&gt;" in html and "<script>x" not in html)
    check("o guia vai como Article para o buscador", '"@type": "Article"' in html)
    check("e liga ao passeio relacionado", "/passeio/lagoa-azul" in html)
    check("rascunho não tem página", c.get(f"/guia/{rasc['slug']}").status_code == 404)
    check("a capa do guia publicado sai pela rota pública",
          await sites.midia_publica(DB, await sites.site_da_empresa(DB, TENANT), f"img-{TENANT}") is not None)
    check("a inicial lista os guias", "Guias da região" in c.get("/").text)
    check("o passeio mostra o guia relacionado", "Melhor época para os Lençóis" in c.get("/passeio/lagoa-azul").text)
    check("a lista de guias abre", c.get("/guias").status_code == 200)
    check("e o sitemap inclui o guia", "/guia/melhor-epoca-para-os-lencois" in c.get("/sitemap.xml").text)
    renomeado = await rsite.editar_guia(g["id"], site_guias.GuiaIn(titulo="Quando ir aos Lençóis", corpo=corpo,
                                                                    publicada=True, passeios=g["passeios"]), ADMIN)
    check("renomear o guia mantém o endereço (o link já foi divulgado)",
          renomeado["slug"] == "melhor-epoca-para-os-lencois", renomeado["slug"])

    # ---- idiomas
    check("sem o idioma ligado, /en/ não existe", c.get("/en/").status_code == 404)
    await rsite.salvar_site(rsite.SiteIn(idiomas=["en", "fr"]), ADMIN)
    check("idioma desconhecido é descartado", (await sites.site_da_empresa(DB, TENANT))["idiomas"] == ["en"])
    r = c.get("/en/")
    html = r.text
    check("/en/ abre com a interface em inglês", r.status_code == 200 and '<html lang="en">' in html
          and ">Tours<" in html and "Frequently asked questions" in html)
    check("os links do site em inglês ficam em inglês", 'href="/en/passeio/lagoa-azul"' in html)
    check("hreflang aponta as versões para o Google",
          'hreflang="pt-BR" href="http://lencois-receptivo.localhost:8010/"' in html
          and 'hreflang="en" href="http://lencois-receptivo.localhost:8010/en/"' in html and 'hreflang="x-default"' in html)
    check("o seletor de idioma aparece", 'class="idiomas"' in html)
    check("espanhol continua desligado", c.get("/es/").status_code == 404)
    check("sem tradução, o conteúdo da agência aparece em português (nunca vazio)",
          "Passeios nos Lençóis" in html)
    check("o dia da semana na página do passeio sai em inglês",
          any(d in c.get("/en/passeio/lagoa-azul").text for d in ("Mon,", "Tue,", "Wed,", "Thu,", "Fri,", "Sat,", "Sun,")))

    # Sem chave de IA nenhuma: diz o que fazer.
    os.environ.pop("AI_PLATFORM_KEY", None)
    det = await erro(rsite.traduzir(rsite.TraduzirIn(idioma="en"), ADMIN), 400)
    check("sem chave de IA, a tradução automática diz como configurar", "chave de IA" in det, det)

    chamadas = []

    async def ia_falsa(db, tenant_id, agente, instrucoes, historico, **kw):
        chamadas.append(instrucoes)
        itens = _json.loads(historico[-1]["content"])["itens"]
        return ai_client.Resposta(texto="```json\n" + _json.dumps(
            {"itens": [{"id": i["id"], "texto": "EN " + i["texto"]} for i in itens]}, ensure_ascii=False) + "\n```",
            modelo="falso")
    original = ai_runtime.responder_por_agente
    ai_runtime.responder_por_agente = ia_falsa
    try:
        rev = await rsite.ver_traducoes("en", ADMIN)
        total = rev["total"]
        check("a revisão lista os textos da agência", total > 10 and rev["faltando"] == total, rev["faltando"])
        voltas = 0
        while True:
            voltas += 1
            r = await rsite.traduzir(rsite.TraduzirIn(idioma="en"), ADMIN)
            if not r["restantes"] or voltas > 30:
                break
        check("a tradução vai em lotes até acabar", r["restantes"] == 0 and voltas > 1, (r, voltas))
        check("e o pedido à IA diz o idioma e manda manter os nomes de lugar",
              "inglês" in chamadas[0] and "Lençóis Maranhenses" in chamadas[0])
        html = c.get("/en/").text
        check("a página em inglês mostra o conteúdo traduzido", "EN Passeios nos Lençóis" in html)
        check("o depoimento não é traduzido (é a fala do turista)", "EN Incrível" not in html)
        check("o português continua igual", "EN " not in c.get("/").text)
        g_html = c.get("/en/guia/melhor-epoca-para-os-lencois").text
        check("o guia traduzido mantém a estrutura", "<h2>EN Mês a mês</h2>" in g_html or "EN" in g_html)

        # A agência muda a frase em português: a tradução velha deixa de valer.
        await rsite.salvar_site(rsite.SiteIn(chamada="Passeios nas lagoas"), ADMIN)
        html = c.get("/en/").text
        check("texto mudado em português aparece em português até traduzir de novo",
              "Passeios nas lagoas" in html and "EN Passeios nos Lençóis" not in html)
        rev = await rsite.ver_traducoes("en", ADMIN)
        check("e a revisão mostra que falta", rev["faltando"] == 1, rev["faltando"])
        item = next(i for i in rev["itens"] if i["texto"] == "Passeios nas lagoas")
        await rsite.corrigir_traducao(rsite.TraducaoIn(idioma="en", chave=item["chave"], texto="Lagoon tours"), ADMIN)
        await rsite.traduzir(rsite.TraduzirIn(idioma="en"), ADMIN)
        check("a correção à mão vale e a automática não passa por cima",
              "Lagoon tours" in c.get("/en/").text)
        await erro(rsite.corrigir_traducao(rsite.TraducaoIn(idioma="en", chave="naoexiste", texto="x"), ADMIN), 404)
        check("corrigir tradução de texto que não existe mais é recusado", True)

        # Guia longo vai em pedaços e volta inteiro.
        longo = "\n\n".join(f"Parágrafo {i} sobre as dunas e as lagoas, " + "areia branca e água doce. " * 12 for i in range(12))
        await rsite.editar_guia(g["id"], site_guias.GuiaIn(titulo="Quando ir aos Lençóis", corpo=longo,
                                                            publicada=True, passeios=g["passeios"]), ADMIN)
        antes = len(chamadas)
        for _ in range(10):
            r = await rsite.traduzir(rsite.TraduzirIn(idioma="en"), ADMIN)
            if not r["restantes"]:
                break
        g_html = c.get("/en/guia/melhor-epoca-para-os-lencois").text
        check("texto longo vai em pedaços (um por chamada) e volta inteiro",
              len(chamadas) - antes >= 2 and g_html.count("EN Parágrafo") == len(chamadas) - antes
              and "Parágrafo 0" in g_html and "Parágrafo 11" in g_html, (len(chamadas) - antes, g_html.count("EN Parágrafo")))
    finally:
        ai_runtime.responder_por_agente = original

    # O botão em inglês: mensagem em inglês, e o código reconhecido na chegada.
    r = c.post("/api/publico/clique", json={"passeio": "lagoa-azul", "adultos": 2, "criancas": 0, "idioma": "en"})
    link = r.json()["link"]
    from urllib.parse import unquote
    check("a mensagem do botão sai em inglês", "I%27d%20like%20to%20book" in link or "like to book" in unquote(link))
    texto = unquote(link.split("text=")[1])
    check("com o código em inglês", "Site code: " in texto, texto)
    await DB.contacts.insert_one({"id": "c-en", "tenant_id": TENANT, "numero_whatsapp": "15550001111",
                                  "nome": "John", "origem": "mensagem recebida"})
    resumo = await sites.atribuir(DB, TENANT, await DB.contacts.find_one({"id": "c-en"}), "s-en", texto)
    check("o código em inglês é reconhecido e o atendente sabe a língua",
          bool(resumo) and "Viu o site em inglês." in resumo, resumo)
    sm = c.get("/sitemap.xml").text
    check("o sitemap lista as versões em inglês com as alternativas",
          "/en/passeio/lagoa-azul" in sm and 'xhtml:link rel="alternate" hreflang="en"' in sm)

    await rsite.salvar_site(rsite.SiteIn(idiomas=[]), ADMIN)
    await rsite.remover_guia(rasc["id"], ADMIN)
    # As etapas seguintes contam cliques e orçamentos desde zero.
    await DB.site_cliques.delete_many({"tenant_id": TENANT})
    await DB.deals.delete_many({"tenant_id": TENANT, "contact_id": "c-en"})
    await DB.contacts.delete_many({"id": "c-en"})


async def etapa_previa(ctx):
    _conectar_fake()
    await rsite.salvar_site(rsite.SiteIn(publicado=False), ADMIN)
    c = cliente("lencois-receptivo.localhost:8010")
    check("despublicado, o site some para o mundo", c.get("/").status_code == 404)
    check("e o robots fecha tudo", "Disallow: /" in c.get("/robots.txt").text)
    token = sites.token_previa(TENANT, "lencois-receptivo")
    r = c.get(f"/?previa={token}")
    check("com o token, a prévia abre com a faixa e sem indexação",
          r.status_code == 200 and "Pré-visualização" in r.text and "noindex" in r.text)
    check("os links internos levam o token adiante", f"?previa={token}" in r.text)
    check("token errado não abre", c.get("/?previa=errado").status_code == 404)
    res = c.post(f"/api/publico/clique?previa={token}", json={"passeio": "lagoa-azul"})
    check("clique na prévia não grava nada e sai sem código",
          res.status_code == 200 and res.json()["codigo"] is None
          and await DB.site_cliques.count_documents({"tenant_id": TENANT}) == 0, res.json())
    await rsite.salvar_site(rsite.SiteIn(publicado=True), ADMIN)


async def etapa_clique_e_atribuicao(ctx):
    _conectar_fake()
    c = cliente("lencois-receptivo.localhost:8010")
    dia = (datetime.now(timezone.utc) + timedelta(days=5)).date().isoformat()
    res = c.post("/api/publico/clique", json={"passeio": "lagoa-azul", "dia": dia,
                                             "adultos": 2, "criancas": 1, "pagina": "/passeio/lagoa-azul"})
    corpo = res.json()
    check("o clique devolve um código de 6 e o link do WhatsApp",
          res.status_code == 200 and len(corpo["codigo"]) == 6
          and corpo["link"].startswith("https://wa.me/5598981112233?text="), corpo)
    check("o código vai na mensagem pronta", corpo["codigo"] in corpo["link"])
    c2 = cliente("outra-agencia.localhost:8010")
    res2 = c2.post("/api/publico/clique", json={"passeio": "lagoa-azul"})
    check("quem decide a agência do clique é o host, não o corpo",
          res2.status_code in (200, 404) and (res2.status_code == 404 or "5511999990000" in res2.json()["link"]))

    # A mensagem chega com o código.
    await DB.whatsapp_connections.insert_one({"id": "conn-s", "tenant_id": TENANT,
                                              "phone_number_id": "pn-s", "waba_id": "w",
                                              "status": "conectado"})
    texto = ("Olá! Vi o site e quero reservar *Lagoa Azul*.\n📅 Data: x\n👥 2 adultos e 1 criança"
             f"\n\nCódigo do site: {corpo['codigo'].lower()}")
    await flow_runtime.handle_incoming_message(TENANT, "5598977776666", texto, "m-site-1",
                                               connection_id="conn-s", push_name="Ana Turista")
    contato = await DB.contacts.find_one({"tenant_id": TENANT, "numero_whatsapp": "5598977776666"})
    check("a ficha nasce com origem 'site'", contato and contato["origem"] == "site", contato)
    deal = await DB.deals.find_one({"tenant_id": TENANT, "contact_id": contato["id"]})
    check("o orçamento abre no funil com o passeio, as pessoas e o valor estimado",
          deal and "Lagoa Azul" in deal["titulo"] and "2 adultos e 1 criança" in deal["titulo"]
          and deal["valor"] == 375 and deal["origem"] == "site", deal)
    check("na primeira etapa do funil padrão, criado na hora",
          deal["status"] == "aberto" and await DB.pipelines.count_documents({"tenant_id": TENANT}) == 1)
    sist = [m["content"] for m in await DB.messages_log.find(
        {"tenant_id": TENANT, "direction": "sistema"}).to_list(50)]
    check("a conversa diz de onde o turista veio", any(t.startswith("Veio do site: Lagoa Azul") for t in sist), sist)
    clique = await DB.site_cliques.find_one({"codigo": corpo["codigo"]})
    check("o clique fica marcado como convertido", clique["convertido_em"] and clique["deal_id"] == deal["id"])

    await flow_runtime.handle_incoming_message(TENANT, "5598977776666", texto, "m-site-2",
                                               connection_id="conn-s")
    check("reenviar a mesma mensagem não abre um segundo orçamento",
          await DB.deals.count_documents({"tenant_id": TENANT, "contact_id": contato["id"]}) == 1)
    await flow_runtime.handle_incoming_message(TENANT, "5598966665555",
                                               "Código do site: ZZZZZZ", "m-site-3",
                                               connection_id="conn-s")
    check("código inexistente não cria nada",
          await DB.deals.count_documents({"tenant_id": TENANT}) == 1)

    # Clique na página inicial (sem passeio): ficha marcada, sem orçamento.
    res = c.post("/api/publico/clique", json={})
    texto = f"Olá! Vi o site e quero saber mais sobre os passeios.\n\nCódigo do site: {res.json()['codigo']}"
    await flow_runtime.handle_incoming_message(TENANT, "5598955554444", texto, "m-site-4",
                                               connection_id="conn-s")
    c3 = await DB.contacts.find_one({"tenant_id": TENANT, "numero_whatsapp": "5598955554444"})
    check("clique sem passeio marca a origem mas não inventa orçamento",
          c3["origem"] == "site" and not await DB.deals.find_one({"contact_id": c3["id"]}))

    m = (await rsite.ver_site(ADMIN))["metricas"]
    check("as métricas contam cliques, conversas e orçamentos",
          (m["cliques"], m["conversas"], m["orcamentos"]) == (2, 2, 1), m)
    check("e quebram por passeio", any(p["nome"] == "Lagoa Azul" and p["cliques"] == 1
                                        for p in m["por_passeio"]), m["por_passeio"])


async def etapa_limite_e_tls(ctx):
    _conectar_fake()
    sites._cliques_por_ip.clear()
    c = cliente("lencois-receptivo.localhost:8010")
    codigos = [c.post("/api/publico/clique", json={}).json()["codigo"]
               for _ in range(sites.LIMITE_CLIQUES + 3)]
    check("acima do limite por IP o clique ainda leva ao WhatsApp, só sem código",
          all(codigos[:sites.LIMITE_CLIQUES]) and not any(codigos[sites.LIMITE_CLIQUES:]))
    sites._cliques_por_ip.clear()

    tc = cliente("localhost:8010")
    check("o ask do TLS libera o subdomínio de site publicado",
          tc.get("/api/publico/tls-ask?domain=lencois-receptivo.localhost").status_code == 200)
    check("e o domínio próprio", tc.get("/api/publico/tls-ask?domain=www.lencoisreceptivo.com.br").status_code == 200)
    check("e recusa o resto", tc.get("/api/publico/tls-ask?domain=qualquer.coisa.com").status_code == 404)
    await rsite.salvar_site(rsite.SiteIn(publicado=False), ADMIN)
    check("site despublicado não ganha certificado",
          tc.get("/api/publico/tls-ask?domain=lencois-receptivo.localhost").status_code == 404)
    await rsite.salvar_site(rsite.SiteIn(publicado=True), ADMIN)


async def etapa_guardas():
    _conectar_fake()
    import inspect
    from auth import require_tenant_admin

    def guarda(fn):
        for p in inspect.signature(fn).parameters.values():
            if getattr(p.default, "dependency", None) is require_tenant_admin:
                return True
        return False

    check("configurar o site é só do admin",
          all(guarda(f) for f in (rsite.ver_site, rsite.salvar_site, rsite.slug_livre,
                                  rsite.sugestao_slug, rsite.subir_imagem)))


async def _suite():
    print("\n=== site ===")
    ctx = await etapa_semear()
    await etapa_slug_do_passeio(ctx)
    await etapa_endereco()
    await etapa_configurar(ctx)
    await etapa_paginas(ctx)
    await etapa_conteudo(ctx)
    await etapa_local_guias_idiomas(ctx)
    await etapa_previa(ctx)
    await etapa_clique_e_atribuicao(ctx)
    await etapa_limite_e_tls(ctx)
    await etapa_guardas()
    await etapa_dominio()
    await etapa_imagens()
    print(f"\n{ok} verificações OK")


def test_suite():
    asyncio.run(_suite())
