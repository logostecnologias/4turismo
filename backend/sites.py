"""O site público de cada agência: endereço, aparência, e o caminho do clique até o orçamento.

O site não é o produto, é a porta de entrada (ver CLAUDE.md): o que importa é o
turista que vê o passeio, toca em "Quero esse passeio" e chega no WhatsApp da
agência já dizendo **qual** passeio, **que dia** e **quantas pessoas**. É por
isso que este módulo tem mais regra sobre o clique do que sobre a página.

Decisões:

  - **Endereço por subdomínio**: `{slug}.{SITE_DOMINIO}`. O domínio base é
    variável de ambiente porque o nome do produto ainda não existe; localmente é
    `localhost`, e `slug.localhost` já resolve para a própria máquina em todo
    navegador moderno. O slug é **global** (dois sites não podem ter o mesmo
    endereço) e tem lista de reservados: `api.dominio` ou `admin.dominio` nas
    mãos de um cliente seriam uma porta para phishing com a nossa marca.

  - **O clique vira um código curto, e o código vira ficha e orçamento.** Ao
    tocar no botão, a página pede um código ao servidor (`registrar_clique`), e
    a mensagem pronta do WhatsApp leva "Código do site: K7Q2XM". Quando essa
    mensagem chega, `atribuir` (chamado pela entrada única de mensagens,
    `flow_runtime.handle_incoming_message`) reconhece o código, marca a ficha
    com origem "site" e abre o orçamento já com passeio, data, pessoas e valor
    estimado. Sem o código, a agência teria uma conversa a mais sem saber de
    onde veio, e o relatório de "o site vende?" seria opinião.

  - **O código é guardado no servidor, não codificado na mensagem.** Um código
    que carregasse passeio e data no próprio texto seria forjável por quem
    digita, e longo. O registro (`site_cliques`) também é o que conta cliques
    que **não** viraram conversa, que é metade da métrica.

  - **Uma conversão por código.** O turista que reenvia a mesma mensagem não
    abre dois orçamentos: a atribuição é um `find_one_and_update` condicionado
    a `convertido_em` vazio.

  - **Nada aqui derruba a mensagem.** A atribuição é best-effort: falhar em
    abrir o orçamento é ruim, deixar a mensagem do turista sem resposta porque
    o orçamento falhou é pior.
"""
import hashlib
import hmac
import logging
import os
import re
import secrets
import time
import unicodedata
import uuid
from collections import deque
from datetime import datetime, timedelta, timezone
from urllib.parse import quote, urlparse

from fastapi import HTTPException
from pydantic import BaseModel, Field

logger = logging.getLogger("turismo.site")

SLUG_MIN, SLUG_MAX = 3, 40
RE_SLUG = re.compile(r"^[a-z0-9]+(?:-[a-z0-9]+)*$")

# Subdomínios que nenhum cliente pode ter. Os técnicos (api, app, admin, mail)
# por segurança; os genéricos (site, suporte, blog) porque um dia são nossos.
RESERVADOS = {
    "www", "api", "app", "admin", "painel", "dashboard", "mail", "email", "smtp",
    "ftp", "cdn", "static", "assets", "img", "media", "midia", "suporte", "ajuda",
    "help", "status", "blog", "site", "sites", "docs", "login", "conta", "contas",
    "pagamento", "pagamentos", "billing", "webhook", "webhooks", "dev", "teste",
    "test", "staging", "homolog", "localhost", "ns1", "ns2", "autoconfig",
}

MAX_CHAMADA = 90
MAX_SOBRE = 2000
MAX_RESUMO = 160
MAX_DESCRICAO = 4000
MAX_ITENS_LISTA = 20
MAX_FOTOS = 10
MAX_BYTES_IMAGEM = 5 * 1024 * 1024

# Alfabeto do código: sem 0/O, 1/I/L, para o turista conseguir ditar ou
# redigitar se o WhatsApp dele cortar a mensagem.
ALFABETO = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
TAMANHO_CODIGO = 6
# O código chega na mensagem no idioma em que o turista viu o site (ver
# `site_i18n.T[...]["zap_codigo"]`): "Código do site", "Site code", "Código del sitio".
RE_CODIGO = re.compile(r"(?:c[oó]digo\s+do\s+site|site\s+code|c[oó]digo\s+del\s+sitio)\s*:?\s*([A-Za-z0-9]{6})\b",
                       re.IGNORECASE)

# Teto de cliques por IP numa janela, no endpoint público. Sem ele, qualquer
# script enche `site_cliques` e as métricas da agência viram ruído.
LIMITE_CLIQUES = 30
JANELA_CLIQUES_S = 600
_cliques_por_ip: dict[str, deque] = {}


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Endereço
# ---------------------------------------------------------------------------

def dominio_base() -> str:
    """O domínio dos sites, com porta se houver (local: `localhost:8010`)."""
    return (os.environ.get("SITE_DOMINIO") or "localhost:8010").strip().lower().rstrip("/")


def _sem_porta(host: str) -> str:
    return (host or "").strip().lower().split(":")[0].rstrip(".")


def esquema() -> str:
    return (os.environ.get("SITE_ESQUEMA") or "http").strip().lower()


def hosts_do_app() -> set:
    """Hosts que são do painel e da API, nunca de um site.

    O backend só recebe `/` (fora de `/api`) quando alguém abre um site: o
    painel é servido pelo Caddy. Mesmo assim o host do app é excluído de
    propósito, para um GET perdido no endereço do painel não virar a página de
    erro de um site.
    """
    brutos = os.environ.get("APP_HOSTS") or "localhost,127.0.0.1"
    return {_sem_porta(h) for h in brutos.split(",") if h.strip()}


def url_do_site(slug: str | None) -> str | None:
    if not slug:
        return None
    return f"{esquema()}://{slug}.{dominio_base()}"


def host_e_de_site(host: str) -> bool:
    """Este host pode ser de um site? (Só a forma; quem existe é o banco que diz.)"""
    h = _sem_porta(host)
    base = _sem_porta(dominio_base())
    if not h or h in hosts_do_app() or h in (base, f"www.{base}"):
        return False
    return True


