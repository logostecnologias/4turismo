"""Teste da credencial de IA: cifra, validacao antes de gravar, resolucao
(chave do agente x chave da plataforma), cota do plano e permissoes.

A credencial e do AGENTE, nao da empresa — um cliente pode ter uma chave da
Anthropic no agente de suporte e uma da OpenAI no de vendas.

O provedor nunca e chamado de verdade aqui — `ai_client.validar` e substituido.
O que se testa e a regra em volta dele, que e onde moram os erros: gravar uma
chave que nao funciona, vazar o segredo para a tela, deixar a empresa B
alcancar o agente da empresa A, ou contar consumo errado.
"""
import asyncio
import inspect
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

from cryptography.fernet import Fernet

# A chave mestra precisa existir antes de qualquer cifra. Gerada aqui para o
# teste nao depender do .env da maquina.
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()

from fakedb import FakeDB
from fastapi import HTTPException

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import ai_client
import ai_credentials
import ai_runtime
import routes.ai as rai

_MODULOS_FAKE = (rai,)


def _conectar_fake():
    db_mod.get_db = lambda: DB
    for _m in _MODULOS_FAKE:
        _m.get_db = lambda: DB


TENANT_A, TENANT_B = "t-a", "t-b"
ADMIN_A = {"id": "u-a", "tenant_id": TENANT_A, "role": "tenant_admin",
           "name": "Ana", "email": "ana@x.com"}
ADMIN_B = {"id": "u-b", "tenant_id": TENANT_B, "role": "tenant_admin",
           "name": "Bruno", "email": "bruno@x.com"}

CHAVE_ANTHROPIC = "sk-ant-api03-" + "K" * 40
CHAVE_OPENAI = "sk-proj-" + "Z" * 40

ok = 0


def check(nome, cond, extra=""):
    global ok
    if cond:
        ok += 1
        print(f"  OK  {nome}")
    else:
        print(f"  FALHOU  {nome}  -> {extra}")
        raise AssertionError(nome)


async def erro(coro):
    try:
        await coro
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return None, None


class _ValidacaoFalsa:
    """Substitui `ai_client.validar`. Guarda com que foi chamada, para o teste
    poder afirmar que a validacao aconteceu *antes* da gravacao."""

    def __init__(self):
        self.chamadas = []
        self.erro = None

    async def __call__(self, provedor, chave, modelo, base_url=None):
        self.chamadas.append((provedor, chave, modelo))
        self.base_url = base_url
        if self.erro:
            raise ai_client.IAError(self.erro)
        return ai_client.Resposta(texto="ok", modelo=modelo, tokens_entrada=10,
                                  tokens_saida=2)


VALIDAR = _ValidacaoFalsa()


async def semear():
    DB._cols.clear()
    VALIDAR.chamadas.clear()
    VALIDAR.erro = None
    os.environ.pop("AI_PLATFORM_KEY", None)
    await DB.tenants.insert_one({"id": TENANT_A})
    await DB.tenants.insert_one({"id": TENANT_B})


# ---------------------------------------------------------------------------

async def etapa_cifra():
    print("\n[cifra da credencial]")
    bloco = ai_credentials.cifrar(CHAVE_ANTHROPIC)
    check("round-trip devolve a chave original",
          ai_credentials.decifrar(bloco) == CHAVE_ANTHROPIC)
    check("o segredo nao aparece no bloco gravado",
          CHAVE_ANTHROPIC not in str(bloco), bloco)
    check("a mascara mostra so prefixo e 4 finais",
          bloco["mascara"] == "sk-ant-…KKKK", bloco["mascara"])
    check("a versao da chave mestra fica gravada",
          bloco["chave_versao"] == ai_credentials.CHAVE_VERSAO)

    # Chave mestra trocada: tem que dizer o que houve, nao devolver lixo.
    original = os.environ["CREDENTIALS_KEY"]
    os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()
    try:
        ai_credentials.decifrar(bloco)
        check("chave mestra errada e recusada", False, "decifrou o que nao devia")
    except ai_credentials.CredencialError as e:
        check("chave mestra errada da erro explicito", "CREDENTIALS_KEY" in str(e), e)
    os.environ["CREDENTIALS_KEY"] = original

    # Versao antiga: pede reconexao em vez de falhar como "credencial invalida".
    antigo = {**bloco, "chave_versao": 0}
    try:
        ai_credentials.decifrar(antigo)
        check("versao anterior e recusada", False)
    except ai_credentials.CredencialError as e:
        # A mensagem serve as duas credenciais que passam por aqui (chave de IA
        # e token da Cloud API), entao ela pede para cadastrar de novo em vez de
        # falar em "reconectar a conta de IA".
        check("versao anterior pede cadastro de novo", "cadastrá-la de novo" in str(e), e)


