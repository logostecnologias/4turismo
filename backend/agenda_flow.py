"""O nó de Agendamento do editor de fluxos: uma reserva inteira dentro de um nó.

Reservar um passeio são quatro perguntas (passeio, quantas pessoas, dia, saída)
e uma gravação. Montar isso com os nós que já existiam era impossível: o Menu
precisa das opções escritas à mão no editor, e aqui **as opções só existem no
momento do envio**: dependem de quantos lugares sobram naquele minuto.

Decisões:

  - **Um nó, não quatro.** A alternativa seria um nó por pergunta, encadeados. O
    cliente teria de ligar as saídas de "que dia?" nas de "que hora?" sem saber
    quantas seriam, e um dia sem vaga deixaria o fluxo com uma ponta solta. As
    etapas vivem dentro do nó, em `awaiting`, e para quem monta o fluxo há só
    duas saídas: **reserva solicitada** e **não reservou**.

  - **Quantas pessoas vem antes do dia.** Assim os dias oferecidos já são os que
    comportam o grupo inteiro. Na ordem inversa o turista escolhe sábado e só
    então descobre que não cabem seis: volta para o começo, ou desiste.

  - **O bot só solicita.** A reserva nasce Solicitada e segura os lugares pelo
    prazo configurado; quem confirma é a agência. Por isso o texto de sucesso
    padrão diz "recebemos seu pedido", nunca "está confirmado".

  - **As perguntas saem como Menu, pelo mesmo caminho de envio.** Cada etapa
    produz um output `{"type": "menu"}`: o mesmo que o nó de Menu produz, e é
    `flow_runtime._enviar_menu` que decide botões, lista ou texto numerado, com
    a mesma degradação. Um segundo caminho de envio significaria uma segunda
    chance de perder a pergunta.

  - **A escolha é resolvida por `flow_engine.resolver_escolha_menu`**, a mesma
    função do nó de Menu: id do botão primeiro, depois número e rótulo digitados.
    Na pergunta de pessoas isso vale de graça: o turista que digita "4" cai na
    opção "4 pessoas" pelo número da opção, que é o próprio número de pessoas.

  - **Os rótulos são cortados na montagem, não deixados para a validação.** Aqui
    o rótulo é o nome de um passeio cadastrado em outra tela; um "Circuito Lagoa
    Bonita + Lagoa Azul" faria **todo** menu degradar para texto numerado. O
    nome inteiro vai na descrição da linha, que aceita 72 caracteres.

  - **Onde buscar vem por último, antes de gravar** (fase 2). Só quando a
    agência cadastrou pontos de embarque e o nó não desligou a pergunta. A
    lista termina em "Outro lugar", que reserva com a busca a combinar: o hotel
    que não está na lista é exatamente o caso em que uma pessoa precisa falar
    com o turista, e travar a reserva por isso perderia a venda.

  - **Nada é gravado no chat de teste do editor.** O teste consulta as vagas de
    verdade (é o que prova que a grade está certa), mas `session["simulado"]`
    impede a reserva: testar um fluxo não pode ocupar o lugar de um turista.
"""
import logging

import agenda
import saidas
import whatsapp_client as whatsapp
from db import get_db
from fastapi import HTTPException

logger = logging.getLogger("turismo.agenda")

# Saídas do nó, como aparecem no editor. Os ids continuam os da agenda de
# horário: são o `sourceHandle` das arestas de fluxos já publicados.
SAIDA_OK = "agendado"
SAIDA_FALHA = "nao_agendado"

ETAPAS = ("servico", "pessoas", "dia", "hora", "embarque")

# Id da opção "Outro lugar" na pergunta de embarque. Não colide com id de ponto,
# que é uuid.
OUTRO_LUGAR = "outro"

# Quantas vezes o contato pode responder qualquer coisa antes de o fluxo
# desistir e sair pela saída "não reservou". Sem teto, quem não entendeu a
# pergunta fica preso no nó para sempre, e ninguém na agência fica sabendo.
MAX_TENTATIVAS = 3

