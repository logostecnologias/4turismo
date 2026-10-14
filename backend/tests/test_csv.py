"""Teste de importacao e exportacao de contatos em CSV."""
import asyncio
import io
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
for k, v in {"MONGO_URL": "mongodb://x/", "DB_NAME": "x", "JWT_SECRET": "x",
             "META_PLATFORM_TOKEN": "token-de-teste"}.items():
    os.environ.setdefault(k, v)

from fakedb import FakeDB
from fastapi import HTTPException

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import contatos_csv as cc
import routes.contacts as rc

rc.get_db = lambda: DB

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


TENANT = "t-1"
ADMIN = {"id": "u-admin", "tenant_id": TENANT, "name": "Marcos", "email": "marcos@x.com"}
OUTRO = {"id": "u-b", "tenant_id": "t-2", "name": "B", "email": "b@x.com"}
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


class FakeUpload:
    """UploadFile o bastante para o `await file.read(n)` do endpoint."""

    def __init__(self, dados: bytes, filename="contatos.csv"):
        self._buf = io.BytesIO(dados)
        self.filename = filename
        self.content_type = "text/csv"

    async def read(self, n=-1):
        return self._buf.read(n)


async def semear():
    DB._cols.clear()
    await DB.tenants.insert_one({"id": TENANT})
    await DB.users.insert_one(dict(ADMIN))
    await DB.users.insert_one({"id": "u-bia", "tenant_id": TENANT, "name": "Bia",
                               "email": "bia@x.com"})


def csv_bytes(texto, codificacao="utf-8"):
    return texto.encode(codificacao)


def etapa_numeros():
    print("\n[1] Normalizacao de numero")
    casos = [
        ("11999998888", "5511999998888"),
        ("(11) 99999-8888", "5511999998888"),
        ("+55 11 99999-8888", "5511999998888"),
        ("5511999998888", "5511999998888"),
        ("55 11 3333-4444", "551133334444"),
        ("1133334444", "551133334444"),
        ("0 11 99999-8888", "5511999998888"),
    ]
    for bruto, esperado in casos:
        num, err = cc.normaliza_numero(bruto)
        check(f"'{bruto}' -> {esperado}", num == esperado and err is None, (num, err))

    for ruim in ("", "abc", "12345", "999", "1234567890123456789"):
        num, err = cc.normaliza_numero(ruim)
        check(f"'{ruim}' recusado com motivo", num is None and err, (num, err))

    # nunca inventa o nono digito: 8 digitos + DDD e o que veio, nao o que
    # gostariamos que fosse
    num, err = cc.normaliza_numero("1133334444")
    check("nao acrescenta digito em numero de 10", num == "551133334444", num)
    num, err = cc.normaliza_numero("351912345678")
    check("numero internacional passa sem forcar DDI 55", num == "351912345678", num)


def etapa_parsing():
    print("\n[2] Leitura do arquivo")
    check("detecta ponto e virgula", cc.detecta_delimitador("a;b;c\n1;2;3") == ";")
    check("detecta virgula", cc.detecta_delimitador("a,b,c\n1,2,3") == ",")

    check("le utf-8 com BOM",
          cc.decodifica("\ufeffnome\nJoão".encode("utf-8-sig")).startswith("nome"))
    check("le cp1252 sem quebrar", "Joao" in cc.decodifica("nome\nJoao".encode("cp1252")))
    check("acento sobrevive em latin-1",
          "ã" in cc.decodifica("nome\nJoão".encode("latin-1")))

    colunas, linhas, delim = cc.le_arquivo(csv_bytes("Nome;Telefone\nMaria;11999998888\n"))
    check("colunas lidas", colunas == ["Nome", "Telefone"], colunas)
    check("uma linha de dados", len(linhas) == 1, linhas)
    check("delimitador detectado", delim == ";", delim)

    try:
        cc.le_arquivo(b"")
        check("arquivo sem cabecalho recusado", False)
    except ValueError:
        check("arquivo sem cabecalho recusado", True)

    check("tags separadas por virgula", cc.separa_tags("vip, cliente") == ["vip", "cliente"])
    check("tags separadas por barra", cc.separa_tags("vip/lead") == ["vip", "lead"])
    check("tag repetida deduplicada", cc.separa_tags("vip, VIP ") == ["vip"])


