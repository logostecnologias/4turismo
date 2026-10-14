"""O site público das agências: páginas, imagens, o clique do botão e o `ask` do TLS.

As páginas são HTML gerado aqui, não o React do painel: site de turismo vive de
busca no Google ("passeio lagoa azul barreirinhas"), e um SPA que só tem
conteúdo depois do JavaScript rodar é uma página vazia para o buscador e uma
espera a mais no 4G de quem está na praia.

Quem chega aqui: o middleware de `server.py` reescreve para `/_site/...` todo
pedido fora de `/api` cujo host não é o do painel (`sites.host_e_de_site`). O
host diz qual site é (`sites.site_por_host`): subdomínio do domínio base ou
domínio próprio da agência. Nada aqui usa sessão do painel: é público, e o
cookie do painel nem chega a este host.
"""
import json
import secrets
from datetime import datetime, timezone
from pathlib import Path
from urllib.parse import quote

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, PlainTextResponse, RedirectResponse, Response
from jinja2 import Environment, FileSystemLoader, select_autoescape
from pydantic import BaseModel, Field

import agenda
import imagens_site
import site_guias
import site_i18n
import site_traducao
import media_store
import saidas
import sites
from db import get_db

router = APIRouter(tags=["site_publico"])

_env = Environment(
    loader=FileSystemLoader(str(Path(__file__).resolve().parent.parent / "site_templates")),
    autoescape=select_autoescape(["html", "xml"]),
    trim_blocks=True, lstrip_blocks=True,
)

# Quantos dias à frente a página do passeio oferece no seletor de data. Duas
# semanas cobrem quem está planejando a viagem; mais que isso a lista vira
# rolagem, e quem vem daqui a um mês escreve a data na conversa.
DIAS_OFERECIDOS = 14


def _moeda(v) -> str:
    inteiro, centavos = f"{float(v):,.2f}".split(".")
    s = f"R$ {inteiro.replace(',', '.')}"
    return s if centavos == "00" else f"{s},{centavos}"


def _duracao(minutos) -> str | None:
    if not minutos:
        return None
    h, m = divmod(int(minutos), 60)
    if h and m:
        return f"{h}h{m:02d}"
    return f"{h}h" if h else f"{m} min"


def _base_url(request: Request) -> str:
    host = request.headers.get("host") or ""
    return f"{sites.esquema()}://{host}"


def _base(site: dict, request: Request) -> str:
    """O endereço oficial (canonical, sitemap, JSON-LD, og:image): sempre um só.

    Era o host do pedido, e com domínio próprio o mesmo site se declarava
    oficial nos dois endereços. Ver `sites.base_canonica`.
    """
    return sites.base_canonica(site) or _base_url(request)


async def _contexto(request: Request) -> dict:
    """Site, empresa, paleta e idioma deste pedido, ou 404 se não há site visível aqui."""
    db = get_db()
    site = await sites.site_por_host(db, request.headers.get("host") or "")
    previa = request.query_params.get("previa")
    if not site or not sites.visivel(site, previa):
        raise _Nao()
    tenant = await db.tenants.find_one({"id": site["tenant_id"]}, {"_id": 0}) or {}
    if (tenant.get("status") or "ativo") not in ("ativo", "active"):
        # Agência suspensa não tem vitrine: o botão levaria turista para um
        # atendimento que não existe.
        raise _Nao()
    # O idioma vem do prefixo do caminho (/en/..., /es/...), tirado pelo
    # middleware de `server.py`. Idioma que a agência não ligou não existe.
    idioma = site_i18n.valido(request.scope.get("site_idioma"))
    if idioma != site_i18n.PADRAO and idioma not in (site.get("idiomas") or []):
        raise _Nao()
    em_previa = not site.get("publicado")
    previa_ok = previa if sites.previa_valida(site, previa) else None

    host = request.headers.get("host") or ""
    esquema_pedido = (request.headers.get("x-forwarded-proto") or request.url.scheme or "").split(",")[0].strip()
    await sites.marcar_dominio_ativo(db, site, host, esquema_pedido)
    # Site publicado com domínio próprio já funcionando: o subdomínio passa a
    # mandar (301) para o mesmo caminho lá, idioma incluído. A prévia fica onde
    # está: é o dono olhando, não o Google.
    if (not em_previa and not previa_ok and site.get("dominio_ativo_em")
            and not sites.veio_pelo_dominio_proprio(site, host)):
        destino = sites.base_canonica(site) + (request.scope.get("site_caminho") or "/")
        if request.url.query:
            destino += "?" + request.url.query
        raise _Redirecionar(destino)
    tr = await site_traducao.carregar(db, site["tenant_id"], idioma)
    return {"db": db, "site": site, "st": tr.site(site), "tenant": tenant, "tr": tr,
            "paleta": sites.paleta_da_empresa(tenant), "previa": previa_ok, "em_previa": em_previa,
            "idioma": idioma, "t": site_i18n.T[idioma],
            "pref": "" if idioma == site_i18n.PADRAO else f"/{idioma}",
            "sufixo": f"?previa={previa_ok}" if previa_ok else ""}


