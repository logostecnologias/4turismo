import csv
import io
import json
import re
import uuid
from datetime import datetime, timezone
from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from fastapi.responses import StreamingResponse
from pydantic import BaseModel, Field
from pymongo import InsertOne, UpdateOne

from db import get_db
from auth import accessible_connection_ids, require_tenant_admin, require_tenant_member
from flow_runtime import JanelaFechada, send_human_message as _send_human
import audit
import contatos
import contatos_csv
import outbound
import whatsapp_client as whatsapp

router = APIRouter(prefix="/api", tags=["contacts"])


def _now():
    return datetime.now(timezone.utc).isoformat()


class ContactIn(BaseModel):
    bot_id: str | None = None
    numero_whatsapp: str
    nome: str


class ContactPatch(BaseModel):
    """Só o que a ficha edita. `numero_whatsapp` fica de fora de propósito: ele
    é a chave que liga contato, conversas e histórico, e trocá-lo faria todo o
    histórico apontar para outra pessoa."""
    nome: str | None = None
    email: str | None = None
    empresa: str | None = None
    cargo: str | None = None
    origem: str | None = None
    responsavel_id: str | None = None
    tags: list[str] | None = None
    opt_out: bool | None = None
    # {id_da_definicao: valor} — validado contra `contact_field_defs`.
    campos: dict | None = None


class FieldDefIn(BaseModel):
    nome: str
    tipo: str = Field(default="texto", pattern="^(texto|numero|data|selecao)$")
    opcoes: list[str] = []
    ordem: int = 0


class NotaIn(BaseModel):
    texto: str


class TemplateEnvioIn(BaseModel):
    """Modelo aprovado, para quem está fora da janela de 24h.

    `texto` do envio continua sendo o que vai para o histórico: o corpo do
    modelo já preenchido, para o atendente ver o que o contato leu.
    """
    nome: str
    idioma: str = "pt_BR"
    valores: list[str] = Field(default_factory=list)


class EnvioIn(BaseModel):
    texto: str
    whatsapp_connection_id: str | None = None
    template: TemplateEnvioIn | None = None


TIPOS_COM_OPCOES = ("selecao",)
EMAIL_RE = re.compile(r"^[^@\s]+@[^@\s]+\.[^@\s]+$")


def _limpa_tags(tags: list[str]) -> list[str]:
    """Etiquetas normalizadas: sem espaço nas pontas, sem vazias, sem repetida.
    O filtro por etiqueta é por igualdade exata, então "VIP" e "vip " seriam
    dois segmentos para quem os digitou pensando no mesmo."""
    vistas, saida = set(), []
    for t in tags:
        limpa = (t or "").strip()
        chave = limpa.lower()
        if limpa and chave not in vistas:
            vistas.add(chave)
            saida.append(limpa)
    return saida[:20]


def _para_numero(valor) -> float:
    """Aceita "1250.5", "1250,50" e "1.250,50".

    O campo é digitado numa tela pt-BR, onde o ponto separa milhar e a vírgula
    separa decimal — mas o `<input type="number">` do navegador entrega o valor
    com ponto decimal. Daí a regra: com os dois presentes, o ponto é milhar e a
    vírgula é decimal; só com vírgula, ela é o decimal; só com ponto, é decimal
    (é o que o input numérico manda). Um `replace(",", ".")` cru transformava
    "1.250,50" em "1.250.50" e recusava um valor perfeitamente válido.
    """
    texto = str(valor).strip().replace(" ", "").replace(" ", "")
    if "," in texto and "." in texto:
        texto = texto.replace(".", "").replace(",", ".")
    elif "," in texto:
        texto = texto.replace(",", ".")
    return float(texto)


async def _valida_campos(db, tenant_id: str, campos: dict) -> dict:
    """Confere os campos personalizados contra as definições da empresa.

    Valor de definição que não existe mais é **descartado**, não recusado:
    apagar um campo personalizado não pode impedir que a ficha volte a ser
    salva. Valor com tipo errado é recusado, aí sim — guardar "abc" num campo
    numérico quebraria depois qualquer filtro que dependa dele.
    """
    defs = {
        d["id"]: d
        async for d in db.contact_field_defs.find({"tenant_id": tenant_id}, {"_id": 0})
    }
    limpo: dict = {}
    for chave, valor in (campos or {}).items():
        definicao = defs.get(chave)
        if not definicao:
            continue
        if valor is None or valor == "":
            continue
        tipo, nome = definicao.get("tipo", "texto"), definicao.get("nome", chave)
        if tipo == "numero":
            try:
                limpo[chave] = _para_numero(valor)
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{nome}: informe um número")
        elif tipo == "data":
            try:
                datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
            except ValueError:
                raise HTTPException(status_code=400, detail=f"{nome}: data inválida")
            limpo[chave] = str(valor)
        elif tipo == "selecao":
            if str(valor) not in (definicao.get("opcoes") or []):
                raise HTTPException(status_code=400, detail=f"{nome}: opção inválida")
            limpo[chave] = str(valor)
        else:
            limpo[chave] = str(valor)[:1000]
    return limpo


