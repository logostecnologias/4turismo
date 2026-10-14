"""Saídas e vagas: a grade de saídas de cada passeio, as vagas e a reserva.

Substitui, para o turismo, a agenda de horário que veio da base. Lá o recurso
era uma pessoa e o conflito era de **intervalo** (um atendimento de 2h às 14h
colide com um de 30min às 15h); aqui o recurso é a saída de um passeio e o
conflito é de **capacidade**: a saída das 8h da Lagoa Azul tem 30 lugares, um
grupo reserva 4, outro reserva 7. Duas reservas na mesma saída não colidem;
colidem quando juntas passam do total de lugares.

Cinco decisões moldam o módulo:

  - **A vaga livre continua calculada na leitura, nunca gravada como slot.** O
    que se guarda é a grade (poucas linhas no passeio) e quantos lugares já
    foram reservados; a vaga é a capacidade menos o reservado. É o mesmo
    raciocínio de `agenda.py`: slot gravado fica errado no instante em que
    alguém muda a grade.

  - **O reservado é um contador por saída, mudado só por operação atômica.** O
    documento de `saidas` nasce na primeira reserva daquela saída (chave única
    empresa + passeio + instante) e reservar é um `find_one_and_update` com
    `reservados <= capacidade - n` no filtro e `$inc` no update: ou o grupo
    inteiro entra, ou nada muda. Contador cabe num índice e intervalo não, e é
    por isso que aqui não existe o "insere, confere e recua" da agenda de
    horário: não há janela entre conferir e gravar, as duas coisas são a mesma
    operação no banco.

  - **O contador é a soma das pessoas das reservas que ocupam lugar**
    (`agenda.OCUPAM`: solicitada, confirmada, realizada), e toda mudança de
    status passa por `_transicao`, que é o único lugar que mexe no contador
    junto com o status. Um `$set` de status avulso deixaria o contador contando
    uma reserva cancelada para sempre, e a saída apareceria lotada com lugar
    sobrando.

  - **A capacidade não é gravada no contador.** Ela sai da grade no momento da
    reserva: a agência que troca a van de 15 lugares pela de 20 muda o passeio
    e as saídas já existentes passam a ter 20, sem migrar nada. Baixar a
    capacidade abaixo do já reservado não cancela ninguém; a saída só fica sem
    vaga.

  - **Solicitação segura a vaga por um prazo.** Bot e IA só criam reserva
    `agendado` (Solicitada): quem confirma é a agência. Sem segurar a vaga, a
    agência confirmaria dois grupos para os últimos lugares; segurando para
    sempre, uma solicitação esquecida trancaria a saída. Daí `expira_em`
    (`scheduling_settings.prazo_solicitacao_horas`, 24h por padrão), varrido
    pelo `agenda_worker` e também na hora de reservar, quando a saída parece
    cheia: é ali que um lugar preso por solicitação vencida faz diferença.

Os status gravados são os mesmos da agenda de horário (`agendado`,
`confirmado`, `concluido`, `faltou`, `cancelado`) e só o rótulo da tela mudou:
é o que mantém a tela Hoje, o lembrete e os relatórios lendo o mesmo campo.
"""
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone

from fastapi import HTTPException
from pymongo.errors import DuplicateKeyError

import agenda

logger = logging.getLogger("turismo.saidas")

MIN_CAPACIDADE = 1
MAX_CAPACIDADE = 500

# Teto de pessoas numa reserva. Um grupo de 100 é excursão, e excursão é
# negociação, não reserva pelo WhatsApp; o teto existe para um "1000" digitado
# por engano não trancar a saída inteira.
MAX_PESSOAS = 100

# Teto de dias numa consulta de ocupação. A tela mostra semana ou mês; acima
# disso é relatório.
MAX_DIAS_JANELA = 62

# Teto de saídas num dia de um passeio. Passeio com mais de 24 saídas por dia
# é transfer de hora em hora, e a grade vira lista ilegível no WhatsApp.
MAX_SAIDAS_DIA = 24

# Quantas solicitações vencidas uma passada do worker cancela. Mesmo motivo do
# `MAX_POR_PASSADA` dos lembretes: nada de rajada.
MAX_EXPIRADAS_POR_PASSADA = 200

PRAZO_SOLICITACAO_PADRAO = 24


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Grade de saídas do passeio
# ---------------------------------------------------------------------------

def _capacidade_valida(valor, rotulo: str) -> int:
    try:
        n = int(valor)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail=f"{rotulo}: capacidade inválida")
    if not MIN_CAPACIDADE <= n <= MAX_CAPACIDADE:
        raise HTTPException(
            status_code=400,
            detail=f"{rotulo}: a capacidade vai de {MIN_CAPACIDADE} a {MAX_CAPACIDADE} lugares")
    return n


def _lista_de_saidas(itens, rotulo: str) -> list:
    """Uma lista de saídas de um dia, validada: HH:MM, sem hora repetida."""
    limpo = []
    for item in itens or []:
        h = agenda._hhmm((item or {}).get("hora"))
        cap = item.get("capacidade")
        limpo.append({"hora": f"{h.hour:02d}:{h.minute:02d}",
                      "capacidade": None if cap in (None, "") else _capacidade_valida(cap, rotulo)})
    limpo.sort(key=lambda s: s["hora"])
    for a, b in zip(limpo, limpo[1:]):
        if a["hora"] == b["hora"]:
            # Duas saídas na mesma hora seriam a mesma saída com duas
            # capacidades, e o contador (chave passeio + instante) é um só.
            raise HTTPException(status_code=400,
                                detail=f"{rotulo}: a saída das {a['hora']} aparece duas vezes")
    if len(limpo) > MAX_SAIDAS_DIA:
        raise HTTPException(status_code=400,
                            detail=f"{rotulo}: no máximo {MAX_SAIDAS_DIA} saídas por dia")
    return limpo


def validar_grade_saidas(grade) -> list:
    """A grade semanal como vai para o banco: 7 dias, 0 = segunda."""
    if grade is None:
        return [[] for _ in range(7)]
    if not isinstance(grade, list) or len(grade) != 7:
        raise HTTPException(status_code=400,
                            detail="A grade de saídas precisa ter os 7 dias da semana")
    return [_lista_de_saidas(dia, agenda.DIAS[i]) for i, dia in enumerate(grade)]


def validar_excecoes(excecoes) -> list:
    """Exceções por data: dia fechado (com motivo) ou saídas próprias daquele dia."""
    saida, vistas = [], set()
    for e in excecoes or []:
        d = agenda._dia((e or {}).get("data")).isoformat()
        if d in vistas:
            raise HTTPException(status_code=400,
                                detail=f"Há duas exceções para o dia {d}")
        vistas.add(d)
        fechado = bool(e.get("fechado", True))
        saida.append({
            "data": d, "fechado": fechado,
            "motivo": (e.get("motivo") or "").strip() or None,
            "saidas": [] if fechado else _lista_de_saidas(e.get("saidas"), f"Exceção de {d}"),
        })
    return sorted(saida, key=lambda e: e["data"])


