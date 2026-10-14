"""Agentes de IA da empresa: comportamento e credencial.

**A chave de API mora no agente, não na empresa.** Um cliente pode ter uma
chave da Anthropic no agente de suporte e uma da OpenAI no de vendas —
contas, times e orçamentos diferentes. Uma credencial única por empresa
obrigaria os dois a dividirem a mesma conta e o mesmo modelo.

O provedor não permite que um SaaS de terceiro use a assinatura do cliente
por OAuth — Anthropic, OpenAI e Google fecharam esse caminho no primeiro
trimestre de 2026 — então a credencial é uma chave de API que o cliente gera
no console e cola aqui. A tela leva ele direto à página certa (`console_url`
do catálogo) para que isso leve segundos, não uma busca na documentação.

Tudo aqui é `require_tenant_admin`: é configuração e é credencial. A regra
do projeto de abrir a base para o atendente (`require_tenant_member`) vale
para contatos, funil e agenda — não para o segredo que paga a conta.
"""
import uuid
from datetime import datetime, timezone

from fastapi import APIRouter, Depends, File, Form, HTTPException, UploadFile
from pydantic import BaseModel

from db import get_db
from auth import require_tenant_admin
import ai_agents
import ai_client
import saidas
import ai_credentials
import ai_documentos
import ai_estoque
import ai_runtime
import media_store
from routes.contacts import _le_upload

router = APIRouter(prefix="/api/ai", tags=["ai"])


def _now():
    return datetime.now(timezone.utc).isoformat()


@router.get("/providers")
async def listar_provedores(_=Depends(require_tenant_admin)):
    """Catálogo do passo de credencial: provedores, modelos, preço e o link do
    console de cada um. Sem segredo nenhum.

    `plataforma_disponivel` acompanha porque decide o texto da tela: sem chave
    no agente e sem chave nossa, ele não responde nada — e isso precisa ser dito
    antes de o cliente ligar o agente, não depois da primeira mensagem perdida.
    """
    return {
        "provedores": ai_client.catalogo_publico(),
        "plataforma_disponivel": ai_runtime._credencial_plataforma() is not None,
    }


class ModelosIn(BaseModel):
    provedor: str
    chave_api: str | None = None
    base_url: str | None = None
    # Ao editar: sem chave nova, usa a guardada neste agente, desde que
    # provedor e endereço sejam os mesmos (mesma regra de salvar).
    agente_id: str | None = None


