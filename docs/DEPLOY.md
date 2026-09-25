# Деплой KURILKA SIGARKI

Бот и мини-апп — **один процесс**: `venv/bin/python bot/main.py` (бот на polling + веб-сервер
мини-аппа на `127.0.0.1:9542`). Входящие порты боту не нужны. Telegram открывает мини-апп только
по **https** — поэтому нужен туннель (домашний ПК) или домен + Caddy (VPS).

Два пути:
- **(а) Домашний ПК / SteamOS + cloudflared** — бесплатно, без домена; быстрый туннель меняет адрес
  при каждом перезапуске.
- **(б) VPS + домен + Caddy** — постоянный адрес, автозапуск, HTTPS сам.

Файлы: `deploy/kurilka.service` (systemd), `deploy/install.sh` (установка на Ubuntu/Debian),
`deploy/tunnel.sh` (быстрый туннель), `deploy/Caddyfile`, `deploy/backup.sh`.

---

## 0. BotFather (один раз)

1. [@BotFather](https://t.me/BotFather) → `/newbot` → имя и username → получишь **токен**
   `123456:ABC…` → в `bot/.env`: `BOT_TOKEN=…`. Токен никому не показывай; утёк — `/revoke`.
2. Кнопку меню мини-аппа **руками ставить не надо**: при старте бот сам вызывает
   `setChatMenuButton`, если `WEBAPP_PUBLIC_URL` начинается с `https://`. Команды «/» бот тоже
   выставляет сам (`setMyCommands`, root видит полное меню).
3. `/setdomain` **не нужен** — это для Login Widget на сайтах, мини-апп авторизуется через initData.
4. По желанию: `/setuserpic`, `/setdescription`.

## 1. Root-админ: `ADMIN_IDS` и `/myid`

- Запусти бота, напиши ему `/myid` — он пришлёт твой Telegram ID.
- Впиши в `bot/.env`: `ADMIN_IDS=6163072393` (несколько — через запятую) и перезапусти бота.
- Root сразу админ мини-аппа (Кабинет → 👮) и видит все команды. Админов мини-аппа выдаёт
  `/admin @user`, судей турнира — `/judge <турнир_id> @user`.

## 2. OCR (распознавание скринов)

Нужен хотя бы один бесплатный ключ в `bot/.env` (лучше 2–3 — при лимите сработает следующий):

| Ключ | Где взять |
|---|---|
| `GEMINI_API_KEY` | aistudio.google.com (бесплатно, пока к проекту не привязан биллинг); модель — `GEMINI_MODEL` |
| `OPENROUTER_API_KEY` | openrouter.ai/keys — только `:free`-модели (свои — `OPENROUTER_MODELS`) |
| `NIM_API_KEY` | build.nvidia.com → Get API Key |
| `OCRSPACE_API_KEY` | ocr.space/ocrapi (только счёт, без бомбардиров) |

Без ключей остаётся локальный tesseract (слабее; `install.sh` ставит его сам, на ПК — `tesseract-ocr` + `rus`). Порядок каскада:
Gemini → OpenRouter free → NIM → Ollama → OCR.space → tesseract.

**Свои провайдеры без правки .env:** мини-апп → Кабинет → 👮 → «🧠 OCR-провайдеры» (только root):
любой OpenAI-совместимый API — base URL + модель + ключ, «первым» или «запасным», вкл/выкл,
кнопка «Проверить» прогоняет эталонный скрин (ожидается 2:3). Ключ хранится только в БД.

**Ollama (необязательно, локальная модель, ~3.2 ГБ):** `ollama pull qwen2.5vl:3b`, затем в `.env`
`OLLAMA_URL=http://127.0.0.1:11434` (`OLLAMA_FIRST=1` — раньше облака). На слабом VPS не ставь.

---

## (а) Домашний ПК / SteamOS + cloudflared

На SteamOS: режим рабочего стола → Konsole. Корень системы read-only — всё ставим в домашнюю папку,
без `sudo pacman`.

```bash
git clone https://github.com/1lvldufix-lab/legendary-robot.git ~/kurilka   # или своя папка проекта
cd ~/kurilka
python3 -m venv venv && venv/bin/pip install -r bot/requirements.txt
cp bot/.env.example bot/.env && nano bot/.env        # BOT_TOKEN, ADMIN_IDS, ключи OCR
venv/bin/python bot/main.py                          # проверка: http://127.0.0.1:9542/app
```

**cloudflared** (без root):
```bash
mkdir -p ~/.local/bin
curl -fL -o ~/.local/bin/cloudflared \
  https://github.com/cloudflare/cloudflared/releases/latest/download/cloudflared-linux-amd64
chmod +x ~/.local/bin/cloudflared
export PATH="$HOME/.local/bin:$PATH"                 # добавь в ~/.bashrc
```

**Быстрый туннель** (второй терминал, бот уже запущен):
```bash
deploy/tunnel.sh --write     # поднимет https://….trycloudflare.com и впишет WEBAPP_PUBLIC_URL в bot/.env
```
Затем перезапусти бота (Ctrl+C → `venv/bin/python bot/main.py` или `systemctl --user restart kurilka`) —
появятся кнопка меню «Мини-апп» и кнопка «🔥 Открыть мини-апп». Без `--write` скрипт просто
напечатает адрес.

> ⚠️ Адрес quick tunnel **меняется при каждом перезапуске** туннеля (и после сна/перезагрузки ПК).
> Каждый раз: `deploy/tunnel.sh --write` → перезапуск бота. Постоянный адрес — named tunnel ниже или VPS.

**Автозапуск бота (systemd --user):**
```bash
mkdir -p ~/.config/systemd/user
cat > ~/.config/systemd/user/kurilka.service <<EOF
[Unit]
Description=KURILKA SIGARKI
After=network-online.target

[Service]
WorkingDirectory=%h/kurilka
ExecStart=%h/kurilka/venv/bin/python bot/main.py
Restart=always
RestartSec=5

[Install]
WantedBy=default.target
EOF
systemctl --user daemon-reload && systemctl --user enable --now kurilka
sudo loginctl enable-linger "$USER"     # работать без входа в сессию (на SteamOS сперва `passwd`)
journalctl --user -u kurilka -f         # логи
```
ПК не должен засыпать (Steam → Настройки → Питание), иначе бот и туннель встанут.

**Named tunnel — постоянный адрес через Cloudflare** (нужен домен, добавленный в Cloudflare):
```bash
cloudflared tunnel login                              # откроет браузер, выбери домен
cloudflared tunnel create kurilka                     # создаст ~/.cloudflared/<UUID>.json
cloudflared tunnel route dns kurilka bot.example.com
cat > ~/.cloudflared/config.yml <<EOF
tunnel: kurilka
credentials-file: $HOME/.cloudflared/<UUID>.json
ingress:
  - hostname: bot.example.com
    service: http://127.0.0.1:9542
  - service: http_status:404
EOF
cloudflared tunnel run kurilka
```
В `.env`: `WEBAPP_PUBLIC_URL=https://bot.example.com` — больше не меняется. Автозапуск туннеля —
такой же user-юнит с `ExecStart=%h/.local/bin/cloudflared tunnel run kurilka`.

---

## (б) VPS + домен + Caddy (Ubuntu 22.04/24.04, Debian 12)

Хватает 1 vCPU / 1 ГБ RAM (с Playwright — лучше 2 ГБ).

1. **Код и установка:**
   ```bash
   sudo git clone https://github.com/1lvldufix-lab/legendary-robot.git /opt/kurilka
   sudo bash /opt/kurilka/deploy/install.sh --with-backup-cron      # + --with-playwright по желанию
   ```
   `install.sh` идемпотентен: пакеты, системный пользователь `kurilka`, `venv`, зависимости,
   `bot/.env` из примера (если нет, права 600), юнит `kurilka.service` (enable), cron бэкапа.
   В новый `.env` дописывается `DB_PATH=data/logovo.db` — рабочая база вне git
   (в репо лежит `logovo.db`, и `git pull` иначе конфликтовал бы с живой базой).
   Перенести текущую базу: `sudo cp /opt/kurilka/logovo.db /opt/kurilka/data/logovo.db` до первого старта
   (или скопируй свою с ПК через `scp`), затем `sudo chown kurilka: /opt/kurilka/data/logovo.db`.
2. **Настройки:** `sudo -u kurilka nano /opt/kurilka/bot/.env` → `BOT_TOKEN`, `ADMIN_IDS`, ключи OCR,
   `WEBAPP_PUBLIC_URL=https://bot.example.com`. Затем `sudo systemctl restart kurilka`.
3. **DNS:** A-запись `bot.example.com` → IP VPS. Файрвол: открыть 80 и 443 (`sudo ufw allow 80,443/tcp`),
   порт 9542 наружу **не** открывать (сервер слушает только 127.0.0.1).
4. **Caddy** (автоматический HTTPS от Let's Encrypt):
   ```bash
   sudo apt install -y debian-keyring debian-archive-keyring apt-transport-https curl
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/gpg.key' \
     | sudo gpg --dearmor -o /usr/share/keyrings/caddy-stable-archive-keyring.gpg
   curl -1sLf 'https://dl.cloudsmith.io/public/caddy/stable/debian.deb.txt' \
     | sudo tee /etc/apt/sources.list.d/caddy-stable.list
   sudo apt update && sudo apt install -y caddy
   sudo cp /opt/kurilka/deploy/Caddyfile /etc/caddy/Caddyfile
   sudo sed -i 's/bot.example.com/ТВОЙ.ДОМЕН/' /etc/caddy/Caddyfile
   sudo systemctl reload caddy
   ```
   Проверка: `https://ТВОЙ.ДОМЕН/app` открывается в браузере.

Без домена на VPS можно и cloudflared (путь «а», `deploy/tunnel.sh`), но адрес будет плавать.

---

## /scan по ссылке Challenge Place (необязательно)

`/scan https://challenge.place/c/<турнир> [турнир_id]` — бот сначала пробует обычный HTTPS-запрос,
затем headless Chromium. Нужен Playwright (≈400 МБ с браузером):
```bash
venv/bin/pip install -r bot/requirements-optional.txt
venv/bin/playwright install --with-deps chromium      # на VPS: install.sh --with-playwright
```
Принимаются только `https://` ссылки на `challenge.place` / `challengeplace.com` (и поддомены).
Сайт за Cloudflare Turnstile и часто не пускает автоматизированный браузер — бот ответит
«Cloudflare не пропустил…». Тогда: открыть турнир в обычном браузере → Ctrl+S («веб-страница
целиком») → положить файл на сервер → `/scan /путь/стр.html` (только root).

---

## Обновление

```bash
# VPS
sudo git -C /opt/kurilka -c safe.directory=/opt/kurilka pull
sudo bash /opt/kurilka/deploy/install.sh     # зависимости + права + restart; .env и базу не трогает
# домашний ПК
cd ~/kurilka && git pull && venv/bin/pip install -r bot/requirements.txt && systemctl --user restart kurilka
```
Схема БД мигрирует сама при старте. Перед крупным обновлением — ручной бэкап (ниже).

## Логи

```bash
journalctl -u kurilka -f                  # VPS, в реальном времени
journalctl -u kurilka --since "1 hour ago"
systemctl status kurilka
journalctl --user -u kurilka -f           # домашний ПК (user-юнит)
```

## Бэкапы

- Бот сам раз в сутки (04:00 МСК) кладёт копию в `backups/` и пишет root в ЛС.
- `deploy/backup.sh` — консистентный онлайн-бэкап SQLite (`.backup`, безопасно при работающем боте) в
  `backups/logovo-ГГГГММДД-ЧЧММ.db`, проверка `integrity_check`, копии старше 14 дней удаляются
  (`KEEP_DAYS=30 deploy/backup.sh` — дольше). Без `sqlite3` CLI (SteamOS) работает через Python.
- cron (VPS, ставит `install.sh --with-backup-cron` в `/etc/cron.d/kurilka-backup`):
  ```
  15 */6 * * * kurilka /opt/kurilka/deploy/backup.sh >>/opt/kurilka/backups/backup.log 2>&1
  ```
  Домашний ПК: `crontab -e` → `15 */6 * * * $HOME/kurilka/deploy/backup.sh >>$HOME/kurilka/backups/backup.log 2>&1`
  (на SteamOS cron может не быть — тогда systemd user-timer или хватит ежедневной копии бота).
- Хранить копии и вне сервера: `scp vps:/opt/kurilka/backups/logovo-*.db ~/kurilka-backups/`.
- **Восстановление** (проверь хотя бы раз, что бэкап рабочий):
  ```bash
  sudo systemctl stop kurilka
  sudo cp /opt/kurilka/backups/logovo-20260925-0415.db /opt/kurilka/data/logovo.db
  sudo rm -f /opt/kurilka/data/logovo.db-wal /opt/kurilka/data/logovo.db-shm
  sudo chown kurilka: /opt/kurilka/data/logovo.db && sudo systemctl start kurilka
  ```

## Чек-лист после деплоя

- [ ] `/start` боту отвечает, `/myid` → ID в `ADMIN_IDS`, root видит полное меню «/».
- [ ] Кнопка меню «Мини-апп» открывает приложение внутри Telegram (не «ошибка авторизации»).
- [ ] Скрин матча распознаётся (есть хотя бы один OCR-ключ или свой провайдер).
- [ ] `deploy/backup.sh` отработал, восстановление проверено.
- [ ] Секреты только в `bot/.env` (права 600), в git их нет.
