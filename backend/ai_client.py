"""Camada única de chamada aos provedores de IA.

Três protocolos, não um por provedor: a Anthropic (Messages), a OpenAI
(Responses) e o **Chat Completions compatível com OpenAI**, que é o que Groq,
Gemini, OpenRouter, DeepSeek, Mistral, xAI e quase todo o resto expõem. Um
provedor novo desse terceiro grupo é uma entrada no catálogo com `base_url`,
não código novo.

Mesmo papel que `whatsapp_client.py` tem para o WhatsApp: nenhuma outra parte do
backend fala com provedor de IA diretamente. Quem chama passa provedor, modelo
e credencial, e recebe sempre a mesma `Resposta` — com o texto e a contagem de
tokens já normalizada, porque é dela que sai a medição de consumo.

Decisões que valem registro:

  - **SDK oficial de cada provedor, não httpx cru.** O resto do projeto tem
    cliente próprio (a Cloud API é REST simples), mas aqui os dois têm, e são eles que
    acertam a semântica de cache de prompt, os tipos de erro e o retry. O
    import é preguiçoso para o backend continuar subindo num ambiente onde só
    um dos dois (ou nenhum) esteja instalado.

  - **Um client por chamada, fechado no fim** — o oposto do
    `whatsapp_client._get_client()`. Lá o client é único do processo porque são
    muitas chamadas curtas para o mesmo host; aqui a credencial é *de cada empresa*, e
    um pool compartilhado significaria manter chaves de clientes vivas num
    dicionário de módulo. O custo é um handshake TLS por resposta, contra 1-3s
    da própria chamada — irrelevante.

  - **Cache de prompt é parte do desenho, não otimização posterior.** O bloco
    de instruções + base de conhecimento é idêntico em toda chamada e responde
    por ~80% dos tokens de entrada. Sem cache a conta de um cliente de porte
    médio quase dobra. Na Anthropic isso é explícito (`cache_control` no bloco
    de sistema); na OpenAI é automático sobre o prefixo — por isso a instrução
    vai em `instructions`, estável, e o que varia fica no `input`.

  - **A contagem de tokens volta em toda resposta.** É o que permite cobrar por
    faixa de uso e mostrar consumo ao cliente. Ler isso depois, do provedor,
    seria uma segunda integração para reconstruir o que a chamada já disse.
"""
import json
import logging
import re
from dataclasses import dataclass, field

import ai_credentials

logger = logging.getLogger("turismo.ia")


PROVEDOR_COMPATIVEL = "compativel"


class IAError(Exception):
    """Falha ao falar com o provedor. A mensagem é exibida ao cliente, então
    não carrega credencial nem corpo bruto de resposta."""


# ---------------------------------------------------------------------------
# Catálogo
# ---------------------------------------------------------------------------
#
# Lista curta de propósito. O provedor publica dezenas de modelos, e a maioria
# é cara demais ou lenta demais para responder WhatsApp — oferecer todos só
# transfere para o cliente uma decisão que ele não tem como tomar. Os preços
# ficam aqui (US$ por 1M de tokens) porque é o que permite mostrar o custo
# estimado na tela e medir o consumo real sem uma segunda tabela.

@dataclass(frozen=True)
class Modelo:
    id: str
    rotulo: str
    entrada: float          # US$/1M tokens
    saida: float            # US$/1M tokens
    cache_leitura: float    # US$/1M tokens lidos do cache
    cache_escrita: float    # US$/1M tokens gravados no cache (0 = sem custo extra)
    nota: str = ""


# `protocolo` decide qual caminho de chamada o provedor usa. `chat` é o Chat
# Completions compatível com OpenAI, apontado para `base_url`.
#
# `modelo_livre` aceita qualquer id de modelo além dos sugeridos: nesses
# provedores o catálogo muda toda semana (Groq e OpenRouter trocam modelo mais
# rápido do que um deploy nosso), e barrar um id que o provedor aceita só
# faria o cliente esperar por nós. Modelo fora da lista é medido em tokens com
# custo zero, porque não sabemos o preço, e a tela diz isso.
#
# Sem desconto de cache no provedor, `cache_leitura` é igual à entrada: é o que
# faz a projeção de custo da tela não contar um desconto que não existe.
#
# `prefixo_chave` None = o provedor não tem prefixo fixo (Mistral, endpoint
# próprio), e aí só a chamada de teste decide.