async def etapa_salvar():
    print()
    print("[gravar credencial do agente]")
    await semear()
    # Provedor desconhecido nem chega perto da rede.
    st, det = await erro(rai.criar_agente(
        rai.AgenteIn(nome="X", provedor="inexistente", chave_api="sk-x"), ADMIN_A))
    check("provedor desconhecido e recusado", st == 400, (st, det))

    # Chave com o prefixo do outro provedor e barrada ANTES da chamada.
    VALIDAR.chamadas.clear()
    st, det = await erro(rai.criar_agente(
        rai.AgenteIn(nome="X", provedor="anthropic", chave_api=CHAVE_OPENAI), ADMIN_A))
    check("chave da openai no campo da anthropic e recusada", st == 400, (st, det))
    check("sem gastar chamada no provedor", VALIDAR.chamadas == [], VALIDAR.chamadas)

    # Caminho feliz: valida antes de gravar.
    VALIDAR.chamadas.clear()
    agente = await rai.criar_agente(rai.AgenteIn(
        nome="Bia", tipo="suporte", provedor="anthropic",
        modelo="claude-haiku-4-5", chave_api=CHAVE_ANTHROPIC), ADMIN_A)
    check("validou antes de gravar", len(VALIDAR.chamadas) == 1, VALIDAR.chamadas)
    check("gravou como ok", agente["credencial_status"] == "ok", agente)
    check("a chave em claro nao volta para a tela",
          CHAVE_ANTHROPIC not in str(agente), agente)
    check("so a mascara volta", agente["mascara"].startswith("sk-ant-")
          and agente["mascara"].endswith("KKKK"), agente["mascara"])

    # Chave recusada pelo provedor nao pode ser gravada.
    VALIDAR.erro = ai_client.IAError("Chave de API recusada pelo provedor.")
    st, det = await erro(rai.criar_agente(rai.AgenteIn(
        nome="Ruim", provedor="openai", chave_api=CHAVE_OPENAI), ADMIN_A))
    check("chave recusada nao e gravada", st == 400, (st, det))
    check("com o motivo do provedor", "recusada" in det, det)
    VALIDAR.erro = None
    restantes = await DB.ai_agents.find({"tenant_id": TENANT_A}).to_list(10)
    check("e o agente ruim nao ficou no banco", len(restantes) == 1, len(restantes))

    globals()["AGENTE_A"] = agente


