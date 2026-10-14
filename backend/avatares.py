"""Foto de perfil do contato, no canal não oficial.

A Cloud API não expõe avatar de quem escreve; a ponte expõe, porque fala o
protocolo do aparelho. `wuzapi_client.get_avatar` existia desde sempre e
**nada o chamava**: a chamada saiu na migração para a Cloud API e não voltou
quando o canal não oficial voltou. O efeito era `contacts.foto_url` só ser
preenchido por cadastro manual ou importação, e toda conversa aberta pela
ponte mostrar iniciais.

Quatro decisões, e as quatro vieram de armadilhas concretas:

  - **O arquivo vira nosso.** A URL que a ponte devolve aponta para os
    servidores do WhatsApp e não é estável: muda quando a pessoa troca a foto
    e deixa de valer. Guardá-la daria uma imagem que quebra sozinha depois, e
    ainda deixaria um endereço público da foto de um contato no nosso banco. É
    a mesma conclusão das mídias recebidas por um motivo diferente — lá a URL
    expira em minutos e o arquivo é cifrado; aqui ela só apodrece.

  - **Uma tentativa por contato, não uma por mensagem.** Quem esconde a foto
    devolve vazio para sempre. Sem marca, isso viraria uma consulta à ponte a
    cada mensagem recebida, indefinidamente, para não descobrir nada. A marca
    é gravada **antes** da tentativa, como o `lembrete_enviado_em` da agenda:
    gravar depois faria uma falha no meio deixar o campo em branco e o próximo
    evento tentar de novo, que é exatamente o laço que se quer evitar.

  - **Nunca por cima do que alguém pôs.** Cadastro manual e importação
    escrevem no mesmo lugar, e a foto do WhatsApp não tem por que ganhar de
    uma escolha da empresa.

  - **Nada aqui derruba a mensagem.** Ponte fora do ar, foto escondida, imagem
    gigante, host recusado pelo `net_guard`: tudo termina em log. Uma mensagem
    de contato que não vira conversa porque o avatar falhou seria trocar um
    enfeite por uma venda.

O id do arquivo fica em `contacts.foto_media_id`, separado de `foto_url`: o
primeiro é nosso e é servido por `/api/media`, o segundo é um endereço externo
que veio de planilha ou do cadastro. Misturar os dois num campo só obrigaria a
tela a adivinhar qual é qual para montar a URL certa.
"""
import logging
from datetime import datetime, timedelta, timezone

import media_store
import net_guard
import whatsapp_client as whatsapp
import wuzapi_client as wuzapi

logger = logging.getLogger("turismo.avatares")

# Uma foto de perfil do WhatsApp tem dezenas de KB. O teto é folgado de
# propósito e existe para o caso de a URL apontar para outra coisa — não é
# expectativa de tamanho.
MAX_BYTES = 2 * 1024 * 1024

# Quanto tempo até tentar de novo quando não veio foto. Curto demais devolve o
# laço que a marca existe para cortar; longo demais deixa quem acabou de pôr
# uma foto sem ela por semanas.
RETENTAR_DIAS = 7

# Marca no documento do arquivo. Serve a dois leitores, e os dois importam:
# a retenção de mídia, que não deve expirar avatar (ver `media_retention`), e a
# anonimização, que **deve** apagá-lo — é foto de uma pessoa.
USO = "avatar"


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _pode_tentar(contact: dict) -> bool:
    marca = contact.get("foto_tentada_em")
    if not marca:
        return True
    try:
        quando = datetime.fromisoformat(marca)
    except (TypeError, ValueError):
        # Marca ilegível conta como ausente: melhor uma consulta a mais que um
        # contato que nunca mais busca foto por causa de um campo torto.
        return True
    if quando.tzinfo is None:
        quando = quando.replace(tzinfo=timezone.utc)
    return datetime.now(timezone.utc) - quando >= timedelta(days=RETENTAR_DIAS)


async def atualizar(db, tenant_id: str, conn: dict, contact: dict) -> str | None:
    """Busca e guarda a foto deste contato. Devolve o id do arquivo, ou None.

    Best-effort por contrato: **não levanta**. Quem chama está no caminho de
    uma mensagem recebida.
    """
    try:
        return await _atualizar(db, tenant_id, conn, contact)
    except Exception as e:
        logger.warning("Avatar do contato %s não veio: %s", contact.get("id"), e)
        return None


async def _atualizar(db, tenant_id: str, conn: dict, contact: dict) -> str | None:
    if (conn.get("canal") or whatsapp.CANAL_CLOUD) != whatsapp.CANAL_WUZAPI:
        return None
    if contact.get("foto_media_id") or contact.get("foto_url"):
        return None
    if not _pode_tentar(contact):
        return None
    cred = whatsapp.credencial(conn)
    if not cred:
        return None

    # Antes de sair para a rede. Ver o segundo parágrafo do módulo.
    await db.contacts.update_one(
        {"id": contact["id"], "tenant_id": tenant_id},
        {"$set": {"foto_tentada_em": _now()}},
    )

    # O `wa_id` observado, nunca um número remontado: é a mesma regra do
    # envio. No Brasil o wa_id frequentemente não tem o nono dígito, e
    # "corrigi-lo" pergunta pela foto de outra pessoa.
    numero = contact.get("wa_id") or contact.get("numero_whatsapp")
    if not numero:
        return None

    url = await wuzapi.get_avatar(cred.token, numero)
    if not url:
        # Foto escondida, ou contato sem foto. Não é erro, e é o caso comum.
        return None

    # Pelo `net_guard` como qualquer URL que **nós** buscamos: ela vem de um
    # serviço externo, e um endereço apontando para dentro da rede seria uma
    # requisição nossa a um alvo escolhido de fora.
    ctype, dados = await net_guard.fetch_bytes(url, max_bytes=MAX_BYTES)

    async def _pedaco_unico():
        yield dados

    doc = await media_store.store_pedacos(
        db, tenant_id, None, "avatar", ctype or "image/jpeg", _pedaco_unico(),
        kind="image", limit=MAX_BYTES,
    )
    # O vínculo com o contato é o que permite a anonimização encontrar este
    # arquivo: ela varre por `session_id`, e avatar não tem conversa.
    await db.media_files.update_one(
        {"id": doc["id"]},
        {"$set": {"contact_id": contact["id"], "uso": USO}},
    )
    await db.contacts.update_one(
        {"id": contact["id"], "tenant_id": tenant_id},
        {"$set": {"foto_media_id": doc["id"], "updated_at": _now()}},
    )
    logger.info("Avatar guardado para o contato %s", contact["id"])
    return doc["id"]