def saidas_do_dia(passeio: dict, d: date, config: dict) -> list:
    """As saídas deste passeio neste dia: `[{"hora": time, "capacidade": int}]`.

    Precedência igual à da agenda de horário: feriado da agência fecha tudo,
    exceção do passeio (dia fechado ou saídas próprias) ganha da grade semanal, e
    sem nenhuma das duas vale a grade.
    """
    if agenda.bloqueado(config, d):
        return []
    alvo = d.isoformat()
    itens = None
    for e in passeio.get("excecoes_saidas") or []:
        if (e.get("data") or "")[:10] == alvo:
            itens = [] if e.get("fechado", True) else (e.get("saidas") or [])
            break
    if itens is None:
        grade = passeio.get("grade_saidas") or []
        itens = (grade[d.weekday()] or []) if len(grade) == 7 else []

    padrao = int(passeio.get("capacidade") or 0)
    saida = []
    for item in itens:
        try:
            h = agenda._hhmm(item.get("hora"))
        except HTTPException:
            continue
        cap = item.get("capacidade")
        saida.append({"hora": h, "capacidade": int(cap) if cap not in (None, "") else padrao})
    return sorted(saida, key=lambda s: s["hora"])


def capacidade_em(passeio: dict, ini: datetime, config: dict) -> int | None:
    """A capacidade da saída que começa neste instante, ou None se não há saída."""
    tz = agenda.fuso_de(config)
    local = ini.astimezone(tz)
    for s in saidas_do_dia(passeio, local.date(), config):
        if s["hora"] == local.time().replace(second=0, microsecond=0):
            return s["capacidade"]
    return None


def resumo_grade(passeio: dict) -> bool:
    """O passeio tem alguma saída na grade semanal? É o critério de "pronto"."""
    return any(dia for dia in (passeio.get("grade_saidas") or []))


async def passeios_ativos(db, tenant_id: str) -> list:
    return await db.services.find(
        {"tenant_id": tenant_id, "ativo": True}, {"_id": 0}
    ).sort([("ordem", 1), ("nome", 1)]).to_list(200)


async def passeios_com_saida(db, tenant_id: str) -> list:
    """Os passeios que o bot e a IA podem oferecer: ativos e com grade."""
    return [p for p in await passeios_ativos(db, tenant_id) if resumo_grade(p)]


# ---------------------------------------------------------------------------
# Ocupação
# ---------------------------------------------------------------------------

def _chave(tenant_id: str, service_id: str, inicio: str) -> dict:
    return {"tenant_id": tenant_id, "service_id": service_id, "inicio": inicio}


async def contadores(db, tenant_id: str, service_ids: list, de_iso: str,
                     ate_iso: str) -> dict:
    """`{(service_id, inicio): documento da saída}` na janela, numa consulta só.

    O documento é o contador **e** o estado da saída (cancelada, guia,
    veículo): tudo que é daquela saída específica, e não do passeio, mora
    nele. É por isso que ele pode existir sem reserva nenhuma, quando a agência
    escala o guia antes de vender o primeiro lugar.
    """
    if not service_ids:
        return {}
    cursor = db.saidas.find(
        {"tenant_id": tenant_id, "service_id": {"$in": list(service_ids)},
         "inicio": {"$gte": de_iso, "$lt": ate_iso}},
        {"_id": 0},
    )
    return {(c["service_id"], c["inicio"]): c async for c in cursor}


def _limites(config: dict, agora: datetime) -> tuple:
    piso = agora + timedelta(hours=float(config.get("antecedencia_min_horas") or 0))
    teto = agora + timedelta(days=int(config.get("antecedencia_max_dias") or 365))
    return piso, teto


async def saidas_da_janela(db, tenant_id: str, *, passeios: list, de: date, ate: date,
                           config: dict, agora: datetime = None) -> list:
    """As saídas de cada dia da janela com capacidade, reservado e vaga.

    É o que a tela de Saídas mostra e o que o bot e a IA consultam: uma função
    só, para a tela nunca mostrar vaga que o bot recusa. Os contadores saem numa
    consulta para a janela inteira, não uma por dia: a ida e volta ao banco é
    o custo, não o volume.

    Uma saída fora da grade que tenha reserva (encaixe feito na tela, ou grade
    mudada depois da reserva) aparece como `extra`: some da oferta, mas não
    pode sumir da tela, senão o grupo reservado fica invisível.
    """
    if (ate - de).days + 1 > MAX_DIAS_JANELA:
        raise HTTPException(status_code=400,
                            detail=f"Consulte no máximo {MAX_DIAS_JANELA} dias de uma vez")
    if ate < de:
        raise HTTPException(status_code=400, detail="A data final vem antes da inicial")
    tz = agenda.fuso_de(config)
    agora = agora or agenda._agora()
    piso, teto = _limites(config, agora)

    ini_janela = agenda._combina(de, time(0, 0), tz)
    fim_janela = agenda._combina(ate + timedelta(days=1), time(0, 0), tz)
    cont = await contadores(db, tenant_id, [p["id"] for p in passeios],
                            agenda._iso(ini_janela), agenda._iso(fim_janela))
    por_id = {p["id"]: p for p in passeios}

    dias = []
    d = de
    while d <= ate:
        itens, vistos = [], set()
        for p in passeios:
            for s in saidas_do_dia(p, d, config):
                inicio_dt = agenda._combina(d, s["hora"], tz)
                inicio = agenda._iso(inicio_dt)
                vistos.add((p["id"], inicio))
                itens.append(_linha(p, inicio, inicio_dt, s["capacidade"],
                                    cont.get((p["id"], inicio)), piso, teto, tz))
        dias.append({"dia": d.isoformat(),
                     "rotulo": f"{agenda.DIAS[d.weekday()]} {d.day:02d}/{d.month:02d}",
                     "bloqueio": agenda.bloqueado(config, d),
                     "saidas": itens})
        d += timedelta(days=1)

    # Encaixes: contador com gente, numa hora que a grade não tem. Saída fora da
    # grade sem ninguém (o guia escalado numa saída que depois saiu da grade)
    # não aparece: não há grupo para ficar invisível.
    por_dia = {x["dia"]: x for x in dias}
    for (sid, inicio), doc in cont.items():
        if int(doc.get("reservados") or 0) <= 0 or (sid, inicio) in vistos or sid not in por_id:
            continue
        inicio_dt = agenda.local(inicio, tz)
        alvo = por_dia.get(inicio_dt.date().isoformat())
        if alvo is not None:
            p = por_id[sid]
            linha = _linha(p, inicio, inicio_dt, int(p.get("capacidade") or 0), doc, piso, teto, tz)
            linha["extra"] = True
            alvo["saidas"].append(linha)
    for x in dias:
        x["saidas"].sort(key=lambda s: (s["inicio"], s["service_nome"] or ""))
    return dias


