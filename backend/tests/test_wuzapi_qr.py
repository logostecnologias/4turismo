"""Criar a conexão não oficial e chegar ao QR.

É o caminho que não tinha cobertura nenhuma, e ele é feito quase inteiro de
falhas que não dão erro visível — a razão de valer um arquivo próprio:

  - **Configuração faltando.** `WUZAPI_ADMIN_TOKEN` ausente levantava um
    `KeyError` cru, e quem chama embrulha toda exceção na mensagem da tela: o
    cliente lia `'WUZAPI_ADMIN_TOKEN'`, um nome de variável entre aspas, sem
    dizer que era configuração nem onde ela mora. Foi o que travou a geração
    do QR na prática.

  - **O id da instância.** A ponte muda a caixa e o nome dos campos entre
    versões, e aqui o campo errado não dá erro: o id vira vazio, a conexão
    fica sem referência à instância que existe de verdade do outro lado, o
    cartão diz "sem instância" e o QR nunca é oferecido. A instância órfã
    também nunca é apagada, porque ninguém sabe o id dela.

  - **O webhook que grava vazio.** A WuzAPI responde `200 success` e guarda
    URL vazia quando a chave não é a que ela espera. A conexão pareia, envia
    normalmente e **nunca recebe nada** — o sintoma mais caro deste canal, e o
    que manda o suporte procurar no lado errado.

  - **A barreira do prefixo.** O token de administração apaga qualquer
    instância do servidor, inclusive as de outros sistemas que compartilham a
    ponte. `nome_nosso` é a única coisa entre isso e um acidente.

Nada sai para a rede: `wuzapi_client._request` é substituído por uma ponte
falsa que guarda o que teria trafegado.
"""
import asyncio
import hashlib
import hmac as hmaclib
import json as jsonlib
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

# O conftest já põe estas em pé sob o pytest. Repetidas aqui para o arquivo
# também rodar sozinho (`python tests/test_wuzapi_qr.py`), que é como se
# acompanha a lista de verificações uma a uma — sem a chave de cifra, a
# gravação do token da instância falha antes da primeira delas.
for _chave, _valor in {
    "MONGO_URL": "mongodb://fake/", "DB_NAME": "fake", "JWT_SECRET": "teste",
    "CREDENTIALS_KEY": "VlKvCmSGH9QsR1bwg10fq1e4oiOZ_0MP3B-YxhSL1oo=",
}.items():
    os.environ.setdefault(_chave, _valor)

from fastapi import HTTPException

from fakedb import FakeDB

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import cripto
import wuzapi_client as wuzapi
import routes.whatsapp as rw
import routes.webhooks_wuzapi as rwh


def _conectar_fake():
    """(Re)aponta o `get_db` para o Mongo falso DESTE arquivo.

    Chamado no início de cada etapa, não só no import: o pytest importa todos
    os módulos de teste no mesmo processo, e `get_db` é atributo de módulo
    compartilhado. O mesmo vale para `wuzapi._request`, que é reinstalado a
    cada etapa pela `instalar`.
    """
    db_mod.get_db = lambda: DB
    rw.get_db = lambda: DB
    rwh.get_db = lambda: DB
    rwh.handle_incoming_message = _gravar


TENANT = "t-1"
ADMIN = {"id": "u-admin", "tenant_id": TENANT, "role": "tenant_admin",
         "name": "Dono", "email": "a@x.com"}
BASE = "https://exemplo.test"
URL_WEBHOOK = BASE + "/api/webhooks/wuzapi"

ok = 0

# Fronteira do receptor: o que ele decidiria entregar ao motor de conversas.
# Nada além disto roda — é justamente o ponto onde a autenticação tem de ter
# parado o que não é da ponte.
ENTREGUES = []


async def _gravar(*a, **k):
    ENTREGUES.append((a, k))