def etapa_mapeamento():
    print("\n[3] Mapeamento sugerido")
    defs = [{"id": "f1", "nome": "Ticket médio"}, {"id": "f2", "nome": "Plano"}]
    mapa = cc.sugere_mapeamento(
        ["Nome completo", "Celular", "E-mail", "Empresa", "Etiquetas",
         "Ticket medio", "Coluna estranha"], defs)
    check("nome reconhecido", mapa["Nome completo"] == "nome", mapa)
    check("celular vira numero", mapa["Celular"] == "numero_whatsapp", mapa)
    check("e-mail com acento/hifen reconhecido", mapa["E-mail"] == "email", mapa)
    check("etiquetas vira tags", mapa["Etiquetas"] == "tags", mapa)
    check("campo personalizado reconhecido por nome (sem acento)",
          mapa["Ticket medio"] == "campo:f1", mapa)
    check("coluna desconhecida fica sem alvo", mapa["Coluna estranha"] == "", mapa)

    # duas colunas de telefone: a segunda nao pode sobrescrever a primeira
    mapa2 = cc.sugere_mapeamento(["Telefone", "Celular"], [])
    alvos = [a for a in mapa2.values() if a == "numero_whatsapp"]
    check("so uma coluna vira numero_whatsapp", len(alvos) == 1, mapa2)


async def etapa_previa():
    print("\n[4] Previa (dry run) nao grava nada")
    await semear()
    texto = (
        "Nome;Celular;E-mail;Empresa;Etiquetas;Responsavel\n"
        "Maria Silva;(11) 99999-0001;maria@acme.com;Acme;vip, cliente;bia@x.com\n"
        "Joao Souza;11999990002;;Beta;lead;\n"
        "Sem numero;;x@y.com;;;\n"
        "Numero ruim;123;;;;\n"
        "Repetida;11999990001;;;;\n"
        "Resp errado;11999990003;;;;naoexiste@x.com\n"
    )
    r = await rc.import_contacts(file=FakeUpload(csv_bytes(texto)), dry_run=True,
                                 mapeamento="", duplicados="ignorar",
                                 ddi="55", user=ADMIN)
    check("nada gravado na previa", await DB.contacts.count_documents({}) == 0)
    check("aplicado=False", r["aplicado"] is False)
    check("6 linhas lidas", r["linhas"] == 6, r["linhas"])
    check("2 contatos novos validos", r["novos"] == 2, r["novos"])
    check("4 linhas com erro", r["erros"] == 4, (r["erros"], r["erros_detalhe"]))

    motivos = {e["linha"]: e["erro"] for e in r["erros_detalhe"]}
    check("linha 4 (sem numero) apontada", "vazio" in motivos.get(4, ""), motivos)
    check("linha 5 (numero ruim) apontada", "curto" in motivos.get(5, ""), motivos)
    check("linha 6 (repetida no arquivo) apontada",
          "repetido" in motivos.get(6, ""), motivos)
    check("linha 7 (responsavel inexistente) apontada",
          "responsável" in motivos.get(7, ""), motivos)
    check("numero da linha e o da planilha (cabecalho conta como 1)",
          min(motivos) == 4, motivos)

    amostra = r["amostra"][0]
    check("amostra normaliza o numero", amostra["numero_whatsapp"] == "5511999990001", amostra)
    check("amostra traz as tags", amostra["tags"] == ["vip", "cliente"], amostra)
    check("amostra resolve o responsavel", amostra["responsavel_id"] == "u-bia", amostra)
    check("mapeamento devolvido para a tela", r["mapeamento"]["Celular"] == "numero_whatsapp",
          r["mapeamento"])
    return texto


