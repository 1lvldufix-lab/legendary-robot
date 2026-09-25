#!/usr/bin/env bash
# Бэкап SQLite-базы (консистентный, через .backup — безопасно при работающем боте).
#   deploy/backup.sh            → backups/logovo-ГГГГММДД-ЧЧММ.db, старше KEEP_DAYS — удаляются
#   KEEP_DAYS=30 deploy/backup.sh
# cron (каждые 6 ч):  15 */6 * * * kurilka /opt/kurilka/deploy/backup.sh >>/opt/kurilka/backups/backup.log 2>&1
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
KEEP_DAYS="${KEEP_DAYS:-14}"
BACKUP_DIR="${BACKUP_DIR:-$ROOT/backups}"

# DB_PATH / DATABASE_URL — из окружения или bot/.env (как у бота)
env_get() {
	local key="$1" line
	[[ -f "$ROOT/bot/.env" ]] || return 0
	line="$(grep -E "^${key}=" "$ROOT/bot/.env" | tail -n 1 || true)"
	line="${line#*=}"
	line="${line%\"}"
	line="${line#\"}"
	printf '%s' "$line"
}
DATABASE_URL="${DATABASE_URL:-$(env_get DATABASE_URL)}"
DB_PATH="${DB_PATH:-$(env_get DB_PATH)}"
DB_PATH="${DB_PATH:-logovo.db}"
[[ "$DB_PATH" = /* ]] || DB_PATH="$ROOT/$DB_PATH"

if [[ -n "$DATABASE_URL" ]]; then
	echo "DATABASE_URL задан (Postgres) — используй pg_dump, этот скрипт только для SQLite." >&2
	exit 1
fi
if [[ ! -f "$DB_PATH" ]]; then
	echo "БД не найдена: $DB_PATH" >&2
	exit 1
fi

mkdir -p "$BACKUP_DIR"
DST="$BACKUP_DIR/logovo-$(date +%Y%m%d-%H%M).db"

if command -v sqlite3 >/dev/null; then
	sqlite3 "$DB_PATH" ".backup '$DST'"
	check="$(sqlite3 "$DST" 'PRAGMA integrity_check;')"
else
	# нет sqlite3 CLI (SteamOS) — тот же онлайн-бэкап через Python
	PY="$ROOT/venv/bin/python"
	[[ -x "$PY" ]] || PY=python3
	check="$("$PY" - "$DB_PATH" "$DST" <<'EOF'
import sqlite3, sys
src, dst = sqlite3.connect(sys.argv[1]), sqlite3.connect(sys.argv[2])
src.backup(dst)
print(dst.execute("PRAGMA integrity_check").fetchone()[0])
dst.close(); src.close()
EOF
)"
fi
if [[ "$check" != "ok" ]]; then
	echo "integrity_check бэкапа: $check" >&2
	exit 1
fi

# ротация: и наши, и ежедневные копии бота (job_daily_backup) — одинаковый префикс
find "$BACKUP_DIR" -maxdepth 1 -name 'logovo-*.db' -type f -mtime +"$KEEP_DAYS" -delete
echo "$(date '+%F %T') бэкап ок: $DST ($(du -h "$DST" | cut -f1))"