PROVEDORES: dict[str, dict] = {
    "anthropic": {
        "rotulo": "Anthropic (Claude)",
        "protocolo": "anthropic",
        # Link direto para a página de chaves — é o "abrir console" da tela de
        # conexão. Levar o cliente à home do provedor faria ele se perder.
        "console_url": "https://console.anthropic.com/settings/keys",
        "prefixo_chave": "sk-ant-",
        "modelos": [
            Modelo("claude-haiku-4-5", "Haiku 4.5", 1.00, 5.00, 0.10, 1.25,
                   "Recomendado para atendimento: rápido e barato."),
            Modelo("claude-sonnet-5", "Sonnet 5", 3.00, 15.00, 0.30, 3.75,
                   "Respostas mais elaboradas, ~3x o custo."),
        ],
    },
    "openai": {
        "rotulo": "OpenAI (GPT)",
        "protocolo": "responses",
        "console_url": "https://platform.openai.com/api-keys",
        "prefixo_chave": "sk-",
        "modelos": [
            Modelo("gpt-5-nano", "GPT-5 nano", 0.05, 0.40, 0.005, 0.0,
                   "O mais barato. Bom para triagem e perguntas frequentes."),
            Modelo("gpt-5-mini", "GPT-5 mini", 0.25, 2.00, 0.025, 0.0,
                   "Recomendado para atendimento: equilíbrio de custo e qualidade."),
            Modelo("gpt-5", "GPT-5", 1.25, 10.00, 0.125, 0.0,
                   "Melhor qualidade, ~5x o custo do mini."),
        ],
    },
    "groq": {
        "rotulo": "Groq",
        "protocolo": "chat",
        "base_url": "https://api.groq.com/openai/v1",
        "console_url": "https://console.groq.com/keys",
        "prefixo_chave": "gsk_",
        "modelo_livre": True,
        # O plano gratuito da Groq não cobra, só limita requisições por minuto
        # e por dia. Os preços abaixo são os do plano pago, para a projeção não
        # prometer zero a quem passar do limite gratuito.
        "gratuito": True,
        "modelos": [
            # Os Llama da Groq passaram a "fale com vendas" (out/2026): numa
            # chave do plano grátis eles respondem 404. Por isso não são
            # sugeridos; quem tiver acesso os acha em "Buscar modelos".
            Modelo("openai/gpt-oss-120b", "GPT-OSS 120B", 0.15, 0.60, 0.075, 0.0,
                   "Recomendado: modelo aberto da OpenAI, bom em seguir instruções."),
            Modelo("openai/gpt-oss-20b", "GPT-OSS 20B", 0.075, 0.30, 0.0375, 0.0,
                   "Mais barato e mais rápido. Para triagem simples."),
        ],
    },
    "gemini": {
        "rotulo": "Google (Gemini)",
        "protocolo": "chat",
        "base_url": "https://generativelanguage.googleapis.com/v1beta/openai/",
        "console_url": "https://aistudio.google.com/app/apikey",
        "prefixo_chave": "AIza",
        "modelo_livre": True,
        "gratuito": True,
        "modelos": [
            Modelo("gemini-2.5-flash", "Gemini 2.5 Flash", 0.30, 2.50, 0.075, 0.0,
                   "Recomendado: rápido, barato e com cota gratuita no AI Studio."),
            Modelo("gemini-2.5-flash-lite", "Gemini 2.5 Flash-Lite", 0.10, 0.40, 0.025, 0.0,
                   "O mais barato do Google. Para triagem e perguntas frequentes."),
            Modelo("gemini-2.5-pro", "Gemini 2.5 Pro", 1.25, 10.00, 0.31, 0.0,
                   "Melhor qualidade, bem mais caro."),
        ],
    },
    "openrouter": {
        "rotulo": "OpenRouter",
        "protocolo": "chat",
        "base_url": "https://openrouter.ai/api/v1",
        "console_url": "https://openrouter.ai/settings/keys",
        "prefixo_chave": "sk-or-",
        "modelo_livre": True,
        "gratuito": True,
        "modelos": [
            # Os gratuitos do OpenRouter (sufixo `:free`) mudam toda semana, e
            # sugerir um deles foi o que produziu 404 no primeiro uso. Ficam
            # para "Buscar modelos", que lê a lista viva da conta.
            Modelo("google/gemini-2.5-flash", "Gemini 2.5 Flash", 0.30, 2.50, 0.075, 0.0,
                   "Centenas de modelos numa conta só, inclusive gratuitos (:free)."),
        ],
    },
    "deepseek": {
        "rotulo": "DeepSeek",
        "protocolo": "chat",
        "base_url": "https://api.deepseek.com",
        "console_url": "https://platform.deepseek.com/api_keys",
        "prefixo_chave": "sk-",
        "modelo_livre": True,
        "modelos": [
            Modelo("deepseek-chat", "DeepSeek V3", 0.28, 0.42, 0.028, 0.0,
                   "Muito barato, boa qualidade."),
        ],
    },
    "mistral": {
        "rotulo": "Mistral",
        "protocolo": "chat",
        "base_url": "https://api.mistral.ai/v1",
        "console_url": "https://console.mistral.ai/api-keys",
        "prefixo_chave": None,
        "modelo_livre": True,
        "gratuito": True,
        "modelos": [
            Modelo("mistral-small-latest", "Mistral Small", 0.10, 0.30, 0.10, 0.0,
                   "Barato, bom em português."),
            Modelo("mistral-medium-latest", "Mistral Medium", 0.40, 2.00, 0.40, 0.0,
                   "Mais elaborado."),
        ],
    },
    "xai": {
        "rotulo": "xAI (Grok)",
        "protocolo": "chat",
        "base_url": "https://api.x.ai/v1",
        "console_url": "https://console.x.ai",
        "prefixo_chave": "xai-",
        "modelo_livre": True,
        "modelos": [
            Modelo("grok-4-fast-non-reasoning", "Grok 4 Fast", 0.20, 0.50, 0.05, 0.0,
                   "Rápido e barato."),
        ],
    },
    # Qualquer outro serviço que fale Chat Completions (Together, Fireworks,
    # Cerebras, um vLLM próprio publicado na internet...). O endereço é do
    # cliente, e por isso passa pelo `net_guard` antes de toda chamada: é o
    # servidor que vai até ele, e sem a checagem um admin de empresa apontaria
    # o backend para a própria rede interna.
    PROVEDOR_COMPATIVEL: {
        "rotulo": "Outro (compatível com OpenAI)",
        "protocolo": "chat",
        "url_livre": True,
        "console_url": None,
        "prefixo_chave": None,
        "modelo_livre": True,
        "modelos": [],
    },
}

