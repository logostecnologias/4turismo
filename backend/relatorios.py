"""Relatórios de atendimento e de agenda.

O funil já tinha os dele (`deals.metricas`). Aqui ficam os dois que faltavam: o
que a operação de atendimento produz e o que a agenda realizou.

Duas escolhas estruturais valem explicação:

  - **Os tempos são calculados na leitura, não gravados.** `espera` é
    `assumido_em - aguardando_desde` e `atendimento` é `finalizado_em -
    assumido_em`, e o `$avg` do Mongo não subtrai duas datas guardadas como
    string ISO. No funil isso foi resolvido gravando `ciclo_dias` no
    fechamento, mas ali o campo nasceu junto com a coleção; aqui existem
    conversas antigas, e um campo novo exigiria migrar a base inteira para
    produzir um número que só é lido quando alguém abre um relatório. Então a
    conta é feita em Python sobre as conversas da janela, com projeção de
    quatro campos.

  - **A série por dia usa `$substr` no ISO.** `"2026-08-24T18:30:00+00:00"[:10]`
    é `"2026-08-24"`, e agrupar por esse prefixo dá o dia sem converter data
    nenhuma. É o único lugar em que o formato ISO em texto vira uma vantagem.
    **O relatório de agenda é a exceção**, e por um motivo concreto: o corte de
    dia dele é no fuso da empresa, então um horário das 21h em Brasília cai em
    `00:00Z` do dia seguinte e o `$substr` o arquivaria no dia errado, jogando
    as faltas de sexta para sábado. Ali o dia sai de `agenda.local()`.

Nada é truncado em silêncio: acima de `MAX_SESSOES` na janela o relatório
recusa e diz para encurtar o período, em vez de responder com meia conta.
"""
import logging
from datetime import datetime, timedelta, timezone

import agenda
import saidas

logger = logging.getLogger("turismo.relatorios")

# Teto de conversas por janela. Os tempos são calculados em Python, então o
# custo é linear no número de conversas — e é melhor pedir um período menor do
# que devolver um número que ninguém sabe que está incompleto.
MAX_SESSOES = 50000

# Lote do `$in` de mensagens quando há filtro de conexão/departamento.
LOTE_IDS = 1000

# Teto de agendamentos por janela, pela mesma razão do de conversas: as contas
# da agenda saem em Python, uma passagem por documento.
MAX_AGENDAMENTOS = 20000

# Até onde a ocupação é calculada. Os lugares oferecidos não estão gravados em
# lugar nenhum (vaga é calculada na leitura, ver `saidas.py`), então o
# denominador sai de somar a capacidade de cada saída da grade, dia a dia. Acima disto o
# relatório devolve ocupação nula em vez de gastar a volta: janela de vários
# anos é exportação, não acompanhamento.
MAX_DIAS_OCUPACAO = 400

STATUS_KANBAN = ("bot", "aguardando", "em_atendimento", "finalizado")


def _now():
    return datetime.now(timezone.utc).isoformat()


class RelatorioError(Exception):
    pass


def _segundos(inicio: str | None, fim: str | None) -> float | None:
    if not inicio or not fim:
        return None
    try:
        d = (datetime.fromisoformat(fim) - datetime.fromisoformat(inicio)).total_seconds()
    except ValueError:
        return None
    # Negativo significa dado inconsistente (relógio, ou campo preenchido fora
    # de ordem). Entra como ausente em vez de puxar a média para baixo.
    return d if d >= 0 else None


def _percentil(valores: list[float], p: float) -> float | None:
    """p90 junto com a média porque média sozinha esconde a cauda: uma fila com
    média de 4 minutos e p90 de 40 é uma operação com problema que a média
    não mostra."""
    if not valores:
        return None
    ordenados = sorted(valores)
    i = min(len(ordenados) - 1, int(round((len(ordenados) - 1) * p)))
    return ordenados[i]


def _media(valores: list[float]) -> float | None:
    return sum(valores) / len(valores) if valores else None


