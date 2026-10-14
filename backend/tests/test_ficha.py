"""Teste da ficha de contato e da mensagem direta, com Mongo falso."""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
os.environ.setdefault("MONGO_URL", "mongodb://x/")
os.environ.setdefault("DB_NAME", "x")
os.environ.setdefault("JWT_SECRET", "x")
os.environ.setdefault("META_PLATFORM_TOKEN", "token-de-teste")

from fakedb import FakeDB
from fastapi import HTTPException

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import flow_runtime
import outbound
import routes.contacts as rc

for mod in (flow_runtime, outbound, rc):
    mod.get_db = lambda: DB

ENVIADOS = []


async def fake_send_text(cred, numero, texto):
    ENVIADOS.append((cred.phone_number_id, numero, texto))
    return f"wamid.{len(ENVIADOS)}"


async def fake_send_template(cred, numero, nome, idioma, componentes=None):
    valores = []
    for c in (componentes or []):
        if c.get("type") == "body":
            valores = [p.get("text") for p in c.get("parameters") or []]
    ENVIADOS.append((cred.phone_number_id, numero, f"[modelo {nome}]", valores))
    return f"wamid.{len(ENVIADOS)}"


flow_runtime.whatsapp.send_text = fake_send_text
flow_runtime.whatsapp.send_template = fake_send_template



_MODULOS_FAKE = (flow_runtime, outbound, rc)


def _conectar_fake():
    """(Re)aponta o `get_db` de cada módulo para o Mongo falso DESTE arquivo.

    Precisa ser chamado no início do teste, não só no import: o pytest importa
    todos os módulos de teste no mesmo processo, e `get_db` é atributo de módulo
    compartilhado — sem isto, o último arquivo importado ficaria com o banco de
    todos, e os testes anteriores rodariam contra dados de outra suíte.
    """
    db_mod.get_db = lambda: DB
    for _m in _MODULOS_FAKE:
        _m.get_db = lambda: DB
    flow_runtime.whatsapp.send_text = fake_send_text
    flow_runtime.whatsapp.send_template = fake_send_template


TENANT = "t-1"
CONN = "conn-1"
CONN2 = "conn-2"
ADMIN = {"id": "u-admin", "tenant_id": TENANT, "name": "Marcos Vendas", "email": "m@x.com",
         "role": "tenant_admin"}
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
    """Roda e devolve (status, detail) da HTTPException levantada."""
    try:
        await coro
    except HTTPException as e:
        return e.status_code, str(e.detail)
    return None, None


async def semear():
    DB._cols.clear()
    await DB.tenants.insert_one({"id": TENANT, "plan_id": None})
    await DB.users.insert_one(dict(ADMIN))
    await DB.users.insert_one({"id": "u-outra", "tenant_id": "t-2", "name": "De outra empresa",
                               "email": "o@x.com"})
    await DB.whatsapp_connections.insert_one({
        "id": CONN, "tenant_id": TENANT, "nome": "Comercial", "status": "conectado",
        "phone_number_id": "pn-teste", "waba_id": "waba-teste", "departamento_padrao_id": "dep-1"})
    await DB.whatsapp_connections.insert_one({
        "id": CONN2, "tenant_id": TENANT, "nome": "Suporte", "status": "conectado",
        "phone_number_id": "pn-teste", "waba_id": "waba-teste"})


async def etapa_criacao_e_dados():
    print("\n[1] Cadastro e dados da ficha")
    await semear()
    c = await rc.create_contact(rc.ContactIn(nome="Maria Silva", numero_whatsapp="+55 (11) 90000-0001"), ADMIN)
    check("numero normalizado para digitos", c["numero_whatsapp"] == "5511900000001", c["numero_whatsapp"])
    check("origem marcada como cadastro manual", c["origem"] == "cadastro manual", c["origem"])
    check("nasce sem opt-out", c["opt_out"] is False)

    st, det = await erro(rc.create_contact(
        rc.ContactIn(nome="Maria de novo", numero_whatsapp="5511900000001"), ADMIN))
    check("numero duplicado recusado com 409", st == 409, (st, det))

    atualizado = await rc.update_contact(c["id"], rc.ContactPatch(
        email="maria@empresa.com", empresa="Acme", cargo="Compras",
        origem="indicacao", tags=["VIP", "vip ", " cliente"]), ADMIN)
    check("email gravado", atualizado["email"] == "maria@empresa.com", atualizado["email"])
    check("empresa e cargo gravados",
          atualizado["empresa"] == "Acme" and atualizado["cargo"] == "Compras", atualizado)
    check("etiqueta repetida deduplicada", atualizado["tags"] == ["VIP", "cliente"], atualizado["tags"])

    st, det = await erro(rc.update_contact(c["id"], rc.ContactPatch(email="isso-nao-e-email"), ADMIN))
    check("email invalido recusado", st == 400 and "mail" in det.lower(), (st, det))

    st, det = await erro(rc.update_contact(c["id"], rc.ContactPatch(nome="   "), ADMIN))
    check("nome vazio recusado", st == 400, (st, det))

    st, det = await erro(rc.update_contact(c["id"], rc.ContactPatch(responsavel_id="u-outra"), ADMIN))
    check("responsavel de outra empresa recusado", st == 400, (st, det))

    await rc.update_contact(c["id"], rc.ContactPatch(responsavel_id="u-admin"), ADMIN)
    ficha = await rc.contact_detail(c["id"], ADMIN)
    check("responsavel resolvido na ficha", ficha["responsavel"]["name"] == "Marcos Vendas", ficha["responsavel"])
    return c


