"""Guias do site: páginas de conteúdo ("Melhor época para os Lençóis", "Como
chegar a Barreirinhas").

São o tipo de página que o buscador e as IAs citam quando alguém pergunta
sobre a região, e que nenhum passeio responde sozinho. Moram em
`site_paginas`, um documento por guia, e saem em `/guia/<slug>` (lista em
`/guias`).

O texto é escrito como o da descrição do passeio, com três marcas simples, e
nunca HTML: `## ` no começo da linha é subtítulo, `- ` é item de lista, linha
em branco separa parágrafos. Quem escreve é a agência; HTML colado no corpo
seria script rodando no site.
"""
import re
import uuid
from datetime import datetime, timezone

from fastapi import HTTPException
from pydantic import BaseModel, Field

import sites

MAX_GUIAS = 30
MAX_TITULO = 120
MAX_RESUMO = 200
MAX_CORPO = 20000
MAX_PASSEIOS = 6
MAX_SLUG = 80


class GuiaIn(BaseModel):
    titulo: str = Field(default="", max_length=MAX_TITULO)
    resumo: str | None = Field(default=None, max_length=MAX_RESUMO)
    corpo: str = Field(default="", max_length=MAX_CORPO)
    capa_id: str | None = None
    publicada: bool = False
    passeios: list[str] = []
    slug: str | None = None
    ordem: int = 0


def _now():
    return datetime.now(timezone.utc).isoformat()


def blocos(corpo: str) -> list:
    """O corpo em blocos para o template: subtítulo, parágrafo ou lista."""
    saida, paragrafo, lista = [], [], []

    def fecha():
        if paragrafo:
            saida.append({"tipo": "p", "texto": " ".join(paragrafo)})
            paragrafo.clear()
        if lista:
            saida.append({"tipo": "lista", "itens": list(lista)})
            lista.clear()

    for linha in (corpo or "").replace("\r\n", "\n").split("\n"):
        l = linha.strip()
        if not l:
            fecha()
        elif l.startswith("### "):
            fecha()
            saida.append({"tipo": "h3", "texto": l[4:].strip()})
        elif l.startswith("## "):
            fecha()
            saida.append({"tipo": "h2", "texto": l[3:].strip()})
        elif re.match(r"^[-*•]\s+", l):
            if paragrafo:
                saida.append({"tipo": "p", "texto": " ".join(paragrafo)})
                paragrafo.clear()
            lista.append(re.sub(r"^[-*•]\s+", "", l))
        else:
            if lista:
                saida.append({"tipo": "lista", "itens": list(lista)})
                lista.clear()
            paragrafo.append(l)
    fecha()
    return saida


def minutos_de_leitura(corpo: str) -> int:
    return max(1, round(len(re.findall(r"\w+", corpo or "")) / 200))


async def slug_livre(db, tenant_id: str, desejado: str | None, titulo: str, exceto_id: str = None) -> str:
    """Único na agência. Gerado do título e **mantido** quando o título muda,
    pelo mesmo motivo do passeio: o link já foi divulgado."""
    if desejado:
        s = sites.slugify(desejado, MAX_SLUG)
        if not s or len(s) < 2:
            raise HTTPException(status_code=400, detail="Endereço do guia inválido")
        outro = await db.site_paginas.find_one({"tenant_id": tenant_id, "slug": s}, {"_id": 0, "id": 1})
        if outro and outro["id"] != exceto_id:
            raise HTTPException(status_code=409, detail=f"Outro guia já usa o endereço /guia/{s}")
        return s
    raiz = sites.slugify(titulo, MAX_SLUG) or "guia"
    s, n = raiz, 2
    while True:
        outro = await db.site_paginas.find_one({"tenant_id": tenant_id, "slug": s}, {"_id": 0, "id": 1})
        if not outro or outro["id"] == exceto_id:
            return s
        s = f"{raiz}-{n}"
        n += 1


async def campos(db, tenant_id: str, body: GuiaIn, atual: dict | None = None) -> dict:
    titulo = body.titulo.strip()
    if not titulo:
        raise HTTPException(status_code=400, detail="Dê um título ao guia")
    corpo = (body.corpo or "").strip()
    if body.publicada and len(corpo) < 200:
        raise HTTPException(status_code=400, detail=(
            "O guia está curto demais para publicar (menos de 200 caracteres). "
            "Página rasa não ajuda no Google e passa a impressão de site abandonado"))
    sobra = [i for i in _instrucoes() if i in corpo]
    if body.publicada and sobra:
        raise HTTPException(status_code=400, detail=(
            f"O guia ainda tem texto de orientação do modelo (\"{sobra[0][:70]}…\"). "
            "Troque pelo conteúdo da agência antes de publicar"))
    if body.capa_id and not await db.media_files.find_one(
            {"id": body.capa_id, "tenant_id": tenant_id, "kind": "image"}, {"_id": 0, "id": 1}):
        raise HTTPException(status_code=400, detail="Imagem de capa inválida")
    passeios = []
    for pid in body.passeios or []:
        if pid in passeios:
            continue
        if not await db.services.find_one({"id": pid, "tenant_id": tenant_id}, {"_id": 0, "id": 1}):
            raise HTTPException(status_code=400, detail="Passeio relacionado inválido")
        passeios.append(pid)
    if len(passeios) > MAX_PASSEIOS:
        raise HTTPException(status_code=400, detail=f"No máximo {MAX_PASSEIOS} passeios relacionados")
    desejado = (body.slug or "").strip() or (atual or {}).get("slug")
    await sites.marcar_imagens_do_site(db, tenant_id, [body.capa_id])
    return {
        "titulo": titulo, "resumo": (body.resumo or "").strip() or None, "corpo": corpo,
        "capa_id": body.capa_id or None, "publicada": bool(body.publicada), "passeios": passeios,
        "ordem": int(body.ordem or 0),
        "slug": await slug_livre(db, tenant_id, desejado, titulo, exceto_id=(atual or {}).get("id")),
        "updated_at": _now(),
    }