@router.post("/models")
async def modelos_da_conta(body: ModelosIn, user=Depends(require_tenant_admin)):
    """Os modelos que esta chave alcança, lidos do provedor na hora.

    POST e não GET porque leva a chave no corpo: numa query string ela iria
    parar no log de acesso do Caddy e no histórico do navegador.
    """
    chave = (body.chave_api or "").strip()
    if not chave and body.agente_id:
        atual = await get_db().ai_agents.find_one(
            {"id": body.agente_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
        mesmo_endereco = ((atual or {}).get("base_url") or None) == (
            (body.base_url or "").strip().rstrip("/") or None)
        if (atual and atual.get("credencial") and atual.get("provedor") == body.provedor
                and (mesmo_endereco or not ai_client.PROVEDORES.get(body.provedor, {}).get("url_livre"))):
            try:
                chave = ai_credentials.decifrar(atual["credencial"])
            except ai_credentials.CredencialError as e:
                raise HTTPException(status_code=400, detail=str(e))
    if not chave:
        raise HTTPException(status_code=400, detail="Cole a chave de API para buscar os modelos")
    try:
        modelos = await ai_client.listar_modelos(body.provedor, chave, body.base_url)
    except ai_client.IAError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return {"modelos": modelos}


@router.get("/usage")
async def consumo(mes: str | None = None, user=Depends(require_tenant_admin)):
    """Consumo do mês. `mes` no formato AAAA-MM; ausente = mês corrente."""
    db = get_db()
    return await ai_runtime.consumo_do_mes(db, user["tenant_id"], mes)


# ---------------------------------------------------------------------------
# Agentes
# ---------------------------------------------------------------------------
#
# Vários por empresa, cada um amarrado a conexões de WhatsApp. Um número de
# vendas e um de suporte precisam de agentes diferentes.
#
# O prompt não é escrito num campo em branco: a empresa escolhe peças (tipo,
# tom, tamanho, o que não fazer) e `ai_agents.montar_instrucoes` costura. Um
# textarea vazio produz agente ruim, porque ninguém sabe escrever prompt de
# sistema — mas todo mundo sabe dizer "é vendas ou é suporte".


class AgenteIn(BaseModel):
    nome: str
    # Credencial deste agente. `chave_api` ausente = manter a que já está
    # guardada (o cliente não tem como reenviá-la: nunca a devolvemos).
    provedor: str | None = None
    modelo: str | None = None
    # Endereço do Chat Completions, só para o provedor "compativel". Nos
    # outros o endereço é o do catálogo e este campo é ignorado.
    base_url: str | None = None
    chave_api: str | None = None
    tipo: str = "geral"
    objetivo: str | None = None          # obrigatório quando tipo = personalizado
    modo: str = ai_agents.MODO_FALLBACK
    ativo: bool = True

    # Sobre a empresa
    empresa_nome: str | None = None
    empresa_site: str | None = None
    empresa_segmento: str | None = None
    empresa_descricao: str | None = None

    # Comportamento
    tom: list[str] = []
    tamanho: str = "normais"
    # Texto livre, sempre enviado ao modelo. E o campo para o que as
    # escolhas prontas nao cobrem.
    instrucoes: str | None = None
    nao_fazer: str | None = None
    base_conhecimento: str | None = None
    escalar_quando: str | None = None

    # Deixa o agente consultar a agenda e marcar horário de verdade. Desligado
    # por padrão: ligar dá ao modelo a capacidade de **escrever** no sistema, e
    # isso é uma decisão do cliente, não um default. Sem serviços cadastrados, a
    # opção não tem efeito nenhum (ver `ai_agents.preparar_agenda`).
    agenda_ativa: bool = False
    # Deixa o agente encerrar o atendimento quando o assunto se resolve (ver
    # `ai_atendimento`). Desligado pelo mesmo motivo da agenda.
    finalizar_ativo: bool = False

    # Onde atende. Vazio = todas as conexões da empresa.
    conexoes: list[str] = []
    # Para qual departamento a conversa vai quando o agente transfere.
    departamento_id: str | None = None


def _valida_agente(body: AgenteIn):
    if body.tipo not in ai_agents.TIPOS:
        raise HTTPException(status_code=400, detail="Tipo de assistente de IA desconhecido")
    if body.tipo == "personalizado" and not (body.objetivo or "").strip():
        # Sem objetivo, um agente "personalizado" nao tem estrategia nenhuma:
        # responderia qualquer coisa. Melhor recusar do que criar um agente que
        # so decepciona no primeiro contato real.
        raise HTTPException(status_code=400,
                            detail="Descreva o objetivo do assistente de IA personalizado")
    if body.modo not in ai_agents.MODOS:
        raise HTTPException(status_code=400, detail="Modo de atuação desconhecido")
    if body.tamanho not in ai_agents.TAMANHOS:
        raise HTTPException(status_code=400, detail="Tamanho de resposta desconhecido")
    desconhecidos = [t for t in body.tom if t not in ai_agents.TONS]
    if desconhecidos:
        raise HTTPException(status_code=400,
                            detail=f"Tom desconhecido: {', '.join(desconhecidos)}")


async def _resolver_credencial(body: AgenteIn, atual: dict | None) -> dict:
    """O bloco de credencial que vai para o documento do agente.

    Valida contra o provedor **antes** de gravar. Guardar primeiro e testar
    depois deixaria o agente marcado como pronto com uma chave morta, e o
    cliente só descobriria na primeira mensagem de um contato real — quando já
    é tarde. O teste custa fração de centavo.

    Sem provedor escolhido, o agente fica sem chave própria e cai na chave da
    plataforma (com o teto do plano). É um estado válido, não um erro.
    """
    if not body.provedor:
        return {"provedor": None, "modelo": None, "credencial": None,
                "credencial_status": None, "credencial_erro": None,
                "credencial_em": None, "base_url": None}

    if body.provedor not in ai_client.PROVEDORES:
        raise HTTPException(status_code=400, detail="Provedor desconhecido")
    provedor = ai_client.PROVEDORES[body.provedor]
    try:
        modelo = (body.modelo or "").strip() or ai_client.modelo_padrao(body.provedor)
        ai_client.modelo_de(body.provedor, modelo)
    except ai_client.IAError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Endereço informado pelo cliente: https e destino público, conferido antes
    # de a chave sair do servidor em direção a ele.
    base_url = None
    if provedor.get("url_livre"):
        try:
            base_url = await ai_client.validar_base_url(body.base_url)
        except ai_client.IAError as e:
            raise HTTPException(status_code=400, detail=str(e))

    if body.chave_api:
        chave = body.chave_api.strip()
        prefixo = provedor.get("prefixo_chave")
        if prefixo and not chave.startswith(prefixo):
            # Conferido antes de gastar uma chamada: o engano comum é colar a
            # chave de um provedor no campo do outro.
            raise HTTPException(
                status_code=400,
                detail=(f"Esta não parece uma chave de "
                        f"{ai_client.PROVEDORES[body.provedor]['rotulo']} "
                        f"(deveria começar com \"{prefixo}\")."),
            )
    elif (atual and atual.get("credencial") and atual.get("provedor") == body.provedor
          # Trocar o endereço exige a chave de novo: reaproveitar a guardada
          # mandaria a credencial para um servidor que ela nunca conheceu, e
          # quem troca o endereço pode não ser quem colou a chave.
          and (atual.get("base_url") or None) == base_url):
        try:
            chave = ai_credentials.decifrar(atual["credencial"])
        except ai_credentials.CredencialError as e:
            raise HTTPException(status_code=400, detail=str(e))
    else:
        # Trocar de provedor sem informar a chave nova não tem como funcionar:
        # a guardada é do provedor antigo.
        raise HTTPException(status_code=400,
                            detail=("Informe a chave de API de novo: o endereço da API mudou"
                                    if atual and atual.get("provedor") == body.provedor
                                    and atual.get("credencial")
                                    else "Informe a chave de API do provedor escolhido"))

    try:
        await ai_client.validar(body.provedor, chave, modelo, base_url=base_url)
    except ai_client.IAError as e:
        # 400, não 502: quem precisa agir é o cliente (chave, saldo, acesso ao
        # modelo), e a mensagem de `_traduz_erro` já diz o quê.
        raise HTTPException(status_code=400, detail=str(e))

    try:
        credencial = ai_credentials.cifrar(chave)
    except ai_credentials.CredencialError as e:
        raise HTTPException(status_code=500, detail=str(e))

    return {"provedor": body.provedor, "modelo": modelo, "credencial": credencial,
            "credencial_status": "ok", "credencial_erro": None,
            "credencial_em": _now(), "base_url": base_url}


def _publico(agente: dict) -> dict:
    """O agente como a tela pode vê-lo: o bloco cifrado sai, a máscara fica.

    Devolver a chave "para preencher o campo de edição" a faria trafegar a cada
    abertura de tela — por isso o formulário reaproveita a guardada quando o
    provedor não muda.
    """
    a = {k: v for k, v in agente.items() if k not in ("_id", "credencial")}
    a["documentos"] = [ai_documentos.publico(d) for d in agente.get("documentos") or []]
    credencial = agente.get("credencial") or {}
    a["mascara"] = credencial.get("mascara")
    a["tem_chave"] = bool(credencial)
    return a


@router.get("/agents/catalog")
async def catalogo_de_agentes(_=Depends(require_tenant_admin)):
    """Tipos, tons, tamanhos, segmentos e modos — o que a tela oferece."""
    return ai_agents.catalogo_publico()


@router.get("/agents")
async def listar_agentes(user=Depends(require_tenant_admin)):
    """Os agentes com o que cada um está atendendo agora.

    Os contadores saem de **uma** agregação sobre os ids em tela, não de uma
    consulta por cartão — mesmo motivo de `atividades.negocios_sem_proximo_passo`:
    uma ida ao banco por cartão custa caro num túnel SSH, e a tela é uma grade.
    """
    db = get_db()
    agentes = await db.ai_agents.find(
        {"tenant_id": user["tenant_id"]}, {"_id": 0}
    ).sort("created_at", 1).to_list(50)
    if not agentes:
        return []

    cursor = db.conversation_sessions.aggregate([
        {"$match": {"tenant_id": user["tenant_id"],
                    "ia_agente_id": {"$in": [a["id"] for a in agentes]}}},
        {"$group": {"_id": {"agente": "$ia_agente_id", "situacao": "$status_atendimento"},
                    "n": {"$sum": 1}}},
    ])
    por_agente: dict[str, dict] = {}
    for linha in await cursor.to_list(500):
        chave = linha["_id"]
        alvo = por_agente.setdefault(chave["agente"], {"atendendo": 0, "transferidas": 0, "total": 0})
        alvo["total"] += linha["n"]
        if chave["situacao"] == "bot":
            # Ainda com o agente: ele respondeu e a conversa não saiu para gente.
            alvo["atendendo"] += linha["n"]
        elif chave["situacao"] in ("aguardando", "em_atendimento"):
            alvo["transferidas"] += linha["n"]

    vazio = {"atendendo": 0, "transferidas": 0, "total": 0}
    saida = []
    for a in agentes:
        pub = _publico(a)
        pub["conversas"] = por_agente.get(a["id"], dict(vazio))
        saida.append(pub)
    return saida


@router.post("/agents")
async def criar_agente(body: AgenteIn, user=Depends(require_tenant_admin)):
    _valida_agente(body)
    db = get_db()
    campos = body.model_dump()
    campos.pop("chave_api", None)  # nunca em claro no documento
    agente = {
        "id": str(uuid.uuid4()),
        "tenant_id": user["tenant_id"],
        **campos,
        **await _resolver_credencial(body, None),
        "created_at": _now(),
        "updated_at": _now(),
    }
    await db.ai_agents.insert_one(agente)
    return _publico(agente)


@router.patch("/agents/{agent_id}")
async def atualizar_agente(agent_id: str, body: AgenteIn, user=Depends(require_tenant_admin)):
    _valida_agente(body)
    db = get_db()
    atual = await db.ai_agents.find_one({"id": agent_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not atual:
        raise HTTPException(status_code=404, detail="Assistente de IA não encontrado")
    campos = body.model_dump()
    campos.pop("chave_api", None)
    await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$set": {**campos, **await _resolver_credencial(body, atual), "updated_at": _now()}},
    )
    return _publico(await db.ai_agents.find_one({"id": agent_id}, {"_id": 0}))


@router.delete("/agents/{agent_id}")
async def remover_agente(agent_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    agente = await db.ai_agents.find_one({"id": agent_id, "tenant_id": user["tenant_id"]},
                                         {"_id": 0, "midias": 1}) or {}
    res = await db.ai_agents.delete_one({"id": agent_id, "tenant_id": user["tenant_id"]})
    if res.deleted_count == 0:
        raise HTTPException(status_code=404, detail="Assistente de IA não encontrado")
    # As fotos do catálogo voltam para a retenção (ficam nas mensagens já enviadas).
    await media_store.liberar_do_catalogo(
        db, user["tenant_id"], [m.get("media_id") for m in agente.get("midias") or []])
    # O estoque é do agente: sem ele, as linhas ficariam no banco sem dono.
    await db.ai_estoque.delete_many({"tenant_id": user["tenant_id"], "agente_id": agent_id})
    return {"ok": True}


class PoderesIn(BaseModel):
    # Só o que vier preenchido muda. Uma chave por poder, e não o agente
    # inteiro: o PATCH completo exige o formulário todo, e a tela de treino não
    # tem (nem deve ter) provedor, chave e tom à mão.
    agenda_ativa: bool | None = None
    finalizar_ativo: bool | None = None


@router.patch("/agents/{agent_id}/poderes")
async def mudar_poderes(agent_id: str, body: PoderesIn, user=Depends(require_tenant_admin)):
    """O que o agente pode fazer no sistema além de conversar."""
    db = get_db()
    await _agente_do_tenant(db, agent_id, user["tenant_id"])
    mudanca = {k: v for k, v in body.model_dump().items() if v is not None}
    if mudanca:
        await db.ai_agents.update_one(
            {"id": agent_id, "tenant_id": user["tenant_id"]},
            {"$set": {**mudanca, "updated_at": _now()}})
    return await _poderes(db, user["tenant_id"], agent_id)


@router.get("/agents/{agent_id}/poderes")
async def ver_poderes(agent_id: str, user=Depends(require_tenant_admin)):
    return await _poderes(get_db(), user["tenant_id"], agent_id)


async def _poderes(db, tenant_id: str, agent_id: str) -> dict:
    """Os poderes e se a agenda tem com o que trabalhar.

    `agenda_pronta` existe por causa de um caso real (02/10/2026): a chave
    estava ligada, a empresa não tinha nenhum serviço cadastrado, o agente não
    recebeu ferramenta nenhuma e a tela não dizia nada. O cliente achou que o
    agente marcava na agenda, e ele não marcava.
    """
    agente = await _agente_do_tenant(db, agent_id, tenant_id)
    # Passeio sem saída na grade não dá ao agente nada para oferecer: conta
    # como não pronto, igual a não ter passeio.
    passeios = len(await saidas.passeios_com_saida(db, tenant_id))
    return {"agenda_ativa": bool(agente.get("agenda_ativa")),
            "finalizar_ativo": bool(agente.get("finalizar_ativo")),
            "agenda_servicos": passeios,
            "agenda_pronta": passeios > 0}


class TesteIn(BaseModel):
    # A conversa do teste, no mesmo formato do histórico real. A última
    # mensagem é a que o agente vai responder.
    mensagens: list[dict]


@router.post("/agents/{agent_id}/test")
async def testar_agente(agent_id: str, body: TesteIn, user=Depends(require_tenant_admin)):
    """Conversa de teste, sem WhatsApp e sem gravar nada.

    Mesma ideia do "Testar" do editor de fluxos: ver o agente responder antes
    de apontá-lo para clientes de verdade. Usa o motor real
    (`ai_runtime.responder_por_tenant`), então também exercita a credencial, o
    teto do plano e a medição de consumo — um teste que passasse por fora
    desses três não provaria que o agente funciona em produção.
    """
    db = get_db()
    agente = await db.ai_agents.find_one({"id": agent_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not agente:
        raise HTTPException(status_code=404, detail="Assistente de IA não encontrado")

    historico = [
        {"role": "assistant" if m.get("role") == "assistant" else "user",
         "content": str(m.get("content") or "").strip()}
        for m in body.mensagens if str(m.get("content") or "").strip()
    ]
    while historico and historico[0]["role"] != "user":
        historico.pop(0)
    if not historico:
        raise HTTPException(status_code=400, detail="Envie ao menos uma mensagem do cliente")

    ultima = historico[-1]["content"] if historico[-1]["role"] == "user" else ""
    if ultima and ai_agents.pediu_humano(ultima):
        # Mesmo atalho do atendimento real: nem chega a chamar o provedor.
        return {"transferiu": True, "texto": None,
                "motivo": "O cliente pediu para falar com uma pessoa"}

    # O contato do teste é fictício, e as ferramentas rodam em modo simulação:
    # consultam a agenda de verdade (é o que prova que a grade está certa) e não
    # gravam nada. Um teste que marcasse horário ocuparia a agenda do cliente.
    contato_teste = {"id": "teste", "nome": "Contato de teste", "numero_whatsapp": ""}
    bloco_agenda, ferramentas, executar = await ai_agents.preparar_ferramentas(
        db, user["tenant_id"], agente, contato_teste, None, simulado=True)
    executar, marcou = ai_agents.registrar_marcacao(executar)

    try:
        resposta = await ai_runtime.responder_por_agente(
            db, user["tenant_id"], agente,
            ai_agents.montar_instrucoes(agente, bloco_agenda), historico,
            ferramentas=ferramentas, executar=executar)
    except ai_client.IAError as e:
        # A chave pode ter sido revogada no console depois de salva. Marcar aqui
        # é o que faz o cartão mostrar o problema em vez de o agente falhar em
        # silêncio na próxima mensagem de um contato real.
        if agente.get("credencial"):
            await db.ai_agents.update_one(
                {"id": agent_id, "tenant_id": user["tenant_id"]},
                {"$set": {"credencial_status": "erro", "credencial_erro": str(e)}})
        raise HTTPException(status_code=400, detail=str(e))
    if agente.get("credencial_status") == "erro":
        # Voltou a funcionar (saldo recarregado, chave reativada).
        await db.ai_agents.update_one(
            {"id": agent_id, "tenant_id": user["tenant_id"]},
            {"$set": {"credencial_status": "ok", "credencial_erro": None}})

    texto = (resposta.texto or "").strip()
    # As mesmas duas regras do atendimento real (`ai_agents.tentar_responder`):
    # a frase que acompanha a transferência chega ao contato, e confirmação de
    # horário sem agendamento gravado é segurada. Sem elas aqui, o teste
    # mostraria ao admin uma conversa diferente da que o cliente vai ter.
    if ai_agents.SENTINELA.lower() in texto.lower():
        frase = ai_agents.frase_de_transferencia(texto)
        if frase and await ai_agents.confirmou_sem_agenda(db, user["tenant_id"], contato_teste, frase, marcou):
            frase = ai_agents.AVISO_CONFIRMACAO
        return {"transferiu": True, "texto": frase or None,
                "motivo": "O assistente de IA decidiu transferir para um atendente"}
    if await ai_agents.confirmou_sem_agenda(db, user["tenant_id"], contato_teste, texto, marcou):
        return {"transferiu": True, "texto": ai_agents.AVISO_CONFIRMACAO, "segurada": texto,
                "motivo": ("O assistente de IA ia confirmar um horário que não está gravado nas Saídas. "
                           "A mensagem foi segurada e a conversa iria para uma pessoa confirmar")}
    return {
        "transferiu": False,
        "texto": texto,
        "tokens": (resposta.tokens_entrada + resposta.tokens_saida
                   + resposta.tokens_cache_leitura + resposta.tokens_cache_escrita),
        # Quais ferramentas ele chegou a usar. É o que mostra, no teste, que o
        # agente foi de fato à agenda em vez de inventar um horário plausível
        # a diferença entre as duas coisas não aparece no texto da resposta.
        "ferramentas": resposta.ferramentas_usadas,
        # No teste nada é gravado, mas a tela precisa mostrar que, numa
        # conversa real, a conversa teria sido encerrada aqui.
        "finalizou": "finalizar_atendimento" in (resposta.ferramentas_usadas or []),
    }


@router.get("/agents/{agent_id}/prompt")
async def ver_prompt(agent_id: str, user=Depends(require_tenant_admin)):
    """O prompt final montado a partir das escolhas da tela.

    Existe para o admin poder conferir o que o agente realmente recebe. Sem
    isto, ajustar tom e restrições é adivinhação: a pessoa muda um campo e não
    tem como saber o que mudou de fato.
    """
    db = get_db()
    agente = await db.ai_agents.find_one({"id": agent_id, "tenant_id": user["tenant_id"]}, {"_id": 0})
    if not agente:
        raise HTTPException(status_code=404, detail="Assistente de IA não encontrado")
    bloco_agenda, _, _ = await ai_agents.preparar_agenda(db, user["tenant_id"], agente)
    return {"instrucoes": ai_agents.montar_instrucoes(agente, bloco_agenda)}


# ---------------------------------------------------------------------------
# Fotos que o agente pode enviar
# ---------------------------------------------------------------------------
#
# Vivem embutidas no documento do agente (`midias`), e não numa coleção própria
# — o oposto da decisão tomada em `contact_notes`. O motivo é o inverso do de
# lá: aquela lista cresce sem teto e não é necessária na leitura quente, esta é
# limitada a MAX_FOTOS e é lida em TODA mensagem, porque é dela que sai o
# catálogo no prompt. Uma coleção separada custaria uma consulta extra por
# mensagem recebida para buscar o que sempre é preciso.
#
# O binário vai para o `media_store` como qualquer outro arquivo do sistema, e
# é o mesmo `/api/media/{id}` que serve a prévia na tela de configuração.


class MidiaPatch(BaseModel):
    descricao: str


async def _agente_do_tenant(db, agent_id: str, tenant_id: str) -> dict:
    agente = await db.ai_agents.find_one({"id": agent_id, "tenant_id": tenant_id}, {"_id": 0})
    if not agente:
        raise HTTPException(status_code=404, detail="Assistente de IA não encontrado")
    return agente


@router.post("/agents/{agent_id}/midias")
async def subir_foto(agent_id: str, file: UploadFile = File(...),
                     descricao: str = Form(""), user=Depends(require_tenant_admin)):
    """Sobe uma foto para o catálogo do agente.

    A descrição é obrigatória de propósito: ela é a **única** coisa que o
    modelo sabe sobre a imagem — ele é de texto e não enxerga o arquivo. Uma
    foto sem descrição é uma foto que o agente nunca vai saber quando mandar,
    e aceitar o upload assim só produziria a frustração mais tarde.
    """
    if not descricao.strip():
        raise HTTPException(
            status_code=400,
            detail="Descreva a foto — é por essa descrição que o assistente de IA decide quando enviá-la.")

    db = get_db()
    agente = await _agente_do_tenant(db, agent_id, user["tenant_id"])
    midias = agente.get("midias") or []
    if len(midias) >= ai_agents.MAX_FOTOS:
        raise HTTPException(
            status_code=400,
            detail=(f"Máximo de {ai_agents.MAX_FOTOS} fotos por assistente de IA. A descrição de cada "
                    "uma vai no prompt de toda mensagem, então a lista precisa ser curta."))

    kind = media_store.classify(file.content_type, file.filename)
    if kind != "image":
        raise HTTPException(status_code=400, detail="Envie uma imagem (JPG, PNG ou WebP).")

    try:
        doc = await media_store.store_stream(
            db, user["tenant_id"], None, user["id"],
            file.filename, file.content_type, file.read, kind="image")
    except media_store.MediaError as e:
        raise HTTPException(status_code=400, detail=str(e))

    # Fora da retenção de mídia enquanto estiver no catálogo (ver media_store).
    await db.media_files.update_one({"id": doc["id"], "tenant_id": user["tenant_id"]},
                                    {"$set": {"uso": media_store.USO_CATALOGO_IA}})
    foto = {
        "id": str(uuid.uuid4()),
        "media_id": doc["id"],
        "descricao": descricao.strip(),
        "filename": doc.get("filename"),
        "created_at": _now(),
    }
    await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$push": {"midias": foto}, "$set": {"updated_at": _now()}})
    return foto


@router.patch("/agents/{agent_id}/midias/{midia_id}")
async def editar_foto(agent_id: str, midia_id: str, body: MidiaPatch,
                      user=Depends(require_tenant_admin)):
    """Corrige a descrição. É a operação mais provável depois de ver o agente
    mandar a foto na hora errada — e a correção vale já na próxima mensagem."""
    if not body.descricao.strip():
        raise HTTPException(status_code=400, detail="A descrição não pode ficar vazia")
    db = get_db()
    agente = await _agente_do_tenant(db, agent_id, user["tenant_id"])

    # Reescreve a lista inteira em vez de usar o operador posicional
    # (`midias.$.descricao`). A lista tem no máximo MAX_FOTOS itens, então
    # regravá-la é barato — e o posicional casa pelo filtro, o que torna o
    # comportamento dependente de um detalhe do Mongo difícil de enxergar aqui.
    midias = agente.get("midias") or []
    achou = False
    for m in midias:
        if m.get("id") == midia_id:
            m["descricao"] = body.descricao.strip()
            achou = True
    if not achou:
        raise HTTPException(status_code=404, detail="Foto não encontrada")

    await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$set": {"midias": midias, "updated_at": _now()}})
    return {"ok": True}


@router.delete("/agents/{agent_id}/midias/{midia_id}")
async def remover_foto(agent_id: str, midia_id: str, user=Depends(require_tenant_admin)):
    """Tira a foto do catálogo. O arquivo em si fica para a rotina de retenção:
    ele pode estar referenciado por mensagens já enviadas, e apagá-lo agora
    deixaria bolhas quebradas no histórico de quem já recebeu."""
    db = get_db()
    agente = await _agente_do_tenant(db, agent_id, user["tenant_id"])
    res = await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$pull": {"midias": {"id": midia_id}}, "$set": {"updated_at": _now()}})
    if res.modified_count == 0:
        raise HTTPException(status_code=404, detail="Foto não encontrada")
    await media_store.liberar_do_catalogo(
        db, user["tenant_id"], [m.get("media_id") for m in agente.get("midias") or [] if m.get("id") == midia_id])
    return {"ok": True}


# ---------------------------------------------------------------------------
# Treinamento: o que a empresa vai ensinando ao agente
# ---------------------------------------------------------------------------
#
# Cada mensagem mandada na tela de treino vira um ensinamento guardado no
# próprio agente (`memoria`) e entra no prompt de toda resposta. Embutido no
# documento pelo mesmo motivo das fotos: é lido em toda mensagem recebida, e o
# teto de caracteres (`MAX_MEMORIA_TOTAL`) mantém a lista curta.
#
# Quem ensina é só o admin, nunca o contato. Deixar o agente memorizar sozinho a
# partir das conversas reais daria a qualquer pessoa que escreve para a empresa
# o poder de "ensinar" um preço falso que valeria para todos os seguintes.


class MemoriaIn(BaseModel):
    texto: str


def _texto_memoria(texto: str, memoria: list[dict], ignorar_id: str | None = None) -> str:
    texto = (texto or "").strip()
    if not texto:
        raise HTTPException(status_code=400, detail="Escreva o que o assistente de IA deve aprender")
    if len(texto) > ai_agents.MAX_MEMORIA_ITEM:
        raise HTTPException(
            status_code=400,
            detail=(f"Cada ensinamento vai até {ai_agents.MAX_MEMORIA_ITEM} caracteres. "
                    "Divida em mensagens menores, um assunto por vez."))
    total = sum(len(m.get("texto") or "") for m in memoria if m.get("id") != ignorar_id)
    if total + len(texto) > ai_agents.MAX_MEMORIA_TOTAL:
        raise HTTPException(
            status_code=400,
            detail=(f"O treinamento chegou ao limite de {ai_agents.MAX_MEMORIA_TOTAL} caracteres. "
                    "Tudo isso vai em toda resposta do assistente de IA: apague ensinamentos antigos "
                    "ou que já foram corrigidos por outros mais novos."))
    return texto


@router.post("/agents/{agent_id}/memoria")
async def ensinar(agent_id: str, body: MemoriaIn, user=Depends(require_tenant_admin)):
    """Guarda um ensinamento. Vale a partir da próxima mensagem que o agente responder."""
    db = get_db()
    agente = await _agente_do_tenant(db, agent_id, user["tenant_id"])
    item = {
        "id": str(uuid.uuid4()),
        "texto": _texto_memoria(body.texto, agente.get("memoria") or []),
        "autor_id": user["id"],
        "autor_nome": user.get("name"),
        "created_at": _now(),
    }
    await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$push": {"memoria": item}, "$set": {"updated_at": _now()}})
    return item


@router.patch("/agents/{agent_id}/memoria/{item_id}")
async def corrigir_ensinamento(agent_id: str, item_id: str, body: MemoriaIn,
                               user=Depends(require_tenant_admin)):
    db = get_db()
    agente = await _agente_do_tenant(db, agent_id, user["tenant_id"])
    memoria = agente.get("memoria") or []
    texto = _texto_memoria(body.texto, memoria, ignorar_id=item_id)
    # Reescreve a lista inteira, como `editar_foto`: é curta, e o operador
    # posicional dependeria de um detalhe do Mongo difícil de enxergar aqui.
    achou = False
    for m in memoria:
        if m.get("id") == item_id:
            m["texto"] = texto
            m["editado_em"] = _now()
            achou = True
    if not achou:
        raise HTTPException(status_code=404, detail="Ensinamento não encontrado")
    await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$set": {"memoria": memoria, "updated_at": _now()}})
    return {"ok": True}


@router.delete("/agents/{agent_id}/memoria/{item_id}")
async def esquecer(agent_id: str, item_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await _agente_do_tenant(db, agent_id, user["tenant_id"])
    res = await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$pull": {"memoria": {"id": item_id}}, "$set": {"updated_at": _now()}})
    if res.modified_count == 0:
        raise HTTPException(status_code=404, detail="Ensinamento não encontrado")
    return {"ok": True}


@router.delete("/agents/{agent_id}/memoria")
async def esquecer_tudo(agent_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await _agente_do_tenant(db, agent_id, user["tenant_id"])
    await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$set": {"memoria": [], "updated_at": _now()}})
    return {"ok": True}


# ---------------------------------------------------------------------------
# Estoque que o agente consulta (ver `ai_estoque`)
# ---------------------------------------------------------------------------

@router.post("/agents/{agent_id}/estoque")
async def subir_estoque(agent_id: str, file: UploadFile = File(...),
                        user=Depends(require_tenant_admin)):
    """Troca o estoque do agente pela planilha enviada (Excel .xlsx ou CSV)."""
    db = get_db()
    await _agente_do_tenant(db, agent_id, user["tenant_id"])

    # Mesmo leitor da importação de contatos: teto de 10MB aplicado durante a
    # leitura, não depois de o arquivo inteiro já estar na memória.
    dados = await _le_upload(file)
    try:
        colunas, linhas = ai_estoque.le_planilha(dados, file.filename or "")
    except ai_estoque.EstoqueError as e:
        raise HTTPException(status_code=400, detail=str(e))
    return await ai_estoque.substituir(db, user["tenant_id"], agent_id,
                                       file.filename, colunas, linhas)


@router.get("/agents/{agent_id}/estoque")
async def ver_estoque(agent_id: str, busca: str = "", user=Depends(require_tenant_admin)):
    """As linhas do estoque, pela **mesma** busca que o agente usa.

    É o que a tela mostra quando o admin procura "onix prata": se não achar ali,
    o agente também não acha, e o problema está na planilha, não no modelo.
    """
    db = get_db()
    agente = await _agente_do_tenant(db, agent_id, user["tenant_id"])
    linhas, total = await ai_estoque.buscar(db, user["tenant_id"], agente, busca, limite=50)
    return {"estoque": agente.get("estoque"), "linhas": linhas, "total": total}


@router.delete("/agents/{agent_id}/estoque")
async def remover_estoque(agent_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await _agente_do_tenant(db, agent_id, user["tenant_id"])
    await ai_estoque.remover(db, user["tenant_id"], agent_id)
    return {"ok": True}


# ---------------------------------------------------------------------------
# Documentos do treino (ver `ai_documentos`)
# ---------------------------------------------------------------------------

@router.post("/agents/{agent_id}/documentos")
async def subir_documento(agent_id: str, file: UploadFile = File(...),
                          user=Depends(require_tenant_admin)):
    """Lê o texto do PDF/Word/texto e o guarda no agente. O arquivo não é guardado."""
    db = get_db()
    agente = await _agente_do_tenant(db, agent_id, user["tenant_id"])
    dados = await _le_upload(file)
    try:
        texto = ai_documentos.extrair(file.filename or "", dados)
        doc = ai_documentos.novo(file.filename, texto, agente.get("documentos") or [])
    except ai_documentos.DocumentoError as e:
        raise HTTPException(status_code=400, detail=str(e))
    await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$push": {"documentos": doc}, "$set": {"updated_at": _now()}})
    return ai_documentos.publico(doc)


@router.delete("/agents/{agent_id}/documentos/{doc_id}")
async def remover_documento(agent_id: str, doc_id: str, user=Depends(require_tenant_admin)):
    db = get_db()
    await _agente_do_tenant(db, agent_id, user["tenant_id"])
    res = await db.ai_agents.update_one(
        {"id": agent_id, "tenant_id": user["tenant_id"]},
        {"$pull": {"documentos": {"id": doc_id}}, "$set": {"updated_at": _now()}})
    if res.modified_count == 0:
        raise HTTPException(status_code=404, detail="Documento não encontrado")
    return {"ok": True}
