"""Tradução do que a agência escreve no site, para inglês e espanhol.

**A tradução é guardada pelo texto original, não pelo campo.** Cada frase em
português vira uma chave (o hash dela), e `site_traducoes` guarda, por idioma,
a tradução daquela frase exata. Daí saem as três garantias que importam:

- A agência muda a frase em português: o hash muda, a tradução antiga deixa de
  casar, e a página mostra o **português** até traduzir de novo. Nunca a
  tradução de um texto que não existe mais (o turista leria um preço ou um
  horário que a agência já corrigiu).
- Reordenar perguntas ou itens não perde nada: a chave é o texto, não a posição.
- A mesma frase em dois lugares ("Protetor solar" em três passeios) é traduzida
  uma vez.

Quem traduz é a IA, pelo mesmo caminho do assistente (`ai_runtime`), com a
chave do assistente de IA da agência ou a da plataforma, cota e medição de
sempre. A agência pode corrigir qualquer tradução à mão (`manual`), e uma
correção manual nunca é sobrescrita por tradução automática do mesmo texto.

Depoimentos não são traduzidos de propósito: são a fala de um turista, e
traduzir uma citação é pôr palavras na boca de alguém.
"""
import hashlib
import json
import re
from datetime import datetime, timezone

from fastapi import HTTPException

import site_i18n

# Lote por chamada: a resposta do provedor tem teto de saída (ai_client.MAX_SAIDA,
# ~2000 tokens), e a tradução tem mais ou menos o tamanho do original.
LOTE_CARACTERES = 2400
LOTE_ITENS = 25
PEDACO = 1800          # texto maior que isso (corpo de guia) vai por parágrafos

SPEC_SITE = {"texto": [("chamada", "Chamada do topo"), ("sobre", "Sobre a agência"),
                       ("cancelamento", "Política de cancelamento"), ("horario", "Horário de atendimento")],
             "listas": [("diferenciais", "Diferencial"), ("pagamento", "Forma de pagamento")],
             "objetos": [("faq", ("pergunta", "resposta"), "Pergunta frequente")]}
SPEC_PASSEIO = {"texto": [("nome", "Nome"), ("site_resumo", "Resumo"), ("site_descricao", "Descrição"),
                          ("site_melhor_epoca", "Melhor época"), ("site_chuva", "Se chover")],
                "listas": [("site_inclui", "O que inclui"), ("site_levar", "O que levar")],
                "objetos": [("site_roteiro", ("texto",), "Roteiro"),
                            ("site_faq", ("pergunta", "resposta"), "Pergunta")],
                "dicts": [("site_legendas", "Legenda de foto")]}
SPEC_GUIA = {"texto": [("titulo", "Título"), ("resumo", "Resumo"), ("corpo", "Texto")]}


def chave(texto: str) -> str:
    return hashlib.sha1((texto or "").strip().encode("utf-8")).hexdigest()[:20]


def _percorrer(doc: dict, spec: dict, prefixo: str):
    for campo, rotulo in spec.get("texto", []):
        if (doc.get(campo) or "").strip():
            yield f"{prefixo} · {rotulo}", doc[campo]
    for campo, rotulo in spec.get("listas", []):
        for item in doc.get(campo) or []:
            if (item or "").strip():
                yield f"{prefixo} · {rotulo}", item
    for campo, subcampos, rotulo in spec.get("objetos", []):
        for obj in doc.get(campo) or []:
            for sub in subcampos:
                if (obj.get(sub) or "").strip():
                    yield f"{prefixo} · {rotulo}", obj[sub]
    for campo, rotulo in spec.get("dicts", []):
        for valor in (doc.get(campo) or {}).values():
            if (valor or "").strip():
                yield f"{prefixo} · {rotulo}", valor


