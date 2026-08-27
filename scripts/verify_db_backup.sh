#!/usr/bin/env bash
#
# Database automatic backup and restore verification drill — Issue #527.
#
# Verifies that a `pg_dump` backup can be restored into a fresh database and
# that row counts match between the source and the restored database.
#
# Usage:
#   DATABASE_URL=postgresql://user:pass@host:5432/nociq ./scripts/verify_db_backup.sh
#
# Environment:
#   DATABASE_URL       Source database connection string (required).
#   RESTORE_DB_NAME    Name of the temporary restore database (default: nociq_backup_drill).
#   BACKUP_TABLES      Space-separated table names to compare row counts for
#                      (default: outages sla_results payment_transactions).
#   BACKUP_DIR         Directory for the temporary dump file (default: /tmp).
set -euo pipefail

log() { echo "[verify_db_backup] $(date -u +%Y-%m-%dT%H:%M:%SZ) $*"; }
fail() { log "ERROR: $*"; exit 1; }

: "${RESTORE_DB_NAME:=nociq_backup_drill}"
: "${BACKUP_TABLES:=outages sla_results payment_transactions}"
: "${BACKUP_DIR:=/tmp}"

DATABASE_URL="${DATABASE_URL:-}"
if [[ -z "$DATABASE_URL" ]]; then
    fail "DATABASE_URL is required (e.g. postgresql://user:pass@host:5432/nociq)"
fi

for tool in pg_dump pg_restore psql createdb dropdb; do
    command -v "$tool" >/dev/null 2>&1 || fail "Required tool '$tool' not found on PATH"
done

# Parse DATABASE_URL (postgresql://user:pass@host:port/dbname) into components.
parse_db_url() {
    local url="$1" rest user pass host port db
    rest="${url#*://}"
    user="${rest%%:*}"
    rest="${rest#*:}"
    pass="${rest%%@*}"
    rest="${rest#*@}"
    host="${rest%%/*}"
    rest="${rest#*/}"
    db="${rest%%\?*}"
    SOURCE_DB_USER="$user"
    SOURCE_DB_PASS="$pass"
    SOURCE_DB_HOST="${host%%:*}"
    SOURCE_DB_PORT="${host##*:}"
    if [[ "$SOURCE_DB_PORT" == "$SOURCE_DB_HOST" ]]; then
        SOURCE_DB_PORT="5432"
    fi
    SOURCE_DB_NAME="$db"
}

parse_db_url "$DATABASE_URL"

export PGPASSWORD="$SOURCE_DB_PASS"
PSQL_OPTS=(-h "$SOURCE_DB_HOST" -p "$SOURCE_DB_PORT" -U "$SOURCE_DB_USER")

DUMP_FILE="$BACKUP_DIR/nociq_backup_drill_$(date +%s).dump"
RESTORE_OK=1
cleanup() {
    rm -f "$DUMP_FILE"
    dropdb "${PSQL_OPTS[@]}" --if-exists "$RESTORE_DB_NAME" >/dev/null 2>&1 || true
}
trap cleanup EXIT

log "Backing up database '$SOURCE_DB_NAME' from $SOURCE_DB_HOST:$SOURCE_DB_PORT ..."
pg_dump "${PSQL_OPTS[@]}" -Fc "$SOURCE_DB_NAME" -f "$DUMP_FILE" \
    || fail "pg_dump failed for database '$SOURCE_DB_NAME'"
log "Backup written to $DUMP_FILE"

log "Creating temporary restore database '$RESTORE_DB_NAME' ..."
createdb "${PSQL_OPTS[@]}" "$RESTORE_DB_NAME" || fail "createdb failed for '$RESTORE_DB_NAME'"

log "Restoring backup into '$RESTORE_DB_NAME' ..."
pg_restore "${PSQL_OPTS[@]}" -d "$RESTORE_DB_NAME" --no-owner --no-privileges "$DUMP_FILE" \
    || fail "pg_restore failed into '$RESTORE_DB_NAME'"

count_rows() {
    psql "${PSQL_OPTS[@]}" -d "$1" -tAc "SELECT count(*) FROM $2"
}

log "Comparing row counts (tables: $BACKUP_TABLES) ..."
ALL_MATCH=1
for table in $BACKUP_TABLES; do
    src_count=$(count_rows "$SOURCE_DB_NAME" "$table" 2>/dev/null || echo "n/a")
    rst_count=$(count_rows "$RESTORE_DB_NAME" "$table" 2>/dev/null || echo "n/a")
    if [[ "$src_count" == "$rst_count" ]]; then
        log "OK   $table: $src_count rows (source) == $rst_count rows (restore)"
    else
        log "FAIL $table: $src_count rows (source) != $rst_count rows (restore)"
        ALL_MATCH=0
    fi
done

if [[ "$ALL_MATCH" == "1" ]]; then
    log "Backup verification drill PASSED: restore integrity confirmed."
    exit 0
fi
fail "Backup verification drill FAILED: row counts do not match after restore."
