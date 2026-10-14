"""Armazenamento dos arquivos de mídia trocados nas conversas.

O binário fica em disco (`MEDIA_DIR`, default `backend/media/<tenant_id>/`) e
cada arquivo ganha um documento em `media_files` carregando o `tenant_id`. É
esse documento — nunca o caminho em disco — que autoriza o download: sem ele,
um id vazado daria acesso a arquivo de outra empresa. O nome original do
usuário também nunca vira nome de arquivo (path traversal); ele fica só nos
metadados, para exibição e para o campo FileName do WhatsApp.

Na Cloud API o arquivo não viaja embutido na mensagem: ele é **subido antes**
(`POST /{phone_number_id}/media`) e o envio carrega só o `media_id` devolvido.
Esse id vale 30 dias e serve para quantos envios se quiser, então ele é
guardado no próprio documento (ver `id_na_meta`): sem isso, a mesma imagem
enviada a vários contatos subiria uma vez por envio.
"""
import asyncio
import base64
import mimetypes
import os
import re
import uuid
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import AsyncIterator

import net_guard

DEFAULT_MIME = "application/octet-stream"

# Bloco de leitura/escrita em disco.
CHUNK = 3 * 256 * 1024

# O mime vem do upload, ou seja, do cliente. Aceitar só a forma canônica
# (tipo/subtipo) evita que ele carregue aspas ou barras invertidas: o valor vai
# no campo `type` do multipart de upload e no cabeçalho da parte do arquivo.
_MIME_OK = re.compile(r"^[A-Za-z0-9!#$&^_.+-]{1,64}/[A-Za-z0-9!#$&^_.+-]{1,64}$")

# Tetos por tipo, agora os da própria Cloud API. Documento subiu de 20MB para
# 100MB porque o teto antigo era nosso: o arquivo virava base64 (mais 33%) e
# atravessava o gateway num POST só. Com upload multipart direto para a Meta,
# quem manda é o limite dela.
MAX_BYTES = {
    "image": 5 * 1024 * 1024,
    "video": 16 * 1024 * 1024,
    "audio": 16 * 1024 * 1024,
    "document": 100 * 1024 * 1024,
}
HARD_MAX_BYTES = max(MAX_BYTES.values())

KIND_LABEL = {
    "image": "Imagem",
    "video": "Vídeo",
    "audio": "Áudio",
    "document": "Documento",
}


class MediaError(Exception):
    pass


def sanitize_mime(mime: str | None, filename: str | None = None) -> str:
    """Mime em forma canônica, ou o genérico se não for reconhecível."""
    limpo = (mime or "").split(";")[0].strip().lower()
    if not _MIME_OK.match(limpo):
        limpo = (mimetypes.guess_type(filename or "")[0] or "").lower()
    return limpo if _MIME_OK.match(limpo) else DEFAULT_MIME


def media_root() -> Path:
    root = os.environ.get("MEDIA_DIR") or (Path(__file__).parent / "media")
    return Path(root)


# Áudio que a Cloud API aceita como áudio. Fora desta lista o arquivo vai como
# documento: a Meta recusa o envio inteiro por mime não suportado, e um anexo
# que chega como documento é melhor que uma mensagem que não chega. O mp3, que
# na API anterior precisava ir como documento (o endpoint de áudio de lá só
# tocava ogg/opus), agora é aceito de verdade.
AUDIO_SUPORTADO = {"audio/aac", "audio/mp4", "audio/mpeg", "audio/amr",
                   "audio/ogg", "audio/opus"}


def classify(mime: str | None, filename: str | None = None) -> str:
    """Tipo de envio da Cloud API para este arquivo."""
    mime = (mime or "").lower().split(";")[0].strip()
    if (not mime or mime == DEFAULT_MIME) and filename:
        mime = (mimetypes.guess_type(filename)[0] or "").lower()
    # SVG é uma imagem que também é um documento executável (<script>, onload).
    # Tratá-lo como documento faz `/api/media` servi-lo como anexo, nunca
    # inline na origem da API. É a raiz do ZZ-07, e vale para upload, mídia
    # recebida de um contato e envio.
    if mime == "image/svg+xml":
        return "document"
    if mime.startswith("image/"):
        return "image"
    if mime.startswith("video/"):
        return "video"
    if mime in AUDIO_SUPORTADO:
        return "audio"
    return "document"


