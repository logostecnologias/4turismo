"""Real-conversation runtime. Uses the same node-processing logic as the
simulator in flow_engine.py, but persists sessions in MongoDB and sends
real messages through the official Cloud API (see whatsapp_client).
"""
import asyncio
import logging
import re
import unicodedata
import uuid
from contextlib import asynccontextmanager
from datetime import datetime, timedelta, timezone
from db import get_db
from flow_engine import (
    _find_node, _find_next, _find_start, _interp, _process_node,
    FORMATO_BOTOES, FORMATO_LISTA, FORMATO_TEXTO, resolver_escolha_menu,
)
import contatos_csv
import avatares
import media_store
import whatsapp_client as whatsapp
import ai_agents

logger = logging.getLogger("turismo.flow_runtime")


def _now():
    return datetime.now(timezone.utc).isoformat()


# Como o contato pede para sair da lista de mensagens em massa. Comparação com
# a mensagem **inteira**, não "contém": "quero cancelar meu pedido" é
# atendimento, não descadastro, e marcar esse cliente por engano é pior do que
# exigir a palavra exata.
OPT_OUT_PALAVRAS = {
    "sair", "parar", "pare", "cancelar", "descadastrar", "descadastro",
    "remover", "stop", "sairdalista", "naoquero", "naoqueromais",
    "naoqueroreceber", "naoquerorecebermais", "semmensagens", "naomeenviemais",
}


def _normaliza_opt_out(texto: str) -> str:
    """Minúsculas, sem acento e sem pontuação/espaço: "SAIR!", "sair" e
    "Sair " são a mesma intenção."""
    limpo = unicodedata.normalize("NFKD", texto or "")
    limpo = "".join(c for c in limpo if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", limpo.lower())


def pediu_opt_out(texto: str) -> bool:
    return _normaliza_opt_out(texto) in OPT_OUT_PALAVRAS


# A Cloud API não expõe foto de perfil de quem escreve: a WuzAPI expunha
# porque falava o protocolo do aparelho. `contacts.foto_url` continua no
# schema (importação e cadastro manual podem preenchê-lo), mas nada aqui o
# preenche mais, e a Central mostra as iniciais do contato.


async def _marcar_lida_bg(cred, wamid: str):
    """Confirmação de leitura, sem atrapalhar a resposta.

    Em tarefa própria de propósito: é uma ida à rede que não muda nada do
    atendimento, e esperá-la atrasaria a resposta do bot em uma volta inteira.
    """
    try:
        await whatsapp.marcar_lida(cred, wamid)
    except Exception as e:
        logger.debug("Não foi possível marcar %s como lida: %s", wamid, e)


async def _ingest_incoming_media(db, tenant_id, session_id, cred, media: dict) -> dict | None:
    """Traz para o nosso armazenamento a mídia que o contato mandou.

    São duas idas à Meta: o `media_id` do webhook vira uma URL temporária (5
    minutos) e a URL é baixada com o mesmo token. Guardar a URL não serviria
    de nada, e o id só vale 7 dias, então o binário é buscado agora e vira
    arquivo nosso, servido por /api/media como qualquer outro.

    O download vai direto para o disco, sem passar inteiro pela memória.

    Falha aqui não derruba a mensagem: devolve None e o texto ou legenda ainda
    entra no histórico, só sem o anexo.
    """
    if not cred or not media.get("media_id"):
        return None
    try:
        info, pedacos = await whatsapp.baixar_midia(cred, media["media_id"])
        doc = await media_store.store_pedacos(
            db, tenant_id, session_id,
            media.get("filename"), media.get("mime") or info.get("mime_type"), pedacos,
            kind=media["kind"], limit=media_store.HARD_MAX_BYTES,
        )
        return media_store.public_payload(doc)
    except Exception as e:
        logger.warning("Falha ao baixar mídia recebida na sessão %s: %s", session_id, e)
        return None


def last_message_preview(doc: dict) -> dict:
    """Recorte da mensagem que fica espelhado na sessão para a lista de
    conversas — só o que o card mostra.

    Guardar o documento inteiro faria a sessão carregar um anexo de metadados
    a cada mensagem; guardar nada obrigava a Central a redescobrir a última
    mensagem de cada conversa a cada atualização de tela.
    """
    media = (doc.get("meta") or {}).get("media") or None
    return {
        "content": doc.get("content"),
        "type": doc.get("type"),
        "direction": doc.get("direction"),
        "timestamp": doc.get("timestamp"),
        # Só o `kind`: é o único campo do anexo que a prévia usa, para dizer
        # "📷 Imagem" quando a mensagem não tem legenda.
        "meta": {"media": {"kind": media.get("kind")}} if media else {},
    }


async def log_message(db, session_id, tenant_id, direction, content, mtype="text", meta=None) -> dict:
    """Grava a mensagem e atualiza a prévia na sessão, sempre juntas.

    Todo caminho que escreve em `messages_log` passa por aqui justamente para
    que os dois lados não possam divergir. Devolve o documento gravado; quando
    a sessão ainda não existe (primeira mensagem de uma conversa nova), o
    update não casa nada e quem cria a sessão embute a prévia no insert.
    """
    doc = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "tenant_id": tenant_id,
        "direction": direction,
        "content": content,
        "type": mtype,
        "meta": meta or {},
        "timestamp": _now(),
    }
    await db.messages_log.insert_one(doc)
    doc.pop("_id", None)
    await db.conversation_sessions.update_one(
        {"id": session_id, "tenant_id": tenant_id},
        {"$set": {"last_message": last_message_preview(doc)}},
    )
    return doc


async def _log_msg(db, session_id, tenant_id, direction, content, mtype="text", meta=None):
    return await log_message(db, session_id, tenant_id, direction, content, mtype, meta)


def _itens_do_menu(o: dict) -> list[dict]:
    """As opções com id.

    Output antigo (de uma sessão que atravessou o deploy) só tem `options`, com
    rótulos e sem id — daí o id sintetizado na mesma forma que
    `flow_engine.ids_das_opcoes` usa.
    """
    if o.get("itens"):
        return o["itens"]
    return [{"id": f"opt_{i}", "label": rotulo, "descricao": ""}
            for i, rotulo in enumerate(o.get("options") or [])]


