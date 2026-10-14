"""API de Saídas: configuração, passeios com grade de saídas, vagas e reservas.

Permissão segue a regra do resto do CRM (ver CLAUDE.md): **operar é de quem
atende, configurar é do admin**. Uma recepcionista marca, remarca e cancela o
dia inteiro: barrar isso no `require_tenant_admin` transformaria a agenda numa
tela que só o dono usa. Já mexer na grade de horários, no catálogo de serviços e
nos feriados muda o que o bot promete a todos os contatos, e isso é configuração.

Ler a agenda é liberado para a equipe inteira, sem recorte por profissional: quem
atende o telefone precisa ver a agenda de todo mundo para dizer "a Ana tem às
15h". O recorte por profissional existe como **filtro** na tela, não como
barreira, e o admin, como pediu o cliente, vê tudo por padrão.
"""
import re
import uuid
from datetime import datetime, timedelta, timezone
from typing import Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

import agenda as dominio
import agenda_worker
import saidas
import sites
import contatos_csv
from auth import require_tenant_admin, require_tenant_member
from db import get_db

router = APIRouter(prefix="/api/agenda", tags=["agenda"])

# Teto de agendamentos devolvidos numa janela. A tela mostra semana ou mês de
# uma equipe; acima disso é exportação, não visualização, e uma consulta sem
# teto num tenant grande traria o histórico inteiro pelo túnel.
MAX_AGENDAMENTOS = 1000


def _now():
    return datetime.now(timezone.utc).isoformat()


# ---------------------------------------------------------------------------
# Configuração
# ---------------------------------------------------------------------------

class BloqueioIn(BaseModel):
    data: str
    motivo: str | None = None


class TemplateAvisoIn(BaseModel):
    """Modelo aprovado usado quando a janela de 24h está fechada.

    Aviso de agenda é o caso em que ela quase sempre está: o lembrete sai um
    dia antes, e quem marcou há duas semanas não escreveu nada desde então.
    Sem modelo, o WhatsApp recusa o envio, e é justamente o lembrete que faz
    este módulo se pagar.

    Cada variável é um texto que passa pelo mesmo `montar` do corpo livre, então
    `{contato}` e `{data_completa}` funcionam igual nos dois lugares.
    """
    nome: str = ""
    idioma: str = "pt_BR"
    variaveis: list[str] = Field(default_factory=list)


class PontoEmbarqueIn(BaseModel):
    """Um lugar onde a agência busca turistas. Ver `saidas.validar_pontos`."""
    id: str | None = None
    nome: str
    endereco: str | None = None
    minutos_antes: int | None = Field(default=None, ge=0, le=240)
    ativo: bool = True


class ConfigIn(BaseModel):
    fuso: str | None = None
    passo_min: int | None = Field(default=None, ge=5, le=240)
    intervalo_min: int | None = Field(default=None, ge=0, le=240)
    antecedencia_min_horas: float | None = Field(default=None, ge=0, le=720)
    antecedencia_max_dias: int | None = Field(default=None, ge=1, le=365)
    cancelamento_min_horas: float | None = Field(default=None, ge=0, le=720)
    lembrete_ativo: bool | None = None
    lembrete_horas: float | None = Field(default=None, ge=1, le=168)
    prazo_solicitacao_horas: float | None = Field(default=None, ge=0, le=720)
    bloqueios: list[BloqueioIn] | None = None
    mensagem_confirmacao: str | None = None
    mensagem_lembrete: str | None = None
    mensagem_cancelamento: str | None = None
    mensagem_cancelamento_saida: str | None = None
    template_confirmacao: TemplateAvisoIn | None = None
    template_lembrete: TemplateAvisoIn | None = None
    template_cancelamento: TemplateAvisoIn | None = None
    template_cancelamento_saida: TemplateAvisoIn | None = None
    pontos_embarque: list[PontoEmbarqueIn] | None = None


@router.get("/config")
async def ver_config(user=Depends(require_tenant_member)):
    db = get_db()
    config = await dominio.configuracao(db, user["tenant_id"])
    # Os modelos de mensagem padrão vão junto: a tela precisa mostrar o que sai
    # hoje, e um campo vazio com placeholder faria o cliente achar que nenhuma
    # mensagem é enviada.
    config["mensagens_padrao"] = agenda_worker.MENSAGENS
    config["variaveis"] = agenda_worker.VARIAVEIS
    return config


