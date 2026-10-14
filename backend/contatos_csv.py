"""Importação e exportação de contatos em CSV.

É o que faltava para a base ser utilizável: contato só nascia de mensagem
recebida ou de cadastro um por um. Uma agência chega com planilha de milhares
de leads, e sem isto o funil não tem o que processar.

Duas coisas concentram todo o risco aqui:

  - **O número.** Ele é a identidade do contato em todo o sistema (conversa,
    histórico). Uma planilha real traz "(11) 99999-8888",
    "11999998888", "+55 11 99999-8888" e "5511999998888" na mesma coluna. O
    normalizador aceita as quatro formas e **nunca inventa dígito**: acrescentar
    o nono dígito num número de 8 manda a mensagem para outra pessoa, o que é
    pior do que recusar a linha.

  - **O que a planilha chama de quê.** Nenhum cliente exporta com os nossos
    nomes de coluna. O mapeamento é sugerido por sinônimos e fica editável na
    tela — adivinhar sem mostrar seria gravar e-mail no campo de empresa sem
    ninguém perceber.

Nada é importado em silêncio: o resumo devolve novos, atualizados, ignorados e
a lista de linhas com erro, com o número da linha do arquivo.
"""
import csv
import io
import logging
import re
import unicodedata

logger = logging.getLogger("turismo.contatos_csv")

# Teto do arquivo. Não é limite de negócio, é de memória: o CSV é lido inteiro
# como texto para o mapeamento e a prévia funcionarem.
MAX_BYTES = 10 * 1024 * 1024
MAX_LINHAS = 20000

# Quantas linhas com erro voltam detalhadas. O total vem separado — cortar sem
# dizer quantas ficaram de fora seria esconder o tamanho do problema.
MAX_ERROS_DETALHADOS = 50

DDI_PADRAO = "55"

# Alvos do mapeamento e os nomes de coluna que costumam aparecer para cada um.
# Cotejo por texto normalizado (sem acento, sem espaço, minúsculo).
CAMPOS_PADRAO: dict[str, tuple[str, ...]] = {
    "nome": ("nome", "nomecompleto", "cliente", "contato", "name", "fullname", "razaosocial",
             "paciente", "nomedopaciente", "nomepaciente"),
    "numero_whatsapp": ("numero", "numerowhatsapp", "whatsapp", "telefone", "celular", "fone",
                        "tel", "phone", "mobile", "contatotelefone", "numerodetelefone",
                        "telefonedopaciente", "celulardopaciente", "whatsappdopaciente"),
    "email": ("email", "emails", "enderecodeemail", "mail"),
    "empresa": ("empresa", "company", "organizacao", "negocio", "estabelecimento"),
    "cargo": ("cargo", "funcao", "posicao", "role", "jobtitle", "titulo"),
    "origem": ("origem", "fonte", "source", "canal", "comonosconheceu"),
    "tags": ("tags", "etiquetas", "etiqueta", "marcadores", "labels", "segmento"),
    "responsavel_email": ("responsavel", "responsavelemail", "vendedor", "consultor", "owner",
                          "atendente", "dono"),
}

# Colunas da exportação, na ordem em que saem. Os campos personalizados entram
# depois, um por definição da empresa.
COLUNAS_EXPORT = ["nome", "numero_whatsapp", "email", "empresa", "cargo", "origem",
                  "tags", "responsavel_email", "opt_out", "created_at"]


def normaliza_texto(valor: str) -> str:
    limpo = unicodedata.normalize("NFKD", valor or "")
    limpo = "".join(c for c in limpo if not unicodedata.combining(c))
    return re.sub(r"[^a-z0-9]", "", limpo.lower())