def _menu_em_texto(o: dict) -> str:
    """O menu como lista numerada — o formato que chega sempre.

    É o que sai no formato `texto` e é também a degradação dos outros dois. E é
    sempre ele que vai para o histórico: o atendente que abre a conversa na
    Central precisa ver quais opções o contato recebeu, e uma bolha dizendo
    apenas "Escolha uma opção" não diz nada.
    """
    cabeca = "\n".join([p for p in (o.get("titulo"), o.get("content")) if p])
    linhas = [cabeca, ""]
    for i, item in enumerate(_itens_do_menu(o)):
        rotulo = item.get("label") or ""
        if item.get("descricao"):
            rotulo = f"{rotulo} — {item['descricao']}"
        linhas.append(f"{i+1}. {rotulo}")
    if o.get("rodape"):
        linhas += ["", o["rodape"]]
    return "\n".join(linhas)


def janela_aberta(session: dict | None, canal: str | None = None) -> bool:
    """A janela de atendimento de 24h desta conversa ainda está aberta?

    Mensagem livre (texto, mídia, menu) só sai enquanto ela estiver; fora
    dela, só modelo aprovado. A marca é gravada a cada mensagem **recebida**
    (`janela_expira_em`), porque é o contato quem abre e renova a janela.

    Conversa sem a marca conta como fechada. É o caso das que já existiam
    antes desta mudança: presumir aberto faria o primeiro envio de cada uma
    delas falhar com 131047 depois de já ter aparecido como enviada na tela.

    **No canal `wuzapi` a janela não existe**, e por isso `cred` entra aqui.
    A janela de 24h é regra da Meta aplicada pela Cloud API, não uma
    propriedade da conversa: no protocolo não oficial não há nada que recuse
    texto livre por tempo. Devolver `False` ali faria a Central mostrar
    "janela fechada" numa conexão em que mandar funciona — e, pior, empurraria
    o lembrete para `send_template`, que naquele canal não existe.

    O parâmetro é o **canal** e não a credencial porque os pontos de chamada
    não têm a mesma coisa em mão: no motor de fluxos existe uma `Credencial`
    (`cred.canal`), na ficha de contato e no lembrete de agenda existe o
    documento da conexão (`conn["canal"]`). Uma string atende os dois sem
    obrigar quem só tem o documento a decifrar o token para consultar o canal.

    É opcional para poder ser adotado aos poucos: sem ele, o comportamento é o
    de sempre, que é o conservador (a janela vale).
    """
    if canal == whatsapp.CANAL_WUZAPI:
        return True
    if not session:
        return False
    expira = session.get("janela_expira_em")
    return bool(expira) and expira > _now()


async def _enviar_menu(db, tenant_id, cred, number, session_id, o):
    """Manda o Menu no formato escolhido no editor, caindo para texto se não der.

    A degradação é o ponto do desenho, não um detalhe. O nó de Menu deixa a
    conversa em `awaiting`: se a mensagem que faz a pergunta não chega, o
    contato fica sem saber que devia responder e a conversa trava — e o
    WhatsApp descarta mensagem interativa fora dos tetos **sem devolver erro**.
    Receber a mesma pergunta como lista numerada é muito melhor que não receber.

    São dois os motivos para degradar, e os dois ficam registrados em `meta`: a
    configuração estourou um teto (`problemas`, calculado no motor) ou o envio
    falhou de verdade. Na Cloud API a mensagem interativa fora dos tetos volta
    com erro 100 em vez de sumir calada, o que é uma melhora, mas a degradação
    continua sendo o que evita a conversa travar esperando uma pergunta que
    nunca chegou.
    """
    formato = o.get("formato") or FORMATO_TEXTO
    itens = _itens_do_menu(o)
    texto = _menu_em_texto(o)
    problemas = o.get("problemas") or []
    motivo = "; ".join(problemas) if problemas else None

    if formato in (FORMATO_BOTOES, FORMATO_LISTA) and itens and not problemas:
        try:
            if formato == FORMATO_BOTOES:
                wamid = await whatsapp.send_buttons(
                    cred, number, o.get("content", ""), itens,
                    titulo=o.get("titulo", ""), rodape=o.get("rodape", ""))
            else:
                wamid = await whatsapp.send_list(
                    cred, number, o.get("content", ""),
                    o.get("botao_texto") or "Ver opções", itens,
                    titulo=o.get("titulo", ""), rodape=o.get("rodape", ""),
                    titulo_secao=o.get("titulo_secao", ""))
            await _log_msg(db, session_id, tenant_id, "enviada", texto,
                           mtype="menu", meta={"menu": {"formato": formato},
                                               "wamid": wamid})
            return
        except Exception as e:
            # Qualquer falha, não só WhatsAppError: `_request` deixa escapar
            # erro de httpx que não está na lista de retry (PoolTimeout, por
            # exemplo), e aí a pergunta se perderia. Logado como exceção porque
            # um erro que não seja de rede é bug e precisa continuar visível.
            motivo = str(e)
            logger.exception("Menu interativo (%s) falhou, enviando como texto",
                             formato)

    meta = {"formato": FORMATO_TEXTO}
    if formato != FORMATO_TEXTO:
        meta.update({"pedido": formato, "degradado": motivo})
    wamid = await whatsapp.send_text(cred, number, texto)
    await _log_msg(db, session_id, tenant_id, "enviada", texto,
                   mtype="menu", meta={"menu": meta, "wamid": wamid})


