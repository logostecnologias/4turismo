"""Teste da tela "Hoje" (`hoje.py`).

Onde estao os riscos, e por isso o que se testa aqui:

- **O corte do dia.** O dia e o da clinica, nao o UTC: um horario das 22h em
  Brasilia esta gravado como 01:00Z do dia seguinte, e cortar pelo UTC o
  mandaria para a agenda de amanha. A etapa de corte usa exatamente esse caso.
- **O escopo.** A fila tem de respeitar as conexoes liberadas para o atendente
  (o mesmo filtro da Central), e as tarefas dele sao so as dele. Mostrar a fila
  inteira na tela inicial furaria a permissao que a Central aplica.
- **O que o atendente nao ve.** Orcamentos, alertas e o mes sao do admin.
- **Taxa de falta sobre decididos**, nula sem nenhum decidido, como no
  relatorio de agenda.
"""
import asyncio
from datetime import datetime, timezone

from fakedb import FakeDB

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import hoje

T = "t-hoje"
OUTRA = "t-outra"
ADMIN = {"id": "u-adm", "tenant_id": T, "role": "tenant_admin", "name": "Ana"}
ATENDENTE = {"id": "u-at", "tenant_id": T, "role": "agent", "name": "Bia"}

# 08/10/2026, 15:00 em Brasilia (UTC-3) = 18:00Z.
AGORA = datetime(2026, 10, 8, 18, 0, tzinfo=timezone.utc)

ok = 0