async def etapa_isolamento():
    print()
    print("[uma empresa nao alcanca o agente da outra]")
    agente_b = await rai.criar_agente(rai.AgenteIn(
        nome="Do Bruno", provedor="openai", modelo="gpt-5-nano",
        chave_api=CHAVE_OPENAI), ADMIN_B)

    vistos_b = await rai.listar_agentes(ADMIN_B)
    check("B ve so o agente dele", [a["id"] for a in vistos_b] == [agente_b["id"]], vistos_b)
    vistos_a = await rai.listar_agentes(ADMIN_A)
    check("A ve so o dele", [a["id"] for a in vistos_a] == [AGENTE_A["id"]], vistos_a)

    # B nao alcanca o agente de A por id — o tenant_id entra no filtro de todas.
    st, _ = await erro(rai.ver_prompt(AGENTE_A["id"], ADMIN_B))
    check("B nao le o prompt do agente de A", st == 404, st)
    st, _ = await erro(rai.atualizar_agente(
        AGENTE_A["id"], rai.AgenteIn(nome="invadido"), ADMIN_B))
    check("B nao edita o agente de A", st == 404, st)
    st, _ = await erro(rai.remover_agente(AGENTE_A["id"], ADMIN_B))
    check("B nao apaga o agente de A", st == 404, st)
    st, _ = await erro(rai.testar_agente(
        AGENTE_A["id"], rai.TesteIn(mensagens=[{"role": "user", "content": "oi"}]), ADMIN_B))
    check("B nao testa (nem gasta a chave de) A", st == 404, st)

    sobrou = await DB.ai_agents.find_one({"id": AGENTE_A["id"]})
    check("o agente de A segue intacto", sobrou and sobrou["nome"] == "Bia", sobrou)


async def etapa_teste_de_conexao():
    print()
    print("[a chave pode morrer depois de salva]")

    async def responder_ok(db, tenant_id, agente, instrucoes, historico, **kw):
        return ai_client.Resposta(texto="ok", modelo="claude-haiku-4-5")

    async def responder_morto(db, tenant_id, agente, instrucoes, historico, **kw):
        raise ai_client.IAError("A conta no provedor esta sem saldo.")

    original = ai_runtime.responder_por_agente
    rai.ai_runtime.responder_por_agente = responder_ok
    try:
        msg = rai.TesteIn(mensagens=[{"role": "user", "content": "oi"}])
        r = await rai.testar_agente(AGENTE_A["id"], msg, ADMIN_A)
        check("teste responde quando a chave funciona", r["texto"] == "ok", r)

        rai.ai_runtime.responder_por_agente = responder_morto
        st, det = await erro(rai.testar_agente(AGENTE_A["id"], msg, ADMIN_A))
        check("chave morta devolve 400 com o motivo", st == 400 and "saldo" in det, (st, det))
        doc = await DB.ai_agents.find_one({"id": AGENTE_A["id"]})
        check("e o agente fica marcado como quebrado",
              doc["credencial_status"] == "erro", doc.get("credencial_status"))
        check("guardando o motivo para a tela", "saldo" in (doc.get("credencial_erro") or ""))

        rai.ai_runtime.responder_por_agente = responder_ok
        await rai.testar_agente(AGENTE_A["id"], msg, ADMIN_A)
        doc = await DB.ai_agents.find_one({"id": AGENTE_A["id"]})
        check("e desmarca quando volta a funcionar",
              doc["credencial_status"] == "ok", doc.get("credencial_status"))
    finally:
        rai.ai_runtime.responder_por_agente = original