class _BG:
    """BackgroundTasks o bastante: executa na hora, para o teste ver o efeito."""

    def __init__(self):
        self.tarefas = []

    def add_task(self, fn, *a, **k):
        self.tarefas.append((fn, a, k))


def check(rotulo, condicao, extra=None):
    global ok
    if condicao:
        ok += 1
        print("  ok  " + rotulo)
    else:
        raise AssertionError("{} | {!r}".format(rotulo, extra))


class Ponte:
    """WuzAPI de mentira: responde por rota e guarda o que recebeu.

    `chave_webhook` é o detalhe que faz este dublê valer alguma coisa. A ponte
    real só grava a URL quando o corpo traz a grafia que ela espera, e responde
    `200 success` de qualquer jeito — então o dublê aceita o POST sempre e só
    registra a URL se a chave combinar. É o que permite encenar aqui a falha
    que, na produção, não deixa rastro nenhum.
    """

    def __init__(self, chave_webhook="webhook", id_criacao="inst-1",
                 chave_id="id", chave_hmac="hmacKey"):
        self.chave_webhook = chave_webhook
        self.chave_id = chave_id
        self.chave_hmac = chave_hmac
        self.id_criacao = id_criacao
        self.webhook_gravado = ""
        self.hmac_gravado = None
        self.chamadas = []
        self.apagadas = []
        self.logado = False
        self.qr = None

    async def __call__(self, method, path, headers, json=None, body=None):
        self.chamadas.append((method, path, json))
        if path == "/admin/users" and method == "POST":
            # A criação também aponta o webhook, e pela mesma chave frágil.
            if self.chave_webhook == "webhook":
                self.webhook_gravado = json.get("webhook") or ""
            # E a chave de assinatura entra pela grafia que a ponte aceitar.
            # `hmac_key` na RESPOSTA é booleano — dizer se tem, não qual é —,
            # que é exatamente o que confundia: mandar a chave no nome errado
            # volta `false` com `200 success`.
            if self.chave_hmac and self.chave_hmac in json:
                self.hmac_gravado = json[self.chave_hmac]
            return {"data": {self.chave_id: self.id_criacao, "name": json["name"],
                             "hmac_key": bool(self.hmac_gravado)}}
        if path == "/admin/users" and method == "GET":
            return {"data": [{"id": self.id_criacao, "name": "zz_x",
                              "hmac_key": bool(self.hmac_gravado)}]}
        if path.startswith("/admin/users/") and method == "DELETE":
            self.apagadas.append(path.rsplit("/", 1)[-1])
            return {"data": {"Details": "User deleted"}}
        if path == "/webhook" and method == "POST":
            if self.chave_webhook in json:
                self.webhook_gravado = json[self.chave_webhook]
            return {"data": {"Details": "Webhook updated"}}
        if path == "/webhook":
            return {"data": {"webhook": self.webhook_gravado,
                             "subscribe": ["Message"]}}
        if path == "/session/connect":
            self.qr = "data:image/png;base64,QVFS"
            return {"data": {"Details": "Connected!"}}
        if path == "/session/qr":
            return {"data": {"QRCode": self.qr}}
        if path == "/session/status":
            jid = "5585999998888:12@s.whatsapp.net" if self.logado else ""
            return {"data": {"connected": True, "loggedIn": self.logado, "jid": jid}}
        raise AssertionError("rota não prevista no dublê: {} {}".format(method, path))


class _Req:
    """Request o bastante para o receptor: corpo cru e cabeçalhos."""

    def __init__(self, corpo, headers=None):
        self._corpo = corpo
        self.headers = headers if headers is not None else {}

    async def body(self):
        return self._corpo


def instalar(ponte):
    wuzapi._request = ponte
    return ponte


async def criar(nome="Comercial"):
    return await rw.create_connection(
        rw.ConnectionIn(nome=nome, canal="wuzapi"), user=ADMIN)


# ---------------------------------------------------------------------------


