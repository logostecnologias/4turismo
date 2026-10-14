import asyncio
import uuid
from datetime import datetime, timezone
from typing import Annotated
from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, EmailStr, Field, StringConstraints
from pymongo.errors import DuplicateKeyError

from db import get_db
from auth import (require_super_admin, hash_password, create_access_token,
                  create_refresh_token, _set_auth_cookies, _cookie_secure)
from plan_limits import plan_excess
import whatsapp_client as whatsapp
import audit

router = APIRouter(prefix="/api/admin", tags=["super_admin"])

# Piso da senha inicial do admin da empresa. Não existe troca obrigatória no
# primeiro acesso, então a senha digitada aqui é a que o cliente vai usar por
# tempo indefinido: aceitar "1" era aceitar isso para sempre. A tela mostra o
# mesmo número e oferece uma senha gerada, mas a barreira é esta.
SENHA_MIN = 8

# Texto que sobrevive a espaço em volta: `min_length` sozinho aceita "  " como
# nome de dois caracteres, e a empresa nasceria sem nome legível na lista.
Nome = Annotated[str, StringConstraints(strip_whitespace=True, min_length=2, max_length=120)]


class TenantCreate(BaseModel):
    name: Nome
    plan_id: str | None = None
    admin_name: Nome
    admin_email: EmailStr
    admin_password: str = Field(min_length=SENHA_MIN, max_length=128)


class TenantUpdate(BaseModel):
    name: str | None = None
    plan_id: str | None = None
    status: str | None = None


async def _enrich_plan(db, tenants: list[dict]):
    plan_ids = list({t["plan_id"] for t in tenants if t.get("plan_id")})
    plans = {p["id"]: p async for p in db.plans.find({"id": {"$in": plan_ids}}, {"_id": 0})}
    for t in tenants:
        t["plan"] = plans.get(t.get("plan_id"))
    return tenants


async def _count_por_tenant(collection, tenant_ids: list[str]) -> dict[str, int]:
    """Quantos documentos cada empresa tem, numa consulta só."""
    if not tenant_ids:
        return {}
    return {
        linha["_id"]: linha["n"]
        async for linha in collection.aggregate([
            {"$match": {"tenant_id": {"$in": tenant_ids}}},
            {"$group": {"_id": "$tenant_id", "n": {"$sum": 1}}},
        ])
    }


@router.get("/tenants")
async def list_tenants(_=Depends(require_super_admin)):
    """Lista as empresas com o resumo de bots e usuários de cada uma.

    As contagens vêm agrupadas, e não uma consulta por empresa dentro do laço:
    com 200 empresas na tela isso eram 400 idas ao banco para montar duas
    colunas. Agora são duas, e em paralelo.
    """
    db = get_db()
    tenants = await db.tenants.find({}, {"_id": 0}).sort("created_at", -1).to_list(500)
    ids = [t["id"] for t in tenants]
    bots, users = await asyncio.gather(
        _count_por_tenant(db.bots, ids),
        _count_por_tenant(db.users, ids),
    )
    result = [
        {**t, "bots_count": bots.get(t["id"], 0), "users_count": users.get(t["id"], 0)}
        for t in tenants
    ]
    return await _enrich_plan(db, result)


@router.post("/tenants")
async def create_tenant(body: TenantCreate, _=Depends(require_super_admin)):
    db = get_db()
    email = body.admin_email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    if body.plan_id and not await db.plans.find_one({"id": body.plan_id}):
        raise HTTPException(status_code=400, detail="Plano inválido")
    tenant_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    tenant = {
        "id": tenant_id,
        "name": body.name,
        "plan_id": body.plan_id,
        "status": "active",
        "created_at": now,
    }
    await db.tenants.insert_one(tenant)
    try:
        await db.users.insert_one({
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "name": body.admin_name,
            "email": email,
            "role": "tenant_admin",
            "status": "active",
            "password_hash": hash_password(body.admin_password),
            "created_at": now,
        })
    except DuplicateKeyError:
        # A empresa é gravada antes do admin, então quem perde a corrida no
        # índice único de `users.email` deixaria uma empresa sem nenhum
        # usuário: ela apareceria na lista, ninguém entraria nela e nem o
        # cliente nem o suporte saberiam por quê. Acontece com duas abas
        # abertas, F5 no meio do envio ou retry de rede: a checagem acima não
        # cobre esses casos, porque entre ela e o insert existe uma janela.
        await db.tenants.delete_one({"id": tenant_id})
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    tenant.pop("_id", None)
    (await _enrich_plan(db, [tenant]))
    return tenant


