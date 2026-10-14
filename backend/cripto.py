"""Cifra de credencial de terceiro em repouso, para todo o sistema.

Nasceu em `ai_credentials`, quando só a chave de IA precisava disso. Com a
Cloud API o token de acesso da conta de WhatsApp do cliente passou a ser a
segunda credencial guardada no banco, e ela é ainda mais sensível: quem a copia
manda mensagem em nome da empresa para a base inteira de contatos dela. Duas
implementações da mesma cifra divergiriam justamente no ponto em que não se
pode divergir, então a lógica mora aqui e `ai_credentials` delega.

O modelo não mudou: o segredo mestre vive em variável de ambiente, nunca no
banco e nunca no código. Quem tem um dump do Mongo não tem as credenciais dos
clientes; quem tem o `.env` não tem o banco.

Duas regras que não podem ser afrouxadas:

  - **A credencial em claro nunca sai daqui para o frontend.** A tela recebe
    só `mascara()`, o suficiente para o cliente reconhecer qual chave cadastrou
    e nada além disso. Devolvê-la "para preencher o campo de edição" a faria
    trafegar a cada abertura de tela.

  - **`chave_versao` é gravado junto do texto cifrado.** Trocando a chave
    mestra, é preciso saber quais registros são antigos para pedir ao cliente
    que reconecte, em vez de o envio falhar em silêncio com "credencial
    inválida" e o suporte perseguir o problema errado.

A chave é lida de `CREDENTIALS_KEY`, com `AI_CREDENTIALS_KEY` como alternativa
para não quebrar instalação existente: as credenciais de IA já gravadas foram
cifradas com ela, e mudar o nome da variável as tornaria ilegíveis de uma vez.
"""
import os

from cryptography.fernet import Fernet, InvalidToken, MultiFernet

# Versão da chave mestra em uso. Subir este número (e trocar a env) é o que
# marca as credenciais antigas como irrecuperáveis, de propósito.
CHAVE_VERSAO = 1

_PRIMARIA = ("CREDENTIALS_KEY", "AI_CREDENTIALS_KEY")
_ANTERIORES = ("CREDENTIALS_KEY_PREV", "AI_CREDENTIALS_KEY_PREV")


class CredencialError(Exception):
    """Falha ao cifrar ou decifrar. Nunca carrega o segredo na mensagem."""


def _primeira_env(nomes) -> str:
    for nome in nomes:
        valor = (os.environ.get(nome) or "").strip()
        if valor:
            return valor
    return ""


def _fernet() -> MultiFernet:
    """Lido sob demanda, não no import: o `.env` é carregado pelo `server.py`
    depois que este módulo já pode ter sido importado por um teste.

    Devolve um `MultiFernet`: cifra sempre com a chave primária e decifra
    tentando a primária e depois as anteriores (`*_PREV`, uma ou mais
    separadas por vírgula). É o que permite trocar a chave mestra sem quebrar
    o que já está gravado, promovendo a nova a primária e mantendo a antiga em
    PREV durante a janela de recifragem.
    """
    primaria = _primeira_env(_PRIMARIA)
    if not primaria:
        raise CredencialError(
            "CREDENTIALS_KEY não configurada: sem ela não é possível guardar "
            "credencial de cliente. Gere uma com: "
            "python -c \"from cryptography.fernet import Fernet; "
            "print(Fernet.generate_key().decode())\""
        )
    brutas = [primaria] + [
        k.strip() for k in _primeira_env(_ANTERIORES).split(",") if k.strip()
    ]
    try:
        return MultiFernet([Fernet(k.encode("utf-8")) for k in brutas])
    except Exception as e:
        raise CredencialError(f"CREDENTIALS_KEY inválida: {e}")


def cifrar(segredo: str) -> dict:
    """Devolve o bloco que vai para o documento: cifrado, versão e máscara.

    A máscara é calculada aqui, e não na tela, porque este é o único ponto do
    sistema que enxerga o segredo em claro.
    """
    if not segredo or not segredo.strip():
        raise CredencialError("Credencial vazia")
    segredo = segredo.strip()
    return {
        "cifrada": _fernet().encrypt(segredo.encode("utf-8")).decode("utf-8"),
        "chave_versao": CHAVE_VERSAO,
        "mascara": mascara(segredo),
    }


def decifrar(bloco: dict) -> str:
    """Recupera o segredo para uso imediato numa chamada. O retorno não deve
    ser guardado em lugar nenhum: nem em cache, nem em log, nem em variável de
    módulo."""
    if not bloco or not bloco.get("cifrada"):
        raise CredencialError("Registro sem credencial guardada")
    if bloco.get("chave_versao") != CHAVE_VERSAO:
        raise CredencialError(
            "Credencial cifrada com uma chave mestra anterior: o cliente "
            "precisa cadastrá-la de novo"
        )
    try:
        return _fernet().decrypt(bloco["cifrada"].encode("utf-8")).decode("utf-8")
    except InvalidToken:
        # Acontece quando a chave do ambiente não é a que cifrou. Dizer isso
        # explicitamente evita horas perseguindo um "401 do provedor" que na
        # verdade é a env errada no deploy.
        raise CredencialError(
            "Não foi possível decifrar a credencial: CREDENTIALS_KEY não "
            "corresponde à que gravou este registro"
        )


def mascara(segredo: str) -> str:
    """`sk-ant-…abcd`, `EAAGm0P…7xQz`: prefixo mais os últimos quatro.

    Mostra o bastante para o cliente distinguir duas credenciais dele e nada
    que ajude a reconstruir uma.
    """
    segredo = (segredo or "").strip()
    if len(segredo) <= 8:
        return "•" * len(segredo)
    return f"{segredo[:7]}…{segredo[-4:]}"
