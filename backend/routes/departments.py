import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from db import get_db
from auth import get_current_user, require_tenant_admin

router = APIRouter(prefix="/api/departments", tags=["departments"])


class DeptIn(BaseModel):
    nome: str
    cor: str = Field(default="#0B6E8A", pattern=r"^#[0-9A-Fa-f]{6}$")


def _now():
    return datetime.now(timezone.utc).isoformat()


@router.get("")
async def list_departments(user: dict = Depends(get_current_user)):
    # Any authenticated user in the tenant can list (needed for filters).
    if not user.get("tenant_id"):
        raise HTTPException(status_code=403, detail="Usuário sem agência vinculada")
    db = get_db()
    depts = await db.departments.find({"tenant_id": user["tenant_id"]}, {"_id": 0}).sort("nome", 1).to_list(200)
    return depts


@router.post("")
async def create_department(body: DeptIn, user=Depends(require_tenant_admin)):
    db = get_db()
    dept = {
        "id": str(uuid.uuid4()),
        "tenant_id": user["tenant_id"],
        "nome": body.nome,
        "cor": body.cor,
        "created_at": _now(),
    }
    await db.departments.insert_one(dept)
    dept.pop("_id", None)
    return dept


@router.patch("/{dept_id}")
async def update_department(dept_id: str, body: DeptIn, user=Depends(require_tenant_admin)):
    db = get_db()
    res = await db.departments.update_one(
        {"id": dept_id, "tenant_id": user["tenant_id"]},
        {"$set": {"nome": body.nome, "cor": body.cor}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Departamento não encontrado")
    return await db.departments.find_one({"id": dept_id}, {"_id": 0})


@router.delete("/{dept_id}")
async def delete_department(dept_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await db.departments.delete_one({"id": dept_id, "tenant_id": user["tenant_id"]})
    # Unset references
    await db.whatsapp_connections.update_many(
        {"tenant_id": user["tenant_id"], "departamento_padrao_id": dept_id},
        {"$set": {"departamento_padrao_id": None}},
    )
    await db.conversation_sessions.update_many(
        {"tenant_id": user["tenant_id"], "departamento_id": dept_id},
        {"$set": {"departamento_id": None}},
    )
    # Nós de Atendimento Humano que mandavam pra esse departamento voltam a
    # cair no padrão da conexão, em vez de apontar pra um id que não existe.
    await db.flows.update_many(
        {"tenant_id": user["tenant_id"], "nodes.data.department_id": dept_id},
        {"$set": {"nodes.$[n].data.department_id": "", "nodes.$[n].data.department_name": ""}},
        array_filters=[{"n.data.department_id": dept_id}],
    )
    return {"ok": True}