# Teto do id de modelo digitado. Os ids reais passam pouco de 60 caracteres;
# o teto só impede que um campo livre vire um texto qualquer no documento.
MAX_ID_MODELO = 120


def base_url_de(provedor: str, base_url: str | None = None) -> str | None:
    """O endereço do Chat Completions deste provedor: o do catálogo, ou o que
    o cliente informou quando o provedor é o compatível genérico."""
    p = PROVEDORES.get(provedor) or {}
    if p.get("url_livre"):
        return (base_url or "").strip() or None
    return p.get("base_url")


async def validar_base_url(base_url: str | None) -> str:
    """Recusa endereço que não seja https público. Levanta `IAError` com texto
    para a tela. Chamado ao salvar e antes de toda chamada: o DNS de um domínio
    do cliente pode mudar depois de salvo."""
    import net_guard
    from urllib.parse import urlsplit
    url = (base_url or "").strip()
    if not url:
        raise IAError("Informe o endereço da API (ex.: https://api.exemplo.com/v1)")
    if urlsplit(url).scheme != "https":
        raise IAError("O endereço da API precisa começar com https://")
    try:
        host, port = net_guard._checar_esquema_e_host(url)
        await net_guard._validar_host(host, port)
    except net_guard.SSRFError as e:
        raise IAError(f"Endereço da API recusado: {e}")
    return url.rstrip("/")


def modelo_de(provedor: str, modelo_id: str) -> Modelo:
    p = PROVEDORES.get(provedor, {})
    for m in p.get("modelos", []):
        if m.id == modelo_id:
            return m
    modelo_id = (modelo_id or "").strip()
    if p.get("modelo_livre") and modelo_id and len(modelo_id) <= MAX_ID_MODELO \
            and not any(c.isspace() for c in modelo_id):
        # Preço desconhecido: o consumo é medido em tokens e o custo fica zero.
        return Modelo(modelo_id, modelo_id, 0.0, 0.0, 0.0, 0.0)
    raise IAError(f"Modelo desconhecido para {provedor}: {modelo_id}")


def modelo_padrao(provedor: str) -> str:
    """O primeiro da lista é o recomendado — a ordem do catálogo é a
    recomendação, não alfabética."""
    if provedor not in PROVEDORES:
        raise IAError(f"Provedor desconhecido: {provedor}")
    modelos = PROVEDORES[provedor].get("modelos", [])
    if not modelos:
        raise IAError("Informe o id do modelo")
    return modelos[0].id


