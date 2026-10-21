"""Endereço a partir do CEP, para a tela Site preencher rua, bairro, cidade e UF.

Consulta o **ViaCEP** e, se ele não responder, a **BrasilAPI**. Passa pelo
servidor, e não pelo navegador, para a troca de provedor morar num lugar só e
para a resposta já vir no formato e nos tamanhos que o `PUT /api/site` aceita.
O endereço consultado é sempre um de dois hosts fixos com 8 dígitos no
caminho: não há URL vinda de fora, então não passa pelo `net_guard`.

**CEP de cidade inteira** (o caso de Barreirinhas, Atins, Santo Amaro: um CEP
só para o município) volta sem rua e sem bairro. Isso não é "não encontrado":
a cidade e a UF estão certas, e a tela pede o resto à mão.

CEP não muda de endereço, então o resultado fica num cache pequeno em memória.
"""
import logging
import re
from collections import OrderedDict

import httpx
from fastapi import HTTPException

import sites

logger = logging.getLogger(__name__)

TIMEOUT = httpx.Timeout(4.0, connect=3.0)
MAX_CACHE = 500
MAX_RUA = 120
MAX_BAIRRO = 60
MAX_CIDADE = 60

_cache: "OrderedDict[str, dict | None]" = OrderedDict()


class _Indisponivel(Exception):
    """O provedor não respondeu direito: tentar o próximo."""


def _corta(v, n: int) -> str:
    return (str(v or "").strip())[:n]


def _endereco(rua, bairro, cidade, uf) -> dict | None:
    uf = str(uf or "").strip().upper()
    cidade = _corta(cidade, MAX_CIDADE)
    if not cidade or uf not in sites.UFS:
        return None
    return {"rua": _corta(rua, MAX_RUA), "bairro": _corta(bairro, MAX_BAIRRO), "cidade": cidade, "uf": uf}


async def _viacep(client: httpx.AsyncClient, cep: str) -> dict | None:
    try:
        r = await client.get(f"https://viacep.com.br/ws/{cep}/json/")
    except httpx.HTTPError as e:
        raise _Indisponivel(str(e))
    if r.status_code == 400:
        return None
    if r.status_code != 200:
        raise _Indisponivel(f"HTTP {r.status_code}")
    try:
        d = r.json()
    except ValueError:
        raise _Indisponivel("resposta não é JSON")
    # O ViaCEP responde 200 com {"erro": true} (ou "true", nas versões novas)
    # para CEP que não existe.
    if d.get("erro") in (True, "true"):
        return None
    return _endereco(d.get("logradouro"), d.get("bairro"), d.get("localidade"), d.get("uf"))


async def _brasilapi(client: httpx.AsyncClient, cep: str) -> dict | None:
    try:
        r = await client.get(f"https://brasilapi.com.br/api/cep/v1/{cep}")
    except httpx.HTTPError as e:
        raise _Indisponivel(str(e))
    if r.status_code == 404:
        return None
    if r.status_code != 200:
        raise _Indisponivel(f"HTTP {r.status_code}")
    try:
        d = r.json()
    except ValueError:
        raise _Indisponivel("resposta não é JSON")
    return _endereco(d.get("street"), d.get("neighborhood"), d.get("city"), d.get("state"))


PROVEDORES = (_viacep, _brasilapi)


async def consultar(valor: str, transport: httpx.AsyncBaseTransport | None = None) -> dict:
    """Devolve `{cep, rua, bairro, cidade, uf}`. 400 para CEP malformado, 404
    para CEP que não existe, 503 quando nenhum provedor respondeu."""
    cep = re.sub(r"\D", "", valor or "")
    if len(cep) != 8:
        raise HTTPException(status_code=400, detail="CEP com 8 números, como 65590-000")
    if cep in _cache:
        _cache.move_to_end(cep)
        achado = _cache[cep]
    else:
        achado, respondeu = None, False
        async with httpx.AsyncClient(timeout=TIMEOUT, transport=transport, follow_redirects=False) as client:
            for provedor in PROVEDORES:
                try:
                    achado = await provedor(client, cep)
                    respondeu = True
                except _Indisponivel as e:
                    logger.warning("CEP %s: %s falhou (%s)", cep, provedor.__name__, e)
                    continue
                if achado:
                    break
        if not respondeu:
            raise HTTPException(status_code=503,
                                detail="Não consegui consultar o CEP agora. Preencha o endereço à mão.")
        # "Não existe" só é guardado quando algum provedor respondeu: guardar
        # a falha de rede faria um CEP válido parecer inexistente até reiniciar.
        _cache[cep] = achado
        if len(_cache) > MAX_CACHE:
            _cache.popitem(last=False)
    if not achado:
        raise HTTPException(status_code=404, detail="CEP não encontrado. Confira os números.")
    return {"cep": f"{cep[:5]}-{cep[5:]}", **achado}
