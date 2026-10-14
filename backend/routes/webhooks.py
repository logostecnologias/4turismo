"""Receptor de webhook da Cloud API oficial (Meta).

Três coisas mudaram de forma estrutural em relação ao receptor anterior, e as
três aparecem no desenho deste arquivo:

  - **É um endereço só, para todas as empresas.** A Meta entrega os eventos de
    todas as contas assinadas ao app num único callback, configurado no painel
    do app. Não dá para carregar `tenant_id`/`connection_id` no caminho, como
    a URL por conexão fazia antes. Quem diz de qual número o evento é são os
    metadados do próprio payload (`value.metadata.phone_number_id`), e é por
    ele que a conexão é encontrada. Um `phone_number_id` desconhecido é
    respondido com 200 e ignorado: devolver erro faria a Meta repetir por sete
    dias um evento que nunca vai casar com nada.

  - **A verificação é em duas etapas e as duas são obrigatórias.** O `GET` com
    `hub.challenge` acontece uma vez, quando o endereço é cadastrado no painel;
    sem ele a Meta não aceita a URL. O `POST` de cada evento vem assinado em
    `X-Hub-Signature-256` com o App Secret sobre os **bytes crus** do corpo.
    Reserializar o JSON antes de conferir quebraria a assinatura, então o corpo
    é lido uma vez, conferido e só então interpretado.

  - **Chega status de entrega.** `sent`, `delivered`, `read` e `failed` de cada
    mensagem que nós mandamos, ligados à bolha pelo `wamid` gravado no envio.
    É o que a API não oficial nunca deu, e é o que faz "a mensagem saiu mas o
    contato não recebeu" ser uma pergunta respondível.

A resposta é sempre 200 e rápida: o processamento vai para background. A Meta
repete qualquer coisa diferente de 200 com frequência decrescente por até sete
dias, e uma fila de repetição em cima de um processamento lento é como um pico
de mensagens vira uma avalanche.
"""
import hmac
import hashlib
import logging
import os
from datetime import datetime, timezone

from fastapi import APIRouter, Request, HTTPException, BackgroundTasks
from fastapi.responses import PlainTextResponse

from db import get_db
from flow_runtime import handle_incoming_message

router = APIRouter(prefix="/api/webhooks", tags=["webhooks"])

logger = logging.getLogger("turismo.webhooks")


def _now():
    return datetime.now(timezone.utc).isoformat()


def _validar_assinatura(corpo: bytes, request: Request):
    """Confere `X-Hub-Signature-256` contra o App Secret.

    Fail-closed, como era antes: sem o segredo configurado o webhook
    alimentaria o motor de conversas sem autenticação nenhuma, e daria para
    forjar mensagem em qualquer conversa de qualquer empresa. A ausência
    recusa explicitamente em vez de desligar a checagem em silêncio, e o boot
    também avisa (ver `server.on_startup`).
    """
    segredo = os.environ.get("META_APP_SECRET")
    if not segredo:
        logger.error("META_APP_SECRET ausente: webhooks recusados até configurá-lo")
        raise HTTPException(status_code=503, detail="Webhook signing not configured")
    recebida = request.headers.get("x-hub-signature-256", "")
    if not recebida.startswith("sha256="):
        raise HTTPException(status_code=401, detail="Invalid signature")
    esperada = hmac.new(segredo.encode(), corpo, hashlib.sha256).hexdigest()
    if not hmac.compare_digest(recebida[7:], esperada):
        raise HTTPException(status_code=401, detail="Invalid signature")


# Cada tipo de mídia vira um nó próprio da mensagem. Figurinha entra como
# imagem: é webp, o chat exibe igual e o download é o mesmo.
_TIPOS_DE_MIDIA = {
    "image": "image",
    "video": "video",
    "audio": "audio",
    "document": "document",
    "sticker": "image",
}


def _extrair_midia(msg: dict) -> dict | None:
    """O anexo, reduzido ao que o runtime precisa para buscá-lo depois.

    Bem mais simples que na API anterior: não há metadado de cifra nem URL
    expirando aqui, só um `media_id`. O binário é buscado na hora pelo
    runtime, com duas idas à Meta (id para URL, URL para bytes).
    """
    for tipo, kind in _TIPOS_DE_MIDIA.items():
        no = msg.get(tipo)
        if not isinstance(no, dict) or not no.get("id"):
            continue
        return {
            "kind": kind,
            "media_id": no["id"],
            "mime": no.get("mime_type"),
            "filename": no.get("filename"),
            "caption": no.get("caption") or "",
            # Áudio gravado na hora chega com `voice: true`. Guardado porque
            # muda o que a bolha deve dizer: recado de voz não é "um arquivo
            # de áudio anexado".
            "voz": bool(no.get("voice")),
        }
    return None