class _Nao(Exception):
    pass


class _Redirecionar(Exception):
    def __init__(self, url: str):
        self.url = url


def _midia(mid: str, previa: str | None, w: int | None = None, f: str | None = None) -> str:
    q = [f"w={w}"] if w else []
    if f:
        q.append(f"f={f}")
    if previa:
        q.append(f"previa={previa}")
    return f"/midia/{mid}" + ("?" + "&".join(q) if q else "")


def _img(mid: str | None, previa: str | None, sizes: str, padrao: int = 960) -> dict | None:
    """`src` + `srcset` + `sizes`: o navegador baixa a largura que a tela usa."""
    if not mid:
        return None
    return {"src": _midia(mid, previa, padrao),
            "srcset": ", ".join(f"{_midia(mid, previa, w)} {w}w" for w in imagens_site.LARGURAS),
            "sizes": sizes}


def _cartao(p: dict, c: dict) -> dict:
    fotos = p.get("site_fotos") or []
    return {
        "nome": p["nome"],
        "url": f"{c['pref']}/passeio/{p.get('site_slug')}{c['sufixo']}",
        "resumo": p.get("site_resumo") or (p.get("descricao") or "")[:160] or None,
        "foto": _img(fotos[0] if fotos else None, c["previa"], "(max-width: 640px) 100vw, 360px", 480),
        "preco": _moeda(p["preco"]) if p.get("preco") is not None else None,
        "duracao": _duracao(p.get("duracao_min")),
        "quando": site_i18n.quando_sai(p.get("grade_saidas"), c["idioma"]),
    }


def _cartao_guia(g: dict, c: dict) -> dict:
    return {"titulo": g["titulo"], "resumo": g.get("resumo"),
            "url": f"{c['pref']}/guia/{g['slug']}{c['sufixo']}",
            "capa": _img(g.get("capa_id"), c["previa"], "(max-width: 640px) 100vw, 360px", 480),
            "minutos": site_guias.minutos_de_leitura(g.get("corpo"))}


def _html(template: str, status: int = 200, publico: bool = True, **ctx) -> HTMLResponse:
    nonce = secrets.token_urlsafe(16)
    corpo = _env.get_template(template).render(nonce=nonce, **ctx)
    return HTMLResponse(corpo, status_code=status, headers={
        "Content-Security-Policy": (
            "default-src 'self'; img-src 'self' data:; "
            "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com; "
            "font-src https://fonts.gstatic.com; "
            f"script-src 'nonce-{nonce}'; connect-src 'self'; "
            "frame-ancestors 'none'; base-uri 'none'; form-action 'self'"),
        "X-Content-Type-Options": "nosniff",
        "Referrer-Policy": "strict-origin-when-cross-origin",
        # Publicado: um minuto de cache, que segura rajada sem atrasar a
        # mudança de preço que a agência acabou de salvar. Prévia: nada.
        "Cache-Control": "public, max-age=60" if publico else "no-store",
        "Content-Language": site_i18n.HTML_LANG[ctx.get("idioma") or "pt"],
    })


