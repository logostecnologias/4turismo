"""Desempenho do site: visitas, cliques e o caminho do turista até a reserva.

O Google Analytics para no clique. Aqui as duas pontas estão no mesmo banco:
o site conta a visita e o clique, o código do botão vira conversa e orçamento
(`sites.atribuir`), e a reserva é do mesmo contato. Por isso o funil do painel
vai de "visitou" até "reservou", por origem.

Decisões:

  - **Sem cookie e sem IP gravado.** Visitante único é um hash de IP +
    navegador com um sal que muda todo dia (`_visitante`): conta a mesma pessoa
    uma vez por dia e não permite segui-la de um dia para o outro. A origem da
    visita fica no `sessionStorage` do navegador, que some ao fechar a aba.
  - **O host diz de qual agência é o evento**, como no clique do botão. Nada
    do corpo escolhe a empresa.
  - **Evento é métrica, não verdade.** O endpoint é público e dá para forjar
    visita; o limite por IP (`limite`) e o filtro de robô seguram o grosso. O
    que vale como dado confiável continua sendo a conversa que chegou com
    código e a reserva gravada.
  - **O cru expira em 90 dias** (TTL em `criado_dt`), a mesma janela do
    `site_cliques`. O painel lê no máximo esse período.
  - **O clique no WhatsApp não é evento**: ele já é gravado com código em
    `site_cliques`, que é de onde sai a conversão. Contar nos dois lugares
    abriria espaço para os números divergirem.
"""
import hashlib
import hmac
import re
import time
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone

import agenda
import site_guias
import site_i18n
import sites

# Janelas que a tela oferece; o cru não vive mais que a maior.
JANELAS = (7, 30, 90)
# Teto de eventos lidos por relatório. Acima disso, 413 dizendo o número, como
# nos outros relatórios: meia conta sem aviso é pior que recusar.
MAX_EVENTOS = 300_000

# Os cliques internos que o modelo do site marca (`data-4t`) e os que o script
# acha sozinho. Nome fora da lista é descartado: o endpoint é público, e sem
# lista fechada qualquer um criaria linhas novas no painel da agência.
ALVOS = {"ver_passeios", "passeio", "guia", "escolher_data", "faq", "avaliacoes", "mapa",
         "idioma", "instagram", "ligacao", "email", "link_externo"}

LIMITE_EVENTOS = 120
JANELA_LIMITE_S = 600
_eventos_por_ip: dict[str, deque] = {}

_ROBO = re.compile(r"bot|crawl|spider|slurp|facebookexternalhit|whatsapp|preview|headless|lighthouse"
                   r"|python|curl|wget|httpclient|monitor", re.I)
_RE_HOST = re.compile(r"^[a-z0-9.-]{1,253}$")


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def limite(ip: str) -> bool:
    """True se este IP ainda pode registrar evento agora."""
    agora = time.monotonic()
    fila = _eventos_por_ip.setdefault(ip or "?", deque())
    while fila and agora - fila[0] > JANELA_LIMITE_S:
        fila.popleft()
    if len(fila) >= LIMITE_EVENTOS:
        return False
    fila.append(agora)
    if len(_eventos_por_ip) > 10000:
        for chave in [k for k, f in _eventos_por_ip.items() if not f][:5000]:
            _eventos_por_ip.pop(chave, None)
    return True


def e_robo(ua: str) -> bool:
    return not ua or bool(_ROBO.search(ua))


def dispositivo(ua: str) -> str:
    if re.search(r"ipad|tablet", ua or "", re.I):
        return "tablet"
    if re.search(r"mobi|android|iphone", ua or "", re.I):
        return "celular"
    return "computador"


def _visitante(tenant_id: str, ip: str, ua: str) -> str:
    dia = datetime.now(timezone.utc).date().isoformat()
    msg = f"visitante:{dia}:{tenant_id}:{ip}:{ua}".encode()
    return hmac.new(sites._segredo(), msg, hashlib.sha256).hexdigest()[:16]


def _txt(v, n: int) -> str | None:
    v = str(v).strip() if v is not None else ""
    return v[:n] or None


# ---------------------------------------------------------------------------
# Origem
# ---------------------------------------------------------------------------

_PAGO = {"cpc", "ppc", "paid", "paid_social", "paidsocial", "ads", "ad", "anuncio", "anúncio", "display"}
_FONTES = {"instagram": "Instagram", "ig": "Instagram", "facebook": "Facebook", "fb": "Facebook",
           "google": "Google", "bing": "Bing", "tripadvisor": "TripAdvisor", "whatsapp": "WhatsApp",
           "youtube": "YouTube", "tiktok": "TikTok", "email": "E-mail"}
