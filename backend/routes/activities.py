"""API de atividades e follow-up."""
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import atividades as dominio
import datas
from auth import require_tenant_admin, require_tenant_member
from db import get_db

router = APIRouter(prefix="/api/activities", tags=["activities"])


def _now():
    return datetime.now(timezone.utc).isoformat()


class AtividadeIn(BaseModel):
    titulo: str
    tipo: str = Field(default="tarefa", pattern="^(ligacao|mensagem|reuniao|tarefa)$")
    vence_em: str
    descricao: str | None = None
    responsavel_id: str | None = None
    contact_id: str | None = None
    deal_id: str | None = None


class AtividadePatch(BaseModel):
    titulo: str | None = None
    tipo: str | None = Field(default=None, pattern="^(ligacao|mensagem|reuniao|tarefa)$")
    vence_em: str | None = None
    descricao: str | None = None
    responsavel_id: str | None = None


class ProximaIn(BaseModel):
    """A atividade seguinte, criada na mesma chamada que conclui a atual.

    Existe para o follow-up não depender de a pessoa lembrar de voltar e
    agendar: no momento em que ela marca "liguei", é ali que sabe quando é o
    próximo toque.
    """
    titulo: str
    tipo: str = Field(default="tarefa", pattern="^(ligacao|mensagem|reuniao|tarefa)$")
    vence_em: str
    descricao: str | None = None


class ConcluirIn(BaseModel):
    proxima: ProximaIn | None = None