def _jsonld(obj) -> str:
    # `</` escapado: um nome de passeio com "</script>" fecharia o bloco.
    return json.dumps(obj, ensure_ascii=False).replace("</", "<\\/")


# ---------------------------------------------------------------------------
# Blocos de conteúdo. Cada um devolve None/[] quando não há o que mostrar, e o
# template esconde a seção inteira: seção vazia é pior que nenhuma.
# ---------------------------------------------------------------------------

def _milhar(n: int) -> str:
    return f"{int(n):,}".replace(",", ".")


def _avaliacoes(site: dict) -> dict | None:
    nota = site.get("nota_avaliacoes")
    if not nota:
        return None
    total = site.get("total_avaliacoes")
    return {"nota": f"{float(nota):.1f}".replace(".", ","), "estrelas": round(float(nota)),
            "total": _milhar(total) if total else None,
            "fonte": site.get("fonte_avaliacoes") or "Google", "link": site.get("link_avaliacoes")}


def _confianca(site: dict) -> dict | None:
    desde = site.get("desde")
    anos = datetime.now(timezone.utc).year - desde if desde else None
    bloco = {"diferenciais": site.get("diferenciais") or [], "desde": desde,
             "anos": anos if anos and anos > 1 else None, "cadastur": site.get("cadastur")}
    return bloco if (bloco["diferenciais"] or desde or bloco["cadastur"]) else None


def _embarque(site: dict, config: dict, t: dict) -> list:
    """Os pontos de busca já cadastrados nas Saídas, sem a agência digitar de novo."""
    if site.get("mostrar_embarque") is False:
        return []
    saida = []
    for p in saidas.pontos_ativos(config):
        m = p.get("minutos_antes") or 0
        saida.append({"nome": p["nome"], "endereco": p.get("endereco"),
                      "quando": t["busca_antes"].format(n=m) if m else t["busca_hora"]})
    return saida


def _mapa(site: dict) -> str | None:
    """O link do mapa: pelas coordenadas, que não erram; sem elas, pelo endereço."""
    if site.get("latitude") is not None and site.get("longitude") is not None:
        return f"https://www.google.com/maps/search/?api=1&query={site['latitude']},{site['longitude']}"
    end = sites.endereco_exibido(site)
    return f"https://www.google.com/maps/search/?api=1&query={quote(end)}" if end else None


def _faq_jsonld(perguntas: list, idioma: str) -> dict | None:
    if not perguntas:
        return None
    return {"@context": "https://schema.org", "@type": "FAQPage", "inLanguage": site_i18n.HTML_LANG[idioma],
            "mainEntity": [{"@type": "Question", "name": q["pergunta"],
                            "acceptedAnswer": {"@type": "Answer", "text": q["resposta"]}} for q in perguntas]}


def _org(c: dict, nome: str, base: str, capa: str | None = None) -> dict:
    """A agência como dado: é o que o Google usa para a busca local."""
    site, st = c["site"], c["st"]
    org = {"@context": "https://schema.org", "@type": "TravelAgency", "name": nome,
           "url": base + (c["pref"] or "/")}
    if st.get("chamada"):
        org["description"] = st["chamada"]
    if site.get("whatsapp"):
        org["telephone"] = f"+{site['whatsapp']}"
    endereco = sites.endereco_schema(site)
    if endereco:
        org["address"] = endereco
    if site.get("latitude") is not None and site.get("longitude") is not None:
        org["geo"] = {"@type": "GeoCoordinates", "latitude": site["latitude"], "longitude": site["longitude"]}
    horario = sites.horario_schema(site)
    if horario:
        org["openingHoursSpecification"] = horario
    if site.get("logo_id"):
        org["logo"] = f"{base}{_midia(site['logo_id'], None, 480)}"
    if capa:
        org["image"] = f"{base}{_midia(capa, None, f='jpg')}"
    mesmo = [f"https://instagram.com/{site['instagram']}"] if site.get("instagram") else []
    if site.get("link_avaliacoes"):
        mesmo.append(site["link_avaliacoes"])
    if mesmo:
        org["sameAs"] = mesmo
    return org


