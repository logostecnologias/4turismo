"""Cifra da chave de IA de cada empresa, em repouso.

Por que existe: a chave de API do cliente é a credencial de uma conta que ele
paga. Ela chega pela tela, fica guardada no agente e é lida a cada resposta.
Guardá-la em texto puro no Mongo significaria que um dump do banco (backup,
réplica, acesso de leitura de um terceiro) entrega a conta de todos os clientes
de uma vez.

A implementação mora em `cripto.py`, compartilhada com o token da conta de
WhatsApp do cliente, que tem exatamente o mesmo problema. Este módulo continua
existindo porque é o nome que o resto do código de IA usa, e porque a máscara
de uma chave de provedor é o que a tela de agentes espera ver.

Duas regras que não podem ser afrouxadas (ver `cripto`): a chave em claro nunca
volta para o frontend, só `mascara()`; e `chave_versao` é gravado junto do
texto cifrado, para uma troca de chave mestra virar "reconecte a conta" em vez
de "credencial inválida" sem explicação.
"""
from cripto import (  # noqa: F401  (reexport proposital)
    CHAVE_VERSAO,
    CredencialError,
    cifrar,
    decifrar,
    mascara,
)