_REFERENCIAS = [(re.compile(r"(^|\.)instagram\.com$"), "Instagram"),
                (re.compile(r"(^|\.)(facebook\.com|fb\.com|fb\.me)$"), "Facebook"),
                (re.compile(r"(^|\.)google\.[a-z.]+$"), "Google"),
                (re.compile(r"(^|\.)bing\.com$"), "Bing"),
                (re.compile(r"(^|\.)tripadvisor\.[a-z.]+$"), "TripAdvisor"),
                (re.compile(r"(^|\.)(whatsapp\.com|wa\.me)$"), "WhatsApp"),
                (re.compile(r"(^|\.)(youtube\.com|youtu\.be)$"), "YouTube"),
                (re.compile(r"(^|\.)tiktok\.com$"), "TikTok"),
                (re.compile(r"(^|\.)(chatgpt\.com|openai\.com)$"), "ChatGPT"),
                (re.compile(r"(^|\.)perplexity\.ai$"), "Perplexity")]


def classificar_origem(origem: dict | None) -> tuple[str, str | None]:
    """(fonte, campanha) a partir do que o navegador viu na primeira página.

    `utm_source` ganha de tudo: é a agência (ou a L4T) dizendo de onde é o
    link. Depois o identificador de clique de anúncio, depois o domínio de
    quem indicou. Sem nada, "Direto".
    """
    o = origem or {}
    fonte_utm = (_txt(o.get("utm_source"), 40) or "").lower()
    meio = (_txt(o.get("utm_medium"), 40) or "").lower()
    campanha = _txt(o.get("utm_campaign"), 80)
    pago = meio in _PAGO
    if fonte_utm:
        nome = _FONTES.get(fonte_utm, fonte_utm.capitalize())
        return (f"{nome} (anúncio)" if pago else nome), campanha
    if o.get("gclid"):
        return "Google (anúncio)", campanha
    ref = (_txt(o.get("ref"), 253) or "").lower()
    nome = None
    if ref and _RE_HOST.match(ref):
        nome = next((n for r, n in _REFERENCIAS if r.search(ref)), None) or ref.removeprefix("www.")
    if o.get("fbclid") and not nome:
        nome = "Facebook"
    return (f"{nome} (anúncio)" if nome and pago else nome or "Direto"), campanha


def pagina_e_idioma(caminho: str | None) -> tuple[str, str]:
    """O caminho sem o prefixo de idioma, e o idioma: /en/passeio/x é a mesma
    página que /passeio/x, vista em inglês."""
    p = (str(caminho or "/").split("?")[0].split("#")[0] or "/")[:200]
    if not p.startswith("/"):
        p = "/" + p
    partes = p.split("/", 2)
    if len(partes) > 1 and partes[1] in site_i18n.EXTRAS:
        return "/" + (partes[2] if len(partes) > 2 else ""), partes[1]
    return p, site_i18n.PADRAO


# ---------------------------------------------------------------------------
# Gravação
# ---------------------------------------------------------------------------

async def registrar(db, site: dict, corpo: dict, *, ip: str, ua: str) -> bool:
    """Grava uma visita ou um clique interno. Devolve se gravou."""
    tipo = corpo.get("tipo")
    if tipo not in ("visita", "clique") or e_robo(ua):
        return False
    alvo = detalhe = None
    if tipo == "clique":
        alvo = corpo.get("alvo")
        if alvo not in ALVOS:
            return False
        detalhe = _txt(corpo.get("detalhe"), 80)
    if not limite(ip):
        return False
    pagina, idioma = pagina_e_idioma(corpo.get("pagina"))
    fonte, campanha = classificar_origem(corpo.get("origem") if isinstance(corpo.get("origem"), dict) else None)
    await db.site_eventos.insert_one({
        "id": str(uuid.uuid4()), "tenant_id": site["tenant_id"], "tipo": tipo,
        "alvo": alvo, "detalhe": detalhe, "pagina": pagina, "idioma": idioma,
        "fonte": fonte, "campanha": campanha, "dispositivo": dispositivo(ua),
        "visitante": _visitante(site["tenant_id"], ip, ua),
        "created_at": _now(),
        # Date nativo, para o TTL (mesmo caso do `site_cliques.criado_dt`).
        "criado_dt": datetime.now(timezone.utc),
    })
    return True


# ---------------------------------------------------------------------------
# O painel
# ---------------------------------------------------------------------------

class MuitoGrande(Exception):
    pass


