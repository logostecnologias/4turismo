"""Tudo que as Saídas dizem ao turista pelo WhatsApp: confirmação, lembrete e
a resposta dele ao lembrete. E a varredura que expira solicitação vencida.

Separado de `agenda.py` por dependência, não por gosto: enviar mensagem passa
por `outbound` e `flow_runtime`, e `flow_runtime` importa `flow_engine`, que
importa `agenda` para o nó de Agendamento. Deixar o envio dentro de `agenda`
fecharia o ciclo e derrubaria o boot.

Três decisões:

  - **O lembrete é o módulo inteiro se pagando.** Falta em horário marcado é o
    prejuízo do negócio de serviço, e um lembrete no dia anterior é o que a
    reduz. Sem ele, agendar pelo WhatsApp economiza uma ligação para marcar e
    não economiza a cadeira vazia.

  - **A resposta ao lembrete é cotejada com a mensagem inteira**, normalizada,
    como o opt-out ("SAIR"): "quero cancelar meu pedido" não pode cancelar
    a consulta de amanhã. A diferença é que aqui palavras curtas (`sim`, `ok`,
    `1`) valem: elas só são consultadas quando a conversa está com
    `agenda_aguardando` preenchido, isto é, logo depois de uma pergunta nossa
    que pedia exatamente isso. Sem esse contexto elas seriam ambíguas demais.

  - **Falha de envio não desfaz o agendamento.** O horário está reservado; o
    contato não recebeu o aviso. Cancelar por causa disso trocaria um problema
    de comunicação por um de agenda.

  - **No turismo, quem confirma a reserva é a agência, não o turista.** Por isso
    o lembrete só sai para reserva Confirmada (lembrar de uma solicitação que
    ninguém aceitou faria o turista achar que está tudo certo), e o CONFIRMAR
    dele registra **presença** (`presenca_confirmada_em`) sem mexer no status.
"""
import asyncio
import logging
import re
import unicodedata
from datetime import datetime, timedelta, timezone

import agenda
import ritmo_envio
import saidas
from db import get_db
from whatsapp_client import CANAL_WUZAPI

logger = logging.getLogger("turismo.agenda")

# Como `avisar` espaça os envios em lote (ver `ritmo_envio`).
RITMO_ADIAVEL = "adiavel"   # lembrete e solicitação vencida: intervalo + teto por hora
RITMO_URGENTE = "urgente"   # cancelamento de saída: intervalo, sem teto

# De quanto em quanto tempo a varredura de lembretes roda. Lembrete é sobre
# hora marcada: quem tem consulta às 14h de amanhã não fica pior por receber o
# aviso às 14h05 de hoje em vez de às 14h00. Mesmo raciocínio do polling de 60s
# do selo de atividades atrasadas contra os 3s da fila.
INTERVALO = 5 * 60

# Teto da janela varrida numa passada, independente do que cada empresa
# configurou. Sem ele, uma empresa com `lembrete_horas` absurdo (720) faria a
# varredura carregar um mês de agenda a cada cinco minutos.
MAX_ANTECEDENCIA_HORAS = 168  # 7 dias

# Teto de agendamentos processados por passada. Uma empresa que ligue o lembrete
# depois de já ter mil horários marcados dispararia mil mensagens de uma vez
# que é exatamente o padrão de rajada que derruba número de WhatsApp.
MAX_POR_PASSADA = 200

# Depois de quantas horas do início do passeio o aviso de solicitação vencida
# deixa de fazer sentido e é dispensado.
AVISO_EXPIRACAO_ATRASO_MAX = 12

_task = None


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Mensagens
# ---------------------------------------------------------------------------