def _capa(site: dict, passeios: list) -> str | None:
    """A capa da agência; sem ela, a primeira foto de passeio, que já é pública.

    Bloco de cor no topo foi o que mais fez o site parecer vazio: agência que
    subiu foto de passeio já tem uma imagem melhor que nenhuma.
    """
    if site.get("capa_id"):
        return site["capa_id"]
    return next((p["site_fotos"][0] for p in passeios if p.get("site_fotos")), None)


def _comum(c: dict, config: dict) -> dict:
    """O que todas as páginas mostram igual: avaliação, confiança, busca, pagamento."""
    st = c["st"]
    return {"avaliacoes": _avaliacoes(st), "confianca": _confianca(st),
            "embarque": _embarque(st, config, c["t"]), "mapa": _mapa(c["site"]),
            "pagamento": st.get("pagamento") or [], "cancelamento": st.get("cancelamento"),
            "horario": sites.horario_exibido(st, c["idioma"]), "endereco": sites.endereco_exibido(c["site"])}


def _pagina(c: dict, request: Request, caminho: str, nome: str) -> dict:
    """O que o `base.html` precisa em toda página: idioma, endereços e cabeçalho."""
    base = _base(c["site"], request)
    idiomas = [site_i18n.PADRAO] + [i for i in site_i18n.EXTRAS if i in (c["site"].get("idiomas") or [])]
    prefixo = lambda i: "" if i == site_i18n.PADRAO else f"/{i}"  # noqa: E731
    whats = c["site"].get("whatsapp")
    return {
        "paleta": c["paleta"], "site": c["st"], "nome": nome, "base": base, "em_previa": c["em_previa"],
        "sufixo": c["sufixo"], "pref": c["pref"], "t": c["t"], "idioma": c["idioma"],
        "html_lang": site_i18n.HTML_LANG[c["idioma"]], "og_locale": site_i18n.OG_LOCALE[c["idioma"]],
        "canonical": base + c["pref"] + caminho,
        # hreflang: as outras versões da mesma página, para o Google mostrar a
        # certa a quem busca em cada língua. Sem elas, as três competem entre si.
        "alternativas": ([{"hreflang": site_i18n.HTML_LANG[i], "href": base + prefixo(i) + caminho} for i in idiomas]
                         + [{"hreflang": "x-default", "href": base + caminho}]) if len(idiomas) > 1 else [],
        "menu_idiomas": [{"curto": site_i18n.CURTOS[i], "nome": site_i18n.NOMES[i], "ativo": i == c["idioma"],
                          "href": (prefixo(i) + caminho) + c["sufixo"], "lang": site_i18n.HTML_LANG[i]}
                         for i in idiomas] if len(idiomas) > 1 else [],
        "logo": _midia(c["site"]["logo_id"], c["previa"], 480) if c["site"].get("logo_id") else None,
        "whatsapp_link": sites.link_whatsapp(whats, sites.mensagem_whatsapp(None, None, None, None, c["idioma"])) if whats else None,
    }


async def _guias(c: dict) -> list:
    guias = await site_guias.publicados(c["db"], c["site"]["tenant_id"], com_rascunho=bool(c["previa"]))
    return [c["tr"].guia(g) for g in guias]


def _nao_encontrado(idioma: str = "pt") -> HTMLResponse:
    return _html("erro.html", status=404, publico=False, paleta=sites.PALETAS[sites.PALETA_PADRAO],
                 t=site_i18n.T[site_i18n.valido(idioma)], idioma=site_i18n.valido(idioma),
                 html_lang=site_i18n.HTML_LANG[site_i18n.valido(idioma)])


# ---------------------------------------------------------------------------
# Páginas
# ---------------------------------------------------------------------------