async def etapa_sem_token_de_admin():
    print("\n[configuração faltando: a falha que travou o QR]")
    _conectar_fake()
    os.environ["PUBLIC_BASE_URL"] = BASE
    os.environ.pop("WUZAPI_ADMIN_TOKEN", None)
    instalar(Ponte())

    try:
        await wuzapi.create_instance("zz_x", "tok", URL_WEBHOOK)
        raise AssertionError("criou instância sem token de administração")
    except wuzapi.WuzapiError as e:
        check("erro é WuzapiError, não KeyError", True)
        check("a mensagem nomeia a variável que falta",
              "WUZAPI_ADMIN_TOKEN" in str(e), str(e))
        check("e explica para que ela serve, em vez de só repetir o nome",
              len(str(e)) > 60 and "instância" in str(e), str(e))

    # E o efeito disso pela rota: a conexão não pode ficar para trás.
    try:
        await criar()
        raise AssertionError("criou a conexão sem conseguir provisionar")
    except HTTPException as e:
        check("a rota recusa com 400", e.status_code == 400, e.status_code)
        check("dizendo qual configuração falta",
              "WUZAPI_ADMIN_TOKEN" in str(e.detail), e.detail)
    check("e não deixa conexão órfã no banco",
          await DB.whatsapp_connections.count_documents({}) == 0)


async def etapa_sem_url_publica():
    print("\n[sem PUBLIC_BASE_URL: recusa antes de tocar na ponte]")
    _conectar_fake()
    os.environ["WUZAPI_ADMIN_TOKEN"] = "admin-de-teste"
    os.environ.pop("PUBLIC_BASE_URL", None)
    ponte = instalar(Ponte())

    try:
        await criar()
        raise AssertionError("criou conexão sem saber o endereço do webhook")
    except HTTPException as e:
        check("recusa com a mensagem da configuração",
              "PUBLIC_BASE_URL" in str(e.detail), e.detail)
    # O ponto da verificação adiantada: nem chega a criar instância para
    # depois desfazer, nem grava documento para depois apagar.
    check("nenhuma chamada à ponte", ponte.chamadas == [], ponte.chamadas)
    check("nenhum documento gravado",
          await DB.whatsapp_connections.count_documents({}) == 0)
    os.environ["PUBLIC_BASE_URL"] = BASE


async def etapa_criacao_feliz():
    print("\n[criação: instância, token cifrado e webhook apontado]")
    _conectar_fake()
    ponte = instalar(Ponte())
    conn = await criar()

    check("a conexão nasce apontando para a instância",
          conn["wuzapi_id"] == "inst-1", conn)
    check("com o nome prefixado, que é a barreira de exclusão",
          conn["wuzapi_nome"] == "zz_" + conn["id"], conn)
    check("e o webhook dado por assinado", conn["webhook_assinado"] is True, conn)
    check("sem aviso", not conn.get("aviso"), conn)

    doc = await DB.whatsapp_connections.find_one({"id": conn["id"]})
    check("o token da instância fica cifrado em repouso",
          isinstance(doc["token_cifrado"], dict) and doc["token_cifrado"] != {},
          doc.get("token_mascara"))
    check("e é recuperável para uso", len(cripto.decifrar(doc["token_cifrado"])) > 20)
    check("a chave de assinatura do webhook nasce junto",
          len(doc["wuzapi_hmac"]) == 64, doc.get("wuzapi_hmac"))

    # Nem o token nem a chave de assinatura podem voltar para o navegador.
    check("o token cifrado não vai para a tela", "token_cifrado" not in conn, conn)
    check("a chave de assinatura também não", "wuzapi_hmac" not in conn, conn)

    # A URL não é mais uma só: ela carrega o segredo que autentica o evento,
    # porque a ponte não assina o que envia. O que precisa bater é o segredo
    # do fim da URL com o que ficou guardado — se divergirem, a ponte posta
    # num endereço que não encontra conexão nenhuma e nada entra.
    check("a ponte recebeu a URL de webhook com o segredo",
          ponte.webhook_gravado.startswith(URL_WEBHOOK + "/"), ponte.webhook_gravado)
    segredo = ponte.webhook_gravado.rsplit("/", 1)[-1]
    check("e o segredo é o mesmo guardado na conexão",
          segredo == doc["wuzapi_webhook_token"], segredo)
    check("o segredo é longo o bastante para não ser adivinhado",
          len(segredo) >= 32, len(segredo))
    check("e não vai para a tela", "wuzapi_webhook_token" not in conn, conn)

    # A chave de assinatura continua sendo mandada — não custa e passa a valer
    # se a chave global for configurada um dia —, mas já não é o que autentica.
    check("a chave de assinatura ainda é configurada na ponte",
          ponte.hmac_gravado is not None, ponte.hmac_gravado)
    check("e é a mesma que ficou guardada aqui",
          ponte.hmac_gravado == doc["wuzapi_hmac"], ponte.hmac_gravado)


