#!/usr/bin/env bash
# Установка/обновление KURILKA SIGARKI на Ubuntu/Debian как systemd-сервис.
# Идемпотентно: повторный запуск ничего не ломает (обновляет код/зависимости, .env не трогает).
#
#   sudo bash deploy/install.sh [--with-playwright] [--with-backup-cron] [--no-start]
#
# Код берётся из папки, где лежит этот скрипт (git clone). Если клон не в /opt/kurilka —
# код копируется туда (без venv, bot/.env, *.db, backups/).
set -euo pipefail

APP_DIR="${APP_DIR:-/opt/kurilka}"
APP_USER="${APP_USER:-kurilka}"
SERVICE="kurilka"
SRC_DIR="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"

WITH_PLAYWRIGHT=0
WITH_CRON=0
START=1
for arg in "$@"; do
	case "$arg" in
	--with-playwright) WITH_PLAYWRIGHT=1 ;;
	--with-backup-cron) WITH_CRON=1 ;;
	--no-start) START=0 ;;
	-h | --help)
		sed -n '2,9p' "$0"
		exit 0
		;;
	*)
		echo "Неизвестный флаг: $arg" >&2
		exit 2
		;;
	esac
done

say() { printf '\033[1;32m==>\033[0m %s\n' "$*"; }
warn() { printf '\033[1;33m!!\033[0m %s\n' "$*" >&2; }
# чистое окружение для pip/venv: чужой PYTHONPATH (например /root) ломает pip под другим юзером
as_app() { (cd "$APP_DIR" && runuser -u "$APP_USER" -- env -u PYTHONPATH HOME="$APP_DIR" "$@"); }

if [[ $EUID -ne 0 ]]; then
	echo "Запусти от root: sudo bash $0 $*" >&2
	exit 1
fi
if ! command -v apt-get >/dev/null; then
	echo "Скрипт для Ubuntu/Debian (нужен apt-get). Для SteamOS/домашнего ПК — см. docs/DEPLOY.md" >&2
	exit 1
fi

say "Системные пакеты"
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq python3 python3-venv python3-pip sqlite3 rsync git ca-certificates \
	tesseract-ocr tesseract-ocr-rus >/dev/null  # tesseract — последний шаг OCR-каскада

say "Пользователь $APP_USER"
if ! id "$APP_USER" >/dev/null 2>&1; then
	useradd --system --home-dir "$APP_DIR" --no-create-home --shell /usr/sbin/nologin "$APP_USER"
fi
mkdir -p "$APP_DIR"

if [[ "$SRC_DIR" != "$(realpath "$APP_DIR")" ]]; then
	say "Копирую код $SRC_DIR → $APP_DIR"
	# БД и секреты не перетираем: при первом деплое перенеси logovo.db руками, если нужна
	rsync -a --delete \
		--exclude '/.git' --exclude '/venv' --exclude '/bot/.env' --exclude '*.db' \
		--exclude '*.db-wal' --exclude '*.db-shm' --exclude '/backups' --exclude '/data' --exclude '/.cache' \
		--exclude '__pycache__' "$SRC_DIR"/ "$APP_DIR"/
fi
mkdir -p "$APP_DIR/backups" "$APP_DIR/data"
chown -R "$APP_USER:$APP_USER" "$APP_DIR"

say "venv + зависимости"
if [[ ! -x "$APP_DIR/venv/bin/python" ]]; then
	as_app python3 -m venv "$APP_DIR/venv"
fi
as_app "$APP_DIR/venv/bin/pip" install -q --upgrade pip
as_app "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/bot/requirements.txt"

if [[ $WITH_PLAYWRIGHT -eq 1 ]]; then
	say "Playwright + Chromium (для /scan по ссылке)"
	as_app "$APP_DIR/venv/bin/pip" install -q -r "$APP_DIR/bot/requirements-optional.txt"
	"$APP_DIR/venv/bin/playwright" install-deps chromium
	as_app env PLAYWRIGHT_BROWSERS_PATH="$APP_DIR/.cache/ms-playwright" \
		"$APP_DIR/venv/bin/playwright" install chromium
fi

ENV_FILE="$APP_DIR/bot/.env"
if [[ ! -f "$ENV_FILE" ]]; then
	say "Создаю bot/.env из .env.example — заполни BOT_TOKEN и ADMIN_IDS"
	cp "$APP_DIR/bot/.env.example" "$ENV_FILE"
	# живая БД — вне git (logovo.db в репо), иначе git pull конфликтует с рабочей базой
	printf '\n# рабочая база сервера (вне git)\nDB_PATH=data/logovo.db\n' >>"$ENV_FILE"
fi
chown "$APP_USER:$APP_USER" "$ENV_FILE"
chmod 600 "$ENV_FILE"

say "systemd-юнит"
sed "s#/opt/kurilka#$APP_DIR#g; s#^User=kurilka#User=$APP_USER#; s#^Group=kurilka#Group=$APP_USER#" \
	"$APP_DIR/deploy/kurilka.service" >"/etc/systemd/system/$SERVICE.service"
systemctl daemon-reload
systemctl enable "$SERVICE" >/dev/null

if [[ $WITH_CRON -eq 1 ]]; then
	say "cron: бэкап БД каждые 6 ч → /etc/cron.d/kurilka-backup"
	echo "15 */6 * * * $APP_USER $APP_DIR/deploy/backup.sh >>$APP_DIR/backups/backup.log 2>&1" \
		>/etc/cron.d/kurilka-backup
	chmod 644 /etc/cron.d/kurilka-backup
fi

if ! grep -qE '^BOT_TOKEN=.+' "$ENV_FILE"; then
	warn "BOT_TOKEN пуст. Впиши его: sudo -u $APP_USER nano $ENV_FILE"
	warn "потом: sudo systemctl restart $SERVICE"
elif [[ $START -eq 1 ]]; then
	say "Перезапуск сервиса"
	systemctl restart "$SERVICE"
	sleep 3
	systemctl --no-pager --lines=5 status "$SERVICE" || true
fi

say "Готово. Логи: journalctl -u $SERVICE -f"
