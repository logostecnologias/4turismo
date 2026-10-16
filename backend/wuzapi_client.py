"""WuzAPI (whatsmeow) async client.

The gateway (not WuzAPI directly) is the base URL for everything here: it
applies rate limiting to every call and, for /chat/send/*, automatically
resolves the phone number to WhatsApp's canonical JID first (handles the
Brazilian 9-digit quirk transparently — no need to replicate that logic
here).

Two-tier auth, unlike Evolution's single apikey:
  - WUZAPI_ADMIN_TOKEN: only for /admin/users (create/list/delete instances).
  - per-connection token: returned when creating an instance, used as the
    `token` header for every other call (session, chat, user) for that
    instance. Store it as `wuzapi_token` — it's the functional replacement
    for what used to be passed around as `instance_name`.

Endpoint shape (WuzAPI, via gateway):
  POST   /admin/users               — cria instância (Authorization: ADMIN_TOKEN)
  DELETE /admin/users/{id}          — remove instância
  POST   /session/connect           — inicia sessão (token header)
  POST   /session/disconnect        — desconecta mantendo sessão (permite reconectar sem novo QR)
  POST   /session/logout            — desconecta e apaga sessão
  GET    /session/status            — {"data": {"connected": bool, "loggedIn": bool, ...}}
  GET    /session/qr                — {"data": {"QRCode": "data:image/png;base64,..."}}
  POST   /chat/send/text            — {Phone, Body}
  POST   /chat/send/image           — {Phone, Image, Caption}
  POST   /chat/send/audio           — {Phone, Audio, Caption}
  POST   /chat/send/document        — {Phone, Document, FileName, Caption}
  POST   /chat/send/video           — {Phone, Video, Caption}
  POST   /chat/send/sticker         — {Phone, Sticker}
  POST   /chat/send/buttons         — {Phone, Body, Title, Footer, Buttons:[{type,id,title}]}
  POST   /chat/send/list            — {Phone, Desc, ButtonText, TopText, FooterText,
                                       Sections:[{title, rows:[{title, desc, RowId}]}]}
  POST   /chat/downloadimage        — mídia recebida: {Url, DirectPath, MediaKey,
                                       Mimetype, FileSHA256, FileEncSHA256, FileLength}
  POST   /chat/downloadvideo        — idem
  POST   /chat/downloadaudio        — idem
  POST   /chat/downloaddocument     — idem

As duas últimas são as mensagens interativas: botão de resposta rápida e
lista de opções. Nenhuma das duas está documentada no API.md da WuzAPI — a
forma dos campos vem do handler dela, e as chaves de id/rótulo mudam de grafia
entre versões e forks, daí a redundância deliberada em send_buttons/send_list.

Media fields (Image/Audio/Document/Video/Sticker) carry the file itself as a
base64 data URI ("data:image/png;base64,..."), not a URL — WuzAPI does not
fetch remote files on its own. Callers pass an already-built data URI here;
media_store.ensure_data_uri() is what turns a stored file or a public URL
into one.
"""
import base64
import json
import os
import secrets
import asyncio
import logging
import httpx
from typing import Any

logger = logging.getLogger("turismo.wuzapi")


class WuzapiError(Exception):
    pass


def generate_token() -> str:
    return secrets.token_urlsafe(32)


def _base() -> str:
    """A WuzAPI, agora falada direto: o gateway não existe mais.

    O gateway que ficava na frente da ponte fazia duas coisas por nós, e as duas voltaram para cá
    quando ele foi desligado junto com a migração para a Cloud API:

      - **limitar a taxa** de chamadas, que deixou de ser necessária sem envio
        em massa (o lembrete de agenda tem o próprio teto por passada);
      - **resolver o JID** do número no WhatsApp, que é o tratamento do nono
        dígito brasileiro. Isso sai de `POST /user/check`, que **pergunta ao
        WhatsApp** qual é o número canônico. É consulta, nunca reconstrução:
        acrescentar o 9 a um número de 8 dígitos manda a mensagem para outra
        pessoa, e é a regra que o projeto trata como literal.

    O default aponta para o loopback da própria máquina da aplicação porque é
    onde o túnel do servidor entrega a porta 8080: a WuzAPI escuta só em
    loopback no servidor de banco, e o túnel é o que a alcança.
    """
    return (os.environ.get("WUZAPI_URL") or "http://127.0.0.1:8080").rstrip("/")