async def atendimento(db, tenant_id: str, de: str, ate: str,
                      connection_ids: list[str] | None = None,
                      departamento_id: str | None = None) -> dict:
    """Volume, tempos e produtividade do atendimento na janela."""
    filtro: dict = {"tenant_id": tenant_id, "created_at": {"$gte": de, "$lte": ate}}
    if connection_ids is not None:
        filtro["whatsapp_connection_id"] = {"$in": connection_ids}
    if departamento_id:
        filtro["departamento_id"] = departamento_id

    quantas = await db.conversation_sessions.count_documents(filtro)
    if quantas > MAX_SESSOES:
        raise RelatorioError(
            f"O período tem {quantas} conversas, acima do limite de {MAX_SESSOES} por "
            "relatório. Escolha um intervalo menor."
        )

    sessoes = await db.conversation_sessions.find(
        filtro,
        {"_id": 0, "id": 1, "created_at": 1, "aguardando_desde": 1, "assumido_em": 1,
         "finalizado_em": 1, "atendente_id": 1, "departamento_id": 1,
         "whatsapp_connection_id": 1, "status_atendimento": 1},
    ).to_list(MAX_SESSOES)

    esperas: list[float] = []
    atendimentos: list[float] = []
    por_dia: dict[str, dict] = {}
    por_atendente: dict[str, dict] = {}
    por_departamento: dict[str, int] = {}
    por_conexao: dict[str, int] = {}
    por_status = {s: 0 for s in STATUS_KANBAN}
    finalizadas = 0

    for s in sessoes:
        dia = (s.get("created_at") or "")[:10]
        alvo = por_dia.setdefault(dia, {"dia": dia, "conversas": 0, "recebidas": 0, "enviadas": 0})
        alvo["conversas"] += 1

        espera = _segundos(s.get("aguardando_desde"), s.get("assumido_em"))
        if espera is not None:
            esperas.append(espera)
        duracao = _segundos(s.get("assumido_em"), s.get("finalizado_em"))
        if duracao is not None:
            atendimentos.append(duracao)

        if s.get("finalizado_em"):
            finalizadas += 1
        estado = s.get("status_atendimento")
        if estado in por_status:
            por_status[estado] += 1

        if s.get("atendente_id"):
            a = por_atendente.setdefault(
                s["atendente_id"],
                {"atendente_id": s["atendente_id"], "assumidas": 0, "finalizadas": 0,
                 "_duracoes": []},
            )
            a["assumidas"] += 1
            if s.get("finalizado_em"):
                a["finalizadas"] += 1
            if duracao is not None:
                a["_duracoes"].append(duracao)
        if s.get("departamento_id"):
            por_departamento[s["departamento_id"]] = por_departamento.get(s["departamento_id"], 0) + 1
        if s.get("whatsapp_connection_id"):
            cid = s["whatsapp_connection_id"]
            por_conexao[cid] = por_conexao.get(cid, 0) + 1

    # Mensagens. Sem filtro, direto por tenant (o índice cobre); com filtro, só
    # das conversas que entraram no recorte — senão o gráfico mostraria o volume
    # da empresa inteira ao lado de números de uma conexão só.
    filtrando = connection_ids is not None or bool(departamento_id)
    recebidas = enviadas = 0
    if filtrando:
        ids = [s["id"] for s in sessoes]
        for i in range(0, len(ids), LOTE_IDS):
            r, e = await _conta_mensagens(db, tenant_id, de, ate, por_dia,
                                          ids[i:i + LOTE_IDS])
            recebidas += r
            enviadas += e
    else:
        recebidas, enviadas = await _conta_mensagens(db, tenant_id, de, ate, por_dia, None)

    for a in por_atendente.values():
        duracoes = a.pop("_duracoes")
        a["atendimento_medio_s"] = _media(duracoes)

    nomes = {
        u["id"]: (u.get("name") or u.get("email"))
        async for u in db.users.find(
            {"id": {"$in": list(por_atendente)}}, {"_id": 0, "id": 1, "name": 1, "email": 1}
        )
    } if por_atendente else {}
    for uid, a in por_atendente.items():
        a["nome"] = nomes.get(uid) or "(usuário removido)"

    deps = {
        d["id"]: d
        async for d in db.departments.find(
            {"id": {"$in": list(por_departamento)}, "tenant_id": tenant_id},
            {"_id": 0, "id": 1, "nome": 1, "cor": 1},
        )
    } if por_departamento else {}
    conns = {
        c["id"]: c
        async for c in db.whatsapp_connections.find(
            {"id": {"$in": list(por_conexao)}, "tenant_id": tenant_id},
            {"_id": 0, "id": 1, "nome": 1},
        )
    } if por_conexao else {}

    return {
        "periodo": {"de": de, "ate": ate},
        "resumo": {
            "conversas": len(sessoes),
            "finalizadas": finalizadas,
            "em_aberto": len(sessoes) - finalizadas,
            "mensagens_recebidas": recebidas,
            "mensagens_enviadas": enviadas,
        },
        "tempos": {
            "espera_media_s": _media(esperas),
            "espera_p90_s": _percentil(esperas, 0.9),
            "espera_amostra": len(esperas),
            "atendimento_medio_s": _media(atendimentos),
            "atendimento_p90_s": _percentil(atendimentos, 0.9),
            "atendimento_amostra": len(atendimentos),
        },
        "por_status": por_status,
        "por_dia": sorted(por_dia.values(), key=lambda x: x["dia"]),
        "por_atendente": sorted(por_atendente.values(),
                                key=lambda x: x["assumidas"], reverse=True),
        "por_departamento": sorted(
            [{"id": k, "nome": deps.get(k, {}).get("nome", "(excluído)"),
              "cor": deps.get(k, {}).get("cor"), "conversas": v}
             for k, v in por_departamento.items()],
            key=lambda x: x["conversas"], reverse=True),
        "por_conexao": sorted(
            [{"id": k, "nome": conns.get(k, {}).get("nome", "(excluída)"), "conversas": v}
             for k, v in por_conexao.items()],
            key=lambda x: x["conversas"], reverse=True),
    }


