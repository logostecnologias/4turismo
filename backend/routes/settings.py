"""Tela de Configurações da empresa: a própria conta e o plano contratado.

Duas coisas que não existiam em lugar nenhum:

1. **Trocar a própria senha.** `/api/users` inteiro é `require_tenant_admin` e
   edita *outro* usuário — não havia caminho para alguém trocar a sua, nem
   conferência da senha atual.
2. **Ver o plano.** Os cinco limites já eram aplicados, mas nenhum aparecia em
   tela: o cliente descobria o teto levando um 403 ao criar o quarto robô.
"""
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from auth import (get_current_user, require_tenant_admin,
                  hash_password, verify_password)
from db import get_db
from plan_limits import uso_do_plano
from aparencia import PALETAS, MODOS, paleta_valida

router = APIRouter(prefix="/api/settings", tags=["settings"])

SENHA_MINIMA = 8


def _bloqueia_modo_suporte(user: dict):
    """Em impersonation o token é do Super Admin com o papel trocado, então
    `user["id"]` continua sendo o **dele**. Editar a conta daqui mexeria na
    credencial do Super Admin achando que mexe na do cliente.
    """
    if user.get("impersonation_id"):
        raise HTTPException(
            status_code=403,
            detail="Em modo suporte não é possível alterar dados de conta. "
                   "Saia do modo suporte para editar a sua.",
        )


# ---------------------------------------------------------------------------
# Minha conta
# ---------------------------------------------------------------------------

class ContaIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)


@router.patch("/conta")
async def atualizar_conta(body: ContaIn, user=Depends(get_current_user)):
    """O nome é o que aparece no prefixo de toda mensagem enviada por um
    atendente (`send_human_message`), então trocá-lo muda o que o contato lê."""
    _bloqueia_modo_suporte(user)
    nome = body.name.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="O nome não pode ficar vazio")
    await get_db().users.update_one({"id": user["id"]}, {"$set": {"name": nome}})
    return {"ok": True, "name": nome}


class SenhaIn(BaseModel):
    senha_atual: str
    nova_senha: str


@router.post("/senha")
async def trocar_senha(body: SenhaIn, user=Depends(get_current_user)):
    """A senha atual é exigida mesmo já havendo sessão válida: o cookie é
    httpOnly e de longa duração, então uma máquina destravada bastaria para
    trocar a senha e tomar a conta.
    """
    _bloqueia_modo_suporte(user)
    db = get_db()
    # `get_current_user` remove o hash do documento que devolve — daí reler.
    doc = await db.users.find_one({"id": user["id"]}, {"_id": 0, "password_hash": 1})
    if not doc or not verify_password(body.senha_atual, doc.get("password_hash") or ""):
        raise HTTPException(status_code=400, detail="Senha atual incorreta")
    if len(body.nova_senha) < SENHA_MINIMA:
        raise HTTPException(
            status_code=400,
            detail=f"A nova senha precisa de pelo menos {SENHA_MINIMA} caracteres",
        )
    if body.nova_senha == body.senha_atual:
        raise HTTPException(status_code=400, detail="A nova senha é igual à atual")
    # `$inc` em token_version invalida toda sessão emitida antes desta troca
    # (ZZ-09): quem tinha o cookie antigo — inclusive numa máquina roubada — é
    # derrubado na próxima requisição.
    await db.users.update_one(
        {"id": user["id"]},
        {"$set": {"password_hash": hash_password(body.nova_senha)},
         "$inc": {"token_version": 1}},
    )
    return {"ok": True}


# ---------------------------------------------------------------------------
# Aparência
# ---------------------------------------------------------------------------

class PreferenciasIn(BaseModel):
    modo: Literal[MODOS]  # type: ignore[valid-type]


@router.patch("/preferencias")
async def salvar_preferencias(body: PreferenciasIn, user=Depends(get_current_user)):
    """Claro, escuro ou sistema: é de cada pessoa, não da empresa.

    Não passa por `_bloqueia_modo_suporte`: em modo suporte isto grava no
    Super Admin, e é dele mesmo a preferência de tema. Ao contrário de senha e
    nome, não há como o cliente sair prejudicado.
    """
    await get_db().users.update_one(
        {"id": user["id"]}, {"$set": {"preferencias.modo": body.modo}}
    )
    return {"ok": True, "modo": body.modo}


class AparenciaIn(BaseModel):
    paleta: Literal[PALETAS]  # type: ignore[valid-type]


@router.patch("/aparencia")
async def salvar_aparencia(body: AparenciaIn, user=Depends(require_tenant_admin)):
    """A paleta da agência (cor e tipografia), para a equipe inteira.

    Liberada em modo suporte de propósito: é configuração da empresa, como
    qualquer outra que o suporte ajusta a pedido do cliente, e grava no
    `tenant_id` visitado, não no usuário do Super Admin.
    """
    paleta = paleta_valida(body.paleta)
    await get_db().tenants.update_one(
        {"id": user["tenant_id"]}, {"$set": {"aparencia.paleta": paleta}}
    )
    return {"ok": True, "paleta": paleta}


# ---------------------------------------------------------------------------
# Empresa e plano
# ---------------------------------------------------------------------------

@router.get("/empresa")
async def empresa(user=Depends(require_tenant_admin)):
    """Nome da empresa, plano e uso contra cada limite.

    Uma chamada, não três: o painel mostra os três blocos juntos, e o que
    domina aqui é a ida e volta pelo túnel SSH, não o volume.
    """
    db = get_db()
    tenant = await db.tenants.find_one(
        {"id": user["tenant_id"]}, {"_id": 0, "id": 1, "name": 1, "status": 1,
                                    "created_at": 1, "plan_id": 1}
    )
    if not tenant:
        raise HTTPException(status_code=404, detail="Agência não encontrada")

    plano = None
    if tenant.get("plan_id"):
        plano = await db.plans.find_one({"id": tenant["plan_id"]}, {"_id": 0})

    return {
        "empresa": {
            "id": tenant["id"],
            "nome": tenant.get("name"),
            "status": tenant.get("status"),
            "criada_em": tenant.get("created_at"),
        },
        "plano": None if not plano else {
            "nome": plano.get("nome"),
            "preco_mensal": plano.get("preco_mensal", 0),
            "retencao_midia_dias": plano.get("retencao_midia_dias"),
        },
        "uso": await uso_do_plano(db, user["tenant_id"], plano),
    }