def _admin_headers() -> dict:
    """O token de administração da ponte, exigido em voz alta.

    Era `os.environ[...]` cru, e o KeyError que ele levantava chegava ao
    cliente pelo caminho mais infeliz possível: quem chama isto é o
    provisionamento de uma conexão nova, que embrulha toda exceção na mensagem
    da tela. O alerta dizia **`'WUZAPI_ADMIN_TOKEN'`** e mais nada — um nome de
    variável entre aspas, sem dizer que é configuração faltando, onde ela mora
    nem que a conexão foi desfeita por causa disso.
    """
    token = os.environ.get("WUZAPI_ADMIN_TOKEN")
    if not token:
        raise WuzapiError(
            "WUZAPI_ADMIN_TOKEN não está no .env do backend. É o token de "
            "administração da ponte, e é com ele que a instância de uma "
            "conexão nova é criada — sem ele o canal não oficial não sai do "
            "lugar. O valor é o da própria WuzAPI (em /etc/wuzapi/env na "
            "máquina dela)."
        )
    return {"Authorization": token, "Content-Type": "application/json"}


def _token_headers(token: str) -> dict:
    return {"token": token, "Content-Type": "application/json"}


MAX_ATTEMPTS = 3
BACKOFF_BASE = 0.5  # waits 0.5s, 1s, 2s between attempts
RETRY_STATUS = {500, 502, 503, 504}

_client: httpx.AsyncClient | None = None


def _get_client() -> httpx.AsyncClient:
    """Client único do processo, com keep-alive.

    Um client por chamada pagava handshake TCP (e TLS, quando o gateway está
    atrás de HTTPS) em cada mensagem enviada, em cada probe do health monitor
    e em cada tentativa de retry. Aqui as conexões ficam abertas e são
    reaproveitadas. Criado sob demanda de propósito: precisa nascer dentro do
    event loop que vai usá-lo.
    """
    global _client
    if _client is None or _client.is_closed:
        _client = httpx.AsyncClient(
            timeout=15.0,
            limits=httpx.Limits(max_connections=50, max_keepalive_connections=20),
        )
    return _client


async def close_client():
    """Fecha o pool no shutdown. Sem isto o encerramento deixa sockets abertos."""
    global _client
    if _client is not None and not _client.is_closed:
        await _client.aclose()
    _client = None


async def _request(method: str, path: str, headers: dict, json: Any = None,
                   body: "_CorpoStream | None" = None) -> dict:
    """`body` envia o corpo aos pedaços, para arquivos grandes não precisarem
    caber inteiros na memória (ver send_media). Nunca vem junto com `json`.

    Ele é remontado a cada tentativa de propósito: um gerador já consumido
    mandaria corpo vazio no retry."""
    last_err: Exception | None = None
    for attempt in range(1, MAX_ATTEMPTS + 1):
        try:
            if body is None:
                extra = {"json": json}
            else:
                extra = {"content": body.pedacos()}
                # Content-Length explícito mantém o envio fora do
                # `Transfer-Encoding: chunked` que o httpx usaria sozinho
                # para um corpo iterável — nem todo servidor aceita chunked.
                headers = {**headers, "Content-Length": str(len(body))}
            r = await _get_client().request(
                method, f"{_base()}{path}", headers=headers, **extra,
            )
            if r.status_code in RETRY_STATUS:
                last_err = WuzapiError(f"WuzAPI {method} {path} -> {r.status_code}: {r.text[:200]}")
                logger.warning("WuzAPI transient %s on attempt %d/%d: %s %s",
                               r.status_code, attempt, MAX_ATTEMPTS, method, path)
            elif r.status_code >= 400:
                raise WuzapiError(f"WuzAPI {method} {path} -> {r.status_code}: {r.text[:200]}")
            else:
                try:
                    return r.json()
                except Exception:
                    return {"raw": r.text}
        except (httpx.ConnectError, httpx.ReadTimeout, httpx.WriteTimeout, httpx.RemoteProtocolError) as e:
            # O endereço entra na mensagem de propósito. A ponte escuta só em
            # loopback na máquina dela e chega aqui por túnel SSH, então o modo
            # normal de falhar é o túnel fora do ar — e "network error" sem
            # dizer para onde se tentou ir manda procurar no lugar errado.
            last_err = WuzapiError(
                f"WuzAPI {method} {_base()}{path} network error: {e} "
                "(a ponte escuta em loopback e chega por túnel: confira se a "
                "porta de WUZAPI_URL está aberta nesta máquina)"
            )
            logger.warning("WuzAPI network error on attempt %d/%d: %s", attempt, MAX_ATTEMPTS, e)

        if attempt < MAX_ATTEMPTS:
            await asyncio.sleep(BACKOFF_BASE * (2 ** (attempt - 1)))

    raise last_err or WuzapiError(f"WuzAPI {method} {path} failed after {MAX_ATTEMPTS} attempts")


