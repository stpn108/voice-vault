#!/bin/sh
# Dumps the database into /backups and prunes dumps older than
# BACKUP_RETENTION_DAYS. Runs inside the db-backup service (see docker-compose.yml).
set -eu

STAMP=$(date +%Y-%m-%d-%H-%M-%S)
TARGET="/backups/backup_${STAMP}.sql.gz"

pg_dump -h "${PGHOST:-db}" -U "$POSTGRES_USER" "$POSTGRES_DB" | gzip > "$TARGET"
echo "$(date -Iseconds) backup written: $TARGET ($(du -h "$TARGET" | cut -f1))"

find /backups -name 'backup_*.sql.gz' -mtime "+${BACKUP_RETENTION_DAYS:-7}" -delete