async def etapa_campos_personalizados(contato):
    print("\n[2] Campos personalizados")
    num = await rc.create_field_def(rc.FieldDefIn(nome="Ticket medio", tipo="numero", ordem=1), ADMIN)
    sel = await rc.create_field_def(
        rc.FieldDefIn(nome="Plano", tipo="selecao", opcoes=["Bronze", "Ouro"], ordem=0), ADMIN)
    dat = await rc.create_field_def(rc.FieldDefIn(nome="Renovacao", tipo="data", ordem=2), ADMIN)

    st, det = await erro(rc.create_field_def(rc.FieldDefIn(nome="Sem opcoes", tipo="selecao"), ADMIN))
    check("selecao sem opcao recusada", st == 400, (st, det))

    await rc.update_contact(contato["id"], rc.ContactPatch(campos={
        num["id"]: "1.250,50", sel["id"]: "Ouro", dat["id"]: "2027-01-15"}), ADMIN)
    ficha = await rc.contact_detail(contato["id"], ADMIN)
    valores = {c["nome"]: c["valor"] for c in ficha["campos"]}
    check("numero com virgula convertido", valores["Ticket medio"] == 1250.50, valores)
    check("selecao gravada", valores["Plano"] == "Ouro", valores)
    check("data gravada", valores["Renovacao"] == "2027-01-15", valores)
    check("campos vem na ordem configurada",
          [c["nome"] for c in ficha["campos"]] == ["Plano", "Ticket medio", "Renovacao"],
          [c["nome"] for c in ficha["campos"]])

    st, det = await erro(rc.update_contact(contato["id"], rc.ContactPatch(campos={num["id"]: "abc"}), ADMIN))
    check("texto em campo numerico recusado", st == 400 and "informe um" in det.lower(), (st, det))

    st, det = await erro(rc.update_contact(contato["id"], rc.ContactPatch(campos={sel["id"]: "Prata"}), ADMIN))
    check("opcao fora da lista recusada", st == 400, (st, det))

    st, det = await erro(rc.update_contact(contato["id"], rc.ContactPatch(campos={dat["id"]: "15/01/2027"}), ADMIN))
    check("data em formato errado recusada", st == 400, (st, det))

    # valor de definicao apagada e descartado, nao recusado
    await rc.update_contact(contato["id"], rc.ContactPatch(campos={"nao-existe": "x", sel["id"]: "Bronze"}), ADMIN)
    ficha = await rc.contact_detail(contato["id"], ADMIN)
    check("valor de campo inexistente descartado sem erro",
          "nao-existe" not in (ficha["contact"].get("campos") or {}), ficha["contact"].get("campos"))

    await rc.delete_field_def(sel["id"], ADMIN)
    ficha = await rc.contact_detail(contato["id"], ADMIN)
    check("apagar o campo tira o valor da ficha",
          sel["id"] not in (ficha["contact"].get("campos") or {}), ficha["contact"].get("campos"))
    check("os outros campos seguem", len(ficha["campos"]) == 2, len(ficha["campos"]))