async def etapa_resolucao_de_credencial():
    print("\n[de quem e a conta]")
    agente_a = await DB.ai_agents.find_one({"id": AGENTE_A["id"]}, {"_id": 0})
    sem_chave = {"id": "ag-sem", "nome": "Sem chave", "tenant_id": TENANT_B}

    # O agente de A tem chave propria: e a dela que responde.
    cred = await ai_runtime.resolver_credencial(DB, TENANT_A, agente_a)
    check("empresa com conexao usa a conta dela",
          cred["origem"] == ai_runtime.ORIGEM_CLIENTE, cred["origem"])
    check("e a chave decifrada e a certa", cred["chave"] == CHAVE_ANTHROPIC)

    # Agente sem chave e sem chave de plataforma: erro claro.
    st, det = await erro(ai_runtime.resolver_credencial(DB, TENANT_B, sem_chave))
    check("sem chave e sem plataforma da 400", st == 400, (st, det))
    check("dizendo qual agente esta sem chave", "Sem chave" in det, det)

    # Com chave de plataforma configurada, a B passa a ter rede de seguranca.
    os.environ["AI_PLATFORM_KEY"] = "sk-plataforma"
    os.environ["AI_PLATFORM_PROVIDER"] = "openai"
    cred = await ai_runtime.resolver_credencial(DB, TENANT_B, sem_chave)
    check("sem chave propria cai na plataforma",
          cred["origem"] == ai_runtime.ORIGEM_PLATAFORMA, cred)
    check("com o modelo padrao do provedor da plataforma",
          cred["modelo"] == ai_client.modelo_padrao("openai"), cred["modelo"])

    # A chave do agente continua tendo precedencia sobre a plataforma.
    cred = await ai_runtime.resolver_credencial(DB, TENANT_A, agente_a)
    check("chave do agente tem precedencia",
          cred["origem"] == ai_runtime.ORIGEM_CLIENTE, cred["origem"])

    # Credencial marcada como quebrada nao e usada: cai na plataforma em vez de
    # insistir numa chave que ja se sabe que nao funciona.
    cred = await ai_runtime.resolver_credencial(
        DB, TENANT_A, {**agente_a, "credencial_status": "erro"})
    check("credencial marcada como quebrada nao e reusada",
          cred["origem"] == ai_runtime.ORIGEM_PLATAFORMA, cred["origem"])

    os.environ.pop("AI_PLATFORM_PROVIDER", None)


async def etapa_medicao():
    print("\n[medicao de consumo]")
    resposta = ai_client.Resposta(
        texto="oi", modelo="claude-haiku-4-5",
        tokens_entrada=330, tokens_saida=120,
        tokens_cache_leitura=1500, tokens_cache_escrita=0)

    await ai_runtime.registrar_uso(DB, TENANT_A, ai_runtime.ORIGEM_CLIENTE,
                                   "anthropic", "claude-haiku-4-5", resposta)
    consumo = await ai_runtime.consumo_do_mes(DB, TENANT_A)
    check("uma chamada registrada", consumo["chamadas"] == 1, consumo)
    check("tokens somados de todas as categorias",
          consumo["tokens"] == 330 + 120 + 1500, consumo)
    esperado = round((330 * 1.00 + 120 * 5.00 + 1500 * 0.10) / 1_000_000, 4)
    check("custo bate com a tabela de precos",
          abs(consumo["custo_usd"] - esperado) < 1e-6, (consumo["custo_usd"], esperado))

    # Segunda chamada acumula no mesmo documento do dia.
    await ai_runtime.registrar_uso(DB, TENANT_A, ai_runtime.ORIGEM_CLIENTE,
                                   "anthropic", "claude-haiku-4-5", resposta)
    consumo = await ai_runtime.consumo_do_mes(DB, TENANT_A)
    check("segunda chamada acumula", consumo["chamadas"] == 2, consumo)
    check("um documento por dia/modelo, nao um por chamada",
          await DB.ai_usage.count_documents({"tenant_id": TENANT_A}) == 1)

    # O consumo de A nao vaza para B.
    check("consumo e por empresa",
          (await ai_runtime.consumo_do_mes(DB, TENANT_B))["chamadas"] == 0)

    # Falha ao medir nao pode derrubar a resposta ao contato.
    await ai_runtime.registrar_uso(DB, TENANT_A, ai_runtime.ORIGEM_CLIENTE,
                                   "provedor-que-nao-existe", "modelo-x", resposta)
    check("medicao com modelo desconhecido nao levanta", True)