@router.put("/config")
async def salvar_config(body: ConfigIn, user=Depends(require_tenant_admin)):
    db = get_db()
    campos = {k: v for k, v in body.model_dump().items() if v is not None}
    if "bloqueios" in campos:
        campos["bloqueios"] = [
            {"data": dominio._dia(b["data"]).isoformat(),
             "motivo": (b.get("motivo") or "").strip() or None}
            for b in campos["bloqueios"]
        ]
    if "pontos_embarque" in campos:
        campos["pontos_embarque"] = saidas.validar_pontos(campos["pontos_embarque"])
    if campos.get("fuso"):
        # Conferido na entrada: um fuso inválido gravado só apareceria como
        # "nenhum horário disponível", que manda o suporte procurar na grade.
        antes = campos["fuso"]
        if str(dominio.fuso_de({"fuso": antes})) != antes:
            raise HTTPException(status_code=400, detail=f"Fuso desconhecido: {antes}")
    campos["updated_at"] = _now()
    await db.scheduling_settings.update_one(
        {"tenant_id": user["tenant_id"]}, {"$set": campos}, upsert=True)
    return await dominio.configuracao(db, user["tenant_id"])


# ---------------------------------------------------------------------------
# Serviços
# ---------------------------------------------------------------------------

class SaidaGradeIn(BaseModel):
    hora: str
    # None = usa a capacidade do passeio. Existe para a saída das 14h ir na van
    # menor sem precisar de um segundo passeio.
    capacidade: int | None = None


class ExcecaoSaidaIn(BaseModel):
    data: str
    fechado: bool = True
    motivo: str | None = None
    saidas: list[SaidaGradeIn] = []


class ServicoIn(BaseModel):
    """O passeio. Mantém o nome `services` da coleção e da rota: renomear não
    mudaria nada para o cliente e quebraria todo documento já gravado."""
    nome: str
    duracao_min: int = Field(ge=5, le=1440 * 7)
    # Por adulto. Sem preço, a reserva fica sem total, e o relatório conta
    # quantas reservas estão assim em vez de mostrar receita baixa calado.
    preco: float | None = Field(default=None, ge=0)
    # None = mesmo preço do adulto; 0 = criança não paga.
    preco_crianca: float | None = Field(default=None, ge=0)
    capacidade: int = Field(default=10, ge=saidas.MIN_CAPACIDADE, le=saidas.MAX_CAPACIDADE)
    grade_saidas: list[list[SaidaGradeIn]] | None = None
    excecoes_saidas: list[ExcecaoSaidaIn] = []
    descricao: str | None = None
    cor: str | None = None
    ativo: bool = True
    ordem: int = 0
    # Legado da agenda de horário (quem atende). A reserva por saída não usa.
    recursos: list[str] = []
    # Fase 3: a página do passeio no site. Ver `sites.py`.
    site_visivel: bool = True
    site_resumo: str | None = Field(default=None, max_length=sites.MAX_RESUMO)
    site_descricao: str | None = Field(default=None, max_length=sites.MAX_DESCRICAO)
    site_inclui: list[str] = []
    site_levar: list[str] = []
    site_fotos: list[str] = []
    site_slug: str | None = None
    # Fase 3.1: o que o turista pergunta antes de reservar.
    site_roteiro: list[sites.PassoRoteiroIn] = []
    site_nivel: Literal["leve", "moderado", "intenso"] | None = None
    site_idade_minima: int | None = Field(default=None, ge=0, le=18)
    site_melhor_epoca: str | None = Field(default=None, max_length=160)
    site_chuva: str | None = Field(default=None, max_length=400)
    site_faq: list[sites.PerguntaIn] = []
    site_legendas: dict[str, str] = {}


def _campos_servico(body: ServicoIn) -> dict:
    campos = body.model_dump()
    campos["grade_saidas"] = saidas.validar_grade_saidas(campos.get("grade_saidas"))
    campos["excecoes_saidas"] = saidas.validar_excecoes(campos.get("excecoes_saidas"))
    campos["site_inclui"] = sites.validar_lista(campos.get("site_inclui"), "O que inclui")
    campos["site_levar"] = sites.validar_lista(campos.get("site_levar"), "O que levar")
    campos["site_resumo"] = (campos.get("site_resumo") or "").strip() or None
    campos["site_descricao"] = (campos.get("site_descricao") or "").strip() or None
    campos["site_roteiro"] = sites.validar_roteiro(campos.get("site_roteiro"))
    campos["site_faq"] = sites.validar_perguntas(campos.get("site_faq"), sites.MAX_PERGUNTAS_PASSEIO)
    for chave in ("site_melhor_epoca", "site_chuva"):
        campos[chave] = (campos.get(chave) or "").strip() or None
    return campos


async def _campos_de_site(db, tenant_id: str, campos: dict, exceto_id: str = None,
                          atual: dict = None) -> dict:
    """Fotos conferidas contra a empresa e o endereço da página resolvido.

    Foto de outra empresa colada no corpo apareceria na vitrine desta, e a rota
    pública de mídia a serviria. O endereço é mantido quando não vem no corpo:
    renomear o passeio não pode quebrar o link já divulgado.
    """
    fotos = []
    for mid in campos.get("site_fotos") or []:
        if mid in fotos:
            continue
        if not await db.media_files.find_one({"id": mid, "tenant_id": tenant_id, "kind": "image"},
                                             {"_id": 0, "id": 1}):
            raise HTTPException(status_code=400, detail="Foto do passeio inválida")
        fotos.append(mid)
    if len(fotos) > sites.MAX_FOTOS:
        raise HTTPException(status_code=400, detail=f"No máximo {sites.MAX_FOTOS} fotos por passeio")
    campos["site_fotos"] = fotos
    await sites.marcar_imagens_do_site(db, tenant_id, fotos)
    campos["site_legendas"] = sites.validar_legendas(campos.get("site_legendas"), fotos)
    desejado = (campos.get("site_slug") or "").strip() or (atual or {}).get("site_slug")
    campos["site_slug"] = await sites.slug_de_passeio(db, tenant_id, desejado, campos["nome"],
                                                      exceto_id=exceto_id)
    return campos