def slug_do_host(host: str) -> str | None:
    """`lencois.dominio.com` → `lencois`. Outro formato (domínio próprio) → None."""
    h = _sem_porta(host)
    base = _sem_porta(dominio_base())
    if h.endswith("." + base):
        sub = h[: -len(base) - 1]
        if "." not in sub and sub not in RESERVADOS:
            return sub
    return None


def slugify(texto: str, maximo: int = SLUG_MAX) -> str:
    t = unicodedata.normalize("NFKD", texto or "")
    t = "".join(c for c in t if not unicodedata.combining(c)).lower()
    t = re.sub(r"[^a-z0-9]+", "-", t).strip("-")
    t = re.sub(r"-{2,}", "-", t)
    if len(t) > maximo:
        # Corta entre palavras: "...-lencois-mar" no fim de um endereço parece erro.
        corte = t[:maximo + 1].rfind("-")
        t = t[:corte] if corte > maximo // 2 else t[:maximo]
    return t.strip("-")


def problema_do_slug(slug: str) -> str | None:
    """O motivo pelo qual o slug não serve, ou None. Não consulta o banco."""
    if not slug:
        return "Escolha um endereço"
    if not SLUG_MIN <= len(slug) <= SLUG_MAX:
        return f"O endereço precisa ter de {SLUG_MIN} a {SLUG_MAX} caracteres"
    if not RE_SLUG.match(slug):
        return "Use só letras minúsculas sem acento, números e hífen (sem hífen no começo ou no fim)"
    if slug in RESERVADOS:
        return "Esse endereço é reservado. Escolha outro"
    return None


async def slug_em_uso(db, slug: str, exceto_tenant: str = None) -> bool:
    doc = await db.sites.find_one({"slug": slug}, {"_id": 0, "tenant_id": 1})
    return bool(doc and doc.get("tenant_id") != exceto_tenant)


async def sugerir_slug(db, base: str, exceto_tenant: str = None) -> str:
    raiz = slugify(base) or "agencia"
    if len(raiz) < SLUG_MIN:
        raiz = f"{raiz}-turismo"
    if raiz in RESERVADOS:
        raiz = f"{raiz}-turismo"
    candidato = raiz
    n = 2
    while await slug_em_uso(db, candidato, exceto_tenant):
        sufixo = f"-{n}"
        candidato = raiz[: SLUG_MAX - len(sufixo)].rstrip("-") + sufixo
        n += 1
    return candidato


def normaliza_dominio(valor: str | None) -> str | None:
    """`https://www.X.com.br/` → `www.x.com.br`. Recusa o que não é host."""
    v = (valor or "").strip().lower()
    if not v:
        return None
    v = re.sub(r"^https?://", "", v).split("/")[0].split(":")[0].rstrip(".")
    if not re.match(r"^(?=.{4,253}$)([a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?\.)+[a-z]{2,63}$", v):
        raise HTTPException(status_code=400, detail="Domínio inválido. Ex.: www.suaagencia.com.br")
    if v == _sem_porta(dominio_base()) or v.endswith("." + _sem_porta(dominio_base())):
        raise HTTPException(status_code=400,
                            detail="Esse já é o endereço da plataforma: use o campo de endereço")
    return v


# ---------------------------------------------------------------------------
# Pré-visualização assinada
# ---------------------------------------------------------------------------
#
# Site não publicado não aparece para ninguém, mas a agência precisa vê-lo antes
# de publicar. O cookie de sessão do painel é do host do app e não chega ao
# subdomínio do site, então a prévia é um token na URL, assinado com o segredo
# do servidor e amarrado ao slug: trocar o slug invalida o link antigo.

def _segredo() -> bytes:
    return (os.environ.get("JWT_SECRET") or "dev").encode("utf-8")


def token_previa(tenant_id: str, slug: str) -> str:
    return hmac.new(_segredo(), f"previa:{tenant_id}:{slug}".encode(), hashlib.sha256).hexdigest()[:32]


def previa_valida(site: dict, token: str | None) -> bool:
    if not token or not site.get("slug"):
        return False
    return hmac.compare_digest(token_previa(site["tenant_id"], site["slug"]), token)


# ---------------------------------------------------------------------------
# Aparência: a paleta escolhida em Ajustes
# ---------------------------------------------------------------------------
#
# Os valores do tema claro de cada paleta, copiados do `frontend/src/index.css`
# (o site só tem o tema claro: é vitrine, e o turista não escolheu tema
# nenhum). Ficam aqui, e não lidos do CSS em tempo de execução, porque o backend
# em produção não tem o código do frontend ao lado. Ao mudar uma paleta lá,
# mudar aqui.

PALETA_PADRAO = "oficial"