# Os padrões existem para o módulo funcionar sem ninguém escrever nada. Cada um
# é sobrescrevível em `scheduling_settings`, porque o tom de uma agência não é o
# de uma oficina.
MENSAGENS = {
    "confirmacao": (
        "✅ Reserva confirmada!\n\n"
        "*{servico}*\n"
        "🗓 {data_completa}\n"
        "👥 {pessoas}\n\n"
        "Qualquer imprevisto, é só avisar por aqui."
    ),
    # O lembrete é o lembrete de embarque: o que o turista precisa no dia
    # anterior não é o nome do passeio, que ele sabe, é a hora e o lugar em que
    # a van passa. Sem ponto, "a combinar" diz isso em vez de calar.
    "lembrete": (
        "Olá, {contato}! 👋\n\n"
        "Passando para lembrar do seu passeio:\n\n"
        "*{servico}*\n"
        "🗓 {data_completa}\n"
        "🚐 Busca: {busca}\n"
        "👥 {pessoas}\n\n"
        "Responda *CONFIRMAR* se estiver tudo certo, ou *CANCELAR* se não puder ir."
    ),
    "cancelamento": (
        "Sua reserva de *{servico}* em {data_completa} foi cancelada.\n\n"
        "Se quiser reservar outra data, é só me chamar."
    ),
    # A saída inteira foi cancelada pela agência (chuva, maré). Diferente do
    # cancelamento acima, que é o de uma reserva: aqui o turista não pediu nada,
    # e o motivo é a primeira coisa que ele vai querer saber.
    "cancelamento_saida": (
        "Olá, {contato}. Infelizmente a saída de *{servico}* em {data_completa} "
        "foi cancelada: {motivo}.\n\n"
        "Sua reserva foi cancelada. Responda aqui que a nossa equipe ajuda você a "
        "escolher outra data."
    ),
    # A solicitação venceu sem a agência confirmar. Não diz "cancelada pela
    # agência" nem promete vaga: diz o que aconteceu e abre a porta. Sem este
    # aviso o turista acha que o passeio está de pé e aparece no embarque.
    "expiracao": (
        "Olá, {contato}. Não conseguimos confirmar a tempo o seu pedido de "
        "*{servico}* para {data_completa}, e ele foi encerrado.\n\n"
        "Se ainda quiser ir, responda aqui que a nossa equipe confere as vagas "
        "com você."
    ),
}

# Os placeholders que `montar` entende, para a tela de configuração mostrar.
# `{profissional}` continua aceito (mensagem escrita antes da fase 1 não pode
# passar a sair com a chave crua), mas não é oferecido: a reserva por saída
# não tem guia.
VARIAVEIS = ["{contato}", "{contato_completo}", "{servico}", "{data}", "{hora}",
             "{data_completa}", "{duracao}", "{pessoas}", "{adultos}", "{criancas}",
             "{valor}", "{ponto_embarque}", "{horario_busca}", "{busca}", "{motivo}"]


def _moeda(valor) -> str:
    if valor is None:
        return "a combinar"
    inteiro, centavos = f"{float(valor):,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


def montar(modelo: str, agendamento: dict, config: dict) -> str:
    """Preenche o texto com os dados do agendamento.

    Placeholder desconhecido fica como está, em vez de derrubar o envio: quem
    escreve o texto na tela erra o nome de um campo com facilidade, e perder o
    lembrete inteiro por causa de `{profisional}` seria desproporcional.
    """
    tz = agenda.fuso_de(config)
    inicio = agenda.local(agendamento["inicio"], tz)
    valores = {
        "contato": (agendamento.get("contact_nome") or "").split(" ")[0] or "tudo bem",
        "contato_completo": agendamento.get("contact_nome") or "",
        "servico": agendamento.get("service_nome") or "passeio",
        "profissional": agendamento.get("resource_nome") or "nossa equipe",
        "data": f"{inicio.day:02d}/{inicio.month:02d}/{inicio.year}",
        "hora": f"{inicio.hour:02d}:{inicio.minute:02d}",
        "data_completa": agenda.rotulo_completo(agendamento["inicio"], tz),
        "duracao": f"{agendamento.get('duracao_min')} min",
        "pessoas": saidas.rotulo_pessoas(int(agendamento.get("adultos") or 0),
                                         int(agendamento.get("criancas") or 0))
                   if agendamento.get("pessoas") else "",
        "adultos": str(agendamento.get("adultos") or 0),
        "criancas": str(agendamento.get("criancas") or 0),
        "valor": _moeda(agendamento.get("preco")),
        "ponto_embarque": agendamento.get("ponto_embarque_nome") or "a combinar",
        "horario_busca": saidas.hora_local(agendamento.get("horario_busca"), config)
                         or "a combinar",
        "busca": saidas.rotulo_busca(agendamento, config),
        "motivo": (agendamento.get("motivo_cancelamento") or "").strip().rstrip(".")
                  or "imprevisto da operação",
    }

    def troca(m):
        return str(valores.get(m.group(1), m.group(0)))

    return re.sub(r"\{([a-zA-Z0-9_]+)\}", troca, modelo)


