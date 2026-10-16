"""O health de verdade e o Sentry opcional (`observabilidade.py`)."""

import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import agenda_worker  # noqa: E402
import health_monitor  # noqa: E402
import media_retention  # noqa: E402
import observabilidade  # noqa: E402


class _Banco:
    def __init__(self, falha=None, demora=0):
        self.falha, self.demora = falha, demora

    async def command(self, nome):
        assert nome == "ping"
        if self.demora:
            await asyncio.sleep(self.demora)
        if self.falha:
            raise self.falha
        return {"ok": 1}


async def _suite():
    vivo = asyncio.create_task(asyncio.sleep(60))
    antigos = {m: getattr(m, "_task", None)
               for m in (agenda_worker, health_monitor, media_retention)}
    try:
        for m in antigos:
            m._task = vivo
        ok, corpo = await observabilidade.estado(_Banco())
        assert ok and corpo["status"] == "ok" and corpo["banco"] == "ok", corpo

        ok, corpo = await observabilidade.estado(_Banco(falha=ConnectionError("x")))
        assert not ok and corpo["banco"] == "falhou", corpo

        observabilidade.PING_TIMEOUT, antes = 0.05, observabilidade.PING_TIMEOUT
        try:
            ok, corpo = await observabilidade.estado(_Banco(demora=1))
            assert not ok and corpo["banco"] == "falhou", "banco lento conta como fora"
        finally:
            observabilidade.PING_TIMEOUT = antes

        # Worker que morreu (ou nunca subiu) derruba o health.
        agenda_worker._task = None
        ok, corpo = await observabilidade.estado(_Banco())
        assert not ok and corpo["workers"] == {"parados": ["agenda"]}, corpo
    finally:
        vivo.cancel()
        for m, t in antigos.items():
            m._task = t


def test_suite():
    asyncio.run(_suite())


def test_sentry_sem_dsn_nao_liga(monkeypatch):
    monkeypatch.delenv("SENTRY_DSN", raising=False)
    assert observabilidade.iniciar_sentry() is False