def catalogo_publico() -> list[dict]:
    """O catálogo como a tela precisa dele. Sem segredo nenhum — é chamado por
    qualquer admin de empresa."""
    return [
        {
            "id": pid,
            "rotulo": p["rotulo"],
            "console_url": p["console_url"],
            # O começo esperado da credencial, só para o texto de ajuda do
            # campo. Não é segredo: é o que a documentação do provedor diz.
            "prefixo": p.get("prefixo_chave"),
            "modelo_livre": bool(p.get("modelo_livre")),
            "url_livre": bool(p.get("url_livre")),
            "gratuito": bool(p.get("gratuito")),
            "modelos": [
                {
                    "id": m.id,
                    "rotulo": m.rotulo,
                    "nota": m.nota,
                    "entrada": m.entrada,
                    "saida": m.saida,
                    # A tela projeta o custo mensal e precisa do preço de
                    # cache: assumir "10% da entrada" vale nos modelos atuais
                    # mas não é regra (o cache do gpt-4o custa 50%), e um dia
                    # a projeção mentiria em silêncio.
                    "cache_leitura": m.cache_leitura,
                }
                for m in p["modelos"]
            ],
        }
        for pid, p in PROVEDORES.items()
    ]


# ---------------------------------------------------------------------------
# Resposta
# ---------------------------------------------------------------------------

@dataclass(frozen=True)
class Ferramenta:
    """Uma ação que o modelo pode executar em vez de só descrever.

    `parametros` é JSON Schema, que é o formato que os dois provedores aceitam
    o que muda entre eles é só o envelope, e é por isso que ele fica aqui.
    """
    nome: str
    descricao: str
    parametros: dict


# Quantas voltas de ferramenta uma resposta pode dar. Uma consulta de agenda
# seguida da marcação são duas; a terceira é folga para o modelo corrigir um
# nome errado. Sem teto, um modelo confuso consulta a agenda indefinidamente e
# cada volta é uma chamada paga, e o contato fica sem resposta enquanto isso.
MAX_RODADAS_FERRAMENTA = 4


@dataclass
class Resposta:
    texto: str
    modelo: str
    tokens_entrada: int = 0
    tokens_saida: int = 0
    tokens_cache_leitura: int = 0
    tokens_cache_escrita: int = 0
    # Nomes das ferramentas que o modelo chegou a executar nesta resposta. Só
    # para registro: quem precisa do resultado já o recebeu pelo executor.
    ferramentas_usadas: list = field(default_factory=list)
    bruto: dict = field(default_factory=dict)

    def somar(self, outra: "Resposta"):
        """Acumula o consumo de mais uma volta do laço de ferramentas.

        Sem isto, uma resposta que consultou a agenda e marcou o horário
        cobraria só a última chamada, e a medição de consumo, que é o que
        fatura, ficaria menor que a fatura do provedor."""
        self.tokens_entrada += outra.tokens_entrada
        self.tokens_saida += outra.tokens_saida
        self.tokens_cache_leitura += outra.tokens_cache_leitura
        self.tokens_cache_escrita += outra.tokens_cache_escrita

    def custo_usd(self, modelo: Modelo) -> float:
        """Custo desta chamada. Separa cache de entrada normal porque a
        diferença é de 10x — somar tudo como entrada mostraria ao cliente um
        número que não bate com a fatura do provedor."""
        return (
            self.tokens_entrada * modelo.entrada
            + self.tokens_saida * modelo.saida
            + self.tokens_cache_leitura * modelo.cache_leitura
            + self.tokens_cache_escrita * modelo.cache_escrita
        ) / 1_000_000


# ---------------------------------------------------------------------------
# Chamada
# ---------------------------------------------------------------------------

# Teto de saída. Não é o tamanho esperado da resposta (~120 tokens numa
# resposta de atendimento) — é folga. Nos modelos GPT-5 o `max_output_tokens`
# inclui os tokens de raciocínio, que são invisíveis: um teto apertado é
# consumido inteiro pelo raciocínio e devolve resposta vazia com status
# `incomplete`. Sobra não custa nada, porque a cobrança é pelo que sai.
MAX_SAIDA = 2000


