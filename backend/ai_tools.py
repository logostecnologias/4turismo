"""As ferramentas de Saídas do agente de IA: consultar vagas, solicitar e cancelar reserva.

Até aqui o agente só conversava. Ele podia descrever o passeio, o preço e o que
levar, mas na hora de reservar transferia para uma pessoa: e é justamente a
reserva que a agência queria automatizar. Com estas três ferramentas ele leva o
turista até a solicitação sozinho.

Decisões:

  - **O agente solicita; quem confirma é a agência.** `solicitar_reserva` grava
    uma reserva Solicitada, que segura os lugares pelo prazo configurado. A
    resposta da ferramenta diz ao modelo exatamente o que contar ao turista
    ("pedido recebido, a equipe confirma"), porque "está confirmado" seria
    mentira, e é a mentira que faz dois grupos chegarem para os últimos lugares.

  - **Os argumentos são nomes, dias e horas de parede, não ids nem ISO.** O
    argumento vai num campo estruturado, o modelo já tem o nome exato do passeio
    nas instruções, e `dia="2026-09-01", hora="08:00"` é o que ele já raciocina
    ("sábado de manhã"). Pedir ISO com fuso seria pedir para ele acertar um
    deslocamento que nem precisa conhecer: o fuso é da agência e está gravado.

  - **Nome não encontrado devolve a lista dos que existem.** O modelo se
    recupera de "não achei 'lagoa'; temos: Lagoa Azul, Circuito Lagoa Bonita"
    perguntando ao turista; devolver só "erro" o faria transferir.

  - **A ferramenta nunca levanta para o provedor.** `ai_client._rodar_ferramenta`
    já converte exceção em texto, mas os erros previsíveis (saída lotada,
    antecedência) são formulados aqui, em português e com o que fazer a seguir,
    porque é esse texto que vira a fala do agente.

  - **Solicitar não exige ter consultado antes.** A capacidade é conferida na
    gravação de qualquer jeito (`saidas.reservar`, contador atômico). Exigir a
    consulta seria uma regra que o modelo esqueceria, trocando uma reserva por
    uma transferência.

  - **Cancelar age sobre as reservas futuras daquele contato**, nunca sobre um
    id que o modelo tenha escrito. Um id inventado cancelaria a reserva de outra
    pessoa: é o único lugar do módulo em que o modelo poderia causar dano fora
    da conversa dele.
"""
import logging
import re
import unicodedata

import agenda
import ai_client
import saidas
from fastapi import HTTPException

logger = logging.getLogger("turismo.ia")

# O nome que `ai_agents.registrar_marcacao` vigia, e o prefixo da resposta que
# prova que a solicitação foi gravada. Ficam aqui para os dois lados não
# divergirem.
FERRAMENTA_RESERVA = "solicitar_reserva"
PREFIXO_RESERVADO = "SOLICITADO"