# Quantas vezes tentar de novo quando a saída escolhida lotou entre a pergunta
# e a resposta. Uma volta basta: a segunda lista já é recente.
MAX_CORRIDAS = 2

# Teto de opções no formato de texto numerado. O limite não é do WhatsApp, é de
# leitura: quarenta linhas numeradas no celular fazem o contato desistir.
MAX_ITENS_TEXTO = 15

# Teto da pergunta de pessoas. Dez cabe numa lista do WhatsApp; grupo maior é
# negociação (preço de grupo, van fechada), e vai para uma pessoa.
MAX_PESSOAS_NO = 10

PADROES_TEXTO = {
    "texto_servico": "Qual passeio você gostaria de reservar?",
    "texto_pessoas": "Quantas pessoas vão no passeio, contando as crianças?",
    "texto_dia": "Para qual dia?",
    "texto_hora": "Qual horário de saída fica melhor?",
    "texto_embarque": "Onde vamos buscar vocês?",
    "texto_sem_horario": ("No momento não encontrei vagas disponíveis. "
                          "Vou passar você para um atendente."),
    "texto_confirmado": ("Recebemos seu pedido de reserva! ✅\n\n*{servico}*\n"
                         "🗓 {data_completa}\n👥 {pessoas}\n🚐 Busca: {busca}\n\n"
                         "Nossa equipe vai confirmar e te avisar por aqui."),
    "texto_invalido": "Não entendi. Escolha uma das opções abaixo:",
}


def texto_do_no(data: dict, chave: str) -> str:
    return (data.get(chave) or "").strip() or PADROES_TEXTO[chave]


def max_pessoas(data: dict) -> int:
    try:
        n = int(data.get("max_pessoas") or MAX_PESSOAS_NO)
    except (TypeError, ValueError):
        n = MAX_PESSOAS_NO
    return max(1, min(n, MAX_PESSOAS_NO))


# ---------------------------------------------------------------------------
# Montagem das perguntas
# ---------------------------------------------------------------------------

def _corta(texto: str, limite: int) -> str:
    """Corta e apara: o corte cai no meio de uma palavra quase sempre, e sobraria
    um espaço no fim do botão. É o mesmo detalhe do rótulo em `whatsapp_client`."""
    return (texto or "")[:limite].rstrip()


def _menu(data: dict, session: dict, node_id: str, etapa: str, pergunta: str,
          itens: list, dados: dict, prefixo: str = "") -> dict:
    """A pergunta desta etapa, no formato de output do nó de Menu.

    Guarda em `awaiting` tudo que a resposta vai precisar: as opções (para
    `resolver_escolha_menu`), os rótulos (para a mensagem recebida ter texto
    quando o contato toca num botão) e o que já foi escolhido nas etapas
    anteriores.
    """
    formato = data.get("formato") or "lista"
    itens = itens[:{"lista": whatsapp.MAX_ITENS_LISTA,
                    "botoes": whatsapp.MAX_BOTOES}.get(formato, MAX_ITENS_TEXTO)]

    session["awaiting"] = {
        "node_id": node_id,
        "tipo": "agenda",
        "etapa": etapa,
        "opcoes": [{"id": i["id"], "label": i["label"]} for i in itens],
        "rotulos": {i["id"]: i["label"] for i in itens},
        "dados": dados,
        "tentativas": (session.get("awaiting") or {}).get("tentativas", 0),
    }
    corpo = f"{prefixo}\n\n{pergunta}".strip() if prefixo else pergunta
    return {
        "type": "menu",
        "content": corpo,
        "formato": formato,
        "titulo": "",
        "rodape": "",
        "botao_texto": (data.get("botao_texto") or "").strip() or "Ver opções",
        "titulo_secao": "",
        "options": [i["label"] for i in itens],
        "itens": itens,
        # Vazio sempre: os rótulos já foram cortados na montagem, e é justamente
        # o que impede a degradação para texto por causa de um nome comprido.
        "problemas": [],
    }