def normaliza_numero(bruto: str, ddi: str = DDI_PADRAO) -> tuple[str | None, str | None]:
    """Devolve (numero_em_digitos, erro).

    Regras, para um sistema que atende o Brasil:
      - 10 ou 11 dígitos → é DDD + número sem DDI; o DDI é acrescentado;
      - 12 ou 13 dígitos começando com o DDI → já está completo;
      - qualquer outro tamanho → recusa, dizendo o motivo.

    Não completa o nono dígito de propósito. Um celular antigo de 8 dígitos e
    um telefone fixo têm a mesma forma, e "corrigir" o fixo criaria um número
    que pertence a outra pessoa — a mensagem sairia, para o destinatário errado.
    """
    digitos = re.sub(r"\D", "", bruto or "")
    if not digitos:
        return None, "número vazio"
    # Zeros de discagem que sobram de planilha ("055...", "0 11 ...").
    digitos = digitos.lstrip("0") or digitos

    if len(digitos) in (10, 11):
        return ddi + digitos, None
    if len(digitos) in (12, 13) and digitos.startswith(ddi):
        return digitos, None
    if len(digitos) in (12, 13):
        # Tem cara de número internacional; passa como está em vez de forçar o
        # DDI brasileiro num número que claramente não é do Brasil.
        return digitos, None
    if len(digitos) < 10:
        return None, f"número curto demais ({len(digitos)} dígitos)"
    return None, f"número não reconhecido ({len(digitos)} dígitos)"


def variantes_br(numero: str) -> list[str]:
    """As duas grafias possíveis de um celular brasileiro, para **procurar**.

    O WhatsApp identifica cada pessoa por um `wa_id`, e no Brasil ele nem
    sempre tem o nono dígito: números registrados antes de 2012 fora do
    Sudeste continuam com oito dígitos no aplicativo, embora se disque com
    nove. Então o mesmo contato pode estar cadastrado aqui como
    `5585999998888` (planilha, cadastro manual) e chegar do webhook como
    `558599998888`, e sem cotejar as duas formas ele viraria dois contatos,
    com duas conversas e dois históricos.

    Isto é para consulta, **nunca** para escolher o número de envio: continua
    valendo que não se inventa dígito (ver `normaliza_numero`). Quando existe
    um `wa_id` observado numa mensagem recebida, é ele que vale no envio,
    porque é o identificador que a própria Meta usou.

    Só mexe em celular brasileiro (DDI 55 + DDD + 8 ou 9 dígitos começando
    por 9). Fixo e número estrangeiro voltam sozinhos, sem variante.
    """
    digitos = re.sub(r"\D", "", numero or "")
    if not digitos.startswith(DDI_PADRAO):
        return [digitos] if digitos else []
    resto = digitos[len(DDI_PADRAO):]
    if len(resto) == 11 and resto[2] == "9":
        return [digitos, DDI_PADRAO + resto[:2] + resto[3:]]
    if len(resto) == 10 and resto[2] in "6789":
        # Oito dígitos começando em 6-9 é celular antigo; 2-5 é fixo, que
        # nunca ganhou nono dígito e não pode ganhar um aqui.
        return [digitos, DDI_PADRAO + resto[:2] + "9" + resto[2:]]
    return [digitos]


def detecta_delimitador(amostra: str) -> str:
    """`;` é o que o Excel em português gera; `,` é o padrão de todo o resto."""
    try:
        return csv.Sniffer().sniff(amostra, delimiters=";,\t|").delimiter
    except csv.Error:
        # Empate resolvido pela contagem: Sniffer falha em arquivo de uma
        # coluna só, e aí o delimitador não importa mesmo.
        return ";" if amostra.count(";") > amostra.count(",") else ","


def decodifica(dados: bytes) -> str:
    """Texto do arquivo, tolerando o que sai de planilha no Windows.

    UTF-8 é tentado primeiro (com BOM, que o Excel adiciona); latin-1 é o
    fallback porque nunca falha — e um acento errado num nome é muito melhor do
    que recusar a importação inteira.
    """
    texto = None
    for codificacao in ("utf-8-sig", "utf-8", "cp1252", "latin-1"):
        try:
            texto = dados.decode(codificacao)
            break
        except UnicodeDecodeError:
            continue
    if texto is None:
        texto = dados.decode("latin-1", errors="replace")
    # `utf-8-sig` remove um BOM; algumas ferramentas gravam dois, e o resto
    # ficaria colado no nome da primeira coluna.
    return texto.lstrip("﻿")


