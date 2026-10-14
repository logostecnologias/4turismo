"""Base da agenda: fuso, configuração da agência, feriados, rótulos e status.

Nasceu como uma agenda de horário (um profissional, conflito por
sobreposição de intervalo). No turismo a reserva é por **saída e capacidade**, e
isso mora em `saidas.py`: lá estão a grade de saídas do passeio, as vagas e a
reserva com contador atômico. Daqui saiu o cálculo de horário livre e a
marcação por intervalo, de propósito: deixar os dois caminhos de gravação
abertos seria deixar uma reserva nascer sem passar pelo contador.

Fica o que os dois modelos compartilham:

  - **O fuso é da empresa e fica gravado** (`scheduling_settings.fuso`). Quem
    mais lê a agenda é o bot, que roda no servidor sem navegador nenhum. A grade
    é hora de parede; a reserva é instante em UTC, no formato ISO que o resto do
    sistema grava e compara como string.

  - **Os status**, que são os mesmos nos dois modelos (só o rótulo da tela
    mudou: `agendado` é Solicitada, `concluido` é Realizada, `faltou` é Não
    compareceu).

  - **Cancelar não apaga.** O lugar volta porque o contador é devolvido, e o
    registro fica: é dele que sai a taxa de falta, o motivo e o histórico.

  - A grade de **guias** (`resources`), que a fase 1 não usa para reservar mas
    cujo cadastro continua existindo.
"""
import logging
import uuid
from datetime import date, datetime, time, timedelta, timezone
from zoneinfo import ZoneInfo, ZoneInfoNotFoundError

from fastapi import HTTPException

logger = logging.getLogger("turismo.agenda")

# ---------------------------------------------------------------------------
# Constantes
# ---------------------------------------------------------------------------

FUSO_PADRAO = "America/Sao_Paulo"

# Estados de uma reserva. `agendado` (Solicitada) é o que bot e IA criam e a
# agência ainda vai confirmar; `confirmado` (Confirmada) é o que a agência
# confirmou. Os dois, mais `concluido` (Realizada), ocupam lugar na saída;
# `cancelado` e `faltou` (Não compareceu) devolvem o lugar.
STATUS_AGENDADO = "agendado"
STATUS_CONFIRMADO = "confirmado"
STATUS_CONCLUIDO = "concluido"
STATUS_CANCELADO = "cancelado"
STATUS_FALTOU = "faltou"
STATUS = (STATUS_AGENDADO, STATUS_CONFIRMADO, STATUS_CONCLUIDO,
          STATUS_CANCELADO, STATUS_FALTOU)
OCUPAM = (STATUS_AGENDADO, STATUS_CONFIRMADO, STATUS_CONCLUIDO)

ORIGENS = ("manual", "bot", "ia")

TIPOS_RECURSO = ("pessoa", "sala", "equipamento")

# Dia da semana no formato de `date.weekday()`: 0 = segunda.
DIAS = ("Segunda", "Terça", "Quarta", "Quinta", "Sexta", "Sábado", "Domingo")

# Padrões de uma empresa que acabou de ativar o módulo. Existem para a primeira
# tela já vir preenchida com algo plausível: configuração em branco é
# configuração que ninguém termina.
PADROES = {
    "fuso": FUSO_PADRAO,
    # De quanto em quanto tempo um horário pode começar. 30 min é o passo de
    # quase todo comércio de serviço; 15 atende quem trabalha com encaixe.
    "passo_min": 30,
    # Folga entre um atendimento e o seguinte (limpeza, sala, deslocamento).
    "intervalo_min": 0,
    # O contato não marca para daqui a cinco minutos: ninguém chega a tempo.
    "antecedencia_min_horas": 2,
    # Nem para daqui a dois anos: a agenda ficaria cheia de fantasmas.
    "antecedencia_max_dias": 60,
    "cancelamento_min_horas": 2,
    "lembrete_ativo": True,
    "lembrete_horas": 24,
    # Por quanto tempo uma solicitação feita pelo bot ou pela IA segura os
    # lugares esperando a agência confirmar. 0 = não expira.
    "prazo_solicitacao_horas": 24,
}

# Teto de dias varridos quando alguém pede "o próximo horário livre". Sem ele,
# uma empresa sem nenhuma grade configurada faria a varredura percorrer o
# `antecedencia_max_dias` inteiro, um dia por consulta, para devolver lista vazia.
MAX_DIAS_VARREDURA = 45

# Teto de horários devolvidos numa consulta de um dia. Uma grade de 12 horas com
# passo de 15 min dá 48 horários: mandar isso para o WhatsApp é ilegível, e para
# o modelo de IA é token pago sem retorno.
MAX_HORARIOS_DIA = 40

