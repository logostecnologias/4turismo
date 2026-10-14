"""Validação e normalização de data vinda do cliente.

Existe porque a mesma checagem já aparecia em vários lugares (previsão de
fechamento de negócio, vencimento de atividade) e as versões divergiam no detalhe que mais importa: se o valor é normalizado
para UTC ou guardado como veio.

A distinção é deliberada e não pode ser unificada:

  - **`para_utc`** é para *instante* — algo que acontece numa hora exata
    (`agendada_para`, `vence_em`). O navegador manda "…Z" e o Python gera
    "…+00:00": dois textos diferentes para o mesmo momento, que não se ordenam
    entre si. Como esses campos são comparados como string no banco,
    normalizar na entrada é o que faz a comparação valer.

  - **`valida_data`** é para *dia do calendário* (`previsao_fechamento`, campo
    personalizado de data). Aqui normalizar seria um erro: "2026-09-30" viraria
    "2026-09-30T00:00:00+00:00" e, exibido em UTC-3, apareceria como dia 29.
    Um prazo não pode andar para trás por causa de fuso.
"""
from datetime import datetime, timezone

from fastapi import HTTPException


def _parse(valor: str, rotulo: str) -> datetime:
    try:
        return datetime.fromisoformat(str(valor).replace("Z", "+00:00"))
    except ValueError:
        raise HTTPException(status_code=400, detail=f"{rotulo} inválida")


def para_utc(valor: str | None, rotulo: str = "Data") -> str | None:
    """Instante normalizado para UTC, no mesmo formato que o resto do sistema
    grava (`datetime.now(timezone.utc).isoformat()`)."""
    if not valor:
        return None
    dt = _parse(valor, rotulo)
    if dt.tzinfo is None:
        # Sem fuso no texto, assume UTC: o "agora" de referência é o do
        # servidor, e adivinhar o fuso do navegador aqui erraria mais.
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc).isoformat()


def valida_data(valor: str | None, rotulo: str = "Data") -> str | None:
    """Confere que é uma data legível e devolve **como veio**."""
    if not valor:
        return None
    _parse(valor, rotulo)
    return valor