def _linha(p, inicio, inicio_dt, capacidade, doc, piso, teto, tz) -> dict:
    doc = doc or {}
    local = inicio_dt.astimezone(tz)
    reservados = int(doc.get("reservados") or 0)
    cancelada = bool(doc.get("cancelada"))
    return {
        "service_id": p["id"], "service_nome": p.get("nome"), "cor": p.get("cor"),
        "inicio": inicio, "hora": f"{local.hour:02d}:{local.minute:02d}",
        "capacidade": capacidade, "reservados": reservados,
        # Saída cancelada não tem vaga, mesmo com lugar sobrando: é o que tira
        # ela da oferta do bot e da IA sem cada um precisar saber de cancelamento.
        "vagas": 0 if cancelada else max(0, capacidade - reservados),
        "aberta": (not cancelada) and piso <= inicio_dt <= teto,
        "extra": False,
        "cancelada": cancelada,
        "motivo_cancelamento": doc.get("motivo_cancelamento") if cancelada else None,
        "guia_id": doc.get("guia_id"), "guia_nome": doc.get("guia_nome"),
        "veiculo_id": doc.get("veiculo_id"), "veiculo_nome": doc.get("veiculo_nome"),
        "equipe_obs": doc.get("equipe_obs"),
    }


async def saidas_com_vaga(db, tenant_id: str, *, passeio: dict, dia: str, pessoas: int,
                          config: dict, agora: datetime = None) -> list:
    """As saídas abertas deste dia que comportam o grupo inteiro."""
    d = agenda._dia(dia)
    dias = await saidas_da_janela(db, tenant_id, passeios=[passeio], de=d, ate=d,
                                  config=config, agora=agora)
    return [s for s in dias[0]["saidas"]
            if s["aberta"] and not s["extra"] and s["vagas"] >= pessoas]


async def proximos_dias(db, tenant_id: str, *, passeio: dict, pessoas: int, config: dict,
                        a_partir_de: str = None, limite_dias: int = 5,
                        agora: datetime = None) -> list:
    """Os próximos dias com alguma saída que comporte o grupo.

    Por isso o bot pergunta quantas pessoas **antes** do dia: oferecer um dia em
    que o grupo não cabe é o jeito mais rápido de fazer o turista desistir.
    """
    tz = agenda.fuso_de(config)
    agora = agora or agenda._agora()
    inicio = agenda._dia(a_partir_de) if a_partir_de else agora.astimezone(tz).date()
    alcance = min(agenda.MAX_DIAS_VARREDURA, int(config.get("antecedencia_max_dias") or 60))
    fim = inicio + timedelta(days=min(alcance, MAX_DIAS_JANELA - 1))
    dias = await saidas_da_janela(db, tenant_id, passeios=[passeio], de=inicio, ate=fim,
                                  config=config, agora=agora)
    saida = []
    for x in dias:
        cabem = [s for s in x["saidas"] if s["aberta"] and not s["extra"] and s["vagas"] >= pessoas]
        if cabem:
            saida.append({"dia": x["dia"], "rotulo": x["rotulo"],
                          "saidas": len(cabem), "primeira": cabem[0]["hora"]})
            if len(saida) >= limite_dias:
                break
    return saida


# ---------------------------------------------------------------------------
# Contador
# ---------------------------------------------------------------------------

async def _ocupa(db, tenant_id: str, service_id: str, inicio: str, n: int,
                 capacidade: int) -> dict | None:
    """Reserva `n` lugares se couberem. Devolve o contador, ou None se não coube.

    O upsert cria o contador zerado na primeira reserva da saída. Duas conversas
    criando a mesma saída ao mesmo tempo batem no índice único e uma delas recebe
    `DuplicateKeyError`, que aqui quer dizer só "já existe": o `$inc` condicional
    seguinte é que decide quem entra.
    """
    chave = _chave(tenant_id, service_id, inicio)
    try:
        await db.saidas.update_one(
            chave,
            {"$setOnInsert": {"id": str(uuid.uuid4()), "reservados": 0, "created_at": _now()}},
            upsert=True)
    except DuplicateKeyError:
        pass
    # `cancelada` entra no mesmo filtro do `$inc`: cancelar a saída e reservar
    # nela ao mesmo tempo não pode deixar um grupo novo dentro de uma saída que
    # a agência acabou de cancelar por chuva. Ver `cancelar_saida`.
    return await db.saidas.find_one_and_update(
        {**chave, "reservados": {"$lte": capacidade - n}, "cancelada": {"$ne": True}},
        {"$inc": {"reservados": n}, "$set": {"updated_at": _now()}},
        projection={"_id": 0})


async def _libera(db, tenant_id: str, service_id: str, inicio: str, n: int):
    """Devolve `n` lugares. Nunca deixa o contador negativo.

    Negativo só aconteceria com o contador já errado (reserva editada por fora do
    módulo). Zerar com aviso no log é melhor que propagar: um contador negativo
    ofereceria mais lugares do que a saída tem.
    """
    if n <= 0:
        return
    chave = _chave(tenant_id, service_id, inicio)
    feito = await db.saidas.find_one_and_update(
        {**chave, "reservados": {"$gte": n}},
        {"$inc": {"reservados": -n}, "$set": {"updated_at": _now()}},
        projection={"_id": 0})
    if feito is None:
        logger.warning("Contador da saída %s %s menor que %d ao liberar; zerado",
                       service_id, inicio, n)
        await db.saidas.update_one(chave, {"$set": {"reservados": 0, "updated_at": _now()}})


async def _reservados(db, tenant_id: str, service_id: str, inicio: str) -> int:
    c = await db.saidas.find_one(_chave(tenant_id, service_id, inicio), {"_id": 0, "reservados": 1})
    return int((c or {}).get("reservados") or 0)


async def _recusa(db, tenant_id: str, service_id: str, inicio: str, capacidade: int,
                  pessoas: int) -> HTTPException:
    """O 409 certo quando `_ocupa` não coube: saída cancelada ou sem lugar."""
    c = await db.saidas.find_one(_chave(tenant_id, service_id, inicio), {"_id": 0}) or {}
    if c.get("cancelada"):
        motivo = c.get("motivo_cancelamento")
        return HTTPException(status_code=409,
                             detail=f"Saída cancelada: {motivo}" if motivo else "Saída cancelada")
    return _sem_vaga(capacidade, int(c.get("reservados") or 0), pessoas)