@router.get("/services")
async def listar_servicos(todos: bool = True, user=Depends(require_tenant_member)):
    db = get_db()
    filtro = {"tenant_id": user["tenant_id"]}
    if not todos:
        filtro["ativo"] = True
    return await db.services.find(filtro, {"_id": 0}) \
        .sort([("ordem", 1), ("nome", 1)]).to_list(300)


@router.post("/services")
async def criar_servico(body: ServicoIn, user=Depends(require_tenant_admin)):
    db = get_db()
    nome = body.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Dê um nome ao passeio")
    await _nome_livre(db, "services", user["tenant_id"], nome, "um passeio")
    await _valida_recursos(db, user["tenant_id"], body.recursos)
    novo_id = str(uuid.uuid4())
    campos = await _campos_de_site(db, user["tenant_id"],
                                   {**_campos_servico(body), "nome": nome}, exceto_id=novo_id)
    doc = {"id": novo_id, "tenant_id": user["tenant_id"],
           **campos, "created_at": _now(), "updated_at": _now()}
    await db.services.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


@router.patch("/services/{service_id}")
async def editar_servico(service_id: str, body: ServicoIn,
                         user=Depends(require_tenant_admin)):
    db = get_db()
    atual = await _get(db, "services", service_id, user["tenant_id"], "Passeio")
    await _nome_livre(db, "services", user["tenant_id"], body.nome.strip(),
                      "um passeio", exceto=service_id)
    await _valida_recursos(db, user["tenant_id"], body.recursos)
    campos = await _campos_de_site(
        db, user["tenant_id"], {**_campos_servico(body), "nome": body.nome.strip()},
        exceto_id=service_id, atual=atual)
    campos["updated_at"] = _now()
    await db.services.update_one(
        {"id": service_id, "tenant_id": user["tenant_id"]}, {"$set": campos})
    return await _get(db, "services", service_id, user["tenant_id"], "Passeio")


