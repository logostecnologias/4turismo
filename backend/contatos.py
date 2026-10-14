"""Exclusão, anonimização e mesclagem de contato.

Vive fora das rotas porque são as três operações destrutivas da base, e cada
uma tem uma decisão que não é óbvia:

**Anonimizar é o padrão, não apagar.** A LGPD pede que o dado pessoal saia; ela
não pede que o registro da transação desapareça. Um negócio fechado de R$ 40
mil e as mensagens de um atendimento são registro do que aconteceu — apagá-los
faria a receita de um mês passado mudar retroativamente, e o relatório que a
empresa mostrou ao contador deixaria de fechar. Então o dado pessoal é removido
e o esqueleto fica: o negócio continua somando, apontando para um contato que
agora se chama "Contato removido".

**O conteúdo das mensagens também é dado pessoal.** O contato digitou endereço,
CPF, foto do documento. Anonimizar sem tocar em `messages_log` deixaria tudo
isso no banco — então o texto é redigido e o arquivo de mídia sai do disco, no
mesmo padrão do `media_retention` (o documento em `media_files` fica com
`purged_at`, para a bolha antiga não virar imagem quebrada).

**Apagar de vez existe para outro caso**: contato de teste, importação errada,
duplicado que não vale mesclar. Aí vai tudo, e a resposta diz o que levou junto.

**Mesclar move, não copia.** Duas fichas do mesmo cliente (dois números) viram
uma: conversas, negócios, atividades e notas passam para a que fica.
"""
import logging
from datetime import datetime, timezone

import media_store

logger = logging.getLogger("turismo.contatos")

# O que aparece na tela onde havia nome. Não é vazio de propósito: uma ficha em
# branco parece defeito, e a pessoa que abrir precisa entender que o dado saiu
# por pedido do titular, não por bug.
NOME_ANONIMO = "Contato removido"
TEXTO_REDIGIDO = "[conteúdo removido a pedido do titular]"

# Campos pessoais da ficha. `numero_whatsapp` entra: é o identificador direto
# da pessoa. Sai substituído por um marcador, e não apagado, porque é ele que
# mantém a unicidade do documento e impede que uma importação futura recrie a
# ficha achando que o número está livre.
# `foto_media_id` é a foto que nós guardamos (canal não oficial);
# `foto_url` é um endereço externo vindo de planilha ou do cadastro. Os
# dois são a cara de uma pessoa e saem juntos — e o arquivo do primeiro é
# apagado do disco por `_apaga_avatar`, senão ficaria servível por
# /api/media depois de a ficha ter sido anonimizada.
CAMPOS_PESSOAIS = ("email", "empresa", "cargo", "origem", "foto_url",
                   "foto_media_id")


def _now():
    return datetime.now(timezone.utc).isoformat()


async def _apaga_avatar(db, contact_id: str) -> int:
    """Tira do disco a foto de perfil guardada para este contato.

    Existe separada de `_apaga_midia` porque aquela varre por `session_id`, e
    avatar não pertence a conversa nenhuma — ficaria em disco, servível por
    /api/media, depois de a ficha ter sido anonimizada. É foto de uma pessoa:
    é exatamente o dado que a anonimização existe para remover.
    """
    total = 0
    agora = _now()
    docs = await db.media_files.find(
        {"contact_id": contact_id, "purged_at": None}, {"_id": 0}
    ).to_list(100)
    for doc in docs:
        try:
            media_store.file_path(doc).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Não consegui apagar o avatar %s: %s", doc.get("stored_name"), e)
            continue
        await db.media_files.update_one({"id": doc["id"]}, {"$set": {"purged_at": agora}})
        total += 1
    return total


async def _apaga_midia(db, session_ids: list[str]) -> int:
    """Tira do disco os arquivos das conversas informadas.

    Mesmo padrão do `media_retention`: o arquivo sai, o documento fica com
    `purged_at` e as mensagens ganham `meta.media.expired` — sem isso a bolha
    antiga tentaria baixar um arquivo que não existe mais.
    """
    if not session_ids:
        return 0
    total = 0
    docs = await db.media_files.find(
        {"session_id": {"$in": session_ids}, "purged_at": None}, {"_id": 0}
    ).to_list(5000)
    agora = _now()
    for doc in docs:
        try:
            media_store.file_path(doc).unlink(missing_ok=True)
        except OSError as e:
            logger.warning("Não consegui apagar mídia %s: %s", doc.get("stored_name"), e)
            continue
        await db.media_files.update_one({"id": doc["id"]}, {"$set": {"purged_at": agora}})
        await db.messages_log.update_many(
            {"meta.media.id": doc["id"]}, {"$set": {"meta.media.expired": True}}
        )
        total += 1
    return total


