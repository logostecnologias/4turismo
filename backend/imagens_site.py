"""Versões leves das imagens do site (logo, capa e fotos de passeio).

A agência sobe a foto do celular como ela é: 4 a 5 MB, 4000px de largura.
Servida assim, uma página de passeio com seis fotos passa de 20 MB, que no 4G
de quem está na praia é a página que não abre, e é a nota de velocidade que o
Google usa para ordenar resultado. Daqui saem:

- **WebP em 480, 960 e 1600px** de largura, para o `srcset` (o navegador
  escolhe pelo tamanho da tela). Foto menor que a largura não é ampliada.
- **JPEG de 1200px** para a prévia de link (`og:image`): o WhatsApp e algumas
  redes ainda não mostram WebP na prévia.

As versões são **reescritas a partir dos pixels**, sem os metadados do arquivo
original. Isso importa além do peso: foto de celular traz no EXIF as
coordenadas GPS de onde foi tirada, e a rota pública passa a servir só a versão
limpa, nunca o original.

Elas moram em `<MEDIA_DIR>/<tenant>/_variantes/<id da mídia>.<largura>.<formato>`.
A subpasta fica fora da varredura de órfãos do `media_retention`, que olha só os
arquivos soltos da pasta da empresa; a limpeza das variantes é feita lá à parte,
pelo id (variante cujo documento não existe mais sai).
"""
import asyncio
import logging
import os
import tempfile
from pathlib import Path

from PIL import Image, ImageOps

import media_store

logger = logging.getLogger("turismo.imagens_site")

LARGURAS = (480, 960, 1600)
LARGURA_PADRAO = 1600
LARGURA_PREVIA = 1200
# Ícone da aba (favicon): PNG quadrado. 48 é o mínimo que o Google aceita para
# mostrar o ícone no resultado da busca; 180 é o da tela inicial do iPhone
# (sobre branco, porque o iOS pinta de preto o que for transparente); 192 é o
# do Android. Saem do logo, e só quando alguém pede.
ICONES = (48, 180, 192)
ICONE_PADRAO = 48
ICONE_OPACO = 180
FORMATOS = {"webp": ("WEBP", "image/webp"), "jpg": ("JPEG", "image/jpeg"), "png": ("PNG", "image/png")}
QUALIDADE = {"webp": 80, "jpg": 82}


def pasta_variantes(tenant_id: str) -> Path:
    return media_store.media_root() / tenant_id / "_variantes"


def caminho(doc: dict, largura: int, formato: str) -> Path:
    return pasta_variantes(doc["tenant_id"]) / f"{doc['id']}.{largura}.{formato}"


def pedido_valido(largura: int | None, formato: str | None) -> tuple[int, str]:
    """A combinação pedida, ou a mais próxima que existe.

    Só as larguras fixas são geradas: aceitar qualquer `?w=` deixaria um robô
    pedir mil larguras da mesma foto e encher o disco de variantes.
    """
    formato = formato if formato in FORMATOS else "webp"
    if formato == "png":
        return (largura if largura in ICONES else ICONE_PADRAO), "png"
    if formato == "jpg":
        return LARGURA_PREVIA, "jpg"
    if largura not in LARGURAS:
        largura = next((l for l in LARGURAS if l >= (largura or LARGURA_PADRAO)), LARGURAS[-1])
    return largura, formato


def gerar(doc: dict, largura: int, formato: str) -> Path | None:
    """Gera (de forma síncrona) uma variante. None se o original não abre."""
    destino = caminho(doc, largura, formato)
    original = media_store.file_path(doc)
    if not original.exists():
        return None
    try:
        with Image.open(original) as im:
            im = ImageOps.exif_transpose(im)   # foto de celular "deitada"
            if formato == "png":
                im = _quadrado(im, largura, opaco=largura == ICONE_OPACO)
            elif formato == "jpg":
                if im.mode != "RGB":
                    # JPEG não tem transparência: o logo PNG transparente vai sobre branco.
                    rgba = im.convert("RGBA")
                    im = Image.new("RGB", im.size, (255, 255, 255))
                    im.paste(rgba, mask=rgba.split()[-1])
            elif im.mode not in ("RGB", "RGBA"):
                im = im.convert("RGBA")
            if im.width > largura:
                im = im.resize((largura, round(im.height * largura / im.width)), Image.LANCZOS)
            destino.parent.mkdir(parents=True, exist_ok=True)
            # Grava num temporário e troca de uma vez: dois visitantes pedindo a
            # mesma variante ao mesmo tempo não podem ler um arquivo pela metade.
            fd, tmp = tempfile.mkstemp(dir=destino.parent, suffix=".tmp")
            os.close(fd)
            # Sem `exif=`: a versão nova sai sem metadado nenhum do original.
            if formato == "png":
                im.save(tmp, "PNG", optimize=True)
            else:
                extra = {"method": 4} if formato == "webp" else {"optimize": True, "progressive": True}
                im.save(tmp, FORMATOS[formato][0], quality=QUALIDADE[formato], **extra)
            os.replace(tmp, destino)
            return destino
    except Exception as e:  # imagem corrompida, formato que o Pillow não abre, bomba de pixels
        logger.warning("Não consegui gerar variante %s de %s: %s", f"{largura}.{formato}", doc.get("id"), e)
        return None


def _quadrado(im: Image.Image, lado: int, opaco: bool) -> Image.Image:
    """A imagem inteira dentro de um quadrado, centrada, sem cortar.

    Logo de agência costuma ser largo; cortar para quadrado tiraria metade do
    nome. Encaixar deixa faixa nas bordas, que fica transparente (ou branca no
    ícone do iPhone)."""
    im = im.convert("RGBA")
    im.thumbnail((lado, lado), Image.LANCZOS)
    fundo = Image.new("RGBA", (lado, lado), (255, 255, 255, 255 if opaco else 0))
    fundo.paste(im, ((lado - im.width) // 2, (lado - im.height) // 2), im)
    return fundo.convert("RGB") if opaco else fundo


async def variante(doc: dict, largura: int | None = None, formato: str | None = None) -> tuple[Path, str] | None:
    """O arquivo leve para servir (gera na primeira vez) e o MIME dele."""
    largura, formato = pedido_valido(largura, formato)
    alvo = caminho(doc, largura, formato)
    if not alvo.exists():
        alvo = await asyncio.to_thread(gerar, doc, largura, formato)
    return (alvo, FORMATOS[formato][1]) if alvo else None


async def gerar_todas(doc: dict) -> int:
    """No upload: deixa as versões prontas para o primeiro visitante não esperar."""
    def _todas():
        n = sum(1 for l in LARGURAS if gerar(doc, l, "webp"))
        return n + (1 if gerar(doc, LARGURA_PREVIA, "jpg") else 0)
    return await asyncio.to_thread(_todas)


def apagar(doc: dict) -> None:
    for l in LARGURAS:
        caminho(doc, l, "webp").unlink(missing_ok=True)
    caminho(doc, LARGURA_PREVIA, "jpg").unlink(missing_ok=True)
    for l in ICONES:
        caminho(doc, l, "png").unlink(missing_ok=True)