def _aplicar(doc: dict, spec: dict, tr) -> dict:
    novo = dict(doc)
    for campo, _ in spec.get("texto", []):
        if novo.get(campo):
            novo[campo] = tr(novo[campo])
    for campo, _ in spec.get("listas", []):
        if novo.get(campo):
            novo[campo] = [tr(i) for i in novo[campo]]
    for campo, subcampos, _ in spec.get("objetos", []):
        if novo.get(campo):
            novo[campo] = [{**o, **{s: tr(o[s]) for s in subcampos if o.get(s)}} for o in novo[campo]]
    for campo, _ in spec.get("dicts", []):
        if novo.get(campo):
            novo[campo] = {k: tr(v) for k, v in novo[campo].items()}
    return novo


class Tradutor:
    """`tr(texto_pt)`: a tradução daquela frase exata, ou o próprio português."""

    def __init__(self, idioma: str, mapa: dict | None = None):
        self.idioma = idioma
        self.mapa = mapa or {}

    def __call__(self, texto):
        if not texto or self.idioma == site_i18n.PADRAO:
            return texto
        return self.mapa.get(chave(texto), texto)

    def site(self, site: dict) -> dict:
        return _aplicar(site, SPEC_SITE, self)

    def passeio(self, p: dict) -> dict:
        return _aplicar(p, SPEC_PASSEIO, self)

    def guia(self, g: dict) -> dict:
        return _aplicar(g, SPEC_GUIA, self)


async def carregar(db, tenant_id: str, idioma: str) -> Tradutor:
    if idioma == site_i18n.PADRAO:
        return Tradutor(idioma)
    mapa = {d["chave"]: d["texto"] async for d in db.site_traducoes.find(
        {"tenant_id": tenant_id, "idioma": idioma}, {"_id": 0, "chave": 1, "texto": 1})}
    return Tradutor(idioma, mapa)


async def textos(db, tenant_id: str) -> list:
    """Todo texto traduzível do site, com o rótulo de onde ele aparece."""
    import sites
    import site_guias
    site = await sites.site_da_empresa(db, tenant_id)
    vistos, saida = set(), []

    def junta(itens):
        for rotulo, texto in itens:
            k = chave(texto)
            if k not in vistos:
                vistos.add(k)
                saida.append({"chave": k, "rotulo": rotulo, "texto": texto.strip()})

    junta(_percorrer(site, SPEC_SITE, "Site"))
    for p in await sites.passeios_do_site(db, tenant_id):
        junta(_percorrer(p, SPEC_PASSEIO, f"Passeio {p['nome']}"))
    for g in await site_guias.publicados(db, tenant_id, com_rascunho=True):
        junta(_percorrer(g, SPEC_GUIA, f"Guia {g['titulo']}"))
    return saida


async def revisao(db, tenant_id: str, idioma: str) -> dict:
    """O que a tela de revisão mostra: cada texto, a tradução atual e de onde veio."""
    if idioma not in site_i18n.EXTRAS:
        raise HTTPException(status_code=400, detail="Idioma inválido")
    atuais = {d["chave"]: d async for d in db.site_traducoes.find(
        {"tenant_id": tenant_id, "idioma": idioma}, {"_id": 0})}
    itens = []
    for t in await textos(db, tenant_id):
        d = atuais.get(t["chave"])
        itens.append({**t, "traducao": d["texto"] if d else None, "manual": bool(d and d.get("manual"))})
    feitos = sum(1 for i in itens if i["traducao"])
    return {"idioma": idioma, "total": len(itens), "traduzidos": feitos, "faltando": len(itens) - feitos,
            "itens": itens}


async def resumo(db, tenant_id: str) -> dict:
    todos = await textos(db, tenant_id)
    chaves = {t["chave"] for t in todos}
    saida = {}
    for idioma in site_i18n.EXTRAS:
        feitos = await db.site_traducoes.count_documents(
            {"tenant_id": tenant_id, "idioma": idioma, "chave": {"$in": list(chaves)}}) if chaves else 0
        saida[idioma] = {"total": len(chaves), "traduzidos": feitos, "faltando": len(chaves) - feitos}
    return saida