# Toda instância criada pelo produto nasce com este prefixo no nome, e é ele
# que sustenta a única barreira que existe do lado da ponte.
#
# O motivo é concreto: a mesma WuzAPI pode servir outros sistemas — no caso
# real, ela é a ponte de WhatsApp de um sistema financeiro — e o token de
# administração apaga **qualquer** instância do servidor. Guardar esse token
# aqui é necessário para o cliente conectar com um clique em vez de colar id e
# token à mão, então o risco é contido em código: `nome_nosso` recusa operação
# destrutiva em nome fora do prefixo, e é por ele que passa toda exclusão.
PREFIXO_INSTANCIA = "zz_"


def nome_da_instancia(conn_id: str) -> str:
    return f"{PREFIXO_INSTANCIA}{conn_id}"


def nome_nosso(nome: str | None) -> bool:
    """O produto pode apagar esta instância?

    Falso para instância criada à mão, para a de outro sistema e para nome
    vazio. Na dúvida, não é nossa — errar para o lado de não apagar custa uma
    instância órfã; errar para o outro derruba a ponte de WhatsApp de alguém.
    """
    return bool(nome) and nome.startswith(PREFIXO_INSTANCIA)


# A chave com que a ponte assina cada webhook entra por `hmacKey` — e **só**
# por essa grafia. Observado contra a instância real em 11/09/2026: `hmacKey`,
# `hmackey` e `HmacKey` funcionam (o Go casa nome de campo sem olhar caixa),
# `hmac_key` e `hmacSecret` são ignorados em silêncio.
#
# A confusão é que `hmac_key` existe na resposta, mas ali é **booleano**: diz
# se a instância tem chave configurada, não qual é. Mandar a chave nesse nome
# volta `hmac_key: false` com `200 success`, do mesmo jeito que a URL de
# webhook na grafia errada volta vazia — a mesma armadilha, no mesmo endpoint.
#
# E o efeito é o pior possível: sem chave a ponte manda o webhook **sem
# assinatura**, e o nosso receptor é fail-closed. A conexão pareia, envia
# normalmente e recusa toda mensagem recebida com 401. Nada no caminho dá erro.
CAMPO_HMAC = "hmacKey"