async def etapa_id_em_outra_grafia():
    print("\n[id noutra grafia: a conexão não pode ficar sem referência]")
    _conectar_fake()
    instalar(Ponte(id_criacao=77, chave_id="userID"))
    conn = await criar("Suporte")
    check("o id é encontrado mesmo fora da grafia canônica",
          conn["wuzapi_id"] == "77", conn)

    # E quando não há id nenhum, falhar alto é melhor que gravar vazio: uma
    # conexão sem id nunca oferece o QR e nunca apaga a instância.
    _conectar_fake()
    instalar(Ponte(chave_id="algo_que_nao_e_id"))
    try:
        await criar("Sem id")
        raise AssertionError("aceitou instância sem id")
    except HTTPException as e:
        check("recusa quando a ponte não devolve id reconhecível",
              "id" in str(e.detail).lower(), e.detail)
    check("e desfaz a conexão em vez de deixá-la sem instância",
          await DB.whatsapp_connections.count_documents({"nome": "Sem id"}) == 0)


async def etapa_webhook_gravado_vazio():
    print("\n[a ponte aceita o webhook e grava vazio: precisa aparecer]")
    _conectar_fake()
    # A criação não registra (chave diferente) e nenhuma grafia do laço casa:
    # é a falha que responde 200 e não deixa rastro.
    ponte = instalar(Ponte(chave_webhook="uma_chave_que_nao_existe"))
    conn = await criar("Vendas")

    check("a conexão é criada assim mesmo — ela envia",
          conn["wuzapi_id"] == "inst-1", conn)
    check("mas o webhook não é dado por assinado",
          conn["webhook_assinado"] is False, conn)
    check("e o aviso chega à tela em vez de sumir",
          "webhook" in (conn.get("aviso") or "").lower(), conn.get("aviso"))
    check("dizendo a consequência: envia e não recebe",
          "não chegam" in (conn.get("aviso") or ""), conn.get("aviso"))
    check("a ponte de fato ficou sem webhook", ponte.webhook_gravado == "",
          ponte.webhook_gravado)


async def etapa_ponte_sem_assinatura_nao_e_erro():
    print("\n[ponte que ignora a chave: deixou de ser impedimento]")
    _conectar_fake()
    # A ponte real se comporta exatamente assim: aceita a chave, responde 200 e
    # nunca assina. Isso derrubava toda mensagem com 401; agora quem autentica
    # é o segredo da URL, então a conexão nasce utilizável do mesmo jeito.
    ponte = instalar(Ponte(chave_hmac="uma_grafia_que_a_ponte_ignora"))
    conn = await criar("Sem assinatura")

    check("a conexão é criada", conn["wuzapi_id"] == "inst-1", conn)
    check("e o caminho de volta é dado por pronto",
          conn["webhook_assinado"] is True, conn)
    check("sem aviso, porque não há nada a consertar",
          not conn.get("aviso"), conn.get("aviso"))
    check("a ponte de fato ficou sem chave de assinatura",
          ponte.hmac_gravado is None)
    check("e mesmo assim a URL levou o segredo",
          ponte.webhook_gravado.startswith(URL_WEBHOOK + "/")
          and len(ponte.webhook_gravado.rsplit("/", 1)[-1]) >= 32,
          ponte.webhook_gravado)