@router.get("/_site/", include_in_schema=False)
async def inicio(request: Request):
    try:
        c = await _contexto(request)
    except _Redirecionar as r:
        return RedirectResponse(r.url, status_code=301)
    except _Nao:
        return _nao_encontrado(request.scope.get("site_idioma"))
    db, site, st, t = c["db"], c["site"], c["st"], c["t"]
    passeios = [c["tr"].passeio(p) for p in await sites.passeios_do_site(db, site["tenant_id"])]
    nome = site.get("nome") or c["tenant"].get("name") or "Agência"
    config = await agenda.configuracao(db, site["tenant_id"])
    capa = _capa(site, passeios)
    faq = st.get("faq") or []
    guias = await _guias(c)
    pag = _pagina(c, request, "/", nome)
    descricao = st.get("chamada") or f"{t['passeios']} · {nome}"
    return _html(
        "inicio.html", publico=not c["em_previa"], **pag,
        titulo=f"{nome} · {descricao}" if st.get("chamada") else nome, descricao=descricao,
        passeios=[_cartao(p, c) for p in passeios],
        guias=[_cartao_guia(g, c) for g in guias[:6]], mais_guias=len(guias) > 6,
        capa=_img(capa, c["previa"], "100vw", 1600),
        og_imagem=f"{pag['base']}{_midia(capa, None, f='jpg')}" if capa else None,
        sobre=[p.strip() for p in (st.get("sobre") or "").split("\n\n") if p.strip()],
        depoimentos=st.get("depoimentos") or [], faq=faq, **_comum(c, config),
        jsonld=_jsonld([j for j in (_org(c, nome, pag["base"], capa), _faq_jsonld(faq, c["idioma"])) if j]),
    )


@router.get("/_site/passeio/{slug_passeio}", include_in_schema=False)
async def pagina_passeio(slug_passeio: str, request: Request):
    try:
        c = await _contexto(request)
    except _Redirecionar as r:
        return RedirectResponse(r.url, status_code=301)
    except _Nao:
        return _nao_encontrado(request.scope.get("site_idioma"))
    db, site, st, t = c["db"], c["site"], c["st"], c["t"]
    original = next((x for x in await sites.passeios_do_site(db, site["tenant_id"])
                     if x.get("site_slug") == slug_passeio), None)
    if not original:
        return _nao_encontrado(c["idioma"])
    p = c["tr"].passeio(original)
    nome = site.get("nome") or c["tenant"].get("name") or "Agência"
    config = await agenda.configuracao(db, site["tenant_id"])
    pag = _pagina(c, request, f"/passeio/{p['site_slug']}", nome)
    if site.get("whatsapp"):
        # O botão desta página já leva o passeio na mensagem.
        pag["whatsapp_link"] = sites.link_whatsapp(
            site["whatsapp"], sites.mensagem_whatsapp(original, None, None, None, c["idioma"]))
    base = pag["base"]

    # Os dias com saída aberta e vaga para pelo menos uma pessoa. A quantidade
    # de vagas **não** aparece, pela regra de sempre: é informação de dentro da
    # casa, e "restam 2" cria pressa falsa quando a agência tem outra van.
    dias = await saidas.proximos_dias(db, site["tenant_id"], passeio=original, pessoas=1,
                                      config=config, limite_dias=DIAS_OFERECIDOS)
    preco = p.get("preco")
    preco_cri = p.get("preco_crianca")
    legendas = p.get("site_legendas") or {}
    fotos = [{"img": _img(m, c["previa"], "(max-width: 900px) 100vw, 760px" if i == 0 else "(max-width: 900px) 25vw, 190px",
                          1600 if i == 0 else 480),
              "legenda": legendas.get(m)} for i, m in enumerate(p.get("site_fotos") or [])]
    descricao = p.get("site_resumo") or (p.get("descricao") or "")[:160] or f"{p['nome']} · {nome}"
    viagem = {"@context": "https://schema.org", "@type": "TouristTrip", "name": p["nome"],
              "description": descricao, "inLanguage": pag["html_lang"],
              "provider": {"@type": "TravelAgency", "name": nome, "url": base + (c["pref"] or "/")}}
    if p.get("site_fotos"):
        viagem["image"] = [f"{base}{_midia(m, None, 1600)}" for m in p["site_fotos"]]
    if preco is not None:
        viagem["offers"] = {"@type": "Offer", "price": f"{float(preco):.2f}", "priceCurrency": "BRL",
                            "url": pag["canonical"]}
    roteiro = p.get("site_roteiro") or []
    if roteiro:
        viagem["itinerary"] = {"@type": "ItemList", "itemListElement": [
            {"@type": "ListItem", "position": i + 1,
             "name": f"{r['hora']} {r['texto']}" if r.get("hora") else r["texto"]}
            for i, r in enumerate(roteiro)]}
    faq = p.get("site_faq") or []
    guias = [g for g in await _guias(c) if original["id"] in (g.get("passeios") or [])]
    return _html(
        "passeio.html", publico=not c["em_previa"], **pag,
        titulo=f"{p['nome']} · {nome}", descricao=descricao,
        og_imagem=f"{base}{_midia(p['site_fotos'][0], None, f='jpg')}" if p.get("site_fotos") else None,
        passeio={
            "nome": p["nome"], "slug": p["site_slug"], "fotos": fotos,
            "paragrafos": [x.strip() for x in (p.get("site_descricao") or p.get("descricao") or "").split("\n\n") if x.strip()],
            "inclui": p.get("site_inclui") or [], "levar": p.get("site_levar") or [],
            "duracao": _duracao(p.get("duracao_min")),
            "quando": site_i18n.quando_sai(p.get("grade_saidas"), c["idioma"]),
            "preco": _moeda(preco) if preco is not None else None,
            "preco_crianca": (None if preco_cri is None or preco is None or preco_cri == preco
                              else (t["gratis"] if not preco_cri else _moeda(preco_cri))),
            "roteiro": roteiro, "nivel": t["esforco"].get(p.get("site_nivel") or ""),
            "idade_minima": p.get("site_idade_minima"),
            "melhor_epoca": p.get("site_melhor_epoca"), "chuva": p.get("site_chuva"),
        },
        faq=faq, tem_faq_geral=bool(st.get("faq")), guias=[_cartao_guia(g, c) for g in guias],
        depoimentos=(st.get("depoimentos") or [])[:3], **_comum(c, config),
        dias=[{"dia": d["dia"], "rotulo": d["rotulo"] if c["idioma"] == "pt" else site_i18n.dia_rotulo(d["dia"], c["idioma"])}
              for d in dias],
        jsonld=_jsonld([j for j in (viagem, _faq_jsonld(faq, c["idioma"])) if j]),
    )