def _item_passeio(p: dict) -> dict:
    partes = []
    if p.get("duracao_min"):
        h, m = divmod(int(p["duracao_min"]), 60)
        partes.append(f"{h}h{m:02d}" if h and m else (f"{h}h" if h else f"{m} min"))
    if p.get("preco") is not None:
        partes.append(f"R$ {float(p['preco']):.2f} por pessoa".replace(".", ","))
    descricao = " · ".join(partes)
    if p.get("descricao"):
        descricao = f"{descricao} · {p['descricao']}" if descricao else p["descricao"]
    return {"id": p["id"], "label": _corta(p["nome"], whatsapp.LIMITE_ROTULO_ITEM),
            "descricao": _corta(descricao, whatsapp.LIMITE_DESC_ITEM)}


def _itens_pessoas(limite: int) -> list:
    # O id é o próprio número: é ele que `resolver_escolha_menu` compara com o
    # número digitado, então "4" escrito à mão cai em "4 pessoas".
    return [{"id": str(n), "label": "1 pessoa" if n == 1 else f"{n} pessoas",
             "descricao": ""} for n in range(1, limite + 1)]


# ---------------------------------------------------------------------------
# Motor do nó
# ---------------------------------------------------------------------------

async def avancar(node: dict, session: dict, outputs: list, *,
                  resposta: str = None, escolha_id: str = None) -> str:
    """Roda o nó a partir do ponto em que ele está. Devolve a saída, ou None
    quando ficou esperando resposta do contato.

    `resposta`/`escolha_id` chegam quando o contato respondeu a uma das
    perguntas; na primeira entrada no nó vêm vazios.
    """
    from flow_engine import resolver_escolha_menu  # tardio: flow_engine importa este módulo

    db = get_db()
    tenant_id = session.get("tenant_id")
    data = node.get("data") or {}
    if not tenant_id:
        return _falhar(session, outputs, data, "conversa sem empresa vinculada")

    config = await agenda.configuracao(db, tenant_id)
    estado = session.get("awaiting") or {}
    dados = dict(estado.get("dados") or {}) if estado.get("tipo") == "agenda" else {}
    etapa = estado.get("etapa") if estado.get("tipo") == "agenda" else None

    prefixo = ""
    # `escolha_id` sozinho também é resposta: quando o contato toca num botão, o
    # payload do WhatsApp chega com o id e **sem** texto. Exigir `resposta` faria
    # o nó reapresentar a mesma pergunta a cada toque, para sempre.
    if etapa and (resposta is not None or escolha_id is not None):
        escolhido = resolver_escolha_menu(estado.get("opcoes") or [],
                                          resposta or "", escolha_id)
        if escolhido is None:
            tentativas = int(estado.get("tentativas") or 0) + 1
            if tentativas >= MAX_TENTATIVAS:
                return _falhar(session, outputs, data,
                               "o contato não escolheu nenhuma das opções")
            session["awaiting"] = {**estado, "tentativas": tentativas}
            estado = session["awaiting"]
            prefixo = texto_do_no(data, "texto_invalido")
        else:
            dados[etapa] = escolhido
            session["awaiting"] = None

    # A partir daqui é sempre "qual a próxima pergunta que falta", nunca "qual
    # era a etapa anterior": o estado é o conjunto de escolhas já feitas, e cada
    # passagem preenche o que dá sozinha (passeio fixo no nó, passeio único)
    # sem gastar uma pergunta.
    return await _proxima_pergunta(db, tenant_id, node, session, outputs, data,
                                   config, dados, prefixo)


