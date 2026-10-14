"""Suporte comum aos testes.

Os testes rodam contra um Mongo **falso** (`fakedb.py`), não contra o banco de
verdade. Isso é deliberado: o Mongo do projeto está atrás de um túnel SSH, e um
teste que só passa com o túnel de pé não roda em nenhuma máquina nova. O que o
falso cobre é a lógica — filtros, agregações, transições de estado, permissão.
O que ele **não** cobre é o comportamento do Mongo real: índices aceitos, tipos
e o resultado exato de uma agregação. Isso continua exigindo um teste de fumaça
com o banco no ar.

Cada módulo de teste cria o seu próprio `FakeDB` e substitui `db.get_db`. As
etapas dentro de um módulo compartilham esse estado e correm em ordem — o
`--dist loadscope` do pytest.ini fixa o módulo num worker só, que é o que
preserva a ordem sob paralelismo.
"""
import os
import sys

# O backend é o pai desta pasta. Resolvido pelo caminho do arquivo para o
# pytest funcionar rodando da raiz do backend ou de dentro de tests/.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

# Os módulos do backend leem estas variáveis no import. Valores de fachada: a
# conexão nunca é aberta, porque get_db é substituído antes do uso.
for _chave, _valor in {
    "MONGO_URL": "mongodb://fake/",
    "DB_NAME": "fake",
    "JWT_SECRET": "teste",
    "META_APP_SECRET": "segredo-de-teste",
    # Token de plataforma: faz `whatsapp_client.credencial()` devolver uma
    # credencial usável para qualquer conexão de teste que tenha
    # `phone_number_id`, sem cada fixture precisar cifrar um token próprio.
    "META_PLATFORM_TOKEN": "token-de-teste",
    "META_WEBHOOK_VERIFY_TOKEN": "verifica-teste",
    # Chave de cifra das credenciais (token da Cloud API, chave de IA). Fixa e
    # de fachada: o teste precisa cifrar e decifrar, nunca proteger nada.
    "CREDENTIALS_KEY": "VlKvCmSGH9QsR1bwg10fq1e4oiOZ_0MP3B-YxhSL1oo=",
}.items():
    os.environ.setdefault(_chave, _valor)