def texto_de(tipo: str, agendamento: dict, config: dict) -> str:
    modelo = (config.get(f"mensagem_{tipo}") or "").strip() or MENSAGENS[tipo]
    return montar(modelo, agendamento, config)


def template_de(tipo: str, agendamento: dict, config: dict) -> dict | None:
    """O modelo aprovado deste aviso, com as variáveis já preenchidas.

    Aviso de agenda é o caso em que a janela de 24h quase sempre está fechada:
    o lembrete sai um dia antes, e quem marcou horário há duas semanas não
    escreveu nada desde então. Sem modelo aprovado, o WhatsApp recusa o envio,
    e é justamente o lembrete que faz este módulo se pagar.

    Cada variável é um texto que passa pelo mesmo `montar` do corpo livre,
    então `{contato}` e `{data_completa}` funcionam igual nos dois lugares.
    """
    cfg = config.get(f"template_{tipo}") or {}
    if not cfg.get("nome"):
        return None
    return {
        "nome": cfg["nome"],
        "idioma": cfg.get("idioma") or "pt_BR",
        "valores": [montar(v or "", agendamento, config).strip() or " "
                    for v in (cfg.get("variaveis") or [])],
    }


# ---------------------------------------------------------------------------
# Envio
# ---------------------------------------------------------------------------

async def _conexao(db, tenant_id: str, agendamento: dict) -> dict:
    """Por qual número da empresa este aviso sai.

    Preferência para a conexão em que o agendamento nasceu: o contato já
    conversou por ali, e trocar de remetente no meio de um assunto parece outra
    empresa falando: a mesma regra de `_resolve_conexao` na ficha do contato.
    Sem ela (agendamento feito à mão na tela), cai na conversa mais recente do
    contato e, por último, na primeira conexão conectada da empresa.
    """
    conn_id = agendamento.get("whatsapp_connection_id")
    if conn_id:
        conn = await db.whatsapp_connections.find_one(
            {"id": conn_id, "tenant_id": tenant_id}, {"_id": 0})
        if conn:
            return conn

    ultima = await db.conversation_sessions.find(
        {"tenant_id": tenant_id, "contact_id": agendamento["contact_id"]},
        {"_id": 0, "whatsapp_connection_id": 1},
    ).sort([("updated_at", -1), ("_id", -1)]).to_list(1)
    if ultima and ultima[0].get("whatsapp_connection_id"):
        conn = await db.whatsapp_connections.find_one(
            {"id": ultima[0]["whatsapp_connection_id"], "tenant_id": tenant_id}, {"_id": 0})
        if conn:
            return conn

    return await db.whatsapp_connections.find_one(
        {"tenant_id": tenant_id, "status": "conectado"}, {"_id": 0}) \
        or await db.whatsapp_connections.find_one({"tenant_id": tenant_id}, {"_id": 0})