FERRAMENTAS = [
    ai_client.Ferramenta(
        nome="consultar_vagas",
        descricao=(
            "Consulta as saídas e vagas de um passeio. Use SEMPRE antes de oferecer "
            "qualquer dia ou horário: você não sabe o que tem vaga sem chamar isto. "
            "Sem o parâmetro 'dia', devolve os próximos dias com vaga para o grupo."
        ),
        parametros={
            "type": "object",
            "properties": {
                "passeio": {"type": "string",
                            "description": "Nome do passeio, exatamente como está na lista de passeios."},
                "dia": {"type": "string",
                        "description": "Dia no formato AAAA-MM-DD. Omita para ver os próximos dias com vaga."},
                "pessoas": {"type": "integer",
                            "description": "Quantas pessoas vão, contando as crianças. Padrão 1."},
            },
            "required": ["passeio"],
        },
    ),
    ai_client.Ferramenta(
        nome=FERRAMENTA_RESERVA,
        descricao=(
            "Registra o pedido de reserva numa saída, segurando os lugares até a "
            "agência confirmar. Só chame depois que o turista escolheu o passeio, o "
            "dia, o horário de saída e disse quantos adultos e quantas crianças vão. "
            "Isto NÃO confirma a reserva: depois de chamar, diga ao turista que o "
            "pedido foi recebido e que a equipe vai confirmar."
        ),
        parametros={
            "type": "object",
            "properties": {
                "passeio": {"type": "string", "description": "Nome do passeio."},
                "dia": {"type": "string", "description": "Dia no formato AAAA-MM-DD."},
                "hora": {"type": "string", "description": "Horário da saída, HH:MM (hora local da agência)."},
                "adultos": {"type": "integer", "description": "Quantos adultos."},
                "criancas": {"type": "integer", "description": "Quantas crianças. 0 se nenhuma."},
                "ponto_embarque": {"type": "string",
                                   "description": "Onde buscar o grupo: o nome de um dos pontos de embarque da lista, ou o hotel/endereço que o turista disse se não estiver nela."},
                "observacao": {"type": "string",
                               "description": "O que o turista disse que importa para o passeio: hotel ou ponto de busca, idades das crianças, restrições."},
            },
            "required": ["passeio", "dia", "hora", "adultos"],
        },
    ),
    ai_client.Ferramenta(
        nome="cancelar_reserva",
        descricao=(
            "Cancela uma reserva deste turista (solicitada ou confirmada). Chame quando "
            "ele pedir para cancelar. Se ele tiver mais de uma reserva futura, a "
            "ferramenta devolve a lista para você perguntar qual."
        ),
        parametros={
            "type": "object",
            "properties": {
                "dia": {"type": "string",
                        "description": "AAAA-MM-DD da reserva a cancelar. Só é necessário se houver mais de uma."},
                "motivo": {"type": "string", "description": "Motivo, se o turista disser."},
            },
            "required": [],
        },
    ),
]


# ---------------------------------------------------------------------------
# Casamento de nome
# ---------------------------------------------------------------------------

def _normaliza(texto: str) -> str:
    texto = unicodedata.normalize("NFKD", texto or "")
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9\s]", " ", texto.lower())).strip()


def _casar(nome: str, itens: list) -> dict:
    """Acha o item pelo nome. Igualdade normalizada primeiro, depois contido.

    O "contido" cobre o caso real: as instruções dizem "Lagoa Azul" e o modelo
    escreve "lagoa azul de manhã". Um empate devolve o primeiro, que numa lista
    ordenada é estável.
    """
    alvo = _normaliza(nome)
    if not alvo:
        return None
    for i in itens:
        if _normaliza(i.get("nome")) == alvo:
            return i
    for i in itens:
        n = _normaliza(i.get("nome"))
        if n and (alvo in n or n in alvo):
            return i
    return None


def _lista(itens: list) -> str:
    return ", ".join(i.get("nome") or "?" for i in itens) or "(nenhum)"


def _inteiro(valor, padrao: int = 0) -> int:
    try:
        return int(valor if valor not in (None, "") else padrao)
    except (TypeError, ValueError):
        return padrao


def _moeda(valor) -> str:
    inteiro, centavos = f"{float(valor):,.2f}".split(".")
    return f"R$ {inteiro.replace(',', '.')},{centavos}"


# ---------------------------------------------------------------------------
# Catálogo para as instruções
# ---------------------------------------------------------------------------

def _resumo_grade(p: dict) -> str:
    """"08:00 e 14:00, de segunda a sábado", curto, para o prompt."""
    grade = p.get("grade_saidas") or []
    dias = [agenda.DIAS[i].lower() for i, d in enumerate(grade) if d]
    horas = sorted({s["hora"] for d in grade for s in (d or [])})
    if not horas:
        return "sem saídas cadastradas"
    return f"saídas às {', '.join(horas)}; dias: {', '.join(dias)}"


