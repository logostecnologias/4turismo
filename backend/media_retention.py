"""Retenção dos arquivos de mídia das conversas.

Mídia é o único dado do sistema que cresce sem teto: cada foto que um cliente
manda fica em disco pra sempre, e nada nunca apagava. Esta varredura aplica
uma janela de retenção e limpa o que passou dela.

A janela vem do plano da empresa (`retencao_midia_dias`), o que permite vender
"guardamos 1 ano" num plano e 30 dias em outro. Plano sem o campo — ou empresa
sem plano — cai no default do servidor (MEDIA_RETENTION_DAYS). `0` em qualquer
um dos dois significa a mesma coisa: nunca apagar.

O documento em `media_files` **não** é apagado junto com o arquivo — só ganha
`purged_at`. É o que permite a bolha antiga continuar mostrando "nome do
arquivo, 2,3 MB — mídia expirada" em vez de virar imagem quebrada. As
mensagens correspondentes recebem `meta.media.expired`, para o chat saber
disso sem precisar tentar baixar cada anexo velho.

Também recolhe arquivo órfão: binário em disco sem documento nenhum
apontando pra ele, resto de upload que morreu no meio. Só depois de
ORPHAN_GRACE_HOURS, para nunca competir com um upload em andamento.
"""
import asyncio
import logging
import os
from datetime import datetime, timezone, timedelta

import media_store
from db import get_db

logger = logging.getLogger("turismo.media_retention")

# Uma varredura por dia dá conta: a janela é medida em dias, então antecipar
# ou atrasar algumas horas não muda nada — e evita I/O de disco à toa.
SWEEP_INTERVAL = 6 * 3600
STARTUP_DELAY = 60          # deixa o boot terminar antes de varrer disco
BATCH = 500
ORPHAN_GRACE_HOURS = 24

_task: asyncio.Task | None = None


def retention_days() -> int:
    """Janela em dias; 0 (ou inválido) desliga a retenção por completo."""
    try:
        return max(0, int(os.environ.get("MEDIA_RETENTION_DAYS", "90")))
    except ValueError:
        logger.warning("MEDIA_RETENTION_DAYS inválido — retenção desligada")
        return 0


async def _tenant_windows(db, tenant_ids: list[str]) -> dict[int, list[str]]:
    """Agrupa as empresas pela janela de retenção do plano de cada uma.

    Agrupado — e não um laço por empresa — de propósito: planos são poucos, e
    empresas do mesmo plano compartilham a mesma janela. Mil empresas em três
    planos viram três consultas de purga, não mil.
    """
    default = retention_days()
    tenants = {
        t["id"]: t.get("plan_id")
        async for t in db.tenants.find({"id": {"$in": tenant_ids}}, {"_id": 0, "id": 1, "plan_id": 1})
    }
    plan_ids = [p for p in set(tenants.values()) if p]
    plans = {
        p["id"]: p.get("retencao_midia_dias")
        async for p in db.plans.find(
            {"id": {"$in": plan_ids}}, {"_id": 0, "id": 1, "retencao_midia_dias": 1}
        )
    }

    groups: dict[int, list[str]] = {}
    for tid in tenant_ids:
        days = plans.get(tenants.get(tid))
        # Cai no default global quando a empresa não tem plano, o plano não
        # define a janela, ou a empresa já foi excluída e a mídia ficou órfã.
        window = default if days is None else max(0, int(days))
        groups.setdefault(window, []).append(tid)
    return groups


async def _purge_expired(db) -> int:
    """Purga respeitando a janela do plano de cada empresa."""
    tenant_ids = await db.media_files.distinct("tenant_id", {"purged_at": None})
    if not tenant_ids:
        return 0
    total = 0
    for days, ids in (await _tenant_windows(db, tenant_ids)).items():
        total += await _purge_window(db, ids, days)
    return total