@router.delete("/services/{service_id}")
async def remover_servico(service_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await _get(db, "services", service_id, user["tenant_id"], "Passeio")
    await _sem_futuro(db, user["tenant_id"], {"service_id": service_id},
                      "Esse passeio ainda tem")
    await db.services.delete_one({"id": service_id, "tenant_id": user["tenant_id"]})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Profissionais / recursos
# ---------------------------------------------------------------------------

class ExcecaoIn(BaseModel):
    data: str
    fechado: bool = True
    motivo: str | None = None
    janelas: list[dict] = []


class RecursoIn(BaseModel):
    nome: str
    tipo: str = Field(default="pessoa", pattern="^(pessoa|sala|equipamento)$")
    descricao: str | None = None
    cor: str | None = None
    ativo: bool = True
    # Liga o profissional a um usuário do sistema. Serve ao filtro "minha
    # agenda", e é opcional porque nem todo profissional tem login.
    user_id: str | None = None
    # None = herda o intervalo da empresa. Zero é um valor legítimo e diferente
    # de "herda", por isso não pode ser representado por ausência.
    intervalo_min: int | None = Field(default=None, ge=0, le=240)
    grade: list | None = None
    excecoes: list[ExcecaoIn] = []
    # Fase 2: guia e veículo da saída. A grade e as exceções acima são da
    # agenda de horário e não valem para a reserva por saída.
    telefone: str | None = None
    capacidade: int | None = Field(default=None, ge=1, le=saidas.MAX_CAPACIDADE)


@router.get("/resources")
async def listar_recursos(todos: bool = True, user=Depends(require_tenant_member)):
    db = get_db()
    filtro = {"tenant_id": user["tenant_id"]}
    if not todos:
        filtro["ativo"] = True
    return await db.resources.find(filtro, {"_id": 0}).sort("nome", 1).to_list(300)


@router.post("/resources")
async def criar_recurso(body: RecursoIn, user=Depends(require_tenant_admin)):
    db = get_db()
    nome = body.nome.strip()
    if not nome:
        raise HTTPException(status_code=400, detail="Dê um nome ao guia")
    await _nome_livre(db, "resources", user["tenant_id"], nome, "um guia")
    await _valida_usuario(db, user["tenant_id"], body.user_id)
    doc = {"id": str(uuid.uuid4()), "tenant_id": user["tenant_id"],
           **_campos_recurso(body), "nome": nome,
           "created_at": _now(), "updated_at": _now()}
    await db.resources.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


@router.patch("/resources/{resource_id}")
async def editar_recurso(resource_id: str, body: RecursoIn,
                         user=Depends(require_tenant_admin)):
    db = get_db()
    await _get(db, "resources", resource_id, user["tenant_id"], "Guia")
    await _nome_livre(db, "resources", user["tenant_id"], body.nome.strip(),
                      "um guia", exceto=resource_id)
    await _valida_usuario(db, user["tenant_id"], body.user_id)
    campos = {**_campos_recurso(body), "nome": body.nome.strip(), "updated_at": _now()}
    await db.resources.update_one(
        {"id": resource_id, "tenant_id": user["tenant_id"]}, {"$set": campos})
    return await _get(db, "resources", resource_id, user["tenant_id"], "Guia")


@router.delete("/resources/{resource_id}")
async def remover_recurso(resource_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await _get(db, "resources", resource_id, user["tenant_id"], "Guia")
    await _sem_futuro(db, user["tenant_id"], {"resource_id": resource_id},
                      "Esse guia ainda tem")
    await db.resources.delete_one({"id": resource_id, "tenant_id": user["tenant_id"]})
    return {"ok": True}


def _campos_recurso(body: RecursoIn) -> dict:
    campos = body.model_dump()
    # Grade ausente vira a comercial padrão, não uma agenda vazia: um
    # profissional recém-criado sem nenhum horário não apareceria em lugar
    # nenhum, e a causa seria invisível.
    campos["grade"] = dominio.validar_grade(
        body.grade if body.grade is not None else dominio.GRADE_PADRAO)
    campos["excecoes"] = [_excecao(e) for e in campos.get("excecoes") or []]
    return campos


def _excecao(e: dict) -> dict:
    """Uma exceção de data validada com o mesmo rigor da grade semanal.

    Reaproveita `validar_grade` pondo as janelas no primeiro dia de uma grade de
    sete: é a mesma checagem (HH:MM, sem inversão, sem sobreposição) e não vale
    uma segunda implementação para divergir depois. O erro é reetiquetado com a
    data: o texto de `validar_grade` diria "Segunda", e mandaria a pessoa
    procurar o problema no dia da semana errado.
    """
    data = dominio._dia(e["data"]).isoformat()
    try:
        janelas = dominio.validar_grade([e.get("janelas") or [], [], [], [], [], [], []])[0]
    except HTTPException as erro:
        detalhe = str(erro.detail).split(":", 1)[-1].strip()
        raise HTTPException(status_code=400, detail=f"Exceção de {data}: {detalhe}")
    return {"data": data, "fechado": bool(e.get("fechado", True)),
            "motivo": (e.get("motivo") or "").strip() or None, "janelas": janelas}


# ---------------------------------------------------------------------------
# Saídas e ocupação
# ---------------------------------------------------------------------------

@router.get("/saidas")
async def listar_saidas(de: str | None = None, ate: str | None = None,
                        service_id: str | None = None,
                        user=Depends(require_tenant_member)):
    """As saídas da janela com capacidade, reservado e vaga.

    Mesma função que o bot e a IA consultam (`saidas.saidas_da_janela`): duas
    implementações fariam a tela mostrar vaga que o bot recusa. `de`/`ate` são
    dias do calendário da agência, não instantes: "segunda" é a segunda dela.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    config = await dominio.configuracao(db, tenant_id)
    tz = dominio.fuso_de(config)
    d_ini = dominio._dia(de) if de else dominio._agora().astimezone(tz).date()
    d_fim = dominio._dia(ate) if ate else d_ini + timedelta(days=6)
    if service_id:
        passeios = [await _get(db, "services", service_id, tenant_id, "Passeio")]
    else:
        passeios = await saidas.passeios_ativos(db, tenant_id)
    return {"fuso": config["fuso"],
            "dias": await saidas.saidas_da_janela(db, tenant_id, passeios=passeios,
                                                   de=d_ini, ate=d_fim, config=config)}


class SaidaRefIn(BaseModel):
    service_id: str
    inicio: str


class EquipeIn(SaidaRefIn):
    guia_id: str | None = None
    veiculo_id: str | None = None
    observacoes: str | None = Field(default=None, max_length=300)


class CancelarSaidaIn(SaidaRefIn):
    motivo: str = Field(min_length=1, max_length=200)
    avisar: bool = True


async def _linha_da_saida(db, tenant_id: str, service_id: str, inicio: str):
    """O passeio, a configuração e a linha da saída como a tela a vê.

    Passa pela mesma `saidas_da_janela` da grade: uma saída que não existe ali
    (hora fora da grade, sem ninguém reservado) não existe para operar também.
    """
    passeio = await _get(db, "services", service_id, tenant_id, "Passeio")
    config = await dominio.configuracao(db, tenant_id)
    alvo = dominio._iso(dominio._instante(inicio))
    d = dominio.local(alvo, dominio.fuso_de(config)).date()
    dia = (await saidas.saidas_da_janela(db, tenant_id, passeios=[passeio], de=d, ate=d,
                                         config=config))[0]
    linha = next((x for x in dia["saidas"] if x["inicio"] == alvo), None)
    if linha is None:
        raise HTTPException(status_code=404, detail="Saída não encontrada")
    return passeio, config, linha


@router.patch("/saidas/equipe")
async def escalar_equipe(body: EquipeIn, user=Depends(require_tenant_member)):
    """Guia e veículo de uma saída. É operação do dia a dia (quem atende escala
    quem vai), não configuração."""
    db = get_db()
    tenant_id = user["tenant_id"]
    passeio, config, linha = await _linha_da_saida(db, tenant_id, body.service_id, body.inicio)
    guia = await _get(db, "resources", body.guia_id, tenant_id, "Guia") if body.guia_id else None
    veiculo = (await _get(db, "resources", body.veiculo_id, tenant_id, "Veículo")
               if body.veiculo_id else None)
    await saidas.definir_equipe(db, tenant_id, passeio["id"], linha["inicio"],
                                guia=guia, veiculo=veiculo, obs=body.observacoes)
    return (await _linha_da_saida(db, tenant_id, body.service_id, body.inicio))[2]


@router.post("/saidas/cancelar")
async def cancelar_saida(body: CancelarSaidaIn, user=Depends(require_tenant_admin)):
    """Cancela a saída inteira e avisa cada grupo.

    Só admin: é a operação que mais mexe com turista de uma vez só (uma van
    cheia recebendo "cancelado" no mesmo minuto), e quem decide que a chuva
    cancela o passeio é quem responde pela agência.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    passeio, config, linha = await _linha_da_saida(db, tenant_id, body.service_id, body.inicio)
    canceladas = await saidas.cancelar_saida(db, tenant_id, passeio["id"], linha["inicio"],
                                             motivo=body.motivo, por=user["id"])
    avisadas = 0
    if body.avisar:
        # Um por um, na ordem de criação. `avisar` nunca levanta: um grupo sem
        # WhatsApp não pode deixar os seguintes sem aviso.
        for r in canceladas:
            if await agenda_worker.avisar(db, tenant_id, r, "cancelamento_saida", config):
                avisadas += 1
    return {"canceladas": len(canceladas), "avisadas": avisadas,
            "sem_aviso": len(canceladas) - avisadas,
            "saida": (await _linha_da_saida(db, tenant_id, body.service_id, body.inicio))[2]}


@router.post("/saidas/reabrir")
async def reabrir_saida(body: SaidaRefIn, user=Depends(require_tenant_admin)):
    db = get_db()
    tenant_id = user["tenant_id"]
    passeio, _, linha = await _linha_da_saida(db, tenant_id, body.service_id, body.inicio)
    await saidas.reabrir_saida(db, tenant_id, passeio["id"], linha["inicio"])
    return (await _linha_da_saida(db, tenant_id, body.service_id, body.inicio))[2]


@router.get("/saidas/embarque")
async def lista_de_embarque(service_id: str, inicio: str, user=Depends(require_tenant_member)):
    """Quem buscar, onde e a que horas: o papel que o guia leva para a van."""
    db = get_db()
    tenant_id = user["tenant_id"]
    passeio, config, linha = await _linha_da_saida(db, tenant_id, service_id, inicio)
    return await saidas.lista_embarque(db, tenant_id, passeio=passeio, linha=linha,
                                       config=config)


@router.get("/saidas/reservas")
async def reservas_da_saida(service_id: str, inicio: str,
                            user=Depends(require_tenant_member)):
    """Uma saída e todas as reservas dela, inclusive canceladas."""
    db = get_db()
    tenant_id = user["tenant_id"]
    passeio = await _get(db, "services", service_id, tenant_id, "Passeio")
    config = await dominio.configuracao(db, tenant_id)
    tz = dominio.fuso_de(config)
    alvo = dominio._iso(dominio._instante(inicio))
    d = dominio.local(alvo, tz).date()
    dia = (await saidas.saidas_da_janela(db, tenant_id, passeios=[passeio], de=d, ate=d,
                                         config=config))[0]
    linha = next((x for x in dia["saidas"] if x["inicio"] == alvo), None)
    if linha is None:
        raise HTTPException(status_code=404, detail="Saída não encontrada")
    reservas = await db.appointments.find(
        {"tenant_id": tenant_id, "service_id": service_id, "inicio": alvo}, {"_id": 0}
    ).sort([("created_at", 1), ("_id", 1)]).to_list(MAX_AGENDAMENTOS)
    return {"fuso": config["fuso"], "saida": linha, "reservas": reservas}


# ---------------------------------------------------------------------------
# Agendamentos
# ---------------------------------------------------------------------------

class AgendamentoIn(BaseModel):
    service_id: str
    inicio: str
    adultos: int = Field(default=1, ge=0, le=saidas.MAX_PESSOAS)
    criancas: int = Field(default=0, ge=0, le=saidas.MAX_PESSOAS)
    # Reservada pela própria agência nasce Confirmada; Solicitada é para quando
    # ela anota um pedido que ainda depende de algo (sinal, disponibilidade do
    # guia) e quer que o lugar fique seguro pelo prazo configurado.
    status: str = Field(default=dominio.STATUS_CONFIRMADO, pattern="^(agendado|confirmado)$")
    # Um contato existente, ou os dados para criar um na hora: quem atende o
    # telefone não vai abrir outra tela para cadastrar antes de marcar.
    contact_id: str | None = None
    contact_nome: str | None = None
    contact_numero: str | None = None
    observacoes: str | None = None
    # Encaixe: saída fora da grade ou dentro da antecedência mínima. Nunca passa
    # da capacidade: ver `saidas.reservar`.
    forcar: bool = False
    # Manda a confirmação pelo WhatsApp. Ligado por padrão: um agendamento que
    # o cliente não sabe que existe é uma falta esperando para acontecer.
    avisar: bool = True
    # Embarque: um ponto cadastrado ou um lugar em texto livre, e a hora da
    # busca (HH:MM do dia da saída). Ver `saidas.embarque`.
    ponto_embarque_id: str | None = None
    ponto_embarque_nome: str | None = None
    horario_busca: str | None = None
    embarque_obs: str | None = None


@router.get("/appointments")
async def listar_agendamentos(de: str | None = None, ate: str | None = None,
                              resource_id: str | None = None,
                              service_id: str | None = None,
                              contact_id: str | None = None,
                              status: str | None = None,
                              user=Depends(require_tenant_member)):
    """A agenda de uma janela. `de`/`ate` são instantes ISO, como no resto do
    sistema; sem eles, a semana a partir de agora."""
    db = get_db()
    agora = dominio._agora()
    inicio = dominio._instante(de, "Data inicial") if de else agora - timedelta(days=1)
    fim = dominio._instante(ate, "Data final") if ate else agora + timedelta(days=7)

    filtro = {"tenant_id": user["tenant_id"],
              "inicio": {"$gte": dominio._iso(inicio), "$lt": dominio._iso(fim)}}
    if resource_id:
        filtro["resource_id"] = resource_id
    if service_id:
        filtro["service_id"] = service_id
    if contact_id:
        filtro["contact_id"] = contact_id
    if status:
        filtro["status"] = {"$in": status.split(",")}

    itens = await db.appointments.find(filtro, {"_id": 0}) \
        .sort([("inicio", 1), ("_id", 1)]).to_list(MAX_AGENDAMENTOS)
    config = await dominio.configuracao(db, user["tenant_id"])
    return {"fuso": config["fuso"], "total": len(itens), "items": itens,
            "truncada": len(itens) >= MAX_AGENDAMENTOS}


@router.get("/summary")
async def resumo(de: str | None = None, ate: str | None = None,
                 user=Depends(require_tenant_member)):
    """Contadores por status na janela, numa agregação só.

    Cinco contagens seriam cinco idas ao banco para produzir cinco números que
    cabem numa linha da tela: mesmo raciocínio de `atividades.resumo`.
    """
    db = get_db()
    agora = dominio._agora()
    inicio = dominio._instante(de, "Data inicial") if de else agora
    fim = dominio._instante(ate, "Data final") if ate else agora + timedelta(days=7)
    cursor = db.appointments.aggregate([
        {"$match": {"tenant_id": user["tenant_id"],
                    "inicio": {"$gte": dominio._iso(inicio), "$lt": dominio._iso(fim)}}},
        {"$group": {"_id": "$status", "n": {"$sum": 1}}},
    ])
    por_status = {s: 0 for s in dominio.STATUS}
    async for linha in cursor:
        por_status[linha["_id"]] = linha["n"]
    por_status["total"] = sum(por_status[s] for s in dominio.STATUS)
    return por_status


@router.post("/appointments")
async def marcar(body: AgendamentoIn, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    servico = await _get(db, "services", body.service_id, tenant_id, "Passeio")
    if body.forcar and user.get("role") != "tenant_admin":
        # Encaixe cria uma saída que a grade não tem, e isso é configuração.
        raise HTTPException(status_code=403, detail="Só o administrador faz encaixe")
    contato = await _contato(db, tenant_id, body)
    config = await dominio.configuracao(db, tenant_id)

    doc = await saidas.reservar(
        db, tenant_id, passeio=servico, contato=contato, inicio=body.inicio,
        adultos=body.adultos, criancas=body.criancas, config=config, origem="manual",
        status=body.status, criado_por=user["id"], observacoes=body.observacoes,
        forcar=body.forcar, ponto_embarque_id=body.ponto_embarque_id,
        ponto_embarque_nome=body.ponto_embarque_nome, horario_busca=body.horario_busca,
        embarque_obs=body.embarque_obs)

    # Solicitada não recebe "confirmada": é exatamente o que ela ainda não é.
    if body.avisar and doc["status"] == dominio.STATUS_CONFIRMADO:
        # Não bloqueia a resposta com o resultado do envio: a marcação já está
        # feita, e uma conexão fora do ar não pode fazer a tela dizer que o
        # horário não foi reservado. `avisar` nunca levanta.
        doc["aviso_enviado"] = await agenda_worker.avisar(
            db, tenant_id, doc, "confirmacao", config)
    return doc


@router.post("/appointments/{appointment_id}/cancel")
async def cancelar_agendamento(appointment_id: str, motivo: str | None = None,
                               avisar: bool = True,
                               user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await saidas.cancelar(db, tenant_id, appointment_id,
                                por=user["id"], motivo=motivo)
    if avisar:
        await agenda_worker.avisar(db, tenant_id, doc, "cancelamento")
    return doc


@router.post("/appointments/{appointment_id}/lembrete")
async def reenviar_lembrete(appointment_id: str, user=Depends(require_tenant_member)):
    """Reenvia o lembrete a quem ainda não confirmou: o botão da tela "Hoje"
    para as confirmações de amanhã sem resposta.

    Passa pelo mesmo `agenda_worker.avisar` da varredura automática, que é quem
    decide entre texto livre e modelo aprovado conforme a janela de 24h, e
    marca a espera de resposta (`marcar_espera`) para o CONFIRMAR do paciente
    ser entendido. Só para horário futuro e ainda em aberto: lembrar de um
    atendimento que já passou ou foi cancelado confundiria o paciente.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    doc = await db.appointments.find_one(
        {"id": appointment_id, "tenant_id": tenant_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail="Agendamento não encontrado")
    # Só Confirmada: lembrar de uma solicitação que a agência ainda não aceitou
    # faria o turista achar que está tudo certo.
    if doc["status"] != dominio.STATUS_CONFIRMADO \
            or doc["inicio"] <= dominio._iso(dominio._agora()):
        raise HTTPException(status_code=409,
                            detail="Só dá para lembrar de uma reserva confirmada e futura")
    await db.appointments.update_one(
        {"id": appointment_id, "tenant_id": tenant_id},
        {"$set": {"lembrete_enviado_em": _now(), "updated_at": _now()}})
    saiu = await agenda_worker.avisar(db, tenant_id, doc, "lembrete", marcar_espera=True)
    if not saiu:
        raise HTTPException(
            status_code=409,
            detail="O lembrete não saiu. Confira se o WhatsApp está conectado e, fora "
                   "da janela de 24 horas, se há modelo de lembrete aprovado em Saídas.")
    return {"ok": True}


class StatusIn(BaseModel):
    status: str
    # Confirmar uma Solicitada avisa o turista. É o momento que ele espera
    # desde que o bot disse "nossa equipe vai confirmar".
    avisar: bool = True


class EmbarqueIn(BaseModel):
    ponto_embarque_id: str | None = None
    ponto_embarque_nome: str | None = None
    horario_busca: str | None = None
    embarque_obs: str | None = None


class PessoasIn(BaseModel):
    adultos: int = Field(ge=0, le=saidas.MAX_PESSOAS)
    criancas: int = Field(default=0, ge=0, le=saidas.MAX_PESSOAS)


@router.patch("/appointments/{appointment_id}/status")
async def mudar_status(appointment_id: str, body: StatusIn,
                       user=Depends(require_tenant_member)):
    """Confirma uma solicitação, ou marca Realizada / Não compareceu.

    Realizada e Não compareceu são registradas por uma pessoa, nunca deduzidas
    do relógio: só quem estava no embarque sabe quem foi.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    antes = await db.appointments.find_one({"id": appointment_id, "tenant_id": tenant_id},
                                           {"_id": 0, "status": 1})
    doc = await saidas.mudar_status(db, tenant_id, appointment_id, body.status)
    if (body.avisar and antes and antes["status"] == dominio.STATUS_AGENDADO
            and doc["status"] == dominio.STATUS_CONFIRMADO):
        doc["aviso_enviado"] = await agenda_worker.avisar(db, tenant_id, doc, "confirmacao")
    return doc


@router.patch("/appointments/{appointment_id}/embarque")
async def mudar_embarque(appointment_id: str, body: EmbarqueIn,
                         user=Depends(require_tenant_member)):
    """Onde e a que horas buscar o grupo. Tudo nulo limpa (volta a "a combinar")."""
    db = get_db()
    return await saidas.alterar_embarque(
        db, user["tenant_id"], appointment_id, ponto_id=body.ponto_embarque_id,
        ponto_nome=body.ponto_embarque_nome, horario_busca=body.horario_busca,
        obs=body.embarque_obs)


@router.patch("/appointments/{appointment_id}/pessoas")
async def mudar_pessoas(appointment_id: str, body: PessoasIn,
                        user=Depends(require_tenant_member)):
    """Muda o tamanho do grupo; aumentar passa pela capacidade da saída."""
    db = get_db()
    return await saidas.alterar_pessoas(db, user["tenant_id"], appointment_id,
                                        adultos=body.adultos, criancas=body.criancas)


# ---------------------------------------------------------------------------
# Auxiliares
# ---------------------------------------------------------------------------

async def _nome_livre(db, colecao: str, tenant_id: str, nome: str, rotulo: str,
                      exceto: str = None):
    """Recusa nome repetido dentro da empresa.

    É a trava que realmente segura contra duplicata. A tela também impede clique
    repetido, mas ela não cobre duas abas abertas, um F5 no meio do envio nem um
    retry de rede: nesses casos chegam duas requisições idênticas e sem esta
    checagem nascem dois cadastros. Foi assim que um duplo clique em "Salvar"
    criou dois profissionais.

    Comparação sem caixa: "Ana" e "ana" são a mesma pessoa, e deixar as duas
    passarem devolve o problema pela porta dos fundos. O nome vai escapado
    porque entra numa regex do Mongo, e um "(" digitado derrubaria a consulta.
    """
    igual = await db[colecao].find_one(
        {"tenant_id": tenant_id,
         "nome": {"$regex": f"^{re.escape(nome)}$", "$options": "i"}},
        {"_id": 0, "id": 1},
    )
    if igual and igual["id"] != exceto:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe {rotulo} com o nome \"{nome}\". "
                   "Use outro nome ou edite o que já está cadastrado.")


async def _get(db, colecao: str, item_id: str, tenant_id: str, rotulo: str) -> dict:
    doc = await db[colecao].find_one({"id": item_id, "tenant_id": tenant_id}, {"_id": 0})
    if not doc:
        raise HTTPException(status_code=404, detail=f"{rotulo} não encontrado")
    return doc


async def _valida_recursos(db, tenant_id: str, ids: list):
    """Um id de outra empresa colado no corpo amarraria o serviço a um
    profissional que a empresa não deveria nem saber que existe."""
    for rid in ids or []:
        if not await db.resources.find_one({"id": rid, "tenant_id": tenant_id},
                                           {"_id": 0, "id": 1}):
            raise HTTPException(status_code=400, detail="Guia inválido")


async def _valida_usuario(db, tenant_id: str, user_id: str | None):
    if user_id and not await db.users.find_one({"id": user_id, "tenant_id": tenant_id},
                                               {"_id": 0, "id": 1}):
        raise HTTPException(status_code=400, detail="Usuário inválido")


async def _sem_futuro(db, tenant_id: str, filtro: dict, prefixo: str):
    """Recusa a exclusão quando ainda há horário marcado à frente.

    Mesma decisão da etapa de funil com negócios: apagar em silêncio deixaria os
    agendamentos apontando para um serviço ou profissional inexistente, e eles
    sumiriam da tela sem nunca terem sido atendidos nem cancelados. Quem quer
    parar de oferecer desativa: o passado continua legível e o futuro continua
    marcado.
    """
    n = await db.appointments.count_documents({
        "tenant_id": tenant_id, **filtro,
        "status": {"$in": [dominio.STATUS_AGENDADO, dominio.STATUS_CONFIRMADO]},
        "inicio": {"$gte": dominio._iso(dominio._agora())},
    })
    if n:
        raise HTTPException(
            status_code=409,
            detail=f"{prefixo} {n} horário(s) marcado(s) à frente. "
                   "Cancele ou atenda antes de excluir, ou desative, "
                   "que preserva o histórico.")


async def _contato(db, tenant_id: str, body: AgendamentoIn) -> dict:
    """O contato do agendamento: o escolhido, o já cadastrado pelo número, ou um
    novo. Nunca dois registros para o mesmo número: ele é a identidade do
    contato em todo o sistema."""
    if body.contact_id:
        return await _get(db, "contacts", body.contact_id, tenant_id, "Cliente")
    if not (body.contact_numero or "").strip():
        raise HTTPException(status_code=400,
                            detail="Escolha um cliente ou informe nome e WhatsApp")
    numero, erro = contatos_csv.normaliza_numero(body.contact_numero)
    if erro:
        raise HTTPException(status_code=400, detail=f"WhatsApp inválido: {erro}")

    existente = await db.contacts.find_one(
        {"tenant_id": tenant_id, "numero_whatsapp": numero}, {"_id": 0})
    if existente:
        return existente
    contato = {
        "id": str(uuid.uuid4()), "tenant_id": tenant_id,
        "numero_whatsapp": numero,
        "nome": (body.contact_nome or "").strip() or numero,
        "origem": "agendamento manual",
        "created_at": _now(),
    }
    await db.contacts.insert_one(dict(contato))
    contato.pop("_id", None)
    return contato
