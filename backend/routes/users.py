"""Tenant admin manages agents (role='agent') + their connection access."""
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, EmailStr, Field

from db import get_db
from auth import require_tenant_admin, hash_password
from plan_limits import enforce_plan_limit
import audit

router = APIRouter(prefix="/api/users", tags=["users"])

# Piso da senha, aplicado na criação E na edição (ZZ-15). Antes, o mínimo de 8
# só existia em /api/settings/senha; as rotas que *criavam* credencial aceitavam
# uma letra. O atendente não troca a própria senha (a tela de Configurações é do
# admin), então a senha definida aqui é a que ele usa por tempo indefinido.
SENHA_MIN = 8


class UserIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    email: EmailStr
    password: str = Field(min_length=SENHA_MIN, max_length=128)
    role: str = "agent"  # agent | tenant_admin (rarely used)
    connection_ids: list[str] = []


class UserUpdate(BaseModel):
    name: str | None = None
    # `None` = não mexer na senha; se vier, respeita o mesmo piso da criação.
    password: str | None = Field(default=None, min_length=SENHA_MIN, max_length=128)
    status: str | None = None
    connection_ids: list[str] | None = None


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _get_access(db, tenant_id, user_id):
    rows = await db.user_connection_access.find(
        {"tenant_id": tenant_id, "user_id": user_id}, {"_id": 0}
    ).to_list(200)
    return [r["whatsapp_connection_id"] for r in rows]


async def _conexoes_validas(db, tenant_id: str, ids: list[str]) -> list[str]:
    """Só os ids de conexão que são realmente desta empresa (ZZ-20).

    Antes, os `connection_ids` do corpo entravam em `user_connection_access`
    sem conferência. O escopo de leitura ainda filtra por `tenant_id`, então
    não vazava — mas gravava lixo (um id de outra empresa, ou inexistente) que
    ninguém limpava. Aqui a lista é reduzida à interseção com as conexões da
    empresa, preservando a ordem pedida.
    """
    if not ids:
        return []
    existentes = await db.whatsapp_connections.find(
        {"tenant_id": tenant_id, "id": {"$in": list(ids)}}, {"_id": 0, "id": 1}
    ).to_list(500)
    validas = {c["id"] for c in existentes}
    return [i for i in ids if i in validas]


@router.get("")
async def list_users(admin=Depends(require_tenant_admin)):
    db = get_db()
    users = await db.users.find(
        {"tenant_id": admin["tenant_id"]},
        {"_id": 0, "password_hash": 0},
    ).sort("created_at", -1).to_list(500)
    for u in users:
        u["connection_ids"] = await _get_access(db, admin["tenant_id"], u["id"])
    return users


@router.post("")
async def create_user(body: UserIn, admin=Depends(require_tenant_admin)):
    db = get_db()
    email = body.email.lower()
    if await db.users.find_one({"email": email}):
        raise HTTPException(status_code=400, detail="Email já cadastrado")
    if body.role not in ("agent", "tenant_admin"):
        raise HTTPException(status_code=400, detail="Papel inválido")
    await enforce_plan_limit(db, admin["tenant_id"], "users", "max_usuarios", "usuários")
    user_id = str(uuid.uuid4())
    doc = {
        "id": user_id,
        "tenant_id": admin["tenant_id"],
        "name": body.name,
        "email": email,
        "role": body.role,
        "status": "active",
        "password_hash": hash_password(body.password),
        "created_at": _now(),
    }
    await db.users.insert_one(doc)
    cids = await _conexoes_validas(db, admin["tenant_id"], body.connection_ids)
    for cid in cids:
        await db.user_connection_access.insert_one({
            "id": str(uuid.uuid4()),
            "tenant_id": admin["tenant_id"],
            "user_id": user_id,
            "whatsapp_connection_id": cid,
        })
    doc.pop("_id", None); doc.pop("password_hash", None)
    doc["connection_ids"] = cids
    return doc


@router.patch("/{user_id}")
async def update_user(user_id: str, body: UserUpdate, admin=Depends(require_tenant_admin)):
    db = get_db()
    u = await db.users.find_one({"id": user_id, "tenant_id": admin["tenant_id"]})
    if not u:
        raise HTTPException(status_code=404, detail="Usuário não encontrado")
    upd = {}
    if body.name is not None: upd["name"] = body.name
    if body.status is not None: upd["status"] = body.status
    if body.password: upd["password_hash"] = hash_password(body.password)
    if upd:
        # Trocar a senha OU suspender (`status`) invalida as sessões abertas
        # daquele usuário na hora (ZZ-09), via bump de token_version. A
        # suspensão também já é barrada por status em get_current_user; o bump
        # cobre o caso de ele ter um token válido em outra aba no instante.
        ops = {"$set": upd}
        if body.password or body.status is not None:
            ops["$inc"] = {"token_version": 1}
        await db.users.update_one({"id": user_id}, ops)
        # Reset de senha e suspensão de um atendente pelo admin deixam rastro
        # (ZZ-21): são justamente as ações que um admin comprometido usaria.
        if body.password or body.status is not None:
            await audit.registrar(
                db, ator=admin, acao="user.update", tenant_id=admin["tenant_id"],
                alvo=user_id,
                meta={"senha_trocada": bool(body.password), "status": body.status},
            )
    if body.connection_ids is not None:
        cids = await _conexoes_validas(db, admin["tenant_id"], body.connection_ids)
        await db.user_connection_access.delete_many({"tenant_id": admin["tenant_id"], "user_id": user_id})
        for cid in cids:
            await db.user_connection_access.insert_one({
                "id": str(uuid.uuid4()),
                "tenant_id": admin["tenant_id"],
                "user_id": user_id,
                "whatsapp_connection_id": cid,
            })
    out = await db.users.find_one({"id": user_id}, {"_id": 0, "password_hash": 0})
    out["connection_ids"] = await _get_access(db, admin["tenant_id"], user_id)
    return out


@router.delete("/{user_id}")
async def delete_user(user_id: str, admin=Depends(require_tenant_admin)):
    db = get_db()
    if user_id == admin["id"]:
        raise HTTPException(status_code=400, detail="Não pode excluir a si mesmo")
    await db.users.delete_one({"id": user_id, "tenant_id": admin["tenant_id"]})
    await db.user_connection_access.delete_many({"tenant_id": admin["tenant_id"], "user_id": user_id})
    return {"ok": True}
