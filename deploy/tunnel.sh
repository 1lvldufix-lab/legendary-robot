#!/usr/bin/env bash
# Быстрый HTTPS для мини-аппа: cloudflared quick tunnel (без аккаунта, домена и открытых портов).
#   deploy/tunnel.sh              — поднять туннель и показать адрес
#   deploy/tunnel.sh --write      — ещё и вписать адрес в bot/.env → WEBAPP_PUBLIC_URL
#   deploy/tunnel.sh --port 9542  — порт мини-аппа (по умолчанию WEBAPP_PORT из bot/.env или 9542)
# ВНИМАНИЕ: адрес *.trycloudflare.com меняется при каждом перезапуске туннеля — после перезапуска
# обнови WEBAPP_PUBLIC_URL и перезапусти бота. Постоянный адрес: named tunnel или VPS + Caddy
# (docs/DEPLOY.md).
set -euo pipefail

ROOT="$(cd "$(dirname "${BASH_SOURCE[0]}")/.." && pwd)"
ENV_FILE="$ROOT/bot/.env"
WRITE=0
PORT=""
while [[ $# -gt 0 ]]; do
	case "$1" in
	--write) WRITE=1 ;;
	--port)
		PORT="${2:?--port N}"
		shift
		;;
	-h | --help)
		sed -n '2,8p' "$0"
		exit 0
		;;
	*)
		echo "Неизвестный флаг: $1" >&2
		exit 2
		;;
	esac
	shift
done

if [[ -z "$PORT" ]]; then
	PORT="${WEBAPP_PORT:-}"
	if [[ -z "$PORT" && -f "$ENV_FILE" ]]; then
		PORT="$(grep -E '^WEBAPP_PORT=' "$ENV_FILE" | tail -n 1 | cut -d= -f2 | tr -d '"[:space:]' || true)"
	fi
	PORT="${PORT:-9542}"
fi

if ! command -v cloudflared >/dev/null; then
	cat >&2 <<'EOF'
cloudflared не найден. Установка:
  Ubuntu/Debian: curl -fL -o /tmp/cloudflared.deb \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64.deb \
      && sudo dpkg -i /tmp/cloudflared.deb
  SteamOS/любой Linux (без root): mkdir -p ~/.local/bin && curl -fL -o ~/.local/bin/cloudflared \
      https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64 \
      && chmod +x ~/.local/bin/cloudflared   (и ~/.local/bin должен быть в PATH)
EOF
	exit 1
fi

if command -v curl >/dev/null && ! curl -fsS -o /dev/null --max-time 3 "http://127.0.0.1:$PORT/"; then
	echo "!! мини-апп не отвечает на 127.0.0.1:$PORT — запусти бота (venv/bin/python bot/main.py)." >&2
	echo "   Туннель всё равно поднимаю: заработает, как только поднимется бот." >&2
fi

LOG="$(mktemp -t kurilka-tunnel.XXXXXX)"
cloudflared tunnel --no-autoupdate --url "http://127.0.0.1:$PORT" >"$LOG" 2>&1 &
PID=$!
cleanup() {
	kill "$PID" 2>/dev/null || true
	rm -f "$LOG"
}
trap cleanup EXIT
trap 'exit 130' INT TERM

URL=""
for _ in $(seq 1 60); do
	URL="$(grep -oE 'https://[a-z0-9-]+\.trycloudflare\.com' "$LOG" | grep -v '//api\.' | head -n 1 || true)"
	[[ -n "$URL" ]] && break
	if ! kill -0 "$PID" 2>/dev/null; then
		echo "cloudflared завершился:" >&2
		cat "$LOG" >&2
		exit 1
	fi
	sleep 1
done
if [[ -z "$URL" ]]; then
	echo "За 60 с адрес туннеля не появился. Лог cloudflared:" >&2
	tail -n 20 "$LOG" >&2
	exit 1
fi

echo
echo "✅ Туннель поднят: $URL  →  http://127.0.0.1:$PORT"
echo "   Проверка в браузере: $URL/app"
if [[ $WRITE -eq 1 ]]; then
	[[ -f "$ENV_FILE" ]] || cp "$ROOT/bot/.env.example" "$ENV_FILE"
	if grep -qE '^WEBAPP_PUBLIC_URL=' "$ENV_FILE"; then
		sed -i "s#^WEBAPP_PUBLIC_URL=.*#WEBAPP_PUBLIC_URL=$URL#" "$ENV_FILE"
	else
		printf '\nWEBAPP_PUBLIC_URL=%s\n' "$URL" >>"$ENV_FILE"
	fi
	echo "   Записано в bot/.env: WEBAPP_PUBLIC_URL=$URL"
else
	echo "   Впиши в bot/.env:  WEBAPP_PUBLIC_URL=$URL   (или запусти с --write)"
fi
echo "   Затем перезапусти бота — он сам выставит кнопку меню «Мини-апп»:"
echo "     systemctl --user restart kurilka   |  sudo systemctl restart kurilka  |  Ctrl+C и venv/bin/python bot/main.py"
echo "   Адрес сменится при перезапуске туннеля. Ctrl+C — остановить туннель."

wait "$PID"