async def create_instance(name: str, token: str, webhook_url: str,
                          events: str = "Message,ReadReceipt",
                          hmac_key: str | None = None) -> dict:
    """Cria a instância. `name` deve vir de `nome_da_instancia`.

    Recusa nome sem o prefixo: uma instância criada por nós que não pudesse ser
    reconhecida depois seria uma que nunca poderia ser apagada, e o servidor
    acumularia sessão de WhatsApp abandonada a cada conexão excluída.

    Devolve o registro da ponte com dois campos normalizados: `id` (string, na
    grafia que ela tiver usado) e `hmac_ativo` (a ponte confirmou que vai
    assinar?). Quem chama precisa olhar o segundo: ele não é detalhe de
    protocolo, é a diferença entre receber mensagem e descartar todas.
    """
    if not nome_nosso(name):
        raise WuzapiError(
            f"Nome de instância sem o prefixo {PREFIXO_INSTANCIA!r}: ela não "
            "poderia ser reconhecida como nossa depois"
        )
    body = {"name": name, "token": token, "webhook": webhook_url, "events": events}
    if hmac_key:
        body[CAMPO_HMAC] = hmac_key
    resp = await _request("POST", "/admin/users", _admin_headers(), body)
    dados = resp.get("data", resp)
    if not isinstance(dados, dict):
        raise WuzapiError(f"WuzAPI /admin/users: resposta inesperada ({dados!r})")
    return {**dados, "id": _id_da_resposta(dados),
            "hmac_ativo": bool(dados.get("hmac_key")) if hmac_key else False}


# O id da instância recém-criada, nas grafias em que ele já foi visto.
#
# Mesmo raciocínio do `_pick` das chaves de mídia e do laço de grafias de
# `set_webhook`: a WuzAPI muda a caixa e o nome dos campos entre versões e
# forks, e aqui o campo errado não dá erro — `.get("id")` volta `None`, vira
# string vazia no documento, e a conexão fica com a instância criada de
# verdade na ponte e **nenhuma referência a ela** do nosso lado. O efeito é o
# pior dos dois: o cartão na tela diz "sem instância" e nunca oferece o QR, e
# a instância órfã nunca é apagada porque ninguém sabe o id dela.
_CHAVES_ID_INSTANCIA = ("id", "Id", "ID", "userID", "userId", "user_id")


def _id_da_resposta(dados: dict) -> str:
    for chave in _CHAVES_ID_INSTANCIA:
        valor = dados.get(chave)
        # `0` não conta: id vazio e id zero dão o mesmo estrago, e nenhuma
        # versão da ponte numera instância a partir de zero.
        if valor not in (None, "", 0):
            return str(valor)
    raise WuzapiError(
        "WuzAPI criou a instância e não devolveu id reconhecível "
        f"(chaves recebidas: {', '.join(sorted(dados)) or 'nenhuma'}). Sem ele "
        "a conexão ficaria apontando para o nada e a instância, órfã na ponte."
    )


async def delete_instance(wuzapi_id: str, nome: str | None) -> dict:
    """Apaga a instância, **se** ela for nossa.

    O nome é exigido como argumento, e não buscado aqui, de propósito: quem
    chama tem o documento da conexão em mão, e uma consulta a mais seria uma
    chance a mais de a resposta vir de outra instância que não a que se quer
    apagar.
    """
    if not nome_nosso(nome):
        raise WuzapiError(
            f"Instância {nome!r} não foi criada pela 4turismo e não será apagada"
        )
    return await _request("DELETE", f"/admin/users/{wuzapi_id}", _admin_headers())


async def connect(token: str) -> dict:
    body = {"Subscribe": ["Message", "ReadReceipt"], "Immediate": True}
    return await _request("POST", "/session/connect", _token_headers(token), body)


async def fetch_qr(token: str) -> str | None:
    resp = await _request("GET", "/session/qr", _token_headers(token))
    return (resp.get("data") or {}).get("QRCode")


async def connection_state(token: str) -> dict:
    resp = await _request("GET", "/session/status", _token_headers(token))
    return resp.get("data", {})  # {"connected": bool, "loggedIn": bool, "jid": "...", ...}


async def get_webhook(token: str) -> dict:
    """A URL de webhook e os eventos assinados desta instância.

    `{"webhook": "...", "subscribe": ["Message", ...]}`.
    """
    resp = await _request("GET", "/webhook", _token_headers(token))
    return resp.get("data", {})