async def etapa_webhook_em_outra_grafia():
    print("\n[grafia diferente: o laço de set_webhook resolve sozinho]")
    _conectar_fake()
    ponte = instalar(Ponte(chave_webhook="webhookURL"))
    conn = await criar("Financeiro")
    check("o webhook acaba configurado", conn["webhook_assinado"] is True, conn)
    check("na URL certa, com o segredo",
          ponte.webhook_gravado.startswith(URL_WEBHOOK + "/"),
          ponte.webhook_gravado)


async def etapa_conectar_e_qr():
    print("\n[conectar e ler o QR]")
    _conectar_fake()
    ponte = instalar(Ponte())
    conn = await criar("QR")

    r = await rw.conectar_wuzapi(conn["id"], user=ADMIN)
    check("conectar põe a conexão em pareando", r["status"] == "pareando", r)
    doc = await DB.whatsapp_connections.find_one({"id": conn["id"]})
    check("e isso fica gravado", doc["status"] == "pareando", doc["status"])

    q = await rw.qr_wuzapi(conn["id"], user=ADMIN)
    check("o QR volta para a tela", (q["qr"] or "").startswith("data:image/"), q)
    check("ainda pareando", q["status"] == "pareando", q)

    # O celular leu: a próxima consulta é o que troca a tela.
    ponte.logado = True
    q = await rw.qr_wuzapi(conn["id"], user=ADMIN)
    check("depois da leitura a conexão fica conectada",
          q["status"] == "conectado", q)
    check("sem QR sobrando na tela", q["qr"] is None, q)
    check("e com o número do aparelho, sem o sufixo de dispositivo",
          q["numero"] == "5585999998888", q)
    doc = await DB.whatsapp_connections.find_one({"id": conn["id"]})
    check("o QR consumido é limpo do banco", doc["qrcode_atual"] is None, doc)


async def etapa_exclusao_respeita_o_prefixo():
    print("\n[exclusão: só apaga instância nossa]")
    _conectar_fake()
    ponte = instalar(Ponte())
    conn = await criar("Descartável")
    await rw.delete_connection(conn["id"], user=ADMIN)
    check("a instância criada por nós é apagada na ponte",
          ponte.apagadas == ["inst-1"], ponte.apagadas)
    check("e a conexão sai do banco",
          await DB.whatsapp_connections.count_documents({"id": conn["id"]}) == 0)

    # Instância apontada à mão (sem o prefixo) é só desvinculada. O token de
    # administração apaga qualquer instância do servidor, inclusive a ponte de
    # WhatsApp de outro sistema.
    _conectar_fake()
    ponte = instalar(Ponte())
    conn = await criar("De fora")
    await DB.whatsapp_connections.update_one(
        {"id": conn["id"]},
        {"$set": {"wuzapi_id": "financas-1", "wuzapi_nome": "financas"}})
    await rw.delete_connection(conn["id"], user=ADMIN)
    check("instância de outro sistema NÃO é apagada",
          ponte.apagadas == [], ponte.apagadas)
    check("mas a conexão sai do banco do mesmo jeito",
          await DB.whatsapp_connections.count_documents({"id": conn["id"]}) == 0)

    check("e a barreira recusa por dentro também",
          not wuzapi.nome_nosso("financas") and not wuzapi.nome_nosso(None)
          and not wuzapi.nome_nosso("") and wuzapi.nome_nosso("zz_abc"))