async def anonimizar(db, tenant_id: str, contact: dict) -> dict:
    """Remove o dado pessoal e preserva o registro do que aconteceu."""
    contact_id = contact["id"]
    agora = _now()

    sessoes = await db.conversation_sessions.find(
        {"tenant_id": tenant_id, "contact_id": contact_id}, {"_id": 0, "id": 1}
    ).to_list(2000)
    session_ids = [s["id"] for s in sessoes]

    midias = await _apaga_midia(db, session_ids)
    midias += await _apaga_avatar(db, contact_id)

    mensagens = 0
    if session_ids:
        # A prévia na sessão espelha a última mensagem, então ela é redigida
        # junto — senão o nome e o texto continuariam visíveis na lista de
        # conversas, que é a primeira tela que alguém abre.
        r = await db.messages_log.update_many(
            {"session_id": {"$in": session_ids}},
            {"$set": {"content": TEXTO_REDIGIDO, "anonimizado_em": agora}},
        )
        mensagens = r.modified_count
        await db.conversation_sessions.update_many(
            {"tenant_id": tenant_id, "contact_id": contact_id},
            {"$set": {"last_message.content": TEXTO_REDIGIDO,
                      "variables.nome_contato": NOME_ANONIMO,
                      "variables.numero": None}},
        )

    notas = await db.contact_notes.delete_many(
        {"tenant_id": tenant_id, "contact_id": contact_id}
    )

    campos = {
        "nome": NOME_ANONIMO,
        # Marcador em vez de vazio: mantém a ficha identificável como "esta era
        # uma pessoa" e evita que uma importação futura recrie o contato
        # pensando que o número está livre.
        "numero_whatsapp": f"anon-{contact_id}",
        "tags": [],
        "campos": {},
        "anonimizado_em": agora,
        "updated_at": agora,
    }
    for chave in CAMPOS_PESSOAIS:
        campos[chave] = None
    await db.contacts.update_one({"id": contact_id, "tenant_id": tenant_id}, {"$set": campos})

    return {
        "modo": "anonimizar",
        "notas_apagadas": notas.deleted_count,
        "mensagens_redigidas": mensagens,
        "midias_apagadas": midias,
        "conversas_preservadas": len(session_ids),
        "negocios_preservados": await db.deals.count_documents(
            {"tenant_id": tenant_id, "contact_id": contact_id}
        ),
    }


async def apagar(db, tenant_id: str, contact: dict) -> dict:
    """Apaga o contato e tudo que era só dele.

    Diferente de anonimizar: aqui os negócios vão embora, e com eles a receita
    que eles representavam nos relatórios. É o modo para contato de teste ou
    importação errada — quem chama precisa ter escolhido isso de propósito.
    """
    contact_id = contact["id"]
    sessoes = await db.conversation_sessions.find(
        {"tenant_id": tenant_id, "contact_id": contact_id}, {"_id": 0, "id": 1}
    ).to_list(2000)
    session_ids = [s["id"] for s in sessoes]

    midias = await _apaga_midia(db, session_ids)
    midias += await _apaga_avatar(db, contact_id)
    if session_ids:
        await db.media_files.delete_many({"session_id": {"$in": session_ids}})
        await db.messages_log.delete_many({"session_id": {"$in": session_ids}})
    # Em `apagar` o documento do avatar sai também. Na anonimização ele fica
    # com `purged_at`, porque o registro da conversa continua existindo; aqui
    # não sobra conversa nenhuma para ele explicar.
    await db.media_files.delete_many({"contact_id": contact_id})
    await db.conversation_sessions.delete_many(
        {"tenant_id": tenant_id, "contact_id": contact_id}
    )

    negocios = await db.deals.find(
        {"tenant_id": tenant_id, "contact_id": contact_id}, {"_id": 0, "id": 1}
    ).to_list(2000)
    if negocios:
        await db.deal_events.delete_many(
            {"tenant_id": tenant_id, "deal_id": {"$in": [d["id"] for d in negocios]}}
        )
    await db.deals.delete_many({"tenant_id": tenant_id, "contact_id": contact_id})

    notas = await db.contact_notes.delete_many({"tenant_id": tenant_id, "contact_id": contact_id})
    atividades = await db.activities.delete_many(
        {"tenant_id": tenant_id, "contact_id": contact_id}
    )
    await db.contacts.delete_one({"id": contact_id, "tenant_id": tenant_id})

    return {
        "modo": "apagar",
        "conversas_apagadas": len(session_ids),
        "negocios_apagados": len(negocios),
        "notas_apagadas": notas.deleted_count,
        "atividades_apagadas": atividades.deleted_count,
        "midias_apagadas": midias,
    }