# A chave do corpo em `POST /webhook` muda de grafia entre versões e forks da
# WuzAPI, e o pior é como ela falha: observado em 09/09/2026 contra a instância
# real, um corpo com a chave errada volta **`200 success`** e grava a URL
# **vazia**. Ou seja, quem apostasse numa grafia teria uma conexão que a tela
# diz "conectada", que envia normalmente, e que nunca recebe nada — o sintoma
# mais caro deste módulo, porque não deixa erro em lugar nenhum.
#
# É a mesma classe de problema do `_pick` das chaves de mídia, e a solução é a
# mesma: tentar as grafias plausíveis. A diferença é que aqui não basta tentar,
# é preciso **conferir**, porque o próprio sucesso é mentiroso.
_CHAVES_WEBHOOK = ("webhookURL", "webhookurl", "WebhookURL", "webhook_url",
                   "webhook", "url")


async def set_webhook(token: str, url: str,
                      eventos: list[str] | None = None) -> str:
    """Configura a URL de webhook da instância e devolve a que ficou gravada.

    Levanta se nenhuma grafia funcionar. Falhar alto aqui é o ponto: esta
    chamada acontece ao conectar, onde há alguém olhando a tela, e é a única
    oportunidade de descobrir o problema antes de o cliente concluir que "o
    robô não responde".
    """
    eventos = eventos or ["Message", "ReadReceipt"]
    for chave in _CHAVES_WEBHOOK:
        await _request("POST", "/webhook", _token_headers(token),
                       {chave: url, "subscribe": eventos, "events": eventos})
        atual = (await get_webhook(token)).get("webhook") or ""
        if atual.rstrip("/") == url.rstrip("/"):
            if chave != _CHAVES_WEBHOOK[0]:
                # Registra a grafia que vingou: na próxima atualização da
                # WuzAPI ela é a primeira coisa que se quer saber.
                logger.info("WuzAPI aceitou webhook na chave %r", chave)
            return atual
    raise WuzapiError(
        "Nenhuma grafia de chave configurou o webhook na WuzAPI "
        f"(tentadas: {', '.join(_CHAVES_WEBHOOK)}). A instância aceita o POST "
        "e grava vazio, então a conexão receberia envio e nenhuma mensagem."
    )


async def check_user(token: str, numeros: list[str]) -> dict[str, str]:
    """Pergunta ao WhatsApp o JID canônico de cada número.

    É o que o gateway da ponte fazia antes de ser desligado, e é a peça que
    trata o nono dígito brasileiro sem inventá-lo: em vez de acrescentar o 9 a
    um número de 8 dígitos — que manda a mensagem para outra pessoa — pergunta
    a quem sabe. Devolve `{numero_perguntado: jid}` só para quem tem WhatsApp.

    Vale para envio a número **nunca observado** (contato importado de
    planilha). Para quem já escreveu para a empresa, o
    caminho continua sendo o `wa_id` observado, via `flow_runtime.destino`.
    """
    if not numeros:
        return {}
    resp = await _request("POST", "/user/check", _token_headers(token),
                          {"Phone": numeros})
    saida: dict[str, str] = {}
    for item in (resp.get("data") or {}).get("Users") or []:
        if not isinstance(item, dict):
            continue
        pedido = item.get("Query") or item.get("query")
        jid = item.get("JID") or item.get("jid")
        existe = item.get("IsInWhatsapp")
        if pedido and jid and existe is not False:
            saida[str(pedido)] = str(jid).split("@")[0].split(":")[0]
    return saida


async def disconnect_instance(token: str) -> dict:
    return await _request("POST", "/session/disconnect", _token_headers(token))


async def logout_instance(token: str) -> dict:
    return await _request("POST", "/session/logout", _token_headers(token))


# Onde mora o endereço da foto na resposta de `/user/avatar`, nesta ordem.
#
# Era lido só como `URL`, em caixa alta, e a instância real devolve **`url`**:
# conferido em 11/09/2026 contra um número com foto, que voltava `200` com o
# endereço presente e virava "contato sem foto" aqui. É a mesma armadilha do
# `_pick` das chaves de mídia e do laço de grafias de `set_webhook` — errar a
# caixa não dá erro nenhum, só devolve vazio, e vazio aqui é indistinguível do
# caso legítimo de quem esconde a foto. Foi por isso que passou despercebido.
_CHAVES_AVATAR = ("url", "URL", "Url")