async def _get(db, activity_id: str, tenant_id: str) -> dict:
    doc = await db.activities.find_one({"id": activity_id, "tenant_id": tenant_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Tarefa não encontrada")
    return doc


async def _valida_vinculos(db, tenant_id: str, responsavel_id: str | None,
                           contact_id: str | None, deal_id: str | None):
    """Responsável, contato e negócio têm de ser da própria empresa.

    Sem isto, um id de outra empresa colado no corpo faria a atividade aparecer
    na agenda de alguém que não deveria nem saber que aquele contato existe.
    """
    if responsavel_id and not await db.users.find_one(
            {"id": responsavel_id, "tenant_id": tenant_id}, {"_id": 0, "id": 1}):
        raise HTTPException(status_code=400, detail="Responsável inválido")
    if contact_id and not await db.contacts.find_one(
            {"id": contact_id, "tenant_id": tenant_id}, {"_id": 0, "id": 1}):
        raise HTTPException(status_code=400, detail="Cliente inválido")
    if deal_id and not await db.deals.find_one(
            {"id": deal_id, "tenant_id": tenant_id}, {"_id": 0, "id": 1}):
        raise HTTPException(status_code=400, detail="Orçamento inválido")


@router.get("/summary")
async def summary(hoje_ate: str | None = None, semana_ate: str | None = None,
                  responsavel_id: str | None = None, user=Depends(require_tenant_member)):
    """Contadores da agenda, para o selo do menu e o painel.

    Os cortes de "hoje" e "semana" vêm do cliente porque só ele sabe o fuso de
    quem está olhando — calcular aqui faria a agenda de fim de tarde mostrar as
    tarefas de amanhã.
    """
    db = get_db()
    return await dominio.resumo(
        db, user["tenant_id"], responsavel_id,
        datas.para_utc(hoje_ate, "Limite de hoje"),
        datas.para_utc(semana_ate, "Limite da semana"),
    )


@router.get("")
async def list_activities(de: str | None = None, ate: str | None = None,
                          atrasadas: bool = False, concluidas: bool = False,
                          responsavel_id: str | None = None, contact_id: str | None = None,
                          deal_id: str | None = None, skip: int = 0, limit: int = 100,
                          user=Depends(require_tenant_member)):
    db = get_db()
    filtro: dict = {"tenant_id": user["tenant_id"]}
    # `concluida_em: None` e não um campo "status": o estado guardado é a data
    # da conclusão, e "pendente" é a ausência dela.
    filtro["concluida_em"] = {"$ne": None} if concluidas else None
    if responsavel_id:
        filtro["responsavel_id"] = responsavel_id
    if contact_id:
        filtro["contact_id"] = contact_id
    if deal_id:
        filtro["deal_id"] = deal_id

    janela: dict = {}
    if atrasadas:
        # Atrasada é comparação, não estado gravado.
        janela["$lt"] = _now()
    else:
        if de:
            janela["$gte"] = datas.para_utc(de, "Data inicial")
        if ate:
            janela["$lte"] = datas.para_utc(ate, "Data final")
    if janela:
        filtro["vence_em"] = janela

    limit = max(1, min(limit, 500))
    # Concluídas descem da mais recente; pendentes sobem da mais urgente — é a
    # ordem em que cada lista é lida. `_id` desempata porque duas atividades
    # criadas juntas podem vencer no mesmo instante.
    direcao = -1 if concluidas else 1
    campo = "concluida_em" if concluidas else "vence_em"
    itens = await db.activities.find(filtro, {"_id": 0}) \
        .sort([(campo, direcao), ("_id", direcao)]).skip(max(0, skip)).limit(limit).to_list(limit)

    agora = _now()
    itens = [dominio.com_atraso(a, agora) for a in itens]

    # Contato e negócio embutidos: a agenda mostra "Ligar para Maria — Plano
    # anual", e sem isso a tela faria uma requisição por linha.
    ids_contato = [a["contact_id"] for a in itens if a.get("contact_id")]
    ids_negocio = [a["deal_id"] for a in itens if a.get("deal_id")]
    contatos = {
        c["id"]: c
        async for c in db.contacts.find(
            {"id": {"$in": ids_contato}, "tenant_id": user["tenant_id"]},
            {"_id": 0, "id": 1, "nome": 1, "numero_whatsapp": 1},
        )
    } if ids_contato else {}
    negocios = {
        d["id"]: d
        async for d in db.deals.find(
            {"id": {"$in": ids_negocio}, "tenant_id": user["tenant_id"]},
            {"_id": 0, "id": 1, "titulo": 1, "valor": 1, "status": 1},
        )
    } if ids_negocio else {}
    for a in itens:
        a["contact"] = contatos.get(a.get("contact_id"))
        a["deal"] = negocios.get(a.get("deal_id"))

    return {"total": await db.activities.count_documents(filtro), "items": itens}


@router.post("")
async def create_activity(body: AtividadeIn, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    titulo = body.titulo.strip()
    if not titulo:
        raise HTTPException(status_code=400, detail="Descreva a tarefa")
    vence = datas.para_utc(body.vence_em, "Data de vencimento")
    if not vence:
        raise HTTPException(status_code=400, detail="Informe quando a tarefa vence")
    await _valida_vinculos(db, tenant_id, body.responsavel_id, body.contact_id, body.deal_id)

    doc = dominio.novo(tenant_id, user, titulo=titulo, tipo=body.tipo, vence_em=vence,
                       descricao=(body.descricao or "").strip() or None,
                       responsavel_id=body.responsavel_id, contact_id=body.contact_id,
                       deal_id=body.deal_id)
    await db.activities.insert_one(dict(doc))
    doc.pop("_id", None)
    return dominio.com_atraso(doc)


@router.patch("/{activity_id}")
async def update_activity(activity_id: str, body: AtividadePatch, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get(db, activity_id, tenant_id)

    campos: dict = {}
    if body.titulo is not None:
        titulo = body.titulo.strip()
        if not titulo:
            raise HTTPException(status_code=400, detail="Descreva a tarefa")
        campos["titulo"] = titulo
    if body.tipo is not None:
        campos["tipo"] = body.tipo
    if body.vence_em is not None:
        vence = datas.para_utc(body.vence_em, "Data de vencimento")
        if not vence:
            raise HTTPException(status_code=400, detail="Informe quando a tarefa vence")
        campos["vence_em"] = vence
    if body.descricao is not None:
        campos["descricao"] = body.descricao.strip() or None
    if body.responsavel_id is not None:
        await _valida_vinculos(db, tenant_id, body.responsavel_id or None, None, None)
        # Sem responsável a atividade sai da agenda de todo mundo; quem limpa o
        # campo herda a tarefa em vez de deixá-la órfã.
        campos["responsavel_id"] = body.responsavel_id or user["id"]

    if not campos:
        raise HTTPException(status_code=400, detail="Nada para atualizar")
    campos["updated_at"] = _now()
    await db.activities.update_one({"id": activity_id, "tenant_id": tenant_id}, {"$set": campos})
    return dominio.com_atraso(await _get(db, activity_id, tenant_id))


@router.post("/{activity_id}/complete")
async def complete_activity(activity_id: str, body: ConcluirIn | None = None,
                            user=Depends(require_tenant_member)):
    """Conclui a atividade e, se vier `proxima`, já agenda o passo seguinte.

    A próxima herda contato e negócio da atual e guarda `origem_id`, formando a
    corrente de follow-up daquele assunto.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    atual = await _get(db, activity_id, tenant_id)
    if atual.get("concluida_em"):
        raise HTTPException(status_code=409, detail="Tarefa já está concluída")

    # A próxima é validada e montada **antes** de fechar a atual. Na ordem
    # inversa, uma data inválida no follow-up deixava a atividade concluída e
    # devolvia erro: quem tentasse de novo receberia 409 e ficaria sem o
    # próximo passo, que era o motivo de ter clicado.
    proxima = None
    if body and body.proxima:
        titulo = body.proxima.titulo.strip()
        vence = datas.para_utc(body.proxima.vence_em, "Data da próxima atividade")
        if not titulo or not vence:
            raise HTTPException(status_code=400,
                                detail="A próxima tarefa precisa de descrição e data")
        proxima = dominio.novo(
            tenant_id, user, titulo=titulo, tipo=body.proxima.tipo, vence_em=vence,
            descricao=(body.proxima.descricao or "").strip() or None,
            responsavel_id=atual.get("responsavel_id"),
            contact_id=atual.get("contact_id"), deal_id=atual.get("deal_id"),
            origem_id=activity_id,
        )

    await db.activities.update_one(
        {"id": activity_id, "tenant_id": tenant_id},
        {"$set": {"concluida_em": _now(), "concluida_por": user["id"], "updated_at": _now()}},
    )
    if proxima:
        await db.activities.insert_one(dict(proxima))
        proxima.pop("_id", None)

    return {"atividade": dominio.com_atraso(await _get(db, activity_id, tenant_id)),
            "proxima": proxima}


@router.post("/{activity_id}/reopen")
async def reopen_activity(activity_id: str, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    atual = await _get(db, activity_id, tenant_id)
    if not atual.get("concluida_em"):
        raise HTTPException(status_code=409, detail="Tarefa não está concluída")
    await db.activities.update_one(
        {"id": activity_id, "tenant_id": tenant_id},
        {"$set": {"concluida_em": None, "concluida_por": None, "updated_at": _now()}},
    )
    return dominio.com_atraso(await _get(db, activity_id, tenant_id))


@router.delete("/{activity_id}")
async def delete_activity(activity_id: str, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    await _get(db, activity_id, tenant_id)
    await db.activities.delete_one({"id": activity_id, "tenant_id": tenant_id})
    # A corrente de follow-up não é apagada em cascata: as atividades seguintes
    # são compromissos reais e já foram assumidas. Só perdem a referência.
    await db.activities.update_many(
        {"tenant_id": tenant_id, "origem_id": activity_id}, {"$set": {"origem_id": None}}
    )
    return {"ok": True}
