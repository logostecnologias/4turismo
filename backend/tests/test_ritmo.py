"""Ritmo humano nas respostas automáticas do canal não oficial.

O que precisa ficar provado:

  - **só resposta automática no canal `wuzapi`** ganha "digitando…" e pausa.
    Na Cloud API não há risco de bloqueio por ritmo, e mensagem sem `inicio`
    (atendente, aviso) não é automática.
  - **a pausa é do tamanho do texto**, com teto, e a primeira desconta o
    tempo que a resposta já levou para ficar pronta.
  - **falha do indicador nunca bloqueia o envio.**
  - **o nó Aguardar espera de verdade** no envio real, com teto de 60s.
"""
import asyncio
import os
import sys
import time

sys.path.insert(0, os.path.dirname(__file__))
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

for _chave, _valor in {
    "MONGO_URL": "mongodb://fake/", "DB_NAME": "fake", "JWT_SECRET": "teste",
    "CREDENTIALS_KEY": "VlKvCmSGH9QsR1bwg10fq1e4oiOZ_0MP3B-YxhSL1oo=",
}.items():
    os.environ.setdefault(_chave, _valor)

import flow_runtime
import whatsapp_client as whatsapp
import wuzapi_client

ok = 0


def check(nome, cond):
    global ok
    assert cond, nome
    ok += 1
    print("ok -", nome)


class Gravador:
    """Dubla rede, relógio e histórico, guardando a ordem dos eventos."""

    def __init__(self):
        self.eventos = []

    async def sleep(self, s):
        self.eventos.append(("espera", round(s, 3)))

    async def digitando(self, cred, numero, audio=False):
        self.eventos.append(("digitando", audio))

    async def send_text(self, cred, numero, texto):
        self.eventos.append(("texto", texto))
        return "id"

    async def log(self, *a, **k):
        pass


def _instala(g):
    flow_runtime.asyncio.sleep = g.sleep
    whatsapp.digitando = g.digitando
    whatsapp.send_text = g.send_text
    flow_runtime._log_msg = g.log
    flow_runtime.random.uniform = lambda a, b: 1.0


ORIGINAIS = (asyncio.sleep, whatsapp.digitando, whatsapp.send_text,
             flow_runtime._log_msg, flow_runtime.random.uniform)


def _restaura():
    (flow_runtime.asyncio.sleep, whatsapp.digitando, whatsapp.send_text,
     flow_runtime._log_msg, flow_runtime.random.uniform) = ORIGINAIS


WUZ = whatsapp.Credencial("inst", "tok", canal=whatsapp.CANAL_WUZAPI)
CLOUD = whatsapp.Credencial("pnid", "tok")


async def etapa_so_automatica_no_wuzapi():
    g = Gravador(); _instala(g)
    saidas = [{"type": "text", "content": "oi"}]
    await flow_runtime._send_outputs(None, "t", CLOUD, "55", "s", saidas,
                                     inicio=time.monotonic())
    check("Cloud API sai sem digitando nem pausa", g.eventos == [("texto", "oi")])
    g.eventos.clear()
    await flow_runtime._send_outputs(None, "t", WUZ, "55", "s", saidas)
    check("sem inicio (não automática) sai sem pausa", g.eventos == [("texto", "oi")])


async def etapa_pausa_proporcional():
    g = Gravador(); _instala(g)
    longo = "x" * 1000
    saidas = [{"type": "text", "content": "a" * 50}, {"type": "text", "content": longo}]
    # `inicio` no futuro distante seria trapaça; no presente o desconto é ~0.
    await flow_runtime._send_outputs(None, "t", WUZ, "55", "s", saidas,
                                     inicio=time.monotonic())
    tipos = [e[0] for e in g.eventos]
    check("digitando antes de cada texto, e a pausa entre os dois",
          tipos == ["digitando", "espera", "texto", "digitando", "espera", "texto"])
    check("50 caracteres = 1s + 1,5s", abs(g.eventos[1][1] - 2.5) < 0.05)
    check("texto longo para no teto de 5s", g.eventos[4][1] == flow_runtime.RITMO_MAX)


async def etapa_desconta_tempo_gasto():
    g = Gravador(); _instala(g)
    saidas = [{"type": "text", "content": "oi"}, {"type": "text", "content": "tudo bem?"}]
    await flow_runtime._send_outputs(None, "t", WUZ, "55", "s", saidas,
                                     inicio=time.monotonic() - 10)
    check("resposta que levou 10s sai sem pausa na primeira",
          g.eventos[:2] == [("digitando", False), ("texto", "oi")])
    check("a segunda ainda ganha pausa", g.eventos[3][0] == "espera")


async def etapa_aguardar_espera_de_verdade():
    g = Gravador(); _instala(g)
    saidas = [{"type": "delay", "seconds": 600}, {"type": "text", "content": "oi"}]
    await flow_runtime._send_outputs(None, "t", CLOUD, "55", "s", saidas)
    check("Aguardar espera no envio real, com teto de 60s",
          g.eventos == [("espera", 60), ("texto", "oi")])


async def etapa_indicador_nunca_bloqueia():
    _restaura()

    class Quebrado:
        async def post(self, *a, **k):
            raise RuntimeError("ponte fora do ar")

    original = wuzapi_client._get_client
    wuzapi_client._get_client = lambda: Quebrado()
    try:
        await wuzapi_client.presence("tok", "55")
        check("falha do indicador é engolida", True)
    finally:
        wuzapi_client._get_client = original


async def _tudo():
    try:
        await etapa_so_automatica_no_wuzapi()
        await etapa_pausa_proporcional()
        await etapa_desconta_tempo_gasto()
        await etapa_aguardar_espera_de_verdade()
        await etapa_indicador_nunca_bloqueia()
    finally:
        _restaura()
    print("\n{} verificações passaram".format(ok))


def test_suite():
    asyncio.run(_tudo())


if __name__ == "__main__":
    test_suite()
