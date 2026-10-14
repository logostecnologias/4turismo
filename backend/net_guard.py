"""Requisições HTTP de saída com destino validado — barreira anti-SSRF.

Dois pontos do sistema deixam o cliente escolher uma URL que o **servidor**
busca: o nó de Webhook do editor de fluxos (ZZ-04) e o nó de Mídia, que baixa
uma URL pública e a converte em data URI (ZZ-05). Sem validação, um
`tenant_admin` aponta qualquer um dos dois para dentro da rede do backend
(`http://127.0.0.1:8000`, o próprio backend, `http://169.254.169.254/...`
(metadados de nuvem), `10.x`/`192.168.x` — e lê a resposta (o webhook ecoa o
corpo de volta ao contato; a mídia vira imagem/documento).

O que este módulo garante:

  - **Só http/https.** `file://`, `gopher://`, etc. são recusados.
  - **Só destino público.** O host é resolvido e **todos** os IPs retornados
    têm de ser públicos — loopback, link-local, privado, reservado, multicast
    e não-especificado são recusados. Resolver todos os registros fecha o
    truque de um A-record público ao lado de um privado.
  - **Redirect revalidado.** `follow_redirects` é desligado; cada 3xx é seguido
    à mão, revalidando o novo destino — senão um 302 para `127.0.0.1` furava a
    checagem inicial.
  - **Teto de bytes durante o streaming.** O corpo é lido aos pedaços e cortado
    ao exceder o limite; nada é materializado inteiro na memória antes de
    checar o tamanho (a regra do projeto, e a segunda metade do ZZ-05).

Limitação assumida e documentada: entre a resolução para validação e a
resolução que o httpx faz para conectar há uma janela de DNS rebinding. É um
ataque avançado (exige um resolvedor que troque de resposta em milissegundos) e
bem mais estreito que o buraco original; fechá-la por completo exige fixar o IP
no socket. Fica como endurecimento futuro; a superfície realista — apontar para
um IP interno direto ou um domínio que resolve para ele — está coberta.
"""
import ipaddress
import socket
from urllib.parse import urljoin, urlsplit

import httpx


class SSRFError(Exception):
    """Destino recusado pela política de saída. Nunca vaza detalhe interno."""


# Blocos de leitura ao baixar; o teto é aplicado somando os pedaços.
_CHUNK = 64 * 1024
_MAX_REDIRECTS = 3
_REDIRECT_STATUS = {301, 302, 303, 307, 308}


def _ip_publico(ip_txt: str) -> bool:
    try:
        ip = ipaddress.ip_address(ip_txt)
    except ValueError:
        return False
    # IPv4 mapeado em IPv6 (::ffff:10.0.0.1) volta a ser avaliado como IPv4.
    if isinstance(ip, ipaddress.IPv6Address) and ip.ipv4_mapped:
        ip = ip.ipv4_mapped
    return not (
        ip.is_private or ip.is_loopback or ip.is_link_local
        or ip.is_multicast or ip.is_reserved or ip.is_unspecified
    )


async def _validar_host(host: str, port: int):
    """Resolve o host e recusa se qualquer IP retornado não for público."""
    if not host:
        raise SSRFError("URL sem host")
    # Resolução em thread para não travar o event loop. Um IP literal também
    # passa por aqui — getaddrinfo o devolve como está e ele é validado igual.
    import asyncio
    try:
        resultados = await asyncio.to_thread(
            socket.getaddrinfo, host, port, 0, socket.SOCK_STREAM
        )
    except socket.gaierror as e:
        raise SSRFError(f"Host não resolvido: {e}") from e
    ips = {r[4][0] for r in resultados}
    if not ips:
        raise SSRFError("Host não resolvido")
    for ip in ips:
        if not _ip_publico(ip):
            raise SSRFError("Destino aponta para um endereço interno/privado")


def _checar_esquema_e_host(url: str) -> tuple[str, int]:
    partes = urlsplit(url)
    if partes.scheme not in ("http", "https"):
        raise SSRFError(f"Esquema não permitido: {partes.scheme or '(vazio)'}")
    host = partes.hostname or ""
    port = partes.port or (443 if partes.scheme == "https" else 80)
    return host, port


async def _percorrer(client: httpx.AsyncClient, method: str, url: str,
                     json_body, max_bytes: int):
    """Segue a cadeia de redirects à mão, validando cada salto, e devolve a
    resposta final já com o corpo lido sob o teto de bytes."""
    metodo = method.upper()
    corpo = json_body
    for _ in range(_MAX_REDIRECTS + 1):
        host, port = _checar_esquema_e_host(url)
        await _validar_host(host, port)
        async with client.stream(metodo, url, json=corpo) as resp:
            if resp.status_code in _REDIRECT_STATUS:
                destino = resp.headers.get("location")
                if not destino:
                    return resp.status_code, resp.headers, b""
                url = urljoin(url, destino)
                # 303 (e 302/301 na prática dos navegadores) vira GET sem corpo.
                if resp.status_code in (301, 302, 303):
                    metodo, corpo = "GET", None
                continue
            dados = bytearray()
            async for pedaco in resp.aiter_bytes(_CHUNK):
                dados.extend(pedaco)
                if len(dados) > max_bytes:
                    raise SSRFError("Resposta acima do limite de tamanho")
            return resp.status_code, resp.headers, bytes(dados)
    raise SSRFError("Redirecionamentos demais")


async def fetch_text(url: str, method: str = "GET", *, json_body=None,
                     max_bytes: int = 256 * 1024, timeout: float = 5.0):
    """Para o nó de Webhook: devolve (status, content_type, texto). O corpo é
    limitado — ele é ecoado ao contato, e uma resposta enorme não pode inchar a
    sessão nem a memória."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        status, headers, body = await _percorrer(client, method, url, json_body, max_bytes)
    ctype = headers.get("content-type", "")
    return status, ctype, body.decode("utf-8", errors="replace")


async def fetch_bytes(url: str, *, max_bytes: int, timeout: float = 30.0):
    """Para o nó de Mídia: devolve (content_type, bytes), baixados sob o teto e
    sem materializar a resposta inteira antes de checar o tamanho."""
    async with httpx.AsyncClient(timeout=timeout, follow_redirects=False) as client:
        status, headers, body = await _percorrer(client, "GET", url, None, max_bytes)
    if status >= 400:
        raise SSRFError(f"Falha ao baixar ({status})")
    return headers.get("content-type", ""), body