# Grade que um recurso novo recebe: comercial, de segunda a sexta.
GRADE_PADRAO = [
    [{"inicio": "09:00", "fim": "12:00"}, {"inicio": "13:00", "fim": "18:00"}],
    [{"inicio": "09:00", "fim": "12:00"}, {"inicio": "13:00", "fim": "18:00"}],
    [{"inicio": "09:00", "fim": "12:00"}, {"inicio": "13:00", "fim": "18:00"}],
    [{"inicio": "09:00", "fim": "12:00"}, {"inicio": "13:00", "fim": "18:00"}],
    [{"inicio": "09:00", "fim": "12:00"}, {"inicio": "13:00", "fim": "18:00"}],
    [],
    [],
]


def _now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _agora() -> datetime:
    return datetime.now(timezone.utc).replace(microsecond=0)


def _iso(dt: datetime) -> str:
    """Instante no formato que o resto do sistema grava e compara como string.

    Sem microssegundos de propósito: os horários da grade são minutos redondos,
    e a comparação lexicográfica entre `...:00+00:00` e `...:00.123456+00:00`
    funciona mas é uma armadilha desnecessária num campo que é ordenado no banco.
    """
    return dt.astimezone(timezone.utc).replace(microsecond=0).isoformat()


# ---------------------------------------------------------------------------
# Fuso e conversão
# ---------------------------------------------------------------------------

def fuso_de(config: dict | None) -> ZoneInfo:
    nome = (config or {}).get("fuso") or FUSO_PADRAO
    try:
        return ZoneInfo(nome)
    except (ZoneInfoNotFoundError, ValueError):
        # Fuso inválido no banco não pode derrubar a agenda inteira: o efeito
        # visível seria a tela de horários vazia, sem explicação nenhuma.
        logger.warning("Fuso desconhecido em scheduling_settings: %r, usando %s",
                       nome, FUSO_PADRAO)
        return ZoneInfo(FUSO_PADRAO)


def _hhmm(valor) -> time:
    try:
        h, m = str(valor).split(":")
        return time(int(h), int(m))
    except (ValueError, AttributeError):
        raise HTTPException(status_code=400, detail=f"Horário inválido: {valor!r} (use HH:MM)")


def _dia(valor) -> date:
    try:
        return date.fromisoformat(str(valor)[:10])
    except ValueError:
        raise HTTPException(status_code=400, detail=f"Data inválida: {valor!r} (use AAAA-MM-DD)")


def local(iso_utc: str, tz: ZoneInfo) -> datetime:
    """O instante gravado, visto no fuso da empresa."""
    return datetime.fromisoformat(str(iso_utc).replace("Z", "+00:00")).astimezone(tz)


def _combina(d: date, hhmm: time, tz: ZoneInfo) -> datetime:
    """Hora de parede daquele dia, no fuso da empresa.

    Numa virada de horário de verão a hora pode não existir (ou existir duas
    vezes); o `zoneinfo` resolve escolhendo uma delas. É o comportamento certo
    aqui: recusar a grade inteira uma vez por ano seria pior que deslocar um
    horário em sessenta minutos num único dia.
    """
    return datetime.combine(d, hhmm, tzinfo=tz)


def rotulo(iso_utc: str, tz: ZoneInfo) -> str:
    """26/08 às 14:30, como o horário aparece para o contato no WhatsApp."""
    dt = local(iso_utc, tz)
    return f"{dt.day:02d}/{dt.month:02d} às {dt.hour:02d}:{dt.minute:02d}"


def rotulo_completo(iso_utc: str, tz: ZoneInfo) -> str:
    dt = local(iso_utc, tz)
    return (f"{DIAS[dt.weekday()]}, {dt.day:02d}/{dt.month:02d}/{dt.year} "
            f"às {dt.hour:02d}:{dt.minute:02d}")


# ---------------------------------------------------------------------------
# Configuração da empresa
# ---------------------------------------------------------------------------

async def configuracao(db, tenant_id: str) -> dict:
    """As preferências de agendamento da empresa, com os padrões preenchidos.

    Empresa que nunca abriu a tela não tem documento, e devolver `None` faria
    cada chamador inventar o próprio default. Aqui ela recebe os padrões sem
    que nada seja gravado.
    """
    doc = await db.scheduling_settings.find_one({"tenant_id": tenant_id}, {"_id": 0}) or {}
    saida = {"tenant_id": tenant_id, **PADROES,
             **{k: v for k, v in doc.items() if v is not None}}
    saida["bloqueios"] = doc.get("bloqueios") or []
    return saida


def bloqueado(config: dict, d: date) -> str | None:
    """Feriado ou fechamento que vale para a empresa toda. Devolve o motivo."""
    alvo = d.isoformat()
    for b in config.get("bloqueios") or []:
        if (b.get("data") or "")[:10] == alvo:
            return b.get("motivo") or "Fechado"
    return None


# ---------------------------------------------------------------------------
# Grade do recurso
# ---------------------------------------------------------------------------

