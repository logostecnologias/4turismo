"""Configuração do site da agência, no painel. Ver `sites.py`.

Só admin: o site é a cara pública da agência, e mexer no endereço ou publicar
muda o que todo turista vê. Operar (responder quem chegou pelo site) é da equipe
toda, e isso acontece na Central, não aqui.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, HTTPException, UploadFile
from pydantic import BaseModel, Field
from pymongo.errors import DuplicateKeyError

import contatos_csv
import imagens_site
import site_guias
import site_i18n
import site_traducao
import media_store
import sites
from auth import require_tenant_admin
from db import get_db

router = APIRouter(prefix="/api/site", tags=["site"])


def _now():
    return datetime.now(timezone.utc).isoformat()


class SiteIn(BaseModel):
    slug: str | None = None
    publicado: bool | None = None
    nome: str | None = Field(default=None, max_length=80)
    chamada: str | None = Field(default=None, max_length=sites.MAX_CHAMADA)
    sobre: str | None = Field(default=None, max_length=sites.MAX_SOBRE)
    whatsapp: str | None = None
    instagram: str | None = Field(default=None, max_length=60)
    email: str | None = Field(default=None, max_length=120)
    endereco: str | None = Field(default=None, max_length=200)
    logo_id: str | None = None
    capa_id: str | None = None
    dominio_proprio: str | None = None
    # Fase 3.1: o que faz o turista confiar e o que ele pergunta antes de reservar.
    cadastur: str | None = Field(default=None, max_length=40)
    desde: int | None = Field(default=None, ge=1900, le=2100)
    diferenciais: list[str] | None = None
    nota_avaliacoes: float | None = Field(default=None, ge=1, le=5)
    total_avaliacoes: int | None = Field(default=None, ge=0, le=10_000_000)
    link_avaliacoes: str | None = Field(default=None, max_length=300)
    fonte_avaliacoes: str | None = Field(default=None, max_length=40)
    depoimentos: list[sites.DepoimentoIn] | None = None
    faq: list[sites.PerguntaIn] | None = None
    horario: str | None = Field(default=None, max_length=120)
    pagamento: list[str] | None = None
    cancelamento: str | None = Field(default=None, max_length=sites.MAX_CANCELAMENTO)
    mostrar_embarque: bool | None = None
    # SEO: a tag (ou só o código) de verificação do Google Search Console.
    google_verificacao: str | None = Field(default=None, max_length=300)
    # Busca local: endereço e horário como dado.
    endereco_rua: str | None = Field(default=None, max_length=120)
    endereco_bairro: str | None = Field(default=None, max_length=60)
    cidade: str | None = Field(default=None, max_length=60)
    uf: str | None = Field(default=None, max_length=2)
    cep: str | None = Field(default=None, max_length=12)
    # Link do Google Maps ou "lat, lng"; vazio apaga a localização.
    coordenadas: str | None = Field(default=None, max_length=1000)
    horarios: list[sites.HorarioIn] | None = None
    idiomas: list[str] | None = None


async def _resposta(db, tenant_id: str) -> dict:
    site = await sites.site_da_empresa(db, tenant_id)
    if not site.get("nome"):
        tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "name": 1}) or {}
        site["nome"] = tenant.get("name")
    url = sites.url_do_site(site.get("slug"))
    dominio = site.get("dominio_proprio")
    canonica = sites.base_canonica(site)
    return {
        "site": {c: site.get(c) for c in sites.CAMPOS},
        "dominio_base": sites.dominio_base(),
        "url": url,
        # A prévia funciona mesmo publicado (é o mesmo endereço com o token);
        # serve para ver mudanças ainda não divulgadas sem despublicar.
        "url_previa": f"{url}/?previa={sites.token_previa(tenant_id, site['slug'])}" if url else None,
        "url_dominio_proprio": f"https://{dominio}" if dominio else None,
        # O domínio próprio só vira o endereço oficial (e o subdomínio passa a
        # redirecionar para ele) depois da primeira visita de verdade por ele.
        "dominio_ativo": bool(dominio and site.get("dominio_ativo_em")),
        "url_canonica": canonica,
        "url_sitemap": f"{canonica}/sitemap.xml" if canonica else None,
        "endereco_exibido": sites.endereco_exibido(site),
        "horario_exibido": sites.horario_exibido(site),
        "traducoes": await site_traducao.resumo(db, tenant_id),
        "pronto": await sites.prontidao(db, site),
        "metricas": await sites.metricas(db, tenant_id),
    }


@router.get("")
async def ver_site(user=Depends(require_tenant_admin)):
    return await _resposta(get_db(), user["tenant_id"])


@router.put("")
async def salvar_site(body: SiteIn, user=Depends(require_tenant_admin)):
    db = get_db()
    tenant_id = user["tenant_id"]
    atual = await sites.site_da_empresa(db, tenant_id)
    campos = {k: v for k, v in body.model_dump().items() if k in body.model_fields_set}

    if "slug" in campos:
        slug = (campos["slug"] or "").strip().lower()
        problema = sites.problema_do_slug(slug)
        if problema:
            raise HTTPException(status_code=400, detail=problema)
        if await sites.slug_em_uso(db, slug, exceto_tenant=tenant_id):
            raise HTTPException(status_code=409, detail="Esse endereço já é de outra agência")
        campos["slug"] = slug
    if "whatsapp" in campos:
        if campos["whatsapp"]:
            numero, erro = contatos_csv.normaliza_numero(campos["whatsapp"])
            if erro:
                raise HTTPException(status_code=400, detail=f"WhatsApp inválido: {erro}")
            campos["whatsapp"] = numero
        else:
            campos["whatsapp"] = None
    if "instagram" in campos:
        campos["instagram"] = (campos["instagram"] or "").strip().lstrip("@").strip("/") or None
    if "dominio_proprio" in campos:
        dom = sites.normaliza_dominio(campos["dominio_proprio"])
        if dom:
            outro = await db.sites.find_one({"dominio_proprio": dom}, {"_id": 0, "tenant_id": 1})
            if outro and outro["tenant_id"] != tenant_id:
                raise HTTPException(status_code=409, detail="Esse domínio já está em outra agência")
        campos["dominio_proprio"] = dom
        if dom != atual.get("dominio_proprio"):
            # Domínio novo precisa provar de novo que funciona antes de virar o
            # oficial; senão o subdomínio redirecionaria para um endereço morto.
            campos["dominio_ativo_em"] = None
    if "google_verificacao" in campos:
        campos["google_verificacao"] = sites.normaliza_verificacao_google(campos["google_verificacao"])
    for chave in ("endereco_rua", "endereco_bairro", "cidade"):
        if chave in campos:
            campos[chave] = (campos[chave] or "").strip() or None
    if "uf" in campos:
        uf = (campos["uf"] or "").strip().upper() or None
        if uf and uf not in sites.UFS:
            raise HTTPException(status_code=400, detail="UF inválida")
        campos["uf"] = uf
    if "cep" in campos:
        campos["cep"] = sites.normaliza_cep(campos["cep"])
    if "coordenadas" in campos:
        coords = sites.ler_coordenadas(campos.pop("coordenadas"))
        campos["latitude"], campos["longitude"] = coords if coords else (None, None)
    if "horarios" in campos:
        campos["horarios"] = sites.validar_horarios(campos["horarios"])
    if "idiomas" in campos:
        campos["idiomas"] = sites.validar_idiomas(campos["idiomas"])
    for chave in ("logo_id", "capa_id"):
        if campos.get(chave) and not await db.media_files.find_one(
                {"id": campos[chave], "tenant_id": tenant_id, "kind": "image"}, {"_id": 0, "id": 1}):
            raise HTTPException(status_code=400, detail="Imagem inválida")
    for chave in ("nome", "chamada", "sobre", "email", "endereco", "cadastur", "fonte_avaliacoes",
                  "horario", "cancelamento"):
        if chave in campos:
            campos[chave] = (campos[chave] or "").strip() or None
    if "diferenciais" in campos:
        campos["diferenciais"] = sites.validar_lista(campos["diferenciais"], "Diferenciais",
                                                     maximo=sites.MAX_DIFERENCIAIS, tamanho=80)
    if "pagamento" in campos:
        campos["pagamento"] = sites.validar_lista(campos["pagamento"], "Formas de pagamento",
                                                  maximo=sites.MAX_PAGAMENTO, tamanho=60)
    if "depoimentos" in campos:
        campos["depoimentos"] = sites.validar_depoimentos(campos["depoimentos"])
    if "faq" in campos:
        campos["faq"] = sites.validar_perguntas(campos["faq"], sites.MAX_PERGUNTAS_SITE)
    if "link_avaliacoes" in campos:
        campos["link_avaliacoes"] = sites.validar_url_publica(campos["link_avaliacoes"], "Link das avaliações")
    if campos.get("nota_avaliacoes") is not None:
        campos["nota_avaliacoes"] = round(float(campos["nota_avaliacoes"]), 1)
    if campos.get("desde") and campos["desde"] > datetime.now(timezone.utc).year:
        raise HTTPException(status_code=400, detail="O ano de fundação não pode estar no futuro")

    if campos.get("publicado"):
        # A prontidão é conferida sobre o estado **depois** desta gravação: quem
        # manda slug e "publicar" no mesmo clique não pode levar um 409 dizendo
        # que falta o slug que ele acabou de escolher.
        pronto = await sites.prontidao(db, {**atual, **campos})
        if not pronto["publicavel"]:
            raise HTTPException(status_code=409,
                                detail="Ainda não dá para publicar: " + "; ".join(pronto["faltando"]))

    await sites.marcar_imagens_do_site(db, tenant_id, [campos.get("logo_id"), campos.get("capa_id")])
    campos["updated_at"] = _now()
    try:
        await db.sites.update_one(
            {"tenant_id": tenant_id},
            {"$set": campos, "$setOnInsert": {"id": str(uuid.uuid4()), "created_at": _now()}},
            upsert=True)
    except DuplicateKeyError:
        # Duas agências escolhendo o mesmo endereço no mesmo instante: a
        # conferência acima não fecha essa corrida, o índice único fecha.
        raise HTTPException(status_code=409, detail="Esse endereço (ou domínio) acabou de ser escolhido por outra agência")
    return await _resposta(db, tenant_id)


@router.get("/slug-livre")
async def slug_livre(slug: str, user=Depends(require_tenant_admin)):
    db = get_db()
    s = (slug or "").strip().lower()
    problema = sites.problema_do_slug(s)
    if problema:
        return {"livre": False, "motivo": problema,
                "sugestao": await sites.sugerir_slug(db, s or "agencia", user["tenant_id"])}
    if await sites.slug_em_uso(db, s, exceto_tenant=user["tenant_id"]):
        return {"livre": False, "motivo": "Esse endereço já é de outra agência",
                "sugestao": await sites.sugerir_slug(db, s, user["tenant_id"])}
    return {"livre": True}


@router.get("/sugestao-slug")
async def sugestao_slug(user=Depends(require_tenant_admin)):
    db = get_db()
    tenant = await db.tenants.find_one({"id": user["tenant_id"]}, {"_id": 0, "name": 1}) or {}
    return {"slug": await sites.sugerir_slug(db, tenant.get("name") or "agencia", user["tenant_id"])}


@router.post("/midia")
async def subir_imagem(file: UploadFile = File(...), user=Depends(require_tenant_admin)):
    """Logo, capa e fotos dos passeios. Só imagem, até 5MB.

    O teto é aplicado durante a leitura, como em todo upload do sistema. O tipo
    é decidido pelo nome e pelo MIME juntos (`media_store.classify`), e o SVG
    cai como documento ali, de propósito: SVG é script, e servi-lo como imagem
    numa página pública seria abrir XSS no domínio do site.
    """
    partes, total = [], 0
    while True:
        bloco = await file.read(64 * 1024)
        if not bloco:
            break
        total += len(bloco)
        if total > sites.MAX_BYTES_IMAGEM:
            raise HTTPException(status_code=400, detail="Imagem acima de 5MB")
        partes.append(bloco)
    if not total:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    if media_store.classify(file.content_type, file.filename) != "image":
        raise HTTPException(status_code=400,
                            detail="Envie uma imagem (JPG, PNG ou WebP)")
    db = get_db()
    doc = await media_store.store_upload(db, user["tenant_id"], None, user.get("id"),
                                         file.filename, file.content_type, b"".join(partes),
                                         kind="image", limit=sites.MAX_BYTES_IMAGEM)
    # `uso: site` tira a imagem da janela de retenção de mídia: ela é a vitrine
    # da agência, não anexo de conversa, e sumiria do site em 90 dias.
    await db.media_files.update_one({"id": doc["id"], "tenant_id": user["tenant_id"]},
                                    {"$set": {"uso": "site"}})
    await imagens_site.gerar_todas(doc)
    return {"id": doc["id"], "url": f"/api/media/{doc['id']}"}


# ---------------------------------------------------------------------------
# Guias (páginas de conteúdo). Ver `site_guias.py`.
# ---------------------------------------------------------------------------

@router.get("/paginas")
async def listar_guias(user=Depends(require_tenant_admin)):
    db = get_db()
    site = await sites.site_da_empresa(db, user["tenant_id"])
    base = sites.base_canonica(site)
    guias = await site_guias.publicados(db, user["tenant_id"], com_rascunho=True)
    return {"guias": [{**g, "url": f"{base}/guia/{g['slug']}" if base else None} for g in guias],
            "modelos": site_guias.MODELOS, "max": site_guias.MAX_GUIAS}


@router.post("/paginas")
async def criar_guia(body: site_guias.GuiaIn, user=Depends(require_tenant_admin)):
    return await site_guias.criar(get_db(), user["tenant_id"], body)


@router.patch("/paginas/{guia_id}")
async def editar_guia(guia_id: str, body: site_guias.GuiaIn, user=Depends(require_tenant_admin)):
    db = get_db()
    atual = await db.site_paginas.find_one({"id": guia_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not atual:
        raise HTTPException(status_code=404, detail="Guia não encontrado")
    novos = await site_guias.campos(db, user["tenant_id"], body, atual)
    await db.site_paginas.update_one({"id": guia_id, "tenant_id": user["tenant_id"]}, {"$set": novos})
    return {**atual, **novos}


@router.delete("/paginas/{guia_id}")
async def remover_guia(guia_id: str, user=Depends(require_tenant_admin)):
    res = await get_db().site_paginas.delete_one({"id": guia_id, "tenant_id": user["tenant_id"]})
    if not res.deleted_count:
        raise HTTPException(status_code=404, detail="Guia não encontrado")
    return {"ok": True}


# ---------------------------------------------------------------------------
# Tradução (inglês e espanhol). Ver `site_traducao.py`.
# ---------------------------------------------------------------------------

class TraducaoIn(BaseModel):
    idioma: str
    chave: str = Field(max_length=40)
    texto: str | None = Field(default=None, max_length=24000)


class TraduzirIn(BaseModel):
    idioma: str


@router.get("/traducoes")
async def ver_traducoes(idioma: str, user=Depends(require_tenant_admin)):
    return await site_traducao.revisao(get_db(), user["tenant_id"], idioma)


@router.put("/traducoes")
async def corrigir_traducao(body: TraducaoIn, user=Depends(require_tenant_admin)):
    return await site_traducao.salvar_manual(get_db(), user["tenant_id"], body.idioma, body.chave, body.texto)


@router.post("/traduzir")
async def traduzir(body: TraduzirIn, user=Depends(require_tenant_admin)):
    """Um lote do que falta traduzir; a tela repete até `restantes` zerar."""
    return await site_traducao.traduzir(get_db(), user["tenant_id"], body.idioma)