async def _conta_mensagens(db, tenant_id: str, de: str, ate: str,
                           por_dia: dict, session_ids: list[str] | None) -> tuple[int, int]:
    """Soma as mensagens por dia e por direção, alimentando `por_dia`."""
    match: dict = {"tenant_id": tenant_id, "timestamp": {"$gte": de, "$lte": ate}}
    if session_ids is not None:
        match["session_id"] = {"$in": session_ids}
    recebidas = enviadas = 0
    cursor = db.messages_log.aggregate([
        {"$match": match},
        # O dia sai do prefixo do próprio ISO: agrupar por `$substr` evita
        # converter data e mantém a consulta dentro do índice de `timestamp`.
        {"$group": {"_id": {"dia": {"$substr": ["$timestamp", 0, 10]},
                            "direcao": "$direction"},
                    "n": {"$sum": 1}}},
    ])
    async for row in cursor:
        dia = row["_id"]["dia"]
        direcao = row["_id"]["direcao"]
        alvo = por_dia.setdefault(dia, {"dia": dia, "conversas": 0, "recebidas": 0, "enviadas": 0})
        if direcao == "recebida":
            alvo["recebidas"] += row["n"]
            recebidas += row["n"]
        elif direcao == "enviada":
            alvo["enviadas"] += row["n"]
            enviadas += row["n"]
    return recebidas, enviadas


# ---------------------------------------------------------------------------
# Agenda
# ---------------------------------------------------------------------------

def _dia_local(iso: str | None, tz) -> str:
    """O dia do agendamento no fuso da empresa.

    É o único relatório que não pode cortar o dia pelo prefixo do ISO: um
    atendimento às 21h em Brasília está gravado como `00:00Z` do dia seguinte, e
    o corte por texto contaria a falta de sexta no sábado. Fuso da empresa e não
    do navegador porque a grade é dela: ver o cabeçalho de `agenda.py`.
    """
    if not iso:
        return ""
    try:
        return agenda.local(iso, tz).date().isoformat()
    except (ValueError, TypeError):
        # Nunca descarta a linha por causa do dia: o agendamento continua
        # contando nos totais, e o prefixo do ISO é a melhor aproximação.
        return iso[:10]