def _sem_vaga(capacidade: int, reservados: int, pessoas: int) -> HTTPException:
    restam = max(0, capacidade - reservados)
    if restam == 0:
        texto = "Essa saída está lotada"
    else:
        texto = (f"Não há vagas suficientes: restam {restam} lugar(es) nesta saída, "
                 f"e o grupo tem {pessoas}")
    return HTTPException(status_code=409, detail=texto)


# ---------------------------------------------------------------------------
# Reserva
# ---------------------------------------------------------------------------

def _grupo(adultos, criancas) -> tuple:
    try:
        a, c = int(adultos or 0), int(criancas or 0)
    except (TypeError, ValueError):
        raise HTTPException(status_code=400, detail="Quantidade de pessoas inválida")
    if a < 0 or c < 0:
        raise HTTPException(status_code=400, detail="Quantidade de pessoas inválida")
    if a + c < 1:
        raise HTTPException(status_code=400, detail="A reserva precisa de pelo menos uma pessoa")
    if a + c > MAX_PESSOAS:
        raise HTTPException(status_code=400,
                            detail=f"No máximo {MAX_PESSOAS} pessoas numa reserva")
    return a, c


def precos(passeio: dict, adultos: int, criancas: int) -> dict:
    """Preço unitário, de criança e o total. Sem preço de adulto, sem total."""
    unit = passeio.get("preco")
    cri = passeio.get("preco_crianca")
    if cri is None:
        cri = unit
    total = None
    if unit is not None:
        total = round(adultos * float(unit) + criancas * float(cri or 0), 2)
    return {"preco_unitario": unit, "preco_crianca": cri, "preco": total}


def rotulo_pessoas(adultos: int, criancas: int) -> str:
    """"4 pessoas", ou "2 adultos e 1 criança" quando há criança."""
    if not criancas:
        n = adultos
        return f"{n} pessoa" if n == 1 else f"{n} pessoas"
    a = f"{adultos} adulto" + ("" if adultos == 1 else "s")
    c = f"{criancas} criança" + ("" if criancas == 1 else "s")
    return f"{a} e {c}" if adultos else c


# ---------------------------------------------------------------------------
# Embarque
# ---------------------------------------------------------------------------
#
# Ponto de embarque é da agência (`scheduling_settings.pontos_embarque`), não do
# passeio: a Pousada do Rio é o mesmo lugar para a Lagoa Azul e para o
# Circuito, e cadastrar o hotel em cada passeio seria a mesma lista repetida
# até divergir. A antecedência (`minutos_antes`) é do ponto, porque é a
# distância dele até a saída que decide a hora da busca.
#
# A reserva guarda **nome, endereço e hora**, não só o id. O ponto pode ser
# renomeado ou apagado depois, e a lista de embarque de amanhã não pode passar
# a dizer "(removido)" no lugar do hotel em que o grupo está esperando.

MAX_PONTOS = 60
MINUTOS_ANTES_PADRAO = 30


def pontos_ativos(config: dict) -> list:
    return [p for p in (config.get("pontos_embarque") or []) if p.get("ativo", True)]


def validar_pontos(pontos) -> list:
    """A lista de pontos como vai para o banco: id estável, nome único, ordem mantida."""
    saida, nomes = [], set()
    for p in pontos or []:
        nome = (p.get("nome") or "").strip()
        if not nome:
            raise HTTPException(status_code=400, detail="Dê um nome a cada ponto de embarque")
        chave = nome.casefold()
        if chave in nomes:
            raise HTTPException(status_code=400,
                                detail=f"O ponto de embarque \"{nome}\" aparece duas vezes")
        nomes.add(chave)
        minutos = p.get("minutos_antes")
        minutos = MINUTOS_ANTES_PADRAO if minutos in (None, "") else int(minutos)
        if not 0 <= minutos <= 240:
            raise HTTPException(status_code=400,
                                detail=f"{nome}: a busca vai de 0 a 240 minutos antes da saída")
        saida.append({"id": p.get("id") or str(uuid.uuid4()), "nome": nome,
                      "endereco": (p.get("endereco") or "").strip() or None,
                      "minutos_antes": minutos, "ativo": bool(p.get("ativo", True))})
    if len(saida) > MAX_PONTOS:
        raise HTTPException(status_code=400,
                            detail=f"No máximo {MAX_PONTOS} pontos de embarque")
    return saida


def hora_local(iso: str | None, config: dict) -> str | None:
    if not iso:
        return None
    d = agenda.local(iso, agenda.fuso_de(config))
    return f"{d.hour:02d}:{d.minute:02d}"


def embarque(config: dict, ini: datetime, *, ponto_id: str = None, ponto_nome: str = None,
             horario_busca: str = None, obs: str = None) -> dict:
    """Os campos de embarque de uma reserva nesta saída.

    Ponto cadastrado sem hora informada: a hora sai de `minutos_antes`. Lugar
    fora da lista (o hotel que o turista disse) sem hora: fica "a combinar",
    porque inventar uma hora de busca para um endereço que a agência nunca viu
    é mandar o guia para a porta errada no horário errado.

    `horario_busca` é hora de parede (HH:MM) no dia da saída. Busca depois da
    saída é erro de digitação, e é recusada em vez de gravada.
    """
    tz = agenda.fuso_de(config)
    ponto = None
    if ponto_id:
        ponto = next((p for p in config.get("pontos_embarque") or [] if p.get("id") == ponto_id),
                     None)
        if not ponto:
            raise HTTPException(status_code=400, detail="Ponto de embarque inválido")
    nome = (ponto or {}).get("nome") or (ponto_nome or "").strip() or None

    busca = None
    if horario_busca:
        h = agenda._hhmm(horario_busca)
        busca_dt = agenda._combina(ini.astimezone(tz).date(), h, tz)
        if busca_dt > ini:
            raise HTTPException(
                status_code=400,
                detail=f"A busca ({horario_busca}) não pode ser depois da saída "
                       f"({hora_local(agenda._iso(ini), config)})")
        busca = agenda._iso(busca_dt)
    elif ponto:
        busca = agenda._iso(ini - timedelta(minutes=int(ponto.get("minutos_antes") or 0)))

    return {"ponto_embarque_id": (ponto or {}).get("id"),
            "ponto_embarque_nome": nome,
            "ponto_embarque_endereco": (ponto or {}).get("endereco"),
            "horario_busca": busca,
            "embarque_obs": (obs or "").strip() or None}