@router.get("/_site/guias", include_in_schema=False)
async def lista_guias(request: Request):
    try:
        c = await _contexto(request)
    except _Redirecionar as r:
        return RedirectResponse(r.url, status_code=301)
    except _Nao:
        return _nao_encontrado(request.scope.get("site_idioma"))
    guias = await _guias(c)
    if not guias:
        return _nao_encontrado(c["idioma"])
    nome = c["site"].get("nome") or c["tenant"].get("name") or "Agência"
    pag = _pagina(c, request, "/guias", nome)
    return _html("guias.html", publico=not c["em_previa"], **pag,
                 titulo=f"{c['t']['guias']} · {nome}", descricao=c["t"]["guias_sub"],
                 guias=[_cartao_guia(g, c) for g in guias], **_comum(c, {}))


@router.get("/_site/guia/{slug_guia}", include_in_schema=False)
async def pagina_guia(slug_guia: str, request: Request):
    try:
        c = await _contexto(request)
    except _Redirecionar as r:
        return RedirectResponse(r.url, status_code=301)
    except _Nao:
        return _nao_encontrado(request.scope.get("site_idioma"))
    guias = await _guias(c)
    g = next((x for x in guias if x["slug"] == slug_guia), None)
    if not g:
        return _nao_encontrado(c["idioma"])
    db, site = c["db"], c["site"]
    nome = site.get("nome") or c["tenant"].get("name") or "Agência"
    pag = _pagina(c, request, f"/guia/{g['slug']}", nome)
    base = pag["base"]
    passeios = [c["tr"].passeio(p) for p in await sites.passeios_do_site(db, site["tenant_id"])
                if p["id"] in (g.get("passeios") or [])]
    artigo = {"@context": "https://schema.org", "@type": "Article", "headline": g["titulo"],
              "inLanguage": pag["html_lang"], "mainEntityOfPage": pag["canonical"],
              "datePublished": (g.get("created_at") or "")[:10] or None,
              "dateModified": (g.get("updated_at") or "")[:10] or None,
              "author": {"@type": "TravelAgency", "name": nome, "url": base + (c["pref"] or "/")},
              "publisher": {"@type": "TravelAgency", "name": nome}}
    if g.get("resumo"):
        artigo["description"] = g["resumo"]
    if g.get("capa_id"):
        artigo["image"] = f"{base}{_midia(g['capa_id'], None, f='jpg')}"
    return _html(
        "guia.html", publico=not c["em_previa"], **pag,
        titulo=f"{g['titulo']} · {nome}", descricao=g.get("resumo") or g["titulo"],
        og_imagem=artigo.get("image"),
        guia={"titulo": g["titulo"], "resumo": g.get("resumo"), "blocos": site_guias.blocos(g.get("corpo")),
              "capa": _img(g.get("capa_id"), c["previa"], "(max-width: 900px) 100vw, 860px", 1600),
              "minutos": site_guias.minutos_de_leitura(g.get("corpo"))},
        passeios=[_cartao(p, c) for p in passeios],
        outros=[_cartao_guia(x, c) for x in guias if x["id"] != g["id"]][:3],
        jsonld=_jsonld({k: v for k, v in artigo.items() if v}),
    )


