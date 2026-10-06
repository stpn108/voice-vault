#!/bin/sh
# Dumps the database into /backups and prunes dumps older than BACKUP_RETENTION_DAYS.
# Started by Ofelia (callisto-services) as a job-exec inside the running db container;
# the job is defined by the labels on the db service in docker-compose.yml.
# The dump goes to a temporary file first, so a failing pg_dump leaves no
# truncated backup behind and the job exits non-zero.
set -eu

STAMP=$(date +%Y-%m-%d-%H-%M-%S)
TARGET="/backups/backup_${STAMP}.sql.gz"
TMP="${TARGET}.tmp"
trap 'rm -f "$TMP"' EXIT

pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$TMP"
gzip -c "$TMP" > "$TARGET"
echo "$(date -Iseconds) backup written: $TARGET ($(du -h "$TARGET" | cut -f1))"

find /backups -name 'backup_*.sql.gz' -mtime "+${BACKUP_RETENTION_DAYS:-7}" -delete