def rotulo_busca(reserva: dict, config: dict) -> str:
    """"07:30 – Pousada do Rio", ou "a combinar com a nossa equipe".

    Sem preposição: "na Pousada" e "no Hotel" pedem gêneros diferentes, e o
    nome é digitado pela agência. Errar o artigo na mensagem do turista é pior
    que não ter artigo.
    """
    nome = reserva.get("ponto_embarque_nome")
    hora = hora_local(reserva.get("horario_busca"), config)
    if nome and hora:
        return f"{hora} – {nome}"
    if nome:
        return f"{nome} (horário a combinar com a nossa equipe)"
    return "a combinar com a nossa equipe"


async def alterar_embarque(db, tenant_id: str, appointment_id: str, *, ponto_id: str = None,
                           ponto_nome: str = None, horario_busca: str = None,
                           obs: str = None) -> dict:
    doc = await _carregar(db, tenant_id, appointment_id)
    config = await agenda.configuracao(db, tenant_id)
    campos = embarque(config, agenda._instante(doc["inicio"]), ponto_id=ponto_id,
                      ponto_nome=ponto_nome, horario_busca=horario_busca, obs=obs)
    await db.appointments.update_one({"id": appointment_id, "tenant_id": tenant_id},
                                     {"$set": {**campos, "updated_at": _now()}})
    return {**doc, **campos}


def prazo_horas(config: dict) -> float:
    v = config.get("prazo_solicitacao_horas")
    return float(PRAZO_SOLICITACAO_PADRAO if v is None else v)


async def reservar(db, tenant_id: str, *, passeio: dict, contato: dict, inicio: str,
                   adultos: int, criancas: int = 0, config: dict,
                   origem: str = "manual", status: str = None,
                   criado_por: str = None, session_id: str = None,
                   connection_id: str = None, observacoes: str = None,
                   forcar: bool = False, agora: datetime = None,
                   ponto_embarque_id: str = None, ponto_embarque_nome: str = None,
                   horario_busca: str = None, embarque_obs: str = None) -> dict:
    """Reserva lugares numa saída. Levanta 409 se o grupo não couber.

    `status` padrão: Solicitada para bot e IA (quem confirma é a agência) e
    Confirmada para quem reserva pela tela (a própria agência).

    `forcar` é o encaixe da tela: aceita uma hora fora da grade (saída extra,
    com a capacidade padrão do passeio) e dentro da antecedência mínima.
    **Não** passa da capacidade: lugar a mais na van nunca é intenção, é engano;
    quem tem lugar a mais muda a capacidade.
    """
    if origem not in agenda.ORIGENS:
        raise HTTPException(status_code=400, detail=f"Origem desconhecida: {origem}")
    if not passeio.get("ativo", True):
        raise HTTPException(status_code=400,
                            detail=f"O passeio {passeio.get('nome')} está desativado")
    if status is None:
        status = agenda.STATUS_CONFIRMADO if origem == "manual" else agenda.STATUS_AGENDADO
    if status not in (agenda.STATUS_AGENDADO, agenda.STATUS_CONFIRMADO):
        raise HTTPException(status_code=400,
                            detail="Uma reserva nasce Solicitada ou Confirmada")
    if origem != "manual" and status != agenda.STATUS_AGENDADO:
        # Bot e IA nunca confirmam: é a regra do produto, e é aqui que ela
        # não depende de ninguém lembrar dela no prompt.
        raise HTTPException(status_code=400, detail="Só a agência confirma uma reserva")

    a, c = _grupo(adultos, criancas)
    pessoas = a + c
    agora = agora or agenda._agora()
    ini = agenda._instante(inicio)
    tz = agenda.fuso_de(config)

    capacidade = capacidade_em(passeio, ini, config)
    if capacidade is None:
        if not forcar:
            raise HTTPException(
                status_code=409,
                detail=f"{passeio.get('nome')} não tem saída em "
                       f"{agenda.rotulo_completo(agenda._iso(ini), tz)}")
        capacidade = int(passeio.get("capacidade") or 0)
    if not forcar:
        piso, teto = _limites(config, agora)
        if ini < piso:
            raise HTTPException(
                status_code=400,
                detail=f"É preciso reservar com pelo menos "
                       f"{config.get('antecedencia_min_horas')}h de antecedência")
        if ini > teto:
            raise HTTPException(
                status_code=400,
                detail=f"As reservas só vão até {config.get('antecedencia_max_dias')} "
                       "dias à frente")

    # Embarque validado antes de ocupar lugar: um ponto inválido depois do
    # `$inc` obrigaria a devolver os lugares, e a ordem certa evita o desfazer.
    campos_embarque = embarque(config, ini, ponto_id=ponto_embarque_id,
                               ponto_nome=ponto_embarque_nome, horario_busca=horario_busca,
                               obs=embarque_obs)

    inicio_iso = agenda._iso(ini)
    contador = await _ocupa(db, tenant_id, passeio["id"], inicio_iso, pessoas, capacidade)
    if contador is None:
        # A saída parece cheia. Se parte dela está presa por solicitação vencida
        # que o worker ainda não varreu, é agora que isso importa.
        if await expirar_solicitacoes(db, agora, tenant_id=tenant_id,
                                      service_id=passeio["id"], inicio=inicio_iso):
            contador = await _ocupa(db, tenant_id, passeio["id"], inicio_iso, pessoas, capacidade)
    if contador is None:
        raise await _recusa(db, tenant_id, passeio["id"], inicio_iso, capacidade, pessoas)

    expira = None
    if status == agenda.STATUS_AGENDADO and prazo_horas(config) > 0:
        # Nunca depois da própria saída: solicitação que vence depois do passeio
        # seguraria o lugar de um passeio que já aconteceu.
        expira = agenda._iso(min(agora + timedelta(hours=prazo_horas(config)), ini))

    duracao = int(passeio.get("duracao_min") or 0)
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "service_id": passeio["id"],
        "service_nome": passeio.get("nome"),
        "saida_id": contador.get("id"),
        "resource_id": None,
        "resource_nome": None,
        "contact_id": contato["id"],
        "contact_nome": contato.get("nome"),
        "contact_numero": contato.get("numero_whatsapp"),
        "inicio": inicio_iso,
        "fim": agenda._iso(ini + timedelta(minutes=duracao)),
        "duracao_min": duracao,
        "adultos": a,
        "criancas": c,
        "pessoas": pessoas,
        **precos(passeio, a, c),
        **campos_embarque,
        "status": status,
        "origem": origem,
        "observacoes": (observacoes or "").strip() or None,
        "session_id": session_id,
        "whatsapp_connection_id": connection_id,
        "criado_por": criado_por,
        "expira_em": expira,
        "confirmado_em": _now() if status == agenda.STATUS_CONFIRMADO else None,
        "presenca_confirmada_em": None,
        "cancelado_em": None,
        "cancelado_por": None,
        "motivo_cancelamento": None,
        "lembrete_enviado_em": None,
        "created_at": _now(),
        "updated_at": _now(),
    }
    try:
        await db.appointments.insert_one(dict(doc))
    except Exception:
        # Os lugares já foram tirados do contador; sem a reserva gravada, eles
        # ficariam presos para sempre numa saída que ninguém reservou.
        await _libera(db, tenant_id, passeio["id"], inicio_iso, pessoas)
        raise
    doc.pop("_id", None)

    # A saída pode ter sido cancelada entre o `$inc` e a gravação acima. O
    # `cancelar_saida` marca a saída **antes** de ler as reservas; então esta
    # reserva ou já existia quando ele leu (e o laço dele a cancela), ou foi
    # gravada depois da marcação, e é aqui que ela enxerga isso e se desfaz.
    # Sem esta conferência, uma reserva do bot gravada nesse meio ficaria viva
    # dentro de uma saída cancelada por chuva. A fumaça contra o Mongo real
    # (`tests/fumaca_saidas.py`) dispara exatamente essa corrida.
    estado = await db.saidas.find_one(_chave(tenant_id, passeio["id"], inicio_iso),
                                      {"_id": 0, "cancelada": 1, "motivo_cancelamento": 1})
    if estado and estado.get("cancelada"):
        motivo = estado.get("motivo_cancelamento")
        try:
            await _transicao(db, tenant_id, doc, agenda.STATUS_CANCELADO, {
                "cancelado_em": _now(), "cancelado_por": "sistema", "expira_em": None,
                "motivo_cancelamento": motivo, "cancelada_pela_saida": True})
        except HTTPException:
            pass  # o laço do cancelamento chegou nela primeiro
        raise HTTPException(status_code=409,
                            detail=f"Saída cancelada: {motivo}" if motivo else "Saída cancelada")
    return doc


