"""A tela "Hoje": o que precisa de alguém agora, na agência.

Substituiu o painel de contadores por módulo (08/10/2026). Aquele respondia
"quantos registros existem em cada tela" ("Pacientes: 0", "Fluxos ativos: 0"),
que ninguém da agência pergunta. Esta responde a manhã da recepção: a agenda do
dia, a fila do WhatsApp, as confirmações de amanhã sem resposta, as tarefas
atrasadas, os orçamentos parados e o que está quebrado. O mês vai no fim,
pequeno.

**O dia é o da agência** (`scheduling_settings.fuso`), não o do navegador: é a
regra da agenda, e é a agenda que domina esta tela. As tarefas não dependem de
corte de dia: atrasada é `vence_em < agora`, igual em qualquer fuso.

**O atendente** vê a agenda, a fila (só das conexões liberadas para ele) e as
tarefas dele. Orçamentos, alertas de configuração e o mês são do admin: são
gestão, e os orçamentos nem fazem parte das telas do atendente.

Uma chamada só para a tela inteira: o que domina é a ida e volta até o banco,
não o volume.
"""
from datetime import datetime, timedelta, timezone

import agenda
import saidas
import atividades
from auth import accessible_connection_ids

MAX_AGENDA_DIA = 200
MAX_LISTA = 8


def _agora() -> datetime:
    return datetime.now(timezone.utc)


def _dia_utc(tz, dia) -> tuple[str, str]:
    """[00:00, 24:00) daquele dia no fuso da agência, como ISO em UTC."""
    ini = datetime(dia.year, dia.month, dia.day, tzinfo=tz)
    return agenda._iso(ini), agenda._iso(ini + timedelta(days=1))