def _minutos(doc: dict) -> int:
    """Duração do atendimento, em minutos."""
    if doc.get("duracao_min"):
        return int(doc["duracao_min"])
    d = _segundos(doc.get("inicio"), doc.get("fim"))
    return int(round(d / 60)) if d else 0


def _taxa(parte: int, total: int) -> float | None:
    """Percentual, ou `None` quando não há base.

    `None` e não zero: uma agenda sem nenhum horário decidido não tem 0% de
    falta, ela não tem taxa. Mesma regra da conversão do funil, onde contar os
    negócios em aberto como perda derrubaria o número só porque o funil está
    cheio.
    """
    return round(parte / total * 100, 1) if total else None


def _lugares_da_grade(passeio: dict, config: dict, dias: list, desde=None) -> tuple:
    """Quantas saídas e quantos lugares este passeio ofereceu nos dias da janela.

    Sai da grade, dia a dia, porque lugar oferecido não existe gravado em lugar
    nenhum: `saidas.py` guarda a grade e o contador do que foi reservado, e
    calcula a vaga na leitura. Aqui o mesmo raciocínio produz o denominador da
    ocupação, já respeitando feriado da agência e exceção do passeio, que é o
    que `saidas_do_dia` resolve.
    """
    n_saidas = lugares = 0
    for d in dias:
        # Passeio cadastrado no meio da janela não tinha saída antes de existir,
        # e contar esses dias inflaria o denominador: a tela mostraria um
        # passeio vazio que nunca esteve à venda.
        if desde and d < desde:
            continue
        for item in saidas.saidas_do_dia(passeio, d, config):
            n_saidas += 1
            lugares += item["capacidade"]
    return n_saidas, lugares


