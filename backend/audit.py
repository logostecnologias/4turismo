"""Trilha de auditoria das operações destrutivas e de exportação (ZZ-21).

Antes, só a impersonation do Super Admin deixava rastro. Apagar a base inteira
de contatos, excluir uma empresa, mesclar fichas, exportar todos os contatos,
resetar a senha de um atendente — nada gerava registro. Depois de um incidente
não havia como responder o que saiu, quando, nem por quem.

`registrar` **nunca levanta**: perder uma linha de auditoria é ruim, travar a
operação de negócio porque o log falhou é pior — o mesmo princípio de
`deals.registrar_evento` e de `ai_runtime.registrar_uso`. Grava em `audit_log`
com ator, ação, empresa-alvo, alvo específico e um resumo livre.
"""
import logging
import uuid
from datetime import datetime, timezone

logger = logging.getLogger("turismo.audit")


async def registrar(db, *, ator: dict | None, acao: str,
                    tenant_id: str | None = None, alvo: str | None = None,
                    meta: dict | None = None):
    ator = ator or {}
    try:
        await db.audit_log.insert_one({
            "id": str(uuid.uuid4()),
            "ator_id": ator.get("id"),
            "ator_email": ator.get("email"),
            "ator_role": ator.get("role"),
            # Em modo suporte o ator é o Super Admin: registrar isso é o ponto.
            "impersonation_id": ator.get("impersonation_id"),
            "acao": acao,
            "tenant_id": tenant_id,
            "alvo": alvo,
            "meta": meta or {},
            "em": datetime.now(timezone.utc).isoformat(),
        })
    except Exception:
        logger.exception("Falha ao gravar auditoria da ação %s", acao)