# `tinta` é a marca usada como texto (link, "ver passeio"). Sem ela, vale `brand`.
# Na oficial de hoje é a própria lagoa funda; o campo existe porque a laranja
# anterior não passava como texto sobre branco.
PALETAS = {
    "oficial": {"bg": "#FBF7EF", "surface": "#FFFFFF", "surface2": "#FDFAF4", "line": "#EADFC9",
        "text": "#0B2E40", "text2": "#1F4556", "text3": "#4F6B78", "brand": "#0B6E8A",
        "brand_fg": "#FFFFFF", "brand_soft": "#E3F4F3", "tinta": "#0B6E8A",
        "titulo": "'Bricolage Grotesque', system-ui, sans-serif", "corpo": "'Inter', system-ui, sans-serif",
        "peso": "700", "tracking": "-0.02em",
        "fontes": "Bricolage+Grotesque:opsz,wght@12..96,600;12..96,700&family=Inter:wght@400;500;600;700"},
    "lagoa": {"bg": "#F7F5F0", "surface": "#FFFFFF", "surface2": "#FCFBF9", "line": "#E6E1D8",
        "text": "#152628", "text2": "#385357", "text3": "#527075", "brand": "#0D767F",
        "brand_fg": "#FFFFFF", "brand_soft": "#E9F5F7",
        "titulo": "'Plus Jakarta Sans', system-ui, sans-serif", "corpo": "'Plus Jakarta Sans', system-ui, sans-serif",
        "peso": "700", "tracking": "-0.02em",
        "fontes": "Plus+Jakarta+Sans:wght@400;500;600;700"},
    "duna": {"bg": "#F8F4EF", "surface": "#FFFFFF", "surface2": "#FCFBF9", "line": "#E8E1D7",
        "text": "#261F17", "text2": "#5A4C3F", "text3": "#756657", "brand": "#8C5A1E",
        "brand_fg": "#FFFFFF", "brand_soft": "#F7F0E9",
        "titulo": "'Bricolage Grotesque', system-ui, sans-serif", "corpo": "'Figtree', system-ui, sans-serif",
        "peso": "600", "tracking": "-0.02em",
        "fontes": "Bricolage+Grotesque:opsz,wght@12..96,500;12..96,600;12..96,700&family=Figtree:wght@400;500;600;700"},
    "sol": {"bg": "#F8F3EF", "surface": "#FFFFFF", "surface2": "#FCFAF9", "line": "#E8DFD6",
        "text": "#271A16", "text2": "#5F473F", "text3": "#765E56", "brand": "#BD3F0C",
        "brand_fg": "#FFFFFF", "brand_soft": "#F7EDE9",
        "titulo": "'Sora', system-ui, sans-serif", "corpo": "'DM Sans', system-ui, sans-serif",
        "peso": "600", "tracking": "-0.02em",
        "fontes": "Sora:wght@500;600;700&family=DM+Sans:opsz,wght@9..40,400;9..40,500;9..40,600;9..40,700"},
    "mangue": {"bg": "#F4F5F2", "surface": "#FFFFFF", "surface2": "#FAFBFA", "line": "#DFE2DC",
        "text": "#17261C", "text2": "#3C5544", "text3": "#54715D", "brand": "#2E6B3F",
        "brand_fg": "#FFFFFF", "brand_soft": "#EAF6ED",
        "titulo": "'Manrope', system-ui, sans-serif", "corpo": "'Manrope', system-ui, sans-serif",
        "peso": "700", "tracking": "-0.02em",
        "fontes": "Manrope:wght@400;500;600;700;800"},
    "azulejo": {"bg": "#F1F3F6", "surface": "#FFFFFF", "surface2": "#F9FAFB", "line": "#DADEE4",
        "text": "#131B2B", "text2": "#394865", "text3": "#4F5E7D", "brand": "#1F4E8C",
        "brand_fg": "#FFFFFF", "brand_soft": "#E9EFF7",
        "titulo": "'Lora', Georgia, serif", "corpo": "'Source Sans 3', system-ui, sans-serif",
        "peso": "600", "tracking": "-0.01em",
        "fontes": "Lora:wght@500;600;700&family=Source+Sans+3:wght@400;500;600;700"},
    "caju": {"bg": "#F7F3F1", "surface": "#FFFFFF", "surface2": "#FCFAF9", "line": "#E5DDD9",
        "text": "#25181A", "text2": "#5C4247", "text3": "#73595D", "brand": "#9B2335",
        "brand_fg": "#FFFFFF", "brand_soft": "#F7E9EB",
        "titulo": "'Fraunces', Georgia, serif", "corpo": "'Figtree', system-ui, sans-serif",
        "peso": "600", "tracking": "-0.01em",
        "fontes": "Fraunces:opsz,wght@9..144,500;9..144,600;9..144,700&family=Figtree:wght@400;500;600;700"},
}

def paleta_da_empresa(tenant: dict | None) -> dict:
    pid = ((tenant or {}).get("aparencia") or {}).get("paleta") or PALETA_PADRAO
    return PALETAS.get(pid) or PALETAS[PALETA_PADRAO]


# ---------------------------------------------------------------------------
# O documento do site
# ---------------------------------------------------------------------------

CAMPOS = ("slug", "publicado", "nome", "chamada", "sobre", "whatsapp", "instagram", "email",
          "endereco", "logo_id", "capa_id", "dominio_proprio",
          # Fase 3.1: confiança, avaliações, perguntas e informação prática.
          "cadastur", "desde", "diferenciais", "nota_avaliacoes", "total_avaliacoes",
          "link_avaliacoes", "fonte_avaliacoes", "depoimentos", "faq", "horario", "pagamento",
          "cancelamento", "mostrar_embarque",
          # SEO: o código de verificação do Google Search Console.
          "google_verificacao",
          # Busca local: endereço, coordenadas e horário como dado, não texto.
          "endereco_rua", "endereco_bairro", "cidade", "uf", "cep", "latitude", "longitude",
          "horarios",
          # Idiomas além do português que o site publica (`site_i18n.EXTRAS`).
          "idiomas")


async def site_da_empresa(db, tenant_id: str) -> dict:
    """O site da agência, com os padrões preenchidos (sem gravar nada)."""
    doc = await db.sites.find_one({"tenant_id": tenant_id}, {"_id": 0}) or {}
    base = {c: None for c in CAMPOS}
    base["publicado"] = False
    return {"tenant_id": tenant_id, **base, **{k: v for k, v in doc.items() if v is not None}}


# ---------------------------------------------------------------------------
# Um endereço oficial por site (SEO)
# ---------------------------------------------------------------------------
#
# Com domínio próprio, o mesmo site responde em dois endereços. Se cada um se
# declarar o oficial, o Google vê duas cópias do mesmo conteúdo disputando entre
# si. O oficial é o domínio próprio, **mas só depois de ele funcionar**: até o
# DNS da agência apontar para nós, redirecionar o subdomínio para lá derrubaria
# o site. "Funcionar" é chegar uma visita de verdade pelo domínio, por HTTPS
# (`dominio_ativo_em`, marcado em `marcar_dominio_ativo`). HTTPS porque é o que
# não se forja: um `Host:` falso numa requisição direta não passa pelo TLS do
# Caddy, que só emite certificado para o domínio cujo DNS aponta para nós.