async def responder(
    provedor: str,
    chave_api: str,
    modelo_id: str,
    instrucoes: str,
    historico: list[dict],
    ferramentas: list | None = None,
    executar=None,
    base_url: str | None = None,
) -> Resposta:
    """Uma resposta do agente.

    `instrucoes` é o bloco estável (papel do agente, regras, base de
    conhecimento) — é ele que vai para o cache. `historico` é a conversa em
    formato `[{"role": "user"|"assistant", "content": "..."}]`, com a mensagem
    nova do contato por último.

    **O laço de ferramentas roda aqui dentro**, e não em quem chama. Foi a razão
    de este módulo existir desde o começo: ninguém mais no backend fala com um
    provedor de IA. Anthropic e OpenAI não só nomeiam os campos de forma
    diferente: eles devolvem a chamada em lugares diferentes da resposta e
    esperam o resultado de volta em formatos diferentes. Com o laço do lado de
    fora, `ai_agents` teria de conhecer os dois formatos, que é exatamente a
    duplicação que a camada evita.

    `executar` é `async (nome, argumentos) -> str`: quem chama sabe o que as
    ferramentas fazem, este módulo só sabe conversá-las com o provedor.
    """
    if not historico:
        raise IAError("Histórico vazio — não há o que responder")
    if ferramentas and executar is None:
        raise IAError("Ferramentas informadas sem executor")
    protocolo = (PROVEDORES.get(provedor) or {}).get("protocolo")
    if protocolo == "anthropic":
        return await _responder_anthropic(chave_api, modelo_id, instrucoes, historico,
                                          ferramentas, executar)
    if protocolo == "responses":
        return await _responder_openai(chave_api, modelo_id, instrucoes, historico,
                                       ferramentas, executar)
    if protocolo == "chat":
        url = base_url_de(provedor, base_url)
        if PROVEDORES[provedor].get("url_livre"):
            url = await validar_base_url(url)
        return await _responder_chat(url, chave_api, modelo_id, instrucoes, historico,
                                     ferramentas, executar)
    raise IAError(f"Provedor desconhecido: {provedor}")


async def _rodar_ferramenta(executar, nome: str, argumentos, usadas: list) -> str:
    """Executa uma ferramenta e devolve **sempre** texto para o modelo.

    Erro vira mensagem, nunca exceção: o modelo é capaz de se recuperar de
    "não existe serviço com esse nome" pedindo o nome certo ao contato, e
    derrubar a resposta inteira por isso deixaria a conversa sem nada, que é o
    caminho que este projeto nunca aceita.
    """
    usadas.append(nome)
    try:
        saida = await executar(nome, argumentos or {})
        return str(saida) if saida is not None else "(sem resultado)"
    except Exception as e:
        logger.warning("Ferramenta %s falhou: %s", nome, e)
        return f"ERRO: {e}"


def _bloco_anthropic(b) -> dict | None:
    """Reconstrói só os campos que a API aceita de volta.

    `model_dump()` do SDK carrega campos internos e nulos que mudam de versão
    para versão; devolvê-los na próxima chamada é o tipo de coisa que passa por
    meses e quebra num upgrade de biblioteca.
    """
    tipo = getattr(b, "type", None)
    if tipo == "text":
        return {"type": "text", "text": b.text}
    if tipo == "tool_use":
        return {"type": "tool_use", "id": b.id, "name": b.name, "input": b.input}
    return None


async def _responder_anthropic(chave_api, modelo_id, instrucoes, historico,
                               ferramentas=None, executar=None) -> Resposta:
    try:
        from anthropic import AsyncAnthropic
    except ImportError:
        raise IAError("SDK da Anthropic não instalado no servidor (pip install anthropic)")

    mensagens = list(historico)
    total = Resposta(texto="", modelo=modelo_id)
    extra = {}
    if ferramentas:
        extra["tools"] = [{"name": f.nome, "description": f.descricao,
                           "input_schema": f.parametros} for f in ferramentas]

    async with AsyncAnthropic(api_key=chave_api, max_retries=2) as client:
        for _ in range(MAX_RODADAS_FERRAMENTA):
            try:
                r = await client.messages.create(
                    model=modelo_id,
                    max_tokens=MAX_SAIDA,
                    # Lista de blocos, não string, porque `cache_control` só
                    # existe no formato de bloco. O prefixo mínimo para o cache
                    # pegar é de ~1024 tokens: instrução mais curta que isso
                    # simplesmente não é cacheada, sem erro nenhum, só não
                    # economiza.
                    system=[{
                        "type": "text",
                        "text": instrucoes,
                        "cache_control": {"type": "ephemeral"},
                    }],
                    messages=mensagens,
                    **extra,
                )
            except Exception as e:
                raise IAError(_traduz_erro(e))

            u = r.usage
            total.somar(Resposta(
                texto="", modelo=modelo_id,
                tokens_entrada=getattr(u, "input_tokens", 0) or 0,
                tokens_saida=getattr(u, "output_tokens", 0) or 0,
                tokens_cache_leitura=getattr(u, "cache_read_input_tokens", 0) or 0,
                tokens_cache_escrita=getattr(u, "cache_creation_input_tokens", 0) or 0,
            ))

            chamadas = [b for b in r.content if getattr(b, "type", None) == "tool_use"]
            if not chamadas:
                # `content` é uma união de blocos; filtrar por tipo é o que
                # impede um KeyError se o modelo passar a devolver outro.
                total.texto = "".join(
                    b.text for b in r.content if getattr(b, "type", None) == "text").strip()
                break

            mensagens.append({"role": "assistant",
                              "content": [b for b in (_bloco_anthropic(x) for x in r.content)
                                          if b]})
            mensagens.append({"role": "user", "content": [
                {"type": "tool_result", "tool_use_id": c.id,
                 "content": await _rodar_ferramenta(executar, c.name, c.input,
                                                    total.ferramentas_usadas)}
                for c in chamadas
            ]})

    if not total.texto:
        raise IAError("O modelo respondeu vazio")
    return total