async def _send_outputs(db, tenant_id, cred, number, session_id, outputs):
    for o in outputs:
        try:
            if o["type"] == "text":
                wamid = await whatsapp.send_text(cred, number, o["content"])
                await _log_msg(db, session_id, tenant_id, "enviada", o["content"],
                               meta={"wamid": wamid})
            elif o["type"] == "menu":
                await _enviar_menu(db, tenant_id, cred, number, session_id, o)
            elif o["type"] == "media" and o.get("doc"):
                # Arquivo nosso, já em disco: é o caso das fotos do catálogo do
                # agente de IA. Sobe para a Meta uma vez e reaproveita o id nos
                # envios seguintes, e o `public_payload` no meta é o que faz a
                # bolha renderizar pelo /api/media em vez de um link cru.
                doc = o["doc"]
                media_id = await media_store.id_na_meta(db, doc, cred)
                wamid = await whatsapp.send_media(
                    cred, number, doc.get("kind", "image"), media_id=media_id,
                    caption=o.get("caption", ""), filename=doc.get("filename"))
                await _log_msg(db, session_id, tenant_id, "enviada", o.get("caption", ""),
                               mtype=doc.get("kind", "image"),
                               meta={"media": media_store.public_payload(doc),
                                     "wamid": wamid})
            elif o["type"] == "media":
                # O nó de Mídia guarda uma URL pública, e a Cloud API busca a
                # URL sozinha: passar `link` evita baixar o arquivo aqui só
                # para subi-lo de novo. Quem tem de alcançar a URL é a Meta,
                # então endereço interno simplesmente não funciona, e é por
                # isso que este caminho não passa mais pelo net_guard.
                url = o.get("url", "")
                if not url.startswith(("http://", "https://")):
                    raise media_store.MediaError(f"URL de mídia inválida: {url[:40]}")
                wamid = await whatsapp.send_media(
                    cred, number, o.get("media_type", "image"), link=url,
                    caption=o.get("caption", ""),
                    filename=url.split("/")[-1].split("?")[0] or None)
                await _log_msg(db, session_id, tenant_id, "enviada",
                               o.get("caption") or o.get("url", ""),
                               mtype=o.get("media_type", "image"),
                               meta={"media": {"kind": o.get("media_type", "image"),
                                               "url": o.get("url", "")},
                                     "wamid": wamid})
            elif o["type"] == "system":
                await _log_msg(db, session_id, tenant_id, "sistema", o["content"], mtype="system")
        except Exception as e:
            await _log_msg(db, session_id, tenant_id, "erro", f"Falha envio: {e}", mtype="error")


async def _run_from(node_id, flow, session, outputs, guard=0):
    while node_id and guard < 50:
        node = _find_node(flow["nodes"], node_id)
        if node is None:
            break
        next_id = await _process_node(node, flow, session, outputs)
        session["node_atual_id"] = node["id"]
        if next_id is None:
            break
        node_id = next_id
        guard += 1


async def _load_bot_flow(db, tenant_id: str, bot_id: str | None, require_active: bool = True):
    """Carrega bot + fluxo publicado + nó de início. Devolve None se qualquer
    peça faltar (bot inexistente/pausado, fluxo não publicado, fluxo sem
    início) — quem chama decide o que fazer com a ausência."""
    if not bot_id:
        return None
    q = {"id": bot_id, "tenant_id": tenant_id}
    if require_active:
        q["status"] = "ativo"
    bot = await db.bots.find_one(q)
    if not bot:
        return None
    flow = await db.flows.find_one({"id": bot["main_flow_id"]}, {"_id": 0})
    if not flow or not flow.get("published_at"):
        return None
    start = _find_start(flow["nodes"])
    if not start:
        return None
    return bot, flow, start


def _keyword_matches(start: dict, text: str) -> bool:
    keyword = (start.get("data", {}).get("trigger_keyword") or "").strip().lower()
    if not keyword:
        return False
    return keyword in (text or "").strip().lower()


async def _pick_bot_for_new_session(db, tenant_id: str, conn: dict, text: str):
    """Escolhe quem atende uma conversa nova.

    Isolamento por conexão é total: só os bots encaixados nos slots DESTA
    conexão podem responder. Não existe fallback pro bot padrão do tenant nem
    varredura por bots soltos — se o slot está vazio, ninguém responde e a
    conversa nasce direto em "aguardando", pra um humano assumir.

    Cada conexão tem no máximo um bot de cada tipo. O de palavra-chave tem
    preferência: se a mensagem contiver a palavra configurada, ele responde.
    Não contendo, entra o de primeira mensagem.

    Antes desta função o bot era escolhido ANTES do gatilho ser avaliado, então
    o gatilho só conseguia vetar o bot já escolhido, nunca convocar outro: um
    bot de palavra-chave só rodava se fosse justamente o padrão da conexão, e
    nesse caso o de primeira mensagem nunca rodava.
    """
    async def slot(trigger: str):
        # O gatilho fica espelhado no documento do bot justamente pra esta
        # busca: sem isso, seria preciso abrir o fluxo de cada bot da conexão
        # a cada mensagem só pra descobrir o tipo de cada um.
        b = await db.bots.find_one({
            "tenant_id": tenant_id,
            "connection_id": conn["id"],
            "trigger": trigger,
            "status": "ativo",
        })
        return await _load_bot_flow(db, tenant_id, b["id"]) if b else None

    keyword_bot = await slot("keyword")
    if keyword_bot and _keyword_matches(keyword_bot[2], text):
        # O quarto item diz que a escolha veio de uma palavra-chave digitada
        # pelo contato. É o que permite o agente de IA em modo "primeiro" ceder
        # a vez: quem digitou a palavra pediu aquele fluxo, não uma conversa.
        return (*keyword_bot, True)

    padrao = await slot("first_message")
    return (*padrao, False) if padrao else None


async def _agente_ia(db, tenant_id, conn, session, contact, text, media, outputs,
                     *, exigir_primeiro=False):
    """Deixa o agente de IA responder, se houver um ligado para esta conexão.

    Devolve True só quando ele de fato respondeu. Escalar (por decisão dele,
    por falha do provedor ou por teto do plano) devolve False, e a explicação
    já ficou no histórico — quem chama segue para a fila humana.

    `exigir_primeiro` é usado quando um bot atenderia a conversa: aí só um
    agente em modo `primeiro` toma a frente. Sem bot, qualquer agente ligado
    serve, que é o modo `fallback`.
    """
    agente = await ai_agents.agente_da_conexao(db, tenant_id, conn.get("id") if conn else None)
    if not agente:
        return False
    if exigir_primeiro and agente.get("modo") != ai_agents.MODO_PRIMEIRO:
        return False
    resultado = await ai_agents.tentar_responder(
        db, agente, session, contact, text, bool(media), outputs)
    if resultado == ai_agents.RESPONDEU:
        session["ia_agente_id"] = agente["id"]
        return True
    return False


