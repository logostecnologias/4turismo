"""Vigia das conexões de WhatsApp, no mesmo padrão start/stop dos outros workers.

O que este monitor vigia mudou por completo com a Cloud API oficial. Antes ele
existia para completar o pareamento: havia um QR na tela e uma sessão que podia
cair sozinha, então ele varria de cinco em cinco segundos perguntando à WuzAPI
se o aparelho já tinha escaneado, e de trinta em trinta se a sessão continuava
de pé. Sessão não existe mais: um número da Cloud API está registrado na conta
da Meta e não "cai".

Sobraram duas formas de uma conexão morrer em silêncio, e são as duas que este
laço procura:

  - **O token deixou de valer.** Foi revogado, expirou (token de usuário de
    sistema pode ter validade), ou perdeu permissão quando alguém mexeu no
    Business Manager do cliente. O sintoma é toda mensagem falhando com 190 ou
    0, e sem esta varredura o cliente descobre pelo contato que não foi
    respondido.

  - **A qualidade do número caiu.** A Meta rebaixa a nota (GREEN, YELLOW, RED)
    quando muitos contatos bloqueiam ou denunciam, e nota baixa reduz o limite
    diário de conversas iniciadas pela empresa até suspender o número. É o
    número do cliente, e é ele quem precisa saber a tempo de mudar o conteúdo
    do que envia.

Por isso o intervalo é de quinze minutos, e não de cinco segundos: nada disto
muda de um instante para outro, e cada varredura é uma chamada por conexão
cadastrada de toda a base.
"""
import asyncio
import logging
from datetime import datetime, timezone

from db import get_db
import whatsapp_client as whatsapp

logger = logging.getLogger("turismo.health")

INTERVALO = 15 * 60

_task: asyncio.Task | None = None


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


async def _probe(row: dict):
    db = get_db()
    conn_id = row["id"]
    cred = whatsapp.credencial(row)
    if not cred:
        return

    try:
        perfil = await whatsapp.perfil_numero(cred)
    except whatsapp.WhatsAppError as e:
        if e.codigo in whatsapp.CODIGOS_TOKEN:
            # Credencial morta: a conexão para de servir para tudo, e dizer
            # isso na tela é a diferença entre o cliente cadastrar um token
            # novo hoje e descobrir na semana que vem que ninguém foi atendido.
            logger.warning("Conexão %s com credencial inválida: %s", conn_id, e)
            await db.whatsapp_connections.update_one(
                {"id": conn_id},
                {"$set": {"status": "erro", "ultimo_erro": str(e), "updated_at": _now()}},
            )
        else:
            # Erro passageiro (rede, 5xx da Meta) não derruba o status: uma
            # conexão marcada como quebrada por causa de uma oscilação faria a
            # tela acusar defeito onde não há nenhum.
            logger.warning("Falha ao consultar a conexão %s: %s", conn_id, e)
        return

    await db.whatsapp_connections.update_one(
        {"id": conn_id},
        {"$set": {
            "status": "conectado",
            "ultimo_erro": None,
            "qualidade": perfil.get("quality_rating"),
            "nome_verificado": perfil.get("verified_name"),
            "updated_at": _now(),
        }},
    )


async def _sweep():
    db = get_db()
    async for row in db.whatsapp_connections.find({"phone_number_id": {"$ne": None}}):
        try:
            await _probe(row)
        except Exception as e:
            logger.exception("Probe falhou para a conexão %s: %s", row.get("id"), e)


async def _loop():
    logger.info("Vigia de conexões iniciado (a cada %ds)", INTERVALO)
    while True:
        try:
            await _sweep()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Varredura de saúde falhou: %s", e)
        await asyncio.sleep(INTERVALO)


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