async def montar(db, user: dict, agora_dt: datetime | None = None) -> dict:
    tenant_id = user["tenant_id"]
    admin = user.get("role") == "tenant_admin"
    agora_dt = agora_dt or _agora()
    agora = agenda._iso(agora_dt)

    config = await agenda.configuracao(db, tenant_id)
    tz = agenda.fuso_de(config)
    hoje_local = agora_dt.astimezone(tz).date()
    hoje_ini, hoje_fim = _dia_utc(tz, hoje_local)

    # Agenda do dia: tudo menos cancelado, que já não ocupa ninguém.
    horarios = await db.appointments.find(
        {"tenant_id": tenant_id, "inicio": {"$gte": hoje_ini, "$lt": hoje_fim},
         "status": {"$ne": agenda.STATUS_CANCELADO}},
        {"_id": 0, "id": 1, "inicio": 1, "fim": 1, "status": 1, "contact_id": 1,
         "contact_nome": 1, "service_nome": 1, "resource_nome": 1, "origem": 1,
         "adultos": 1, "criancas": 1, "pessoas": 1, "presenca_confirmada_em": 1,
         "ponto_embarque_nome": 1, "horario_busca": 1},
    ).sort([("inicio", 1), ("_id", 1)]).to_list(MAX_AGENDA_DIA)
    por_status = {s: 0 for s in agenda.STATUS}
    for h in horarios:
        # A hora da busca já no fuso da agência: é o que a tela mostra ao lado
        # do passeio, e a tela não deveria converter o que o servidor já sabe.
        h["hora_busca"] = saidas.hora_local(h.get("horario_busca"), config)
        por_status[h["status"]] = por_status.get(h["status"], 0) + 1

    # Solicitações esperando a agência: tudo que o bot ou a IA registrou e
    # ninguém confirmou ainda, da mais próxima para a mais distante. Não só as
    # de amanhã: uma solicitação segura lugar pelo prazo configurado e vence
    # sozinha, e a de sábado que ninguém olhou hoje é a que expira amanhã.
    # A chave continua `amanha_sem_confirmacao` porque é o que a tela lê.
    sem_confirmacao = await db.appointments.find(
        {"tenant_id": tenant_id, "inicio": {"$gte": agora},
         "status": agenda.STATUS_AGENDADO},
        {"_id": 0, "id": 1, "inicio": 1, "contact_nome": 1, "service_nome": 1,
         "resource_nome": 1, "lembrete_enviado_em": 1, "adultos": 1, "criancas": 1,
         "pessoas": 1, "preco": 1, "expira_em": 1, "origem": 1},
    ).sort([("inicio", 1), ("_id", 1)]).to_list(MAX_AGENDA_DIA)

    # Fila do WhatsApp, no mesmo escopo da Central: o atendente só enxerga as
    # conexões liberadas para ele.
    escopo = {"tenant_id": tenant_id, "status_atendimento": "aguardando"}
    acesso = await accessible_connection_ids(db, user)
    if acesso is not None:
        escopo["whatsapp_connection_id"] = {"$in": acesso}
    fila_total = await db.conversation_sessions.count_documents(escopo)
    sessoes = await db.conversation_sessions.find(
        escopo, {"_id": 0, "id": 1, "contact_id": 1, "nome_contato": 1, "numero": 1,
                 "last_message": 1, "aguardando_desde": 1, "updated_at": 1},
    ).sort([("aguardando_desde", 1), ("_id", 1)]).to_list(MAX_LISTA)
    ids = [s["contact_id"] for s in sessoes if s.get("contact_id")]
    nomes = {}
    if ids:
        async for c in db.contacts.find({"tenant_id": tenant_id, "id": {"$in": ids}},
                                        {"_id": 0, "id": 1, "nome": 1}):
            nomes[c["id"]] = c.get("nome")
    fila = []
    for s in sessoes:
        lm = s.get("last_message") or {}
        fila.append({
            "id": s["id"],
            "nome": nomes.get(s.get("contact_id")) or s.get("nome_contato") or s.get("numero"),
            "previa": lm.get("content") if isinstance(lm, dict) else None,
            "desde": s.get("aguardando_desde") or s.get("updated_at"),
        })

    # Tarefas atrasadas: as da pessoa para o atendente; a agência inteira para
    # o admin, que é quem cobra.
    filtro = {"tenant_id": tenant_id, "concluida_em": None, "vence_em": {"$lt": agora}}
    if not admin:
        filtro["responsavel_id"] = user["id"]
    tarefas_total = await db.activities.count_documents(filtro)
    tarefas = await db.activities.find(
        filtro, {"_id": 0, "id": 1, "titulo": 1, "vence_em": 1},
    ).sort([("vence_em", 1), ("_id", 1)]).to_list(MAX_LISTA)

    resposta = {
        "fuso": str(tz.key),
        "dia": hoje_local.isoformat(),
        "agora": agora,
        "admin": admin,
        "agenda": {"itens": horarios, "por_status": por_status,
                   "truncada": len(horarios) >= MAX_AGENDA_DIA},
        "amanha_sem_confirmacao": {"total": len(sem_confirmacao),
                                   "itens": sem_confirmacao[:MAX_LISTA]},
        "fila": {"total": fila_total, "itens": fila},
        "tarefas_atrasadas": {"total": tarefas_total, "itens": tarefas},
    }
    if not admin:
        return resposta

    # Orçamentos parados: abertos e sem nenhuma tarefa pendente. Mesmo sinal do
    # cartão do quadro (`sem_proximo_passo`), aqui somado. Os de maior valor
    # primeiro: é por eles que vale começar.
    abertos = await db.deals.find(
        {"tenant_id": tenant_id, "status": "aberto"},
        {"_id": 0, "id": 1, "titulo": 1, "valor": 1},
    ).sort([("valor", -1), ("_id", 1)]).to_list(1000)
    sem_passo = await atividades.negocios_sem_proximo_passo(
        db, tenant_id, [d["id"] for d in abertos])
    parados = [d for d in abertos if d["id"] in sem_passo]

    # Alertas: só o que está quebrado. Tudo certo = lista vazia, e a tela não
    # mostra o bloco.
    alertas = []
    conexoes = await db.whatsapp_connections.find(
        {"tenant_id": tenant_id}, {"_id": 0, "nome": 1, "status": 1}).to_list(50)
    if not conexoes:
        alertas.append({"tipo": "whatsapp",
                        "texto": "Nenhum número de WhatsApp conectado. Sem ele, a agência "
                                 "não recebe nem envia mensagens."})
    for c in conexoes:
        if c.get("status") != "conectado":
            nome = c.get("nome") or "sem nome"
            alertas.append({"tipo": "whatsapp",
                            "texto": f"O WhatsApp {nome} está desconectado. As mensagens "
                                     "dos clientes não estão chegando."})
    async for a in db.ai_agents.find(
            {"tenant_id": tenant_id, "ativo": True, "credencial_status": "erro"},
            {"_id": 0, "nome": 1}):
        alertas.append({"tipo": "assistente",
                        "texto": f"A chave de IA do assistente {a.get('nome')} parou de "
                                 "funcionar. As conversas estão indo para a recepção."})

    # Saída cancelada (chuva) com grupo que o WhatsApp não alcançou. Os avisos
    # saem aos poucos, depois de a tela de cancelamento já ter respondido, e é
    # aqui que a agência descobre quem precisa de ligação. Some quando o
    # horário da saída passa.
    sem_aviso = await db.appointments.find(
        {"tenant_id": tenant_id, "aviso_saida": "sem_aviso",
         "inicio": {"$gte": agora}},
        {"_id": 0, "contact_nome": 1, "contact_numero": 1, "service_nome": 1, "inicio": 1},
    ).sort([("inicio", 1), ("_id", 1)]).to_list(50)
    for r in sem_aviso:
        quando = agenda.rotulo_completo(r["inicio"], tz)
        alertas.append({"tipo": "aviso_saida",
                        "texto": f"{r.get('contact_nome') or 'Um grupo'} "
                                 f"({saidas.telefone(r.get('contact_numero'))}) não recebeu "
                                 f"pelo WhatsApp o cancelamento de {r.get('service_nome') or 'passeio'} "
                                 f"em {quando}. Avise por telefone."})

    # O mês, no calendário da agência.
    mes_ini = agenda._iso(datetime(hoje_local.year, hoje_local.month, 1, tzinfo=tz))
    decididos = {"concluido": 0, "faltou": 0}
    async for row in db.appointments.aggregate([
        {"$match": {"tenant_id": tenant_id, "inicio": {"$gte": mes_ini, "$lt": agora},
                    "status": {"$in": ["concluido", "faltou"]}}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ]):
        decididos[row["_id"]] = row["n"]
    total_decidido = decididos["concluido"] + decididos["faltou"]

    aprovados = {"n": 0, "valor": 0}
    async for row in db.deals.aggregate([
        {"$match": {"tenant_id": tenant_id, "status": "ganho", "fechado_em": {"$gte": mes_ini}}},
        {"$group": {"_id": None, "n": {"$sum": 1}, "valor": {"$sum": "$valor"}}},
    ]):
        aprovados = {"n": row["n"], "valor": row.get("valor") or 0}

    # Conversas do mês que passaram pelo assistente de IA, e quantas ele
    # levou até o fim sem a recepção: é o número que mostra se ele se paga.
    # Nunca assumida por alguém e fora da fila = resolvida por ele.
    assistente = {"total": 0, "sem_recepcao": 0}
    async for s in db.conversation_sessions.find(
            {"tenant_id": tenant_id, "ia_agente_id": {"$ne": None},
             "created_at": {"$gte": mes_ini}},
            {"_id": 0, "status_atendimento": 1, "assumido_em": 1}):
        assistente["total"] += 1
        if s.get("status_atendimento") in ("bot", "finalizado") and not s.get("assumido_em"):
            assistente["sem_recepcao"] += 1

    resposta.update({
        "orcamentos_parados": {"total": len(parados),
                               "valor": sum(d.get("valor") or 0 for d in parados),
                               "itens": parados[:MAX_LISTA]},
        "alertas": alertas,
        "mes": {
            "consultas_realizadas": decididos["concluido"],
            "faltas": decididos["faltou"],
            # Sobre horários decididos, nunca sobre o total: o que ainda vai
            # acontecer não é acerto nem falta. Sem decididos, a taxa é nula.
            "taxa_falta": (decididos["faltou"] / total_decidido) if total_decidido else None,
            "orcamentos_aprovados": aprovados,
            "pacientes_novos": await db.contacts.count_documents(
                {"tenant_id": tenant_id, "created_at": {"$gte": mes_ini}}),
            "assistente": assistente,
        },
    })
    return resposta