async def etapa_cota_do_plano():
    print("\n[cota do plano]")
    await DB.plans.insert_one({"id": "p-1", "nome": "Essencial", "max_tokens_ia_mes": 1000})
    await DB.tenants.update_one({"id": TENANT_B}, {"$set": {"plan_id": "p-1"}})
    os.environ["AI_PLATFORM_KEY"] = "sk-plataforma"
    # Agente sem chave propria: e ele que consome a cota do plano.
    sem_chave = {"id": "ag-sem", "nome": "Sem chave", "tenant_id": TENANT_B}
    agente_a = await DB.ai_agents.find_one({"id": AGENTE_A["id"]}, {"_id": 0})

    # Abaixo do teto: passa.
    cred = await ai_runtime.resolver_credencial(DB, TENANT_B, sem_chave)
    check("abaixo do teto a plataforma responde",
          cred["origem"] == ai_runtime.ORIGEM_PLATAFORMA)

    # Estoura o teto.
    grande = ai_client.Resposta(texto="x", modelo="gpt-5-nano",
                                tokens_entrada=2000, tokens_saida=0)
    await ai_runtime.registrar_uso(DB, TENANT_B, ai_runtime.ORIGEM_PLATAFORMA,
                                   "openai", "gpt-5-nano", grande)
    st, det = await erro(ai_runtime.resolver_credencial(DB, TENANT_B, sem_chave))
    check("acima do teto o plano barra", st == 403, (st, det))
    check("a mensagem sugere usar chave propria", "chave de IA propria" in det.replace("ó", "o"), det)

    # Agente com chave propria nao tem teto nosso, mesmo com a empresa estourada.
    await DB.tenants.update_one({"id": TENANT_A}, {"$set": {"plan_id": "p-1"}})
    await ai_runtime.registrar_uso(DB, TENANT_A, ai_runtime.ORIGEM_CLIENTE,
                                   "openai", "gpt-5-nano", grande)
    cred = await ai_runtime.resolver_credencial(DB, TENANT_A, agente_a)
    check("chave propria nao tem teto do plano",
          cred["origem"] == ai_runtime.ORIGEM_CLIENTE, cred["origem"])

    # Teto 0 = ilimitado.
    await DB.plans.update_one({"id": "p-1"}, {"$set": {"max_tokens_ia_mes": 0}})
    cred = await ai_runtime.resolver_credencial(DB, TENANT_B, sem_chave)
    check("teto 0 significa ilimitado",
          cred["origem"] == ai_runtime.ORIGEM_PLATAFORMA, cred)

    os.environ.pop("AI_PLATFORM_KEY", None)


async def etapa_barreira_de_permissao():
    print("\n[barreira de permissao]")
    # Chamar a funcao da rota direto NAO executa o Depends — a guarda so pode
    # ser conferida onde ela e declarada, na assinatura do endpoint.
    import auth
    endpoints = [rai.listar_provedores, rai.consumo, rai.catalogo_de_agentes,
                 rai.listar_agentes, rai.criar_agente, rai.atualizar_agente,
                 rai.remover_agente, rai.testar_agente, rai.ver_prompt]
    for fn in endpoints:
        deps = [p.default.dependency for p in inspect.signature(fn).parameters.values()
                if hasattr(p.default, "dependency")]
        check(f"{fn.__name__} exige tenant_admin",
              auth.require_tenant_admin in deps, deps)

    # E a guarda em si funciona.
    st, _ = await erro(auth.require_tenant_admin(
        {"id": "x", "role": "agent", "tenant_id": TENANT_A}))
    check("atendente e barrado por require_tenant_admin", st == 403, st)
    st, _ = await erro(auth.require_tenant_admin({"id": "x", "role": "tenant_admin"}))
    check("admin sem tenant e barrado", st == 403, st)


async def etapa_catalogo():
    print("\n[catalogo]")
    cat = ai_client.catalogo_publico()
    ids = {c["id"] for c in cat}
    check("os provedores aparecem", {"anthropic", "openai", "groq", "gemini",
                                     "compativel"} <= ids, ids)
    for prov in cat:
        if not prov["url_livre"]:
            check(f"{prov['id']} tem link do console", prov["console_url"].startswith("https://"))
        for m in prov["modelos"]:
            check(f"{m['id']} expoe preco de cache", "cache_leitura" in m, m)
            # Igual quando o provedor nao da desconto de cache (Mistral, Llama na Groq).
            check(f"{m['id']} tem preco de cache nao maior que o de entrada",
                  m["cache_leitura"] <= m["entrada"], m)
    check("o catalogo nao carrega segredo nenhum",
          "prefixo_chave" not in str(cat) and "chave" not in str(cat).lower())


