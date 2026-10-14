"""O estoque do cliente, consultado pelo agente de IA durante o atendimento.

A empresa sobe a planilha na tela de treino do agente e o agente passa a
responder "tem o Onix 2019?", "quanto custa o tênis 42?" com o que está nela.

Decisões:

  - **O estoque não vai no prompt; o agente consulta por ferramenta.** Uma
    planilha real tem centenas ou milhares de linhas. Inteira no prompt, ela
    seria cobrada em toda resposta do mês, inclusive no "bom dia", e passaria
    do contexto de qualquer modelo barato. O prompt leva só o formato (colunas,
    total, duas linhas de exemplo) e a ferramenta `consultar_estoque` devolve as
    linhas que casam com a pergunta. Mesmo caminho das ferramentas de agenda.

  - **As colunas são as da planilha, sem mapeamento.** Ao contrário da
    importação de contatos, aqui ninguém precisa saber qual coluna é o quê: o
    modelo lê "Modelo: Onix; Ano: 2019; Preço: 62.900" e entende. Pedir que o
    cliente mapeie "preço" e "quantidade" só travaria a planilha de quem vende
    algo que não tem quantidade.

  - **Subir de novo substitui o estoque inteiro.** Estoque é foto do momento:
    quem vendeu o Onix sobe a planilha sem ele, e o Onix tem de sumir. Mesclar
    deixaria vendido aparecendo como disponível. A troca é feita por `versao`:
    as linhas novas entram com a versão nova, o agente passa a apontar para ela
    e só então as antigas são apagadas. Uma consulta no meio da troca enxerga
    um estoque inteiro, nunca um pela metade ou vazio.

  - **A busca é por palavra, sem acento e sem caixa**, e exige todas as
    palavras da pergunta antes de aceitar só algumas. "onix prata" deve achar o
    Onix prata, não todos os Onix e todos os carros prata; sem nenhum que case
    com tudo, aí sim devolve os que casam com mais palavras.

  - **Um estoque por agente**, como as fotos e o treino: é o agente que a
    empresa está treinando, e um agente de peças e outro de carros na mesma
    empresa não devem enxergar o estoque um do outro.
"""
import io
import re
import unicodedata
import uuid
from datetime import datetime, timezone

import ai_client
import contatos_csv

MAX_LINHAS = 20000
MAX_COLUNAS = 40
# Valor de célula maior que isso é texto colado por engano (uma descrição
# inteira de anúncio) e iria inteiro para o modelo em cada consulta.
MAX_VALOR = 500
# Quantas linhas uma consulta devolve ao modelo. É o que ele lê e paga; com
# mais que isso a resposta certa é perguntar ao contato o que ele procura.
MAX_RESULTADOS = 20

# Palavras que aparecem em quase toda pergunta e não dizem nada sobre o item.
# Sem tirá-las, "tem o onix" exigiria a palavra "tem" na linha do Onix.
_VAZIAS = {
    "a", "o", "as", "os", "um", "uma", "de", "do", "da", "dos", "das", "e", "em",
    "no", "na", "nos", "nas", "com", "para", "pra", "por", "tem", "ter", "voces",
    "vcs", "quero", "queria", "algum", "alguma", "qual", "quais", "que", "me",
}


def _now():
    return datetime.now(timezone.utc).isoformat()


def normaliza(texto) -> str:
    texto = unicodedata.normalize("NFKD", str(texto or ""))
    texto = "".join(c for c in texto if not unicodedata.combining(c))
    return re.sub(r"\s+", " ", re.sub(r"[^a-z0-9.,\s]", " ", texto.lower())).strip()


def palavras(busca: str) -> list[str]:
    vistas = []
    for p in normaliza(busca).replace(",", " ").split():
        p = p.strip(".")
        if p and p not in _VAZIAS and p not in vistas:
            vistas.append(p)
    return vistas


class EstoqueError(ValueError):
    pass


def _celula(valor) -> str:
    """O valor de uma célula do Excel como a pessoa o vê na planilha."""
    if valor is None:
        return ""
    if isinstance(valor, bool):
        return "sim" if valor else "não"
    if isinstance(valor, float) and valor.is_integer():
        # 62900.0 viraria "62900.0" no texto que o modelo lê.
        return str(int(valor))
    if isinstance(valor, datetime):
        return valor.strftime("%d/%m/%Y %H:%M") if (valor.hour or valor.minute) else valor.strftime("%d/%m/%Y")
    if hasattr(valor, "strftime"):
        return valor.strftime("%d/%m/%Y")
    return str(valor)