# ---------------------------------------------------------------------------
# Uma conversa de cada vez
# ---------------------------------------------------------------------------
#
# Cada webhook chega numa task própria, e duas mensagens do mesmo contato podem
# ser processadas ao mesmo tempo. As duas leem a sessão no mesmo estado, as duas
# decidem a partir dele, e a última a gravar apaga o que a primeira tinha feito.
#
# Foi exatamente isso que quebrou um agendamento real: o nó mandou "Para qual
# dia?" e gravou `awaiting` na etapa do dia; um segundo depois, uma mensagem que
# tinha chegado ANTES terminou de ser processada, ainda enxergando o Menu
# anterior, respondeu "Resposta inválida" e regravou o `awaiting` do Menu por
# cima. Quando o contato escolheu o dia, a conversa já não esperava mais por ele.
#
# Com duas pessoas conversando isso é raro (as mensagens vêm com segundos de
# distância), mas basta o contato tocar num botão e digitar em seguida. E é
# certeiro quando do outro lado há outro robô respondendo em rajada.
#
# A trava é por conversa, não global: duas conversas diferentes continuam
# andando em paralelo, que é o ponto de o webhook ser assíncrono.
#
# **Vale dentro de um processo.** Com mais de um worker de uvicorn, duas
# mensagens podem cair em processos diferentes e a corrida volta. Fechar isso
# exige trava no Mongo (findAndModify com TTL), e é o caminho no dia em que o
# backend rodar replicado.

_travas_por_conversa: dict[str, tuple] = {}


@asynccontextmanager
async def _uma_por_vez(chave: str):
    trava, esperando = _travas_por_conversa.get(chave, (None, 0))
    if trava is None:
        trava = asyncio.Lock()
    _travas_por_conversa[chave] = (trava, esperando + 1)
    try:
        async with trava:
            yield
    finally:
        # O contador evita que o dicionário cresça sem fim, uma entrada por
        # contato que já conversou. Só sai quando ninguém mais está na fila.
        _trava, quantos = _travas_por_conversa[chave]
        if quantos <= 1:
            _travas_por_conversa.pop(chave, None)
        else:
            _travas_por_conversa[chave] = (_trava, quantos - 1)


async def handle_incoming_message(tenant_id: str, from_number: str, text: str,
                                  message_id: str, connection_id: str | None = None,
                                  push_name: str | None = None, media: dict | None = None,
                                  escolha: dict | None = None):
    """Ponto de entrada do webhook, uma mensagem por vez em cada conversa.

    A chave é (empresa, conexão, número), que é a mesma identidade que
    `conversation_sessions` usa. Ela é montada antes de qualquer leitura do
    banco, senão a corrida aconteceria justamente na busca da sessão.
    """
    async with _uma_por_vez(f"{tenant_id}:{connection_id or '-'}:{from_number}"):
        return await _processar_mensagem(tenant_id, from_number, text, message_id,
                                         connection_id, push_name, media, escolha)