async def etapa_importacao(texto):
    print("\n[5] Importacao de verdade")
    r = await rc.import_contacts(file=FakeUpload(csv_bytes(texto)), dry_run=False,
                                 mapeamento="", duplicados="ignorar", ddi="55", user=ADMIN)
    check("aplicado=True", r["aplicado"] is True)
    check("2 criados", r["criados"] == 2, r)
    check("base tem 2 contatos", await DB.contacts.count_documents({}) == 2)

    maria = await DB.contacts.find_one({"numero_whatsapp": "5511999990001"})
    check("nome gravado", maria["nome"] == "Maria Silva", maria["nome"])
    check("email gravado", maria["email"] == "maria@acme.com", maria["email"])
    check("empresa gravada", maria["empresa"] == "Acme", maria["empresa"])
    check("tags gravadas", maria["tags"] == ["vip", "cliente"], maria["tags"])
    check("responsavel resolvido", maria["responsavel_id"] == "u-bia", maria["responsavel_id"])
    check("origem marcada como importacao", maria["origem"] == "importação CSV", maria["origem"])
    check("nasce sem opt-out", maria["opt_out"] is False)
    check("tenant correto", maria["tenant_id"] == TENANT)

    # reimportar o mesmo arquivo nao duplica
    r2 = await rc.import_contacts(file=FakeUpload(csv_bytes(texto)), dry_run=False,
                                  mapeamento="", duplicados="ignorar", ddi="55", user=ADMIN)
    check("reimportar nao cria de novo", r2["criados"] == 0, r2)
    check("duplicados contados", r2["duplicados"] == 2, r2)
    check("ignorados relatados", r2["ignorados"] == 2, r2)
    check("base segue com 2", await DB.contacts.count_documents({}) == 2)


async def etapa_atualizacao():
    print("\n[6] Duplicados com 'atualizar'")
    # etiqueta posta na tela, que a planilha nao conhece
    await DB.contacts.update_one({"numero_whatsapp": "5511999990001"},
                                 {"$set": {"tags": ["vip", "posta na mao"], "cargo": "Compras"}})
    texto = ("Nome;Celular;Empresa;Etiquetas\n"
             "Maria Silva Souza;11999990001;Acme Ltda;premium\n")
    r = await rc.import_contacts(file=FakeUpload(csv_bytes(texto)), dry_run=False,
                                 mapeamento="", duplicados="atualizar", ddi="55", user=ADMIN)
    check("1 atualizado", r["atualizados"] == 1, r)
    maria = await DB.contacts.find_one({"numero_whatsapp": "5511999990001"})
    check("nome atualizado", maria["nome"] == "Maria Silva Souza", maria["nome"])
    check("empresa atualizada", maria["empresa"] == "Acme Ltda", maria["empresa"])
    check("etiqueta da planilha somada", "premium" in maria["tags"], maria["tags"])
    check("etiqueta posta na mao preservada", "posta na mao" in maria["tags"], maria["tags"])
    check("campo ausente na planilha nao foi apagado",
          maria["cargo"] == "Compras", maria.get("cargo"))
    check("email anterior preservado", maria["email"] == "maria@acme.com", maria.get("email"))