def _extrair_escolha(msg: dict) -> dict | None:
    """A opção que o contato tocou.

    Três formatos, e aqui os três trazem id **e** rótulo, ao contrário da API
    anterior, em que a resposta chegava só com o id e o rótulo tinha de ser
    recuperado do menu que fez a pergunta:

      - `interactive.button_reply`: botão de resposta rápida.
      - `interactive.list_reply`: item de lista.
      - `button`: botão de resposta rápida de um **modelo**, que não é
        interativo e chega com `payload` (o id) e `text` (o rótulo). É o caso
        de "Confirmar"/"Cancelar" num lembrete enviado por template, então
        ignorá-lo faria justamente a resposta ao lembrete se perder.
    """
    interativo = msg.get("interactive")
    if isinstance(interativo, dict):
        for chave in ("button_reply", "list_reply"):
            no = interativo.get(chave)
            if isinstance(no, dict) and no.get("id"):
                return {"id": no["id"], "titulo": no.get("title") or ""}

    botao = msg.get("button")
    if isinstance(botao, dict) and (botao.get("payload") or botao.get("text")):
        return {"id": botao.get("payload") or "", "titulo": botao.get("text") or ""}
    return None


def _texto_da_mensagem(msg: dict, midia: dict | None, escolha: dict | None) -> str:
    """O que vai para o histórico como texto da mensagem.

    Ordem deliberada: o que o contato escreveu vem primeiro; depois a legenda
    do anexo; depois o rótulo do que ele tocou (nunca o id, que é
    identificador interno e faria o atendente ler código na bolha). Tipos sem
    texto ganham uma descrição em vez de bolha vazia, porque "o contato
    respondeu alguma coisa que não aparece" é pior que uma linha explicando o
    que chegou.
    """
    tipo = msg.get("type")
    if tipo == "text":
        return (msg.get("text") or {}).get("body") or ""
    if escolha:
        return escolha.get("titulo") or escolha.get("id") or ""
    if midia and midia.get("caption"):
        return midia["caption"]
    if tipo == "location":
        loc = msg.get("location") or {}
        nome = loc.get("name") or loc.get("address")
        coords = f"{loc.get('latitude')}, {loc.get('longitude')}"
        return f"[localização] {nome or coords}"
    if tipo == "contacts":
        nomes = [((c.get("name") or {}).get("formatted_name") or "")
                 for c in (msg.get("contacts") or [])]
        return "[contato] " + ", ".join([n for n in nomes if n])
    if tipo == "reaction":
        return (msg.get("reaction") or {}).get("emoji") or "[reação]"
    if tipo == "order":
        return "[pedido do catálogo]"
    if tipo == "unsupported":
        # A Meta manda isto para tipo que a Cloud API não entrega (enquete,
        # figurinha animada em alguns casos, mensagem apagada). Sem a linha, a
        # conversa mostraria um vazio no lugar de algo que o contato mandou.
        return "[mensagem não suportada pelo WhatsApp Business]"
    return ""


@router.get("/whatsapp")
async def verificar_webhook(request: Request):
    """Handshake de verificação do painel da Meta.

    Acontece uma vez, quando a URL é cadastrada (e de novo a cada mudança). A
    resposta tem de ser o `hub.challenge` **em texto puro**: devolvido como
    JSON, com aspas em volta, a Meta recusa a URL. É o erro mais comum desta
    etapa e não dá nenhuma mensagem útil no painel.
    """
    params = request.query_params
    esperado = os.environ.get("META_WEBHOOK_VERIFY_TOKEN")
    if not esperado:
        logger.error("META_WEBHOOK_VERIFY_TOKEN ausente: verificação recusada")
        raise HTTPException(status_code=503, detail="Webhook verification not configured")
    if params.get("hub.mode") != "subscribe" or params.get("hub.verify_token") != esperado:
        raise HTTPException(status_code=403, detail="Verificação recusada")
    return PlainTextResponse(params.get("hub.challenge") or "")


@router.post("/whatsapp")
async def receber_webhook(request: Request, bg: BackgroundTasks):
    corpo = await request.body()
    _validar_assinatura(corpo, request)

    try:
        payload = await request.json()
    except Exception:
        raise HTTPException(status_code=400, detail="Invalid JSON")

    if payload.get("object") != "whatsapp_business_account":
        return {"ok": True, "ignored": payload.get("object")}

    db = get_db()
    tratados = 0
    for entry in payload.get("entry") or []:
        for change in entry.get("changes") or []:
            if change.get("field") != "messages":
                # Outros campos (qualidade do número, status de modelo, alerta
                # de conta) chegam no mesmo endereço. Registrados e ignorados:
                # tratá-los é trabalho próprio, e recusar faria a Meta repetir.
                logger.info("Webhook de campo %s ignorado", change.get("field"))
                continue
            tratados += await _processar_change(db, change.get("value") or {}, bg)
    return {"ok": True, "eventos": tratados}