async def avisar(db, tenant_id: str, agendamento: dict, tipo: str, config: dict = None,
                 marcar_espera: bool = False, ritmo: str | None = None) -> bool | None:
    """Manda um aviso de agenda para o contato. Devolve se saiu.

    A conversa sai de `outbound.ensure_session`, nunca montada aqui: é a regra
    do projeto, e é ela que garante que a resposta do contato caia numa conversa
    com contexto em vez de abrir uma segunda para o mesmo número.

    `ritmo` é para envio em lote no canal não oficial (ver `ritmo_envio`):
    `RITMO_ADIAVEL` (varredura: intervalo entre avisos e teto por hora) ou
    `RITMO_URGENTE` (cancelamento de saída: intervalo, sem teto). Sem ritmo, é
    o envio de um clique, que sai na hora. Com `RITMO_ADIAVEL`, devolve
    **None** quando o teto da hora estourou: nada foi enviado, e quem chama
    deixa o aviso para a próxima varredura.

    Nunca levanta. Um lembrete que não sai é ruim; uma varredura que morre no
    primeiro contato sem conexão deixaria todos os outros sem lembrete nenhum.
    """
    # Tardios: `outbound` puxa `flow_runtime`, que puxa `flow_engine`, que
    # importa `agenda`. No topo, o ciclo fecha e o backend não sobe.
    import outbound
    import flow_runtime

    try:
        config = config or await agenda.configuracao(db, tenant_id)
        contato = await db.contacts.find_one(
            {"id": agendamento["contact_id"], "tenant_id": tenant_id}, {"_id": 0})
        if not contato:
            logger.warning("Agendamento %s sem contato: aviso %s não enviado",
                           agendamento["id"], tipo)
            return False
        conn = await _conexao(db, tenant_id, agendamento)
        if not conn:
            logger.warning("Empresa %s sem conexão de WhatsApp: aviso %s não enviado",
                           tenant_id, tipo)
            return False

        session, _ = await outbound.ensure_session(
            db, tenant_id, conn["id"], contato,
            departamento_id=conn.get("departamento_padrao_id"),
            origem="agenda",
        )
        texto = texto_de(tipo, agendamento, config)
        modelo = template_de(tipo, agendamento, config)

        # Texto livre só chega enquanto a janela de 24h estiver aberta, e para
        # aviso de agenda ela quase sempre está fechada. Quando há modelo
        # configurado ele é o caminho preferido; o texto livre fica para a
        # conversa que acabou de acontecer, onde é mais natural e não custa
        # modelo. Sem nenhum dos dois, o aviso não sai e o motivo fica no
        # histórico, em vez de a mensagem sumir sem explicação.
        # No canal wuzapi a janela conta como aberta e o aviso sai como texto
        # livre, sem depender de modelo aprovado — que é o que torna o
        # lembrete possível numa conexão não oficial, onde modelo não existe.
        if ritmo and conn.get("canal") == CANAL_WUZAPI:
            if not await ritmo_envio.vez(conn["id"], adiavel=ritmo == RITMO_ADIAVEL):
                return None
            await flow_runtime.digitar_antes_de_avisar(tenant_id, session["id"], texto)
        if flow_runtime.janela_aberta(session, conn.get("canal")):
            await flow_runtime.send_human_message(tenant_id, session["id"], texto)
        elif modelo:
            await flow_runtime.send_template_message(
                tenant_id, session["id"], modelo["nome"], modelo["idioma"],
                modelo["valores"], texto)
        else:
            await flow_runtime.log_message(
                db, session["id"], tenant_id, "sistema",
                f"Aviso de agenda ({tipo}) não enviado: a janela de 24h desta "
                "conversa está fechada e não há modelo aprovado configurado "
                "em Saídas > Configuração.", mtype="system")
            return False

        if marcar_espera:
            # De qual agendamento o "CONFIRMAR" da próxima mensagem fala. Sem
            # isto, a resposta chega sem referência e teria de adivinhar entre os
            # horários futuros do contato.
            await db.conversation_sessions.update_one(
                {"id": session["id"], "tenant_id": tenant_id},
                {"$set": {"agenda_aguardando": agendamento["id"], "updated_at": _now()}},
            )
        return True
    except Exception as e:
        logger.warning("Falha ao avisar (%s) do agendamento %s: %s",
                       tipo, agendamento.get("id"), e)
        return False


# ---------------------------------------------------------------------------
# Resposta do contato ao lembrete
# ---------------------------------------------------------------------------

def _normaliza(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9\s]", " ", texto.lower()).strip()


# Cotejo com a mensagem **inteira**, nunca "contém": é a mesma regra do opt-out,
# pelo mesmo motivo: "preciso confirmar uns dados" não é uma
# confirmação de presença.
CONFIRMA = {"confirmar", "confirmo", "confirmado", "confirmada", "sim", "ok",
            "1", "esta confirmado", "tudo certo", "vou sim", "estarei la"}
CANCELA = {"cancelar", "cancela", "cancelado", "cancelo", "nao", "2",
           "nao vou", "nao posso", "nao poderei", "desmarcar", "desmarca"}


