"""Documentos que a empresa anexa no treino do agente: PDF, Word e texto.

O agente é de texto, então o que entra dele é o **texto** do arquivo, extraído
na hora do upload. O arquivo em si não é guardado: ninguém vai baixá-lo de
volta, e o que o agente usa é o que foi lido dele.

Decisões:

  - **O texto vai no prompt, junto da base de conhecimento**, e não numa
    ferramenta de busca como o estoque. Documento é prosa (política de troca,
    condições de financiamento, apresentação da empresa), e prosa se responde
    lendo, não casando palavras: "posso pagar no cartão?" não tem nenhuma
    palavra em comum com "aceitamos crédito em até 12x". Lista de produtos,
    que se responde casando palavras, é o estoque.

  - **Por isso há teto de caracteres**, somado entre todos os documentos do
    agente (`MAX_TOTAL`): o texto vai em toda resposta. Documento que estoura é
    recusado dizendo o tamanho, em vez de cortado: um corte calado deixaria o
    agente sem a metade do que a empresa achou que tinha ensinado.

  - **Word é lido sem dependência**: o .docx é um zip com o texto em
    `word/document.xml`. O .doc antigo (binário) e o PDF escaneado (imagem, sem
    camada de texto) são recusados dizendo o que fazer.

  - Embutido no agente (`documentos`), como fotos e treino: é lido em toda
    mensagem, e o teto o mantém pequeno.
"""
import io
import re
import uuid
import zipfile
import xml.etree.ElementTree as ET
from datetime import datetime, timezone

import contatos_csv

# Somado entre todos os documentos de um agente. ~15 mil tokens, o que um
# modelo barato lê sem esforço e o cache de prompt absorve.
MAX_TOTAL = 60000
EXTENSOES = (".pdf", ".docx", ".txt", ".md")


class DocumentoError(ValueError):
    pass


def _now():
    return datetime.now(timezone.utc).isoformat()


def _limpa(texto: str) -> str:
    texto = texto.replace("\r", "")
    texto = re.sub(r"[ \t ]+", " ", texto)
    texto = re.sub(r" *\n *", "\n", texto)
    return re.sub(r"\n{3,}", "\n\n", texto).strip()


def _pdf(dados: bytes) -> str:
    from pypdf import PdfReader  # preguiçoso: só quem sobe PDF paga o import
    try:
        leitor = PdfReader(io.BytesIO(dados))
        if leitor.is_encrypted:
            try:
                leitor.decrypt("")
            except Exception:
                raise DocumentoError("O PDF está protegido por senha. Salve uma cópia sem senha e envie de novo.")
        return "\n\n".join((p.extract_text() or "") for p in leitor.pages)
    except DocumentoError:
        raise
    except Exception:
        raise DocumentoError("Não foi possível ler este PDF. Ele pode estar corrompido.")


_W = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _docx(dados: bytes) -> str:
    try:
        with zipfile.ZipFile(io.BytesIO(dados)) as z:
            xml = z.read("word/document.xml")
    except (zipfile.BadZipFile, KeyError):
        raise DocumentoError("Não foi possível ler este arquivo do Word. Ele pode estar corrompido.")
    raiz = ET.fromstring(xml)
    paragrafos = []
    for p in raiz.iter(_W + "p"):
        partes = []
        for no in p.iter():
            if no.tag == _W + "t" and no.text:
                partes.append(no.text)
            elif no.tag == _W + "tab":
                partes.append("\t")
            elif no.tag in (_W + "br", _W + "cr"):
                partes.append("\n")
        paragrafos.append("".join(partes))
    return "\n".join(paragrafos)


def extrair(filename: str, dados: bytes) -> str:
    nome = (filename or "").lower()
    if nome.endswith(".doc"):
        raise DocumentoError("Arquivo .doc antigo não é lido. No Word: Arquivo > Salvar como > Documento do Word (.docx).")
    if nome.endswith(".pdf"):
        texto = _pdf(dados)
    elif nome.endswith(".docx"):
        texto = _docx(dados)
    elif nome.endswith((".txt", ".md")):
        texto = contatos_csv.decodifica(dados)
    else:
        raise DocumentoError("Formato de documento não suportado. Envie PDF, Word (.docx) ou texto (.txt).")
    texto = _limpa(texto)
    if not texto:
        if nome.endswith(".pdf"):
            raise DocumentoError(
                "Este PDF não tem texto para ler: parece ser uma imagem escaneada. "
                "Envie a versão original do documento, ou cole o texto numa mensagem do treino.")
        raise DocumentoError("O documento está vazio.")
    return texto


def novo(filename: str, texto: str, existentes: list[dict]) -> dict:
    usado = sum(d.get("caracteres", 0) for d in existentes)
    if usado + len(texto) > MAX_TOTAL:
        livre = max(0, MAX_TOTAL - usado)
        raise DocumentoError(
            f"Este documento tem {len(texto):,} caracteres e o assistente de IA tem espaço para mais "
            f"{livre:,} (o limite somado dos documentos é {MAX_TOTAL:,}, porque todo esse "
            "texto vai em cada resposta). Envie só a parte que importa para o atendimento; "
            "lista de produtos e preços vai melhor como planilha de estoque."
            .replace(",", "."))
    return {
        "id": str(uuid.uuid4()),
        "nome": filename or "documento",
        "texto": texto,
        "caracteres": len(texto),
        "created_at": _now(),
    }


def publico(doc: dict) -> dict:
    """O documento sem o texto inteiro: a lista de agentes não precisa de 60 mil
    caracteres por agente para desenhar uma bolha."""
    texto = doc.get("texto") or ""
    return {k: v for k, v in doc.items() if k != "texto"} | {
        "previa": texto[:280] + ("…" if len(texto) > 280 else "")}


def bloco(agente: dict) -> str:
    docs = [d for d in (agente.get("documentos") or []) if (d.get("texto") or "").strip()]
    return "\n\n".join(f"Documento \"{d['nome']}\":\n{d['texto'].strip()}" for d in docs)