def base_canonica(site: dict) -> str | None:
    """O endereço oficial do site: domínio próprio ativo, senão o subdomínio."""
    dom = site.get("dominio_proprio")
    if dom and site.get("dominio_ativo_em"):
        return f"{esquema()}://{dom}"
    return url_do_site(site.get("slug"))


def veio_pelo_dominio_proprio(site: dict, host: str) -> bool:
    return bool(site.get("dominio_proprio")) and _sem_porta(host) == site["dominio_proprio"]


async def marcar_dominio_ativo(db, site: dict, host: str, esquema_pedido: str) -> bool:
    """Registra que o domínio próprio já recebe visita (a partir daqui ele é o oficial)."""
    if site.get("dominio_ativo_em") or not veio_pelo_dominio_proprio(site, host):
        return False
    if (esquema_pedido or "").lower() != esquema():
        return False
    r = await db.sites.update_one(
        {"tenant_id": site["tenant_id"], "dominio_proprio": site["dominio_proprio"],
         "dominio_ativo_em": None},
        {"$set": {"dominio_ativo_em": datetime.now(timezone.utc).isoformat()}})
    if r.modified_count:
        site["dominio_ativo_em"] = True
    return bool(r.modified_count)


_META_GOOGLE = re.compile(r"content\s*=\s*[\"']([^\"']+)[\"']", re.I)
_TOKEN_GOOGLE = re.compile(r"^[A-Za-z0-9_-]{10,100}$")


def normaliza_verificacao_google(valor) -> str | None:
    """O código de verificação do Search Console.

    A agência cola o que o Google mostra: a tag inteira
    (`<meta name="google-site-verification" content="abc…" />`) ou só o
    código. Guarda-se só o código, conferido, e o template monta a tag: HTML
    colado e impresso na página seria a porta para script no site.
    """
    v = (valor or "").strip()
    if not v:
        return None
    m = _META_GOOGLE.search(v)
    token = (m.group(1) if m else v).strip()
    if not _TOKEN_GOOGLE.match(token):
        raise HTTPException(status_code=400,
                            detail="Código de verificação do Google inválido. Cole a tag inteira que o Search Console mostra")
    return token


def visivel(site: dict, previa: str = None) -> bool:
    return bool(site.get("publicado")) or previa_valida(site, previa)


async def site_por_host(db, host: str) -> dict | None:
    """O site deste endereço: subdomínio do domínio base ou domínio próprio."""
    slug = slug_do_host(host)
    if slug:
        return await db.sites.find_one({"slug": slug}, {"_id": 0})
    h = _sem_porta(host)
    if not h:
        return None
    return await db.sites.find_one({"dominio_proprio": h}, {"_id": 0})


SEM_SLUG = [{"site_slug": {"$exists": False}}, {"site_slug": None}, {"site_slug": ""}]


async def garantir_slugs(db, tenant_id: str | None = None) -> int:
    """Dá o endereço da página (`site_slug`) a todo passeio que ainda não tem.

    O slug nasce quando o passeio é criado ou salvo pela tela. Passeio cadastrado
    antes do site existir (ou por outro caminho) ficava sem, e o site mostrava
    o link `/passeio/None` e o `sitemap.xml` caía com 500. Roda nas migrações da
    subida para todas as agências e, por garantia, em `passeios_do_site`. Só
    grava onde o campo continua vazio: nunca troca um endereço já divulgado.
    """
    filtro = {"$or": SEM_SLUG}
    if tenant_id:
        filtro["tenant_id"] = tenant_id
    n = 0
    for s in await db.services.find(filtro, {"_id": 0, "id": 1, "tenant_id": 1, "nome": 1}).to_list(5000):
        slug = await slug_de_passeio(db, s["tenant_id"], None, s.get("nome") or "", exceto_id=s["id"])
        r = await db.services.update_one(
            {"tenant_id": s["tenant_id"], "id": s["id"], "$or": SEM_SLUG}, {"$set": {"site_slug": slug}})
        n += r.modified_count
    return n


async def passeios_do_site(db, tenant_id: str) -> list:
    async def ler():
        return [p for p in await db.services.find(
            {"tenant_id": tenant_id, "ativo": True}, {"_id": 0}
        ).sort([("ordem", 1), ("nome", 1)]).to_list(200) if p.get("site_visivel", True)]

    passeios = await ler()
    if any(not p.get("site_slug") for p in passeios):
        await garantir_slugs(db, tenant_id)
        passeios = await ler()
    # Passeio sem endereço nunca vai para a página: o link sairia quebrado.
    return [p for p in passeios if p.get("site_slug")]


async def prontidao(db, site: dict) -> dict:
    """O que falta para o site poder ser publicado.

    Slug e WhatsApp são obrigatórios: sem slug não há endereço, e sem WhatsApp o
    botão "Quero esse passeio" não leva a lugar nenhum, que é o único motivo de
    o site existir. Pelo menos um passeio visível, senão a página é uma vitrine
    vazia com o nome da agência.
    """
    passeios = await passeios_do_site(db, site["tenant_id"])
    faltando = []
    if not site.get("slug"):
        faltando.append("Escolha o endereço do site")
    if not site.get("whatsapp"):
        faltando.append("Cadastre o WhatsApp do botão")
    if not passeios:
        faltando.append("Deixe pelo menos um passeio ativo visível no site")
    return {"slug": bool(site.get("slug")), "whatsapp": bool(site.get("whatsapp")),
            "passeios": len(passeios), "publicavel": not faltando, "faltando": faltando}


def validar_lista(itens, rotulo: str, maximo: int = MAX_ITENS_LISTA, tamanho: int = 120) -> list:
    saida = []
    for i in itens or []:
        t = (str(i) if i is not None else "").strip()
        if t:
            if len(t) > tamanho:
                raise HTTPException(status_code=400, detail=f"{rotulo}: cada item até {tamanho} caracteres")
            saida.append(t)
    if len(saida) > maximo:
        raise HTTPException(status_code=400, detail=f"{rotulo}: no máximo {maximo} itens")
    return saida