CHAVE_GROQ = "gsk_" + "G" * 48


async def etapa_provedores_compativeis():
    print("\n[provedores compativeis com OpenAI]")
    await semear()

    # Prefixo conferido tambem aqui, antes de gastar chamada.
    VALIDAR.chamadas.clear()
    st, det = await erro(rai.criar_agente(
        rai.AgenteIn(nome="G", provedor="groq", chave_api=CHAVE_OPENAI), ADMIN_A))
    check("chave da openai no campo da groq e recusada", st == 400 and "gsk_" in det, det)
    check("sem chamada ao provedor", VALIDAR.chamadas == [])

    # Modelo fora da lista sugerida e aceito nos provedores de modelo livre.
    ag = await rai.criar_agente(rai.AgenteIn(
        nome="Groq", provedor="groq", modelo="qwen/qwen3-32b", chave_api=CHAVE_GROQ), ADMIN_A)
    check("modelo livre aceito na groq", ag["modelo"] == "qwen/qwen3-32b", ag)
    check("com a mascara da chave", ag["mascara"].startswith("gsk_"), ag["mascara"])
    check("sem endereco proprio", ag["base_url"] is None, ag.get("base_url"))

    # ... mas nao em anthropic/openai, que tem lista fechada.
    st, _ = await erro(rai.criar_agente(rai.AgenteIn(
        nome="X", provedor="openai", modelo="gpt-inventado", chave_api=CHAVE_OPENAI), ADMIN_A))
    check("modelo inventado recusado na openai", st == 400, st)
    st, _ = await erro(rai.criar_agente(rai.AgenteIn(
        nome="X", provedor="groq", modelo="tem espaco", chave_api=CHAVE_GROQ), ADMIN_A))
    check("id de modelo com espaco recusado", st == 400, st)

    # Compativel: endereco obrigatorio, https e publico.
    for url, motivo in [(None, "sem endereco"), ("http://8.8.8.8/v1", "http"),
                        ("https://127.0.0.1/v1", "loopback"),
                        ("https://10.0.0.5/v1", "rede privada"),
                        ("https://169.254.169.254/v1", "metadados de nuvem")]:
        VALIDAR.chamadas.clear()
        st, det = await erro(rai.criar_agente(rai.AgenteIn(
            nome="C", provedor="compativel", modelo="llama", base_url=url,
            chave_api="qualquer-coisa-123"), ADMIN_A))
        check(f"endereco recusado: {motivo}", st == 400, (st, det))
        check(f"e a chave nao saiu do servidor ({motivo})", VALIDAR.chamadas == [])

    ag = await rai.criar_agente(rai.AgenteIn(
        nome="Proprio", provedor="compativel", modelo="meu-modelo",
        base_url="https://8.8.8.8/v1/", chave_api="tok-" + "x" * 30), ADMIN_A)
    check("compativel com endereco publico e gravado", ag["base_url"] == "https://8.8.8.8/v1", ag)
    check("e a validacao recebeu o endereco", VALIDAR.base_url == "https://8.8.8.8/v1")

    atual = await DB.ai_agents.find_one({"id": ag["id"]}, {"_id": 0})
    cred = await ai_runtime.resolver_credencial(DB, TENANT_A, atual)
    check("o runtime carrega o endereco", cred["base_url"] == "https://8.8.8.8/v1", cred)

    # Trocar o endereco sem reenviar a chave: recusado.
    st, det = await erro(rai.atualizar_agente(ag["id"], rai.AgenteIn(
        nome="Proprio", provedor="compativel", modelo="meu-modelo",
        base_url="https://1.1.1.1/v1"), ADMIN_A))
    check("trocar o endereco exige a chave de novo", st == 400 and "endereço" in det, (st, det))
    # Mesmo endereco: reaproveita a guardada.
    r = await rai.atualizar_agente(ag["id"], rai.AgenteIn(
        nome="Renomeado", provedor="compativel", modelo="meu-modelo",
        base_url="https://8.8.8.8/v1"), ADMIN_A)
    check("mesmo endereco reaproveita a chave", r["nome"] == "Renomeado" and r["tem_chave"], r)