async def _transicao(db, tenant_id: str, doc: dict, novo: str, campos: dict) -> dict:
    """Muda o status e ajusta o contador na mesma decisão. O único lugar que faz isso.

    O update é condicionado ao status lido (`status: antigo` no filtro): dois
    cliques em Cancelar, ou o worker expirando a solicitação no mesmo instante em
    que a agência a cancela, não podem devolver os lugares duas vezes. Quem
    perde a corrida recebe o documento como ficou.

    Voltar a ocupar (reativar uma cancelada, desfazer um "não compareceu") passa
    pela capacidade como uma reserva nova: os lugares podem ter sido vendidos
    no intervalo.
    """
    antigo = doc["status"]
    pessoas = int(doc.get("pessoas") or 0)
    ocupava, ocupara = antigo in agenda.OCUPAM, novo in agenda.OCUPAM

    if ocupara and not ocupava:
        p = await db.services.find_one({"id": doc["service_id"], "tenant_id": tenant_id},
                                        {"_id": 0}) or {}
        config = await agenda.configuracao(db, tenant_id)
        cap = capacidade_em(p, agenda._instante(doc["inicio"]), config)
        if cap is None:
            cap = int(p.get("capacidade") or 0)
        if await _ocupa(db, tenant_id, doc["service_id"], doc["inicio"], pessoas, cap) is None:
            raise await _recusa(db, tenant_id, doc["service_id"], doc["inicio"], cap, pessoas)

    res = await db.appointments.update_one(
        {"id": doc["id"], "tenant_id": tenant_id, "status": antigo},
        {"$set": {"status": novo, **campos, "updated_at": _now()}})
    if not res.matched_count:
        if ocupara and not ocupava:
            await _libera(db, tenant_id, doc["service_id"], doc["inicio"], pessoas)
        atual = await db.appointments.find_one({"id": doc["id"], "tenant_id": tenant_id},
                                               {"_id": 0})
        if atual and atual["status"] == novo:
            return atual
        raise HTTPException(status_code=409,
                            detail="A reserva mudou enquanto isso. Atualize a tela e tente de novo.")

    if ocupava and not ocupara:
        await _libera(db, tenant_id, doc["service_id"], doc["inicio"], pessoas)
    return {**doc, "status": novo, **campos}