@router.get("/_site/midia/{media_id}", include_in_schema=False)
async def midia(media_id: str, request: Request):
    try:
        c = await _contexto(request)
    except _Redirecionar as r:
        return RedirectResponse(r.url, status_code=301)
    except _Nao:
        return Response(status_code=404)
    doc = await sites.midia_publica(c["db"], c["site"], media_id)
    if not doc or doc.get("purged_at"):
        return Response(status_code=404)
    try:
        w = int(request.query_params.get("w") or 0) or None
    except ValueError:
        w = None
    leve = await imagens_site.variante(doc, w, request.query_params.get("f"))
    if leve:
        caminho, mime = leve
    else:
        # Original que o Pillow não abre (formato raro): sai como veio, que é
        # melhor que imagem quebrada na vitrine.
        caminho, mime = media_store.file_path(doc), doc["mime"]
    if not caminho.exists():
        return Response(status_code=404)
    return FileResponse(caminho, media_type=mime, headers={
        "Cache-Control": "public, max-age=604800",
        "X-Content-Type-Options": "nosniff",
        "Content-Security-Policy": "sandbox",
    })


@router.get("/_site/robots.txt", include_in_schema=False)
async def robots(request: Request):
    try:
        c = await _contexto(request)
    except _Redirecionar as r:
        return RedirectResponse(r.url, status_code=301)
    except _Nao:
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    if not c["site"].get("publicado"):
        return PlainTextResponse("User-agent: *\nDisallow: /\n")
    return PlainTextResponse(f"User-agent: *\nAllow: /\nSitemap: {_base(c['site'], request)}/sitemap.xml\n")


@router.get("/_site/sitemap.xml", include_in_schema=False)
async def sitemap(request: Request):
    try:
        c = await _contexto(request)
    except _Redirecionar as r:
        return RedirectResponse(r.url, status_code=301)
    except _Nao:
        return Response(status_code=404)
    db, site = c["db"], c["site"]
    if not site.get("publicado"):
        return Response(status_code=404)
    base = _base(site, request)
    caminhos = [("/", (site.get("updated_at") or "")[:10])]
    for p in await sites.passeios_do_site(db, site["tenant_id"]):
        caminhos.append((f"/passeio/{p['site_slug']}", (p.get("updated_at") or "")[:10]))
    guias = await site_guias.publicados(db, site["tenant_id"])
    if guias:
        caminhos.append(("/guias", ""))
    for g in guias:
        caminhos.append((f"/guia/{g['slug']}", (g.get("updated_at") or "")[:10]))
    idiomas = [site_i18n.PADRAO] + [i for i in site_i18n.EXTRAS if i in (site.get("idiomas") or [])]
    prefixo = lambda i: "" if i == site_i18n.PADRAO else f"/{i}"  # noqa: E731
    urls = []
    for caminho, data in caminhos:
        alternativas = [{"hreflang": site_i18n.HTML_LANG[i], "href": base + prefixo(i) + caminho}
                        for i in idiomas] if len(idiomas) > 1 else []
        for i in idiomas:
            urls.append({"loc": base + prefixo(i) + caminho, "data": data, "alternativas": alternativas})
    corpo = _env.get_template("sitemap.xml").render(urls=urls)
    return Response(corpo, media_type="application/xml")