async def _responder_openai(chave_api, modelo_id, instrucoes, historico,
                            ferramentas=None, executar=None) -> Resposta:
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise IAError("SDK da OpenAI não instalado no servidor (pip install openai)")

    entrada = list(historico)
    total = Resposta(texto="", modelo=modelo_id)
    extra = {}
    if ferramentas:
        extra["tools"] = [{"type": "function", "name": f.nome,
                           "description": f.descricao, "parameters": f.parametros}
                          for f in ferramentas]

    async with AsyncOpenAI(api_key=chave_api, max_retries=2) as client:
        for _ in range(MAX_RODADAS_FERRAMENTA):
            try:
                r = await client.responses.create(
                    model=modelo_id,
                    # `instructions` fica fora do `input` e é estável entre as
                    # chamadas: é assim que o cache automático da OpenAI pega o
                    # prefixo. Enfiar as instruções como primeira mensagem do
                    # `input` funcionaria, mas embaralharia o prefixo com o
                    # histórico, que muda a cada turno.
                    instructions=instrucoes,
                    input=entrada,
                    max_output_tokens=MAX_SAIDA,
                    # Os GPT-5 são modelos de raciocínio. Para atendimento isso é
                    # custo e latência sem retorno: a resposta é curta e a decisão
                    # é simples. `low` é o piso útil.
                    reasoning={"effort": "low"},
                    **extra,
                )
            except Exception as e:
                raise IAError(_traduz_erro(e))

            u = getattr(r, "usage", None)
            cacheados = getattr(getattr(u, "input_tokens_details", None), "cached_tokens", 0) or 0
            bruta = getattr(u, "input_tokens", 0) or 0
            total.somar(Resposta(
                texto="", modelo=modelo_id,
                # A OpenAI reporta `input_tokens` como o total, com os cacheados
                # *dentro* dele. A Anthropic reporta os dois separados. Descontar
                # aqui é o que faz `custo_usd` valer para os dois sem um `if` de
                # provedor.
                tokens_entrada=max(bruta - cacheados, 0),
                tokens_cache_leitura=cacheados,
                tokens_saida=getattr(u, "output_tokens", 0) or 0,
            ))

            if getattr(r, "status", None) == "incomplete":
                motivo = getattr(getattr(r, "incomplete_details", None), "reason", "desconhecido")
                raise IAError(f"Resposta truncada pelo provedor ({motivo})")

            chamadas = [o for o in (getattr(r, "output", None) or [])
                        if getattr(o, "type", None) == "function_call"]
            if not chamadas:
                total.texto = (getattr(r, "output_text", "") or "").strip()
                break

            # A volta seguinte manda **só** os resultados, referenciando a
            # resposta anterior por id. A alternativa: reenviar os itens de
            # saída: obrigaria a serializar também os itens de raciocínio, que
            # nos GPT-5 vêm cifrados e são obrigatórios para o modelo continuar
            # de onde parou. `previous_response_id` é o caminho que o provedor
            # mantém, e de quebra não reenvia nada já enviado.
            extra["previous_response_id"] = r.id
            entrada = [
                {"type": "function_call_output", "call_id": c.call_id,
                 "output": await _rodar_ferramenta(
                     executar, c.name, _json(c.arguments), total.ferramentas_usadas)}
                for c in chamadas
            ]

    if not total.texto:
        raise IAError("O modelo respondeu vazio")
    return total


# Raciocínio exposto no próprio texto (Qwen3, DeepSeek R1 e afins servidos por
# Groq/OpenRouter). Sem o corte, o contato receberia o rascunho do modelo.
_PENSAMENTO = re.compile(r"<think>.*?</think>", re.S | re.I)