async def _processar_mensagem(tenant_id: str, from_number: str, text: str, message_id: str, connection_id: str | None = None, push_name: str | None = None, media: dict | None = None, escolha: dict | None = None):
    """Called from webhook. Advances the conversation for this contact.

    `connection_id` identifies exactly which whatsapp_connection (and thus
    which Cloud API phone number) the message came in on, required so the
    right bot and credential are used when a tenant has more than one number.
    `push_name` is the sender's WhatsApp display name (Info.PushName on the
    webhook payload) — free, no extra API call needed.
    `media` descreve o anexo, quando houver (ver _extract_media no webhook);
    `text` é a legenda dele nesse caso.
    `escolha` é {"id", "titulo"} quando o contato tocou num botão ou escolheu
    um item de lista, em vez de digitar (ver _extract_escolha no webhook). O id
    é o da opção do nó de Menu, e é por ele que a saída é resolvida: o rótulo
    volta cortado em 20 caracteres nos botões, então casar por texto falharia
    justamente nos menus mais descritivos.
    """
    db = get_db()

    # Idempotency
    if await db.processed_messages.find_one({"message_id": message_id}):
        return
    # `at` vai como datetime nativo, e não como string ISO igual ao resto do
    # sistema, porque é o campo do índice TTL que expira estes registros — e o
    # TTL do Mongo só age sobre campos do tipo Date.
    await db.processed_messages.insert_one(
        {"message_id": message_id, "at": datetime.now(timezone.utc)}
    )

    if connection_id:
        conn = await db.whatsapp_connections.find_one({"id": connection_id, "tenant_id": tenant_id})
    else:
        conn = await db.whatsapp_connections.find_one({"tenant_id": tenant_id})
    if not conn:
        return
    cred = whatsapp.credencial(conn)

    # Contact (needs to exist before session lookup)
    #
    # A busca cobre as duas grafias do celular brasileiro. O `from` do webhook
    # é o `wa_id` da Meta, e no Brasil ele nem sempre tem o nono dígito: o
    # contato importado de planilha como 5585999998888 chega aqui como
    # 558599998888, e procurar só pela forma exata criaria um segundo contato,
    # com histórico próprio, para a mesma pessoa.
    numeros = contatos_csv.variantes_br(from_number)
    contact = await db.contacts.find_one(
        {"tenant_id": tenant_id, "numero_whatsapp": {"$in": numeros}}
    )
    if not contact:
        contact = {
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "numero_whatsapp": from_number,
            # O identificador que a própria Meta usa para esta pessoa. É ele
            # que vale no envio: reconstruir o número "certo" é o erro clássico
            # de integração no Brasil, e manda a mensagem para outra pessoa.
            "wa_id": from_number,
            "nome": push_name or from_number,
            # Origem preenchida na criação: é campo da ficha, e depois não há
            # como saber se o contato entrou por mensagem ou por cadastro.
            "origem": "mensagem recebida",
            "created_at": _now(),
        }
        await db.contacts.insert_one(contact)
    else:
        updates = {}
        if push_name and contact.get("nome") == contact.get("numero_whatsapp"):
            updates["nome"] = push_name
        if contact.get("wa_id") != from_number:
            updates["wa_id"] = from_number
        if updates:
            await db.contacts.update_one({"id": contact["id"]}, {"$set": updates})
            contact.update(updates)

    # A foto de perfil, no canal não oficial, em tarefa própria — como a
    # confirmação de leitura, e pelo mesmo motivo: é uma ida à ponte e mais um
    # download, e nada disso pode ficar entre a mensagem do contato e a
    # resposta do bot. `avatares.atualizar` decide sozinho se há o que fazer
    # (canal certo, contato ainda sem foto, tentativa não repetida) e nunca
    # levanta.
    asyncio.create_task(avatares.atualizar(db, tenant_id, conn, contact))

    # Conversa já em andamento continua no bot dela (mesmo que ele tenha sido
    # pausado depois — trocar de bot no meio confundiria o contato).
    session = await db.conversation_sessions.find_one({
        "tenant_id": tenant_id, "contact_id": contact["id"],
        "whatsapp_connection_id": conn["id"], "status": {"$in": ["ativa", "aguardando_humano"]}
    })

    # Loga a mensagem antes de qualquer decisão: mesmo sem bot capaz de
    # atender, ela precisa aparecer no histórico da conversa.
    session_id = session["id"] if session else str(uuid.uuid4())

    # Rótulo da opção tocada, recuperado do menu que fez a pergunta.
    #
    # A resposta de um botão chega com o id e, na prática, sem rótulo — e o
    # último recurso do webhook é gravar o id. "t2_dur" no histórico faz o
    # atendente ler um identificador interno no lugar do que o contato tocou.
    # O rótulo guardado em `awaiting.rotulos` quando o menu saiu é a única
    # fonte que existe para essa frase, e serve aos quatro formatos de resposta.
    #
    # `text` só é substituído quando ele veio da própria escolha (vazio, o id
    # ou o rótulo cortado) — se o contato mandou texto de verdade junto, é o
    # texto dele que vale.
    if escolha and escolha.get("id"):
        rotulo = (((session or {}).get("awaiting") or {})
                  .get("rotulos") or {}).get(escolha["id"])
        if rotulo:
            if text in ("", escolha["id"], escolha.get("titulo") or ""):
                text = rotulo
            escolha["titulo"] = rotulo

    # O que o contato de fato escreveu, guardado antes de `text` poder virar um
    # aviso de sistema logo abaixo. É isto que o agente de IA recebe: responder
    # a "[image não pôde ser baixado]" como se fosse fala do contato é pior do
    # que passar a conversa para alguém que consegue abrir o anexo.
    legenda_contato = text
    msg_type, msg_meta = "text", None
    if media:
        stored = await _ingest_incoming_media(db, tenant_id, session_id, cred, media)
        msg_type = media["kind"]
        msg_meta = {"media": stored} if stored else None
        if not stored and not text:
            # Sem o arquivo e sem legenda a bolha ficaria vazia; melhor dizer
            # o que chegou do que deixar um buraco no histórico.
            text = f"[{media['kind']} não pôde ser baixado]"
    msg_meta = {**(msg_meta or {}), "wamid": message_id}
    incoming = await _log_msg(db, session_id, tenant_id, "recebida", text, mtype=msg_type, meta=msg_meta)

    # A janela de atendimento de 24h reabre a cada mensagem do contato, e é ela
    # que decide se a empresa ainda pode responder livremente ou se precisa de
    # um modelo aprovado. Gravada antes de qualquer decisão de roteamento
    # porque vale mesmo quando o resto desta função não roda: conversa com
    # atendente humano é justamente o caso em que alguém vai responder depois.
    janela = (datetime.now(timezone.utc) + timedelta(hours=whatsapp.JANELA_HORAS)).isoformat()
    await db.conversation_sessions.update_one(
        {"id": session_id, "tenant_id": tenant_id},
        {"$set": {"janela_expira_em": janela}},
    )
    if session is not None:
        session["janela_expira_em"] = janela

    # Os dois tiques azuis. Sem isto o contato vê a mensagem dele como não
    # lida mesmo depois de o bot responder, o que na conversa parece empresa
    # que não leu. Best-effort: falhar aqui não pode atrapalhar a resposta.
    if cred:
        asyncio.create_task(_marcar_lida_bg(cred, message_id))

    # Descadastro. Vale para qualquer mensagem: o contato pode pedir para sair
    # quando quiser. Sinaliza e segue — quem pediu para sair continua sendo
    # atendido normalmente, então o fluxo abaixo não é interrompido.
    if text and pediu_opt_out(text) and not contact.get("opt_out"):
        await db.contacts.update_one(
            {"id": contact["id"], "tenant_id": tenant_id},
            {"$set": {"opt_out": True, "opt_out_em": _now()}},
        )
        await _log_msg(db, session_id, tenant_id, "sistema",
                       "Contato pediu para não receber mensagens em massa",
                       mtype="system")

    # Chegou pelo botão do site: a mensagem pronta traz "Código do site: XXXXXX".
    # Antes do roteamento, como o opt-out: o bot ou o agente de IA respondem
    # normalmente, e quem abrir a conversa já vê de onde o turista veio e o que
    # ele quer, com o orçamento aberto. Nunca levanta (ver `sites.atribuir`).
    if text and "site" in text.lower():
        try:
            import sites
            resumo = await sites.atribuir(db, tenant_id, contact, session_id, text)
            if resumo:
                await _log_msg(db, session_id, tenant_id, "sistema", resumo, mtype="system")
        except Exception as e:
            logger.warning("Atribuição do site falhou na conversa %s: %s", session_id, e)

    # Resposta ao lembrete de agendamento ("CONFIRMAR"/"CANCELAR").
    #
    # Vem antes de qualquer roteamento, como o opt-out e pelo mesmo motivo: quem
    # responde a uma pergunta **nossa** não pode depender de haver bot
    # configurado naquela conexão. E vem antes até do desvio de atendimento
    # humano, porque a confirmação de presença é dado de agenda: perdê-la porque
    # um atendente assumiu a conversa no intervalo deixaria a cadeira marcada sem
    # ninguém saber se o cliente vem.
    #
    # Só olha quando a conversa está com `agenda_aguardando` preenchido, isto é,
    # logo depois de um lembrete. É esse contexto que permite aceitar respostas
    # curtas ("sim", "ok", "1") que fora dele seriam ambíguas demais.
    if session and session.get("agenda_aguardando") and text:
        import agenda_worker
        if await agenda_worker.responder_lembrete(db, tenant_id, session, text):
            await db.conversation_sessions.update_one(
                {"id": session_id}, {"$set": {"updated_at": _now()}},
            )
            return

    outputs: list[dict] = []

    if session and session.get("status") == "aguardando_humano":
        # In human-handoff, don't auto-reply — just log
        await db.conversation_sessions.update_one(
            {"id": session_id}, {"$set": {"updated_at": _now()}, "$inc": {"unread_count": 1}}
        )
        return

    if session:
        loaded = await _load_bot_flow(db, tenant_id, session.get("bot_id"), require_active=False)
        if not loaded:
            # Antes da fila, o agente de IA. É aqui que ele atua em modo
            # `fallback`: exatamente o ponto em que hoje a mensagem cairia
            # direto para um humano, e por isso ligar um agente não altera o
            # comportamento de nenhum fluxo já publicado.
            if await _agente_ia(
                    db, tenant_id, conn, session, contact, legenda_contato, media, outputs):
                await db.conversation_sessions.update_one(
                    {"id": session_id},
                    {"$set": {"ia_respostas": session.get("ia_respostas", 0),
                              "ia_agente_id": session.get("ia_agente_id"),
                              "updated_at": _now()},
                     "$inc": {"unread_count": 1}},
                )
                if cred and outputs:
                    await _send_outputs(db, tenant_id, cred, from_number, session_id, outputs)
                return

            # Sem bot e sem agente: fila humana, em vez de engolir a mensagem.
            await db.conversation_sessions.update_one(
                {"id": session_id},
                {"$set": {"status": "aguardando_humano", "status_atendimento": "aguardando",
                          "aguardando_desde": session.get("aguardando_desde") or _now(),
                          "updated_at": _now()},
                 "$inc": {"unread_count": 1}},
            )
            # O agente pode ter deixado uma frase para o contato ao transferir
            # ("vou passar para a equipe confirmar"). Sem este envio ela era
            # montada e descartada, e o contato ficava sem resposta até alguém
            # assumir a conversa.
            if cred and outputs:
                await _send_outputs(db, tenant_id, cred, from_number, session_id, outputs)
            # A conversa aberta por um aviso de agendamento também nasce sem
            # bot, e cai aqui quando o contato responde outra coisa que não
            # CONFIRMAR/CANCELAR ("posso remarcar?"). Dizer "bot indisponível"
            # descreveria um defeito onde não há nenhum.
            if session.get("origem") == "agenda":
                motivo = ("Resposta a um aviso de agendamento: encaminhada "
                          "para atendimento humano")
            else:
                motivo = "Bot da conversa indisponível: encaminhada para atendimento humano"
            await _log_msg(db, session_id, tenant_id, "sistema", motivo)
            return
        bot, flow, start_node = loaded

        session["last_user_message"] = text
        awaiting = session.get("awaiting")
        if awaiting and awaiting.get("node_id"):
            menu_node = _find_node(flow["nodes"], awaiting["node_id"])
            if menu_node and menu_node["type"] == "schedule":
                # O nó de Agendamento conduz várias perguntas dentro de si e
                # resolve a própria resposta, pelo mesmo `resolver_escolha_menu`
                # do Menu. Só devolve saída quando marcou (ou desistiu).
                import agenda_flow
                saida = await agenda_flow.avancar(
                    menu_node, session, outputs,
                    resposta=text, escolha_id=(escolha or {}).get("id"))
                if saida is not None:
                    next_id = _find_next(flow["edges"], menu_node["id"], saida)
                    if next_id:
                        await _run_from(next_id, flow, session, outputs)
            elif menu_node and menu_node["type"] == "menu":
                opts = menu_node.get("data", {}).get("options", [])
                # Mesma função que o chat de teste do editor usa: o id do botão
                # primeiro, depois o número e o rótulo digitados.
                handle = resolver_escolha_menu(
                    opts, text, (escolha or {}).get("id"))
                if handle is None:
                    next_id = _find_next(flow["edges"], menu_node["id"], "invalid")
                    if next_id is None:
                        outputs.append({"type": "text",
                                        "content": menu_node["data"].get("invalid_message", "Resposta inválida.")})
                    else:
                        session["awaiting"] = None
                        await _run_from(next_id, flow, session, outputs)
                else:
                    next_id = _find_next(flow["edges"], menu_node["id"], handle)
                    session["awaiting"] = None
                    if next_id:
                        await _run_from(next_id, flow, session, outputs)
    else:
        picked = await _pick_bot_for_new_session(db, tenant_id, conn, text)
        por_palavra_chave = bool(picked and picked[3])
        run_bot_flow = picked is not None
        if picked:
            bot, flow, start, _ = picked
        else:
            # Nenhum bot atende: a conversa ainda nasce, direto em "aguardando",
            # pra um humano ver na inbox em vez de a mensagem sumir.
            bot, flow, start = None, None, None

        conn_id = conn.get("id") if conn else None
        dept_id = conn.get("departamento_padrao_id") if conn else None
        session = {
            "id": session_id,
            "tenant_id": tenant_id,
            "contact_id": contact["id"],
            "bot_id": bot["id"] if bot else None,
            "flow_id": flow["id"] if flow else None,
            "whatsapp_connection_id": conn_id,
            "departamento_id": dept_id,
            "atendente_id": None,
            "status_atendimento": "bot",
            "aguardando_desde": None,
            "assumido_em": None,
            "finalizado_em": None,
            "node_atual_id": start["id"] if start else None,
            "variables": {"nome_contato": contact["nome"], "numero": from_number},
            "status": "ativa",
            "awaiting": None,
            "last_user_message": text,
            # Quantas vezes o agente de IA já respondeu nesta conversa, e qual
            # agente. O contador é o freio de mão contra conversa infinita.
            "ia_respostas": 0,
            "ia_agente_id": None,
            # A mensagem que abriu a conversa foi gravada antes de a sessão
            # existir, então o update de log_message não teve o que casar — a
            # prévia entra aqui para o card não nascer vazio.
            "last_message": last_message_preview(incoming),
            # Mesma razão: a janela foi calculada antes de a sessão existir, e
            # sem ela aqui a primeira resposta do bot seria barrada por uma
            # janela que na verdade acabou de abrir.
            "janela_expira_em": janela,
            "created_at": _now(),
            "updated_at": _now(),
        }
        await db.conversation_sessions.insert_one(session)
        # Palavra-chave digitada pelo contato tem precedência absoluta: quem
        # digitou pediu aquele fluxo, não uma conversa com o agente. Fora
        # disso, o agente assume — em modo `primeiro` mesmo havendo um bot de
        # primeira mensagem, em `fallback` só quando não há bot nenhum.
        assumiu_ia = False
        if not por_palavra_chave:
            assumiu_ia = await _agente_ia(
                db, tenant_id, conn, session, contact, legenda_contato, media, outputs,
                exigir_primeiro=run_bot_flow)
        if assumiu_ia:
            # A conversa passa a ser do agente, não de um fluxo. Zerar o
            # vínculo é o que faz a próxima mensagem do contato cair de novo no
            # agente (via `_load_bot_flow` sem bot) em vez de tentar retomar um
            # fluxo que nunca chegou a rodar.
            session["bot_id"] = None
            session["flow_id"] = None
            session["node_atual_id"] = None
        elif run_bot_flow:
            await _run_from(start["id"], flow, session, outputs)
        else:
            session["status"] = "aguardando_humano"
            await _log_msg(db, session_id, tenant_id, "sistema",
                           "Nenhum bot configurado para atender esta mensagem — "
                           "encaminhada para atendimento humano")

    # persist session
    # Mirror flow_engine's session.status → status_atendimento so kanban stays in sync.
    if session.get("status") == "aguardando_humano" and session.get("status_atendimento") == "bot":
        session["status_atendimento"] = "aguardando"
        # Marca o início da espera para a UI mostrar "aguardando há X min".
        session["aguardando_desde"] = _now()
    elif session.get("status") == "finalizada" and session.get("status_atendimento") in (None, "bot"):
        session["status_atendimento"] = "finalizado"
        session["finalizado_em"] = _now()

    await db.conversation_sessions.update_one(
        {"id": session_id},
        {"$set": {
            "node_atual_id": session["node_atual_id"],
            "variables": session["variables"],
            "status": session["status"],
            "status_atendimento": session.get("status_atendimento", "bot"),
            "aguardando_desde": session.get("aguardando_desde"),
            "awaiting": session.get("awaiting"),
            "bot_id": session.get("bot_id"),
            "flow_id": session.get("flow_id"),
            "departamento_id": session.get("departamento_id"),
            "finalizado_em": session.get("finalizado_em"),
            "ia_respostas": session.get("ia_respostas", 0),
            "ia_agente_id": session.get("ia_agente_id"),
            "updated_at": _now(),
        }, "$inc": {"unread_count": 1}},
    )

    # Handle bot-to-bot transfer: swap this session onto the target bot's flow
    # and continue processing so the new bot's greeting fires immediately.
    guard = 0
    while session.get("status") == "transferindo" and guard < 5:
        guard += 1
        target_bot_id = session.get("transfer_target")
        target_bot = await db.bots.find_one({"id": target_bot_id, "tenant_id": tenant_id, "status": "ativo"})
        if not target_bot:
            await _log_msg(db, session_id, tenant_id, "sistema",
                           f"Bot de destino {target_bot_id} não existe ou está inativo", mtype="error")
            await db.conversation_sessions.update_one({"id": session_id}, {"$set": {"status": "finalizada"}})
            break
        target_flow = await db.flows.find_one({"id": target_bot["main_flow_id"]}, {"_id": 0})
        if not target_flow or not target_flow.get("published_at"):
            await _log_msg(db, session_id, tenant_id, "sistema",
                           f"Fluxo do bot destino não publicado", mtype="error")
            await db.conversation_sessions.update_one({"id": session_id}, {"$set": {"status": "finalizada"}})
            break
        start = _find_start(target_flow["nodes"])
        if not start:
            break
        session["bot_id"] = target_bot["id"]
        session["flow_id"] = target_flow["id"]
        session["status"] = "ativa"
        session["awaiting"] = None
        session["transfer_target"] = None
        session["node_atual_id"] = start["id"]
        await _run_from(start["id"], target_flow, session, outputs)
        await db.conversation_sessions.update_one(
            {"id": session_id},
            {"$set": {
                "bot_id": session["bot_id"],
                "flow_id": session["flow_id"],
                "node_atual_id": session["node_atual_id"],
                "variables": session["variables"],
                "status": session["status"],
                "awaiting": session.get("awaiting"),
                "updated_at": _now(),
            }},
        )
        flow = target_flow  # for subsequent iterations if chained transfer

    # send outputs through the Cloud API
    if cred and outputs:
        await _send_outputs(db, tenant_id, cred, from_number, session_id, outputs)