def check(nome, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  FALHOU  {nome}  -> {extra}")
        raise AssertionError(nome)


def _ag(id_, inicio, status="agendado", tenant=T, **extra):
    return {"id": id_, "tenant_id": tenant, "inicio": inicio, "fim": inicio, "status": status,
            "contact_id": "c1", "contact_nome": "Mariana", "service_nome": "Avaliação",
            "resource_nome": "Dra. Helena", "origem": "manual", "lembrete_enviado_em": None,
            **extra}


async def etapa_semear():
    print("\n[semear]")
    ags = [
        _ag("a-manha", "2026-10-08T12:00:00+00:00", "confirmado"),     # 09h local
        _ag("a-tarde", "2026-10-08T19:00:00+00:00"),                   # 16h local
        # 22h local de hoje = 01:00Z de amanha: tem de cair em HOJE.
        _ag("a-noite", "2026-10-09T01:00:00+00:00"),
        _ag("a-cancel", "2026-10-08T20:00:00+00:00", "cancelado"),
        # Amanha 10h local, ninguem confirmou.
        _ag("a-amanha", "2026-10-09T13:00:00+00:00"),
        _ag("a-amanha-ok", "2026-10-09T14:00:00+00:00", "confirmado"),
        # Mes: duas realizadas, uma falta.
        _ag("m1", "2026-10-02T13:00:00+00:00", "concluido"),
        _ag("m2", "2026-10-03T13:00:00+00:00", "concluido"),
        _ag("m3", "2026-10-05T13:00:00+00:00", "faltou"),
        # Outra clinica no mesmo horario: nao pode aparecer.
        _ag("x1", "2026-10-08T19:00:00+00:00", tenant=OUTRA),
    ]
    for a in ags:
        await DB.appointments.insert_one(a)

    await DB.whatsapp_connections.insert_one(
        {"id": "w1", "tenant_id": T, "nome": "Recepção", "status": "conectado"})
    await DB.whatsapp_connections.insert_one(
        {"id": "w2", "tenant_id": T, "nome": "Estética", "status": "desconectado"})
    await DB.user_connection_access.insert_one(
        {"tenant_id": T, "user_id": ATENDENTE["id"], "whatsapp_connection_id": "w1"})
    await DB.contacts.insert_one({"id": "c1", "tenant_id": T, "nome": "Mariana",
                                  "created_at": "2026-10-04T10:00:00+00:00"})
    for sid, conn, desde in (("s1", "w1", "2026-10-08T17:50:00+00:00"),
                             ("s2", "w2", "2026-10-08T17:40:00+00:00")):
        await DB.conversation_sessions.insert_one({
            "id": sid, "tenant_id": T, "whatsapp_connection_id": conn, "contact_id": "c1",
            "status_atendimento": "aguardando", "aguardando_desde": desde,
            "last_message": {"content": "Quanto custa o bioestimulador?"},
            "created_at": desde, "updated_at": desde})

    await DB.activities.insert_one({"id": "t1", "tenant_id": T, "titulo": "Ligar para Paula",
                                    "vence_em": "2026-10-07T12:00:00+00:00",
                                    "responsavel_id": ATENDENTE["id"], "concluida_em": None})
    await DB.activities.insert_one({"id": "t2", "tenant_id": T, "titulo": "Revisar orçamento",
                                    "vence_em": "2026-10-06T12:00:00+00:00",
                                    "responsavel_id": ADMIN["id"], "concluida_em": None,
                                    "deal_id": "d1"})

    await DB.deals.insert_one({"id": "d1", "tenant_id": T, "titulo": "Com passo",
                               "valor": 3000, "status": "aberto"})
    await DB.deals.insert_one({"id": "d2", "tenant_id": T, "titulo": "Parado",
                               "valor": 5000, "status": "aberto"})
    await DB.deals.insert_one({"id": "d3", "tenant_id": T, "titulo": "Aprovado",
                               "valor": 2000, "status": "ganho",
                               "fechado_em": "2026-10-06T12:00:00+00:00"})
    await DB.ai_agents.insert_one({"id": "ia1", "tenant_id": T, "nome": "Sofia", "ativo": True,
                                   "credencial_status": "erro"})
    check("semeadura pronta", await DB.appointments.count_documents({}) == 10)


async def etapa_admin():
    print("\n[admin]")
    r = await hoje.montar(DB, ADMIN, AGORA)
    ids = [a["id"] for a in r["agenda"]["itens"]]
    check("o dia e o da clinica: 22h local entra em hoje", "a-noite" in ids, ids)
    check("cancelado fica fora da agenda do dia", "a-cancel" not in ids, ids)
    check("outra clinica nao aparece", "x1" not in ids, ids)
    check("agenda em ordem de horario", ids == ["a-manha", "a-tarde", "a-noite"], ids)
    # Solicitacoes esperando a agencia: todas as futuras com status agendado
    # (nao so as de amanha), da mais proxima para a mais distante. Confirmada,
    # cancelada e as que ja passaram ficam fora.
    check("solicitacoes pendentes sao as futuras ainda nao confirmadas",
          [a["id"] for a in r["amanha_sem_confirmacao"]["itens"]]
          == ["a-tarde", "a-noite", "a-amanha"], r["amanha_sem_confirmacao"])
    check("admin ve a fila inteira", r["fila"]["total"] == 2, r["fila"])
    check("fila do que espera ha mais tempo primeiro", r["fila"]["itens"][0]["id"] == "s2",
          r["fila"]["itens"])
    check("a fila traz o nome e a previa", r["fila"]["itens"][0]["nome"] == "Mariana"
          and "bioestimulador" in r["fila"]["itens"][0]["previa"], r["fila"]["itens"][0])
    check("admin ve todas as tarefas atrasadas", r["tarefas_atrasadas"]["total"] == 2,
          r["tarefas_atrasadas"])
    check("orcamento parado e o sem tarefa pendente",
          [d["id"] for d in r["orcamentos_parados"]["itens"]] == ["d2"], r["orcamentos_parados"])
    tipos = sorted(a["tipo"] for a in r["alertas"])
    check("alerta de WhatsApp desconectado e de chave de IA quebrada",
          tipos == ["assistente", "whatsapp"], r["alertas"])
    mes = r["mes"]
    check("consultas realizadas no mes", mes["consultas_realizadas"] == 2, mes)
    check("taxa de falta sobre os decididos", abs(mes["taxa_falta"] - 1 / 3) < 1e-9, mes)
    check("orcamentos aprovados no mes", mes["orcamentos_aprovados"]["valor"] == 2000, mes)
    check("pacientes novos no mes", mes["pacientes_novos"] == 1, mes)


async def etapa_atendente():
    print("\n[atendente]")
    r = await hoje.montar(DB, ATENDENTE, AGORA)
    check("atendente ve so a fila das conexoes liberadas",
          r["fila"]["total"] == 1 and r["fila"]["itens"][0]["id"] == "s1", r["fila"])
    check("atendente ve so as proprias tarefas",
          [t["id"] for t in r["tarefas_atrasadas"]["itens"]] == ["t1"], r["tarefas_atrasadas"])
    check("atendente ve a agenda do dia", len(r["agenda"]["itens"]) == 3, r["agenda"])
    for chave in ("orcamentos_parados", "alertas", "mes"):
        check(f"atendente nao recebe {chave}", chave not in r, list(r))


async def etapa_sem_decididos():
    print("\n[taxa sem decididos]")
    r = await hoje.montar(DB, {**ADMIN, "tenant_id": OUTRA}, AGORA)
    check("sem horario decidido a taxa e nula, nao zero", r["mes"]["taxa_falta"] is None, r["mes"])
    check("clinica sem conexao recebe o alerta de conectar",
          any("Nenhum número" in a["texto"] for a in r["alertas"]), r["alertas"])


def test_suite():
    db_mod.get_db = lambda: DB

    async def _tudo():
        await etapa_semear()
        await etapa_admin()
        await etapa_atendente()
        await etapa_sem_decididos()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
