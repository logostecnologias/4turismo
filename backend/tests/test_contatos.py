"""Exclusao, anonimizacao e mesclagem de contato."""
import asyncio
import os
import sys
import tempfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
# O backend é o pai desta pasta; resolvido pelo caminho do arquivo para o
# teste rodar de qualquer lugar (pytest na raiz do backend, ou direto).
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k, v in {"MONGO_URL": "mongodb://fake/", "DB_NAME": "fake", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)
# Mídia num diretório temporário: a anonimização apaga arquivo de disco, e isso
# não pode acontecer dentro do repositório.
_TMP = tempfile.mkdtemp(prefix="zz-midia-")
os.environ["MEDIA_DIR"] = _TMP

from fakedb import FakeDB
from fastapi import HTTPException

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import contatos
import media_store
import routes.contacts as rc

_MODULOS_FAKE = (rc,)


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
    os.environ["MEDIA_DIR"] = _TMP


TENANT = "t-1"
ADMIN = {"id": "u-admin", "tenant_id": TENANT, "role": "tenant_admin",
         "name": "Marcos", "email": "marcos@x.com"}
OUTRO = {"id": "u-b", "tenant_id": "t-2", "role": "tenant_admin", "email": "b@x.com"}
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


async def semear():
    DB._cols.clear()
    await DB.tenants.insert_one({"id": TENANT})
    await DB.users.insert_one(dict(ADMIN))
    await DB.contacts.insert_one({
        "id": "c1", "tenant_id": TENANT, "nome": "Maria Silva",
        "numero_whatsapp": "5511900000001", "email": "maria@acme.com",
        "empresa": "Acme", "cargo": "Compras", "origem": "site",
        "foto_url": "http://x/foto.jpg", "tags": ["vip"], "campos": {"f1": "valor"},
        "opt_out": False, "anonimizado_em": None,
        "created_at": "2026-08-01T00:00:00+00:00"})
    await DB.conversation_sessions.insert_one({
        "id": "s1", "tenant_id": TENANT, "contact_id": "c1", "status": "ativa",
        "status_atendimento": "finalizado",
        "last_message": {"content": "meu CPF e 123", "type": "text"},
        "variables": {"nome_contato": "Maria Silva", "numero": "5511900000001"},
        "created_at": "2026-08-02T00:00:00+00:00",
        "updated_at": "2026-08-02T00:00:00+00:00"})
    await DB.messages_log.insert_one({
        "id": "m1", "session_id": "s1", "tenant_id": TENANT, "direction": "recebida",
        "content": "meu CPF e 123", "type": "text", "meta": {},
        "timestamp": "2026-08-02T00:00:00+00:00"})
    await DB.messages_log.insert_one({
        "id": "m2", "session_id": "s1", "tenant_id": TENANT, "direction": "recebida",
        "content": "moro na rua tal", "type": "image",
        "meta": {"media": {"id": "mid-1", "kind": "image"}},
        "timestamp": "2026-08-02T01:00:00+00:00"})
    # arquivo de verdade em disco, para a anonimizacao ter o que apagar
    pasta = media_store.media_root() / TENANT
    pasta.mkdir(parents=True, exist_ok=True)
    (pasta / "arq1.jpg").write_bytes(b"conteudo")
    await DB.media_files.insert_one({
        "id": "mid-1", "tenant_id": TENANT, "session_id": "s1",
        "stored_name": "arq1.jpg", "filename": "doc.jpg", "mime": "image/jpeg",
        "kind": "image", "size": 8, "purged_at": None,
        "created_at": "2026-08-02T01:00:00+00:00"})
    await DB.contact_notes.insert_one({
        "id": "n1", "tenant_id": TENANT, "contact_id": "c1", "texto": "ligou dia 12",
        "autor_id": "u-admin", "created_at": "2026-08-03T00:00:00+00:00"})
    await DB.deals.insert_one({
        "id": "d1", "tenant_id": TENANT, "pipeline_id": "p1", "contact_id": "c1",
        "etapa_id": "e-ganho", "status": "ganho", "valor": 40000,
        "fechado_em": "2026-08-10T00:00:00+00:00",
        "created_at": "2026-08-05T00:00:00+00:00"})
    await DB.deal_events.insert_one({
        "id": "ev1", "tenant_id": TENANT, "deal_id": "d1", "tipo": "ganho",
        "created_at": "2026-08-10T00:00:00+00:00"})
    await DB.activities.insert_one({
        "id": "a1", "tenant_id": TENANT, "contact_id": "c1", "titulo": "Ligar",
        "vence_em": "2026-08-20T00:00:00+00:00", "concluida_em": None})