def destino(contact: dict | None) -> str | None:
    """O número para onde a mensagem vai.

    O `wa_id` observado numa mensagem recebida ganha do número cadastrado,
    sempre. No Brasil os dois divergem no nono dígito com frequência, e o que
    a Meta aceita é o identificador que ela própria usou. Contato que nunca
    escreveu não tem `wa_id`, e aí vale o número cadastrado, que é o único que
    existe.
    """
    if not contact:
        return None
    return contact.get("wa_id") or contact.get("numero_whatsapp")


async def _outgoing_context(db, tenant_id: str, session_id: str):
    """Peças comuns de todo envio humano: sessão, contato, credencial da
    conexão e nome do atendente. Devolve None se a sessão não existir."""
    session = await db.conversation_sessions.find_one({"id": session_id, "tenant_id": tenant_id})
    if not session:
        return None
    contact = await db.contacts.find_one({"id": session["contact_id"]})
    conn_id = session.get("whatsapp_connection_id")
    if conn_id:
        conn = await db.whatsapp_connections.find_one({"id": conn_id, "tenant_id": tenant_id})
    else:
        conn = await db.whatsapp_connections.find_one({"tenant_id": tenant_id})
    cred = whatsapp.credencial(conn)

    agent = None
    if session.get("atendente_id"):
        # No tenant_id filter here: a super admin acting via impersonation
        # has atendente_id set to their own user id, whose record carries
        # tenant_id=None (they belong to no tenant) — filtering by tenant_id
        # would silently fail to find them and drop the name.
        agent = await db.users.find_one({"id": session["atendente_id"]})
    return session, contact, cred, (agent.get("name") if agent else None)