async def etapa_notas(contato):
    print("\n[3] Anotacoes")
    n1 = await rc.create_note(contato["id"], rc.NotaIn(texto="Ligou dia 12, quer proposta"), ADMIN)
    await rc.create_note(contato["id"], rc.NotaIn(texto="Segunda anotacao"), ADMIN)
    check("autor gravado pelo campo 'name'", n1["autor_nome"] == "Marcos Vendas", n1["autor_nome"])

    st, det = await erro(rc.create_note(contato["id"], rc.NotaIn(texto="   "), ADMIN))
    check("anotacao vazia recusada", st == 400, (st, det))
    st, det = await erro(rc.create_note("nao-existe", rc.NotaIn(texto="x"), ADMIN))
    check("anotacao em contato inexistente da 404", st == 404, (st, det))

    ficha = await rc.contact_detail(contato["id"], ADMIN)
    check("duas anotacoes na ficha", len(ficha["notas"]) == 2, len(ficha["notas"]))
    check("mais recente primeiro", ficha["notas"][0]["texto"] == "Segunda anotacao", ficha["notas"][0])

    await rc.delete_note(contato["id"], n1["id"], ADMIN)
    ficha = await rc.contact_detail(contato["id"], ADMIN)
    check("anotacao excluida", len(ficha["notas"]) == 1, len(ficha["notas"]))


async def etapa_envio_direto():
    print("\n[4] Mensagem direta da ficha")
    await semear()
    c = await rc.create_contact(rc.ContactIn(nome="Joao Souza", numero_whatsapp="5511900000002"), ADMIN)
    ENVIADOS.clear()

    # Contato que nunca escreveu esta fora da janela de 24h, e ai o WhatsApp so
    # entrega modelo aprovado. Texto livre e recusado com 409 e o motivo, em vez
    # de "falha ao enviar", que mandaria o atendente clicar de novo para sempre.
    st, det = await erro(rc.send_to_contact(c["id"], rc.EnvioIn(texto="Oi Joao"), ADMIN))
    check("texto livre para contato frio e recusado", st == 409, (st, det))
    check("e o motivo explica a janela de 24h", "24 horas" in (det or ""), det)
    check("nada foi enviado", len(ENVIADOS) == 0, ENVIADOS)

    res = await rc.send_to_contact(
        c["id"],
        rc.EnvioIn(texto="Oi Joao, tudo bem?",
                   template=rc.TemplateEnvioIn(nome="primeiro_contato", idioma="pt_BR",
                                               valores=["Joao"])),
        ADMIN)
    check("conversa criada no primeiro envio", res["criada"] is True, res)
    check("modelo saiu pela Cloud API", len(ENVIADOS) == 1, ENVIADOS)
    check("com a variavel preenchida", ENVIADOS[0][3] == ["Joao"], ENVIADOS[0])

    s = await DB.conversation_sessions.find_one({"contact_id": c["id"]})
    check("conversa ja nasce em atendimento", s["status_atendimento"] == "em_atendimento", s["status_atendimento"])
    check("com quem enviou como atendente", s["atendente_id"] == "u-admin", s["atendente_id"])
    check("origem marcada como direta", s["origem"] == "direta", s["origem"])
    check("departamento padrao da conexao propagado", s["departamento_id"] == "dep-1", s["departamento_id"])

    msgs = await DB.messages_log.find({"session_id": s["id"]}).to_list(None)
    check("mensagem no historico", len(msgs) == 1, len(msgs))
    check("historico guarda o texto limpo, sem o prefixo",
          msgs[0]["content"] == "Oi Joao, tudo bem?", msgs[0]["content"])
    check("previa da conversa preenchida", bool(s.get("last_message")), s.get("last_message"))

    # segundo envio reaproveita a conversa (a janela segue fechada: o contato
    # ainda nao respondeu, entao continua sendo caso de modelo)
    res2 = await rc.send_to_contact(
        c["id"],
        rc.EnvioIn(texto="Segunda", template=rc.TemplateEnvioIn(nome="primeiro_contato")),
        ADMIN)
    check("segundo envio reaproveita a conversa", res2["criada"] is False, res2)
    check("mesma conversa", res2["session_id"] == s["id"], (res2, s["id"]))
    check("uma conversa so", await DB.conversation_sessions.count_documents({"contact_id": c["id"]}) == 1)

    st, det = await erro(rc.send_to_contact(c["id"], rc.EnvioIn(texto="   "), ADMIN))
    check("mensagem vazia recusada", st == 400, (st, det))

    # a resposta do contato nao pode acordar bot nenhum
    ENVIADOS.clear()
    await flow_runtime.handle_incoming_message(TENANT, "5511900000002", "tudo otimo", "m-1",
                                               connection_id=CONN)
    check("resposta nao dispara bot", len(ENVIADOS) == 0, ENVIADOS)
    s = await DB.conversation_sessions.find_one({"contact_id": c["id"]})
    check("conversa segue com o mesmo atendente", s["atendente_id"] == "u-admin", s["atendente_id"])
    check("nao lida incrementada", s.get("unread_count", 0) >= 1, s.get("unread_count"))
    check("a resposta do contato abriu a janela de 24h",
          bool(s.get("janela_expira_em")), s.get("janela_expira_em"))

    # Com a janela aberta, texto livre volta a ser o caminho normal: e o mesmo
    # envio do painel de atendimento, com o nome de quem escreveu na frente.
    ENVIADOS.clear()
    await rc.send_to_contact(c["id"], rc.EnvioIn(texto="Que bom, Joao!"), ADMIN)
    check("com a janela aberta, o texto livre sai", len(ENVIADOS) == 1, ENVIADOS)
    check("saiu com o nome de quem enviou",
          ENVIADOS[0][2].startswith("*Marcos Vendas:*"), ENVIADOS[0][2])
    check("texto limpo vai no corpo", "Que bom, Joao!" in ENVIADOS[0][2], ENVIADOS[0][2])
    return c