async def etapa_campos_personalizados():
    print("\n[7] Campos personalizados na importacao")
    await semear()
    await DB.contact_field_defs.insert_one({
        "id": "f-num", "tenant_id": TENANT, "nome": "Ticket medio", "tipo": "numero",
        "opcoes": [], "ordem": 0})
    await DB.contact_field_defs.insert_one({
        "id": "f-sel", "tenant_id": TENANT, "nome": "Plano", "tipo": "selecao",
        "opcoes": ["Bronze", "Ouro"], "ordem": 1})

    texto = ("Nome;Celular;Ticket medio;Plano\n"
             "Boa;11999990010;1.250,50;Ouro\n"
             "Numero ruim no campo;11999990011;abc;Ouro\n"
             "Plano invalido;11999990012;100;Prata\n")
    r = await rc.import_contacts(file=FakeUpload(csv_bytes(texto)), dry_run=False,
                                 mapeamento="", duplicados="ignorar", ddi="55", user=ADMIN)
    check("so a linha boa entrou", r["criados"] == 1, r)
    check("duas linhas com erro de campo", r["erros"] == 2, r["erros_detalhe"])
    c = await DB.contacts.find_one({"numero_whatsapp": "5511999990010"})
    check("numero brasileiro convertido no campo", c["campos"]["f-num"] == 1250.50, c["campos"])
    check("selecao gravada", c["campos"]["f-sel"] == "Ouro", c["campos"])
    motivos = " ".join(e["erro"] for e in r["erros_detalhe"])
    check("erro de campo diz o nome do campo", "Ticket medio" in motivos, motivos)
    check("erro de opcao invalida citado", "Plano" in motivos, motivos)


async def etapa_recusas():
    print("\n[8] Recusas do endpoint")
    st, det = await erro(rc.import_contacts(
        file=FakeUpload(csv_bytes("Nome;Email\nMaria;m@x.com\n")), dry_run=True,
        mapeamento="", duplicados="ignorar", ddi="55", user=ADMIN))
    check("sem coluna de numero da 400", st == 400 and "WhatsApp" in det, (st, det))

    st, det = await erro(rc.import_contacts(
        file=FakeUpload(b""), dry_run=True, mapeamento="", duplicados="ignorar",
        ddi="55", user=ADMIN))
    check("arquivo vazio da 400", st == 400, (st, det))

    st, det = await erro(rc.import_contacts(
        file=FakeUpload(csv_bytes("Nome;Celular\n")), dry_run=True, mapeamento="",
        duplicados="ignorar", ddi="55", user=ADMIN))
    check("arquivo sem linha de dados da 400", st == 400, (st, det))

    st, det = await erro(rc.import_contacts(
        file=FakeUpload(csv_bytes("Nome;Celular\nx;11999990001\n")), dry_run=True,
        mapeamento="{isso nao e json", duplicados="ignorar", ddi="55", user=ADMIN))
    check("mapeamento invalido da 400", st == 400, (st, det))

    st, det = await erro(rc.import_contacts(
        file=FakeUpload(csv_bytes("Nome;Celular\nx;11999990001\n")), dry_run=True,
        mapeamento="", duplicados="inventado", ddi="55", user=ADMIN))
    check("acao de duplicado invalida da 400", st == 400, (st, det))

    grande = b"Nome;Celular\n" + b"x;11999990001\n" * 10
    st, det = await erro(rc.import_contacts(
        file=FakeUpload(b"x" * (cc.MAX_BYTES + 1)), dry_run=True, mapeamento="",
        duplicados="ignorar", ddi="55", user=ADMIN))
    check("arquivo acima do teto da 400", st == 400 and "MB" in det, (st, det))
    check("teto e de 10MB", cc.MAX_BYTES == 10 * 1024 * 1024)
    check("amostra grande nao quebra", len(grande) > 0)

    # mapeamento explicito sobrepondo a sugestao
    r = await rc.import_contacts(
        file=FakeUpload(csv_bytes("A;B\nMaria;11999990099\n")), dry_run=True,
        mapeamento='{"A": "nome", "B": "numero_whatsapp"}', duplicados="ignorar",
        ddi="55", user=ADMIN)
    check("mapeamento explicito respeitado", r["novos"] == 1, r)
    check("colunas sem sinonimo funcionam com mapeamento manual",
          r["amostra"][0]["nome"] == "Maria", r["amostra"])


