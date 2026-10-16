#!/usr/bin/env bash
# Backup diário do 4turismo: banco, sessões do WhatsApp e mídia.
#
# Roda no servidor, de dentro de infra/ (onde estão o compose.yaml e o .env).
# Agendar no cron do root, por exemplo às 3h:
#   0 3 * * * /opt/4turismo/infra/backup.sh >> /var/log/4turismo-backup.log 2>&1
#
# Backup que fica só no mesmo disco não protege de perder o servidor. Com
# BACKUP_REMOTO definido (um destino do rclone, ex.: "b2:4turismo-backup"),
# cada cópia também sobe para fora. Configure o rclone uma vez com `rclone config`.
#
# Restaurar: ver infra/DEPLOY.md, "Restaurar um backup". Um backup que nunca foi
# restaurado é uma suposição, não um backup.
set -euo pipefail

cd "$(dirname "$0")"
set -a
# shellcheck disable=SC1091
. ./.env
set +a

RAIZ="${BACKUP_DIR:-/var/backups/4turismo}"
DIAS="${BACKUP_DIAS:-14}"
DESTINO="$RAIZ/$(date +%Y-%m-%d_%H%M)"
mkdir -p "$DESTINO"
chmod 700 "$RAIZ"

echo "[$(date -Is)] backup em $DESTINO"

# Mongo: dump consistente com o servidor no ar.
docker compose exec -T mongo mongodump \
    --username turismo --password "$MONGO_SENHA" --authenticationDatabase admin \
    --db turismo --archive --gzip > "$DESTINO/mongo.archive.gz"

# Ponte do WhatsApp: as sessões. Sem elas, todo número lê o QR de novo.
docker compose exec -T wuzapi-db pg_dump -U wuzapi wuzapi | gzip > "$DESTINO/wuzapi.sql.gz"

# Mídia (fotos do site, anexos das conversas), direto do volume.
docker run --rm -v turismo_media:/dados:ro -v "$DESTINO":/saida alpine:3.20 \
    tar czf /saida/media.tar.gz -C /dados .

# Sem o .env, o dump do banco não serve: as credenciais estão cifradas com a
# CREDENTIALS_KEY que mora nele.
cp .env "$DESTINO/env"
chmod 600 "$DESTINO"/*

# Confere que nada saiu vazio: um dump de 0 bytes "dá certo" e não restaura nada.
for f in mongo.archive.gz wuzapi.sql.gz media.tar.gz; do
    if [ ! -s "$DESTINO/$f" ]; then
        echo "[$(date -Is)] ERRO: $f vazio" >&2
        exit 1
    fi
done

if [ -n "${BACKUP_REMOTO:-}" ]; then
    rclone copy "$DESTINO" "$BACKUP_REMOTO/$(basename "$DESTINO")"
    echo "[$(date -Is)] enviado para $BACKUP_REMOTO"
fi

find "$RAIZ" -mindepth 1 -maxdepth 1 -type d -mtime +"$DIAS" -exec rm -rf {} +
echo "[$(date -Is)] ok: $(du -sh "$DESTINO" | cut -f1)"