# Último recurso: a resposta sempre traz `direct_path`, que é o caminho da
# mesma imagem sem o host. Reconstruir é seguro porque o host é fixo e o
# caminho já vem assinado (o `oh=`/`oe=` da query) — e é melhor que devolver
# vazio numa versão que deixe de mandar a URL pronta.
HOST_AVATAR = "https://pps.whatsapp.net"


async def get_avatar(token: str, phone: str) -> str | None:
    """O endereço da foto de perfil do contato, ou None.

    None é resultado normal e frequente: quem restringe a foto nas opções de
    privacidade faz a ponte responder 500, que é o caso esperado aqui e não um
    defeito de servidor.
    """
    try:
        resp = await _request("POST", "/user/avatar", _token_headers(token), {"Phone": phone})
    except WuzapiError:
        return None
    dados = resp.get("data") or {}
    for chave in _CHAVES_AVATAR:
        if (valor := dados.get(chave)):
            return str(valor)
    caminho = dados.get("direct_path") or dados.get("directPath")
    if caminho:
        logger.info("Avatar sem URL pronta; remontado a partir de direct_path")
        return f"{HOST_AVATAR}{caminho}"
    return None


async def send_text(token: str, number: str, text: str) -> dict:
    return await _request("POST", "/chat/send/text", _token_headers(token), {"Phone": number, "Body": text})


async def presence(token: str, number: str, audio: bool = False) -> None:
    """Mostra "digitando…" (ou "gravando áudio…") para o contato.

    Uma chamada só, curta e sem retry, e o erro é engolido: o indicador é
    enfeite de ritmo, e a mensagem que vem depois tem de sair do mesmo jeito.
    Passar por `_request` repetiria três vezes um enfeite e atrasaria a
    resposta justamente quando a ponte está mal. Não é preciso mandar
    `paused` depois: a mensagem enviada derruba o indicador.
    """
    try:
        await _get_client().post(
            f"{_base()}/chat/presence", headers=_token_headers(token),
            json={"Phone": number, "State": "composing",
                  "Media": "audio" if audio else ""},
            timeout=5.0)
    except Exception as e:
        logger.debug("Indicador de digitação falhou para %s: %s", number, e)


async def marcar_lida(token: str, message_id: str, chat: str) -> None:
    """Os dois tiques azuis na mensagem que o contato mandou.

    `POST /chat/markread` com `{"Id": [...], "ChatPhone": ...}`, conferido no
    handler da versão fixada em infra/compose.yaml (`MarkRead`). `chat` é o
    JID do chat **como chegou** no webhook (`Info.Chat`, que pode ser um
    `@lid`), não o número reconstruído: o recibo de leitura vai para o chat, e
    um JID diferente do da conversa marcaria nada.

    Uma chamada, sem retry, como o indicador de digitação. Levanta em falha
    para quem chama registrar; quem chama engole.
    """
    resp = await _get_client().post(
        f"{_base()}/chat/markread", headers=_token_headers(token),
        json={"Id": [message_id], "ChatPhone": chat}, timeout=5.0)
    if resp.status_code >= 400:
        raise WuzapiError(f"markread {resp.status_code}: {resp.text[:200]}")


_MEDIA_ENDPOINTS = {
    "image": ("/chat/send/image", "Image"),
    "audio": ("/chat/send/audio", "Audio"),
    "document": ("/chat/send/document", "Document"),
    "video": ("/chat/send/video", "Video"),
    "sticker": ("/chat/send/sticker", "Sticker"),
}


_DOWNLOAD_ENDPOINTS = {
    "image": "/chat/downloadimage",
    "video": "/chat/downloadvideo",
    "audio": "/chat/downloadaudio",
    "document": "/chat/downloaddocument",
}