async def _carregar(db, tenant_id: str, appointment_id: str) -> dict:
    doc = await db.appointments.find_one({"id": appointment_id, "tenant_id": tenant_id},
                                         {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Reserva não encontrada")
    return doc


async def cancelar(db, tenant_id: str, appointment_id: str, *, por: str = None,
                   motivo: str = None) -> dict:
    """Cancela e devolve os lugares. O registro fica: é dele que sai o histórico."""
    doc = await _carregar(db, tenant_id, appointment_id)
    if doc["status"] == agenda.STATUS_CANCELADO:
        return doc
    if doc["status"] in (agenda.STATUS_CONCLUIDO, agenda.STATUS_FALTOU):
        raise HTTPException(status_code=409,
                            detail="Esse passeio já aconteceu e a reserva não pode ser cancelada")
    return await _transicao(db, tenant_id, doc, agenda.STATUS_CANCELADO, {
        "cancelado_em": _now(), "cancelado_por": por, "expira_em": None,
        "motivo_cancelamento": (motivo or "").strip() or None})


async def mudar_status(db, tenant_id: str, appointment_id: str, status: str) -> dict:
    if status not in agenda.STATUS:
        raise HTTPException(status_code=400, detail=f"Status desconhecido: {status}")
    if status == agenda.STATUS_CANCELADO:
        return await cancelar(db, tenant_id, appointment_id)
    doc = await _carregar(db, tenant_id, appointment_id)
    if doc["status"] == status:
        return doc
    campos = {}
    if status == agenda.STATUS_CONFIRMADO:
        # Confirmada pela agência: o prazo de solicitação deixa de valer.
        campos["expira_em"] = None
        if not doc.get("confirmado_em"):
            campos["confirmado_em"] = _now()
    if status == agenda.STATUS_AGENDADO:
        raise HTTPException(status_code=400,
                            detail="Uma reserva não volta a ser solicitação")
    return await _transicao(db, tenant_id, doc, status, campos)


async def alterar_pessoas(db, tenant_id: str, appointment_id: str, *, adultos: int,
                          criancas: int) -> dict:
    """Muda o tamanho do grupo. Aumentar passa pela capacidade; diminuir devolve lugar."""
    doc = await _carregar(db, tenant_id, appointment_id)
    a, c = _grupo(adultos, criancas)
    novo = a + c
    antigo = int(doc.get("pessoas") or 0)
    ocupa = doc["status"] in agenda.OCUPAM
    p = await db.services.find_one({"id": doc["service_id"], "tenant_id": tenant_id},
                                   {"_id": 0}) or {}

    if ocupa and novo > antigo:
        config = await agenda.configuracao(db, tenant_id)
        cap = capacidade_em(p, agenda._instante(doc["inicio"]), config)
        if cap is None:
            cap = int(p.get("capacidade") or 0)
        if await _ocupa(db, tenant_id, doc["service_id"], doc["inicio"], novo - antigo, cap) is None:
            reservados = await _reservados(db, tenant_id, doc["service_id"], doc["inicio"])
            raise HTTPException(
                status_code=409,
                detail=f"Não cabe: restam {max(0, cap - reservados)} lugar(es) nesta saída "
                       f"além dos {antigo} já reservados")

    # O preço é recalculado pelo valor gravado na reserva, não pelo do passeio
    # hoje: o turista reservou a um preço, e mudar o tamanho do grupo não
    # reajusta o que já foi combinado.
    base = {"preco": doc.get("preco_unitario"), "preco_crianca": doc.get("preco_crianca")}
    campos = {"adultos": a, "criancas": c, "pessoas": novo, **precos(base, a, c),
              "updated_at": _now()}
    res = await db.appointments.update_one(
        {"id": appointment_id, "tenant_id": tenant_id, "status": doc["status"], "pessoas": doc.get("pessoas")},
        {"$set": campos})
    if not res.matched_count:
        if ocupa and novo > antigo:
            await _libera(db, tenant_id, doc["service_id"], doc["inicio"], novo - antigo)
        raise HTTPException(status_code=409,
                            detail="A reserva mudou enquanto isso. Atualize a tela e tente de novo.")
    if ocupa and novo < antigo:
        await _libera(db, tenant_id, doc["service_id"], doc["inicio"], antigo - novo)
    return {**doc, **campos}


async def expirar_solicitacoes(db, agora: datetime = None, *, tenant_id: str = None,
                               service_id: str = None, inicio: str = None) -> int:
    """Cancela solicitações cujo prazo venceu, devolvendo os lugares.

    Sem filtro, é a varredura do worker (todas as empresas). Com empresa,
    passeio e instante, é a da hora de reservar, só daquela saída.
    """
    agora = agora or agenda._agora()
    filtro = {"status": agenda.STATUS_AGENDADO,
              "expira_em": {"$ne": None, "$lte": agenda._iso(agora)}}
    if tenant_id:
        filtro["tenant_id"] = tenant_id
    if service_id:
        filtro["service_id"] = service_id
    if inicio:
        filtro["inicio"] = inicio
    vencidas = await db.appointments.find(filtro, {"_id": 0}) \
        .sort([("expira_em", 1), ("_id", 1)]).to_list(MAX_EXPIRADAS_POR_PASSADA)
    n = 0
    for v in vencidas:
        try:
            await _transicao(db, v["tenant_id"], v, agenda.STATUS_CANCELADO, {
                "cancelado_em": _now(), "cancelado_por": "sistema", "expira_em": None,
                "motivo_cancelamento": "Solicitação não confirmada no prazo"})
            n += 1
        except HTTPException:
            # Alguém mexeu nela no mesmo instante (confirmou ou cancelou): o
            # estado que ficou é o dessa pessoa.
            continue
    if n:
        logger.info("Solicitações expiradas: %d", n)
    return n


async def reservas_do_contato(db, tenant_id: str, contact_id: str, *,
                              futuras: bool = True, limite: int = 20) -> list:
    filtro = {"tenant_id": tenant_id, "contact_id": contact_id}
    if futuras:
        filtro["inicio"] = {"$gte": agenda._iso(agenda._agora())}
        filtro["status"] = {"$in": [agenda.STATUS_AGENDADO, agenda.STATUS_CONFIRMADO]}
    return await db.appointments.find(filtro, {"_id": 0}) \
        .sort([("inicio", 1 if futuras else -1), ("_id", 1)]).to_list(limite)


# ---------------------------------------------------------------------------
# A saída como operação: equipe, cancelamento em lote, lista de embarque
# ---------------------------------------------------------------------------

async def _estado_da_saida(db, tenant_id: str, service_id: str, inicio: str, campos: dict):
    """Grava estado no documento da saída, criando-o se ainda não há reserva.

    Mesmo cuidado do `_ocupa`: duas pessoas mexendo na mesma saída nova ao mesmo
    tempo batem no índice único, e quem perde só precisa repetir sem upsert.
    """
    chave = _chave(tenant_id, service_id, inicio)
    update = {"$set": {**campos, "updated_at": _now()},
              "$setOnInsert": {"id": str(uuid.uuid4()), "reservados": 0, "created_at": _now()}}
    try:
        await db.saidas.update_one(chave, update, upsert=True)
    except DuplicateKeyError:
        await db.saidas.update_one(chave, {"$set": update["$set"]})


async def definir_equipe(db, tenant_id: str, service_id: str, inicio: str, *,
                         guia: dict = None, veiculo: dict = None, obs: str = None):
    """Guia e veículo **desta** saída. Escala é por saída, não por passeio: o
    guia da Lagoa Azul de segunda não é o de sábado."""
    await _estado_da_saida(db, tenant_id, service_id, inicio, {
        "guia_id": (guia or {}).get("id"), "guia_nome": (guia or {}).get("nome"),
        "veiculo_id": (veiculo or {}).get("id"), "veiculo_nome": (veiculo or {}).get("nome"),
        "equipe_obs": (obs or "").strip() or None,
    })


async def cancelar_saida(db, tenant_id: str, service_id: str, inicio: str, *,
                         motivo: str, por: str = None) -> list:
    """Cancela a saída inteira (chuva, maré, lagoa seca) e todas as reservas dela.

    A ordem é a garantia: **primeiro** a saída é marcada cancelada, e a partir
    daí o `$inc` de `_ocupa` já recusa qualquer reserva nova (o filtro tem
    `cancelada != True`); **depois** as reservas existentes são lidas e
    canceladas. Na ordem inversa, uma reserva feita pelo bot entre a leitura e a
    marcação ficaria viva dentro de uma saída cancelada, e o turista apareceria
    no píer sozinho.

    Realizada não é tocada: o que já aconteceu não se cancela. Devolve as
    reservas canceladas, para quem chama avisar cada grupo.
    """
    motivo = (motivo or "").strip()
    if not motivo:
        raise HTTPException(status_code=400, detail="Diga o motivo do cancelamento da saída")
    await _estado_da_saida(db, tenant_id, service_id, inicio, {
        "cancelada": True, "motivo_cancelamento": motivo,
        "cancelada_em": _now(), "cancelada_por": por})

    alvo = await db.appointments.find(
        {"tenant_id": tenant_id, "service_id": service_id, "inicio": inicio,
         "status": {"$in": [agenda.STATUS_AGENDADO, agenda.STATUS_CONFIRMADO]}},
        {"_id": 0},
    ).sort([("created_at", 1), ("_id", 1)]).to_list(MAX_PESSOAS * 10)
    canceladas = []
    for r in alvo:
        try:
            canceladas.append(await _transicao(db, tenant_id, r, agenda.STATUS_CANCELADO, {
                "cancelado_em": _now(), "cancelado_por": por, "expira_em": None,
                "motivo_cancelamento": motivo, "cancelada_pela_saida": True}))
        except HTTPException:
            # Alguém mexeu nela no mesmo instante; o estado que ficou é o dela.
            continue
    return canceladas


async def reabrir_saida(db, tenant_id: str, service_id: str, inicio: str):
    """A saída volta a aceitar reserva. As reservas canceladas **não** voltam:
    os turistas já foram avisados do cancelamento, e reativá-las em silêncio
    faria a agência esperar grupos que remarcaram para outro dia."""
    await db.saidas.update_one(_chave(tenant_id, service_id, inicio), {"$set": {
        "cancelada": False, "motivo_cancelamento": None, "reaberta_em": _now(),
        "updated_at": _now()}})


def telefone(numero: str | None) -> str:
    """5598981112233 → (98) 98111-2233. O que não reconhece volta como veio."""
    d = "".join(c for c in (numero or "") if c.isdigit())
    if d.startswith("55") and len(d) in (12, 13):
        d = d[2:]
    if len(d) == 11:
        return f"({d[:2]}) {d[2:7]}-{d[7:]}"
    if len(d) == 10:
        return f"({d[:2]}) {d[2:6]}-{d[6:]}"
    return numero or ""


async def lista_embarque(db, tenant_id: str, *, passeio: dict, linha: dict, config: dict) -> dict:
    """O que o guia leva para o embarque: quem buscar, onde, a que horas.

    Só quem ocupa lugar (Solicitada, Confirmada, Realizada). A Solicitada entra
    marcada: o lugar está reservado e o grupo pode aparecer, e esconder seria
    pior que avisar. Ordem pela hora da busca, com "a combinar" por último,
    porque é a ordem em que a van passa.
    """
    tz = agenda.fuso_de(config)
    grupos = await db.appointments.find(
        {"tenant_id": tenant_id, "service_id": passeio["id"], "inicio": linha["inicio"],
         "status": {"$in": list(agenda.OCUPAM)}},
        {"_id": 0},
    ).to_list(MAX_PESSOAS * 10)

    def ordem(g):
        return (g.get("horario_busca") or "~", (g.get("ponto_embarque_nome") or "~").casefold(),
                (g.get("contact_nome") or "").casefold())

    grupos.sort(key=ordem)
    itens, por_ponto = [], {}
    tot = {"grupos": 0, "pessoas": 0, "adultos": 0, "criancas": 0, "sem_ponto": 0,
           "solicitadas": 0}
    for g in grupos:
        hora = hora_local(g.get("horario_busca"), config)
        item = {k: g.get(k) for k in (
            "id", "contact_id", "contact_nome", "contact_numero", "adultos", "criancas",
            "pessoas", "status", "ponto_embarque_nome", "ponto_embarque_endereco",
            "horario_busca", "embarque_obs", "observacoes", "preco", "presenca_confirmada_em")}
        item["hora_busca"] = hora
        item["telefone"] = telefone(g.get("contact_numero"))
        itens.append(item)
        chave = (hora, g.get("ponto_embarque_nome"))
        pp = por_ponto.setdefault(chave, {"ponto": g.get("ponto_embarque_nome"),
                                          "endereco": g.get("ponto_embarque_endereco"),
                                          "hora_busca": hora, "grupos": 0, "pessoas": 0})
        pp["grupos"] += 1
        pp["pessoas"] += int(g.get("pessoas") or 0)
        tot["grupos"] += 1
        tot["pessoas"] += int(g.get("pessoas") or 0)
        tot["adultos"] += int(g.get("adultos") or 0)
        tot["criancas"] += int(g.get("criancas") or 0)
        tot["sem_ponto"] += 0 if g.get("ponto_embarque_nome") else 1
        tot["solicitadas"] += 1 if g.get("status") == agenda.STATUS_AGENDADO else 0

    return {"fuso": config.get("fuso"), "saida": linha,
            "passeio": {"nome": passeio.get("nome"), "duracao_min": passeio.get("duracao_min")},
            "grupos": itens, "por_ponto": list(por_ponto.values()), "totais": tot,
            "texto": _texto_embarque(passeio, linha, itens, tot, tz)}


def _texto_embarque(passeio, linha, itens, tot, tz) -> str:
    """A lista em texto, pronta para colar no WhatsApp do guia."""
    d = agenda.local(linha["inicio"], tz)
    cab = [f"*{passeio.get('nome')}* · {agenda.DIAS[d.weekday()]} "
           f"{d.day:02d}/{d.month:02d} · {linha['hora']}"]
    equipe = [x for x in (f"Guia: {linha['guia_nome']}" if linha.get("guia_nome") else "",
                          f"Veículo: {linha['veiculo_nome']}" if linha.get("veiculo_nome") else "")
              if x]
    if equipe:
        cab.append(" · ".join(equipe))
    if linha.get("equipe_obs"):
        cab.append(linha["equipe_obs"])
    resumo = f"{tot['pessoas']} pessoa(s) em {tot['grupos']} grupo(s)"
    if tot["criancas"]:
        resumo += f" ({rotulo_pessoas(tot['adultos'], tot['criancas'])})"
    cab.append(resumo)

    linhas = ["\n".join(cab)]
    atual = object()
    for g in itens:
        chave = (g["hora_busca"], g["ponto_embarque_nome"])
        if chave != atual:
            atual = chave
            titulo = " – ".join(x for x in (g["hora_busca"], g["ponto_embarque_nome"]) if x)
            linhas.append(f"\n*{titulo or 'Busca a combinar'}*")
        partes = [f"• {g.get('contact_nome') or 'Sem nome'} "
                  f"({rotulo_pessoas(int(g.get('adultos') or 0), int(g.get('criancas') or 0))})"]
        if g["telefone"]:
            partes.append(g["telefone"])
        if g.get("embarque_obs"):
            partes.append(g["embarque_obs"])
        if g.get("status") == agenda.STATUS_AGENDADO:
            partes.append("⚠️ não confirmada")
        linhas.append(" – ".join(partes))
    if not itens:
        linhas.append("\nNenhum grupo nesta saída.")
    return "\n".join(linhas)