async def agendamentos(db, tenant_id: str, de: str, ate: str) -> dict:
    """O que as Saídas realizaram na janela: faltas, receita, ocupação e quem reservou.

    **A janela filtra `inicio`, não `created_at`.** Tudo aqui é sobre o período
    do calendário: "agosto" é o que estava marcado para agosto, não o que foi
    marcado durante agosto. Por isso a quebra por origem responde "dos horários
    de agosto, quantos o robô marcou", e é assim que a tela precisa rotulá-la.
    Misturar os dois campos faria a soma das partes não fechar com o total.

    A taxa de falta é sobre horários **decididos** (realizado ou falta): quem
    ainda vai acontecer não é acerto nem erro, e contá-lo no denominador faria a
    taxa cair só porque a agenda da semana que vem está cheia.
    """
    config = await agenda.configuracao(db, tenant_id)
    tz = agenda.fuso_de(config)

    filtro = {"tenant_id": tenant_id, "inicio": {"$gte": de, "$lte": ate}}
    quantos = await db.appointments.count_documents(filtro)
    if quantos > MAX_AGENDAMENTOS:
        raise RelatorioError(
            f"O período tem {quantos} horários marcados, acima do limite de "
            f"{MAX_AGENDAMENTOS} por relatório. Escolha um intervalo menor."
        )

    docs = await db.appointments.find(
        filtro,
        {"_id": 0, "inicio": 1, "fim": 1, "duracao_min": 1, "preco": 1, "status": 1,
         "origem": 1, "service_id": 1, "service_nome": 1, "lembrete_enviado_em": 1,
         "pessoas": 1},
    ).to_list(MAX_AGENDAMENTOS)

    por_status = {s: 0 for s in agenda.STATUS}
    por_origem = {o: 0 for o in agenda.ORIGENS}
    receita = {"realizada": 0.0, "prevista": 0.0, "perdida": 0.0}
    sem_preco = 0
    minutos_realizados = 0
    pessoas_ocupando = pessoas_realizadas = 0
    por_dia: dict[str, dict] = {}
    por_servico: dict[str, dict] = {}
    # Efeito do lembrete, o número que decide se o módulo se paga. Não é
    # experimento controlado: quem marcou para hoje à tarde não recebe lembrete
    # nenhum, então a coluna "sem lembrete" carrega os de última hora. A tela
    # diz isso em vez de vender a comparação como causa.
    lembrete = {"com": {"decididos": 0, "faltas": 0}, "sem": {"decididos": 0, "faltas": 0}}

    for a in docs:
        estado = a.get("status")
        origem = a.get("origem")
        preco = a.get("preco")
        minutos = _minutos(a)
        dia = _dia_local(a.get("inicio"), tz)
        # Reserva de antes da fase 1 (sem grupo) conta como uma pessoa.
        pessoas = int(a.get("pessoas") or 1)
        if estado in agenda.OCUPAM:
            pessoas_ocupando += pessoas
        if estado == agenda.STATUS_CONCLUIDO:
            pessoas_realizadas += pessoas

        if estado in por_status:
            por_status[estado] += 1
        if origem in por_origem:
            por_origem[origem] += 1
        if preco is None:
            sem_preco += 1
        valor = float(preco or 0)

        if estado == agenda.STATUS_CONCLUIDO:
            receita["realizada"] += valor
            minutos_realizados += minutos
        elif estado in (agenda.STATUS_AGENDADO, agenda.STATUS_CONFIRMADO):
            receita["prevista"] += valor
        else:
            receita["perdida"] += valor

        linha = por_dia.setdefault(dia, {"dia": dia, "marcados": 0, "realizados": 0,
                                         "faltas": 0, "cancelados": 0,
                                         "equipe": 0, "whatsapp": 0})
        linha["marcados"] += 1
        if estado == agenda.STATUS_CONCLUIDO:
            linha["realizados"] += 1
        elif estado == agenda.STATUS_FALTOU:
            linha["faltas"] += 1
        elif estado == agenda.STATUS_CANCELADO:
            linha["cancelados"] += 1
        # "Quem marcou" é a pergunta que justifica o módulo, e para ela bot e IA
        # são a mesma resposta: o contato marcou sozinho pelo WhatsApp.
        if origem == "manual":
            linha["equipe"] += 1
        else:
            linha["whatsapp"] += 1

        decidido = estado in (agenda.STATUS_CONCLUIDO, agenda.STATUS_FALTOU)
        if decidido:
            lado = lembrete["com"] if a.get("lembrete_enviado_em") else lembrete["sem"]
            lado["decididos"] += 1
            if estado == agenda.STATUS_FALTOU:
                lado["faltas"] += 1

        if a.get("service_id"):
            s = _linha_passeio(por_servico, a["service_id"], a.get("service_nome"))
            s["marcados"] += 1
            s["minutos"] += minutos
            # Ocupação conta o que tomou lugar: cancelado e "não compareceu"
            # devolveram o lugar (é o que `agenda.OCUPAM` diz), e contá-los
            # mostraria uma saída cheia com a van vazia.
            if estado in agenda.OCUPAM:
                s["pessoas"] += pessoas
            if estado == agenda.STATUS_CONCLUIDO:
                s["realizados"] += 1
                s["receita"] += valor
            elif estado == agenda.STATUS_FALTOU:
                s["faltas"] += 1

    await _ocupacao_da_janela(db, tenant_id, config, tz, de, ate, por_servico)

    realizados = por_status[agenda.STATUS_CONCLUIDO]
    faltas = por_status[agenda.STATUS_FALTOU]
    cancelados = por_status[agenda.STATUS_CANCELADO]
    decididos = realizados + faltas

    for s in por_servico.values():
        s["duracao_media_min"] = round(s["minutos"] / s["marcados"]) if s["marcados"] else None
        s["taxa_falta"] = _taxa(s["faltas"], s["realizados"] + s["faltas"])
        oferecidos = s.get("lugares_oferecidos")
        # Pode passar de 100%: o encaixe (`forcar`) cria saída fora da grade de
        # propósito. Cortar em 100 esconderia exatamente o excesso que alguém
        # quer ver.
        s["ocupacao"] = _taxa(s["pessoas"], oferecidos) if oferecidos else None

    for lado in lembrete.values():
        lado["taxa_falta"] = _taxa(lado["faltas"], lado["decididos"])

    return {
        "periodo": {"de": de, "ate": ate},
        # A tela mostra dias, e o dia depende do fuso: sem dizer qual, "faltas
        # de sexta" fica sem significado para quem abre a tela em outro país.
        "fuso": str(tz),
        "resumo": {
            "marcados": len(docs),
            "realizados": realizados,
            "faltas": faltas,
            "cancelados": cancelados,
            "por_acontecer": por_status[agenda.STATUS_AGENDADO] + por_status[agenda.STATUS_CONFIRMADO],
            "confirmados": por_status[agenda.STATUS_CONFIRMADO],
            "decididos": decididos,
            "taxa_falta": _taxa(faltas, decididos),
            "taxa_cancelamento": _taxa(cancelados, len(docs)),
            "receita_realizada": round(receita["realizada"], 2),
            "receita_prevista": round(receita["prevista"], 2),
            "receita_perdida": round(receita["perdida"], 2),
            # Quantos horários não têm preço nenhum: sem este número a receita
            # pareceria baixa e ninguém saberia que o catálogo é que está sem
            # preço.
            "sem_preco": sem_preco,
            "horas_realizadas": round(minutos_realizados / 60, 1),
            # Pessoas, não reservas: uma reserva de 8 e uma de 1 são duas
            # reservas, e é por lugar que a van enche.
            "pessoas": pessoas_ocupando,
            "pessoas_realizadas": pessoas_realizadas,
        },
        "lembrete": lembrete,
        "por_origem": por_origem,
        "por_dia": sorted(por_dia.values(), key=lambda x: x["dia"]),
        "por_servico": sorted(por_servico.values(),
                              key=lambda x: (x["marcados"], x["pessoas"]), reverse=True),
    }