async def etapa_receptor_exige_o_segredo():
    print("\n[receptor: o segredo da URL é a autenticação]")
    _conectar_fake()
    instalar(Ponte())
    conn = await criar("Receptor")
    doc = await DB.whatsapp_connections.find_one({"id": conn["id"]})
    segredo = doc["wuzapi_webhook_token"]

    corpo = jsonlib.dumps({
        "userID": "inst-1", "type": "Message",
        "event": {"Info": {"Chat": "5585999990000@s.whatsapp.net",
                           "ID": "MSG1", "PushName": "Fulano",
                           "IsFromMe": False, "Type": "text"},
                  "Message": {"conversation": "oi"}},
    }).encode()

    ENTREGUES.clear()
    r = await rwh.webhook_wuzapi(segredo, _Req(corpo), _BG())
    check("com o segredo certo, o evento é aceito", r.get("ok") is not False, r)

    # A barreira. Sem ela, qualquer um que descubra o endereço posta mensagem
    # na conversa de qualquer empresa — o endpoint alimenta o motor e está na
    # internet.
    for errado in ("segredo-errado", segredo[:-1], segredo.upper()):
        try:
            await rwh.webhook_wuzapi(errado, _Req(corpo), _BG())
            raise AssertionError(f"aceitou segredo errado: {errado!r}")
        except HTTPException as e:
            check(f"segredo errado recusado ({errado[:14]}…)", e.status_code == 404,
                  e.status_code)

    check("e a recusa é 404, não 401: não confirma que a rota existe",
          True)

    # A rota antiga resolvia a conexão pelo `userID` do corpo. Sem assinatura,
    # o corpo é escolhido por quem chama.
    try:
        await rwh.webhook_wuzapi_sem_segredo(_Req(corpo))
        raise AssertionError("a rota sem segredo aceitou o evento")
    except HTTPException as e:
        check("a rota sem segredo recusa", e.status_code == 404, e.status_code)


async def etapa_assinatura_so_quando_da_para_conferir():
    print("\n[assinatura: desarmada por padrão, exigente quando armada]")
    corpo = b'{"userID":"inst-1"}'
    os.environ.pop("WUZAPI_GLOBAL_HMAC_KEY", None)
    # Sem chave global não há o que conferir, e exigir assinatura era o defeito
    # que recusava todas as mensagens.
    rwh._validar_assinatura_se_houver(corpo, _Req(corpo, {}))
    check("sem chave global, evento sem assinatura passa", True)

    os.environ["WUZAPI_GLOBAL_HMAC_KEY"] = "chave-global"
    rwh._validar_assinatura_se_houver(corpo, _Req(corpo, {}))
    check("com chave global, evento SEM cabeçalho ainda passa "
          "(a ponte não assina)", True)

    certa = hmaclib.new(b"chave-global", corpo, hashlib.sha256).hexdigest()
    rwh._validar_assinatura_se_houver(corpo, _Req(corpo, {"x-hmac-signature": certa}))
    check("assinatura correta passa", True)

    try:
        rwh._validar_assinatura_se_houver(
            corpo, _Req(corpo, {"x-hmac-signature": "0" * 64}))
        raise AssertionError("aceitou assinatura errada")
    except HTTPException as e:
        check("assinatura ERRADA recusa: aí é corpo adulterado, não ausência",
              e.status_code == 401, e.status_code)
    os.environ.pop("WUZAPI_GLOBAL_HMAC_KEY", None)


async def _tudo():
    await etapa_sem_token_de_admin()
    await etapa_sem_url_publica()
    await etapa_criacao_feliz()
    await etapa_id_em_outra_grafia()
    await etapa_webhook_gravado_vazio()
    await etapa_ponte_sem_assinatura_nao_e_erro()
    await etapa_webhook_em_outra_grafia()
    await etapa_conectar_e_qr()
    await etapa_receptor_exige_o_segredo()
    await etapa_assinatura_so_quando_da_para_conferir()
    await etapa_exclusao_respeita_o_prefixo()
    print("\n{} verificações passaram".format(ok))


def test_suite():
    asyncio.run(_tudo())


if __name__ == "__main__":
    test_suite()