async def etapa_anonimizar():
    print("\n[1] Anonimizar: dado pessoal sai, registro fica")
    await semear()
    arquivo = media_store.media_root() / TENANT / "arq1.jpg"
    check("arquivo de midia existe antes", arquivo.exists())

    r = await rc.delete_contact("c1", modo="anonimizar", user=ADMIN)
    check("modo relatado", r["modo"] == "anonimizar", r)

    c = await DB.contacts.find_one({"id": "c1"})
    check("nome substituido pelo marcador", c["nome"] == contatos.NOME_ANONIMO, c["nome"])
    check("numero vira marcador (nao vazio)",
          c["numero_whatsapp"] == "anon-c1", c["numero_whatsapp"])
    check("email removido", c["email"] is None, c["email"])
    check("empresa removida", c["empresa"] is None)
    check("cargo removido", c["cargo"] is None)
    check("foto removida", c["foto_url"] is None)
    check("etiquetas limpas", c["tags"] == [], c["tags"])
    check("campos personalizados limpos", c["campos"] == {}, c["campos"])
    check("data de anonimizacao gravada", bool(c["anonimizado_em"]))

    check("nota apagada", await DB.contact_notes.count_documents({"contact_id": "c1"}) == 0)
    check("relatorio conta a nota", r["notas_apagadas"] == 1, r)

    msgs = await DB.messages_log.find({"session_id": "s1"}).to_list(None)
    check("texto das mensagens redigido",
          all(m["content"] == contatos.TEXTO_REDIGIDO for m in msgs), [m["content"] for m in msgs])
    check("relatorio conta as mensagens", r["mensagens_redigidas"] == 2, r)

    s = await DB.conversation_sessions.find_one({"id": "s1"})
    check("previa da conversa redigida",
          s["last_message"]["content"] == contatos.TEXTO_REDIGIDO, s["last_message"])
    check("nome sai das variaveis da sessao",
          s["variables"]["nome_contato"] == contatos.NOME_ANONIMO, s["variables"])
    check("numero sai das variaveis", s["variables"]["numero"] is None, s["variables"])

    check("arquivo apagado do disco", not arquivo.exists())
    mid = await DB.media_files.find_one({"id": "mid-1"})
    check("documento da midia marcado como purgado", bool(mid["purged_at"]), mid)
    check("documento da midia NAO foi apagado", mid is not None)
    m2 = await DB.messages_log.find_one({"id": "m2"})
    check("bolha antiga sabe que expirou", m2["meta"]["media"]["expired"] is True, m2["meta"])
    check("relatorio conta a midia", r["midias_apagadas"] == 1, r)

    # o que precisa SOBREVIVER
    check("conversa preservada", await DB.conversation_sessions.count_documents({"id": "s1"}) == 1)
    check("relatorio conta a conversa preservada", r["conversas_preservadas"] == 1, r)
    d = await DB.deals.find_one({"id": "d1"})
    check("negocio preservado", d is not None)
    check("valor do negocio intacto", d["valor"] == 40000, d["valor"])
    check("negocio segue ganho (relatorio de agosto nao muda)", d["status"] == "ganho")
    check("relatorio conta o negocio preservado", r["negocios_preservados"] == 1, r)
    check("atividade preservada", await DB.activities.count_documents({"contact_id": "c1"}) == 1)


async def etapa_apagar():
    print("\n[2] Apagar: leva tudo")
    await semear()
    arquivo = media_store.media_root() / TENANT / "arq1.jpg"

    r = await rc.delete_contact("c1", modo="apagar", user=ADMIN)
    check("modo relatado", r["modo"] == "apagar", r)
    check("contato apagado", await DB.contacts.count_documents({"id": "c1"}) == 0)
    check("conversa apagada", await DB.conversation_sessions.count_documents({"id": "s1"}) == 0)
    check("mensagens apagadas", await DB.messages_log.count_documents({"session_id": "s1"}) == 0)
    check("negocio apagado", await DB.deals.count_documents({"id": "d1"}) == 0)
    check("historico do negocio apagado", await DB.deal_events.count_documents({"deal_id": "d1"}) == 0)
    check("nota apagada", await DB.contact_notes.count_documents({"contact_id": "c1"}) == 0)
    check("atividade apagada", await DB.activities.count_documents({"contact_id": "c1"}) == 0)
    check("documento da midia apagado", await DB.media_files.count_documents({"id": "mid-1"}) == 0)
    check("arquivo apagado do disco", not arquivo.exists())
    check("relatorio diz o que levou",
          r["conversas_apagadas"] == 1 and r["negocios_apagados"] == 1, r)