# ---------- Lista ----------


@router.get("/contacts")
async def list_contacts(
    q: str | None = None,
    tag: str | None = None,
    responsavel_id: str | None = None,
    skip: int = 0,
    limit: int = 100,
    user=Depends(require_tenant_member),
):
    """Base de contatos, filtrável e paginada.

    Antes devolvia 500 documentos crus sem filtro nenhum. Além do peso, era
    inútil para o que a tela precisa: achar um contato, ver quem está numa
    etiqueta antes de disparar, e listar a carteira de um responsável.
    """
    db = get_db()
    filtro: dict = {"tenant_id": user["tenant_id"]}
    if tag:
        filtro["tags"] = tag
    if responsavel_id:
        filtro["responsavel_id"] = responsavel_id
    if q:
        # `re.escape` porque a busca vai para dentro de uma regex do Mongo: um
        # "(" digitado no campo derrubaria a consulta com erro de sintaxe.
        termo = re.escape(q.strip())
        filtro["$or"] = [
            {"nome": {"$regex": termo, "$options": "i"}},
            {"numero_whatsapp": {"$regex": termo}},
            {"email": {"$regex": termo, "$options": "i"}},
            {"empresa": {"$regex": termo, "$options": "i"}},
        ]
    limit = max(1, min(limit, 500))
    items = await db.contacts.find(filtro, {"_id": 0}).sort("created_at", -1) \
        .skip(max(0, skip)).limit(limit).to_list(limit)
    return {"total": await db.contacts.count_documents(filtro), "items": items}


@router.get("/contacts/tags")
async def list_contact_tags(user=Depends(require_tenant_member)):
    """Etiquetas em uso, para o filtro da lista de contatos."""
    db = get_db()
    tags = await db.contacts.distinct("tags", {"tenant_id": user["tenant_id"]})
    return sorted([t for t in tags if t], key=str.lower)


# ---------- Campos personalizados ----------
#
# Declarados antes de /contacts/{contact_id} de propósito: numa rota com
# parâmetro no mesmo nível, "fields" seria capturado como se fosse um id.


@router.get("/contacts/fields")
async def list_field_defs(user=Depends(require_tenant_member)):
    db = get_db()
    return await db.contact_field_defs.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("ordem", 1).to_list(100)


@router.post("/contacts/fields")
async def create_field_def(body: FieldDefIn, user=Depends(require_tenant_admin)):
    db = get_db()
    if body.tipo in TIPOS_COM_OPCOES and not body.opcoes:
        raise HTTPException(status_code=400, detail="Campo de seleção precisa de ao menos uma opção")
    doc = {
        "id": str(uuid.uuid4()),
        "tenant_id": user["tenant_id"],
        **body.model_dump(),
        "created_at": _now(),
    }
    await db.contact_field_defs.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