async def download_media(token: str, mediatype: str, media_info: dict) -> bytes:
    """Fetches an *incoming* media file, decrypting it from WhatsApp's servers.

    `media_info` carries the encryption metadata straight off the message
    (Url/DirectPath/MediaKey/Mimetype/FileSHA256/FileEncSHA256/FileLength) —
    the file is stored encrypted, so it can only be pulled with those.

    Only needed when WuzAPI doesn't already ship the bytes in the webhook
    (i.e. when it runs with -skipmedia). The response envelope isn't pinned
    down in WuzAPI's docs, so the payload is unwrapped leniently: the base64
    has been seen both raw and as a data URI, under a handful of key spellings.
    """
    endpoint = _DOWNLOAD_ENDPOINTS.get(mediatype, _DOWNLOAD_ENDPOINTS["document"])
    resp = await _request("POST", endpoint, _token_headers(token), media_info)
    data = resp.get("data", resp)
    if isinstance(data, str):
        b64 = data
    else:
        b64 = next((data[k] for k in ("Data", "data", "base64", "Base64", "Media")
                    if isinstance(data.get(k), str)), None)
    if not b64:
        raise WuzapiError(f"WuzAPI {endpoint}: resposta sem conteúdo base64")
    if b64.startswith("data:"):
        b64 = b64.split(",", 1)[-1]
    try:
        return base64.b64decode(b64)
    except Exception as e:
        raise WuzapiError(f"WuzAPI {endpoint}: base64 inválido ({e})")


def _dumps(valor: Any) -> bytes:
    """Mesmos parâmetros que o httpx usa no `json=`, para o corpo montado aqui
    sair byte a byte igual ao que ele mandaria — inclusive acento, que sem
    `ensure_ascii=False` viraria \\uXXXX e mudaria o tamanho do corpo."""
    return json.dumps(valor, ensure_ascii=False, separators=(",", ":"),
                      allow_nan=False).encode("utf-8")


class _CorpoStream:
    """Corpo JSON cujo campo de mídia é lido do disco durante o envio.

    Os campos pequenos são serializados normalmente; o grande é emitido aos
    pedaços pelo `midia`, que sabe o próprio tamanho. Assim o corpo tem
    Content-Length conhecido sem nunca existir inteiro na memória.

    O que trafega é idêntico ao que o httpx montaria com `json=`: mesma
    ordem de campos, mesmos separadores, mesmo escape.
    """

    def __init__(self, campos: dict, campo_midia: str, midia):
        cabeca = bytearray(b"{")
        for chave, valor in campos.items():
            cabeca += _dumps(chave) + b":" + _dumps(valor) + b","
        cabeca += _dumps(campo_midia) + b':"'
        self.cabeca = bytes(cabeca)
        self.cauda = b'"}'
        self.midia = midia

    def __len__(self) -> int:
        return len(self.cabeca) + len(self.midia) + len(self.cauda)

    async def pedacos(self):
        yield self.cabeca
        async for pedaco in self.midia:
            yield pedaco
        yield self.cauda


async def send_media(token: str, number: str, mediatype: str, media,
                     caption: str = "", filename: str | None = None) -> dict:
    """`media` must already be a base64 data URI (see module docstring).

    Aceita a str pronta — é o caso do nó de Mídia do editor, que baixa de uma
    URL — ou um media_store.DataUriStream, quando o arquivo é nosso: aí ele é
    lido do disco durante o envio, sem passar inteiro pela memória.
    """
    endpoint, field = _MEDIA_ENDPOINTS.get(mediatype, _MEDIA_ENDPOINTS["document"])
    body = {"Phone": number, "Caption": caption}
    if endpoint == _MEDIA_ENDPOINTS["document"][0]:
        # WuzAPI names the document from FileName; without it the contact
        # receives an unnamed attachment with no extension.
        body["FileName"] = filename or "arquivo"

    if isinstance(media, str):
        return await _request("POST", endpoint, _token_headers(token), {**body, field: media})
    return await _request("POST", endpoint, _token_headers(token),
                          body=_CorpoStream(body, field, media))


# ---------------------------------------------------------------------------
# Mensagens interativas: botão de resposta rápida e lista de opções.
#
# Os tetos abaixo são do WhatsApp, não da WuzAPI: o handler dela não conta
# botão nem linha, e o WhatsApp não devolve erro quando o teto é ultrapassado —
# a mensagem simplesmente não chega ao contato. Como um Menu deixa a conversa
# esperando resposta, uma mensagem que não chega trava a conversa sem ninguém
# perceber. Por isso quem valida é o nosso lado (flow_engine.validar_menu, na
# publicação do fluxo) e quem envia degrada para texto numerado.
MAX_BOTOES = 3
MAX_ITENS_LISTA = 10
LIMITE_ROTULO_BOTAO = 20
LIMITE_ROTULO_ITEM = 24
LIMITE_DESC_ITEM = 72
LIMITE_BOTAO_LISTA = 20