async def _gravar(db, tenant_id: str, idioma: str, k: str, origem: str, texto: str, manual: bool):
    await db.site_traducoes.update_one(
        {"tenant_id": tenant_id, "idioma": idioma, "chave": k},
        {"$set": {"origem": origem, "texto": texto, "manual": manual,
                  "atualizado_em": datetime.now(timezone.utc).isoformat()}},
        upsert=True)


async def salvar_manual(db, tenant_id: str, idioma: str, k: str, texto: str | None) -> dict:
    if idioma not in site_i18n.EXTRAS:
        raise HTTPException(status_code=400, detail="Idioma inválido")
    original = next((t for t in await textos(db, tenant_id) if t["chave"] == k), None)
    if not original:
        raise HTTPException(status_code=404, detail="Esse texto não está mais no site (foi alterado em português?)")
    texto = (texto or "").strip()
    if not texto:
        await db.site_traducoes.delete_one({"tenant_id": tenant_id, "idioma": idioma, "chave": k})
        return {"chave": k, "traducao": None}
    if len(texto) > max(400, len(original["texto"]) * 3):
        raise HTTPException(status_code=400, detail="Tradução longa demais para esse texto")
    await _gravar(db, tenant_id, idioma, k, original["texto"], texto, manual=True)
    return {"chave": k, "traducao": texto, "manual": True}


# ---------------------------------------------------------------------------
# A tradução automática
# ---------------------------------------------------------------------------

def instrucoes(idioma: str) -> str:
    return (
        f"Você traduz o site de uma agência de turismo receptivo do Maranhão, Brasil, do português para o "
        f"{site_i18n.NOME_PARA_IA[idioma]}. Quem lê é um turista estrangeiro planejando a viagem.\n\n"
        "Regras:\n"
        "- Traduza com naturalidade, como um site de turismo escrito originalmente nesse idioma, não palavra por palavra.\n"
        "- Mantenha nomes próprios de lugares como estão (Lençóis Maranhenses, Barreirinhas, Atins, Lagoa Azul, "
        "Rio Preguiças, Santo Amaro), nomes de empresas e de pessoas.\n"
        "- Mantenha horários (07:30), datas, números, preços em reais (R$) e o que estiver entre aspas como nome.\n"
        "- Mantenha as marcas de formatação no começo das linhas: '## ', '### ' e '- '. Mantenha as quebras de linha.\n"
        "- Não acrescente nem tire informação. Não explique nada.\n\n"
        "Você recebe um JSON {\"itens\": [{\"id\": n, \"texto\": \"...\"}]} e responde SOMENTE com um JSON "
        "{\"itens\": [{\"id\": n, \"texto\": \"tradução\"}]}, com os mesmos ids, sem nenhum texto fora do JSON."
    )


async def _agente_que_paga(db, tenant_id: str) -> dict:
    """O assistente de IA da agência com chave funcionando; sem ele, a plataforma."""
    for ag in await db.ai_agents.find({"tenant_id": tenant_id}, {"_id": 0}).to_list(50):
        if ag.get("credencial") and ag.get("credencial_status") != "erro":
            return ag
    return {"id": None, "nome": "Tradução do site"}


def _ler_json(texto: str) -> dict:
    t = (texto or "").strip()
    t = re.sub(r"^```(?:json)?\s*|\s*```$", "", t)
    ini, fim = t.find("{"), t.rfind("}")
    if ini < 0 or fim <= ini:
        raise ValueError("sem JSON")
    return json.loads(t[ini:fim + 1])