@router.patch("/tenants/{tenant_id}")
async def update_tenant(tenant_id: str, body: TenantUpdate, admin=Depends(require_super_admin)):
    db = get_db()
    update = {k: v for k, v in body.model_dump().items() if v is not None}
    if not update:
        return {"ok": True}
    novo_plano = None
    if "plan_id" in update:
        novo_plano = await db.plans.find_one({"id": update["plan_id"]}, {"_id": 0})
        if not novo_plano:
            raise HTTPException(status_code=400, detail="Plano inválido")
    res = await db.tenants.update_one({"id": tenant_id}, {"$set": update})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Agência não encontrada")
    tenant = await db.tenants.find_one({"id": tenant_id}, {"_id": 0})
    (await _enrich_plan(db, [tenant]))
    if novo_plano:
        # Downgrade não remove nada retroativamente — só avisa quem trocou.
        tenant["plan_warnings"] = await plan_excess(db, tenant_id, novo_plano)
    # Suspender/reativar uma empresa e trocar o plano são decisões do Super
    # Admin que precisam de rastro (ZZ-21).
    if "status" in update or "plan_id" in update:
        await audit.registrar(db, ator=admin, acao="tenant.update",
                              tenant_id=tenant_id, meta=update)
    return tenant


# Tudo que morre com a empresa, numa lista só. Estava escrito como uma
# sequência de `delete_many` no corpo da rota, e o jeito de essa sequência ficar
# defasada é criar uma coleção nova em outro módulo e não lembrar daqui: foi
# assim que a agenda, os agentes de IA, as mídias, os departamentos, o acesso
# por conexão e o próprio histórico de mensagens ficaram para trás. Coleção
# nova com `tenant_id` entra aqui no mesmo commit em que nasce.
#
# Fora da lista de propósito:
#   impersonation_log   é auditoria do super admin, e auditoria que desaparece
#                       junto com o objeto auditado não serve de nada
#   processed_messages  não tem `tenant_id` (é dedup por id de mensagem) e
#                       expira sozinha por TTL em 7 dias
COLECOES_DA_EMPRESA = (
    "users",
    "bots",
    "flows",
    "flow_versions",
    "whatsapp_connections",
    "user_connection_access",
    "departments",
    "conversation_sessions",
    # O histórico das conversas e a ficha dos arquivos. O binário em disco fica
    # para a varredura de órfãos do `media_retention`: sem o documento ela
    # deixa de reconhecer o arquivo e o recolhe na passagem seguinte, 24h
    # depois. Apagar arquivo aqui dentro trocaria uma exclusão de banco por uma
    # operação de disco que pode falhar no meio.
    "messages_log",
    "media_files",
    "contacts",
    "contact_notes",
    "contact_field_defs",
    "pipelines",
    "deals",
    "deal_events",
    "activities",
    # Agenda: grade, catálogo e o que estava marcado. O `agenda_worker` varre
    # os agendamentos de todas as empresas de uma vez, então lembrete de
    # empresa excluída continuaria saindo por uma conexão que já não existe.
    "appointments",
    # Contador de lugares por saída (`saidas.py`).
    "saidas",
    # O site público da agência e os cliques do botão (`sites.py`).
    "sites",
    "site_cliques",
    "site_paginas",
    "site_traducoes",
    "resources",
    "services",
    "scheduling_settings",
    # Agentes de IA e o consumo medido deles.
    "ai_agents",
    "ai_usage",
    "ai_estoque",
)