# ---------- Mesclagem ----------


async def achar_duplicados(db, tenant_id: str, limite: int = 50) -> list[dict]:
    """Grupos de fichas que parecem ser a mesma pessoa.

    O número não entra no critério porque ele já é único por empresa (o cadastro
    recusa duplicado com 409). Duplicata real aparece quando a mesma pessoa
    chegou por dois números — e o que sobra para reconhecê-la é o e-mail ou o
    nome. E-mail primeiro, porque é evidência forte; nome é sugestão, e a tela
    precisa deixar a decisão com quem conhece o cliente.
    """
    grupos: list[dict] = []
    for campo, criterio in (("email", "email"), ("nome", "nome")):
        cursor = db.contacts.aggregate([
            {"$match": {"tenant_id": tenant_id, campo: {"$ne": None},
                        "anonimizado_em": None}},
            {"$group": {"_id": f"${campo}", "n": {"$sum": 1},
                        "ids": {"$push": "$id"}}},
        ])
        async for row in cursor:
            valor = row["_id"]
            if row["n"] < 2 or not valor or not str(valor).strip():
                continue
            grupos.append({"criterio": criterio, "valor": valor, "ids": row["ids"]})
            if len(grupos) >= limite:
                break
        if len(grupos) >= limite:
            break

    # As fichas de cada grupo, para a tela mostrar o que vai ser mesclado sem
    # uma requisição por contato.
    todos = list({i for g in grupos for i in g["ids"]})
    fichas = {
        c["id"]: c
        async for c in db.contacts.find(
            {"id": {"$in": todos}, "tenant_id": tenant_id},
            {"_id": 0, "id": 1, "nome": 1, "numero_whatsapp": 1, "email": 1,
             "empresa": 1, "created_at": 1},
        )
    } if todos else {}
    for g in grupos:
        g["contatos"] = [fichas[i] for i in g["ids"] if i in fichas]
    return [g for g in grupos if len(g["contatos"]) > 1]


async def mesclar(db, tenant_id: str, principal: dict, secundario: dict) -> dict:
    """Move tudo do `secundario` para o `principal` e apaga o secundário.

    O número do secundário é perdido — é o preço de ter uma ficha só, e é por
    isso que a tela deve deixar claro qual das duas fica. Campo vazio no
    principal é preenchido com o do secundário; campo preenchido não é
    sobrescrito, porque quem escolheu o principal escolheu os dados dele.
    """
    destino, origem = principal["id"], secundario["id"]
    if destino == origem:
        raise ValueError("Não é possível mesclar um cliente com ele mesmo")

    movidos = {}
    for colecao in ("conversation_sessions", "deals", "activities", "contact_notes"):
        r = await db[colecao].update_many(
            {"tenant_id": tenant_id, "contact_id": origem},
            {"$set": {"contact_id": destino}},
        )
        movidos[colecao] = r.modified_count

    campos: dict = {"updated_at": _now()}
    for chave in ("email", "empresa", "cargo", "origem", "responsavel_id", "foto_url"):
        if not principal.get(chave) and secundario.get(chave):
            campos[chave] = secundario[chave]
    juntas = list(principal.get("tags") or [])
    for t in (secundario.get("tags") or []):
        if t.lower() not in {x.lower() for x in juntas}:
            juntas.append(t)
    if juntas != (principal.get("tags") or []):
        campos["tags"] = juntas[:20]
    # Campo personalizado só do secundário é aproveitado; conflito fica com o
    # principal, pela mesma razão dos campos fixos.
    unidos = dict(secundario.get("campos") or {})
    unidos.update(principal.get("campos") or {})
    if unidos != (principal.get("campos") or {}):
        campos["campos"] = unidos
    # Opt-out é a recusa mais restritiva das duas: se qualquer uma das fichas
    # pediu para sair, a ficha resultante continua marcada.
    if secundario.get("opt_out") and not principal.get("opt_out"):
        campos["opt_out"] = True
        campos["opt_out_em"] = secundario.get("opt_out_em") or _now()

    await db.contacts.update_one({"id": destino, "tenant_id": tenant_id}, {"$set": campos})
    await db.contacts.delete_one({"id": origem, "tenant_id": tenant_id})

    return {
        "principal": destino,
        "removido": origem,
        "conversas": movidos["conversation_sessions"],
        "negocios": movidos["deals"],
        "atividades": movidos["activities"],
        "notas": movidos["contact_notes"],
        "campos_herdados": sorted(k for k in campos if k != "updated_at"),
    }
