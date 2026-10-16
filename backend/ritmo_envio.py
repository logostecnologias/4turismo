"""Ritmo dos envios ativos no canal não oficial (WuzAPI).

O ritmo humano das **respostas** (`flow_runtime._ritmo`) cobre o robô
conversando. Faltava o outro lado, que é o que mais derruba número não oficial:
a plataforma **escrevendo primeiro** em sequência. A varredura de lembretes
mandava todos os da hora no mesmo segundo, e cancelar uma saída de 40 grupos
disparava 40 mensagens em menos de um minuto. Para o WhatsApp isso é disparo
em massa, e é por disparo em massa que número não oficial é banido.

Duas regras, por número (conexão), nunca entre números diferentes:

- **Intervalo**: entre um envio ativo e o seguinte do mesmo número, uma espera
  aleatória de `INTERVALO` segundos. Aleatória, porque intervalo fixo também é
  assinatura de robô. Números diferentes andam em paralelo.
- **Teto por hora** (`TETO_HORA`), só para o que pode esperar (lembrete, aviso
  de solicitação vencida): acima dele, o envio é **adiado**, não perdido, e a
  varredura seguinte tenta de novo. O cancelamento de saída por chuva não tem
  teto: é urgente, e o turista precisa saber antes de ir para o ponto.

Envio de um clique (confirmar uma reserva, lembrar um grupo pela tela) não
passa por aqui: é uma pessoa agindo, a mesma regra da mensagem de atendente.

O estado mora na memória do processo, que é um só (o backend roda com um
worker, ver backend/Dockerfile). Reiniciar zera o teto da hora, o que no pior
caso deixa passar uma hora com o dobro do teto, uma vez.
"""
import asyncio
import random
import time
from collections import deque

INTERVALO = (8.0, 20.0)
TETO_HORA = 40
_JANELA = 3600.0

_estado: dict[str, dict] = {}


def _de(conn_id: str) -> dict:
    est = _estado.get(conn_id)
    if est is None:
        est = _estado[conn_id] = {"trava": asyncio.Lock(), "ultimo": None, "envios": deque()}
    return est


def _limpa(est: dict, agora: float) -> None:
    envios = est["envios"]
    while envios and agora - envios[0] > _JANELA:
        envios.popleft()


async def vez(conn_id: str, *, adiavel: bool) -> bool:
    """Espera a vez deste número enviar. Devolve False se o envio deve ser adiado.

    A trava fica presa durante a espera, de propósito: é ela que enfileira os
    envios do mesmo número, enquanto os de outros números seguem. O teto é
    conferido já com a trava, senão cem avisos chegando juntos passariam todos
    pela conferência antes de o primeiro ser contado.
    """
    est = _de(conn_id)
    async with est["trava"]:
        agora = time.monotonic()
        _limpa(est, agora)
        if adiavel and len(est["envios"]) >= TETO_HORA:
            return False
        if est["ultimo"] is not None:
            espera = est["ultimo"] + random.uniform(*INTERVALO) - agora
            if espera > 0:
                await asyncio.sleep(espera)
        est["ultimo"] = time.monotonic()
        est["envios"].append(est["ultimo"])
        return True


def _zerar() -> None:
    """Para os testes."""
    _estado.clear()
