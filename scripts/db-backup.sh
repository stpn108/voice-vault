#!/bin/sh
# Dumps the database into /backups and prunes dumps older than BACKUP_RETENTION_DAYS.
# Started by Ofelia (callisto-services) as a job-exec inside the running db container;
# the job is defined by the labels on the db service in docker-compose.yml.
# The dump goes to a temporary file first, so a failing pg_dump leaves no
# truncated backup behind and the job exits non-zero.
set -eu
umask 077  # the dumps hold private transcripts and the Plaud token pair: readable for the owner only

RETENTION="${BACKUP_RETENTION_DAYS:-7}"
case "$RETENTION" in
    ''|*[!0-9]*|0) echo "BACKUP_RETENTION_DAYS must be a whole number of at least 1, got '$RETENTION'" >&2; exit 1 ;;
esac

STAMP=$(date +%Y-%m-%d-%H-%M-%S)
TARGET="/backups/backup_${STAMP}.sql.gz"
TMP="${TARGET}.tmp"
trap 'rm -f "$TMP"' EXIT

pg_dump -U "$POSTGRES_USER" "$POSTGRES_DB" > "$TMP"
gzip -c "$TMP" > "$TARGET"
# A dump that cannot be read back, or is an empty shell, is not a backup: fail before old ones are pruned.
if ! gzip -t "$TARGET" || [ "$(wc -c < "$TARGET")" -lt 200 ]; then
    rm -f "$TARGET"
    echo "backup failed verification and was removed" >&2
    exit 1
fi
echo "$(date -Iseconds) backup written: $TARGET ($(du -h "$TARGET" | cut -f1))"

chmod 600 /backups/backup_*.sql.gz 2>/dev/null || true  # dumps from before the umask change
find /backups -name 'backup_*.sql.gz' -mtime "+${RETENTION}" -delete
