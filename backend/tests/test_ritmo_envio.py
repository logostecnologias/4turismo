"""Proteção do número no canal não oficial: ritmo dos envios ativos e leitura.

O tempo é simulado: `asyncio.sleep` e `time.monotonic` de `ritmo_envio` são
trocados por um relógio falso que só anda quando alguém "dorme". É o que
deixa conferir intervalos de 8 a 20 segundos sem o teste levar minutos.
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
for k, v in {"MONGO_URL": "mongodb://fake/", "DB_NAME": "fake", "JWT_SECRET": "x"}.items():
    os.environ.setdefault(k, v)

import flow_runtime  # noqa: E402
import ritmo_envio  # noqa: E402
import whatsapp_client  # noqa: E402
import wuzapi_client  # noqa: E402


class _Relogio:
    def __init__(self):
        self.t = 1000.0

    def monotonic(self):
        return self.t

    async def sleep(self, s):
        self.t += max(s, 0)
        await asyncio.sleep(0)


async def _com_relogio(coro_fn):
    rel = _Relogio()
    sleep_orig, mono_orig = ritmo_envio.asyncio.sleep, ritmo_envio.time.monotonic
    ritmo_envio._zerar()
    # Só dentro de ritmo_envio: o resto do asyncio continua com o sleep de verdade.
    ritmo_envio.asyncio = type("A", (), {"sleep": staticmethod(rel.sleep),
                                         "Lock": asyncio.Lock})
    ritmo_envio.time = type("T", (), {"monotonic": staticmethod(rel.monotonic)})
    try:
        return await coro_fn(rel)
    finally:
        ritmo_envio.asyncio = asyncio
        import time as _t
        ritmo_envio.time = _t
        ritmo_envio._zerar()
        assert sleep_orig and mono_orig


def test_intervalo_entre_envios_do_mesmo_numero():
    async def corpo(rel):
        momentos = []

        async def envia():
            assert await ritmo_envio.vez("conn-a", adiavel=False)
            momentos.append(rel.t)

        await asyncio.gather(*(envia() for _ in range(5)))
        gaps = [b - a for a, b in zip(momentos, momentos[1:])]
        assert momentos[0] == 1000.0, "o primeiro sai na hora"
        assert all(ritmo_envio.INTERVALO[0] <= g <= ritmo_envio.INTERVALO[1] for g in gaps), gaps
        assert len(set(round(g, 3) for g in gaps)) > 1, "intervalo fixo é assinatura de robô"
    asyncio.run(_com_relogio(corpo))


def test_numeros_diferentes_nao_esperam_um_pelo_outro():
    async def corpo(rel):
        assert await ritmo_envio.vez("conn-a", adiavel=False)
        antes = rel.t
        assert await ritmo_envio.vez("conn-b", adiavel=False)
        assert rel.t == antes, "outro número sai na hora"
    asyncio.run(_com_relogio(corpo))


def test_teto_por_hora_adia_so_o_que_pode_esperar():
    async def corpo(rel):
        for _ in range(ritmo_envio.TETO_HORA):
            assert await ritmo_envio.vez("conn-a", adiavel=True)
        assert not await ritmo_envio.vez("conn-a", adiavel=True), "acima do teto: adia"
        assert await ritmo_envio.vez("conn-a", adiavel=False), "urgente passa do teto"
        rel.t += 3601
        assert await ritmo_envio.vez("conn-a", adiavel=True), "uma hora depois, volta"
    asyncio.run(_com_relogio(corpo))


def test_marcar_lida_na_wuzapi_usa_o_chat_como_chegou():
    chamadas = []

    class _Resp:
        status_code = 200
        text = "{}"

    class _Cliente:
        async def post(self, url, headers=None, json=None, timeout=None):
            chamadas.append((url, headers, json))
            return _Resp()

    orig = wuzapi_client._get_client
    wuzapi_client._get_client = lambda: _Cliente()
    try:
        campos = {f: None for f in whatsapp_client.Credencial._fields}
        cred = whatsapp_client.Credencial(**{**campos, "canal": whatsapp_client.CANAL_WUZAPI,
                                             "token": "tk"})
        asyncio.run(whatsapp_client.marcar_lida(cred, "MSG1", chat="123456@lid"))
        url, headers, corpo = chamadas[0]
        assert url.endswith("/chat/markread"), url
        assert corpo == {"Id": ["MSG1"], "ChatPhone": "123456@lid"}, corpo
        # Sem o chat, não chuta: não chama nada.
        asyncio.run(whatsapp_client.marcar_lida(cred, "MSG2"))
        assert len(chamadas) == 1
    finally:
        wuzapi_client._get_client = orig


def test_leitura_no_canal_nao_oficial_espera_um_pouco():
    esperas, marcadas = [], []

    async def sleep_falso(s):
        esperas.append(s)

    async def marcar_falso(cred, wamid, digitando=False, chat=None):
        marcadas.append((wamid, chat))

    s_orig, m_orig = flow_runtime.asyncio.sleep, flow_runtime.whatsapp.marcar_lida
    flow_runtime.asyncio.sleep = sleep_falso
    flow_runtime.whatsapp.marcar_lida = marcar_falso
    try:
        wuz = type("C", (), {"canal": whatsapp_client.CANAL_WUZAPI})()
        cloud = type("C", (), {"canal": "cloud_api"})()
        asyncio.run(flow_runtime._marcar_lida_bg(wuz, "M1", "55@s.whatsapp.net"))
        asyncio.run(flow_runtime._marcar_lida_bg(cloud, "M2"))
    finally:
        flow_runtime.asyncio.sleep = s_orig
        flow_runtime.whatsapp.marcar_lida = m_orig
    lo, hi = flow_runtime.LEITURA_ESPERA
    assert len(esperas) == 1 and lo <= esperas[0] <= hi, esperas
    assert marcadas == [("M1", "55@s.whatsapp.net"), ("M2", None)], marcadas
