"""Teste do agente de IA atendendo de verdade, por dentro de
`handle_incoming_message` — que e o caminho por onde passa TODA mensagem
recebida do WhatsApp.

E por isso que o teste exercita a funcao inteira em vez de so `ai_agents`: o
risco do modulo nao esta em montar prompt, esta em ter mexido no roteamento de
mensagem. O que precisa ficar provado e que ligar um agente nao rouba conversa
de fluxo que ja existe, e que nenhum caminho de falha engole a mensagem.

O provedor nunca e chamado: `ai_runtime.responder_por_agente` e substituido.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
import tempfile
# As fotos do agente sao arquivos de verdade em disco: o envio sobe o arquivo
# para a Meta e levanta se ele nao existir, entao o teste precisa de um
# MEDIA_DIR de verdade.
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste",
             "MEDIA_DIR": tempfile.mkdtemp(prefix="zz-teste-midia-")}.items():
    os.environ.setdefault(k, v)

from cryptography.fernet import Fernet
os.environ["CREDENTIALS_KEY"] = Fernet.generate_key().decode()

from fakedb import FakeDB
from fastapi import HTTPException

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import ai_agents
import ai_client
import media_store
import ai_runtime
import flow_runtime
import routes.ai as rai

ENVIADOS: list[tuple] = []


async def fake_send_text(cred, numero, texto):
    ENVIADOS.append((numero, texto))
    return f"wamid.t{len(ENVIADOS)}"


MIDIAS_ENVIADAS: list[tuple] = []


async def fake_send_media(cred, numero, tipo, *, media_id=None, link=None,
                          caption="", filename=None):
    MIDIAS_ENVIADAS.append((numero, tipo, filename, caption, media_id))
    return f"wamid.m{len(MIDIAS_ENVIADAS)}"


async def fake_upload_media(cred, caminho, mime, filename=None):
    """O upload da foto para a Meta. Na Cloud API a mídia não viaja embutida na
    mensagem: sobe antes e o envio carrega o id devolvido."""
    return "media-meta-1"


class _IAFalsa:
    """Substitui `ai_runtime.responder_por_agente`. `proxima` define o que o
    modelo devolve; `erro` faz a chamada falhar."""

    def __init__(self):
        self.chamadas = []
        self.proxima = "Claro, posso ajudar com isso."
        self.erro = None
        # (nome, argumentos): a ferramenta que o "modelo" chama antes de
        # responder. Exercita o executor de verdade, como o laço do ai_client faria.
        self.chamar = None

    async def __call__(self, db, tenant_id, agente, instrucoes, historico,
                       ferramentas=None, executar=None):
        # `ferramentas`/`executar` sao aceitos e ignorados: o duble existe para
        # provar o roteamento do agente, nao o laco de ferramentas do provedor
        # (esse tem teste proprio em test_agenda.py). Sem os parametros, ligar a
        # agenda num agente quebraria toda esta suite com TypeError.
        self.chamadas.append({"tenant_id": tenant_id, "agente": agente,
                              "instrucoes": instrucoes, "historico": historico,
                              "ferramentas": ferramentas})
        if self.erro:
            raise self.erro
        usadas = []
        if self.chamar and ferramentas and executar:
            nome, args = self.chamar
            if nome in {f.nome for f in ferramentas}:
                self.chamadas[-1]["resultado_ferramenta"] = await executar(nome, args)
                usadas.append(nome)
        return ai_client.Resposta(texto=self.proxima, modelo="claude-haiku-4-5",
                                  tokens_entrada=100, tokens_saida=20,
                                  ferramentas_usadas=usadas)


IA = _IAFalsa()

_MODULOS_FAKE = (flow_runtime, rai)


def _conectar_fake():
    db_mod.get_db = lambda: DB
    for _m in _MODULOS_FAKE:
        _m.get_db = lambda: DB
    flow_runtime.whatsapp.send_text = fake_send_text
    flow_runtime.whatsapp.send_media = fake_send_media
    flow_runtime.whatsapp.upload_media = fake_upload_media
    ai_runtime.responder_por_agente = IA
    ai_agents.ai_runtime.responder_por_agente = IA


TENANT = "t-1"
CONN_A, CONN_B = "conn-a", "conn-b"
NUM = "5511900000001"
NUM2 = "5511900000002"
ADMIN = {"id": "u-1", "tenant_id": TENANT, "role": "tenant_admin",
         "name": "Ana", "email": "ana@x.com"}

ok = 0
_msg_seq = 0


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


def _um_pedaco(dados: bytes):
    """`read_chunk` no formato que `store_stream` espera: devolve tudo de uma
    vez e depois vazio."""
    restante = {"b": dados}

    async def ler(_n=None):
        b, restante["b"] = restante["b"], b""
        return b

    return ler


class _upload:
    """UploadFile o bastante para `store_stream` e para a checagem de tipo."""

    def __init__(self, filename, content_type, dados=b"conteudo"):
        self.filename = filename
        self.content_type = content_type
        self._ler = _um_pedaco(dados)

    async def read(self, n=None):
        return await self._ler(n)


async def receber(texto, numero=NUM, conn=CONN_A, media=None):
    """Uma mensagem chegando do WhatsApp."""
    global _msg_seq
    _msg_seq += 1
    await flow_runtime.handle_incoming_message(
        TENANT, numero, texto, f"msg-{_msg_seq}", connection_id=conn,
        push_name="Maria", media=media)


async def sessao(numero=NUM):
    contato = await DB.contacts.find_one({"tenant_id": TENANT, "numero_whatsapp": numero})
    if not contato:
        return None
    return await DB.conversation_sessions.find_one(
        {"tenant_id": TENANT, "contact_id": contato["id"]})


async def sistemas(session_id):
    docs = await DB.messages_log.find(
        {"session_id": session_id, "direction": "sistema"}).to_list(50)
    return [d["content"] for d in docs]


async def semear(*, agente=None, com_bot=False, bot_trigger="first_message",
                 keyword=None):
    DB._cols.clear()
    ENVIADOS.clear()
    MIDIAS_ENVIADAS.clear()
    IA.chamadas.clear()
    IA.erro = None
    IA.chamar = None
    IA.proxima = "Claro, posso ajudar com isso."
    os.environ.pop("AI_PLATFORM_KEY", None)

    await DB.tenants.insert_one({"id": TENANT})
    await DB.users.insert_one(dict(ADMIN))
    for cid, nome in ((CONN_A, "Vendas"), (CONN_B, "Suporte")):
        await DB.whatsapp_connections.insert_one({
            "id": cid, "tenant_id": TENANT, "nome": nome,
            "status": "conectado", "phone_number_id": f"pn-{cid}", "waba_id": "waba-1"})

    if com_bot:
        await DB.bots.insert_one({
            "id": "bot-1", "tenant_id": TENANT, "nome": "Robo",
            "connection_id": CONN_A, "trigger": bot_trigger,
            "status": "ativo", "main_flow_id": "flow-1"})
        await DB.flows.insert_one({
            "id": "flow-1", "tenant_id": TENANT, "bot_id": "bot-1",
            "published_at": "2026-08-01T00:00:00+00:00",
            "nodes": [
                {"id": "n1", "type": "start",
                 "data": {"trigger": bot_trigger, "trigger_keyword": keyword or ""}},
                {"id": "n2", "type": "text", "data": {"text": "RESPOSTA DO FLUXO"}},
            ],
            "edges": [{"source": "n1", "target": "n2", "sourceHandle": "out"}],
        })

    if agente is not None:
        await DB.ai_agents.insert_one({
            "id": "ag-1", "tenant_id": TENANT, "nome": "Bia",
            "tipo": "suporte", "modo": ai_agents.MODO_FALLBACK, "ativo": True,
            "tom": ["profissional"], "tamanho": "normais",
            "conexoes": [], "created_at": "2026-08-01T00:00:00+00:00",
            **agente})


# ---------------------------------------------------------------------------

async def etapa_fallback_responde():
    print("\n[fallback: responde onde nao ha bot]")
    await semear(agente={})
    await receber("qual o horario de voces?")

    s = await sessao()
    check("a conversa nasceu", s is not None)
    check("o agente respondeu", ENVIADOS and ENVIADOS[-1][1] == "Claro, posso ajudar com isso.", ENVIADOS)
    check("o provedor foi chamado uma vez", len(IA.chamadas) == 1, len(IA.chamadas))
    check("a conversa segue como bot, nao na fila",
          s["status_atendimento"] == "bot", s["status_atendimento"])
    check("e continua ativa", s["status"] == "ativa", s["status"])
    check("o contador de respostas subiu", s.get("ia_respostas") == 1, s.get("ia_respostas"))
    check("o agente ficou registrado na sessao", s.get("ia_agente_id") == "ag-1", s.get("ia_agente_id"))

    # A mensagem do contato chegou no historico enviado ao modelo.
    hist = IA.chamadas[0]["historico"]
    check("o historico termina na mensagem do contato",
          hist[-1]["role"] == "user" and "horario" in hist[-1]["content"], hist)
    check("o nome do contato entra no historico, nao nas instrucoes",
          "Maria" in hist[0]["content"] and "Maria" not in IA.chamadas[0]["instrucoes"])


async def etapa_continuidade():
    print("\n[a conversa continua com o agente]")
    await receber("e no sabado?")
    s = await sessao()
    check("respondeu de novo", len(IA.chamadas) == 2, len(IA.chamadas))
    check("contador acumulou", s.get("ia_respostas") == 2, s.get("ia_respostas"))
    hist = IA.chamadas[-1]["historico"]
    check("o historico agora tem a volta anterior", len(hist) >= 3, hist)
    check("alterna papeis",
          all(hist[i]["role"] != hist[i + 1]["role"] for i in range(len(hist) - 1)), hist)
    check("as instrucoes nao mudaram entre as chamadas (cache)",
          IA.chamadas[0]["instrucoes"] == IA.chamadas[1]["instrucoes"])


async def etapa_pediu_humano():
    print("\n[contato pede uma pessoa]")
    await semear(agente={})
    await receber("quero falar com um atendente")
    s = await sessao()
    check("nao gastou chamada no provedor", len(IA.chamadas) == 0, IA.chamadas)
    check("nada foi enviado ao contato", not ENVIADOS, ENVIADOS)
    check("foi para a fila", s["status_atendimento"] == "aguardando", s["status_atendimento"])
    check("com aguardando_desde preenchido", bool(s.get("aguardando_desde")))
    check("e o motivo ficou no historico",
          any("pediu atendimento humano" in m for m in await sistemas(s["id"])),
          await sistemas(s["id"]))


async def etapa_sentinela():
    print("\n[o agente decide transferir]")
    await semear(agente={})
    IA.proxima = "[HUMANO]"
    await receber("quero cancelar meu contrato e quero meu dinheiro de volta")
    s = await sessao()
    check("a sentinela nao vaza para o contato", not ENVIADOS, ENVIADOS)
    check("foi para a fila", s["status_atendimento"] == "aguardando", s["status_atendimento"])
    check("motivo registrado",
          any("transferiu para atendimento humano" in m for m in await sistemas(s["id"])))

    # Sentinela com pontuacao em volta tambem conta.
    await semear(agente={})
    IA.proxima = "[HUMANO]."
    await receber("reclamacao")
    check("sentinela com pontuacao tambem transfere", not ENVIADOS, ENVIADOS)


async def etapa_teto_de_respostas():
    print("\n[sem teto de respostas]")
    # Um `max_respostas` gravado antes da mudanca nao pode voltar a cortar.
    await semear(agente={"max_respostas": 2})
    for i in range(15):
        await receber(f"mensagem {i}")
    s = await sessao()
    check("respondeu todas as 15", len(ENVIADOS) == 15, len(ENVIADOS))
    check("e a conversa continua com o agente", s["status_atendimento"] == "bot", s["status_atendimento"])
    check("o contador segue contando", s.get("ia_respostas") == 15, s.get("ia_respostas"))


async def etapa_nao_rouba_do_bot():
    print("\n[fallback nao rouba conversa de fluxo publicado]")
    await semear(agente={}, com_bot=True)
    await receber("oi")
    check("quem respondeu foi o fluxo",
          ENVIADOS and ENVIADOS[-1][1] == "RESPOSTA DO FLUXO", ENVIADOS)
    check("o provedor nem foi chamado", len(IA.chamadas) == 0, IA.chamadas)


async def etapa_modo_primeiro():
    print("\n[modo primeiro assume mesmo havendo bot]")
    await semear(agente={"modo": ai_agents.MODO_PRIMEIRO}, com_bot=True)
    await receber("oi")
    check("quem respondeu foi o agente",
          ENVIADOS and ENVIADOS[-1][1] == "Claro, posso ajudar com isso.", ENVIADOS)
    s = await sessao()
    check("a sessao nao ficou presa ao fluxo", s.get("bot_id") is None, s.get("bot_id"))
    check("nem a um no", s.get("node_atual_id") is None, s.get("node_atual_id"))

    # E a mensagem seguinte continua com o agente, nao tenta retomar o fluxo.
    await receber("me fala mais")
    check("a segunda mensagem tambem foi do agente", len(IA.chamadas) == 2, len(IA.chamadas))


async def etapa_palavra_chave_ganha():
    print("\n[palavra-chave tem precedencia sobre o modo primeiro]")
    await semear(agente={"modo": ai_agents.MODO_PRIMEIRO}, com_bot=True,
                 bot_trigger="keyword", keyword="orcamento")
    await receber("quero um orcamento")
    check("quem respondeu foi o fluxo da palavra-chave",
          ENVIADOS and ENVIADOS[-1][1] == "RESPOSTA DO FLUXO", ENVIADOS)
    check("o agente nao foi chamado", len(IA.chamadas) == 0, IA.chamadas)

    # Sem a palavra, o agente assume.
    await semear(agente={"modo": ai_agents.MODO_PRIMEIRO}, com_bot=True,
                 bot_trigger="keyword", keyword="orcamento")
    await receber("bom dia")
    check("sem a palavra-chave, o agente assume", len(IA.chamadas) == 1, IA.chamadas)


async def etapa_desligado_e_inativo():
    print("\n[agente desligado nao atua]")
    await semear(agente={"modo": ai_agents.MODO_DESLIGADO})
    await receber("oi")
    s = await sessao()
    check("modo desligado nao responde", len(IA.chamadas) == 0, IA.chamadas)
    check("cai na fila humana", s["status_atendimento"] == "aguardando")

    await semear(agente={"ativo": False})
    await receber("oi")
    check("agente inativo nao responde", len(IA.chamadas) == 0, IA.chamadas)

    await semear(agente=None)
    await receber("oi")
    check("sem agente nenhum, fila humana", len(IA.chamadas) == 0, IA.chamadas)
    s = await sessao()
    check("com a mensagem de sempre",
          any("Nenhum bot configurado" in m for m in await sistemas(s["id"])),
          await sistemas(s["id"]))


async def etapa_amarracao_por_conexao():
    print("\n[o agente so atende as conexoes dele]")
    await semear(agente={"conexoes": [CONN_A]})
    await receber("oi", conn=CONN_B, numero=NUM2)
    check("nao atende conexao fora da lista", len(IA.chamadas) == 0, IA.chamadas)
    s = await sessao(NUM2)
    check("essa conversa foi para a fila", s["status_atendimento"] == "aguardando")

    await receber("oi", conn=CONN_A, numero=NUM)
    check("atende a conexao amarrada", len(IA.chamadas) == 1, IA.chamadas)

    # Especifico ganha do generico.
    await semear(agente={"conexoes": [CONN_B], "nome": "Generico"})
    await DB.ai_agents.insert_one({
        "id": "ag-2", "tenant_id": TENANT, "nome": "SoDoA", "tipo": "vendas",
        "modo": ai_agents.MODO_FALLBACK, "ativo": True, "tom": [],
        "tamanho": "normais", "conexoes": [CONN_A],
        "created_at": "2026-08-02T00:00:00+00:00"})
    escolhido = await ai_agents.agente_da_conexao(DB, TENANT, CONN_A)
    check("o agente especifico da conexao ganha", escolhido["id"] == "ag-2", escolhido["nome"])


async def etapa_conversa_com_humano():
    print("\n[conversa ja com humano nao e tocada]")
    await semear(agente={"modo": ai_agents.MODO_PRIMEIRO})
    await receber("oi")
    s = await sessao()
    await DB.conversation_sessions.update_one(
        {"id": s["id"]},
        {"$set": {"status": "aguardando_humano", "status_atendimento": "em_atendimento",
                  "atendente_id": "u-1"}})
    antes = len(IA.chamadas)
    await receber("mais uma pergunta")
    check("o agente nao responde por cima do atendente",
          len(IA.chamadas) == antes, IA.chamadas)
    check("e nada foi enviado", len(ENVIADOS) == 1, ENVIADOS)


async def etapa_anexo_sem_legenda():
    print("\n[anexo sem legenda]")
    await semear(agente={})
    await receber("", media={"kind": "image"})
    s = await sessao()
    check("nao tenta responder imagem", len(IA.chamadas) == 0, IA.chamadas)
    check("vai para a fila", s["status_atendimento"] == "aguardando")
    check("dizendo que nao analisa arquivo",
          any("nao analisa" in m or "não analisa" in m for m in await sistemas(s["id"])),
          await sistemas(s["id"]))


async def etapa_falhas_nao_engolem_mensagem():
    print("\n[nenhuma falha engole a mensagem]")
    # Provedor fora do ar.
    await semear(agente={})
    IA.erro = ai_client.IAError("O provedor está fora do ar no momento.")
    await receber("oi")
    s = await sessao()
    check("falha do provedor manda para a fila",
          s["status_atendimento"] == "aguardando", s["status_atendimento"])
    check("com o motivo no historico",
          any("falhou" in m for m in await sistemas(s["id"])), await sistemas(s["id"]))

    # Teto do plano / sem conta conectada.
    await semear(agente={})
    IA.erro = HTTPException(status_code=403, detail="Limite de IA do plano atingido")
    await receber("oi")
    s = await sessao()
    check("teto do plano manda para a fila", s["status_atendimento"] == "aguardando")
    check("dizendo que a IA esta indisponivel",
          any("indisponível" in m for m in await sistemas(s["id"])), await sistemas(s["id"]))

    # Resposta vazia.
    await semear(agente={})
    IA.proxima = "   "
    await receber("oi")
    s = await sessao()
    check("resposta vazia manda para a fila", s["status_atendimento"] == "aguardando")
    check("nada vazio foi enviado ao contato", not ENVIADOS, ENVIADOS)


async def etapa_prompt():
    print("\n[o prompt montado]")
    agente = {
        "nome": "Bia", "tipo": "vendas", "empresa_nome": "Vubis",
        "empresa_segmento": "Tecnologia", "tom": ["profissional", "empatico"],
        "tamanho": "curtas", "nao_fazer": "Não dar desconto\nNão prometer prazo",
        "base_conhecimento": "Horário: 9h às 18h.",
        "escalar_quando": "Pedirem nota fiscal",
    }
    agente["instrucoes"] = ("Sempre pergunte a cidade antes de falar de frete.\n"
                            "Trate quem ja e cliente pelo primeiro nome.")
    p = ai_agents.montar_instrucoes(agente)
    check("identifica agente e empresa", "Bia" in p and "Vubis" in p)
    check("o texto livre entra inteiro, sem reformatacao",
          "Sempre pergunte a cidade antes de falar de frete." in p
          and "Trate quem ja e cliente pelo primeiro nome." in p, p)
    # A ordem e o que garante que o cliente ajusta o comportamento sem afrouxar
    # o que nao pode ser afrouxado.
    check("as instrucoes livres vem depois do objetivo",
          p.index("Instruções específicas") > p.index("Seu objetivo:"))
    check("mas antes das regras fixas e da sentinela",
          p.index("Instruções específicas") < p.index("Nunca peça senha")
          and p.index("Instruções específicas") < p.index(ai_agents.SENTINELA))
    check("traz a estrategia do tipo escolhido", "vender" in p.lower(), p[:200])
    check("combina os dois tons", "profissional" in p and "empático" in p.lower())
    check("pede o tamanho escolhido", "15 palavras" in p, p)
    check("carrega as restricoes da empresa", "Não dar desconto" in p and "Não prometer prazo" in p)
    check("carrega a base de conhecimento", "9h às 18h" in p)
    check("carrega a regra extra de transferencia", "Pedirem nota fiscal" in p)
    check("explica a sentinela", ai_agents.SENTINELA in p)
    check("proibe markdown (é WhatsApp)", "markdown" in p.lower())

    # Sem base de conhecimento o prompt tem que dizer isso, senao o modelo
    # inventa horario e preco.
    sem_base = ai_agents.montar_instrucoes({"nome": "X", "tipo": "geral"})
    check("sem base, avisa que nao sabe nada da empresa",
          "não recebeu base de conhecimento" in sem_base, sem_base[-400:])

    # Personalizado sem objetivo e recusado na rota.
    # Guardado no agente e relido em toda chamada: salva, recarrega do banco e
    # o prompt montado a partir do documento tem que conter o texto.
    salvo = await rai.criar_agente(rai.AgenteIn(
        nome="Com instrucoes", tipo="suporte",
        instrucoes="Nunca abrevie o nome do produto."), ADMIN)
    doc = await DB.ai_agents.find_one({"id": salvo["id"]}, {"_id": 0})
    check("o texto livre e persistido no agente",
          doc["instrucoes"] == "Nunca abrevie o nome do produto.", doc.get("instrucoes"))
    check("e reaparece no prompt montado a partir do banco",
          "Nunca abrevie o nome do produto." in ai_agents.montar_instrucoes(doc))
    r = await rai.ver_prompt(salvo["id"], ADMIN)
    check("e no prompt que a tela mostra",
          "Nunca abrevie o nome do produto." in r["instrucoes"])
    await rai.remover_agente(salvo["id"], ADMIN)

    st, det = await erro(rai.criar_agente(
        rai.AgenteIn(nome="X", tipo="personalizado"), ADMIN))
    check("personalizado exige objetivo", st == 400, (st, det))

    st, det = await erro(rai.criar_agente(rai.AgenteIn(nome="X", tom=["inexistente"]), ADMIN))
    check("tom fora do catalogo e recusado", st == 400, (st, det))
    st, det = await erro(rai.criar_agente(rai.AgenteIn(nome="X", modo="turbo"), ADMIN))
    check("modo fora do catalogo e recusado", st == 400, (st, det))


async def etapa_historico():
    print("\n[janela de historico]")
    await semear(agente={})
    s_id = "sess-hist"
    await DB.conversation_sessions.insert_one({"id": s_id, "tenant_id": TENANT})
    # Comeca com uma resposta nossa, para provar que a janela e reajustada.
    await DB.messages_log.insert_one({
        "id": "m0", "session_id": s_id, "tenant_id": TENANT, "direction": "enviada",
        "content": "sobra do turno anterior", "timestamp": "2026-08-01T10:00:00+00:00"})
    for i in range(3):
        await DB.messages_log.insert_one({
            "id": f"mu{i}", "session_id": s_id, "tenant_id": TENANT, "direction": "recebida",
            "content": f"pergunta {i}", "timestamp": f"2026-08-01T10:0{i+1}:00+00:00"})
    await DB.messages_log.insert_one({
        "id": "msys", "session_id": s_id, "tenant_id": TENANT, "direction": "sistema",
        "content": "isto nao pode entrar", "timestamp": "2026-08-01T10:05:00+00:00"})

    h = await ai_agents.montar_historico(DB, s_id, TENANT)
    check("comeca sempre no contato", h[0]["role"] == "user", h)
    check("mensagem de sistema fica de fora",
          all("isto nao pode entrar" not in t["content"] for t in h), h)
    check("turnos seguidos do contato viram um so", len(h) == 1, h)
    check("preservando o conteudo de todos",
          all(f"pergunta {i}" in h[0]["content"] for i in range(3)), h[0])


async def etapa_contadores_do_cartao():
    print("\n[contadores do cartao]")
    # Tres conversas do mesmo agente em situacoes diferentes, mais uma de
    # outro agente — o cartao de cada um so pode contar as suas.
    await semear(agente={})
    await DB.ai_agents.insert_one({
        "id": "ag-2", "tenant_id": TENANT, "nome": "Outro", "tipo": "vendas",
        "modo": ai_agents.MODO_FALLBACK, "ativo": True, "tom": [],
        "tamanho": "normais", "conexoes": [], "created_at": "2026-08-02T00:00:00+00:00"})
    for i, (agente_id, situacao) in enumerate([
        ("ag-1", "bot"), ("ag-1", "bot"), ("ag-1", "aguardando"),
        ("ag-1", "finalizado"), ("ag-2", "em_atendimento"),
    ]):
        await DB.conversation_sessions.insert_one({
            "id": f"s{i}", "tenant_id": TENANT, "ia_agente_id": agente_id,
            "status_atendimento": situacao})

    linhas = await rai.listar_agentes(ADMIN)
    por_id = {a["id"]: a["conversas"] for a in linhas}
    check("atendendo conta so as que seguem com o agente",
          por_id["ag-1"]["atendendo"] == 2, por_id["ag-1"])
    check("transferidas junta fila e em atendimento",
          por_id["ag-1"]["transferidas"] == 1, por_id["ag-1"])
    check("total inclui as finalizadas", por_id["ag-1"]["total"] == 4, por_id["ag-1"])
    check("um agente nao conta a conversa do outro",
          por_id["ag-2"] == {"atendendo": 0, "transferidas": 1, "total": 1}, por_id["ag-2"])

    # Agente sem conversa nenhuma tem que vir com os tres zeros, e nao sem a
    # chave — a tela le `a.conversas` direto.
    await semear(agente={})
    linhas = await rai.listar_agentes(ADMIN)
    check("agente novo vem zerado, nao sem a chave",
          linhas[0]["conversas"] == {"atendendo": 0, "transferidas": 0, "total": 0},
          linhas[0].get("conversas"))

    # Uma conversa que nenhum agente atendeu nao pode aparecer em cartao algum.
    await DB.conversation_sessions.insert_one({
        "id": "s-humana", "tenant_id": TENANT, "ia_agente_id": None,
        "status_atendimento": "aguardando"})
    linhas = await rai.listar_agentes(ADMIN)
    check("conversa sem agente fica de fora",
          linhas[0]["conversas"]["total"] == 0, linhas[0]["conversas"])


async def etapa_credencial_por_agente():
    print("\n[credencial e do agente, nao da empresa]")
    await semear(agente=None)

    # O `validar` de verdade sairia para a rede. O dubla registra o que seria
    # conferido, que e justamente o que precisa ficar provado: cada agente e
    # validado com a SUA chave e o SEU modelo.
    validados = []

    async def fake_validar(provedor, chave, modelo, base_url=None):
        validados.append((provedor, chave, modelo))
        return ai_client.Resposta(texto="ok", modelo=modelo)

    original = ai_client.validar
    rai.ai_client.validar = fake_validar
    try:
        suporte = await rai.criar_agente(rai.AgenteIn(
            nome="Suporte", tipo="suporte", provedor="anthropic",
            modelo="claude-haiku-4-5", chave_api="sk-ant-CHAVE-DO-SUPORTE"), ADMIN)
        vendas = await rai.criar_agente(rai.AgenteIn(
            nome="Vendas", tipo="vendas", provedor="openai",
            modelo="gpt-5-mini", chave_api="sk-CHAVE-DE-VENDAS"), ADMIN)

        check("cada agente foi validado com a propria chave e modelo",
              validados == [("anthropic", "sk-ant-CHAVE-DO-SUPORTE", "claude-haiku-4-5"),
                            ("openai", "sk-CHAVE-DE-VENDAS", "gpt-5-mini")], validados)
        check("dois provedores diferentes convivem na mesma empresa",
              (suporte["provedor"], vendas["provedor"]) == ("anthropic", "openai"))

        # A chave nunca volta em claro, nem na criacao nem na listagem.
        check("a resposta nao traz o bloco cifrado", "credencial" not in suporte, suporte.keys())
        check("so a mascara volta", suporte["mascara"].endswith("ORTE")
              and "CHAVE-DO-SUPORTE" not in suporte["mascara"], suporte["mascara"])
        listados = await rai.listar_agentes(ADMIN)
        check("a listagem tambem nao vaza a chave",
              all("credencial" not in a for a in listados), [a.keys() for a in listados])
        check("mas diz quem tem chave", all(a["tem_chave"] for a in listados))

        # No banco, cifrada — e decifravel de volta.
        doc = await DB.ai_agents.find_one({"id": suporte["id"]})
        check("no banco fica cifrada",
              "sk-ant-CHAVE-DO-SUPORTE" not in str(doc["credencial"]), doc["credencial"])
        import ai_credentials
        check("e volta identica ao decifrar",
              ai_credentials.decifrar(doc["credencial"]) == "sk-ant-CHAVE-DO-SUPORTE")

        # Editar sem reenviar a chave reaproveita a guardada (o cliente nao a tem).
        validados.clear()
        await rai.atualizar_agente(suporte["id"], rai.AgenteIn(
            nome="Suporte", tipo="suporte", provedor="anthropic",
            modelo="claude-sonnet-5"), ADMIN)
        check("trocar so o modelo reusa a chave guardada",
              validados == [("anthropic", "sk-ant-CHAVE-DO-SUPORTE", "claude-sonnet-5")], validados)

        # Trocar de provedor sem chave nova nao tem como funcionar.
        st, det = await erro(rai.atualizar_agente(suporte["id"], rai.AgenteIn(
            nome="Suporte", tipo="suporte", provedor="openai", modelo="gpt-5-mini"), ADMIN))
        check("trocar de provedor exige chave nova", st == 400, (st, det))

        # Chave do provedor errado e barrada antes de ir para a rede.
        validados.clear()
        st, det = await erro(rai.criar_agente(rai.AgenteIn(
            nome="X", provedor="anthropic", chave_api="sk-proj-DA-OPENAI"), ADMIN))
        check("chave do provedor errado e recusada", st == 400, (st, det))
        check("sem gastar chamada no provedor", validados == [], validados)

        # Agente sem provedor e valido: cai na chave da plataforma.
        sem_chave = await rai.criar_agente(rai.AgenteIn(nome="Simples"), ADMIN)
        check("agente sem provedor pode existir", sem_chave["tem_chave"] is False, sem_chave)
    finally:
        rai.ai_client.validar = original

    # --- resolucao em tempo de atendimento ---
    doc_suporte = await DB.ai_agents.find_one({"id": suporte["id"]}, {"_id": 0})
    cred = await ai_runtime.resolver_credencial(DB, TENANT, doc_suporte)
    check("o atendimento usa a chave do proprio agente",
          cred["chave"] == "sk-ant-CHAVE-DO-SUPORTE" and cred["origem"] == "cliente", cred["origem"])

    doc_vendas = await DB.ai_agents.find_one({"id": vendas["id"]}, {"_id": 0})
    cred = await ai_runtime.resolver_credencial(DB, TENANT, doc_vendas)
    check("e o outro agente usa a dele", cred["chave"] == "sk-CHAVE-DE-VENDAS", cred["chave"])

    # Sem chave e sem plataforma: erro que diz o que fazer, nao 500.
    doc_simples = await DB.ai_agents.find_one({"id": sem_chave["id"]}, {"_id": 0})
    st, det = await erro(ai_runtime.resolver_credencial(DB, TENANT, doc_simples))
    check("sem chave e sem plataforma, erro explicando", st == 400 and "Simples" in det, (st, det))

    # Com a chave da plataforma configurada, o mesmo agente passa a funcionar.
    os.environ["AI_PLATFORM_KEY"] = "sk-DA-PLATAFORMA"
    os.environ["AI_PLATFORM_PROVIDER"] = "openai"
    try:
        cred = await ai_runtime.resolver_credencial(DB, TENANT, doc_simples)
        check("sem chave propria, cai na plataforma",
              cred["origem"] == "plataforma" and cred["chave"] == "sk-DA-PLATAFORMA", cred)

        # Credencial marcada como quebrada nao e usada — cai na plataforma.
        await DB.ai_agents.update_one({"id": suporte["id"]},
                                      {"$set": {"credencial_status": "erro"}})
        quebrado = await DB.ai_agents.find_one({"id": suporte["id"]}, {"_id": 0})
        cred = await ai_runtime.resolver_credencial(DB, TENANT, quebrado)
        check("chave sabidamente quebrada nao e reusada",
              cred["origem"] == "plataforma", cred["origem"])
    finally:
        os.environ.pop("AI_PLATFORM_KEY", None)
        os.environ.pop("AI_PLATFORM_PROVIDER", None)


async def etapa_fotos():
    print()
    print("[o agente manda foto na hora certa]")

    # --- o extrator, sem rede e sem banco ---
    fotos = [{"id": f"f{i}", "media_id": f"m{i}", "descricao": f"foto {i}"} for i in range(1, 4)]

    limpo, escolhidas = ai_agents.extrair_fotos("Olha esse Onix [FOTO:2] que beleza", fotos)
    check("a etiqueta sai do texto", "[FOTO:2]" not in limpo and "FOTO" not in limpo, limpo)
    check("sobra a frase limpa, sem espaco duplo", limpo == "Olha esse Onix que beleza", repr(limpo))
    check("e a foto certa foi escolhida", [f["id"] for f in escolhidas] == ["f2"], escolhidas)

    _, varias = ai_agents.extrair_fotos("[FOTO:1][FOTO:3]", fotos)
    check("varias etiquetas viram varias fotos",
          [f["id"] for f in varias] == ["f1", "f3"], varias)

    _, repetida = ai_agents.extrair_fotos("[FOTO:1] e de novo [FOTO:1]", fotos)
    check("etiqueta repetida vale uma vez", len(repetida) == 1, repetida)

    limpo, fora = ai_agents.extrair_fotos("Veja [FOTO:9] e [FOTO:0]", fotos)
    check("etiqueta fora da faixa nao vira foto", fora == [], fora)
    check("e some do texto em vez de chegar ao contato",
          "FOTO" not in limpo and limpo == "Veja e", repr(limpo))

    muitas = [{"id": f"f{i}", "media_id": f"m{i}", "descricao": "x"} for i in range(1, 11)]
    _, cortadas = ai_agents.extrair_fotos(" ".join(f"[FOTO:{i}]" for i in range(1, 11)), muitas)
    check("o teto por resposta e respeitado",
          len(cortadas) == ai_agents.MAX_FOTOS_POR_RESPOSTA, len(cortadas))

    _, minusculo = ai_agents.extrair_fotos("[foto: 2 ]", fotos)
    check("a etiqueta e reconhecida com espaco e minuscula",
          [f["id"] for f in minusculo] == ["f2"], minusculo)

    # --- o catalogo no prompt ---
    agente = {"nome": "Bia", "tipo": "vendas", "midias": [
        {"id": "f1", "media_id": "m1", "descricao": "Onix 2019 prata, frente"},
        {"id": "f2", "media_id": "m2", "descricao": "Onix 2019 prata, interior"},
    ]}
    prompt = ai_agents.montar_instrucoes(agente)
    check("as fotos aparecem numeradas no prompt",
          "[FOTO:1] Onix 2019 prata, frente" in prompt
          and "[FOTO:2] Onix 2019 prata, interior" in prompt, prompt)
    check("com a regra de nao inventar etiqueta", "Nunca invente um número" in prompt)
    check("e o teto por resposta dito ao modelo",
          str(ai_agents.MAX_FOTOS_POR_RESPOSTA) in prompt)
    check("agente sem foto nao ganha bloco de fotos alguma",
          "Fotos que você pode enviar" not in ai_agents.montar_instrucoes(
              {"nome": "X", "tipo": "geral"}))

    # --- ponta a ponta: a foto sai de verdade pelo WhatsApp ---
    await semear(agente={})
    doc = await media_store.store_stream(
        DB, TENANT, None, "u-1", "onix.jpg", "image/jpeg",
        _um_pedaco(b"conteudo-da-foto"), kind="image")
    await DB.ai_agents.update_one({"id": "ag-1"}, {"$set": {"midias": [
        {"id": "f1", "media_id": doc["id"], "descricao": "Onix 2019 prata, frente"}]}})

    IA.proxima = "Tenho esse Onix 2019 com 45 mil km. [FOTO:1]"
    await receber("tem onix?")

    check("o texto saiu sem a etiqueta",
          ENVIADOS and ENVIADOS[-1][1] == "Tenho esse Onix 2019 com 45 mil km.", ENVIADOS)
    check("e a foto saiu junto", len(MIDIAS_ENVIADAS) == 1, MIDIAS_ENVIADAS)
    check("como imagem, com o arquivo certo",
          MIDIAS_ENVIADAS[0][1] == "image" and MIDIAS_ENVIADAS[0][2] == "onix.jpg",
          MIDIAS_ENVIADAS[0])

    s_ = await sessao()
    msgs = await DB.messages_log.find(
        {"session_id": s_["id"], "direction": "enviada"}).to_list(10)
    check("o historico guarda a imagem como imagem",
          any(m["type"] == "image" and m.get("meta", {}).get("media", {}).get("id") == doc["id"]
              for m in msgs), msgs)
    check("a conversa continua com o agente, nao foi para a fila",
          s_["status_atendimento"] == "bot", s_["status_atendimento"])

    # --- foto apagada nao derruba a resposta ---
    await semear(agente={})
    await DB.ai_agents.update_one({"id": "ag-1"}, {"$set": {"midias": [
        {"id": "f1", "media_id": "nao-existe", "descricao": "sumida"}]}})
    IA.proxima = "Segue a foto [FOTO:1]"
    await receber("manda a foto")
    check("texto entregue mesmo com a foto indisponivel",
          ENVIADOS and ENVIADOS[-1][1] == "Segue a foto", ENVIADOS)
    check("e nada de imagem foi enviado", MIDIAS_ENVIADAS == [], MIDIAS_ENVIADAS)

    # --- resposta so com etiqueta invalida vai para a fila, nao vazia ---
    await semear(agente={})
    IA.proxima = "[FOTO:7]"
    await receber("manda foto")
    s_ = await sessao()
    check("resposta so com etiqueta invalida escala",
          s_["status_atendimento"] == "aguardando", s_["status_atendimento"])
    check("sem mandar bolha vazia", ENVIADOS == [], ENVIADOS)

    IA.proxima = "Claro, posso ajudar com isso."


async def etapa_upload_de_fotos():
    print()
    print("[subir foto pelo painel]")
    await semear(agente={})

    st, det = await erro(rai.subir_foto(
        "ag-1", _upload("a.jpg", "image/jpeg"), "", ADMIN))
    check("foto sem descricao e recusada", st == 400 and "Descreva" in det, (st, det))

    st, det = await erro(rai.subir_foto(
        "ag-1", _upload("contrato.pdf", "application/pdf"), "um pdf", ADMIN))
    check("arquivo que nao e imagem e recusado", st == 400, (st, det))

    foto = await rai.subir_foto("ag-1", _upload("onix.jpg", "image/jpeg"),
                                "Onix 2019 prata", ADMIN)
    check("a foto entra no agente", foto["descricao"] == "Onix 2019 prata", foto)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("guardada dentro do documento do agente", len(doc["midias"]) == 1, doc.get("midias"))
    arq = await DB.media_files.find_one({"id": foto["media_id"]})
    check("e fora da retencao de midia enquanto esta no catalogo (sumia em 90 dias)",
          arq.get("uso") == "catalogo_ia", arq.get("uso"))
    check("e ja aparece no prompt",
          "Onix 2019 prata" in ai_agents.montar_instrucoes(doc))

    await rai.editar_foto("ag-1", foto["id"], rai.MidiaPatch(descricao="Onix 2019 preto"), ADMIN)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("a descricao pode ser corrigida",
          doc["midias"][0]["descricao"] == "Onix 2019 preto", doc["midias"])

    # Empresa B nao alcanca o agente de A.
    outro = {"id": "u-b", "tenant_id": "t-outro", "role": "tenant_admin",
             "name": "B", "email": "b@x.com"}
    st, _ = await erro(rai.subir_foto("ag-1", _upload("x.jpg", "image/jpeg"), "x", outro))
    check("outra empresa nao sobe foto no agente alheio", st == 404, st)
    st, _ = await erro(rai.remover_foto("ag-1", foto["id"], outro))
    check("nem remove", st == 404, st)

    await rai.remover_foto("ag-1", foto["id"], ADMIN)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("removida do catalogo", doc["midias"] == [], doc["midias"])
    check("mas o arquivo fica para a retencao (historico de quem ja recebeu)",
          await DB.media_files.count_documents({"tenant_id": TENANT}) >= 1)
    arq = await DB.media_files.find_one({"id": foto["media_id"]})
    check("e volta para a janela de retencao como qualquer anexo", "uso" not in arq, arq.get("uso"))


async def etapa_treinamento():
    print()
    print("[treinar o agente]")
    await semear(agente={})

    st, det = await erro(rai.ensinar("ag-1", rai.MemoriaIn(texto="   "), ADMIN))
    check("ensinamento vazio e recusado", st == 400, (st, det))

    a = await rai.ensinar("ag-1", rai.MemoriaIn(texto="O frete custa R$ 30."), ADMIN)
    b = await rai.ensinar("ag-1", rai.MemoriaIn(texto="Na verdade o frete e gratis acima de R$ 200."), ADMIN)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("os dois ficam guardados, na ordem", [m["texto"][:5] for m in doc["memoria"]] == ["O fre", "Na ve"],
          doc.get("memoria"))
    prompt = ai_agents.montar_instrucoes(doc)
    check("entram no prompt numerados",
          "1. O frete custa R$ 30." in prompt and "2. Na verdade" in prompt, prompt)
    check("e o prompt diz que o mais recente ganha", "número maior" in prompt)

    # Salvar a configuracao pelo assistente nao apaga o treino: o PATCH do
    # agente faz $set so dos campos do formulario, e `memoria` nao e um deles.
    check("memoria fica fora do formulario do agente", "memoria" not in rai.AgenteIn.model_fields)

    await rai.corrigir_ensinamento("ag-1", a["id"], rai.MemoriaIn(texto="O frete custa R$ 25."), ADMIN)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("o ensinamento pode ser corrigido", doc["memoria"][0]["texto"] == "O frete custa R$ 25.")

    st, _ = await erro(rai.ensinar("ag-1", rai.MemoriaIn(texto="x" * (ai_agents.MAX_MEMORIA_ITEM + 1)), ADMIN))
    check("ensinamento longo demais e recusado", st == 400, st)
    await DB.ai_agents.update_one({"id": "ag-1"}, {"$set": {"memoria": doc["memoria"] + [
        {"id": "grande", "texto": "y" * (ai_agents.MAX_MEMORIA_TOTAL - 60)}]}})
    st, det = await erro(rai.ensinar("ag-1", rai.MemoriaIn(texto="z" * 100), ADMIN))
    check("o total do treino tem teto", st == 400 and "limite" in det, (st, det))
    await rai.esquecer("ag-1", "grande", ADMIN)

    outro = {"id": "u-b", "tenant_id": "t-outro", "role": "tenant_admin",
             "name": "B", "email": "b@x.com"}
    st, _ = await erro(rai.ensinar("ag-1", rai.MemoriaIn(texto="invasao"), outro))
    check("outra empresa nao ensina o agente alheio", st == 404, st)
    st, _ = await erro(rai.esquecer("ag-1", a["id"], outro))
    check("nem apaga o que ele aprendeu", st == 404, st)

    await rai.esquecer("ag-1", b["id"], ADMIN)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("esquecer tira so aquele", [m["id"] for m in doc["memoria"]] == [a["id"]], doc["memoria"])
    await rai.esquecer_tudo("ag-1", ADMIN)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("esquecer tudo zera o treino", doc["memoria"] == [])
    check("e o bloco some do prompt", "já te ensinou" not in ai_agents.montar_instrucoes(doc))


PLANILHA = (
    "Modelo;Ano;Cor;Preço\n"
    "Onix;2019;Prata;62.900\n"
    "Onix;2020;Preto;68.500\n"
    "HB20;2019;Prata;59.000\n"
    ";;;\n"
    "Tênis Corrida;;Azul;399\n"
).encode("cp1252")


async def etapa_estoque():
    print()
    print("[estoque do cliente]")
    await semear(agente={})
    import ai_estoque

    st, det = await erro(rai.subir_estoque("ag-1", _upload("e.xls", "application/vnd.ms-excel", b"\xd0\xcf"), ADMIN))
    check(".xls antigo e recusado dizendo como salvar em .xlsx", st == 400 and ".xlsx" in det, (st, det))
    st, det = await erro(rai.subir_estoque("ag-1", _upload("e.xlsx", "application/vnd.ms-excel", b"PK lixo"), ADMIN))
    check("xlsx corrompido e recusado com motivo", st == 400 and "Excel" in det, (st, det))

    # Excel de verdade, do jeito que loja manda: titulo em cima, linha em
    # branco, numero inteiro que o openpyxl le como float, data.
    import io as _io
    import datetime as _dt
    import openpyxl
    livro = openpyxl.Workbook()
    aba = livro.active
    aba.append(["Estoque ADS Veiculos"])
    aba.append([])
    aba.append(["Modelo", "Ano", "Preço", "Entrada"])
    aba.append(["Clio", 2012, 32900.0, _dt.date(2026, 9, 1)])
    aba.append(["Palio", 2015, 38500.5, None])
    buf = _io.BytesIO()
    livro.save(buf)
    resumo = await rai.subir_estoque("ag-1", _upload("estoque.xlsx", "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet", buf.getvalue()), ADMIN)
    check("xlsx entra, achando o cabecalho abaixo do titulo",
          resumo["colunas"] == ["Modelo", "Ano", "Preço", "Entrada"] and resumo["total"] == 2, resumo)
    check("numero inteiro sai sem .0 e data sai em pt-BR",
          resumo["exemplos"][0] == {"Modelo": "Clio", "Ano": "2012", "Preço": "32900", "Entrada": "01/09/2026"},
          resumo["exemplos"])

    resumo = await rai.subir_estoque("ag-1", _upload("estoque.csv", "text/csv", PLANILHA), ADMIN)
    check("a planilha entra, sem a linha em branco", resumo["total"] == 4, resumo)
    check("as colunas sao as da planilha", resumo["colunas"] == ["Modelo", "Ano", "Cor", "Preço"], resumo)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})

    linhas, total = await ai_estoque.buscar(DB, TENANT, doc, "tem o onix prata?")
    check("busca exige todas as palavras", total == 1 and linhas[0]["Ano"] == "2019", (total, linhas))
    linhas, _ = await ai_estoque.buscar(DB, TENANT, doc, "TENIS")
    check("sem acento e sem caixa", linhas and linhas[0]["Modelo"] == "Tênis Corrida", linhas)
    linhas, total = await ai_estoque.buscar(DB, TENANT, doc, "onix vermelho")
    check("sem nenhum completo, devolve os mais parecidos", total == 2, (total, linhas))
    linhas, total = await ai_estoque.buscar(DB, TENANT, doc, "")
    check("sem busca, os primeiros na ordem da planilha", linhas[0]["Modelo"] == "Onix" and total == 4)

    prompt = ai_agents.montar_instrucoes(doc)
    check("o prompt leva o formato, nao a planilha",
          "4 itens" in prompt and "consultar_estoque" in prompt and "HB20" not in prompt, prompt)

    _, ferramentas, executar = await ai_agents.preparar_ferramentas(
        DB, TENANT, doc, {"id": "c"}, None)
    check("o agente recebe a ferramenta", [f.nome for f in ferramentas] == ["consultar_estoque"])
    saida = await executar("consultar_estoque", {"busca": "hb20"})
    check("e a ferramenta responde com a linha", "HB20" in saida and "59.000" in saida, saida)
    _, sem, _ = await ai_agents.preparar_ferramentas(DB, TENANT, {"id": "x"}, {"id": "c"}, None)
    check("agente sem estoque nao ganha ferramenta", not sem)

    novo = "Modelo;Ano\nCivic;2021\n".encode()
    await rai.subir_estoque("ag-1", _upload("novo.csv", "text/csv", novo), ADMIN)
    check("subir de novo substitui o estoque inteiro",
          await DB.ai_estoque.count_documents({"agente_id": "ag-1"}) == 1)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    linhas, _ = await ai_estoque.buscar(DB, TENANT, doc, "onix")
    check("o vendido some", linhas == [], linhas)

    outro = {"id": "u-b", "tenant_id": "t-outro", "role": "tenant_admin",
             "name": "B", "email": "b@x.com"}
    st, _ = await erro(rai.ver_estoque("ag-1", "", outro))
    check("outra empresa nao ve o estoque", st == 404, st)
    st, _ = await erro(rai.subir_estoque("ag-1", _upload("x.csv", "text/csv", novo), outro))
    check("nem troca", st == 404, st)

    vista = await rai.ver_estoque("ag-1", "civic", ADMIN)
    check("a tela usa a mesma busca", vista["total"] == 1, vista)

    await rai.remover_estoque("ag-1", ADMIN)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("remover limpa as linhas e o resumo",
          doc["estoque"] is None and await DB.ai_estoque.count_documents({}) == 0)

    await rai.subir_estoque("ag-1", _upload("novo.csv", "text/csv", novo), ADMIN)
    await rai.remover_agente("ag-1", ADMIN)
    check("excluir o agente leva o estoque junto", await DB.ai_estoque.count_documents({}) == 0)


async def etapa_finalizar():
    print()
    print("[agente finaliza o atendimento]")
    await semear(agente={})
    IA.chamar = ("finalizar_atendimento", {"resumo": "Tirou duvida sobre frete"})

    await receber("oi, qual o frete?")
    s = await sessao()
    check("desligado, o agente nao ganha a ferramenta",
          not IA.chamadas[-1]["ferramentas"] and s["status_atendimento"] == "bot", s["status_atendimento"])

    poderes = await rai.mudar_poderes("ag-1", rai.PoderesIn(finalizar_ativo=True), ADMIN)
    check("o poder e ligado pela tela de treino",
          poderes["agenda_ativa"] is False and poderes["finalizar_ativo"] is True, poderes)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("o prompt diz quando encerrar", "finalizar_atendimento" in ai_agents.montar_instrucoes(doc))

    # Conversa ja existente: o caminho do agente grava so os contadores.
    IA.proxima = "Por nada! Qualquer coisa estamos aqui."
    await receber("obrigado, era so isso")
    s = await sessao()
    check("conversa existente: finalizada", s["status_atendimento"] == "finalizado"
          and s["status"] == "finalizada" and s.get("finalizado_em"), s)
    check("o motivo fica no historico",
          any("finalizou o atendimento: Tirou duvida" in t for t in await sistemas(s["id"])))
    check("e a despedida sai para o contato", ENVIADOS and ENVIADOS[-1][1].startswith("Por nada"), ENVIADOS)

    # A mensagem seguinte abre outra conversa, que e o caminho da sessao nova:
    # ela e gravada inteira no fim, e o $set nao pode desfazer o status.
    await receber("oi de novo, obrigado, era so isso")
    sessoes = await DB.conversation_sessions.find({"tenant_id": TENANT}).to_list(10)
    check("a nova mensagem abriu outra conversa", len(sessoes) == 2, len(sessoes))
    nova = [x for x in sessoes if x["id"] != s["id"]][0]
    check("conversa nova: tambem finalizada", nova["status_atendimento"] == "finalizado", nova["status_atendimento"])

    # Chat de teste: diz que finalizaria, sem gravar nada.
    antes = await DB.conversation_sessions.count_documents({"status": "finalizada"})
    r = await rai.testar_agente("ag-1", rai.TesteIn(mensagens=[{"role": "user", "content": "valeu"}]), ADMIN)
    check("no teste a tela sabe que finalizaria", r["finalizou"] is True, r)
    check("e nada foi gravado", "seria finalizado" in IA.chamadas[-1]["resultado_ferramenta"]
          and await DB.conversation_sessions.count_documents({"status": "finalizada"}) == antes)

    outro = {"id": "u-b", "tenant_id": "t-outro", "role": "tenant_admin", "name": "B", "email": "b@x.com"}
    st, _ = await erro(rai.mudar_poderes("ag-1", rai.PoderesIn(agenda_ativa=True), outro))
    check("outra empresa nao muda os poderes", st == 404, st)
    IA.chamar = None


def _pdf_com_texto(texto: str) -> bytes:
    """Um PDF minimo com uma linha de texto, montado a mao (o pypdf le, mas
    nao escreve texto). Os offsets do xref sao calculados, nao chutados."""
    conteudo = f"BT /F1 12 Tf 72 720 Td ({texto}) Tj ET".encode("latin-1")
    objetos = [
        b"<< /Type /Catalog /Pages 2 0 R >>",
        b"<< /Type /Pages /Kids [3 0 R] /Count 1 >>",
        b"<< /Type /Page /Parent 2 0 R /MediaBox [0 0 612 792] /Contents 4 0 R "
        b"/Resources << /Font << /F1 5 0 R >> >> >>",
        b"<< /Length " + str(len(conteudo)).encode() + b" >>\nstream\n" + conteudo + b"\nendstream",
        b"<< /Type /Font /Subtype /Type1 /BaseFont /Helvetica >>",
    ]
    saida = bytearray(b"%PDF-1.4\n")
    offsets = []
    for i, corpo in enumerate(objetos, 1):
        offsets.append(len(saida))
        saida += f"{i} 0 obj\n".encode() + corpo + b"\nendobj\n"
    xref = len(saida)
    saida += f"xref\n0 {len(objetos) + 1}\n0000000000 65535 f \n".encode()
    for o in offsets:
        saida += f"{o:010d} 00000 n \n".encode()
    saida += f"trailer\n<< /Size {len(objetos) + 1} /Root 1 0 R >>\nstartxref\n{xref}\n%%EOF".encode()
    return bytes(saida)


def _docx_com_texto(*paragrafos: str) -> bytes:
    import io as _io
    import zipfile as _zip
    corpo = "".join(f"<w:p><w:r><w:t>{p}</w:t></w:r></w:p>" for p in paragrafos)
    xml = ('<?xml version="1.0" encoding="UTF-8"?>'
           '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
           f"<w:body>{corpo}</w:body></w:document>")
    buf = _io.BytesIO()
    with _zip.ZipFile(buf, "w") as z:
        z.writestr("word/document.xml", xml)
    return buf.getvalue()


async def etapa_documentos():
    print()
    print("[documentos do treino: pdf, word, texto]")
    await semear(agente={})
    import ai_documentos

    pdf = await rai.subir_documento("ag-1", _upload("financiamento.pdf", "application/pdf",
                                    _pdf_com_texto("Financiamos em ate 48x com entrada de 20%")), ADMIN)
    check("o texto do PDF e lido", pdf["caracteres"] > 20 and "48x" in pdf["previa"], pdf)
    docx = await rai.subir_documento("ag-1", _upload("trocas.docx", "application/octet-stream",
                                     _docx_com_texto("Politica de troca", "Aceitamos seu usado na troca.")), ADMIN)
    check("o texto do Word e lido, paragrafo por paragrafo",
          "Politica de troca\nAceitamos seu usado" in (await DB.ai_agents.find_one({"id": "ag-1"}))["documentos"][1]["texto"])
    await rai.subir_documento("ag-1", _upload("horario.txt", "text/plain", "Abrimos de segunda a sabado.".encode()), ADMIN)

    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    prompt = ai_agents.montar_instrucoes(doc)
    check("os tres entram na base de conhecimento do prompt",
          all(t in prompt for t in ("48x", "usado na troca", "segunda a sabado"))
          and prompt.index("Base de conhecimento") < prompt.index("48x"), prompt[:200])
    publico = rai._publico(doc)
    check("a lista de agentes nao carrega o texto inteiro",
          all("texto" not in d and "previa" in d for d in publico["documentos"]), publico["documentos"])

    st, det = await erro(rai.subir_documento("ag-1", _upload("velho.doc", "application/msword", b"\xd0\xcf"), ADMIN))
    check(".doc antigo e recusado dizendo como salvar", st == 400 and ".docx" in det, (st, det))
    st, det = await erro(rai.subir_documento("ag-1", _upload("scan.pdf", "application/pdf", _pdf_com_texto("")), ADMIN))
    check("PDF sem texto (escaneado) e recusado com o motivo", st == 400 and "escaneada" in det, (st, det))
    st, det = await erro(rai.subir_documento("ag-1", _upload("grande.txt", "text/plain",
                                             ("x" * ai_documentos.MAX_TOTAL).encode()), ADMIN))
    check("documento que estoura o teto e recusado dizendo o tamanho", st == 400 and "caracteres" in det, (st, det))

    outro = {"id": "u-b", "tenant_id": "t-outro", "role": "tenant_admin", "name": "B", "email": "b@x.com"}
    st, _ = await erro(rai.remover_documento("ag-1", pdf["id"], outro))
    check("outra empresa nao apaga documento alheio", st == 404, st)
    await rai.remover_documento("ag-1", pdf["id"], ADMIN)
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    check("remover tira o texto do prompt", "48x" not in ai_agents.montar_instrucoes(doc))


async def etapa_confirmacao_inventada():
    print()
    print("[agente nao confirma horario que nao existe]")
    import agenda
    # O caso real: SDR de loja, chave da agenda ligada, nenhum servico cadastrado.
    await semear(agente={"tipo": "sdr", "agenda_ativa": True})
    doc = await DB.ai_agents.find_one({"id": "ag-1"})
    prompt = ai_agents.montar_instrucoes(doc)
    check("sem servico, o SDR tambem ouve que nao pode marcar", "NÃO tem acesso à agenda" in prompt)
    check("e o prompt traz a data de hoje", "Hoje é" in prompt)
    poderes = await rai.ver_poderes("ag-1", ADMIN)
    check("a tela sabe que a agenda nao tem com o que trabalhar",
          poderes["agenda_ativa"] and not poderes["agenda_pronta"] and poderes["agenda_servicos"] == 0, poderes)

    IA.proxima = "Perfeito, Italo! Sua visita está confirmada para terça-feira, dia 14, às 10h."
    await receber("pode ser terça às 10h")
    s = await sessao()
    check("a confirmacao inventada nao sai", not any("confirmada para" in t for _, t in ENVIADOS), ENVIADOS)
    check("o contato recebe o aviso neutro", ENVIADOS and ENVIADOS[-1][1] == ai_agents.AVISO_CONFIRMACAO, ENVIADOS)
    check("e a conversa vai para uma pessoa", s["status_atendimento"] == "aguardando", s["status_atendimento"])
    check("com o texto que o agente ia mandar no historico",
          any("ia confirmar um horário" in t and "terça-feira, dia 14" in t for t in await sistemas(s["id"])))

    # Conversa que segue com o agente: pergunta normal nao e afetada.
    await semear(agente={"tipo": "sdr"})
    for frase in ("Quer agendar uma visita para conhecer o carro?",
                  "Qual dia fica melhor para você?",
                  "O Clio 2012 está disponível sim.",
                  "Quer que eu reserve o passeio da Lagoa Azul?",
                  "A saída das 8h costuma ter vaga."):
        check(f"nao trava: {frase[:30]}",
              not await ai_agents.confirmou_sem_agenda(DB, TENANT, {"id": "c"}, frase, {"ok": False}))
    for frase in ("Agendei sua visita para sexta.", "Fica marcado para amanhã às 9h!",
                  "Seu horário está reservado.", "Visita confirmada, te esperamos!",
                  # Turismo: o que o agente de passeios diria ao inventar.
                  "Passeio confirmado para amanhã!", "Reserva confirmada, até sábado!",
                  "Suas vagas estão garantidas: vagas reservadas para 4 pessoas.",
                  "Saída confirmada às 8h."):
        check(f"trava: {frase[:30]}",
              await ai_agents.confirmou_sem_agenda(DB, TENANT, {"id": "c"}, frase, {"ok": False}))

    # Solicitacao gravada (o agente so registra pedido): dizer que esta
    # "confirmado" continua sendo mentira, e trava; dizer que esta reservado, nao.
    await DB.appointments.insert_one({"id": "ap-1", "tenant_id": TENANT, "contact_id": "c",
                                      "status": agenda.STATUS_AGENDADO, "inicio": "2099-01-01T13:00:00+00:00"})
    check("com so a solicitacao gravada, 'esta confirmado' trava",
          await ai_agents.confirmou_sem_agenda(DB, TENANT, {"id": "c"}, "Está confirmado para terça!", {"ok": False}))
    check("com a solicitacao gravada, 'esta reservado' passa",
          not await ai_agents.confirmou_sem_agenda(DB, TENANT, {"id": "c"}, "Seu horário está reservado.", {"ok": False}))

    # Reserva Confirmada pela agencia: confirmar de novo e verdade, nao trava.
    await DB.appointments.update_one({"id": "ap-1"}, {"$set": {"status": agenda.STATUS_CONFIRMADO}})
    check("com reserva confirmada para o contato, confirmar passa",
          not await ai_agents.confirmou_sem_agenda(DB, TENANT, {"id": "c"}, "Está confirmado para terça!", {"ok": False}))
    check("e a ferramenta agendar ter gravado agora tambem libera",
          not await ai_agents.confirmou_sem_agenda(DB, TENANT, {"id": "x"}, "Agendei para terça.", {"ok": True}))

    # Chat de teste mostra o mesmo.
    IA.proxima = "Pronto, sua visita está agendada para amanhã às 14h."
    r = await rai.testar_agente("ag-1", rai.TesteIn(mensagens=[{"role": "user", "content": "amanha 14h"}]), ADMIN)
    check("o chat de teste tambem segura", r["transferiu"] and r.get("segurada") and r["texto"] == ai_agents.AVISO_CONFIRMACAO, r)
    IA.proxima = "Claro, posso ajudar com isso."


async def etapa_frase_ao_transferir():
    print()
    print("[frase antes de transferir]")
    await semear(agente={})
    IA.proxima = "Anotei terça às 10h, vou passar para a equipe confirmar com você. [HUMANO]"
    await receber("quero marcar terça 10h")
    s = await sessao()
    check("conversa nova: a frase chega ao contato",
          ENVIADOS and ENVIADOS[-1][1].startswith("Anotei terça") and "[HUMANO]" not in ENVIADOS[-1][1], ENVIADOS)
    check("e a conversa vai para a fila", s["status_atendimento"] == "aguardando")

    await semear(agente={})
    IA.proxima = "Oi! Como posso ajudar?"
    await receber("oi")
    IA.proxima = "Vou chamar alguém da equipe. [HUMANO]"
    await receber("quero falar do meu financiamento")
    check("conversa existente: a frase tambem chega", ENVIADOS[-1][1] == "Vou chamar alguém da equipe.", ENVIADOS)
    s = await sessao()
    check("e a conversa vai para a fila", s["status_atendimento"] == "aguardando")
    IA.proxima = "Claro, posso ajudar com isso."


# ---------------------------------------------------------------------------

def test_suite():
    _conectar_fake()

    async def _tudo():
        await etapa_fallback_responde()
        await etapa_continuidade()
        await etapa_pediu_humano()
        await etapa_sentinela()
        await etapa_teto_de_respostas()
        await etapa_nao_rouba_do_bot()
        await etapa_modo_primeiro()
        await etapa_palavra_chave_ganha()
        await etapa_desligado_e_inativo()
        await etapa_amarracao_por_conexao()
        await etapa_conversa_com_humano()
        await etapa_anexo_sem_legenda()
        await etapa_falhas_nao_engolem_mensagem()
        await etapa_prompt()
        await etapa_historico()
        await etapa_contadores_do_cartao()
        await etapa_credencial_por_agente()
        await etapa_fotos()
        await etapa_upload_de_fotos()
        await etapa_treinamento()
        await etapa_estoque()
        await etapa_finalizar()
        await etapa_documentos()
        await etapa_confirmacao_inventada()
        await etapa_frase_ao_transferir()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