async def _processar_change(db, value: dict, bg: BackgroundTasks) -> int:
    phone_number_id = (value.get("metadata") or {}).get("phone_number_id")
    if not phone_number_id:
        return 0
    conn = await db.whatsapp_connections.find_one({"phone_number_id": str(phone_number_id)})
    if not conn:
        # Número que não é de nenhuma conexão nossa: pode ser uma conta que o
        # cliente removeu da plataforma sem tirar a assinatura do app. 200 e
        # segue, senão a Meta repete por uma semana.
        logger.info("Webhook de phone_number_id desconhecido: %s", phone_number_id)
        return 0

    tenant_id, conn_id = conn["tenant_id"], conn["id"]
    await db.whatsapp_connections.update_one(
        {"id": conn_id}, {"$set": {"last_webhook_event_at": _now()}}
    )

    eventos = 0

    # O nome de perfil vem uma vez por evento, fora da mensagem, e casa com a
    # mensagem pelo `wa_id`. É o único lugar onde ele aparece, e é de graça:
    # buscar o nome do contato numa chamada à parte custaria uma ida por
    # conversa nova.
    perfis = {c.get("wa_id"): ((c.get("profile") or {}).get("name") or None)
              for c in (value.get("contacts") or []) if c.get("wa_id")}

    for msg in value.get("messages") or []:
        numero = msg.get("from")
        wamid = msg.get("id")
        if not numero or not wamid:
            continue
        midia = _extrair_midia(msg)
        escolha = _extrair_escolha(msg)
        texto = _texto_da_mensagem(msg, midia, escolha)
        if not texto and not midia and not escolha:
            continue
        bg.add_task(handle_incoming_message, tenant_id, numero, texto, wamid,
                    conn_id, perfis.get(numero), midia, escolha)
        eventos += 1

    for status in value.get("statuses") or []:
        bg.add_task(_registrar_status, tenant_id, status)
        eventos += 1

    for erro in value.get("errors") or []:
        # Erro de conta (token revogado, número suspenso). Não pertence a
        # nenhuma conversa, então vai para o log e para a conexão, que é onde
        # a tela mostra "algo está errado com este número".
        logger.error("Erro de conta na conexão %s: %s", conn_id, erro)
        await db.whatsapp_connections.update_one(
            {"id": conn_id},
            {"$set": {"status": "erro",
                      "ultimo_erro": (erro.get("error_data") or {}).get("details")
                      or erro.get("title") or str(erro.get("code")),
                      "updated_at": _now()}},
        )

    return eventos


# Ordem dos estados de entrega. Um `delivered` que chega depois de um `read`
# (a Meta não garante ordem) não pode rebaixar a bolha de "lida" para
# "entregue": a comparação é por posição, não por chegada.
_ORDEM_STATUS = {"enviada": 0, "entregue": 1, "lida": 2, "falha": 3}
_TRADUZ_STATUS = {"sent": "enviada", "delivered": "entregue",
                  "read": "lida", "failed": "falha"}


async def _registrar_status(tenant_id: str, status: dict):
    """Ata o status de entrega à mensagem que já está no histórico.

    Chave é o `wamid`, gravado em `meta.wamid` no envio. Falha carrega o
    código de erro da Meta, que é o que distingue "número não tem WhatsApp"
    (131026) de "a janela de 24h fechou" (131047), duas coisas que o atendente
    resolve de formas opostas.
    """
    wamid = status.get("id")
    novo = _TRADUZ_STATUS.get(status.get("status") or "")
    if not wamid or not novo:
        return
    db = get_db()

    mudanca: dict = {"meta.entrega": novo, "meta.entrega_em": _now()}
    if novo == "falha":
        erros = status.get("errors") or []
        primeiro = erros[0] if erros else {}
        mudanca["meta.entrega_erro"] = {
            "codigo": primeiro.get("code"),
            "detalhe": (primeiro.get("error_data") or {}).get("details")
            or primeiro.get("title"),
        }

    atual = await db.messages_log.find_one(
        {"tenant_id": tenant_id, "meta.wamid": wamid}, {"_id": 0, "meta": 1}
    )
    if not atual:
        return
    anterior = (atual.get("meta") or {}).get("entrega")
    if anterior and _ORDEM_STATUS.get(novo, 0) <= _ORDEM_STATUS.get(anterior, 0):
        return
    await db.messages_log.update_one(
        {"tenant_id": tenant_id, "meta.wamid": wamid}, {"$set": mudanca}
    )