async def _proxima_pergunta(db, tenant_id, node, session, outputs, data, config,
                            dados, prefixo="") -> str:
    node_id = node["id"]

    # --- passeio ---------------------------------------------------------
    passeio = None
    passeio_id = dados.get("servico") or (data.get("service_id") or "").strip()
    passeios = await saidas.passeios_com_saida(db, tenant_id)
    if not passeios:
        return _falhar(session, outputs, data, "nenhum passeio com saídas cadastrado")
    if passeio_id:
        passeio = next((p for p in passeios if p["id"] == passeio_id), None)
        # Passeio desativado (ou sem saída) depois de o fluxo ser publicado:
        # perguntar de novo é melhor que travar, as opções atuais existem.
    if not passeio:
        if len(passeios) == 1:
            passeio = passeios[0]
        else:
            outputs.append(_menu(data, session, node_id, "servico",
                                 texto_do_no(data, "texto_servico"),
                                 [_item_passeio(p) for p in passeios], dados, prefixo))
            return None
    dados["servico"] = passeio["id"]

    # --- pessoas ---------------------------------------------------------
    try:
        pessoas = int(dados.get("pessoas") or 0)
    except (TypeError, ValueError):
        pessoas = 0
    if not 1 <= pessoas <= max_pessoas(data):
        dados.pop("pessoas", None)
        outputs.append(_menu(data, session, node_id, "pessoas",
                             texto_do_no(data, "texto_pessoas"),
                             _itens_pessoas(max_pessoas(data)), dados, prefixo))
        return None

    # --- dia -------------------------------------------------------------
    dia = dados.get("dia")
    if not dia:
        limite = max(1, min(int(data.get("dias_oferecidos") or 5), whatsapp.MAX_ITENS_LISTA))
        dias = await saidas.proximos_dias(db, tenant_id, passeio=passeio, pessoas=pessoas,
                                          config=config, limite_dias=limite)
        if not dias:
            return _falhar(session, outputs, data,
                           f"sem saídas com vaga para {pessoas} pessoa(s) em {passeio['nome']}")
        # A descrição diz a primeira saída do dia e **não** quantos lugares
        # sobraram: o WhatsApp repete a linha escolhida como se o turista a
        # tivesse escrito, e "3 vagas" vira uma mensagem dele contando à
        # agência o quanto a saída está vazia. Informação de dentro da casa.
        itens = [{"id": d["dia"], "label": _corta(d["rotulo"], whatsapp.LIMITE_ROTULO_ITEM),
                  "descricao": f"saída a partir das {d['primeira']}"}
                 for d in dias]
        outputs.append(_menu(data, session, node_id, "dia",
                             texto_do_no(data, "texto_dia"), itens, dados, prefixo))
        return None

    # --- saída -----------------------------------------------------------
    opcoes = await saidas.saidas_com_vaga(db, tenant_id, passeio=passeio, dia=dia,
                                          pessoas=pessoas, config=config)
    hora = dados.get("hora")
    if hora:
        escolhida = next((s for s in opcoes if s["inicio"] == hora), None)
        if escolhida:
            return await _embarque_ou_reservar(db, tenant_id, node, session, outputs, data,
                                               config, passeio, escolhida, pessoas, dados)
        # A saída lotou entre a pergunta e a resposta. Recomeça pelo mesmo dia
        # com a lista nova, uma vez.
        dados.pop("hora", None)
        corridas = int(dados.get("corridas") or 0) + 1
        dados["corridas"] = corridas
        if corridas > MAX_CORRIDAS:
            return _falhar(session, outputs, data, "a saída escolhida lotou")
        prefixo = "Essa saída acabou de lotar."

    if not opcoes:
        # Dia esvaziou depois de escolhido: volta a perguntar o dia em vez de
        # desistir, as Saídas têm outros dias e o turista já disse o que quer.
        dados.pop("dia", None)
        return await _proxima_pergunta(db, tenant_id, node, session, outputs, data,
                                       config, dados,
                                       prefixo or "Esse dia acabou de lotar.")

    if len(opcoes) == 1 and not prefixo:
        # Uma saída só no dia: perguntar "qual horário?" com uma opção é gastar
        # uma mensagem para o turista tocar no óbvio. O horário vai no texto de
        # sucesso, e quem não puder nele responde.
        return await _embarque_ou_reservar(db, tenant_id, node, session, outputs, data,
                                           config, passeio, opcoes[0], pessoas, dados)

    itens = [{"id": s["inicio"], "label": s["hora"], "descricao": ""} for s in opcoes]
    outputs.append(_menu(data, session, node_id, "hora",
                         texto_do_no(data, "texto_hora"), itens, dados, prefixo))
    return None