async def responder_lembrete(db, tenant_id: str, session: dict, texto: str) -> str:
    """Trata "CONFIRMAR"/"CANCELAR" quando a conversa está esperando essa resposta.

    Devolve `"confirmado"`, `"cancelado"` ou `None` (não era uma resposta de
    lembrete: a mensagem segue o caminho normal, bot ou agente de IA).

    Chamado antes de qualquer roteamento, como o opt-out: o contato que responde
    ao lembrete não deve depender de haver um bot configurado naquela conexão.
    """
    import flow_runtime

    appointment_id = session.get("agenda_aguardando")
    if not appointment_id:
        return None
    n = _normaliza(texto)
    if not n:
        return None
    if n in CONFIRMA:
        acao = "confirmado"
    elif n in CANCELA:
        acao = "cancelado"
    else:
        return None

    doc = await db.appointments.find_one(
        {"id": appointment_id, "tenant_id": tenant_id}, {"_id": 0})
    # A marca sai de qualquer jeito: se o agendamento sumiu ou já foi resolvido,
    # deixá-la faria a próxima mensagem parecida ser lida como resposta de novo.
    await db.conversation_sessions.update_one(
        {"id": session["id"], "tenant_id": tenant_id},
        {"$set": {"agenda_aguardando": None}},
    )
    session["agenda_aguardando"] = None
    if not doc or doc["status"] not in (agenda.STATUS_AGENDADO, agenda.STATUS_CONFIRMADO):
        return None

    config = await agenda.configuracao(db, tenant_id)
    tz = agenda.fuso_de(config)
    if acao == "confirmado":
        # Presença, não status: confirmar a reserva é da agência. O turista que
        # responde CONFIRMAR está dizendo que vai, e é isso que se grava.
        await db.appointments.update_one(
            {"id": appointment_id, "tenant_id": tenant_id},
            {"$set": {"presenca_confirmada_em": _now(), "updated_at": _now()}})
        resposta = (f"Perfeito! Presença confirmada para "
                    f"{agenda.rotulo_completo(doc['inicio'], tz)}. Até lá! 👍")
    else:
        await saidas.cancelar(db, tenant_id, appointment_id,
                              por="contato", motivo="Cancelado pelo contato no WhatsApp")
        resposta = (f"Tudo bem, cancelei a sua reserva de "
                    f"{agenda.rotulo_completo(doc['inicio'], tz)}. "
                    "Se quiser reservar outra data, é só me dizer.")

    await flow_runtime.send_human_message(tenant_id, session["id"], resposta)
    return acao


# ---------------------------------------------------------------------------
# Varredura de lembretes
# ---------------------------------------------------------------------------

async def _pendentes(db, agora: datetime) -> list:
    """Agendamentos futuros que ainda não receberam lembrete, na janela máxima.

    Uma consulta só para todas as empresas, filtrando depois pela janela de cada
    uma. O contrário: uma consulta por empresa, seria uma ida ao banco por
    cliente a cada cinco minutos, e o banco está atrás de um túnel SSH. Mesmo
    raciocínio do `_tenant_windows` da retenção de mídia.
    """
    limite = agenda._iso(agora + timedelta(hours=MAX_ANTECEDENCIA_HORAS))
    cursor = db.appointments.find(
        # Só Confirmada: ver o cabeçalho do módulo.
        {"status": agenda.STATUS_CONFIRMADO,
         "lembrete_enviado_em": None,
         "inicio": {"$gt": agenda._iso(agora), "$lt": limite}},
        {"_id": 0},
    ).sort([("inicio", 1), ("_id", 1)])
    return await cursor.to_list(MAX_POR_PASSADA)