async def criar(db, tenant_id: str, body: GuiaIn) -> dict:
    if await db.site_paginas.count_documents({"tenant_id": tenant_id}) >= MAX_GUIAS:
        raise HTTPException(status_code=400, detail=f"No máximo {MAX_GUIAS} guias por site")
    doc = {"id": str(uuid.uuid4()), "tenant_id": tenant_id, "created_at": _now(),
           **await campos(db, tenant_id, body)}
    await db.site_paginas.insert_one(dict(doc))
    doc.pop("_id", None)
    return doc


async def publicados(db, tenant_id: str, com_rascunho: bool = False) -> list:
    filtro = {"tenant_id": tenant_id}
    if not com_rascunho:
        filtro["publicada"] = True
    return await db.site_paginas.find(filtro, {"_id": 0}).sort([("ordem", 1), ("titulo", 1)]).to_list(MAX_GUIAS)


# Pontos de partida, para a agência não encarar uma página em branco. São só a
# estrutura (os subtítulos e o que vai em cada um): o conhecimento é dela, e um
# texto genérico pronto seria igual ao de toda outra agência, que é o que o
# Google trata como conteúdo copiado.
MODELOS = [
    {"id": "melhor-epoca", "titulo": "Melhor época para visitar os Lençóis Maranhenses",
     "resumo": "Quando as lagoas estão cheias, o clima mês a mês e quando evitar.",
     "corpo": "Conte em duas ou três frases a resposta curta: em que meses as lagoas estão no melhor ponto.\n\n"
              "## Mês a mês\n- Janeiro a março: \n- Abril a junho: \n- Julho a setembro: \n- Outubro a dezembro: \n\n"
              "## Chuva e calor\nComo é o tempo em cada época e o que isso muda nos passeios.\n\n"
              "## Alta e baixa temporada\nQuando lota, quando fica tranquilo, e o que reservar com antecedência.\n\n"
              "## Nossa dica\nA época que a equipe recomenda, e por quê."},
    {"id": "como-chegar", "titulo": "Como chegar a Barreirinhas",
     "resumo": "De São Luís até Barreirinhas: estrada, transfer, voo e quanto tempo leva.",
     "corpo": "Conte em duas frases o caminho mais comum e quanto tempo leva.\n\n"
              "## De São Luís de carro\nDistância, estrada, tempo e o que observar no caminho.\n\n"
              "## Transfer e van\nComo funciona, de onde sai, quanto tempo leva. Se a agência oferece, diga aqui.\n\n"
              "## Avião\nVoos para São Luís e para o aeroporto regional, quando houver.\n\n"
              "## Ao chegar\nOnde ficar, como é a cidade, e como chegar aos passeios."},
    {"id": "o-que-levar", "titulo": "O que levar para os Lençóis Maranhenses",
     "resumo": "Roupa, protetor, água e o que deixar no hotel.",
     "corpo": "Conte em uma frase o essencial.\n\n"
              "## Na mochila\n- \n- \n- \n\n## Roupa e calçado\nO que funciona na areia e na água.\n\n"
              "## O que deixar no hotel\nO que atrapalha nos passeios.\n\n## Crianças e idosos\nCuidados extras."},
    {"id": "roteiro-dias", "titulo": "Roteiro de 3 dias nos Lençóis Maranhenses",
     "resumo": "Como aproveitar três dias em Barreirinhas e região.",
     "corpo": "Conte para quem é este roteiro e o que dá para conhecer em três dias.\n\n"
              "## Dia 1\n\n## Dia 2\n\n## Dia 3\n\n## Se tiver mais um dia\n"},
]


def _instrucoes() -> list:
    """As frases de orientação dos modelos, que não podem ir para o ar."""
    saida = []
    for m in MODELOS:
        for linha in m["corpo"].splitlines():
            l = linha.strip()
            if l and not l.startswith(("#", "-")):
                saida.append(l)
    return saida
