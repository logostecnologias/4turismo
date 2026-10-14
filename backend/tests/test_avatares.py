"""Foto de perfil do contato, no canal não oficial.

O que precisa ficar provado aqui não é "a foto chega" — é o conjunto de
regras que impede a foto de virar problema:

  - **uma tentativa por contato, não uma por mensagem.** Quem esconde a foto
    devolve vazio para sempre; sem marca isso seria uma ida à ponte por
    mensagem recebida, indefinidamente, para não descobrir nada. E a marca é
    gravada antes da tentativa, senão uma falha no meio devolve o laço.

  - **nunca por cima do que alguém pôs.** Cadastro e importação escrevem no
    mesmo lugar.

  - **só no canal não oficial.** A Cloud API não expõe avatar; pedir ali é
    gastar chamada para receber nada.

  - **nada derruba a mensagem.** Este código roda no caminho de uma mensagem
    recebida. Ponte fora do ar, host recusado pelo `net_guard`, imagem grande:
    tudo termina em log.

  - **a anonimização alcança o arquivo.** É o ponto de LGPD: `_apaga_midia`
    varre por `session_id` e avatar não tem conversa, então sem o caminho
    próprio a foto de uma pessoa continuaria em disco e servível por
    /api/media depois de a ficha ter sido anonimizada.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _chave, _valor in {
    "MONGO_URL": "mongodb://fake/", "DB_NAME": "fake", "JWT_SECRET": "teste",
    "CREDENTIALS_KEY": "VlKvCmSGH9QsR1bwg10fq1e4oiOZ_0MP3B-YxhSL1oo=",
}.items():
    os.environ.setdefault(_chave, _valor)

from datetime import datetime, timedelta, timezone

from fakedb import FakeDB

import db as db_mod
DB = FakeDB()
db_mod.get_db = lambda: DB

import avatares
import contatos
import cripto
import media_retention
import media_store
import net_guard
import wuzapi_client as wuzapi

TENANT = "t-1"
PNG = bytes.fromhex("89504e470d0a1a0a") + b"corpo-da-imagem"

ok = 0


def check(rotulo, condicao, extra=None):
    global ok
    if condicao:
        ok += 1
        print("  ok  " + rotulo)
    else:
        raise AssertionError("{} | {!r}".format(rotulo, extra))


def _conectar_fake(tmp):
    db_mod.get_db = lambda: DB
    os.environ["MEDIA_DIR"] = str(tmp)


def _conexao(canal="wuzapi"):
    bloco = cripto.cifrar("token-da-instancia")
    return {"id": "c-1", "tenant_id": TENANT, "canal": canal,
            "wuzapi_id": "inst-1", "token_cifrado": bloco,
            "phone_number_id": "pn-1"}


async def _contato(**extra):
    await DB.contacts.delete_many({})
    doc = {"id": "ct-1", "tenant_id": TENANT, "numero_whatsapp": "5585999998888",
           "wa_id": "558599998888", "nome": "Markus", **extra}
    await DB.contacts.insert_one(doc)
    return doc


class Rede:
    """Ponte e download dublados, contando quantas vezes foram procurados."""

    def __init__(self, url="https://pps.whatsapp.net/v/foto.jpg", erro=None):
        self.url = url
        self.erro = erro
        self.consultas = 0
        self.downloads = 0
        self.numeros = []

    async def get_avatar(self, token, phone):
        self.consultas += 1
        self.numeros.append(phone)
        if self.erro == "ponte":
            raise wuzapi.WuzapiError("ponte fora do ar")
        return self.url

    async def fetch_bytes(self, url, *, max_bytes, timeout=30.0):
        self.downloads += 1
        if self.erro == "download":
            raise net_guard.SSRFError("host recusado")
        return "image/jpeg", PNG


def instalar(rede):
    avatares.wuzapi.get_avatar = rede.get_avatar
    avatares.net_guard.fetch_bytes = rede.fetch_bytes
    return rede


# ---------------------------------------------------------------------------


async def etapa_busca_e_guarda(tmp):
    print("\n[a foto vira arquivo nosso]")
    _conectar_fake(tmp)
    rede = instalar(Rede())
    contact = await _contato()

    mid = await avatares.atualizar(DB, TENANT, _conexao(), contact)
    check("devolveu o id do arquivo", bool(mid), mid)

    doc = await DB.media_files.find_one({"id": mid})
    check("o binário foi guardado como mídia nossa", doc is not None)
    check("marcado como avatar, que é o que a retenção e a LGPD consultam",
          doc.get("uso") == "avatar", doc.get("uso"))
    check("e amarrado ao contato — avatar não pertence a conversa nenhuma",
          doc.get("contact_id") == "ct-1", doc.get("contact_id"))
    check("o arquivo existe em disco", media_store.file_path(doc).exists())
    check("com o conteúdo que veio", media_store.file_path(doc).read_bytes() == PNG)

    ct = await DB.contacts.find_one({"id": "ct-1"})
    check("a ficha aponta para o arquivo", ct.get("foto_media_id") == mid, ct.get("foto_media_id"))
    check("e `foto_url` não foi inventada: ela é para endereço externo",
          not ct.get("foto_url"), ct.get("foto_url"))

    # O wa_id observado, nunca o número remontado. Perguntar pela forma errada
    # é perguntar pela foto de outra pessoa.
    check("perguntou pelo wa_id observado", rede.numeros == ["558599998888"], rede.numeros)


async def etapa_uma_tentativa_por_contato(tmp):
    print("\n[sem foto: não pode virar uma consulta por mensagem]")
    _conectar_fake(tmp)
    rede = instalar(Rede(url=None))
    contact = await _contato()

    mid = await avatares.atualizar(DB, TENANT, _conexao(), contact)
    check("contato sem foto não gera arquivo", mid is None)
    check("mas a tentativa foi registrada", rede.consultas == 1, rede.consultas)

    ct = await DB.contacts.find_one({"id": "ct-1"})
    check("a marca ficou na ficha", bool(ct.get("foto_tentada_em")), ct)

    for _ in range(5):
        await avatares.atualizar(DB, TENANT, _conexao(), await DB.contacts.find_one({"id": "ct-1"}))
    check("e as mensagens seguintes não voltam à ponte",
          rede.consultas == 1, rede.consultas)

    # Passados os dias, tenta de novo: quem pôs uma foto essa semana merece
    # aparecer.
    antigo = (datetime.now(timezone.utc)
              - timedelta(days=avatares.RETENTAR_DIAS + 1)).isoformat()
    await DB.contacts.update_one({"id": "ct-1"}, {"$set": {"foto_tentada_em": antigo}})
    await avatares.atualizar(DB, TENANT, _conexao(), await DB.contacts.find_one({"id": "ct-1"}))
    check("depois da janela, tenta uma vez mais", rede.consultas == 2, rede.consultas)


async def etapa_nao_sobrescreve(tmp):
    print("\n[foto posta por gente não é substituída]")
    _conectar_fake(tmp)
    rede = instalar(Rede())

    contact = await _contato(foto_url="https://cdn.empresa.com/markus.jpg")
    await avatares.atualizar(DB, TENANT, _conexao(), contact)
    check("com foto externa, nem consulta a ponte", rede.consultas == 0, rede.consultas)

    contact = await _contato(foto_media_id="ja-tenho")
    await avatares.atualizar(DB, TENANT, _conexao(), contact)
    check("com foto nossa, também não", rede.consultas == 0, rede.consultas)


async def etapa_so_no_canal_nao_oficial(tmp):
    print("\n[Cloud API não expõe avatar: nem tenta]")
    _conectar_fake(tmp)
    rede = instalar(Rede())
    contact = await _contato()
    await avatares.atualizar(DB, TENANT, _conexao(canal="cloud_api"), contact)
    check("nenhuma consulta no canal oficial", rede.consultas == 0, rede.consultas)
    ct = await DB.contacts.find_one({"id": "ct-1"})
    check("e nem marca de tentativa, que seria mentira",
          not ct.get("foto_tentada_em"), ct.get("foto_tentada_em"))


async def etapa_falha_nunca_derruba(tmp):
    print("\n[falha é log, nunca exceção: isto roda no caminho da mensagem]")
    _conectar_fake(tmp)

    for modo, rotulo in (("ponte", "ponte fora do ar"),
                         ("download", "host recusado pelo net_guard")):
        instalar(Rede(erro=modo))
        contact = await _contato()
        mid = await avatares.atualizar(DB, TENANT, _conexao(), contact)
        check(f"{rotulo}: devolve None sem levantar", mid is None)
        ct = await DB.contacts.find_one({"id": "ct-1"})
        # A marca gravada ANTES da tentativa é o que impede a falha de virar
        # laço: sem ela, a próxima mensagem tentaria de novo, e a seguinte.
        check(f"{rotulo}: a marca foi gravada antes de sair para a rede",
              bool(ct.get("foto_tentada_em")), ct)


async def etapa_retencao_nao_expira_avatar(tmp):
    print("\n[retenção: avatar é atributo corrente, não acervo]")
    _conectar_fake(tmp)
    instalar(Rede())
    contact = await _contato()
    mid = await avatares.atualizar(DB, TENANT, _conexao(), contact)

    # Envelhece o arquivo muito além de qualquer janela.
    velho = (datetime.now(timezone.utc) - timedelta(days=999)).isoformat()
    await DB.media_files.update_one({"id": mid}, {"$set": {"created_at": velho}})
    await DB.tenants.delete_many({})
    await DB.tenants.insert_one({"id": TENANT, "plan_id": None})

    apagados = await media_retention._purge_expired(DB)
    doc = await DB.media_files.find_one({"id": mid})
    check("a varredura não apaga o avatar", doc.get("purged_at") is None, doc)
    check("o arquivo continua em disco", media_store.file_path(doc).exists())
    check("nada foi expirado nesta passagem", apagados == 0, apagados)


async def etapa_anonimizar_apaga_a_foto(tmp):
    print("\n[LGPD: a foto sai na anonimização]")
    _conectar_fake(tmp)
    contatos.get_db = lambda: DB
    instalar(Rede())
    contact = await _contato()
    mid = await avatares.atualizar(DB, TENANT, _conexao(), contact)
    caminho = media_store.file_path(await DB.media_files.find_one({"id": mid}))
    check("a foto existe antes", caminho.exists())

    await contatos.anonimizar(DB, TENANT, await DB.contacts.find_one({"id": "ct-1"}))

    check("o arquivo saiu do disco", not caminho.exists())
    doc = await DB.media_files.find_one({"id": mid})
    check("o documento fica com purged_at, como o resto da mídia",
          doc.get("purged_at"), doc)
    ct = await DB.contacts.find_one({"id": "ct-1"})
    check("e a ficha não aponta mais para ela",
          not ct.get("foto_media_id"), ct.get("foto_media_id"))


async def etapa_apagar_leva_o_documento(tmp):
    print("\n[modo apagar: nem o registro do arquivo fica]")
    _conectar_fake(tmp)
    instalar(Rede())
    contact = await _contato()
    mid = await avatares.atualizar(DB, TENANT, _conexao(), contact)

    await contatos.apagar(DB, TENANT, await DB.contacts.find_one({"id": "ct-1"}))
    check("o documento do avatar some",
          await DB.media_files.find_one({"id": mid}) is None)


async def etapa_extracao_do_endereco(tmp):
    print("\n[a resposta da ponte: onde mora o endereco da foto]")
    _conectar_fake(tmp)

    # Esta etapa dubla `_request`, não `get_avatar`. A diferença é o defeito
    # que ela existe para pegar: a primeira versão do teste dublava
    # `get_avatar` e por isso NUNCA exercitou a extração — o código lia `URL`
    # em caixa alta, a instância real devolve `url`, e o resultado era
    # "contato sem foto" para todo mundo, indistinguível do caso legítimo de
    # quem esconde a foto. Payload abaixo é o observado em 11/09/2026.
    real = {"data": {
        "id": 2101513792, "type": "image", "hash": "zdpF...",
        "direct_path": "/v/t61.24694-24/680434955_n.jpg?ccb=11-4",
        "url": "https://pps.whatsapp.net/v/t61.24694-24/680434955_n.jpg?ccb=11-4",
    }}

    async def _resp(method, path, headers, json=None, body=None):
        return _resp.devolve

    wuzapi._request = _resp

    _resp.devolve = real
    url = await wuzapi.get_avatar("tok", "5585999998888")
    check("acha o endereco na chave real (`url`, minuscula)",
          url == real["data"]["url"], url)

    _resp.devolve = {"data": {"URL": "https://x/y.jpg"}}
    check("e continua achando em caixa alta, que e a grafia antiga",
          await wuzapi.get_avatar("tok", "x") == "https://x/y.jpg")

    # Sem URL pronta, remonta do caminho: o host e fixo e o caminho ja vem
    # assinado. Melhor que devolver vazio numa versao que pare de mandar a URL.
    _resp.devolve = {"data": {"direct_path": "/v/abc.jpg?ccb=1"}}
    check("sem URL pronta, remonta a partir de direct_path",
          await wuzapi.get_avatar("tok", "x") == wuzapi.HOST_AVATAR + "/v/abc.jpg?ccb=1",
          await wuzapi.get_avatar("tok", "x"))

    _resp.devolve = {"data": {"type": "image"}}
    check("resposta sem endereco nenhum vira None",
          await wuzapi.get_avatar("tok", "x") is None)

    async def _erro(*a, **k):
        raise wuzapi.WuzapiError("500: foto escondida")
    wuzapi._request = _erro
    check("foto escondida (500 da ponte) e None, nao excecao",
          await wuzapi.get_avatar("tok", "x") is None)


async def _tudo():
    import tempfile
    with tempfile.TemporaryDirectory() as tmp:
        await etapa_extracao_do_endereco(tmp)
        await etapa_busca_e_guarda(tmp)
        await etapa_uma_tentativa_por_contato(tmp)
        await etapa_nao_sobrescreve(tmp)
        await etapa_so_no_canal_nao_oficial(tmp)
        await etapa_falha_nunca_derruba(tmp)
        await etapa_retencao_nao_expira_avatar(tmp)
        await etapa_anonimizar_apaga_a_foto(tmp)
        await etapa_apagar_leva_o_documento(tmp)
    print("\n{} verificações passaram".format(ok))


def test_suite():
    asyncio.run(_tudo())


if __name__ == "__main__":
    test_suite()