class JanelaFechada(Exception):
    """A conversa passou de 24h sem mensagem do contato.

    Erro próprio porque a tela precisa distinguir isto de falha de envio: uma
    pede "peça ao contato para escrever, ou mande um modelo", a outra pede
    tentar de novo. Sem a distinção, o atendente lê "erro ao enviar" e repete
    o clique para sempre.
    """


MSG_JANELA_FECHADA = (
    "A janela de 24 horas desta conversa fechou. O WhatsApp só permite "
    "mensagem livre até 24h depois da última mensagem do contato; depois "
    "disso, apenas modelo aprovado."
)


async def send_human_message(tenant_id: str, session_id: str, content: str):
    """Sends a text from human agent (inbox) through the Cloud API.

    The agent's name is prepended (WhatsApp bold syntax) to the text that
    actually goes out over WhatsApp, so the customer sees who's answering,
    not just an internal-only label. The clean `content` (without the
    prefix) is what's stored/shown in our own chat panel, with the agent
    name kept separately in `meta` for the bold label above the bubble.

    A janela é conferida antes de tentar: o WhatsApp recusaria com 131047, e
    aí a mensagem já teria ido para o histórico como enviada.
    """
    db = get_db()
    ctx = await _outgoing_context(db, tenant_id, session_id)
    if not ctx:
        return None
    session, contact, cred, agent_name = ctx
    outgoing_text = f"*{agent_name}:*\n{content}" if agent_name else content

    if cred and contact:
        if not janela_aberta(session, getattr(cred, "canal", None)):
            raise JanelaFechada(MSG_JANELA_FECHADA)
        try:
            wamid = await whatsapp.send_text(cred, destino(contact), outgoing_text)
        except Exception as e:
            await _log_msg(db, session_id, tenant_id, "erro", f"Falha envio humano: {e}", mtype="error")
            raise
    else:
        wamid = None
    meta = {"wamid": wamid} if wamid else {}
    if agent_name:
        meta["agent_name"] = agent_name
    await _log_msg(db, session_id, tenant_id, "enviada", content, meta=meta or None)
    await db.conversation_sessions.update_one({"id": session_id}, {"$set": {"updated_at": _now()}})
    return True


