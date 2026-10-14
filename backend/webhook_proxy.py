"""Superfície pública mínima para o túnel de desenvolvimento (corrige ZZ-03).

O problema: `cloudflared --url http://localhost:8000` publicava a **API
inteira** num endereço `*.trycloudflare.com` — login, `/api/admin/*`, tudo —
sem autenticação de borda. O único recurso que precisa mesmo ser alcançável de
fora é o receptor de webhook do WhatsApp; o resto não tem por que estar na
internet.

Este processo é o novo alvo do túnel, no lugar do backend. Ele repassa
**apenas** `/api/webhooks/*` (e `/api/health`, que o próprio script usa para
saber que o túnel subiu) para o backend local. Qualquer outro caminho recebe
403 e nunca chega ao backend.

Dois cuidados que não podem mudar:

  - **O corpo é repassado byte a byte.** A assinatura do webhook da Meta é
    calculada sobre os bytes crus do corpo (HMAC-SHA256 com o App Secret);
    reserializar o JSON quebraria a conferência. Aqui o corpo é lido e
    reenviado sem tocar.
  - **A assinatura viaja intacta.** O header `x-hub-signature-256` é repassado
    como qualquer outro; só os hop-by-hop (RFC 7230) são removidos.

O `GET` também passa, e precisa passar: é por ele que a Meta verifica a URL
(`hub.challenge`) na hora de cadastrar o webhook no painel do app.

Defesa em profundidade: mesmo o que passa continua exigindo assinatura válida
no backend. Este proxy remove a superfície; a assinatura autentica o que sobra.

Uso: `python webhook_proxy.py` — portas via PROXY_PORT / BACKEND_PORT.
Nota: é ferramenta de desenvolvimento. Em produção o certo é não tunelar a
partir da estação (ver ZZ-21): backend junto do banco, com a borda publicando
só a rota de webhook.
"""
import http.server
import os
import urllib.error
import urllib.request

BACKEND = f"http://127.0.0.1:{os.environ.get('BACKEND_PORT', '8000')}"
PORT = int(os.environ.get("PROXY_PORT", "8099"))

# O único caminho público. Prefixo, não igualdade: as rotas de webhook
# carregam tenant_id/connection_id no caminho.
ALLOW_PREFIXES = ("/api/webhooks/",)
# Exato: o checador do túnel bate aqui para confirmar que subiu.
ALLOW_EXACT = ("/api/health",)

# Cabeçalhos hop-by-hop não são repassados (RFC 7230 §6.1). `host` e
# `content-length` saem porque são recalculados pelo cliente/urllib.
_HOP = {
    "connection", "keep-alive", "proxy-authenticate", "proxy-authorization",
    "te", "trailers", "transfer-encoding", "upgrade", "host", "content-length",
}


def _permitido(path: str) -> bool:
    caminho = path.split("?", 1)[0]
    return caminho in ALLOW_EXACT or any(caminho.startswith(p) for p in ALLOW_PREFIXES)


class Handler(http.server.BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def _responde(self, status: int, corpo: bytes, content_type: str = "application/json"):
        self.send_response(status)
        self.send_header("Content-Type", content_type)
        self.send_header("Content-Length", str(len(corpo)))
        self.end_headers()
        self.wfile.write(corpo)

    def _repassa(self):
        if not _permitido(self.path):
            # 403 sem detalhe: nada aqui fora do webhook é assunto de quem vem
            # pela internet. O backend nem é consultado.
            self._responde(403, b'{"detail":"Forbidden"}')
            return

        tamanho = int(self.headers.get("Content-Length") or 0)
        corpo = self.rfile.read(tamanho) if tamanho else None
        cabecalhos = {k: v for k, v in self.headers.items() if k.lower() not in _HOP}

        req = urllib.request.Request(
            f"{BACKEND}{self.path}", data=corpo, headers=cabecalhos, method=self.command
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                dados = resp.read()
                self.send_response(resp.status)
                for k, v in resp.headers.items():
                    if k.lower() not in _HOP:
                        self.send_header(k, v)
                self.send_header("Content-Length", str(len(dados)))
                self.end_headers()
                self.wfile.write(dados)
        except urllib.error.HTTPError as e:
            # Erro HTTP do backend (4xx/5xx): repassa status e corpo como vieram.
            dados = e.read() or b""
            self.send_response(e.code)
            self.send_header("Content-Type", e.headers.get("Content-Type", "application/json"))
            self.send_header("Content-Length", str(len(dados)))
            self.end_headers()
            self.wfile.write(dados)
        except Exception:
            # Backend fora do ar ou timeout: o gateway tem fila+retry próprios.
            self._responde(502, b'{"detail":"Bad gateway"}')

    # HEAD e OPTIONS entram aqui de propósito: o Cloudflare os dispara em
    # probes, e sem handler o BaseHTTPRequestHandler responde 501 *antes* do
    # allowlist. Bloqueia igual (nunca chega ao backend), mas passando pelo
    # `_repassa` a resposta de um caminho barrado é sempre um 403 limpo.
    do_GET = _repassa
    do_POST = _repassa
    do_PUT = _repassa
    do_PATCH = _repassa
    do_DELETE = _repassa
    do_HEAD = _repassa
    do_OPTIONS = _repassa

    def log_message(self, *args):
        pass  # silencioso; o backend já loga o que importa


if __name__ == "__main__":
    print(f"webhook-proxy: publico 127.0.0.1:{PORT} -> {BACKEND} (so /api/webhooks/*)", flush=True)
    http.server.ThreadingHTTPServer(("127.0.0.1", PORT), Handler).serve_forever()