async def _responder_chat(base_url, chave_api, modelo_id, instrucoes, historico,
                          ferramentas=None, executar=None) -> Resposta:
    """Chat Completions compatível com OpenAI: Groq, Gemini, OpenRouter,
    DeepSeek, Mistral, xAI e qualquer endpoint que imite a OpenAI.

    Não usa a Responses API porque quase nenhum desses provedores a implementa;
    Chat Completions é o denominador comum. A instrução vai como primeira
    mensagem `system`, sempre igual, que é o que os provedores com cache
    automático de prefixo (Gemini, DeepSeek, Groq) precisam para cachear.
    """
    try:
        from openai import AsyncOpenAI
    except ImportError:
        raise IAError("SDK da OpenAI não instalado no servidor (pip install openai)")

    mensagens = [{"role": "system", "content": instrucoes}, *historico]
    total = Resposta(texto="", modelo=modelo_id)
    extra = {}
    if ferramentas:
        extra["tools"] = [{"type": "function", "function": {
            "name": f.nome, "description": f.descricao, "parameters": f.parametros}}
            for f in ferramentas]

    async with AsyncOpenAI(api_key=chave_api, base_url=base_url, max_retries=2,
                           timeout=60) as client:
        for _ in range(MAX_RODADAS_FERRAMENTA):
            try:
                r = await client.chat.completions.create(
                    model=modelo_id, messages=mensagens, max_tokens=MAX_SAIDA, **extra)
            except Exception as e:
                raise IAError(_traduz_erro(e))

            u = getattr(r, "usage", None)
            cacheados = getattr(getattr(u, "prompt_tokens_details", None),
                                "cached_tokens", 0) or 0
            bruta = getattr(u, "prompt_tokens", 0) or 0
            total.somar(Resposta(
                texto="", modelo=modelo_id,
                # Mesmo desconto da OpenAI: `prompt_tokens` já inclui os cacheados.
                tokens_entrada=max(bruta - cacheados, 0),
                tokens_cache_leitura=cacheados,
                tokens_saida=getattr(u, "completion_tokens", 0) or 0,
            ))

            escolhas = getattr(r, "choices", None) or []
            if not escolhas:
                raise IAError("O provedor devolveu uma resposta sem conteúdo")
            msg = escolhas[0].message
            chamadas = getattr(msg, "tool_calls", None) or []
            if not chamadas:
                texto = _PENSAMENTO.sub("", getattr(msg, "content", None) or "").strip()
                if not texto and getattr(escolhas[0], "finish_reason", None) == "length":
                    raise IAError("Resposta truncada pelo provedor (length)")
                total.texto = texto
                break

            mensagens.append({
                "role": "assistant",
                "content": getattr(msg, "content", None),
                "tool_calls": [{"id": c.id, "type": "function",
                                "function": {"name": c.function.name,
                                             "arguments": c.function.arguments or "{}"}}
                               for c in chamadas],
            })
            for c in chamadas:
                mensagens.append({
                    "role": "tool", "tool_call_id": c.id,
                    "content": await _rodar_ferramenta(
                        executar, c.function.name, _json(c.function.arguments),
                        total.ferramentas_usadas),
                })

    if not total.texto:
        raise IAError("O modelo respondeu vazio")
    return total


def _json(argumentos) -> dict:
    """Os argumentos da chamada, que a OpenAI entrega como texto JSON.

    Modelo que inventa JSON quebrado existe; devolver `{}` faz a ferramenta
    reclamar do argumento que falta, e o modelo tenta de novo: melhor que
    derrubar a resposta.
    """
    if isinstance(argumentos, dict):
        return argumentos
    try:
        return json.loads(argumentos or "{}")
    except (ValueError, TypeError):
        logger.warning("Argumentos de ferramenta ilegíveis: %r", argumentos)
        return {}