def _le_xlsx(dados: bytes) -> tuple[list[str], list[dict]]:
    """Mesmo formato de `contatos_csv.le_arquivo`: cabeçalho e linhas como dicionário.

    Lê a primeira aba que tiver conteúdo, e o cabeçalho é a primeira linha com
    alguma célula preenchida: planilha de loja costuma ter um título ou uma
    linha em branco em cima da tabela.
    """
    import openpyxl  # preguiçoso: só quem sobe Excel paga o import
    try:
        livro = openpyxl.load_workbook(io.BytesIO(dados), read_only=True, data_only=True)
    except Exception:
        raise EstoqueError("Não foi possível ler esta planilha do Excel. Ela pode estar corrompida ou protegida por senha.")
    try:
        for aba in livro.worksheets:
            linhas = aba.iter_rows(values_only=True)
            # O cabeçalho é a primeira linha com duas células preenchidas ou
            # mais, procurada nas primeiras 20: um título em cima da tabela
            # ("Estoque ADS Veículos") ocupa uma célula só e não é cabeçalho.
            # Planilha de uma coluna só fica com a primeira linha preenchida.
            cabecalho, primeira, vistas = None, None, []
            for valores in linhas:
                vistas.append(valores)
                cheias = sum(1 for v in valores if _celula(v).strip())
                if cheias and primeira is None:
                    primeira = len(vistas) - 1
                if cheias >= 2:
                    cabecalho = valores
                    break
                if len(vistas) >= 20:
                    break
            if cabecalho is None:
                if primeira is None:
                    continue
                # Uma coluna só: o que vem depois da primeira linha cheia é dado.
                cabecalho = vistas[primeira]
                resto = vistas[primeira + 1:]
                linhas = iter(resto + list(linhas))
            colunas, vistos = [], {}
            for i, v in enumerate(cabecalho):
                nome = _celula(v).strip() or f"Coluna {i + 1}"
                # Duas colunas com o mesmo título se apagariam no dicionário.
                vistos[nome] = vistos.get(nome, 0) + 1
                colunas.append(nome if vistos[nome] == 1 else f"{nome} {vistos[nome]}")
            brutas = []
            for valores in linhas:
                brutas.append({c: _celula(v) for c, v in zip(colunas, valores)})
                if len(brutas) > MAX_LINHAS:
                    break
            return colunas, brutas
    finally:
        livro.close()
    raise EstoqueError("A planilha está vazia.")


def le_planilha(dados: bytes, filename: str = "") -> tuple[list[str], list[dict]]:
    """(colunas, linhas) da planilha, já limpas. Levanta `EstoqueError` com o motivo.

    Excel (.xlsx) ou CSV. O .xls antigo é recusado dizendo como salvar: é outro
    formato binário, e quem tem Excel salva em .xlsx em dois cliques.
    """
    nome = (filename or "").lower()
    if nome.endswith(".xls"):
        raise EstoqueError("Planilha .xls antiga não é lida. No Excel: Arquivo > Salvar como > Pasta de Trabalho do Excel (.xlsx).")
    if nome.endswith(".xlsx") or dados[:2] == b"PK":
        colunas, brutas = _le_xlsx(dados)
    else:
        try:
            colunas, brutas, _ = contatos_csv.le_arquivo(dados)
        except ValueError as e:
            raise EstoqueError(str(e))
    # O cabeçalho cru é a chave do dicionário de cada linha; o limpo é o que se mostra.
    pares = [(c, c.strip()) for c in colunas][:MAX_COLUNAS]
    colunas = [limpo for _, limpo in pares]
    if len(brutas) > MAX_LINHAS:
        raise EstoqueError(f"A planilha passa de {MAX_LINHAS:,} linhas".replace(",", "."))
    linhas = []
    for bruta in brutas:
        linha = {}
        for cru, limpo in pares:
            valor = str(bruta.get(cru) or "").strip()
            if valor:
                linha[limpo] = valor[:MAX_VALOR]
        if linha:  # linha toda em branco é resto de planilha, não item
            linhas.append(linha)
    if not linhas:
        raise EstoqueError("A planilha não tem nenhuma linha preenchida abaixo do cabeçalho")
    return colunas, linhas


def descreve(linha: dict) -> str:
    return "; ".join(f"{k}: {v}" for k, v in linha.items())


async def substituir(db, tenant_id: str, agente_id: str, filename: str,
                     colunas: list[str], linhas: list[dict]) -> dict:
    """Troca o estoque do agente pelo da planilha. Devolve o resumo gravado no agente."""
    versao = str(uuid.uuid4())
    docs = [{
        "id": str(uuid.uuid4()),
        "tenant_id": tenant_id,
        "agente_id": agente_id,
        "versao": versao,
        "ordem": i,
        "dados": linha,
        "busca": normaliza(" ".join(linha.values())),
    } for i, linha in enumerate(linhas)]
    for i in range(0, len(docs), 1000):
        await db.ai_estoque.insert_many(docs[i:i + 1000])

    resumo = {
        "versao": versao,
        "arquivo": filename,
        "colunas": colunas,
        "total": len(docs),
        "exemplos": linhas[:2],
        "atualizado_em": _now(),
    }
    await db.ai_agents.update_one(
        {"id": agente_id, "tenant_id": tenant_id},
        {"$set": {"estoque": resumo, "updated_at": _now()}})
    # Só agora: até a linha de cima, o agente ainda lia a versão anterior.
    await db.ai_estoque.delete_many(
        {"tenant_id": tenant_id, "agente_id": agente_id, "versao": {"$ne": versao}})
    return resumo