# ---------------------------------------------------------------------------
# Conteúdo que vende (fase 3.1): confiança, avaliações, perguntas, roteiro
# ---------------------------------------------------------------------------
#
# Toda seção destas **some quando está vazia**: o site é um modelo que cada
# agência preenche, e uma seção "Depoimentos" sem depoimento nenhum é pior que
# seção nenhuma. Por isso nada aqui é obrigatório, e o que vem vazio é
# descartado na gravação em vez de guardado como item em branco.

MAX_DIFERENCIAIS = 6
MAX_PAGAMENTO = 8
MAX_DEPOIMENTOS = 12
MAX_PERGUNTAS_SITE = 20
MAX_PERGUNTAS_PASSEIO = 10
MAX_ROTEIRO = 15
MAX_CANCELAMENTO = 800
NIVEIS = {"leve": "Esforço leve", "moderado": "Esforço moderado", "intenso": "Esforço intenso"}
_HORA = re.compile(r"^([01]\d|2[0-3]):[0-5]\d$")


class PerguntaIn(BaseModel):
    pergunta: str = Field(default="", max_length=150)
    resposta: str = Field(default="", max_length=800)


class DepoimentoIn(BaseModel):
    nome: str = Field(default="", max_length=60)
    origem: str | None = Field(default=None, max_length=60)   # "São Paulo, SP"
    texto: str = Field(default="", max_length=400)
    nota: int | None = Field(default=None, ge=1, le=5)


class PassoRoteiroIn(BaseModel):
    hora: str | None = Field(default=None, max_length=5)      # "07:30", opcional
    texto: str = Field(default="", max_length=160)


def _txt(v) -> str:
    return (v or "").strip()


def validar_perguntas(itens, maximo: int) -> list:
    saida = []
    for i in itens or []:
        p, r = _txt(i.get("pergunta")), _txt(i.get("resposta"))
        if not p and not r:
            continue
        if not p or not r:
            raise HTTPException(status_code=400,
                                detail="Cada pergunta frequente precisa da pergunta e da resposta")
        saida.append({"pergunta": p, "resposta": r})
    if len(saida) > maximo:
        raise HTTPException(status_code=400, detail=f"No máximo {maximo} perguntas frequentes")
    return saida


def validar_depoimentos(itens) -> list:
    saida = []
    for i in itens or []:
        nome, texto = _txt(i.get("nome")), _txt(i.get("texto"))
        if not nome and not texto:
            continue
        if not nome or not texto:
            raise HTTPException(status_code=400, detail="Cada depoimento precisa do nome e do texto")
        saida.append({"nome": nome, "origem": _txt(i.get("origem")) or None, "texto": texto,
                      "nota": i.get("nota")})
    if len(saida) > MAX_DEPOIMENTOS:
        raise HTTPException(status_code=400, detail=f"No máximo {MAX_DEPOIMENTOS} depoimentos")
    return saida


def validar_roteiro(itens) -> list:
    saida = []
    for i in itens or []:
        hora, texto = _txt(i.get("hora")), _txt(i.get("texto"))
        if not hora and not texto:
            continue
        if not texto:
            raise HTTPException(status_code=400, detail="Cada passo do roteiro precisa do que acontece")
        if hora and not _HORA.match(hora):
            raise HTTPException(status_code=400, detail=f"Roteiro: hora inválida \"{hora}\" (use 07:30)")
        saida.append({"hora": hora or None, "texto": texto})
    if len(saida) > MAX_ROTEIRO:
        raise HTTPException(status_code=400, detail=f"No máximo {MAX_ROTEIRO} passos no roteiro")
    return saida


def validar_url_publica(url, rotulo: str) -> str | None:
    """Link que vai para a página pública: só https, com host.

    É o link "Ver todas as avaliações". Sem a conferência, `javascript:...`
    colado no campo viraria um clique que roda script no domínio do site.
    """
    u = _txt(url)
    if not u:
        return None
    if len(u) > 300:
        raise HTTPException(status_code=400, detail=f"{rotulo}: link muito longo")
    p = urlparse(u)
    if p.scheme != "https" or not p.netloc or any(c in u for c in " <>\"'"):
        raise HTTPException(status_code=400, detail=f"{rotulo}: use um link que comece com https://")
    return u


# ---------------------------------------------------------------------------
# Endereço e horário estruturados (busca local)
# ---------------------------------------------------------------------------
#
# O Google entende "agência de passeio em Barreirinhas" pelo endereço e pelo
# horário em formato de dado (PostalAddress, openingHoursSpecification), não
# pelo texto solto. Os campos de texto (`endereco`, `horario`) continuam
# valendo para quem não preencheu os estruturados; quem preencheu tem o texto
# montado a partir deles.

UFS = {"AC", "AL", "AP", "AM", "BA", "CE", "DF", "ES", "GO", "MA", "MT", "MS", "MG", "PA", "PB",
       "PR", "PE", "PI", "RJ", "RN", "RS", "RO", "RR", "SC", "SP", "SE", "TO"}
MAX_HORARIOS = 7
_NUM = r"(-?\d{1,3}\.\d+)"
_COORD_PADROES = [
    re.compile(r"@" + _NUM + "," + _NUM),                 # .../@-2.7477,-42.8269,15z
    re.compile(r"!3d" + _NUM + r"!4d" + _NUM),             # .../data=!3d-2.74!4d-42.82
    re.compile(r"[?&](?:q|query|ll)=" + _NUM + r"(?:,|%2C)" + _NUM),
    re.compile(r"^\s*" + _NUM + r"\s*[,; ]\s*" + _NUM + r"\s*$"),   # "-2.7477, -42.8269"
]


