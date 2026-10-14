import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel

from db import get_db
from auth import require_super_admin

router = APIRouter(prefix="/api/admin/plans", tags=["plans"])


class PlanIn(BaseModel):
    nome: str
    preco_mensal: float = 0
    max_bots: int = 1
    max_conexoes: int = 1
    max_usuarios: int = 1
    # Dias que a mídia das conversas fica guardada. None = usa o default do
    # servidor (MEDIA_RETENTION_DAYS); 0 = nunca apaga. Ver media_retention.py.
    retencao_midia_dias: int | None = None
    # Teto de tokens de IA no mês, contado só quando a empresa usa a chave da
    # plataforma. Empresa que conectou a conta própria paga o provedor direto e
    # não tem teto nosso. None ou 0 = ilimitado. Ver ai_runtime._checar_cota.
    max_tokens_ia_mes: int | None = None
    status: str = "active"


def _now():
    return datetime.now(timezone.utc).isoformat()


@router.get("")
async def list_plans(_=Depends(require_super_admin)):
    db = get_db()
    return await db.plans.find({}, {"_id": 0}).sort("preco_mensal", 1).to_list(200)


@router.post("")
async def create_plan(body: PlanIn, _=Depends(require_super_admin)):
    db = get_db()
    plan = {"id": str(uuid.uuid4()), **body.model_dump(), "created_at": _now()}
    await db.plans.insert_one(plan)
    plan.pop("_id", None)
    return plan


@router.patch("/{plan_id}")
async def update_plan(plan_id: str, body: PlanIn, _=Depends(require_super_admin)):
    db = get_db()
    res = await db.plans.update_one({"id": plan_id}, {"$set": body.model_dump()})
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Plano não encontrado")
    return await db.plans.find_one({"id": plan_id}, {"_id": 0})


@router.delete("/{plan_id}")
async def delete_plan(plan_id: str, _=Depends(require_super_admin)):
    db = get_db()
    await db.plans.delete_one({"id": plan_id})
    return {"ok": True}
