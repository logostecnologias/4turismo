"""Freio de força bruta no login (ZZ-12).

Não havia limite nenhum: `POST /api/auth/login` aceitava tentativas infinitas,
e o bcrypt era o único atrito. Uma lista de senhas comuns contra os e-mails de
admin era um ataque de horas.

Janela deslizante em memória, com dois freios:

  - por **(IP, e-mail)**: trava a conta-alvo depois de poucas tentativas
    erradas — é o que barra o ataque focado num admin;
  - por **IP**: teto mais alto, para conter o spray de um mesmo IP contra
    muitos e-mails (cada e-mail ainda não estourou o freio de conta, mas o
    conjunto sim).

Acerto zera os contadores daquela dupla — quem lembrou a senha não fica preso.

**Escopo: um processo.** Com vários workers de uvicorn ou uma réplica, cada um
tem o seu contador, e o teto efetivo multiplica. Fechar isso exige um contador
compartilhado (Redis, ou uma coleção no Mongo com TTL) — é o caminho quando o
backend rodar replicado. Para o alvo aqui (uma origem martelando o login) o
freio por processo já muda o jogo: de ilimitado para poucas dezenas por janela.
"""
import math
import time
from collections import defaultdict

from fastapi import HTTPException

JANELA = 300          # segundos observados
MAX_POR_CONTA = 5     # erros por (IP, e-mail) na janela antes de travar
MAX_POR_IP = 20       # erros por IP na janela (spray contra vários e-mails)

# chave -> lista de instantes (monotônicos) das falhas recentes
_falhas: dict[str, list[float]] = defaultdict(list)


def _podar(chave: str, agora: float) -> list[float]:
    recentes = [t for t in _falhas.get(chave, ()) if agora - t < JANELA]
    if recentes:
        _falhas[chave] = recentes
    else:
        _falhas.pop(chave, None)
    return recentes


def _retry_after(recentes: list[float], teto: int, agora: float) -> int:
    # A trava cede quando a falha nº (len-teto+1) mais antiga sai da janela.
    idx = len(recentes) - teto
    liberacao = recentes[idx] + JANELA
    return max(1, math.ceil(liberacao - agora))


def checar(ip: str, email: str):
    """Levanta 429 (com Retry-After) se este IP ou esta conta estourou o teto."""
    agora = time.monotonic()
    email = (email or "").lower()
    for chave, teto in ((f"conta:{ip}|{email}", MAX_POR_CONTA), (f"ip:{ip}", MAX_POR_IP)):
        recentes = _podar(chave, agora)
        if len(recentes) >= teto:
            espera = _retry_after(recentes, teto, agora)
            raise HTTPException(
                status_code=429,
                detail="Muitas tentativas de login. Tente de novo em instantes.",
                headers={"Retry-After": str(espera)},
            )


def registrar_falha(ip: str, email: str):
    agora = time.monotonic()
    email = (email or "").lower()
    _falhas[f"conta:{ip}|{email}"].append(agora)
    _falhas[f"ip:{ip}"].append(agora)


def registrar_sucesso(ip: str, email: str):
    email = (email or "").lower()
    _falhas.pop(f"conta:{ip}|{email}", None)
    # O contador por IP não é zerado no sucesso de propósito: um IP que acerta
    # uma conta e erra outra continua somando para o teto de spray.


def _reset_para_testes():
    _falhas.clear()