def ler_coordenadas(valor) -> tuple[float, float] | None:
    """Latitude e longitude de um link do Google Maps ou de "lat, lng" colado.

    A agência não sabe as coordenadas, mas sabe abrir o mapa e copiar o link:
    é por isso que o campo aceita o link. O link curto (maps.app.goo.gl) não
    traz as coordenadas no texto, e abri-lo daqui seria o servidor indo buscar
    um endereço que alguém digitou.
    """
    v = (valor or "").strip()
    if not v:
        return None
    if "goo.gl" in v or "maps.app" in v:
        raise HTTPException(status_code=400, detail=(
            "Esse é o link curto do Google Maps, que não traz a localização. Abra o link no navegador "
            "e copie o endereço completo da barra, ou cole as coordenadas (ex.: -2.7477, -42.8269)"))
    for padrao in _COORD_PADROES:
        m = padrao.search(v)
        if m:
            lat, lng = float(m.group(1)), float(m.group(2))
            if -90 <= lat <= 90 and -180 <= lng <= 180 and (lat, lng) != (0.0, 0.0):
                return round(lat, 6), round(lng, 6)
    raise HTTPException(status_code=400, detail=(
        "Não encontrei a localização. Cole o link do Google Maps (o endereço completo) "
        "ou as coordenadas, como -2.7477, -42.8269"))


def normaliza_cep(valor) -> str | None:
    d = re.sub(r"\D", "", valor or "")
    if not d:
        return None
    if len(d) != 8:
        raise HTTPException(status_code=400, detail="CEP com 8 números, como 65590-000")
    return f"{d[:5]}-{d[5:]}"


class HorarioIn(BaseModel):
    dias: list[int] = Field(default_factory=list)          # 0 = segunda … 6 = domingo
    abre: str = Field(default="", max_length=5)
    fecha: str = Field(default="", max_length=5)


def validar_horarios(itens) -> list:
    saida = []
    for i in itens or []:
        dias = sorted({int(d) for d in i.get("dias") or [] if 0 <= int(d) <= 6})
        abre, fecha = _txt(i.get("abre")), _txt(i.get("fecha"))
        if not dias and not abre and not fecha:
            continue
        if not dias:
            raise HTTPException(status_code=400, detail="Escolha os dias de cada horário de atendimento")
        if not (_HORA.match(abre) and _HORA.match(fecha)):
            raise HTTPException(status_code=400, detail="Horário de atendimento: use 07:00 e 20:00")
        if fecha <= abre:
            raise HTTPException(status_code=400, detail=f"Horário de atendimento: {fecha} é antes de {abre}")
        saida.append({"dias": dias, "abre": abre, "fecha": fecha})
    if len(saida) > MAX_HORARIOS:
        raise HTTPException(status_code=400, detail=f"No máximo {MAX_HORARIOS} faixas de horário")
    return saida


def endereco_exibido(site: dict) -> str | None:
    """"Av. Brasília, 100 – Centro, Barreirinhas – MA, 65590-000", ou o texto antigo."""
    rua, bairro, cidade, uf, cep = (site.get(k) for k in ("endereco_rua", "endereco_bairro", "cidade", "uf", "cep"))
    if not (rua or cidade):
        return site.get("endereco")
    partes = [p for p in (rua, bairro) if p]
    texto = " – ".join(partes)
    lugar = " – ".join(p for p in (cidade, uf) if p)
    if lugar:
        texto = f"{texto}, {lugar}" if texto else lugar
    return f"{texto}, {cep}" if cep else texto


def horario_exibido(site: dict, idioma: str = "pt") -> str | None:
    """"Seg a sex, 07:00–20:00 · Sáb, 08:00–12:00", ou o texto antigo."""
    import site_i18n
    faixas = site.get("horarios") or []
    if not faixas:
        return site.get("horario")
    nomes = site_i18n.DIAS_CURTOS[site_i18n.valido(idioma)]
    t = site_i18n.T[site_i18n.valido(idioma)]
    textos = []
    for f in faixas:
        d = f["dias"]
        if d == list(range(7)):
            dias = t["todos_os_dias"]
        elif len(d) > 2 and d == list(range(d[0], d[-1] + 1)):
            dias = f"{nomes[d[0]]} {t['a']} {nomes[d[-1]]}"
        else:
            dias = ", ".join(nomes[i] for i in d)
        textos.append(f"{dias[:1].upper()}{dias[1:]}, {f['abre']}–{f['fecha']}")
    return " · ".join(textos)


_DIAS_SCHEMA = ("Monday", "Tuesday", "Wednesday", "Thursday", "Friday", "Saturday", "Sunday")


def endereco_schema(site: dict) -> dict | str | None:
    if site.get("endereco_rua") or site.get("cidade"):
        a = {"@type": "PostalAddress", "addressCountry": "BR"}
        for campo, chave in (("endereco_rua", "streetAddress"), ("cidade", "addressLocality"),
                             ("uf", "addressRegion"), ("cep", "postalCode")):
            if site.get(campo):
                a[chave] = site[campo]
        return a
    return site.get("endereco")


def horario_schema(site: dict) -> list:
    return [{"@type": "OpeningHoursSpecification", "dayOfWeek": [_DIAS_SCHEMA[d] for d in f["dias"]],
             "opens": f["abre"], "closes": f["fecha"]} for f in site.get("horarios") or []]


def validar_idiomas(itens) -> list:
    import site_i18n
    return [i for i in site_i18n.EXTRAS if i in (itens or [])]


def validar_legendas(legendas: dict | None, fotos: list) -> dict:
    """Legenda por foto, só das fotos que o passeio tem (até 120 caracteres)."""
    saida = {}
    for mid, txt in (legendas or {}).items():
        t = _txt(txt)
        if mid in fotos and t:
            if len(t) > 120:
                raise HTTPException(status_code=400, detail="Legenda da foto: até 120 caracteres")
            saida[mid] = t
    return saida