async def _corpo(resp):
    partes = []
    async for pedaco in resp.body_iterator:
        partes.append(pedaco)
    return "".join(partes)


async def etapa_exportacao():
    print("\n[9] Exportacao")
    resp = await rc.export_contacts(user=ADMIN)
    texto = await _corpo(resp)
    check("comeca com BOM (Excel le UTF-8)", texto.startswith("\ufeff"), repr(texto[:6]))
    linhas = texto.lstrip("\ufeff").strip().split("\r\n")
    cabecalho = linhas[0].split(";")
    check("separador padrao e ;", ";" in linhas[0], linhas[0])
    check("cabecalho tem as colunas padrao", cabecalho[:2] == ["nome", "numero_whatsapp"], cabecalho)
    check("campos personalizados entram como coluna",
          "Ticket medio" in cabecalho and "Plano" in cabecalho, cabecalho)
    check("uma linha por contato", len(linhas) - 1 == await DB.contacts.count_documents({}),
          (len(linhas) - 1))
    check("valor de campo personalizado exportado", "1250.5" in texto or "1250,5" in texto, texto[:400])
    check("nome do arquivo sugerido",
          "contatos.csv" in resp.headers["content-disposition"], resp.headers)

    resp = await rc.export_contacts(sep=",", user=ADMIN)
    texto = await _corpo(resp)
    check("separador virgula respeitado",
          texto.lstrip("\ufeff").split("\r\n")[0].count(",") > 3, texto[:120])

    st, det = await erro(rc.export_contacts(sep="|", user=ADMIN))
    check("separador invalido da 400", st == 400, (st, det))

    # filtro por etiqueta
    await DB.contacts.update_one({"numero_whatsapp": "5511999990010"},
                                 {"$set": {"tags": ["ouro"]}})
    resp = await rc.export_contacts(tag="ouro", user=ADMIN)
    texto = await _corpo(resp)
    check("filtro de etiqueta reduz a exportacao",
          len(texto.lstrip("\ufeff").strip().split("\r\n")) == 2, texto)

    # empresa de fora nao exporta nada
    resp = await rc.export_contacts(user=OUTRO)
    texto = await _corpo(resp)
    check("exportacao de outra empresa vem so com cabecalho",
          len(texto.lstrip("\ufeff").strip().split("\r\n")) == 1, texto)


async def etapa_isolamento():
    print("\n[10] Isolamento na importacao")
    antes = await DB.contacts.count_documents({"tenant_id": TENANT})
    r = await rc.import_contacts(
        file=FakeUpload(csv_bytes("Nome;Celular\nDe outra;11988887777\n")), dry_run=False,
        mapeamento="", duplicados="ignorar", ddi="55", user=OUTRO)
    check("importou na empresa de quem chamou", r["criados"] == 1, r)
    c = await DB.contacts.find_one({"numero_whatsapp": "5511988887777"})
    check("contato ficou no tenant do chamador", c["tenant_id"] == "t-2", c["tenant_id"])
    check("base da outra empresa intacta",
          await DB.contacts.count_documents({"tenant_id": TENANT}) == antes)
    # mesmo numero pode existir em duas empresas diferentes
    r2 = await rc.import_contacts(
        file=FakeUpload(csv_bytes("Nome;Celular\nMesmo numero;11988887777\n")), dry_run=False,
        mapeamento="", duplicados="ignorar", ddi="55", user=ADMIN)
    check("mesmo numero em outra empresa nao e duplicado", r2["criados"] == 1, r2)



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
        etapa_numeros()
        etapa_parsing()
        etapa_mapeamento()
        texto = await etapa_previa()
        await etapa_importacao(texto)
        await etapa_atualizacao()
        await etapa_campos_personalizados()
        await etapa_recusas()
        await etapa_exportacao()
        await etapa_isolamento()
        print(f"\n=== {ok} verificacoes passaram ===")
    asyncio.run(_tudo())