async def _nomes_das_paginas(db, tenant_id: str) -> dict:
    nomes = {"/": "Página inicial", "/guias": "Guias"}
    async for p in db.services.find({"tenant_id": tenant_id, "site_slug": {"$nin": [None, ""]}},
                                    {"_id": 0, "nome": 1, "site_slug": 1}):
        nomes[f"/passeio/{p['site_slug']}"] = p["nome"]
    for g in await site_guias.publicados(db, tenant_id, com_rascunho=True):
        nomes[f"/guia/{g['slug']}"] = g["titulo"]
    return nomes


def _linha(d: dict, chave, **zeros) -> dict:
    return d.setdefault(chave, {"chave": chave, **zeros})


async def desempenho(db, tenant_id: str, dias: int = 30) -> dict:
    """O painel inteiro numa chamada: funil, série por dia, páginas, origens e cliques."""
    if dias not in JANELAS:
        dias = 30
    tz = agenda.fuso_de(await agenda.configuracao(db, tenant_id))
    agora = datetime.now(timezone.utc)
    hoje = agora.astimezone(tz).date()
    primeiro = hoje - timedelta(days=dias - 1)
    desde = datetime.combine(primeiro, datetime.min.time(), tz).astimezone(timezone.utc).isoformat()

    filtro = {"tenant_id": tenant_id, "created_at": {"$gte": desde}}
    quantos = await db.site_eventos.count_documents(filtro)
    if quantos > MAX_EVENTOS:
        raise MuitoGrande(f"O período tem {quantos} eventos, acima do limite de {MAX_EVENTOS}. "
                          "Escolha um período menor.")

    serie = {(primeiro + timedelta(days=i)).isoformat(): {"dia": (primeiro + timedelta(days=i)).isoformat(),
                                                          "visitas": 0, "visitantes": 0, "whatsapp": 0}
             for i in range(dias)}
    vistos_no_dia: set = set()
    paginas: dict = {}
    origens: dict = {}
    cliques: dict = {}
    idiomas: dict = {}
    aparelhos: dict = {}
    campanhas: dict = {}
    totais = {"visitas": 0, "visitantes": 0, "whatsapp": 0, "conversas": 0, "orcamentos": 0,
              "reservas": 0, "receita": 0.0, "cliques_internos": 0}
    ultimo = None

    async for e in db.site_eventos.find(filtro, {"_id": 0, "tipo": 1, "alvo": 1, "detalhe": 1, "pagina": 1,
                                                 "idioma": 1, "fonte": 1, "campanha": 1,
                                                 "dispositivo": 1, "visitante": 1, "created_at": 1}):
        ultimo = max(ultimo or "", e["created_at"])
        dia = agenda.local(e["created_at"], tz).date().isoformat()
        fonte = e.get("fonte") or "Direto"
        if e["tipo"] == "visita":
            totais["visitas"] += 1
            novo = (dia, e.get("visitante")) not in vistos_no_dia
            if novo:
                vistos_no_dia.add((dia, e.get("visitante")))
                totais["visitantes"] += 1
            if dia in serie:
                serie[dia]["visitas"] += 1
                serie[dia]["visitantes"] += int(novo)
            _linha(paginas, e.get("pagina") or "/", visitas=0, whatsapp=0)["visitas"] += 1
            o = _linha(origens, fonte, visitas=0, visitantes=0, whatsapp=0, conversas=0,
                       orcamentos=0, reservas=0, receita=0.0)
            o["visitas"] += 1
            o["visitantes"] += int(novo)
            _linha(idiomas, e.get("idioma") or "pt", visitas=0)["visitas"] += 1
            _linha(aparelhos, e.get("dispositivo") or "computador", visitas=0)["visitas"] += 1
            if e.get("campanha"):
                _linha(campanhas, (fonte, e["campanha"]), visitas=0, whatsapp=0, conversas=0)["visitas"] += 1
        else:
            totais["cliques_internos"] += 1
            c = _linha(cliques, e.get("alvo"), n=0, detalhes={})
            c["n"] += 1
            if e.get("detalhe"):
                c["detalhes"][e["detalhe"]] = c["detalhes"].get(e["detalhe"], 0) + 1

    # O clique no WhatsApp e o que veio dele: conversa, orçamento e reserva.
    primeiro_contato: dict = {}
    async for c in db.site_cliques.find(filtro, {"_id": 0, "pagina": 1, "fonte": 1, "campanha": 1,
                                                 "convertido_em": 1, "contact_id": 1, "deal_id": 1,
                                                 "created_at": 1}):
        dia = agenda.local(c["created_at"], tz).date().isoformat()
        fonte = c.get("fonte") or "Direto"
        totais["whatsapp"] += 1
        if dia in serie:
            serie[dia]["whatsapp"] += 1
        pagina, _ = pagina_e_idioma(c.get("pagina"))
        _linha(paginas, pagina, visitas=0, whatsapp=0)["whatsapp"] += 1
        o = _linha(origens, fonte, visitas=0, visitantes=0, whatsapp=0, conversas=0,
                   orcamentos=0, reservas=0, receita=0.0)
        o["whatsapp"] += 1
        camp = _linha(campanhas, (fonte, c["campanha"]), visitas=0, whatsapp=0, conversas=0) if c.get("campanha") else None
        if camp:
            camp["whatsapp"] += 1
        if c.get("convertido_em"):
            totais["conversas"] += 1
            o["conversas"] += 1
            if camp:
                camp["conversas"] += 1
            if c.get("deal_id"):
                totais["orcamentos"] += 1
                o["orcamentos"] += 1
            cid = c.get("contact_id")
            if cid and (cid not in primeiro_contato or c["convertido_em"] < primeiro_contato[cid][0]):
                primeiro_contato[cid] = (c["convertido_em"], fonte)

    # Reserva conta para o site quando é do contato que chegou pelo código e
    # foi feita depois de ele chegar. Cancelada não conta; a receita é a das
    # que ocupam lugar, pelo preço gravado na reserva.
    ids = list(primeiro_contato)
    for i in range(0, len(ids), 1000):
        async for r in db.appointments.find(
                {"tenant_id": tenant_id, "contact_id": {"$in": ids[i:i + 1000]}},
                {"_id": 0, "contact_id": 1, "created_at": 1, "status": 1, "preco": 1}):
            chegou, fonte = primeiro_contato[r["contact_id"]]
            if r.get("created_at", "") < chegou or r.get("status") not in agenda.OCUPAM:
                continue
            valor = float(r.get("preco") or 0)
            totais["reservas"] += 1
            totais["receita"] += valor
            o = origens.get(fonte)
            if o:
                o["reservas"] += 1
                o["receita"] += valor

    nomes = await _nomes_das_paginas(db, tenant_id)
    lista_paginas = sorted(({"pagina": k, "nome": nomes.get(k) or k, "visitas": v["visitas"],
                             "whatsapp": v["whatsapp"]} for k, v in paginas.items()),
                           key=lambda x: (-x["visitas"], -x["whatsapp"]))
    lista_origens = sorted(({"fonte": k, **{c: v for c, v in o.items() if c != "chave"}}
                            for k, o in origens.items()), key=lambda x: (-x["visitas"], -x["whatsapp"]))
    lista_cliques = sorted(({"alvo": k, "n": v["n"],
                             "detalhes": sorted(({"detalhe": nomes.get(f"/passeio/{d}") or nomes.get(f"/guia/{d}") or d,
                                                  "n": n} for d, n in v["detalhes"].items()),
                                                key=lambda x: -x["n"])[:10]}
                            for k, v in cliques.items()), key=lambda x: -x["n"])
    lista_campanhas = sorted(({"fonte": k[0], "campanha": k[1], "visitas": v["visitas"],
                               "whatsapp": v["whatsapp"], "conversas": v["conversas"]}
                              for k, v in campanhas.items()), key=lambda x: (-x["visitas"], -x["whatsapp"]))
    site = await sites.site_da_empresa(db, tenant_id)
    if ultimo is None:
        ultimo_doc = await db.site_eventos.find({"tenant_id": tenant_id}, {"_id": 0, "created_at": 1}) \
            .sort("created_at", -1).limit(1).to_list(1)
        ultimo = ultimo_doc[0]["created_at"] if ultimo_doc else None
    totais["receita"] = round(totais["receita"], 2)
    return {
        "dias": dias, "fuso": str(tz), "de": primeiro.isoformat(), "ate": hoje.isoformat(),
        "site": {"publicado": bool(site.get("publicado")), "url": sites.base_canonica(site),
                 "ultimo_evento": ultimo},
        "totais": totais,
        "serie": list(serie.values()),
        "paginas": lista_paginas[:50],
        "origens": lista_origens,
        "campanhas": lista_campanhas[:30],
        "cliques": lista_cliques,
        "idiomas": sorted(({"idioma": k, "visitas": v["visitas"]} for k, v in idiomas.items()),
                          key=lambda x: -x["visitas"]),
        "dispositivos": sorted(({"dispositivo": k, "visitas": v["visitas"]} for k, v in aparelhos.items()),
                               key=lambda x: -x["visitas"]),
    }