@router.patch("/contacts/fields/{field_id}")
async def update_field_def(field_id: str, body: FieldDefIn, user=Depends(require_tenant_admin)):
    db = get_db()
    if body.tipo in TIPOS_COM_OPCOES and not body.opcoes:
        raise HTTPException(status_code=400, detail="Campo de seleção precisa de ao menos uma opção")
    res = await db.contact_field_defs.update_one(
        {"id": field_id, "tenant_id": user["tenant_id"]}, {"$set": body.model_dump()}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Campo não encontrado")
    return await db.contact_field_defs.find_one(
        {"id": field_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    )


@router.delete("/contacts/fields/{field_id}")
async def delete_field_def(field_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    # `field_id` vira chave de documento no $unset abaixo (`campos.<id>`). Ele
    # já é indiretamente contido — o delete_one só casa um id que geramos como
    # uuid4 —, mas validar o formato aqui é a barreira explícita (ZZ-20): um
    # `field_id` com ponto jamais alcança um subcaminho dentro de `campos`.
    try:
        uuid.UUID(field_id)
    except ValueError:
        raise HTTPException(status_code=404, detail="Campo não encontrado")
    res = await db.contact_field_defs.delete_one(
        {"id": field_id, "tenant_id": user["tenant_id"]}
    )
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Campo não encontrado")
    # O valor sai das fichas junto. Deixá-lo em `contacts.campos` guardaria
    # dado que nenhuma tela mostra e nenhum filtro alcança — e ele reapareceria
    # se alguém recriasse um campo com o mesmo id.
    await db.contacts.update_many(
        {"tenant_id": user["tenant_id"], f"campos.{field_id}": {"$exists": True}},
        {"$unset": {f"campos.{field_id}": ""}},
    )
    return {"ok": True}


# ---------- Importação e exportação ----------
#
# Também antes de /contacts/{contact_id}: "import" e "export" seriam capturados
# como id de contato.


async def _le_upload(file: UploadFile) -> bytes:
    """Lê o arquivo aplicando o teto durante a leitura.

    Com o teto só no fim, um arquivo de 500MB já teria passado inteiro pela
    memória antes de ser recusado.
    """
    partes, total = [], 0
    while True:
        bloco = await file.read(64 * 1024)
        if not bloco:
            break
        total += len(bloco)
        if total > contatos_csv.MAX_BYTES:
            raise HTTPException(
                status_code=400,
                detail=f"Arquivo acima de {contatos_csv.MAX_BYTES // (1024 * 1024)}MB",
            )
        partes.append(bloco)
    if not total:
        raise HTTPException(status_code=400, detail="Arquivo vazio")
    return b"".join(partes)


@router.post("/contacts/import")
async def import_contacts(
    file: UploadFile = File(...),
    dry_run: bool = Form(True),
    mapeamento: str = Form(""),
    duplicados: str = Form("ignorar"),
    ddi: str = Form(contatos_csv.DDI_PADRAO),
    user=Depends(require_tenant_admin),
):
    """Importa contatos de um CSV.

    `dry_run` (o padrão) não grava nada: devolve o mapeamento sugerido, uma
    amostra e os números do que aconteceria. É a prévia que a tela mostra antes
    de confirmar — importar às cegas uma lista de milhares e só depois descobrir
    que a coluna do telefone estava errada é o erro que este passo evita.

    O arquivo sobe duas vezes (prévia e confirmação) de propósito: guardar o
    conteúdo entre as duas chamadas criaria estado temporário para expirar e
    limpar, e um CSV é texto — reenviá-lo é barato.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    if duplicados not in ("ignorar", "atualizar"):
        raise HTTPException(status_code=400, detail="Ação para duplicados inválida")

    dados = await _le_upload(file)
    try:
        colunas, linhas, delimitador = contatos_csv.le_arquivo(dados)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    if not linhas:
        raise HTTPException(status_code=400, detail="O arquivo não tem nenhuma linha de dados")
    if len(linhas) > contatos_csv.MAX_LINHAS:
        raise HTTPException(
            status_code=400,
            detail=(f"O arquivo tem mais de {contatos_csv.MAX_LINHAS} linhas. "
                    "Divida em partes menores."),
        )

    defs = await db.contact_field_defs.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(100)
    if mapeamento:
        try:
            mapa = json.loads(mapeamento)
        except json.JSONDecodeError:
            raise HTTPException(status_code=400, detail="Mapeamento inválido")
        if not isinstance(mapa, dict):
            raise HTTPException(status_code=400, detail="Mapeamento inválido")
    else:
        mapa = contatos_csv.sugere_mapeamento(colunas, defs)

    if "numero_whatsapp" not in mapa.values():
        raise HTTPException(
            status_code=400,
            detail="Indique qual coluna tem o número de WhatsApp — sem ela não há cliente.",
        )

    # Responsável vem por e-mail na planilha (é o que uma exportação de outro
    # sistema traz); aqui é resolvido para o id do usuário desta empresa.
    usuarios = {
        (u.get("email") or "").lower(): u["id"]
        async for u in db.users.find({"tenant_id": tenant_id}, {"_id": 0, "id": 1, "email": 1})
    }

    preparadas: list[tuple[int, dict]] = []
    erros: list[dict] = []
    vistos: set[str] = set()
    for i, linha in enumerate(linhas):
        # +2: a linha 1 do arquivo é o cabeçalho, e quem confere olha o número
        # que o editor de planilha mostra.
        numero_linha = i + 2
        dados_linha, erro = contatos_csv.linha_para_contato(linha, mapa, ddi)
        if erro:
            erros.append({"linha": numero_linha, "erro": erro})
            continue
        if dados_linha["numero_whatsapp"] in vistos:
            erros.append({"linha": numero_linha, "erro": "número repetido dentro do arquivo"})
            continue
        if dados_linha.get("campos"):
            try:
                dados_linha["campos"] = await _valida_campos(db, tenant_id, dados_linha["campos"])
            except HTTPException as e:
                # Erro de campo personalizado derruba a linha, não a importação.
                erros.append({"linha": numero_linha, "erro": str(e.detail)})
                continue
        email_resp = (dados_linha.pop("responsavel_email", "") or "").strip().lower()
        if email_resp:
            if email_resp in usuarios:
                dados_linha["responsavel_id"] = usuarios[email_resp]
            else:
                erros.append({"linha": numero_linha,
                              "erro": f"responsável '{email_resp}' não é usuário desta empresa"})
                continue
        vistos.add(dados_linha["numero_whatsapp"])
        preparadas.append((numero_linha, dados_linha))

    # Quem já existe, descoberto em lotes: um find_one por linha seriam 20 mil
    # idas e voltas por um túnel SSH.
    existentes: dict[str, dict] = {}
    numeros = [d["numero_whatsapp"] for _, d in preparadas]
    for i in range(0, len(numeros), 1000):
        fatia = numeros[i:i + 1000]
        async for c in db.contacts.find(
            {"tenant_id": tenant_id, "numero_whatsapp": {"$in": fatia}},
            {"_id": 0, "id": 1, "numero_whatsapp": 1, "nome": 1, "tags": 1},
        ):
            existentes[c["numero_whatsapp"]] = c

    novos = [d for _, d in preparadas if d["numero_whatsapp"] not in existentes]
    duplicadas = [d for _, d in preparadas if d["numero_whatsapp"] in existentes]

    resumo = {
        "colunas": colunas,
        "delimitador": delimitador,
        "mapeamento": mapa,
        "campos_disponiveis": [{"id": d["id"], "nome": d["nome"]} for d in defs],
        "linhas": len(linhas),
        "novos": len(novos),
        "duplicados": len(duplicadas),
        "erros": len(erros),
        "erros_detalhe": erros[:contatos_csv.MAX_ERROS_DETALHADOS],
        "erros_omitidos": max(0, len(erros) - contatos_csv.MAX_ERROS_DETALHADOS),
        "amostra": [d for _, d in preparadas[:5]],
        "acao_duplicados": duplicados,
        "aplicado": False,
    }
    if dry_run:
        return resumo

    agora = _now()
    operacoes = []
    for dados_linha in novos:
        operacoes.append(InsertOne({
            "id": str(uuid.uuid4()),
            "tenant_id": tenant_id,
            "bot_id": None,
            "numero_whatsapp": dados_linha["numero_whatsapp"],
            "nome": dados_linha.get("nome"),
            "email": dados_linha.get("email"),
            "empresa": dados_linha.get("empresa"),
            "cargo": dados_linha.get("cargo"),
            "origem": dados_linha.get("origem") or "importação CSV",
            "responsavel_id": dados_linha.get("responsavel_id"),
            "tags": dados_linha.get("tags") or [],
            "campos": dados_linha.get("campos") or {},
            "opt_out": False,
            "created_at": agora,
            "updated_at": agora,
        }))

    atualizados = 0
    if duplicados == "atualizar":
        for dados_linha in duplicadas:
            atual = existentes[dados_linha["numero_whatsapp"]]
            campos: dict = {"updated_at": agora}
            for chave in ("nome", "email", "empresa", "cargo", "origem", "responsavel_id"):
                if dados_linha.get(chave):
                    campos[chave] = dados_linha[chave]
            if dados_linha.get("tags"):
                # União, não substituição: a etiqueta posta na tela não pode ser
                # apagada por uma planilha que simplesmente não a conhece.
                juntas = list(atual.get("tags") or [])
                for t in dados_linha["tags"]:
                    if t.lower() not in {x.lower() for x in juntas}:
                        juntas.append(t)
                campos["tags"] = juntas[:20]
            for cid, valor in (dados_linha.get("campos") or {}).items():
                campos[f"campos.{cid}"] = valor
            operacoes.append(UpdateOne({"id": atual["id"], "tenant_id": tenant_id},
                                       {"$set": campos}))
            atualizados += 1

    for i in range(0, len(operacoes), 500):
        # `ordered=False` para uma linha problemática não abortar o lote inteiro.
        await db.contacts.bulk_write(operacoes[i:i + 500], ordered=False)

    resumo.update({
        "aplicado": True,
        "criados": len(novos),
        "atualizados": atualizados,
        "ignorados": len(duplicadas) - atualizados,
    })
    return resumo


# Caracteres que, no início de uma célula, fazem o Excel/Sheets tratarem o texto
# como fórmula (ZZ-13). O `nome` do contato vem do PushName do WhatsApp — ou
# seja, do próprio contato, que escolhe o que quiser: um nome começando com "="
# vira fórmula quando o admin abre a exportação. É a única falha do sistema que
# um estranho dispara sem conta nenhuma.
_CSV_GATILHOS = ("=", "+", "-", "@", "\t", "\r")


def _csv_safe(valor) -> str:
    """Prefixa com aspa simples toda célula que começaria com um gatilho de
    fórmula. A aspa neutraliza a interpretação sem alterar o que se lê."""
    texto = "" if valor is None else str(valor)
    if texto and texto[0] in _CSV_GATILHOS:
        return "'" + texto
    return texto


@router.get("/contacts/export")
async def export_contacts(
    q: str | None = None,
    tag: str | None = None,
    responsavel_id: str | None = None,
    sep: str = ";",
    user=Depends(require_tenant_admin),
):
    """Exporta a base filtrada em CSV, com os campos personalizados como colunas.

    Escrito em fluxo (`StreamingResponse`), linha a linha do cursor: montar o
    arquivo inteiro na memória antes de responder é justamente o que não se faz
    neste projeto, e uma base de 20 mil contatos com campos personalizados não
    é pequena.

    `;` por padrão e BOM no início porque o destino real é o Excel em português:
    com `,` ele joga tudo numa coluna, e sem BOM os acentos viram símbolos.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    if sep not in (";", ",", "\\t"):
        raise HTTPException(status_code=400, detail="Separador inválido")
    if sep == "\\t":
        sep = "\t"

    filtro: dict = {"tenant_id": tenant_id}
    if tag:
        filtro["tags"] = tag
    if responsavel_id:
        filtro["responsavel_id"] = responsavel_id
    if q:
        termo = re.escape(q.strip())
        filtro["$or"] = [
            {"nome": {"$regex": termo, "$options": "i"}},
            {"numero_whatsapp": {"$regex": termo}},
            {"email": {"$regex": termo, "$options": "i"}},
            {"empresa": {"$regex": termo, "$options": "i"}},
        ]

    defs = await db.contact_field_defs.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).sort("ordem", 1).to_list(100)
    emails = {
        u["id"]: u.get("email")
        async for u in db.users.find({"tenant_id": tenant_id}, {"_id": 0, "id": 1, "email": 1})
    }
    cabecalho = contatos_csv.COLUNAS_EXPORT + [d["nome"] for d in defs]

    async def linhas():
        buffer = io.StringIO()
        escritor = csv.writer(buffer, delimiter=sep, lineterminator="\r\n",
                              quoting=csv.QUOTE_MINIMAL)

        def despeja() -> str:
            texto = buffer.getvalue()
            buffer.seek(0)
            buffer.truncate(0)
            return texto

        # BOM antes de tudo: é ele que faz o Excel ler o arquivo como UTF-8.
        yield "﻿"
        escritor.writerow(cabecalho)
        yield despeja()

        cursor = db.contacts.find(filtro, {"_id": 0}).sort("created_at", -1)
        async for c in cursor:
            valores = c.get("campos") or {}
            # Toda célula passa por _csv_safe — inclusive os campos
            # personalizados, que podem carregar valor vindo do próprio contato.
            escritor.writerow([_csv_safe(v) for v in ([
                c.get("nome") or "",
                c.get("numero_whatsapp") or "",
                c.get("email") or "",
                c.get("empresa") or "",
                c.get("cargo") or "",
                c.get("origem") or "",
                ", ".join(c.get("tags") or []),
                emails.get(c.get("responsavel_id")) or "",
                "sim" if c.get("opt_out") else "nao",
                (c.get("created_at") or "")[:10],
            ] + [valores.get(d["id"], "") for d in defs])])
            yield despeja()

    # Exportar a base é o caminho mais curto para ela sair da empresa — vai para
    # a auditoria com o filtro usado (ZZ-21). Registrado no início: se o cliente
    # cancelar o download no meio, o pedido de exportação ainda aconteceu.
    await audit.registrar(
        db, ator=user, acao="contact.export", tenant_id=tenant_id,
        meta={"filtro": {"q": q, "tag": tag, "responsavel_id": responsavel_id}},
    )
    return StreamingResponse(
        linhas(),
        media_type="text/csv; charset=utf-8",
        headers={"Content-Disposition": 'attachment; filename="contatos.csv"'},
    )


# ---------- Duplicados ----------
#
# Antes de /contacts/{contact_id}: "duplicates" seria capturado como um id.


@router.get("/contacts/duplicates")
async def list_duplicates(user=Depends(require_tenant_admin)):
    """Fichas que parecem ser a mesma pessoa, para revisão antes de mesclar."""
    db = get_db()
    return await contatos.achar_duplicados(db, user["tenant_id"])


# ---------- Ficha ----------


@router.get("/contacts/{contact_id}")
async def contact_detail(contact_id: str, user=Depends(require_tenant_member)):
    """A ficha inteira numa chamada: dados, campos, notas e conversas.

    Numa só porque o painel abre tudo de uma vez, e o banco está atrás de um
    túnel SSH — seis requests para montar uma tela custariam seis idas e voltas
    onde cabem seis consultas na mesma.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    contact = await db.contacts.find_one({"id": contact_id, "tenant_id": tenant_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    responsavel = None
    if contact.get("responsavel_id"):
        responsavel = await db.users.find_one(
            {"id": contact["responsavel_id"], "tenant_id": tenant_id},
            # `name`, não `nome`: a coleção de usuários usa o campo em inglês.
            {"_id": 0, "id": 1, "name": 1, "email": 1},
        )

    # Definições + valor já casados, na ordem configurada: a tela não deve
    # precisar cruzar as duas listas para descobrir o rótulo de cada valor.
    defs = await db.contact_field_defs.find(
        {"tenant_id": tenant_id}, {"_id": 0}
    ).sort("ordem", 1).to_list(100)
    valores = contact.get("campos") or {}
    campos = [{**d, "valor": valores.get(d["id"])} for d in defs]

    # Desempate por `_id`, mesma razão do histórico de mensagens: o relógio do
    # Windows anda de ~15ms em ~15ms, então duas anotações escritas em seguida
    # nascem com `created_at` idêntico — e só com o timestamp a ordem entre
    # elas é indefinida, fazendo a nota mais nova aparecer embaixo da anterior.
    notas = await db.contact_notes.find(
        {"tenant_id": tenant_id, "contact_id": contact_id}, {"_id": 0}
    ).sort([("created_at", -1), ("_id", -1)]).to_list(200)

    conversas = await db.conversation_sessions.find(
        {"tenant_id": tenant_id, "contact_id": contact_id},
        {"_id": 0, "id": 1, "status": 1, "status_atendimento": 1, "origem": 1,
         "whatsapp_connection_id": 1, "last_message": 1,
         "created_at": 1, "updated_at": 1},
    ).sort("updated_at", -1).to_list(100)

    mensagens = 0
    if conversas:
        mensagens = await db.messages_log.count_documents(
            {"session_id": {"$in": [c["id"] for c in conversas]}}
        )

    return {
        "contact": contact,
        "responsavel": responsavel,
        "campos": campos,
        "notas": notas,
        "conversas": conversas,
        "resumo": {
            "conversas": len(conversas),
            "mensagens": mensagens,
            "primeiro_contato": contact.get("created_at"),
            "ultimo_contato": conversas[0]["updated_at"] if conversas else None,
        },
    }


@router.patch("/contacts/{contact_id}")
async def update_contact(contact_id: str, body: ContactPatch, user=Depends(require_tenant_member)):
    db = get_db()
    tenant_id = user["tenant_id"]
    campos: dict = {}

    if body.nome is not None:
        nome = body.nome.strip()
        if not nome:
            raise HTTPException(status_code=400, detail="Nome não pode ficar vazio")
        campos["nome"] = nome
    if body.email is not None:
        email = body.email.strip()
        if email and not EMAIL_RE.match(email):
            raise HTTPException(status_code=400, detail="E-mail inválido")
        campos["email"] = email or None
    for atributo in ("empresa", "cargo", "origem"):
        valor = getattr(body, atributo)
        if valor is not None:
            campos[atributo] = valor.strip() or None
    if body.responsavel_id is not None:
        if body.responsavel_id:
            # Sem esta checagem, um id de usuário de outra empresa colado no
            # corpo apareceria como responsável na ficha.
            dono = await db.users.find_one(
                {"id": body.responsavel_id, "tenant_id": tenant_id}, {"_id": 0, "id": 1}
            )
            if not dono:
                raise HTTPException(status_code=400, detail="Responsável inválido")
        campos["responsavel_id"] = body.responsavel_id or None
    if body.tags is not None:
        campos["tags"] = _limpa_tags(body.tags)
    if body.opt_out is not None:
        campos["opt_out"] = body.opt_out
        # Guarda quando o contato saiu: numa fiscalização de LGPD, "não recebe"
        # sem data não prova nada.
        campos["opt_out_em"] = _now() if body.opt_out else None
    if body.campos is not None:
        campos["campos"] = await _valida_campos(db, tenant_id, body.campos)

    if not campos:
        raise HTTPException(status_code=400, detail="Nada para atualizar")
    campos["updated_at"] = _now()
    res = await db.contacts.update_one(
        {"id": contact_id, "tenant_id": tenant_id}, {"$set": campos}
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    return await db.contacts.find_one({"id": contact_id, "tenant_id": tenant_id}, {"_id": 0})


@router.post("/contacts")
async def create_contact(body: ContactIn, user=Depends(require_tenant_member)):
    db = get_db()
    numero = "".join(ch for ch in body.numero_whatsapp if ch.isdigit())
    if not numero:
        raise HTTPException(status_code=400, detail="Número de WhatsApp inválido")
    # O número é a identidade do contato em todo o sistema (conversa e
    # histórico). Deixar entrar um duplicado partiria a ficha em duas.
    existente = await db.contacts.find_one(
        {"tenant_id": user["tenant_id"], "numero_whatsapp": numero},
        {"_id": 0, "id": 1, "nome": 1},
    )
    if existente:
        raise HTTPException(
            status_code=409,
            detail=f"Já existe um cliente com este número: {existente.get('nome')}",
        )
    contact = {
        "id": str(uuid.uuid4()),
        "tenant_id": user["tenant_id"],
        "bot_id": body.bot_id,
        "numero_whatsapp": numero,
        "nome": body.nome.strip() or numero,
        "email": None,
        "empresa": None,
        "cargo": None,
        "origem": "cadastro manual",
        "responsavel_id": None,
        "tags": [],
        "campos": {},
        "opt_out": False,
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.contacts.insert_one(dict(contact))
    contact.pop("_id", None)
    return contact


@router.delete("/contacts/{contact_id}")
async def delete_contact(contact_id: str, modo: str = "anonimizar",
                         user=Depends(require_tenant_admin)):
    """Remove o contato. `anonimizar` (padrão) ou `apagar`.

    **anonimizar** atende ao direito de exclusão do titular sem falsificar o
    passado: nome, número, e-mail, etiquetas, campos e notas somem, o texto das
    mensagens é redigido e a mídia sai do disco — mas as conversas e os negócios
    continuam existindo. Um negócio de R$ 40 mil fechado em julho tem de
    continuar somando em julho; apagá-lo mudaria retroativamente um relatório
    que a empresa já entregou.

    **apagar** é para contato de teste ou importação errada: leva conversas,
    mensagens, negócios e histórico. A resposta diz exatamente o que foi.

    Só admin: é a operação mais destrutiva da base.
    """
    if modo not in ("anonimizar", "apagar"):
        raise HTTPException(status_code=400, detail="Modo inválido")
    db = get_db()
    tenant_id = user["tenant_id"]
    contact = await db.contacts.find_one({"id": contact_id, "tenant_id": tenant_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    if modo == "apagar":
        resultado = await contatos.apagar(db, tenant_id, contact)
    else:
        resultado = await contatos.anonimizar(db, tenant_id, contact)
    await audit.registrar(
        db, ator=user, acao=f"contact.{modo}", tenant_id=tenant_id,
        alvo=contact_id, meta={"nome": contact.get("nome")},
    )
    return resultado


class MesclarIn(BaseModel):
    # A ficha que será absorvida e apagada. A que fica é a da URL.
    secundario_id: str


@router.post("/contacts/{contact_id}/merge")
async def merge_contacts(contact_id: str, body: MesclarIn, user=Depends(require_tenant_admin)):
    """Junta duas fichas do mesmo cliente em uma.

    O contato da URL é o que fica; o do corpo é absorvido e apagado. Conversas,
    negócios, atividades e notas passam para o principal; campo vazio
    no principal herda o do secundário, campo preenchido não é sobrescrito.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    principal = await db.contacts.find_one({"id": contact_id, "tenant_id": tenant_id}, {"_id": 0})
    secundario = await db.contacts.find_one(
        {"id": body.secundario_id, "tenant_id": tenant_id}, {"_id": 0}
    )
    if not principal or not secundario:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    try:
        resultado = await contatos.mesclar(db, tenant_id, principal, secundario)
    except ValueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await audit.registrar(
        db, ator=user, acao="contact.merge", tenant_id=tenant_id, alvo=contact_id,
        meta={"secundario_id": body.secundario_id},
    )
    return resultado


# ---------- Notas ----------


@router.post("/contacts/{contact_id}/notes")
async def create_note(contact_id: str, body: NotaIn, user=Depends(require_tenant_member)):
    """Anotação livre na ficha.

    Coleção própria, e não um array dentro do contato: a lista cresce sem teto e
    é lida por data. Um array embutido faria o documento do contato crescer para
    sempre — e ele é lido a cada mensagem recebida.
    """
    db = get_db()
    texto = body.texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Anotação vazia")
    contact = await db.contacts.find_one(
        {"id": contact_id, "tenant_id": user["tenant_id"]}, {"_id": 0, "id": 1}
    )
    if not contact:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")
    nota = {
        "id": str(uuid.uuid4()),
        "tenant_id": user["tenant_id"],
        "contact_id": contact_id,
        "texto": texto[:5000],
        "autor_id": user["id"],
        "autor_nome": user.get("name") or user.get("email"),
        "created_at": _now(),
    }
    await db.contact_notes.insert_one(dict(nota))
    nota.pop("_id", None)
    return nota


@router.delete("/contacts/{contact_id}/notes/{note_id}")
async def delete_note(contact_id: str, note_id: str, user=Depends(require_tenant_member)):
    db = get_db()
    filtro = {"id": note_id, "contact_id": contact_id, "tenant_id": user["tenant_id"]}
    # O atendente apaga só a própria anotação. Anotação de colega é registro de
    # atendimento de outra pessoa, e o admin é quem pode mexer nela.
    if user.get("role") != "tenant_admin":
        filtro["autor_id"] = user["id"]
    res = await db.contact_notes.delete_one(filtro)
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Anotação não encontrada")
    return {"ok": True}


# ---------- Mensagem direta ----------


async def _resolve_conexao(db, tenant_id: str, contact: dict, conn_id: str | None,
                           user: dict) -> dict:
    """De qual número a mensagem sai.

    Sem escolha explícita, prefere a conexão da última conversa do contato: ele
    já conhece aquele número, e trocar o remetente no meio de um assunto parece
    outra empresa falando.

    O atendente só fala pelas conexões liberadas para ele. Se a conversa aberta
    do contato está numa conexão fora do alcance dele, o envio é **recusado**
    em vez de abrir outra: mandar por um número diferente dividiria o histórico
    em dois lugares, e usar aquele número seria furar o escopo de acesso.
    """
    permitidas = await accessible_connection_ids(db, user)

    def alcanca(c: dict) -> bool:
        return permitidas is None or c["id"] in permitidas

    ultima = await db.conversation_sessions.find(
        {"tenant_id": tenant_id, "contact_id": contact["id"],
         "status": {"$in": ["ativa", "aguardando_humano"]}},
        {"_id": 0, "whatsapp_connection_id": 1},
    ).sort([("updated_at", -1), ("_id", -1)]).limit(1).to_list(1)
    conn_da_conversa = ultima[0].get("whatsapp_connection_id") if ultima else None

    if conn_da_conversa and permitidas is not None and conn_da_conversa not in permitidas:
        raise HTTPException(
            status_code=403,
            detail="A conversa aberta deste cliente está numa conexão à qual você não tem acesso.",
        )

    if conn_id:
        conn = await db.whatsapp_connections.find_one(
            {"id": conn_id, "tenant_id": tenant_id}, {"_id": 0}
        )
        if not conn:
            raise HTTPException(status_code=400, detail="Conexão de WhatsApp inválida")
        if not alcanca(conn):
            raise HTTPException(status_code=403, detail="Você não tem acesso a esta conexão")
    else:
        conn = None
        if conn_da_conversa:
            conn = await db.whatsapp_connections.find_one(
                {"id": conn_da_conversa, "tenant_id": tenant_id, "status": "conectado"},
                {"_id": 0},
            )
        if not conn:
            filtro = {"tenant_id": tenant_id, "status": "conectado"}
            if permitidas is not None:
                filtro["id"] = {"$in": permitidas}
            conn = await db.whatsapp_connections.find_one(filtro, {"_id": 0})
    if not conn:
        raise HTTPException(status_code=400, detail="Nenhuma conexão de WhatsApp conectada")
    if conn.get("status") != "conectado":
        raise HTTPException(
            status_code=400,
            detail=f"A conexão '{conn.get('nome')}' está {conn.get('status')}",
        )
    if not whatsapp.credencial(conn):
        raise HTTPException(status_code=400,
                            detail="Conexão sem credencial da Cloud API cadastrada")
    return conn


@router.post("/contacts/{contact_id}/send")
async def send_to_contact(contact_id: str, body: EnvioIn, user=Depends(require_tenant_member)):
    """Escreve para o contato a partir da ficha, sem esperar que ele fale primeiro.

    A conversa é criada (ou reaproveitada) antes do envio e já nasce em nome de
    quem enviou — assim a resposta cai na Central com contexto, em vez de virar
    uma conversa nova sem ninguém.
    """
    db = get_db()
    tenant_id = user["tenant_id"]
    texto = body.texto.strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Mensagem vazia")

    contact = await db.contacts.find_one({"id": contact_id, "tenant_id": tenant_id}, {"_id": 0})
    if not contact:
        raise HTTPException(status_code=404, detail="Cliente não encontrado")

    conn = await _resolve_conexao(db, tenant_id, contact, body.whatsapp_connection_id, user)
    try:
        return await outbound.send_direct(
            db, tenant_id, contact, conn, texto, user["id"],
            template=body.template.model_dump() if body.template else None)
    except HTTPException:
        raise
    except JanelaFechada as e:
        # Contato que nunca escreveu (ou escreveu ha mais de 24h) so recebe
        # modelo aprovado. E a regra da plataforma, entao a ficha diz isso em
        # vez de mostrar "falha ao enviar", que mandaria a pessoa tentar de
        # novo indefinidamente.
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao enviar via WhatsApp: {e}")


# ---------- Sessões (telas antigas) ----------


@router.get("/sessions")
async def list_sessions(user=Depends(require_tenant_admin), status: str | None = None):
    db = get_db()
    q = {"tenant_id": user["tenant_id"]}
    if status:
        q["status"] = status
    sessions = await db.conversation_sessions.find(q, {"_id": 0}).sort("updated_at", -1).to_list(500)
    return sessions


@router.get("/sessions/{session_id}/messages")
async def session_messages(session_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    session = await db.conversation_sessions.find_one(
        {"id": session_id, "tenant_id": user["tenant_id"]}, {"_id": 0}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    # Mesmo desempate por `_id` da Central: mensagens da mesma rajada
    # compartilham o timestamp, e sem ele a ordem do histórico é indefinida.
    messages = await db.messages_log.find(
        {"session_id": session_id}, {"_id": 0}
    ).sort([("timestamp", 1), ("_id", 1)]).to_list(1000)
    return {"session": session, "messages": messages}


class SendMessageIn(BaseModel):
    content: str


@router.post("/sessions/{session_id}/messages")
async def send_human_message(session_id: str, body: SendMessageIn, user=Depends(require_tenant_admin)):
    """Human agent sends a message inside handoff inbox."""
    db = get_db()
    session = await db.conversation_sessions.find_one(
        {"id": session_id, "tenant_id": user["tenant_id"]}
    )
    if not session:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    try:
        await _send_human(user["tenant_id"], session_id, body.content)
    except Exception as e:
        raise HTTPException(status_code=502, detail=f"Falha ao enviar via WhatsApp: {e}")
    msg = {
        "id": str(uuid.uuid4()),
        "session_id": session_id,
        "direction": "enviada",
        "content": body.content,
        "type": "text",
        "sent_by": user["email"],
        "timestamp": _now(),
    }
    return msg


class SessionStatusIn(BaseModel):
    status: str


@router.patch("/sessions/{session_id}")
async def update_session(session_id: str, body: SessionStatusIn, user=Depends(require_tenant_admin)):
    db = get_db()
    res = await db.conversation_sessions.update_one(
        {"id": session_id, "tenant_id": user["tenant_id"]},
        {"$set": {"status": body.status, "updated_at": _now()}},
    )
    if res.matched_count == 0:
        raise HTTPException(status_code=404, detail="Sessão não encontrada")
    return {"ok": True}