async def _chamar(db, tenant_id: str, idioma: str, agente: dict, itens: list[str]) -> list[str]:
    import ai_runtime
    entrada = json.dumps({"itens": [{"id": i, "texto": t} for i, t in enumerate(itens)]}, ensure_ascii=False)
    try:
        resp = await ai_runtime.responder_por_agente(
            db, tenant_id, agente, instrucoes(idioma), [{"role": "user", "content": entrada}])
    except HTTPException as e:
        if e.status_code == 400 and not agente.get("credencial"):
            raise HTTPException(status_code=400, detail=(
                "Para traduzir automaticamente, a agência precisa de uma chave de IA. Configure-a num "
                "assistente de IA (Automação → Assistente de IA) ou digite as traduções à mão.")) from e
        raise
    try:
        dados = _ler_json(resp.texto)
        por_id = {int(i["id"]): str(i["texto"]).strip() for i in dados.get("itens") or []}
    except (ValueError, KeyError, TypeError):
        raise HTTPException(status_code=502, detail="A IA respondeu fora do formato esperado. Tente de novo.")
    return [por_id.get(i) or None for i in range(len(itens))]


def _pedacos(texto: str) -> list[str]:
    partes, atual = [], ""
    for par in texto.split("\n\n"):
        if atual and len(atual) + len(par) + 2 > PEDACO:
            partes.append(atual)
            atual = par
        else:
            atual = f"{atual}\n\n{par}" if atual else par
    if atual:
        partes.append(atual)
    return partes


async def traduzir(db, tenant_id: str, idioma: str) -> dict:
    """Traduz um lote do que falta. A tela chama de novo até `restantes` zerar.

    Um lote por pedido, e não o site inteiro: dois idiomas de um site completo
    são dezenas de chamadas ao provedor, e um pedido só levaria minutos, com a
    tela parada e sem dizer quanto falta.
    """
    if idioma not in site_i18n.EXTRAS:
        raise HTTPException(status_code=400, detail="Idioma inválido")
    todos = await textos(db, tenant_id)
    # Tradução de frase que não está mais no site não serve para nada (a chave é
    # o texto): sai, para a coleção não crescer a cada edição em português.
    await db.site_traducoes.delete_many({"tenant_id": tenant_id, "idioma": idioma,
                                         "chave": {"$nin": [t["chave"] for t in todos]}})
    feitos = {d["chave"] async for d in db.site_traducoes.find(
        {"tenant_id": tenant_id, "idioma": idioma}, {"_id": 0, "chave": 1})}
    faltam = [t for t in todos if t["chave"] not in feitos]
    if not faltam:
        return {"traduzidos": 0, "restantes": 0}
    agente = await _agente_que_paga(db, tenant_id)

    primeiro = faltam[0]
    if len(primeiro["texto"]) > PEDACO:
        # Texto grande (corpo de guia): sozinho, em pedaços, e remontado.
        pedacos = _pedacos(primeiro["texto"])
        traduzidos = []
        for p in pedacos:
            traduzidos += await _chamar(db, tenant_id, idioma, agente, [p])
        if all(traduzidos):
            await _gravar(db, tenant_id, idioma, primeiro["chave"], primeiro["texto"],
                          "\n\n".join(traduzidos), manual=False)
            return {"traduzidos": 1, "restantes": len(faltam) - 1}
        raise HTTPException(status_code=502, detail="A IA não devolveu a tradução completa. Tente de novo.")

    lote, tamanho = [], 0
    for t in faltam:
        if len(t["texto"]) > PEDACO:
            continue
        if lote and (tamanho + len(t["texto"]) > LOTE_CARACTERES or len(lote) >= LOTE_ITENS):
            break
        lote.append(t)
        tamanho += len(t["texto"])
    resultado = await _chamar(db, tenant_id, idioma, agente, [t["texto"] for t in lote])
    n = 0
    for t, traducao in zip(lote, resultado):
        if traducao:
            await _gravar(db, tenant_id, idioma, t["chave"], t["texto"], traducao, manual=False)
            n += 1
    if not n:
        raise HTTPException(status_code=502, detail="A IA não devolveu nenhuma tradução. Tente de novo.")
    return {"traduzidos": n, "restantes": len(faltam) - n}