async def etapa_escolha_de_conexao(contato):
    print("\n[5] De qual numero a mensagem sai")
    # ja existe conversa em CONN -> sem escolha explicita, usa aquela
    conn = await rc._resolve_conexao(DB, TENANT, contato, None, ADMIN)
    check("prefere a conexao da ultima conversa", conn["id"] == CONN, conn["id"])

    conn = await rc._resolve_conexao(DB, TENANT, contato, CONN2, ADMIN)
    check("respeita a escolha explicita", conn["id"] == CONN2, conn["id"])

    st, det = await erro(rc._resolve_conexao(DB, TENANT, contato, "conn-de-outra-empresa", ADMIN))
    check("conexao invalida recusada", st == 400, (st, det))

    await DB.whatsapp_connections.update_one({"id": CONN}, {"$set": {"status": "desconectado"}})
    st, det = await erro(rc._resolve_conexao(DB, TENANT, contato, CONN, ADMIN))
    check("conexao desconectada recusada com motivo", st == 400 and "desconectado" in det, (st, det))

    conn = await rc._resolve_conexao(DB, TENANT, contato, None, ADMIN)
    check("cai para outra conexao conectada", conn["id"] == CONN2, conn["id"])

    await DB.whatsapp_connections.update_many({"tenant_id": TENANT}, {"$set": {"status": "desconectado"}})
    st, det = await erro(rc._resolve_conexao(DB, TENANT, contato, None, ADMIN))
    check("sem nenhuma conectada, erro claro", st == 400 and "conectada" in det, (st, det))


async def etapa_ficha_detalhe():
    print("\n[6] Ficha de um contato novo e de um inexistente")
    await semear()
    c = await rc.create_contact(rc.ContactIn(nome="Ana Lima", numero_whatsapp="5511900000003"), ADMIN)
    ficha = await rc.contact_detail(c["id"], ADMIN)
    check("ficha abre com o resumo", ficha["resumo"]["conversas"] == 0, ficha["resumo"])

    st, det = await erro(rc.contact_detail("nao-existe", ADMIN))
    check("ficha de contato inexistente da 404", st == 404, (st, det))


async def etapa_isolamento():
    print("\n[7] Isolamento entre empresas")
    outro = {"id": "u-b", "tenant_id": "t-2", "name": "Admin B", "email": "b@x.com"}
    c = await DB.contacts.find_one({"tenant_id": TENANT})
    st, det = await erro(rc.contact_detail(c["id"], outro))
    check("ficha de outra empresa da 404", st == 404, (st, det))
    st, det = await erro(rc.update_contact(c["id"], rc.ContactPatch(nome="invadido"), outro))
    check("patch de outra empresa da 404", st == 404, (st, det))
    st, det = await erro(rc.send_to_contact(c["id"], rc.EnvioIn(texto="oi"), outro))
    check("envio de outra empresa da 404", st == 404, (st, det))
    lista = await rc.list_contacts(user=outro)
    check("lista de outra empresa vem vazia", lista["total"] == 0, lista["total"])



# ---------------------------------------------------------------------------
# Entradas do pytest.
#
# Uma função por etapa, na ordem em que foram escritas: as etapas compartilham
# o estado do Mongo falso deste módulo e cada uma continua de onde a anterior
# parou. O pytest.ini usa `--dist loadscope`, que fixa o módulo inteiro num
# worker só — é o que preserva essa ordem sob execução paralela.
# ---------------------------------------------------------------------------

def test_suite():
    _conectar_fake()

    async def _tudo():
        contato = await etapa_criacao_e_dados()
        await etapa_campos_personalizados(contato)
        await etapa_notas(contato)
        direto = await etapa_envio_direto()
        await etapa_escolha_de_conexao(direto)
        await etapa_ficha_detalhe()
        await etapa_isolamento()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