def bloco_de_instrucoes(passeios: list, config: dict) -> str:
    """O que o modelo precisa saber dos passeios, no bloco estável do prompt.

    Vai nas instruções, que são o prefixo cacheado, e não numa ferramenta
    `listar_passeios`: é informação curta, idêntica em toda chamada, e uma volta
    de ferramenta para buscá-la custaria uma chamada inteira ao provedor por
    conversa. Nada aqui varia por contato, que é a condição para o cache pegar.

    Vagas **não** entram aqui: mudam a cada reserva, quebrariam o cache em toda
    mensagem, e envelheceriam no meio da conversa. Vaga é sempre ferramenta.
    """
    linhas = ["Saídas: você pode consultar vagas e registrar pedidos de reserva."]

    if passeios:
        linhas.append("\nPasseios (use o nome exato):")
        for p in passeios:
            detalhes = []
            if p.get("duracao_min"):
                h, m = divmod(int(p["duracao_min"]), 60)
                detalhes.append(f"{h}h{m:02d}" if h else f"{m} min")
            if p.get("preco") is not None:
                detalhes.append(f"{_moeda(p['preco'])} por adulto")
                cri = p.get("preco_crianca")
                if cri is not None and cri != p.get("preco"):
                    detalhes.append("criança grátis" if not cri else f"{_moeda(cri)} por criança")
            detalhes.append(_resumo_grade(p))
            linha = f"- {p['nome']} ({'; '.join(detalhes)})"
            if p.get("descricao"):
                linha += f": {p['descricao']}"
            linhas.append(linha)
    else:
        linhas.append("\nNenhum passeio com saídas cadastrado: não prometa reserva.")

    pontos = saidas.pontos_ativos(config)
    if pontos:
        linhas.append("\nPontos de embarque (onde a van busca; use o nome exato):")
        for pt in pontos:
            linha = f"- {pt['nome']} (busca {pt.get('minutos_antes') or 0} min antes da saída)"
            if pt.get("endereco"):
                linha += f": {pt['endereco']}"
            linhas.append(linha)
        linhas.append("Pergunte onde o grupo está hospedado. Se não for um destes, passe o "
                      "hotel ou endereço que o turista disser: a equipe combina a busca.")

    linhas.append(
        f"\nO fuso da agência é {config.get('fuso')}; use sempre a hora local dela. "
        f"É preciso reservar com pelo menos {config.get('antecedencia_min_horas')}h de "
        "antecedência. Crianças ocupam lugar.\n"
        "Antes de reservar, pergunte quantos adultos e quantas crianças vão. Nunca "
        "invente vaga: chame consultar_vagas antes de oferecer qualquer dia ou "
        "horário, e ofereça no máximo três opções de uma vez.\n"
        "O que você registra é um PEDIDO de reserva: nunca diga que a reserva está "
        "confirmada. Depois de solicitar_reserva, diga que o pedido foi recebido e "
        "que a equipe vai confirmar por aqui."
    )
    # A ferramenta devolve quantas vagas sobraram porque isso ajuda o modelo a
    # escolher. Repetir o número ao turista é contar a ele o quanto a saída está
    # vazia, que é informação de dentro da casa, e cria pressa falsa quando cheia.
    linhas.append(
        "A quantidade de vagas é para você se orientar, não para contar ao turista: "
        "diga que há vaga, nunca quantas sobraram."
    )
    return "\n".join(linhas)


# ---------------------------------------------------------------------------
# Executor
# ---------------------------------------------------------------------------