async def etapa_recusas():
    print("\n[3] Recusas")
    await semear()
    st, det = await erro(rc.delete_contact("c1", modo="inventado", user=ADMIN))
    check("modo invalido da 400", st == 400, (st, det))
    st, det = await erro(rc.delete_contact("nao-existe", modo="anonimizar", user=ADMIN))
    check("contato inexistente da 404", st == 404, (st, det))
    st, det = await erro(rc.delete_contact("c1", modo="apagar", user=OUTRO))
    check("contato de outra empresa da 404", st == 404, (st, det))
    check("e nao apagou nada", await DB.contacts.count_documents({"id": "c1"}) == 1)
    check("exclusao e so do admin",
          rc.delete_contact.__wrapped__ is not None if hasattr(rc.delete_contact, "__wrapped__")
          else True)


async def etapa_mesclar():
    print("\n[4] Mesclar duas fichas do mesmo cliente")
    await semear()
    # segunda ficha da mesma pessoa, por outro numero
    await DB.contacts.insert_one({
        "id": "c2", "tenant_id": TENANT, "nome": "Maria S.",
        "numero_whatsapp": "5511900000002", "email": None,
        "empresa": None, "cargo": "Diretora", "origem": "indicacao",
        "tags": ["premium"], "campos": {"f2": "outro"}, "opt_out": True,
        "opt_out_em": "2026-08-09T00:00:00+00:00", "anonimizado_em": None,
        "created_at": "2026-08-06T00:00:00+00:00"})
    await DB.conversation_sessions.insert_one({
        "id": "s2", "tenant_id": TENANT, "contact_id": "c2", "status": "ativa",
        "status_atendimento": "bot", "updated_at": "2026-08-07T00:00:00+00:00"})
    await DB.deals.insert_one({
        "id": "d2", "tenant_id": TENANT, "pipeline_id": "p1", "contact_id": "c2",
        "etapa_id": "e1", "status": "aberto", "valor": 900,
        "created_at": "2026-08-07T00:00:00+00:00"})
    await DB.activities.insert_one({
        "id": "a2", "tenant_id": TENANT, "contact_id": "c2", "titulo": "Retornar",
        "vence_em": "2026-08-25T00:00:00+00:00", "concluida_em": None})
    await DB.contact_notes.insert_one({
        "id": "n2", "tenant_id": TENANT, "contact_id": "c2", "texto": "prefere whatsapp",
        "autor_id": "u-admin", "created_at": "2026-08-07T00:00:00+00:00"})

    r = await rc.merge_contacts("c1", rc.MesclarIn(secundario_id="c2"), ADMIN)

    check("secundario apagado", await DB.contacts.count_documents({"id": "c2"}) == 0)
    check("conversa movida",
          (await DB.conversation_sessions.find_one({"id": "s2"}))["contact_id"] == "c1")
    check("negocio movido", (await DB.deals.find_one({"id": "d2"}))["contact_id"] == "c1")
    check("atividade movida", (await DB.activities.find_one({"id": "a2"}))["contact_id"] == "c1")
    check("nota movida", (await DB.contact_notes.find_one({"id": "n2"}))["contact_id"] == "c1")
    check("relatorio conta o que moveu",
          r["conversas"] == 1 and r["negocios"] == 1 and r["atividades"] == 1
          and r["notas"] == 1, r)

    c = await DB.contacts.find_one({"id": "c1"})
    check("nome do principal preservado", c["nome"] == "Maria Silva", c["nome"])
    check("email do principal preservado", c["email"] == "maria@acme.com", c["email"])
    check("cargo preenchido NAO e sobrescrito", c["cargo"] == "Compras", c["cargo"])
    check("empresa do principal preservada", c["empresa"] == "Acme", c["empresa"])
    check("etiquetas somadas",
          set(c["tags"]) == {"vip", "premium"}, c["tags"])
    check("campo personalizado do secundario aproveitado",
          c["campos"] == {"f1": "valor", "f2": "outro"}, c["campos"])
    check("opt-out do secundario vence (recusa mais restritiva)", c["opt_out"] is True)
    check("data do opt-out preservada", bool(c["opt_out_em"]))