@router.get("/_site/{resto:path}", include_in_schema=False)
async def qualquer_outro(resto: str, request: Request):
    return _nao_encontrado(request.scope.get("site_idioma"))


# ---------------------------------------------------------------------------
# O clique do botão, e o `ask` do certificado
# ---------------------------------------------------------------------------

class CliqueIn(BaseModel):
    passeio: str | None = Field(default=None, max_length=80)  # site_slug
    dia: str | None = Field(default=None, max_length=10)
    adultos: int = Field(default=1, ge=0, le=saidas.MAX_PESSOAS)
    criancas: int = Field(default=0, ge=0, le=saidas.MAX_PESSOAS)
    pagina: str | None = Field(default=None, max_length=200)
    referencia: str | None = Field(default=None, max_length=300)
    idioma: str | None = Field(default=None, max_length=5)   # "en", "es"; vem do <html lang>


def _ip(request: Request) -> str:
    # Atrás do Caddy, o IP real vem no cabeçalho; sem proxy, é o do socket.
    xff = (request.headers.get("x-forwarded-for") or "").split(",")[0].strip()
    return xff or (request.client.host if request.client else "?")


@router.post("/api/publico/clique")
async def registrar_clique(body: CliqueIn, request: Request):
    """Gera o código e devolve o link do WhatsApp com a mensagem pronta.

    Chamado pelo script da própria página, no mesmo host do site: é o host que
    diz de qual agência é o clique, nunca um campo do corpo. Na prévia o link
    sai sem código e nada é gravado: a agência testando o botão não pode
    aparecer como clique nas métricas dela.
    """
    db = get_db()
    site = await sites.site_por_host(db, request.headers.get("host") or "")
    previa = request.query_params.get("previa")
    if not site or not site.get("whatsapp") or not sites.visivel(site, previa):
        raise HTTPException(status_code=404, detail="Site não encontrado")
    idioma = site_i18n.valido((body.idioma or "")[:2].lower())
    if idioma not in (site.get("idiomas") or []):
        idioma = site_i18n.PADRAO
    passeio = None
    if body.passeio:
        passeio = next((p for p in await sites.passeios_do_site(db, site["tenant_id"])
                        if p.get("site_slug") == body.passeio), None)
    if not site.get("publicado") or not sites.limite_de_cliques(_ip(request)):
        # Prévia ou excesso de cliques: o turista ainda chega ao WhatsApp, só
        # sem código. Recusar o clique perderia uma venda de verdade por causa
        # de um limite que existe para conter robô.
        return {"codigo": None, "link": sites.link_whatsapp(
            site["whatsapp"], sites.mensagem_whatsapp(passeio, None, None, None, idioma))}
    return await sites.registrar_clique(db, site, passeio=passeio, dia=body.dia,
                                        adultos=body.adultos, criancas=body.criancas,
                                        pagina=body.pagina, referencia=body.referencia, idioma=idioma)


@router.get("/api/publico/tls-ask")
async def tls_ask(domain: str):
    """O Caddy pergunta antes de emitir certificado sob demanda.

    Só responde 200 para endereço de site **publicado** (subdomínio ou domínio
    próprio). Sem esta porta, qualquer um apontaria um domínio qualquer para o
    servidor e o faria emitir certificados até esbarrar no limite da Let's
    Encrypt, que é por domínio registrado e deixaria os sites de verdade sem
    renovação.
    """
    db = get_db()
    site = await sites.site_por_host(db, domain)
    if site and site.get("publicado"):
        return PlainTextResponse("ok")
    return PlainTextResponse("não", status_code=404)