def executor(db, tenant_id: str, contato: dict, session: dict, simulado: bool = False):
    """Devolve o `async (nome, argumentos) -> str` que o `ai_client` chama.

    Fechado sobre a conversa: o contato e a conexão saem daqui, nunca de um
    argumento do modelo. É o que impede uma ferramenta de reservar ou cancelar
    para outra pessoa.

    `simulado` é o chat de teste da tela de agentes, e segue a mesma regra do
    `session["simulado"]` do editor de fluxos: a **consulta** roda de verdade
    (é ela que prova que a grade e os passeios estão certos) e a **escrita** não
    acontece. Testar um agente não pode ocupar o lugar de um turista.
    """

    async def _passeio(nome: str):
        passeios = await saidas.passeios_com_saida(db, tenant_id)
        if not passeios:
            return None, "A agência ainda não cadastrou passeios com saídas."
        p = _casar(nome, passeios)
        if not p:
            return None, f"Não existe passeio com esse nome. Os passeios são: {_lista(passeios)}."
        return p, None

    async def consultar_vagas(args):
        passeio, erro = await _passeio(args.get("passeio"))
        if erro:
            return erro
        pessoas = max(1, _inteiro(args.get("pessoas"), 1))
        config = await agenda.configuracao(db, tenant_id)

        dia = (args.get("dia") or "").strip()
        try:
            if not dia:
                dias = await saidas.proximos_dias(db, tenant_id, passeio=passeio,
                                                  pessoas=pessoas, config=config,
                                                  limite_dias=5)
                if not dias:
                    return (f"Não há vaga para {pessoas} pessoa(s) em {passeio['nome']} "
                            "nos próximos dias. Ofereça outro passeio ou transfira "
                            "para um atendente.")
                return (f"Próximos dias com vaga para {pessoas} pessoa(s) em "
                        f"{passeio['nome']}: "
                        + "; ".join(f"{d['dia']} ({d['rotulo']}, primeira saída {d['primeira']})"
                                    for d in dias)
                        + ". Chame de novo com o dia escolhido para ver os horários.")

            opcoes = await saidas.saidas_com_vaga(db, tenant_id, passeio=passeio, dia=dia,
                                                  pessoas=pessoas, config=config)
        except HTTPException as e:
            return f"ERRO: {e.detail}"
        if not opcoes:
            proximos = await saidas.proximos_dias(db, tenant_id, passeio=passeio,
                                                  pessoas=pessoas, config=config,
                                                  a_partir_de=dia, limite_dias=3)
            if not proximos:
                return f"Não há vaga para {pessoas} pessoa(s) em {dia} nem nos dias seguintes."
            return (f"Sem vaga para {pessoas} pessoa(s) em {dia}. Próximos dias com vaga: "
                    + "; ".join(f"{d['dia']} ({d['rotulo']})" for d in proximos) + ".")
        return (f"Saídas de {passeio['nome']} em {dia} com vaga para {pessoas} pessoa(s): "
                + ", ".join(f"{s['hora']} ({s['vagas']} vagas)" for s in opcoes)
                + f". Fuso: {config.get('fuso')}.")

    async def solicitar(args):
        passeio, erro = await _passeio(args.get("passeio"))
        if erro:
            return erro
        config = await agenda.configuracao(db, tenant_id)
        tz = agenda.fuso_de(config)
        dia, hora = (args.get("dia") or "").strip(), (args.get("hora") or "").strip()
        if not dia or not hora:
            return "ERRO: informe dia (AAAA-MM-DD) e hora da saída (HH:MM)."
        adultos, criancas = _inteiro(args.get("adultos")), _inteiro(args.get("criancas"))
        if adultos + criancas < 1:
            return "ERRO: pergunte ao turista quantos adultos e quantas crianças vão."
        try:
            inicio = agenda._combina(agenda._dia(dia), agenda._hhmm(hora), tz)
        except HTTPException as e:
            return f"ERRO: {e.detail}"

        pessoas = adultos + criancas
        alvo = agenda._iso(inicio)
        try:
            opcoes = await saidas.saidas_com_vaga(db, tenant_id, passeio=passeio, dia=dia,
                                                  pessoas=pessoas, config=config)
        except HTTPException as e:
            return f"ERRO: {e.detail}"
        if not any(s["inicio"] == alvo for s in opcoes):
            if not opcoes:
                return (f"Não há saída às {hora} de {dia} com vaga para {pessoas} "
                        "pessoa(s), e o dia não tem outra. Consulte outro dia.")
            return (f"A saída das {hora} em {dia} não tem vaga para {pessoas} pessoa(s). "
                    "Com vaga nesse dia: " + ", ".join(s["hora"] for s in opcoes)
                    + ". Ofereça uma destas ao turista.")

        # Ponto da lista pelo nome; fora dela, o que o turista disse vira o
        # lugar da busca com a hora a combinar. Nunca recusar por isso: o hotel
        # que não está cadastrado é o caso comum, não o erro.
        embarque_args = {}
        local = (args.get("ponto_embarque") or "").strip()
        if local:
            ponto = _casar(local, saidas.pontos_ativos(config))
            embarque_args = ({"ponto_embarque_id": ponto["id"]} if ponto
                             else {"ponto_embarque_nome": local[:120]})
        busca = saidas.rotulo_busca(saidas.embarque(
            config, inicio, ponto_id=embarque_args.get("ponto_embarque_id"),
            ponto_nome=embarque_args.get("ponto_embarque_nome")), config)

        resumo = (f"{passeio['nome']}, {agenda.rotulo_completo(alvo, tz)}, "
                  f"{saidas.rotulo_pessoas(adultos, criancas)}, busca: {busca}")
        total = saidas.precos(passeio, adultos, criancas)["preco"]
        if total is not None:
            resumo += f", total {_moeda(total)}"
        if simulado:
            return (f"{PREFIXO_RESERVADO} (simulação: nada foi gravado). Diga ao turista "
                    f"que o pedido foi recebido e que a equipe vai confirmar: {resumo}.")
        try:
            await saidas.reservar(
                db, tenant_id, passeio=passeio, contato=contato, inicio=alvo,
                adultos=adultos, criancas=criancas, config=config, origem="ia",
                session_id=(session or {}).get("id"),
                connection_id=(session or {}).get("whatsapp_connection_id"),
                observacoes=args.get("observacao"),
                **embarque_args,
            )
        except HTTPException as e:
            return f"NÃO FOI SOLICITADO: {e.detail}"
        return (f"{PREFIXO_RESERVADO} com sucesso (ainda não confirmado). Diga ao turista "
                f"que o pedido foi recebido e que a equipe vai confirmar por aqui: {resumo}.")

    async def cancelar_tool(args):
        futuras = await saidas.reservas_do_contato(db, tenant_id, contato["id"])
        if not futuras:
            return "Este turista não tem nenhuma reserva futura para cancelar."
        config = await agenda.configuracao(db, tenant_id)
        tz = agenda.fuso_de(config)

        def _dia_local(a):
            return agenda.local(a["inicio"], tz).date().isoformat()

        alvo = futuras[0]
        dia = (args.get("dia") or "").strip()
        if len(futuras) > 1:
            candidatas = [a for a in futuras if _dia_local(a) == dia] if dia else []
            if len(candidatas) != 1:
                return ("Este turista tem mais de uma reserva: "
                        + "; ".join(f"{a['service_nome']} em "
                                    f"{agenda.rotulo_completo(a['inicio'], tz)} "
                                    f"(dia {_dia_local(a)})" for a in futuras)
                        + ". Pergunte qual ele quer cancelar e chame de novo com o dia.")
            alvo = candidatas[0]
        elif dia and _dia_local(futuras[0]) != dia:
            return (f"A única reserva é em {_dia_local(futuras[0])}, não em {dia}. "
                    "Confirme com o turista.")

        if simulado:
            return ("CANCELADO (simulação: nada foi gravado). A reserva seria a de "
                    f"{agenda.rotulo_completo(alvo['inicio'], tz)}.")
        try:
            await saidas.cancelar(db, tenant_id, alvo["id"], por="ia",
                                  motivo=args.get("motivo") or "Cancelado pelo turista")
        except HTTPException as e:
            return f"NÃO FOI CANCELADO: {e.detail}"
        return ("CANCELADO. Confirme ao turista que a reserva de "
                f"{alvo['service_nome']} em {agenda.rotulo_completo(alvo['inicio'], tz)} "
                "foi cancelada.")

    acoes = {
        "consultar_vagas": consultar_vagas,
        FERRAMENTA_RESERVA: solicitar,
        "cancelar_reserva": cancelar_tool,
    }

    async def executar(nome: str, argumentos: dict) -> str:
        acao = acoes.get(nome)
        if not acao:
            return f"ERRO: ferramenta desconhecida ({nome})."
        return await acao(argumentos or {})

    return executar