def _traduz_erro(e: Exception) -> str:
    """Mensagem que o cliente entende, a partir do erro do SDK.

    O texto vai direto para a tela dele, então "401 Unauthorized" não serve:
    quem cadastrou a chave precisa saber se o problema é a chave, o saldo ou
    o volume."""
    nome = type(e).__name__
    status = getattr(e, "status_code", None)
    # O Gemini responde chave inválida com 400 ("API key not valid"), não 401.
    if status == 401 or "Authentication" in nome or (
            status == 400 and "api key" in str(e).lower()):
        return "Chave de API recusada pelo provedor. Confira se copiou a chave inteira e se ela não foi revogada."
    if status == 402 or "credit" in str(e).lower() or "quota" in str(e).lower():
        return "A conta no provedor está sem saldo ou sem cota. Adicione créditos e tente de novo."
    if status == 404:
        # Na Groq e no OpenRouter é quase sempre modelo descontinuado ou fora
        # do plano da conta, não chave errada: a saída é escolher outro.
        return ("O provedor não encontrou este modelo para a sua conta: ele pode ter sido "
                "descontinuado ou não estar liberado no seu plano. Use \"Buscar modelos da "
                "conta\" e escolha um da lista.")
    if status == 429 or "RateLimit" in nome:
        return "O provedor está limitando as chamadas desta conta no momento. Tente novamente em instantes."
    if status and status >= 500:
        return "O provedor está fora do ar no momento. Tente novamente em instantes."
    logger.warning("Erro não mapeado do provedor de IA: %s: %s", nome, e)
    return f"Falha ao falar com o provedor de IA ({nome})."


# ---------------------------------------------------------------------------
# Validação (o "testar conexão" da tela)
# ---------------------------------------------------------------------------

async def validar(provedor: str, chave_api: str, modelo_id: str,
                  base_url: str | None = None) -> Resposta:
    """Chamada mínima só para provar que a credencial funciona *neste* modelo.

    Vale mais que uma checagem de formato: uma chave pode ser válida e mesmo
    assim não ter saldo, não ter acesso ao modelo escolhido ou estar revogada —
    e o cliente precisa descobrir isso agora, com a tela aberta, não na
    primeira mensagem de um contato real.

    Custa fração de centavo: é o menor prompt possível e uma resposta de uma
    palavra.
    """
    return await responder(
        provedor,
        chave_api,
        modelo_id,
        instrucoes="Responda exatamente: ok",
        historico=[{"role": "user", "content": "ok"}],
        base_url=base_url,
    )


async def validar_conexao(conexao: dict) -> Resposta:
    """Mesma coisa, a partir do documento guardado (decifrando a chave)."""
    return await validar(
        conexao["provedor"],
        ai_credentials.decifrar(conexao.get("credencial") or {}),
        conexao["modelo"],
        base_url=conexao.get("base_url"),
    )



# ---------------------------------------------------------------------------
# Modelos da conta (o "Buscar modelos" da tela)
# ---------------------------------------------------------------------------
#
# A lista sugerida do catálogo envelhece: no primeiro uso real (30/09/2026) os
# dois modelos sugeridos para Groq e OpenRouter respondiam 404, um porque saiu
# do plano grátis e outro porque deixou de existir. A lista que vale é a que o
# provedor devolve para aquela chave, e todos os provedores aqui expõem
# `GET /models` no formato da OpenAI.

# O que não é modelo de conversa. A lista do provedor mistura embeddings,
# transcrição, voz, moderação e imagem, e escolher um deles daria erro na
# primeira mensagem do contato.
_NAO_CONVERSA = ("embed", "whisper", "tts", "transcribe", "moderation", "guard",
                 "safeguard", "dall-e", "image", "audio", "realtime", "orpheus",
                 "rerank", "search")

MAX_MODELOS_LISTADOS = 500


async def listar_modelos(provedor: str, chave_api: str, base_url: str | None = None) -> list[str]:
    """Ids de modelo de conversa que esta chave alcança, em ordem alfabética.

    Levanta `IAError` com texto para a tela: é a mesma chave que vai ser
    validada ao salvar, então um 401 aqui já diz o que vai dar errado depois.
    """
    p = PROVEDORES.get(provedor)
    if not p:
        raise IAError(f"Provedor desconhecido: {provedor}")
    # Só onde o modelo é livre: em Anthropic e OpenAI a lista é fechada, e
    # mostrar ids que `modelo_de` recusaria ao salvar só confundiria.
    if not p.get("modelo_livre") or p.get("protocolo") != "chat":
        raise IAError("Este provedor usa a lista de modelos da própria 4turismo")
    try:
        from openai import AsyncOpenAI
        url = base_url_de(provedor, base_url)
        if p.get("url_livre"):
            url = await validar_base_url(url)
        async with AsyncOpenAI(api_key=chave_api, base_url=url, max_retries=1,
                               timeout=30) as client:
            pagina = await client.models.list()
            ids = [m.id for m in pagina.data]
    except IAError:
        raise
    except ImportError:
        raise IAError("SDK da OpenAI não instalado no servidor (pip install openai)")
    except Exception as e:
        raise IAError(_traduz_erro(e))

    ids = sorted({i for i in ids if i and not any(x in i.lower() for x in _NAO_CONVERSA)})
    return ids[:MAX_MODELOS_LISTADOS]