async def _purge_window(db, tenant_ids: list[str], days: int) -> int:
    if days <= 0:
        # Guarda proposital: 0 quer dizer "guarda pra sempre", mas sem isto
        # um cutoff de zero dia apagaria o acervo inteiro. Um engano de quem
        # chama não pode custar todas as mídias.
        return 0
    cutoff = (datetime.now(timezone.utc) - timedelta(days=days)).isoformat()
    now = datetime.now(timezone.utc).isoformat()
    total = 0

    while True:
        batch = await db.media_files.find(
            {"tenant_id": {"$in": tenant_ids}, "created_at": {"$lt": cutoff},
             "purged_at": None,
             # Avatar fica fora da janela. A retenção existe para o acervo de
             # conversa, que cresce sem parar; avatar é atributo **corrente**
             # da ficha, um arquivo pequeno por contato, substituído e não
             # acumulado. Expirá-lo não libera espaço que importe e troca a
             # foto da ficha por iniciais sem ninguém ter pedido. Quem o apaga
             # é a anonimização (`contatos._apaga_avatar`), que é onde foto de
             # pessoa tem de sair mesmo.
             # Imagem do site (logo, capa, foto de passeio) também fica fora
             # pelo mesmo motivo: é a vitrine corrente da agência, não anexo
             # de conversa (ver `sites.marcar_imagens_do_site`).
             "uso": {"$nin": ["avatar", "site", media_store.USO_CATALOGO_IA]}},
            {"_id": 0},
        ).limit(BATCH).to_list(BATCH)
        if not batch:
            break

        for doc in batch:
            try:
                media_store.file_path(doc).unlink(missing_ok=True)
            except OSError as e:
                # Arquivo travado ou permissão: deixa o doc intacto pra
                # próxima varredura tentar de novo, em vez de marcar como
                # purgado algo que ainda ocupa disco.
                logger.warning("Não consegui apagar %s: %s", doc.get("stored_name"), e)
                continue

            await db.media_files.update_one({"id": doc["id"]}, {"$set": {"purged_at": now}})
            await db.messages_log.update_many(
                {"meta.media.id": doc["id"]}, {"$set": {"meta.media.expired": True}}
            )
            total += 1

        if len(batch) < BATCH:
            break

    return total


async def _sweep_orphans(db) -> int:
    """Binário sem dono. O doc é gravado depois do arquivo, então um upload
    interrompido no meio deixa exatamente isso pra trás."""
    root = media_store.media_root()
    if not root.exists():
        return 0
    grace = datetime.now(timezone.utc) - timedelta(hours=ORPHAN_GRACE_HOURS)
    total = 0

    for tenant_dir in root.iterdir():
        if not tenant_dir.is_dir():
            continue
        candidates = []
        for f in tenant_dir.iterdir():
            try:
                if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime, timezone.utc) < grace:
                    candidates.append(f)
            except OSError:
                continue
        if not candidates:
            continue

        for i in range(0, len(candidates), BATCH):
            chunk = candidates[i:i + BATCH]
            known = {
                d["stored_name"]
                async for d in db.media_files.find(
                    {"tenant_id": tenant_dir.name, "stored_name": {"$in": [f.name for f in chunk]}},
                    {"_id": 0, "stored_name": 1},
                )
            }
            for f in chunk:
                if f.name in known:
                    continue
                try:
                    f.unlink(missing_ok=True)
                    total += 1
                except OSError as e:
                    logger.warning("Não consegui apagar órfão %s: %s", f, e)

        total += await _variantes_orfas(db, tenant_dir, grace)

    return total


async def _variantes_orfas(db, tenant_dir, grace) -> int:
    """Versões leves do site (`imagens_site`) cujo documento não existe mais.

    Moram numa subpasta própria, que o laço acima não olha (ele só vê arquivo
    solto). O nome é `<id da mídia>.<largura>.<formato>`, e é pelo id que se
    sabe se o original ainda existe.
    """
    pasta = tenant_dir / "_variantes"
    if not pasta.is_dir():
        return 0
    arquivos = []
    for f in pasta.iterdir():
        try:
            if f.is_file() and datetime.fromtimestamp(f.stat().st_mtime, timezone.utc) < grace:
                arquivos.append(f)
        except OSError:
            continue
    total = 0
    for i in range(0, len(arquivos), BATCH):
        chunk = arquivos[i:i + BATCH]
        ids = {f.name.split(".")[0] for f in chunk}
        vivos = {d["id"] async for d in db.media_files.find(
            {"tenant_id": tenant_dir.name, "id": {"$in": list(ids)}, "purged_at": None}, {"_id": 0, "id": 1})}
        for f in chunk:
            if f.name.split(".")[0] in vivos:
                continue
            try:
                f.unlink(missing_ok=True)
                total += 1
            except OSError as e:
                logger.warning("Não consegui apagar variante órfã %s: %s", f, e)
    return total


async def sweep() -> dict:
    db = get_db()
    purged = await _purge_expired(db)
    orphans = await _sweep_orphans(db)
    if purged or orphans:
        logger.info("Retenção de mídia: %d expirados, %d órfãos removidos", purged, orphans)
    return {"purged": purged, "orphans": orphans}


async def _loop():
    days = retention_days()
    logger.info(
        "Media retention started (janela padrão=%s, sobrescrita pelo plano de cada empresa, intervalo=%dh)",
        f"{days}d" if days else "nunca apaga", SWEEP_INTERVAL // 3600,
    )
    await asyncio.sleep(STARTUP_DELAY)
    while True:
        try:
            await sweep()
        except asyncio.CancelledError:
            raise
        except Exception as e:
            logger.exception("Varredura de retenção de mídia falhou: %s", e)
        await asyncio.sleep(SWEEP_INTERVAL)


def start():
    global _task
    if _task is None or _task.done():
        _task = asyncio.create_task(_loop())


def stop():
    global _task
    if _task and not _task.done():
        _task.cancel()
        _task = None