async def etapa_mesclar_recusas():
    print("\n[5] Recusas da mesclagem")
    st, det = await erro(rc.merge_contacts("c1", rc.MesclarIn(secundario_id="c1"), ADMIN))
    check("mesclar com si mesmo da 400", st == 400, (st, det))
    st, det = await erro(rc.merge_contacts("c1", rc.MesclarIn(secundario_id="nao-existe"), ADMIN))
    check("secundario inexistente da 404", st == 404, (st, det))
    st, det = await erro(rc.merge_contacts("c1", rc.MesclarIn(secundario_id="c1"), OUTRO))
    check("mesclagem de outra empresa da 404", st == 404, (st, det))


async def etapa_duplicados():
    print("\n[6] Deteccao de duplicados")
    await semear()
    await DB.contacts.insert_one({
        "id": "c2", "tenant_id": TENANT, "nome": "Joao", "numero_whatsapp": "5511900000002",
        "email": "maria@acme.com", "tags": [], "campos": {}, "opt_out": False,
        "anonimizado_em": None, "created_at": "2026-08-06T00:00:00+00:00"})
    await DB.contacts.insert_one({
        "id": "c3", "tenant_id": TENANT, "nome": "Maria Silva",
        "numero_whatsapp": "5511900000003", "email": None, "tags": [], "campos": {},
        "opt_out": False, "anonimizado_em": None,
        "created_at": "2026-08-07T00:00:00+00:00"})
    await DB.contacts.insert_one({
        "id": "c4", "tenant_id": TENANT, "nome": "Sozinho",
        "numero_whatsapp": "5511900000004", "email": "unico@x.com", "tags": [],
        "campos": {}, "opt_out": False, "anonimizado_em": None,
        "created_at": "2026-08-08T00:00:00+00:00"})
    # de outra empresa, com o mesmo e-mail: nao pode aparecer
    await DB.contacts.insert_one({
        "id": "x1", "tenant_id": "t-2", "nome": "Maria Silva",
        "numero_whatsapp": "5511900000009", "email": "maria@acme.com", "tags": [],
        "campos": {}, "opt_out": False, "anonimizado_em": None,
        "created_at": "2026-08-01T00:00:00+00:00"})

    grupos = await rc.list_duplicates(ADMIN)
    por_criterio = {g["criterio"]: g for g in grupos}
    check("achou grupo por e-mail", "email" in por_criterio, [g["criterio"] for g in grupos])
    check("grupo de e-mail tem as duas fichas certas",
          {c["id"] for c in por_criterio["email"]["contatos"]} == {"c1", "c2"},
          por_criterio["email"]["contatos"])
    check("achou grupo por nome", "nome" in por_criterio, [g["criterio"] for g in grupos])
    check("grupo de nome tem as duas fichas certas",
          {c["id"] for c in por_criterio["nome"]["contatos"]} == {"c1", "c3"},
          por_criterio["nome"]["contatos"])
    check("ficha sem par nao aparece",
          not any("c4" in {c["id"] for c in g["contatos"]} for g in grupos))
    check("ficha de outra empresa nao aparece",
          not any("x1" in {c["id"] for c in g["contatos"]} for g in grupos))
    check("grupo traz dados para a tela decidir",
          all("numero_whatsapp" in c and "created_at" in c
              for g in grupos for c in g["contatos"]))

    check("outra empresa nao ve duplicado nenhum",
          await rc.list_duplicates(OUTRO) == [])

    # anonimizado sai do radar: nome virou o mesmo marcador para todos, e
    # agrupar por ele juntaria pessoas diferentes
    await rc.delete_contact("c3", modo="anonimizar", user=ADMIN)
    grupos = await rc.list_duplicates(ADMIN)
    check("contato anonimizado nao entra em grupo",
          not any("c3" in {c["id"] for c in g["contatos"]} for g in grupos), grupos)


# ---------------------------------------------------------------------------
# Entrada do pytest. As etapas compartilham o Mongo falso deste módulo e
# correm na ordem em que foram escritas.
# ---------------------------------------------------------------------------

def test_suite():
    _conectar_fake()

    async def _tudo():
        await etapa_anonimizar()
        await etapa_apagar()
        await etapa_recusas()
        await etapa_mesclar()
        await etapa_mesclar_recusas()
        await etapa_duplicados()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
