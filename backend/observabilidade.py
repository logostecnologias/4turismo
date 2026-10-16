"""Saber que algo quebrou antes de a agência ligar reclamando.

Duas peças, as duas opcionais e baratas:

- **Erros no Sentry**, ligados só quando `SENTRY_DSN` existe. Sem a variável
  (ou sem o pacote instalado) nada muda: o backend sobe igual, e o erro continua
  só no log. Toda exceção não tratada numa rota e todo `logger.error`/
  `logger.exception` (inclusive dos workers, que engolem a exceção para o laço
  continuar) viram um evento com a pilha.
- **`/api/health` que confere de verdade** (`estado`): o banco responde e os
  três workers estão vivos. É o endereço que o monitor de disponibilidade
  externo consulta. Um health que devolve "ok" com o Mongo fora do ar só diz
  que o processo existe, e o processo existir é o que menos importa.

**O que não vai para o Sentry**, de propósito: corpo de requisição, cookie,
cabeçalho de autenticação, IP e variáveis locais da pilha. Aqui o corpo é a
mensagem do turista, o cookie é a sessão do atendente, e uma variável local no
meio do envio pode ser o token da Cloud API decifrado. Perder esse contexto na
depuração é o preço de não mandar dado pessoal e credencial de cliente para um
serviço de terceiro.
"""

import asyncio
import logging
import os

logger = logging.getLogger("turismo.observabilidade")

# Tempo máximo do ping ao banco no health. O monitor externo costuma desistir
# em 10s; responder 503 antes disso diz "o banco não respondeu", em vez de
# "o servidor não respondeu", que mandaria procurar o problema no lugar errado.
PING_TIMEOUT = 3


def iniciar_sentry() -> bool:
    """Liga o Sentry se houver DSN. Chamado antes de criar o app."""
    dsn = (os.environ.get("SENTRY_DSN") or "").strip()
    if not dsn:
        return False
    try:
        import sentry_sdk
    except ImportError:
        logger.warning("SENTRY_DSN configurado, mas o pacote sentry-sdk não está "
                       "instalado: erros ficam só no log")
        return False
    sentry_sdk.init(
        dsn=dsn,
        environment=os.environ.get("SENTRY_AMBIENTE") or "producao",
        release=os.environ.get("SENTRY_VERSAO") or None,
        send_default_pii=False,
        max_request_body_size="never",
        include_local_variables=False,
        # Só erros. Rastreamento de desempenho fica desligado: cada requisição
        # viraria um evento, e o polling de 3s da Central sozinho esgotaria a
        # cota gratuita.
        traces_sample_rate=0,
    )
    logger.info("Sentry ligado (%s)", os.environ.get("SENTRY_AMBIENTE") or "producao")
    return True


def _workers_parados() -> list[str]:
    # Tardio: os workers puxam `flow_runtime` e companhia, e este módulo é
    # importado antes de tudo, no topo do `server.py`.
    import agenda_worker
    import health_monitor
    import media_retention

    parados = []
    for nome, mod in (("agenda", agenda_worker), ("saude_whatsapp", health_monitor),
                      ("retencao_midia", media_retention)):
        task = getattr(mod, "_task", None)
        if task is None or task.done():
            parados.append(nome)
    return parados


async def estado(db) -> tuple[bool, dict]:
    """O que o `/api/health` responde. Devolve (saudável, corpo)."""
    corpo = {"service": "4turismo"}
    try:
        await asyncio.wait_for(db.command("ping"), timeout=PING_TIMEOUT)
        corpo["banco"] = "ok"
    except Exception as e:  # timeout, conexão recusada, autenticação
        corpo["banco"] = "falhou"
        logger.error("Health: o banco não respondeu ao ping: %s", type(e).__name__)
    parados = _workers_parados()
    corpo["workers"] = "ok" if not parados else {"parados": parados}
    saudavel = corpo["banco"] == "ok" and not parados
    corpo["status"] = "ok" if saudavel else "falhou"
    return saudavel, corpo