async def _embarque_ou_reservar(db, tenant_id, node, session, outputs, data, config,
                                passeio, saida, pessoas, dados) -> str:
    """Pergunta onde buscar (se for o caso) e então grava.

    A saída escolhida vai para `dados["hora"]` antes da pergunta: a resposta
    volta por `_proxima_pergunta`, que reconfere se a saída ainda tem lugar para
    o grupo antes de gravar. Se lotou nesse intervalo, o caminho de corrida
    perdida já existente oferece outra saída, e o ponto escolhido continua
    valendo para ela.
    """
    pontos = saidas.pontos_ativos(config)
    if data.get("perguntar_embarque") is False or not pontos:
        return await _reservar(db, tenant_id, node, session, outputs, data, config,
                               passeio, saida, pessoas, dados, {})

    escolha = dados.get("embarque")
    if escolha == OUTRO_LUGAR:
        return await _reservar(db, tenant_id, node, session, outputs, data, config,
                               passeio, saida, pessoas, dados,
                               {"embarque_obs": "Ponto de busca a combinar com o turista"})
    if escolha and any(p["id"] == escolha for p in pontos):
        return await _reservar(db, tenant_id, node, session, outputs, data, config,
                               passeio, saida, pessoas, dados, {"ponto_embarque_id": escolha})
    # Sem escolha, ou um ponto que foi desativado entre a pergunta e a
    # resposta: pergunta (de novo) com a lista atual.
    dados.pop("embarque", None)
    dados["hora"] = saida["inicio"]
    ini = agenda._instante(saida["inicio"])
    itens = []
    for p in pontos[:whatsapp.MAX_ITENS_LISTA - 1]:
        busca = saidas.embarque(config, ini, ponto_id=p["id"])["horario_busca"]
        itens.append({"id": p["id"], "label": _corta(p["nome"], whatsapp.LIMITE_ROTULO_ITEM),
                      "descricao": _corta(f"busca às {saidas.hora_local(busca, config)}"
                                          + (f" · {p['endereco']}" if p.get("endereco") else ""),
                                          whatsapp.LIMITE_DESC_ITEM)})
    itens.append({"id": OUTRO_LUGAR, "label": "Outro lugar",
                  "descricao": "combinamos o ponto por aqui"})
    outputs.append(_menu(data, session, node["id"], "embarque",
                         texto_do_no(data, "texto_embarque"), itens, dados))
    return None


async def _reservar(db, tenant_id, node, session, outputs, data, config, passeio,
                    saida, pessoas, dados, embarque: dict = None) -> str:
    """Grava a solicitação e devolve a saída de sucesso.

    Recebe o `node` inteiro (e não só `data`) porque o caminho de corrida perdida
    volta a perguntar, e a pergunta grava `awaiting.node_id`.
    """
    import agenda_worker

    if session.get("simulado"):
        # Chat de teste do editor: as vagas foram consultadas de verdade, mas
        # nada é gravado: um teste não pode ocupar o lugar de um turista.
        doc = {"id": "teste", "service_nome": passeio["nome"], "inicio": saida["inicio"],
               "duracao_min": passeio.get("duracao_min"), "adultos": pessoas, "criancas": 0,
               "pessoas": pessoas, **saidas.precos(passeio, pessoas, 0),
               **saidas.embarque(config, agenda._instante(saida["inicio"]),
                                 ponto_id=(embarque or {}).get("ponto_embarque_id"),
                                 obs=(embarque or {}).get("embarque_obs")),
               "contact_nome": session.get("variables", {}).get("nome_contato")}
        outputs.append({"type": "system", "content":
                        "(simulação: nada foi gravado nas Saídas)"})
    else:
        contato = await db.contacts.find_one(
            {"id": session.get("contact_id"), "tenant_id": tenant_id}, {"_id": 0})
        if not contato:
            return _falhar(session, outputs, data, "contato não encontrado")
        try:
            # O bot não sabe quantas são crianças: a pergunta é "quantas
            # pessoas". O grupo entra como adultos, e a agência ajusta ao
            # confirmar (o preço de criança só pode baixar o total).
            doc = await saidas.reservar(
                db, tenant_id, passeio=passeio, contato=contato, inicio=saida["inicio"],
                adultos=pessoas, criancas=0, config=config, origem="bot",
                session_id=session.get("id"),
                connection_id=session.get("whatsapp_connection_id"),
                **(embarque or {}),
            )
        except HTTPException as e:
            # Perdeu a corrida pelos últimos lugares. Volta para as saídas
            # daquele dia em vez de derrubar a conversa.
            dados.pop("hora", None)
            dados["corridas"] = int(dados.get("corridas") or 0) + 1
            if dados["corridas"] > MAX_CORRIDAS:
                return _falhar(session, outputs, data, str(e.detail))
            return await _proxima_pergunta(db, tenant_id, node, session, outputs,
                                           data, config, dados, str(e.detail))

    session["awaiting"] = None
    _variaveis(session, doc, config)
    outputs.append({"type": "text",
                    "content": agenda_worker.montar(
                        texto_do_no(data, "texto_confirmado"), doc, config)})
    return SAIDA_OK