def sugere_mapeamento(colunas: list[str], campos_defs: list[dict]) -> dict[str, str]:
    """{coluna_do_arquivo: alvo}. Alvo `campo:<id>` é campo personalizado.

    Coluna não reconhecida fica de fora (alvo vazio) em vez de ser adivinhada:
    a tela mostra o mapeamento para conferência, e é lá que se resolve.
    """
    por_sinonimo = {
        normaliza_texto(sin): alvo
        for alvo, sinonimos in CAMPOS_PADRAO.items()
        for sin in sinonimos
    }
    por_campo = {normaliza_texto(d["nome"]): f"campo:{d['id']}" for d in campos_defs}

    mapa: dict[str, str] = {}
    usados: set[str] = set()
    for coluna in colunas:
        chave = normaliza_texto(coluna)
        alvo = por_sinonimo.get(chave) or por_campo.get(chave)
        # Uma coluna por alvo: uma planilha com "Telefone" e "Celular" mandaria
        # as duas para `numero_whatsapp` e a segunda sobrescreveria a primeira.
        if alvo and alvo not in usados:
            mapa[coluna] = alvo
            usados.add(alvo)
        else:
            mapa[coluna] = ""
    return mapa


def le_arquivo(dados: bytes) -> tuple[list[str], list[dict], str]:
    """Devolve (colunas, linhas, delimitador). Linhas vêm como dicionários."""
    texto = decodifica(dados)
    delimitador = detecta_delimitador(texto[:8192])
    leitor = csv.DictReader(io.StringIO(texto), delimiter=delimitador)
    colunas = [c for c in (leitor.fieldnames or []) if c and c.strip()]
    if not colunas:
        raise ValueError("O arquivo não tem cabeçalho de colunas")
    linhas = []
    for linha in leitor:
        linhas.append(linha)
        if len(linhas) > MAX_LINHAS:
            break
    return colunas, linhas, delimitador


def separa_tags(valor: str) -> list[str]:
    """Etiquetas numa célula: aceita vírgula, ponto e vírgula ou barra."""
    partes = re.split(r"[,;/|]", valor or "")
    vistas, saida = set(), []
    for p in partes:
        limpa = p.strip()
        if limpa and limpa.lower() not in vistas:
            vistas.add(limpa.lower())
            saida.append(limpa)
    return saida[:20]


def linha_para_contato(linha: dict, mapeamento: dict[str, str],
                       ddi: str = DDI_PADRAO) -> tuple[dict, str | None]:
    """Traduz uma linha do arquivo para os campos do contato.

    Devolve (dados, erro). Sem número válido não há contato: ele é a chave de
    tudo, então a linha é recusada em vez de criar um registro que nunca
    poderia receber uma mensagem.
    """
    dados: dict = {"campos": {}}
    numero_bruto = ""
    for coluna, alvo in (mapeamento or {}).items():
        if not alvo:
            continue
        valor = (linha.get(coluna) or "").strip()
        if not valor:
            continue
        if alvo == "numero_whatsapp":
            numero_bruto = valor
        elif alvo == "tags":
            dados["tags"] = separa_tags(valor)
        elif alvo.startswith("campo:"):
            dados["campos"][alvo.split(":", 1)[1]] = valor
        else:
            dados[alvo] = valor

    numero, erro = normaliza_numero(numero_bruto, ddi)
    if erro:
        return {}, erro
    dados["numero_whatsapp"] = numero
    if not dados.get("nome"):
        # Contato sem nome fica com o próprio número, como já acontece com quem
        # chega por mensagem sem `pushName`.
        dados["nome"] = numero
    return dados, None