async def slug_de_passeio(db, tenant_id: str, desejado: str | None, nome: str,
                          exceto_id: str = None) -> str:
    """O caminho da página do passeio (`/passeio/{slug}`), único na agência.

    Gerado do nome na criação e **mantido** quando o nome muda: o link da
    página já foi divulgado no Instagram, e trocá-lo por causa de uma vírgula no
    nome quebraria todos.
    """
    if desejado:
        s = slugify(desejado)
        if not s or len(s) < 2:
            raise HTTPException(status_code=400, detail="Endereço da página do passeio inválido")
        outro = await db.services.find_one({"tenant_id": tenant_id, "site_slug": s},
                                           {"_id": 0, "id": 1})
        if outro and outro["id"] != exceto_id:
            raise HTTPException(status_code=409,
                                detail=f"Outro passeio já usa o endereço /passeio/{s}")
        return s
    raiz = slugify(nome) or "passeio"
    s, n = raiz, 2
    while True:
        outro = await db.services.find_one({"tenant_id": tenant_id, "site_slug": s},
                                           {"_id": 0, "id": 1})
        if not outro or outro["id"] == exceto_id:
            return s
        s = f"{raiz}-{n}"
        n += 1


async def marcar_imagens_do_site(db, tenant_id: str, ids) -> None:
    """Imagem que está na vitrine sai da janela de retenção de mídia (`uso: site`).

    A retenção existe para o acervo das conversas; logo, capa e foto de passeio
    são a vitrine, e apagadas em 90 dias deixariam o site sem imagem nenhuma.
    """
    ids = [i for i in (ids or []) if i]
    if ids:
        await db.media_files.update_many({"tenant_id": tenant_id, "id": {"$in": ids}},
                                         {"$set": {"uso": "site"}})


async def marcar_todas_imagens_do_site(db) -> int:
    """Migração: as imagens que já estavam no site antes da marcação existir."""
    n = 0
    for s in await db.sites.find({}, {"_id": 0, "tenant_id": 1, "logo_id": 1, "capa_id": 1}).to_list(10000):
        ids = [s.get("logo_id"), s.get("capa_id")]
        for p in await db.services.find({"tenant_id": s["tenant_id"]}, {"_id": 0, "site_fotos": 1}).to_list(500):
            ids += p.get("site_fotos") or []
        ids = [i for i in ids if i]
        if ids:
            r = await db.media_files.update_many(
                {"tenant_id": s["tenant_id"], "id": {"$in": ids}, "uso": {"$ne": "site"}},
                {"$set": {"uso": "site"}})
            n += r.modified_count
    return n


async def midia_publica(db, site: dict, media_id: str) -> dict | None:
    """O arquivo, se ele de fato pertence à vitrine deste site.

    É a única porta pública para arquivo nosso, e por isso não aceita id solto:
    tem de ser o logo, a capa ou foto de um passeio visível. Sem isso, qualquer
    id de `media_files` da agência (a foto do documento que um turista mandou
    no chat) ficaria a um palpite de distância.
    """
    permitidos = {site.get("logo_id"), site.get("capa_id")}
    for p in await passeios_do_site(db, site["tenant_id"]):
        permitidos.update(p.get("site_fotos") or [])
    for g in await db.site_paginas.find({"tenant_id": site["tenant_id"], "publicada": True},
                                        {"_id": 0, "capa_id": 1}).to_list(100):
        permitidos.add(g.get("capa_id"))
    if media_id not in permitidos:
        return None
    return await db.media_files.find_one({"id": media_id, "tenant_id": site["tenant_id"],
                                          "kind": "image"}, {"_id": 0})


# ---------------------------------------------------------------------------
# O clique e o código
# ---------------------------------------------------------------------------

def limite_de_cliques(ip: str) -> bool:
    """True se este IP ainda pode registrar clique agora."""
    agora = time.monotonic()
    fila = _cliques_por_ip.setdefault(ip or "?", deque())
    while fila and agora - fila[0] > JANELA_CLIQUES_S:
        fila.popleft()
    if len(fila) >= LIMITE_CLIQUES:
        return False
    fila.append(agora)
    if len(_cliques_por_ip) > 10000:
        # Teto de memória: IPs que pararam de clicar saem.
        for chave in [k for k, f in _cliques_por_ip.items() if not f][:5000]:
            _cliques_por_ip.pop(chave, None)
    return True


def _novo_codigo() -> str:
    return "".join(secrets.choice(ALFABETO) for _ in range(TAMANHO_CODIGO))


def mensagem_whatsapp(passeio: dict | None, dia_rotulo: str | None, pessoas_rotulo: str | None,
                      codigo: str | None, idioma: str = "pt") -> str:
    """A mensagem pronta, no idioma em que o turista viu o site.

    É o turista quem a manda; em inglês ela é dele, e a agência já sabe na
    primeira linha em que língua responder. O nome do passeio vai como a
    agência o cadastrou, porque é por ele que ela o reconhece.
    """
    import site_i18n
    t = site_i18n.T[site_i18n.valido(idioma)]
    if passeio:
        linhas = [t["zap_passeio"].format(nome=passeio["nome"])]
        if dia_rotulo:
            linhas.append(t["zap_data"].format(d=dia_rotulo))
        if pessoas_rotulo:
            linhas.append(f"👥 {pessoas_rotulo}")
    else:
        linhas = [t["zap_geral"]]
    if codigo:
        linhas.append("\n" + t["zap_codigo"].format(c=codigo))
    return "\n".join(linhas)


def _pessoas_no_idioma(a: int, c: int, idioma: str) -> str | None:
    import saidas
    if not a + c:
        return None
    if idioma == "en":
        partes = [f"{a} adult{'s' if a != 1 else ''}"] if a else []
        partes += [f"{c} child{'ren' if c != 1 else ''}"] if c else []
        return " and ".join(partes)
    if idioma == "es":
        partes = [f"{a} adulto{'s' if a != 1 else ''}"] if a else []
        partes += [f"{c} niño{'s' if c != 1 else ''}"] if c else []
        return " y ".join(partes)
    return saidas.rotulo_pessoas(a, c)


def link_whatsapp(numero: str, texto: str) -> str:
    return f"https://wa.me/{numero}?text={quote(texto)}"