class _Obj:
    def __init__(self, **kw):
        self.__dict__.update(kw)


class _ClienteChatFalso:
    """Imita `AsyncOpenAI` o bastante para o laco de Chat Completions: primeira
    volta pede uma ferramenta, segunda responde texto."""
    ultimo = None

    def __init__(self, api_key=None, base_url=None, **kw):
        self.base_url = base_url
        self.pedidos = []
        _ClienteChatFalso.ultimo = self
        self.chat = _Obj(completions=_Obj(create=self._create))
        self.models = _Obj(list=self._listar)

    async def _listar(self):
        ids = ["openai/gpt-oss-120b", "whisper-large-v3", "meta-llama/llama-prompt-guard-2-86m",
               "nomic-embed-text", "qwen/qwen3.8-27b", "openai/gpt-oss-120b"]
        return _Obj(data=[_Obj(id=i) for i in ids])

    async def __aenter__(self):
        return self

    async def __aexit__(self, *a):
        return False

    async def _create(self, **kw):
        import copy
        self.pedidos.append(copy.deepcopy(kw))
        uso = _Obj(prompt_tokens=100, completion_tokens=10,
                   prompt_tokens_details=_Obj(cached_tokens=40))
        if len(self.pedidos) == 1:
            chamada = _Obj(id="call_1",
                           function=_Obj(name="consultar", arguments='{"dia": "amanha"}'))
            msg = _Obj(content=None, tool_calls=[chamada])
        else:
            msg = _Obj(content="<think>rascunho</think>Tem as 14h.", tool_calls=None)
        return _Obj(usage=uso, choices=[_Obj(message=msg, finish_reason="stop")])


async def etapa_laco_chat_completions():
    print("\n[laco de ferramentas no Chat Completions]")
    import openai
    original = openai.AsyncOpenAI
    openai.AsyncOpenAI = _ClienteChatFalso
    recebidos = []

    async def executar(nome, args):
        recebidos.append((nome, args))
        return "14:00 livre"

    try:
        r = await ai_client.responder(
            "groq", CHAVE_GROQ, "llama-3.3-70b-versatile", "Instrucoes",
            [{"role": "user", "content": "tem horario?"}],
            ferramentas=[ai_client.Ferramenta("consultar", "Consulta", {"type": "object"})],
            executar=executar)
    finally:
        openai.AsyncOpenAI = original
    cli = _ClienteChatFalso.ultimo
    check("usa o endereco da groq", cli.base_url == "https://api.groq.com/openai/v1", cli.base_url)
    check("instrucao vai como system", cli.pedidos[0]["messages"][0] ==
          {"role": "system", "content": "Instrucoes"}, cli.pedidos[0]["messages"][0])
    check("ferramenta no envelope do chat",
          cli.pedidos[0]["tools"][0]["function"]["name"] == "consultar", cli.pedidos[0]["tools"])
    check("executou com os argumentos decodificados",
          recebidos == [("consultar", {"dia": "amanha"})], recebidos)
    volta = cli.pedidos[1]["messages"]
    check("devolveu o resultado como role tool",
          volta[-1] == {"role": "tool", "tool_call_id": "call_1", "content": "14:00 livre"},
          volta[-1])
    check("o raciocinio <think> nao chega ao contato", r.texto == "Tem as 14h.", r.texto)
    check("consumo das duas voltas somado, sem os cacheados na entrada",
          (r.tokens_entrada, r.tokens_cache_leitura, r.tokens_saida) == (120, 80, 20),
          (r.tokens_entrada, r.tokens_cache_leitura, r.tokens_saida))
    check("ferramenta registrada", r.ferramentas_usadas == ["consultar"])

    # Registrar uso de modelo fora do catalogo nao pode perder a contagem.
    await ai_runtime.registrar_uso(DB, TENANT_A, ai_runtime.ORIGEM_CLIENTE,
                                   "groq", "qwen/qwen3-32b", r)
    doc = await DB.ai_usage.find_one({"tenant_id": TENANT_A, "modelo": "qwen/qwen3-32b"})
    check("modelo livre e medido em tokens", doc and doc["tokens"] == 220, doc)


