"""Endereço pelo CEP (`cep.py`), com os provedores dublados por MockTransport.

O que importa aqui é o que confundiria a agência: o ViaCEP fora do ar sem cair
na BrasilAPI, o `{"erro": true}` com 200 lido como endereço, o CEP de cidade
inteira (sem rua) tratado como inexistente, e uma falha de rede ficando no
cache como "CEP não existe".
"""
import asyncio
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import httpx
from fastapi import HTTPException

import cep


def _transporte(respostas: dict, chamadas: list):
    def handler(req: httpx.Request):
        chamadas.append(req.url.host)
        r = respostas.get(req.url.host)
        if isinstance(r, Exception):
            raise r
        status, corpo = r
        return httpx.Response(status, json=corpo)
    return httpx.MockTransport(handler)


async def _consulta(valor, respostas):
    chamadas = []
    try:
        return await cep.consultar(valor, transport=_transporte(respostas, chamadas)), chamadas
    except HTTPException as e:
        return e, chamadas


async def etapa_viacep():
    cep._cache.clear()
    r, ch = await _consulta("65000-100", {"viacep.com.br": (200, {
        "logradouro": "Rua Grande", "bairro": "Centro", "localidade": "São Luís", "uf": "MA"})})
    assert r == {"cep": "65000-100", "rua": "Rua Grande", "bairro": "Centro", "cidade": "São Luís", "uf": "MA"}
    assert ch == ["viacep.com.br"]
    # Segunda vez vem do cache, sem rede.
    r2, ch2 = await _consulta("65000100", {})
    assert r2 == r and ch2 == []


async def etapa_cidade_inteira():
    cep._cache.clear()
    r, _ = await _consulta("65590000", {"viacep.com.br": (200, {
        "logradouro": "", "bairro": "", "localidade": "Barreirinhas", "uf": "MA"})})
    assert r["cidade"] == "Barreirinhas" and r["uf"] == "MA" and r["rua"] == "" and r["bairro"] == ""


async def etapa_reserva_quando_viacep_cai():
    cep._cache.clear()
    r, ch = await _consulta("65590000", {
        "viacep.com.br": httpx.ConnectError("fora"),
        "brasilapi.com.br": (200, {"street": "", "neighborhood": "", "city": "Barreirinhas", "state": "MA"})})
    assert r["cidade"] == "Barreirinhas"
    assert ch == ["viacep.com.br", "brasilapi.com.br"]


async def etapa_inexistente():
    cep._cache.clear()
    r, ch = await _consulta("99999999", {"viacep.com.br": (200, {"erro": "true"}),
                                         "brasilapi.com.br": (404, {"message": "não encontrado"})})
    assert isinstance(r, HTTPException) and r.status_code == 404
    assert ch == ["viacep.com.br", "brasilapi.com.br"]


async def etapa_tudo_fora_nao_fica_no_cache():
    cep._cache.clear()
    r, _ = await _consulta("65000100", {"viacep.com.br": (502, {}), "brasilapi.com.br": httpx.ReadTimeout("lento")})
    assert isinstance(r, HTTPException) and r.status_code == 503
    assert "65000100" not in cep._cache
    r2, _ = await _consulta("65000100", {"viacep.com.br": (200, {
        "logradouro": "Rua Grande", "bairro": "Centro", "localidade": "São Luís", "uf": "MA"})})
    assert r2["cidade"] == "São Luís"


async def etapa_malformado_e_uf_estranha():
    cep._cache.clear()
    r, ch = await _consulta("6500", {})
    assert isinstance(r, HTTPException) and r.status_code == 400 and ch == []
    # Resposta sem UF válida não vira endereço (o PUT recusaria a UF depois).
    r, _ = await _consulta("65000100", {"viacep.com.br": (200, {"localidade": "X", "uf": "ZZ"}),
                                        "brasilapi.com.br": (404, {})})
    assert isinstance(r, HTTPException) and r.status_code == 404


async def _suite():
    await etapa_viacep()
    await etapa_cidade_inteira()
    await etapa_reserva_quando_viacep_cai()
    await etapa_inexistente()
    await etapa_tudo_fora_nao_fica_no_cache()
    await etapa_malformado_e_uf_estranha()


def test_suite():
    asyncio.run(_suite())