def _linha_passeio(por_servico: dict, sid: str, nome: str | None) -> dict:
    return por_servico.setdefault(sid, {
        "id": sid, "nome": nome or "(removido)",
        "marcados": 0, "realizados": 0, "faltas": 0, "receita": 0.0,
        "minutos": 0, "pessoas": 0,
    })


async def _ocupacao_da_janela(db, tenant_id: str, config: dict, tz, de: str, ate: str,
                              por_servico: dict) -> None:
    """Preenche `saidas_oferecidas` e `lugares_oferecidos` de cada passeio.

    Entra também o passeio **sem nenhuma reserva**: saída vazia é justamente o
    que se quer ver num relatório de ocupação, e ela não aparece em
    `appointments` por definição. Passeio desativado que não teve reserva no
    período fica fora, porque não estava à venda.
    """
    try:
        inicio = agenda.local(de, tz).date()
        fim = agenda.local(ate, tz).date()
    except (ValueError, TypeError):
        return
    if fim < inicio:
        return
    dias_na_janela = (fim - inicio).days + 1
    if dias_na_janela > MAX_DIAS_OCUPACAO:
        # Sem lugares somados não existe denominador, e devolver ocupação sem
        # ele seria inventar. A tela mostra "-" nessa coluna.
        return
    dias = [inicio + timedelta(days=i) for i in range(dias_na_janela)]

    async for passeio in db.services.find(
            {"tenant_id": tenant_id}, {"_id": 0, "id": 1, "nome": 1, "ativo": 1,
                                       "capacidade": 1, "grade_saidas": 1,
                                       "excecoes_saidas": 1, "created_at": 1}):
        linha = por_servico.get(passeio["id"])
        if linha is None:
            if not passeio.get("ativo", True):
                continue
            linha = _linha_passeio(por_servico, passeio["id"], passeio.get("nome"))
        linha["nome"] = passeio.get("nome") or linha["nome"]
        linha["ativo"] = bool(passeio.get("ativo", True))
        n, lugares = _lugares_da_grade(passeio, config, dias, desde=_nasceu_em(passeio, tz))
        linha["saidas_oferecidas"] = n
        linha["lugares_oferecidos"] = lugares


def _nasceu_em(recurso: dict, tz):
    """O dia em que o passeio passou a existir, no fuso da agência."""
    try:
        return agenda.local(recurso["created_at"], tz).date()
    except (KeyError, TypeError, ValueError):
        return None