def _extension(filename: str | None, mime: str | None) -> str:
    ext = Path(filename or "").suffix
    if ext and len(ext) <= 10 and ext.isascii():
        return ext.lower()
    return mimetypes.guess_extension(mime or "") or ".bin"


def _safe_display_name(filename: str | None, mime: str | None) -> str:
    """Nome só para exibição/FileName — sem diretório, nunca usado no disco."""
    name = Path(filename or "").name.strip()
    return name or f"arquivo{_extension(None, mime)}"


async def store_upload(
    db,
    tenant_id: str,
    session_id: str | None,
    user_id: str | None,
    filename: str | None,
    mime: str | None,
    data: bytes,
    kind: str | None = None,
    limit: int | None = None,
) -> dict:
    """`kind`/`limit` só são passados no caminho de recebimento: o tipo já vem
    decidido pelo nó da mensagem, e o teto é o geral — recusar por exceder o
    limite de envio perderia uma mídia que o contato já mandou."""
    if not data:
        raise MediaError("Arquivo vazio")
    kind = kind or classify(mime, filename)
    limit = limit or MAX_BYTES.get(kind, HARD_MAX_BYTES)
    if len(data) > limit:
        raise MediaError(
            f"{KIND_LABEL.get(kind, 'Arquivo')} acima do limite de {limit // (1024 * 1024)}MB"
        )

    mime = sanitize_mime(mime, filename)
    media_id = str(uuid.uuid4())
    stored_name = f"{uuid.uuid4().hex}{_extension(filename, mime)}"
    folder = media_root() / tenant_id
    folder.mkdir(parents=True, exist_ok=True)
    (folder / stored_name).write_bytes(data)

    doc = {
        "id": media_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "uploaded_by": user_id,
        "filename": _safe_display_name(filename, mime),
        "stored_name": stored_name,
        "mime": mime,
        "kind": kind,
        "size": len(data),
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.media_files.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def store_stream(
    db,
    tenant_id: str,
    session_id: str | None,
    user_id: str | None,
    filename: str | None,
    mime: str | None,
    read_chunk,
    kind: str | None = None,
    limit: int | None = None,
) -> dict:
    """Igual a store_upload, mas grava o upload conforme ele chega.

    `read_chunk` é uma corrotina que devolve o próximo bloco (o `.read(n)` do
    UploadFile). Juntar o arquivo inteiro na memória para só então escrevê-lo
    custava duas cópias dele — e o teto por tipo chega a 20MB, por envio
    simultâneo. Aqui a memória fica no tamanho de um bloco.

    O arquivo parcial é apagado se algo der errado: quem estoura o limite não
    pode deixar 20MB órfãos em disco.
    """
    kind = kind or classify(mime, filename)
    limit = limit or MAX_BYTES.get(kind, HARD_MAX_BYTES)
    mime = sanitize_mime(mime, filename)

    media_id = str(uuid.uuid4())
    stored_name = f"{uuid.uuid4().hex}{_extension(filename, mime)}"
    folder = media_root() / tenant_id
    folder.mkdir(parents=True, exist_ok=True)
    destino = folder / stored_name

    total = 0
    try:
        with open(destino, "wb") as saida:
            while True:
                bloco = await read_chunk(CHUNK)
                if not bloco:
                    break
                total += len(bloco)
                if total > limit:
                    raise MediaError(
                        f"{KIND_LABEL.get(kind, 'Arquivo')} acima do limite de "
                        f"{limit // (1024 * 1024)}MB"
                    )
                saida.write(bloco)
        if not total:
            raise MediaError("Arquivo vazio")
    except BaseException:
        destino.unlink(missing_ok=True)
        raise

    doc = {
        "id": media_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "uploaded_by": user_id,
        "filename": _safe_display_name(filename, mime),
        "stored_name": stored_name,
        "mime": mime,
        "kind": kind,
        "size": total,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.media_files.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


# `uso` tira um arquivo da janela de retenção de mídia (`media_retention`), que
# existe para o acervo das conversas. Fica fora o que é **corrente**, não
# acumulado: o avatar da ficha, as imagens do site e as fotos do catálogo do
# agente de IA. Sem isso, o catálogo esvaziava sozinho em 90 dias e o agente
# passava a prometer foto que não existe mais.
USO_CATALOGO_IA = "catalogo_ia"


async def liberar_do_catalogo(db, tenant_id: str, media_ids) -> None:
    """Foto que saiu do catálogo volta para a retenção, se nenhum outro agente a usa.

    Ela continua referenciada pelas mensagens já enviadas, e é a retenção que a
    apaga quando a janela da conversa vencer, como qualquer outro anexo.
    """
    ids = {m for m in (media_ids or []) if m}
    if not ids:
        return
    em_uso = set()
    for ag in await db.ai_agents.find({"tenant_id": tenant_id}, {"_id": 0, "midias": 1}).to_list(1000):
        em_uso.update(m.get("media_id") for m in ag.get("midias") or [])
    livres = list(ids - em_uso)
    if livres:
        await db.media_files.update_many(
            {"tenant_id": tenant_id, "id": {"$in": livres}, "uso": USO_CATALOGO_IA},
            {"$unset": {"uso": ""}})


async def marcar_catalogos_existentes(db) -> int:
    """Migração: as fotos que já estavam em catálogo antes da marcação existir."""
    n = 0
    for ag in await db.ai_agents.find({}, {"_id": 0, "tenant_id": 1, "midias": 1}).to_list(10000):
        ids = [m.get("media_id") for m in ag.get("midias") or [] if m.get("media_id")]
        if ids:
            r = await db.media_files.update_many(
                {"tenant_id": ag["tenant_id"], "id": {"$in": ids}, "uso": None},
                {"$set": {"uso": USO_CATALOGO_IA}})
            n += r.modified_count
    return n


def file_path(doc: dict) -> Path:
    return media_root() / doc["tenant_id"] / doc["stored_name"]


def read_bytes(doc: dict) -> bytes:
    path = file_path(doc)
    if not path.exists():
        raise MediaError("Arquivo não encontrado no armazenamento")
    return path.read_bytes()


async def store_pedacos(
    db,
    tenant_id: str,
    session_id: str | None,
    filename: str | None,
    mime: str | None,
    pedacos: AsyncIterator[bytes],
    kind: str | None = None,
    limit: int | None = None,
) -> dict:
    """Grava em disco a mídia que está sendo baixada da Meta, conforme ela chega.

    É o par de `store_stream` para o outro sentido: lá a origem é o upload de
    um atendente (uma corrotina `read(n)`), aqui é o corpo da resposta HTTP do
    download (um iterador assíncrono). Nos dois casos o arquivo nunca existe
    inteiro na memória, que é a regra do projeto, e o parcial é apagado se
    algo falhar no meio.
    """
    kind = kind or classify(mime, filename)
    limit = limit or MAX_BYTES.get(kind, HARD_MAX_BYTES)
    mime = sanitize_mime(mime, filename)

    media_id = str(uuid.uuid4())
    stored_name = f"{uuid.uuid4().hex}{_extension(filename, mime)}"
    folder = media_root() / tenant_id
    folder.mkdir(parents=True, exist_ok=True)
    destino = folder / stored_name

    total = 0
    try:
        with open(destino, "wb") as saida:
            async for bloco in pedacos:
                if not bloco:
                    continue
                total += len(bloco)
                if total > limit:
                    raise MediaError(
                        f"{KIND_LABEL.get(kind, 'Arquivo')} acima do limite de "
                        f"{limit // (1024 * 1024)}MB"
                    )
                saida.write(bloco)
        if not total:
            raise MediaError("Arquivo vazio")
    except BaseException:
        destino.unlink(missing_ok=True)
        raise

    doc = {
        "id": media_id,
        "tenant_id": tenant_id,
        "session_id": session_id,
        "uploaded_by": None,
        "filename": _safe_display_name(filename, mime),
        "stored_name": stored_name,
        "mime": mime,
        "kind": kind,
        "size": total,
        "created_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.media_files.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


# O id que a Meta devolve no upload vale 30 dias. Guardamos por menos, para a
# margem cobrir o id expirando no meio de uma série de envios: subir de novo
# custa um upload, usar um id vencido custa um erro por envio.
VALIDADE_ID_META = timedelta(days=25)


async def id_na_meta(db, doc: dict, cred) -> str:
    """O `media_id` deste arquivo para este número, subindo-o se preciso.

    O id é **por número** (quem sobe é o `phone_number_id`), então o cache é
    uma entrada por conexão dentro do próprio documento do arquivo. Isso é o
    que faz a mesma imagem enviada a muitos contatos subir uma vez só: cada
    upload é o arquivo inteiro atravessando a rede.
    """
    import whatsapp_client as whatsapp  # tardio: evita ciclo no import do módulo

    agora = datetime.now(timezone.utc)
    guardados = (doc.get("meta_ids") or {})
    entrada = guardados.get(cred.phone_number_id) or {}
    if entrada.get("id") and entrada.get("expira_em", "") > agora.isoformat():
        return entrada["id"]

    caminho = file_path(doc)
    if not caminho.exists():
        raise MediaError("Arquivo não encontrado no armazenamento")
    media_id = await whatsapp.upload_media(
        cred, caminho, sanitize_mime(doc.get("mime")), doc.get("filename")
    )
    await db.media_files.update_one(
        {"id": doc["id"]},
        {"$set": {f"meta_ids.{cred.phone_number_id}": {
            "id": media_id,
            "expira_em": (agora + VALIDADE_ID_META).isoformat(),
        }}},
    )
    return media_id


# ---------------------------------------------------------------------------
# Data URI: o caminho de mídia do canal não oficial
#
# Estas três saíram no commit da migração para a Cloud API, e voltaram em
# 09/09/2026 com o canal `wuzapi`. O motivo de existirem é a diferença entre
# os dois protocolos: a Cloud API sobe o arquivo à parte e manda só um
# `media_id`, enquanto a WuzAPI quer o arquivo **dentro** do JSON, em base64.
#
# Não são um retrocesso de desenho — são o preço do canal não oficial, e a
# engenharia aqui existe justamente para esse preço não ser pago em memória.
# ---------------------------------------------------------------------------

def to_data_uri(mime: str | None, data: bytes) -> str:
    return f"data:{mime or DEFAULT_MIME};base64,{base64.b64encode(data).decode()}"


class DataUriStream:
    """O arquivo armazenado, como data URI, lido aos pedaços na hora do envio.

    Existe para o arquivo nunca precisar caber inteiro na memória. O caminho
    ingênuo (`to_data_uri(mime, path.read_bytes())`) mantém vivos ao mesmo
    tempo o arquivo bruto, o base64 em bytes e mais uma cópia em str — e o
    httpx ainda serializa tudo de novo para montar o JSON. Um vídeo de 16MB
    chega à ordem de dezenas de MB de pico; assim, o pico é um bloco.

    `len()` dá o tamanho exato do data URI sem montá-lo — base64 é
    determinístico (4 bytes de saída para cada 3 de entrada, arredondando para
    cima), e é isso que permite mandar `Content-Length` e evitar
    `Transfer-Encoding: chunked`, que nem todo servidor aceita.

    Cada `async for` reabre o arquivo do zero, de propósito: o envio tem
    retry, e um gerador já consumido mandaria corpo vazio na segunda
    tentativa.
    """

    def __init__(self, doc: dict):
        self._preparar(file_path(doc), sanitize_mime(doc.get("mime")))

    @classmethod
    def de_caminho(cls, caminho: str | Path, mime: str | None = None) -> "DataUriStream":
        """O mesmo, a partir de um caminho solto, sem o documento do arquivo.

        É o caso do canal `wuzapi`. Lá o "upload" não existe como etapa, então
        `whatsapp_client.upload_media` devolve um marcador com o caminho e o
        mime, e é o envio que monta o data URI direto do disco. Nesse ponto
        quem chama tem o caminho, não o documento de `media_files` — e exigir
        o documento obrigaria a passar o canal para dentro da Central e do
        motor de fluxos, que é exatamente o que o despacho evita.
        """
        obj = cls.__new__(cls)
        obj._preparar(Path(caminho), sanitize_mime(mime))
        return obj

    def _preparar(self, caminho: Path, mime: str) -> None:
        self.path = caminho
        if not self.path.exists():
            raise MediaError("Arquivo não encontrado no armazenamento")
        # O tamanho vem do disco, não do documento: é o arquivo que vai ser
        # lido, e um Content-Length que não bate com o corpo corrompe a
        # requisição inteira.
        self.size = self.path.stat().st_size
        # O prefixo carrega o mime saneado — ele termina dentro de uma string
        # JSON. O base64 em si só tem caracteres seguros, por construção.
        self.prefixo = f"data:{mime};base64,".encode()

    def __len__(self) -> int:
        return len(self.prefixo) + 4 * ((self.size + 2) // 3)

    async def __aiter__(self):
        yield self.prefixo
        lido = 0
        with open(self.path, "rb") as entrada:
            while True:
                # Em thread para não travar o event loop lendo disco.
                bloco = await asyncio.to_thread(entrada.read, CHUNK)
                if not bloco:
                    break
                lido += len(bloco)
                yield base64.b64encode(bloco)
        if lido != self.size:
            # O arquivo mudou de tamanho entre o stat e a leitura: o corpo já
            # foi enviado pela metade e não há como voltar atrás, mas o erro
            # precisa aparecer no log em vez de virar mídia truncada calada.
            raise MediaError(
                f"Arquivo mudou durante o envio ({lido} != {self.size} bytes)"
            )


async def ensure_data_uri(source: str, mime: str | None = None) -> str:
    """Aceita data URI (devolve como está) ou URL http(s) (baixa e converte).

    O nó de Mídia do editor guarda uma URL pública. Na Cloud API ela vai como
    `link` e **a Meta** a busca; a WuzAPI não busca URL sozinha, então no canal
    não oficial somos nós que baixamos — e é por isso que aqui o `net_guard`
    volta a ser necessário, enquanto no caminho da Cloud API ele não é.
    """
    if not source:
        raise MediaError("Mídia sem origem")
    if source.startswith("data:"):
        return source
    if not source.startswith(("http://", "https://")):
        raise MediaError(f"Origem de mídia não suportada: {source[:40]}")
    # A URL vem do nó de Mídia (cliente) e é o servidor que a busca: o
    # net_guard recusa destino interno/privado e aplica o teto de bytes
    # **durante** o download, sem materializar a resposta antes de checar.
    try:
        ctype, content = await net_guard.fetch_bytes(source, max_bytes=HARD_MAX_BYTES)
    except net_guard.SSRFError as e:
        raise MediaError(str(e)) from e
    return to_data_uri(mime or ctype, content)


def public_payload(doc: dict) -> dict:
    """Recorte que vai no `meta` da mensagem e é lido pelo chat."""
    return {
        "id": doc["id"],
        "filename": doc["filename"],
        "mime": doc["mime"],
        "kind": doc["kind"],
        "size": doc["size"],
    }