async def registrar_clique(db, site: dict, *, passeio: dict | None, dia: str | None,
                           adultos: int, criancas: int, pagina: str = None,
                           referencia: str = None, idioma: str = "pt") -> dict:
    """Grava o clique e devolve o código e o link do WhatsApp."""
    import agenda
    import saidas
    import site_i18n

    idioma = site_i18n.valido(idioma)
    dia_rotulo = None
    if dia:
        try:
            d = agenda._dia(dia)
            dia_rotulo = (f"{agenda.DIAS[d.weekday()].lower()}, {d.day:02d}/{d.month:02d}" if idioma == "pt"
                          else site_i18n.dia_rotulo(dia, idioma))
        except HTTPException:
            dia = None
    a, c = max(0, min(int(adultos or 0), saidas.MAX_PESSOAS)), max(0, min(int(criancas or 0), saidas.MAX_PESSOAS))
    pessoas_rotulo = _pessoas_no_idioma(a, c, idioma)

    for _ in range(5):
        codigo = _novo_codigo()
        doc = {
            "id": str(uuid.uuid4()), "tenant_id": site["tenant_id"], "codigo": codigo,
            "service_id": (passeio or {}).get("id"), "service_nome": (passeio or {}).get("nome"),
            "dia": dia, "adultos": a, "criancas": c, "idioma": idioma,
            "pagina": (pagina or "")[:200] or None, "referencia": (referencia or "")[:300] or None,
            "convertido_em": None, "contact_id": None, "deal_id": None,
            "created_at": _now(),
            # `Date` nativo, não ISO: o índice TTL do Mongo só age sobre Date.
            # Clique que não virou conversa em 90 dias não vira mais.
            "criado_dt": datetime.now(timezone.utc),
        }
        try:
            await db.site_cliques.insert_one(dict(doc))
            break
        except Exception as e:  # código repetido (índice único): tenta outro
            if "duplicate" not in str(e).lower():
                raise
    texto = mensagem_whatsapp(passeio, dia_rotulo, pessoas_rotulo, codigo, idioma)
    return {"codigo": codigo, "link": link_whatsapp(site["whatsapp"], texto)}


async def atribuir(db, tenant_id: str, contato: dict, session_id: str, texto: str) -> str | None:
    """Reconhece o código do site numa mensagem recebida. Devolve o resumo para o
    histórico da conversa, ou None se não havia código válido.

    Chamado pela entrada única de mensagens, antes do roteamento: o bot e o
    agente de IA continuam respondendo normalmente, e quem abrir a conversa na
    Central já vê de onde o turista veio e o que ele quer.
    """
    m = RE_CODIGO.search(texto or "")
    if not m:
        return None
    codigo = m.group(1).upper()
    clique = await db.site_cliques.find_one_and_update(
        {"tenant_id": tenant_id, "codigo": codigo, "convertido_em": None},
        {"$set": {"convertido_em": _now(), "contact_id": contato["id"],
                  "session_id": session_id}},
        projection={"_id": 0})
    if not clique:
        return None

    import saidas
    if contato.get("origem") in (None, "mensagem recebida"):
        await db.contacts.update_one({"id": contato["id"], "tenant_id": tenant_id},
                                     {"$set": {"origem": "site"}})
        contato["origem"] = "site"

    partes = [clique.get("service_nome") or "interesse geral"]
    if clique.get("dia"):
        import agenda
        d = agenda._dia(clique["dia"])
        partes.append(f"{agenda.DIAS[d.weekday()].lower()} {d.day:02d}/{d.month:02d}")
    pessoas = int(clique.get("adultos") or 0) + int(clique.get("criancas") or 0)
    if pessoas:
        partes.append(saidas.rotulo_pessoas(int(clique["adultos"] or 0), int(clique["criancas"] or 0)))

    deal = None
    if clique.get("service_id"):
        passeio = await db.services.find_one({"id": clique["service_id"], "tenant_id": tenant_id},
                                             {"_id": 0})
        if passeio:
            valor = saidas.precos(passeio, int(clique.get("adultos") or 0) or 1,
                                  int(clique.get("criancas") or 0))["preco"] or 0
            import deals
            deal = await deals.criar_negocio(
                db, tenant_id, contato=contato, titulo=" · ".join(partes), valor=valor,
                origem="site", session_id=session_id,
                autor={"id": None, "name": "Site"})
            await db.site_cliques.update_one({"id": clique["id"]},
                                             {"$set": {"deal_id": deal["id"]}})
    resumo = "Veio do site: " + " · ".join(partes) + "."
    import site_i18n
    if clique.get("idioma") in site_i18n.EXTRAS:
        # Quem atende precisa saber, antes de responder, em que língua o turista leu o site.
        resumo += f" Viu o site em {site_i18n.EM_PORTUGUES[clique['idioma']]}."
    if deal:
        resumo += " Orçamento aberto no funil."
    return resumo


async def metricas(db, tenant_id: str, dias: int = 30) -> dict:
    """Cliques no botão e quantos viraram conversa, por passeio, numa agregação."""
    desde = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
    linhas = {}
    total = {"cliques": 0, "conversas": 0, "orcamentos": 0}
    async for c in db.site_cliques.find(
            {"tenant_id": tenant_id, "created_at": {"$gte": desde}},
            {"_id": 0, "service_id": 1, "service_nome": 1, "convertido_em": 1, "deal_id": 1}):
        chave = c.get("service_id") or ""
        lin = linhas.setdefault(chave, {"service_id": c.get("service_id"),
                                        "nome": c.get("service_nome") or "Página inicial",
                                        "cliques": 0, "conversas": 0})
        lin["cliques"] += 1
        total["cliques"] += 1
        if c.get("convertido_em"):
            lin["conversas"] += 1
            total["conversas"] += 1
        if c.get("deal_id"):
            total["orcamentos"] += 1
    return {"dias": dias, **total,
            "por_passeio": sorted(linhas.values(), key=lambda x: -x["cliques"])}