def _ids_do_item(item_id: str) -> dict:
    """O id da opção nas duas grafias que o handler da WuzAPI aceita.

    Ele resolve o campo por cadeia de fallback (`id` → `buttonId`,
    `RowId` → `rowId` → ...), e qual delas é a canônica muda entre versões.
    Mandar as duas é o mesmo raciocínio do `_pick` no webhook: errar a chave
    aqui não dá erro nenhum, só faz o id voltar vazio na resposta do contato —
    e aí a escolha dele não casa com nenhuma saída do nó.

    O rótulo, ao contrário, vai numa chave só (`title`): é a que o handler
    demonstravelmente usa, e duplicar rótulo tem consequência visível (um
    fork que concatenasse mostraria o texto repetido no botão), enquanto
    duplicar id não tem nenhuma.
    """
    return {"id": item_id, "buttonId": item_id}


async def send_buttons(token: str, number: str, body: str, itens: list[dict],
                       titulo: str = "", rodape: str = "") -> dict:
    """Botões de resposta rápida (até MAX_BOTOES).

    `itens` é uma lista de {"id": ..., "label": ...}. O id é o que volta no
    webhook quando o contato toca no botão, e aqui ele é o próprio
    `sourceHandle` da saída do nó de Menu — então a resposta já aponta para a
    aresta certa, sem depender de casar texto.

    O rótulo é cortado em LIMITE_ROTULO_BOTAO pela própria WuzAPI. O corte é
    feito aqui também, de propósito: assim o que fica no histórico é o que o
    contato viu, e não um rótulo mais longo que nunca chegou à tela dele. O
    `rstrip` é porque o corte cai no meio de uma palavra na maioria das vezes,
    e sobra um espaço no fim do botão.
    """
    payload = {
        "Phone": number,
        "Body": body,
        "Buttons": [
            {"type": "reply",
             "title": (it.get("label") or "")[:LIMITE_ROTULO_BOTAO].rstrip(),
             **_ids_do_item(it["id"])}
            for it in itens[:MAX_BOTOES]
        ],
    }
    if titulo:
        payload["Title"] = titulo
    if rodape:
        payload["Footer"] = rodape
    return await _request("POST", "/chat/send/buttons", _token_headers(token), payload)


async def send_list(token: str, number: str, body: str, texto_botao: str,
                    itens: list[dict], titulo: str = "", rodape: str = "",
                    titulo_secao: str = "") -> dict:
    """Lista de opções (até MAX_ITENS_LISTA), aberta por um botão.

    `texto_botao` é o rótulo do botão que abre a lista ("Ver opções",
    "Agendamentos"...). Cada item de `itens` aceita `descricao`, que aparece
    como segunda linha da opção.

    Uma seção só: o WhatsApp permite agrupar as opções em várias seções com
    título, mas o editor não expõe isso ainda — `titulo_secao` é o título
    dessa seção única, e fica vazio quando não há um.
    """
    linhas = [
        {"title": (it.get("label") or "")[:LIMITE_ROTULO_ITEM].rstrip(),
         "desc": (it.get("descricao") or "")[:LIMITE_DESC_ITEM].rstrip(),
         "RowId": it["id"], "rowId": it["id"]}
        for it in itens[:MAX_ITENS_LISTA]
    ]
    payload = {
        "Phone": number,
        "Desc": body,
        "ButtonText": (texto_botao or "")[:LIMITE_BOTAO_LISTA],
        "Sections": [{"title": titulo_secao, "rows": linhas}],
    }
    if titulo:
        payload["TopText"] = titulo
    if rodape:
        payload["FooterText"] = rodape
    return await _request("POST", "/chat/send/list", _token_headers(token), payload)