def janelas_do_dia(recurso: dict, d: date, config: dict) -> list[dict]:
    """Os intervalos em que este profissional atende naquele dia.

    Ordem de precedência: bloqueio da empresa (feriado) derruba tudo; exceção do
    recurso (folga ou horário especial) ganha da grade semanal; sem nenhuma das
    duas, vale a grade.
    """
    if bloqueado(config, d):
        return []

    alvo = d.isoformat()
    for e in recurso.get("excecoes") or []:
        if (e.get("data") or "")[:10] != alvo:
            continue
        if e.get("fechado", True):
            return []
        return _normaliza_janelas(e.get("janelas") or [])

    grade = recurso.get("grade") or []
    if len(grade) != 7:
        return []
    return _normaliza_janelas(grade[d.weekday()] or [])


def _normaliza_janelas(janelas: list) -> list[dict]:
    """Descarta janela invertida ou vazia e devolve em ordem.

    Janela com fim <= início é erro de digitação (`18:00` às `08:00`), e deixá-la
    passar produziria um laço que nunca gera horário nenhum: sintoma idêntico
    ao de "não configurei a grade", que manda o suporte procurar no lugar errado.
    """
    saida = []
    for j in janelas:
        try:
            ini, fim = _hhmm(j.get("inicio")), _hhmm(j.get("fim"))
        except HTTPException:
            continue
        if fim > ini:
            saida.append({"inicio": ini, "fim": fim})
    return sorted(saida, key=lambda j: j["inicio"])


def validar_grade(grade) -> list:
    """A grade como ela vai para o banco: 7 dias, horários em HH:MM, sem inversão."""
    if not isinstance(grade, list) or len(grade) != 7:
        raise HTTPException(status_code=400,
                            detail="A grade precisa ter os 7 dias da semana")
    saida = []
    for i, dia in enumerate(grade):
        limpo = []
        for j in (dia or []):
            ini, fim = _hhmm(j.get("inicio")), _hhmm(j.get("fim"))
            if fim <= ini:
                raise HTTPException(
                    status_code=400,
                    detail=f"{DIAS[i]}: o fim ({j.get('fim')}) não pode vir "
                           f"antes do início ({j.get('inicio')})")
            limpo.append({"inicio": f"{ini.hour:02d}:{ini.minute:02d}",
                          "fim": f"{fim.hour:02d}:{fim.minute:02d}"})
        limpo.sort(key=lambda x: x["inicio"])
        # Janelas encavaladas no mesmo dia gerariam o mesmo horário duas vezes na
        # lista que o contato recebe.
        for a, b in zip(limpo, limpo[1:]):
            if b["inicio"] < a["fim"]:
                raise HTTPException(
                    status_code=400,
                    detail=f"{DIAS[i]}: os intervalos {a['inicio']}-{a['fim']} e "
                           f"{b['inicio']}-{b['fim']} se sobrepõem")
        saida.append(limpo)
    return saida


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------

async def servicos_ativos(db, tenant_id: str) -> list:
    return await db.services.find(
        {"tenant_id": tenant_id, "ativo": True}, {"_id": 0}
    ).sort([("ordem", 1), ("nome", 1)]).to_list(200)


# ---------------------------------------------------------------------------
# Instantes
# ---------------------------------------------------------------------------

def _instante(valor, rotulo_campo: str = "Horário") -> datetime:
    try:
        dt = datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{rotulo_campo} inválido: {valor!r}")
    if dt.tzinfo is None:
        raise HTTPException(
            status_code=400,
            detail=f"{rotulo_campo} sem fuso. Informe o instante completo "
                   "(ex.: 2026-09-01T14:00:00-03:00)")
    return dt.astimezone(timezone.utc).replace(microsecond=0)


# ---------------------------------------------------------------------------
# Mudança de estado: sempre pelo contador
# ---------------------------------------------------------------------------
#
# Ficam aqui com o nome antigo porque o worker de lembrete, a IA e as rotas os
# chamam, mas a decisão é de `saidas`: cancelar e mudar status mexem no contador
# de lugares, e uma segunda implementação aqui deixaria o contador contando
# reserva cancelada. Import tardio: `saidas` importa este módulo.

async def cancelar(db, tenant_id: str, appointment_id: str, *, por: str = None,
                   motivo: str = None) -> dict:
    import saidas
    return await saidas.cancelar(db, tenant_id, appointment_id, por=por, motivo=motivo)


async def mudar_status(db, tenant_id: str, appointment_id: str, status: str) -> dict:
    import saidas
    return await saidas.mudar_status(db, tenant_id, appointment_id, status)


async def agendamentos_do_contato(db, tenant_id: str, contact_id: str, *,
                                  futuros: bool = True, limite: int = 20) -> list:
    import saidas
    return await saidas.reservas_do_contato(db, tenant_id, contact_id,
                                            futuras=futuros, limite=limite)