def _variaveis(session: dict, doc: dict, config: dict):
    """Deixa a reserva acessível aos nós seguintes do fluxo.

    É o que permite um nó de Mensagem depois dele dizer "te esperamos
    {agendamento_data}" sem que o cliente reescreva o texto de sucesso.
    """
    import agenda_worker

    tz = agenda.fuso_de(config)
    session.setdefault("variables", {}).update({
        "agendamento_id": doc.get("id"),
        "agendamento_servico": doc.get("service_nome"),
        "agendamento_data": agenda.rotulo_completo(doc["inicio"], tz),
        "agendamento_hora": agenda.rotulo(doc["inicio"], tz),
        "agendamento_pessoas": str(doc.get("pessoas") or ""),
        "agendamento_valor": agenda_worker._moeda(doc.get("preco")),
        "agendamento_busca": saidas.rotulo_busca(doc, config),
    })


def _falhar(session: dict, outputs: list, data: dict, motivo: str) -> str:
    """Sai pela saída "não reservou", com o motivo à vista.

    O motivo vai para o histórico como mensagem de sistema, não para o turista:
    "nenhum passeio com saídas cadastrado" é um recado para a agência. O turista
    recebe o texto configurado no nó, que fala a língua dele.
    """
    session["awaiting"] = None
    session.setdefault("variables", {})["agendamento_falha"] = motivo
    outputs.append({"type": "text", "content": texto_do_no(data, "texto_sem_horario")})
    outputs.append({"type": "system", "content": f"Reserva não concluída: {motivo}"})
    return SAIDA_FALHA


# ---------------------------------------------------------------------------
# Validação na publicação
# ---------------------------------------------------------------------------

def validar(data: dict) -> list:
    """Problemas que impediriam o nó de funcionar num fluxo publicado.

    Não confere se existe passeio cadastrado: o fluxo é publicado uma vez e as
    saídas mudam todo dia, então isso viraria uma barreira que reprova hoje o
    que aprovou ontem. A falta de passeio é tratada em tempo de execução, pela
    saída "não reservou", que é o caminho que a agência configura para esse caso.
    """
    problemas = []
    mp = data.get("max_pessoas")
    if mp not in (None, ""):
        try:
            ok = 1 <= int(mp) <= MAX_PESSOAS_NO
        except (TypeError, ValueError):
            ok = False
        if not ok:
            problemas.append(f"o nó pode oferecer de 1 a {MAX_PESSOAS_NO} pessoas")
    dias = data.get("dias_oferecidos")
    if dias is not None and not (1 <= int(dias or 0) <= whatsapp.MAX_ITENS_LISTA):
        problemas.append(f"o nó pode oferecer de 1 a {whatsapp.MAX_ITENS_LISTA} dias")
    formato = data.get("formato") or "lista"
    if formato not in ("lista", "botoes", "texto"):
        problemas.append(f"formato de apresentação desconhecido: {formato}")
    if formato == "botoes":
        # Três botões não comportam a lista de pessoas nem a de dias, e a
        # degradação para texto aconteceria em quase toda pergunta.
        problemas.append("reserva não cabe em botões (no máximo 3): "
                         "use Lista de opções ou Texto numerado")
    return problemas