async def send_template_message(tenant_id: str, session_id: str, nome: str,
                                idioma: str, valores: list[str], texto: str):
    """Manda um modelo aprovado, o único envio que atravessa a janela de 24h.

    Existe aqui, e não em cada chamador, pela mesma razão de
    `send_human_message`: é o ponto único que grava a mensagem no histórico
    com a prévia da conversa junto. O `texto` é o corpo do modelo já
    preenchido, e é ele que vai para a bolha: guardar "modelo
    lembrete_consulta" faria o atendente abrir a conversa sem saber o que a
    empresa disse ao contato.
    """
    db = get_db()
    ctx = await _outgoing_context(db, tenant_id, session_id)
    if not ctx:
        return None
    _session, contact, cred, _agent = ctx
    if not cred or not contact:
        raise RuntimeError("Conexão de WhatsApp indisponível para esta conversa")

    componentes = []
    if valores:
        componentes.append({"type": "body",
                            "parameters": [{"type": "text", "text": v} for v in valores]})
    try:
        wamid = await whatsapp.send_template(cred, destino(contact), nome, idioma, componentes)
    except Exception as e:
        await _log_msg(db, session_id, tenant_id, "erro",
                       f"Falha no envio do modelo {nome}: {e}", mtype="error")
        raise
    await _log_msg(db, session_id, tenant_id, "enviada", texto,
                   meta={"wamid": wamid, "template": nome})
    await db.conversation_sessions.update_one({"id": session_id}, {"$set": {"updated_at": _now()}})
    return True


async def send_human_media(tenant_id: str, session_id: str, media_doc: dict, caption: str = ""):
    """Sends a stored file from the human agent (inbox) through the Cloud API.

    Same convention as send_human_message: the agent's name is prepended to
    what actually leaves for WhatsApp (here, to the caption), while the chat
    panel keeps the clean caption plus `meta.media` — the pointer the bubble
    uses to render the image/video/audio/document from /api/media.

    Sending fails loudly (raises): a file the contact never received must not
    show up as delivered in the panel.
    """
    db = get_db()
    ctx = await _outgoing_context(db, tenant_id, session_id)
    if not ctx:
        return None
    session, contact, cred, agent_name = ctx
    if not cred or not contact:
        raise RuntimeError("Conexão de WhatsApp indisponível para esta conversa")
    if not janela_aberta(session, getattr(cred, "canal", None)):
        raise JanelaFechada(MSG_JANELA_FECHADA)

    outgoing_caption = f"*{agent_name}:*\n{caption}" if agent_name and caption else (
        f"*{agent_name}:*" if agent_name else caption
    )
    try:
        # Sobe o arquivo uma vez e guarda o id da Meta no documento: o mesmo
        # anexo mandado para outra conversa não sobe de novo.
        media_id = await media_store.id_na_meta(db, media_doc, cred)
        wamid = await whatsapp.send_media(
            cred, destino(contact), media_doc["kind"], media_id=media_id,
            caption=outgoing_caption, filename=media_doc["filename"])
    except Exception as e:
        await _log_msg(db, session_id, tenant_id, "erro", f"Falha envio de mídia: {e}", mtype="error")
        raise

    meta = {"media": media_store.public_payload(media_doc), "wamid": wamid}
    if agent_name:
        meta["agent_name"] = agent_name
    await _log_msg(db, session_id, tenant_id, "enviada", caption,
                   mtype=media_doc["kind"], meta=meta)
    await db.conversation_sessions.update_one({"id": session_id}, {"$set": {"updated_at": _now()}})
    return True