@router.delete("/tenants/{tenant_id}")
async def delete_tenant(tenant_id: str, admin=Depends(require_super_admin)):
    db = get_db()
    alvo = await db.tenants.find_one({"id": tenant_id}, {"_id": 0, "name": 1})
    # Best-effort: cancela a assinatura do nosso app nas contas da Meta antes
    # de apagar as conexões (ZZ-14). Sem isto a Meta continua entregando o
    # webhook de números de uma empresa que não existe mais, e cada evento
    # desses vira uma consulta ao banco que nunca casa com nada. Falha aqui
    # não impede a exclusão: a coleção é apagada logo abaixo de qualquer jeito.
    async for conn in db.whatsapp_connections.find(
        {"tenant_id": tenant_id, "phone_number_id": {"$ne": None}}, {"_id": 0},
    ):
        try:
            cred = whatsapp.credencial(conn)
            if cred:
                await whatsapp.desassinar_webhook(cred)
        except Exception:
            pass
    await db.tenants.delete_one({"id": tenant_id})
    apagados = {}
    for colecao in COLECOES_DA_EMPRESA:
        res = await db[colecao].delete_many({"tenant_id": tenant_id})
        if res.deleted_count:
            apagados[colecao] = res.deleted_count
    await audit.registrar(
        db, ator=admin, acao="tenant.delete", tenant_id=tenant_id,
        alvo=(alvo or {}).get("name"), meta={"apagados": apagados},
    )
    # O que saiu, por coleção: também vai para a auditoria, junto de quem apagou.
    return {"ok": True, "apagados": apagados}


@router.post("/tenants/{tenant_id}/impersonate")
async def impersonate(tenant_id: str, response: Response, super_admin=Depends(require_super_admin)):
    """SA vira tenant_admin desse tenant. Guarda o token SA original em cookie
    separado para restauração posterior via /auth/exit-impersonation."""
    import uuid
    db = get_db()
    tenant = await db.tenants.find_one({"id": tenant_id})
    if not tenant:
        raise HTTPException(status_code=404, detail="Agência não encontrada")
    log_id = str(uuid.uuid4())
    now = datetime.now(timezone.utc).isoformat()
    await db.impersonation_log.insert_one({
        "id": log_id,
        "super_admin_id": super_admin["id"],
        "tenant_id": tenant_id,
        "iniciado_em": now,
        "finalizado_em": None,
    })
    # Issue new access as tenant_admin (borrowing SA identity but with tenant scope).
    # `tv` é o do próprio Super Admin: trocar a senha dele invalida também as
    # sessões de suporte abertas em nome dele (ZZ-09).
    access = create_access_token(super_admin["id"], super_admin["email"], "tenant_admin",
                                 tenant_id, log_id, token_version=super_admin.get("token_version", 0))
    refresh = create_refresh_token(super_admin["id"], super_admin.get("token_version", 0))
    _set_auth_cookies(response, access, refresh)
    secure = _cookie_secure()
    response.set_cookie("impersonation_id", log_id, httponly=True, secure=secure,
                        samesite="lax", max_age=12 * 3600, path="/")
    response.set_cookie("impersonated_role", "super_admin", httponly=False, secure=secure,
                        samesite="lax", max_age=12 * 3600, path="/")
    return {"ok": True, "tenant_name": tenant["name"], "impersonation_id": log_id}


@router.get("/metrics")
async def global_metrics(_=Depends(require_super_admin)):
    db = get_db()
    return {
        "tenants_total": await db.tenants.count_documents({}),
        "tenants_active": await db.tenants.count_documents({"status": "active"}),
        "users_total": await db.users.count_documents({"role": "tenant_admin"}),
        "bots_total": await db.bots.count_documents({}),
        "messages_total": await db.messages_log.count_documents({}),
        "conversations_total": await db.conversation_sessions.count_documents({}),
    }