async def remover(db, tenant_id: str, agente_id: str):
    await db.ai_agents.update_one(
        {"id": agente_id, "tenant_id": tenant_id},
        {"$set": {"estoque": None, "updated_at": _now()}})
    await db.ai_estoque.delete_many({"tenant_id": tenant_id, "agente_id": agente_id})


async def buscar(db, tenant_id: str, agente: dict, busca: str = "",
                 limite: int = MAX_RESULTADOS) -> tuple[list[dict], int]:
    """(linhas, total_que_casou). É a mesma busca da ferramenta e da tela."""
    estoque = agente.get("estoque") or {}
    if not estoque.get("versao"):
        return [], 0
    base = {"tenant_id": tenant_id, "agente_id": agente["id"], "versao": estoque["versao"]}
    termos = palavras(busca)
    proj = {"_id": 0, "dados": 1, "busca": 1}

    if not termos:
        linhas = await db.ai_estoque.find(base, proj).sort("ordem", 1).to_list(limite)
        return [l["dados"] for l in linhas], estoque.get("total", len(linhas))

    regex = [{"busca": {"$regex": re.escape(t)}} for t in termos]
    filtro = {**base, "$and": regex}
    total = await db.ai_estoque.count_documents(filtro)
    if total:
        linhas = await db.ai_estoque.find(filtro, proj).sort("ordem", 1).to_list(limite)
        return [l["dados"] for l in linhas], total

    # Nenhuma linha tem todas as palavras: as que têm mais delas primeiro.
    # O teto de 500 só existe para uma palavra muito comum ("2019") não trazer
    # a planilha inteira pela rede para ordenar aqui.
    if len(termos) == 1:
        return [], 0
    candidatas = await db.ai_estoque.find({**base, "$or": regex}, proj).sort("ordem", 1).to_list(500)
    candidatas.sort(key=lambda l: -sum(t in l["busca"] for t in termos))
    return [l["dados"] for l in candidatas[:limite]], len(candidatas)


# ---------------------------------------------------------------------------
# O agente
# ---------------------------------------------------------------------------

FERRAMENTAS = [
    ai_client.Ferramenta(
        nome="consultar_estoque",
        descricao=(
            "Procura no estoque da empresa. Use SEMPRE antes de dizer que um item "
            "existe, está disponível, quanto custa ou quantos há: você não sabe o "
            "estoque sem chamar isto. Passe as palavras que identificam o item "
            "(modelo, marca, tamanho, cor). Sem 'busca', devolve os primeiros itens."
        ),
        parametros={
            "type": "object",
            "properties": {
                "busca": {"type": "string",
                          "description": "Palavras do item procurado, ex.: 'onix 2019 prata' ou 'tenis 42'."},
            },
            "required": [],
        },
    ),
]


def bloco_de_instrucoes(agente: dict) -> str:
    estoque = agente.get("estoque") or {}
    exemplos = "\n".join(f"- {descreve(l)}" for l in estoque.get("exemplos") or [])
    return (
        f"Estoque: a empresa tem {estoque.get('total', 0)} itens cadastrados, com as "
        f"colunas {', '.join(estoque.get('colunas') or [])}. Exemplo de linha:\n{exemplos}\n\n"
        "Consulte com a ferramenta consultar_estoque sempre que a conversa envolver um "
        "produto: disponibilidade, preço, quantidade, características. O que a "
        "ferramenta devolve é a única fonte sobre o estoque. Item que ela não "
        "encontrar, a empresa não tem: diga isso e ofereça o mais parecido que ela "
        "devolver, nunca invente. Não diga ao contato quantos itens existem no "
        "estoque nem leia a lista inteira para ele."
    )


def executor(db, tenant_id: str, agente: dict):
    async def consultar(args: dict) -> str:
        linhas, total = await buscar(db, tenant_id, agente, args.get("busca") or "")
        if not linhas:
            return ("Nenhum item do estoque corresponde a essa busca. Tente outras "
                    "palavras ou pergunte ao contato o que exatamente ele procura.")
        texto = "\n".join(f"- {descreve(l)}" for l in linhas)
        if total > len(linhas):
            texto += (f"\n(Mostrando {len(linhas)} de {total}. Se precisar, peça ao "
                      "contato mais detalhes para refinar a busca.)")
        return texto

    async def executar(nome: str, argumentos: dict) -> str:
        if nome != "consultar_estoque":
            return f"ERRO: ferramenta desconhecida ({nome})."
        return await consultar(argumentos or {})

    return executar