async def etapa_buscar_modelos():
    print("\n[buscar modelos da conta]")
    import openai
    original = openai.AsyncOpenAI
    openai.AsyncOpenAI = _ClienteChatFalso
    try:
        ids = await ai_client.listar_modelos("groq", CHAVE_GROQ)
        check("so modelos de conversa, sem repetidos e em ordem",
              ids == ["openai/gpt-oss-120b", "qwen/qwen3.8-27b"], ids)
        check("no endereco do provedor",
              _ClienteChatFalso.ultimo.base_url == "https://api.groq.com/openai/v1")
        try:
            await ai_client.listar_modelos("openai", CHAVE_OPENAI)
            check("lista fechada nao e buscada", False)
        except ai_client.IAError:
            check("lista fechada (openai) nao e buscada", True)
    finally:
        openai.AsyncOpenAI = original

    chamadas = []

    async def listar_falso(provedor, chave, base_url=None):
        chamadas.append((provedor, chave, base_url))
        return ["m1"]

    original_l = rai.ai_client.listar_modelos
    rai.ai_client.listar_modelos = listar_falso
    try:
        r = await rai.modelos_da_conta(rai.ModelosIn(provedor="groq", chave_api=CHAVE_GROQ), ADMIN_A)
        check("devolve a lista", r == {"modelos": ["m1"]}, r)

        st, _ = await erro(rai.modelos_da_conta(rai.ModelosIn(provedor="groq"), ADMIN_A))
        check("sem chave e sem agente pede a chave", st == 400, st)

        ag = await rai.criar_agente(rai.AgenteIn(
            nome="Busca", provedor="groq", modelo="openai/gpt-oss-20b",
            chave_api=CHAVE_GROQ), ADMIN_A)
        chamadas.clear()
        await rai.modelos_da_conta(rai.ModelosIn(provedor="groq", agente_id=ag["id"]), ADMIN_A)
        check("ao editar reaproveita a chave guardada", chamadas[0][1] == CHAVE_GROQ, chamadas)

        st, _ = await erro(rai.modelos_da_conta(
            rai.ModelosIn(provedor="groq", agente_id=ag["id"]), ADMIN_B))
        check("outra empresa nao usa a chave guardada", st == 400, st)
        st, _ = await erro(rai.modelos_da_conta(
            rai.ModelosIn(provedor="gemini", agente_id=ag["id"]), ADMIN_A))
        check("chave de outro provedor nao e reaproveitada", st == 400, st)
    finally:
        rai.ai_client.listar_modelos = original_l
    check("rota exige admin", "require_tenant_admin" in str(
        inspect.signature(rai.modelos_da_conta).parameters["user"].default))


# ---------------------------------------------------------------------------

def test_suite():
    _conectar_fake()
    ai_client.validar = VALIDAR

    async def _tudo():
        await etapa_cifra()
        await etapa_salvar()
        await etapa_isolamento()
        await etapa_teste_de_conexao()
        await etapa_resolucao_de_credencial()
        await etapa_medicao()
        await etapa_cota_do_plano()
        await etapa_barreira_de_permissao()
        await etapa_catalogo()
        await etapa_provedores_compativeis()
        await etapa_laco_chat_completions()
        await etapa_buscar_modelos()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