async def _avisar_expiradas(db, agora: datetime):
    """Avisa o turista de cada solicitação que venceu sem a agência confirmar.

    A expiração acontece em dois lugares (a varredura e a hora de reservar numa
    saída que parece cheia) e só marca `aviso_expiracao: pendente`; o envio é
    sempre daqui, para o segundo caso não mandar mensagem a um turista no meio
    da conversa de outro.

    Mesma regra do lembrete: a marca sai de "pendente" **antes** do envio,
    condicionada a ainda estar pendente. Perder um aviso é melhor que repeti-lo
    a cada cinco minutos com a conexão instável.
    """
    vencidas = await db.appointments.find(
        {"aviso_expiracao": "pendente"}, {"_id": 0},
    ).sort([("_id", 1)]).to_list(MAX_POR_PASSADA)
    configs = await _configs(db, vencidas)

    async def um(a) -> bool:
        tenant_id = a["tenant_id"]
        config = configs[tenant_id]
        # Passeio que já aconteceu há mais de meio dia (servidor parado, por
        # exemplo): o aviso chegaria como notícia velha.
        tarde = a["inicio"] < agenda._iso(agora - timedelta(hours=AVISO_EXPIRACAO_ATRASO_MAX))
        destino = "dispensado" if (tarde or not config.get("avisar_expiracao", True)) \
            else "enviado"
        res = await db.appointments.update_one(
            {"id": a["id"], "tenant_id": tenant_id, "aviso_expiracao": "pendente"},
            {"$set": {"aviso_expiracao": destino, "updated_at": _now()}})
        if destino != "enviado" or not res.modified_count:
            return False
        saiu = await avisar(db, tenant_id, a, "expiracao", config, ritmo=RITMO_ADIAVEL)
        if saiu is None:
            # Teto da hora do número: nada saiu, volta para a próxima varredura.
            await db.appointments.update_one(
                {"id": a["id"], "tenant_id": tenant_id, "aviso_expiracao": "enviado"},
                {"$set": {"aviso_expiracao": "pendente", "updated_at": _now()}})
        return bool(saiu)

    enviados = sum(await asyncio.gather(*(um(a) for a in vencidas)))
    if enviados:
        logger.info("Avisos de solicitação vencida enviados: %d", enviados)


async def _configs(db, docs: list) -> dict:
    configs = {}
    for a in docs:
        if a["tenant_id"] not in configs:
            configs[a["tenant_id"]] = await agenda.configuracao(db, a["tenant_id"])
    return configs


async def _varrer():
    db = get_db()
    agora = agenda._agora()
    # Antes dos lembretes: uma solicitação vencida devolve lugar, e devolver
    # cedo é o que deixa a vaga aparecer para o próximo turista.
    await saidas.expirar_solicitacoes(db, agora)
    await _avisar_expiradas(db, agora)
    pendentes = await _pendentes(db, agora)
    if not pendentes:
        return

    configs = await _configs(db, pendentes)

    async def um(a) -> bool:
        tenant_id = a["tenant_id"]
        config = configs[tenant_id]
        if not config.get("lembrete_ativo"):
            return False
        horas = float(config.get("lembrete_horas") or 24)
        if a["inicio"] > agenda._iso(agora + timedelta(hours=horas)):
            return False  # ainda cedo para esta empresa

        # A marca é gravada **antes** do envio. Um envio que falhe no meio e
        # deixasse a marca em branco faria a passada seguinte tentar de novo, e
        # um contato pode acabar recebendo o mesmo lembrete a cada cinco minutos
        # enquanto a conexão estiver instável. Perder um lembrete é melhor que
        # repeti-lo em laço, e a falha fica no log.
        marca = _now()
        res = await db.appointments.update_one(
            {"id": a["id"], "tenant_id": tenant_id, "lembrete_enviado_em": None},
            {"$set": {"lembrete_enviado_em": marca, "updated_at": marca}},
        )
        if not res.modified_count:
            return False  # alguém lembrou pela tela neste meio-tempo
        saiu = await avisar(db, tenant_id, a, "lembrete", config, marcar_espera=True,
                            ritmo=RITMO_ADIAVEL)
        if saiu is None:
            # Teto da hora do número: nada saiu. A marca é desfeita (só a nossa)
            # e a próxima varredura tenta de novo; lembrete chega com folga.
            await db.appointments.update_one(
                {"id": a["id"], "tenant_id": tenant_id, "lembrete_enviado_em": marca},
                {"$set": {"lembrete_enviado_em": None, "updated_at": _now()}})
            return False
        return True

    # Em paralelo entre números, em fila dentro de cada número (`ritmo_envio`):
    # uma agência com 30 lembretes não atrasa os das outras.
    enviados = sum(await asyncio.gather(*(um(a) for a in pendentes)))
    if enviados:
        logger.info("Lembretes de agenda enviados: %d", enviados)


async def _loop():
    logger.info("Lembretes e expiração de solicitações ativos (varredura a cada %ds)", INTERVALO)
    while True:
        try:
            await _varrer()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Varredura de lembretes falhou: %s", e)
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
