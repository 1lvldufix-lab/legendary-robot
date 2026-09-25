"""Конфиг KURILKA SIGARKI — всё из env, секреты в git не попадают.

Все значения, которыми владелец захочет управлять на лету, дублируются
в таблицу bot_settings (см. bot/settings.py) — env здесь только стартовые дефолты.
"""
import os
from pathlib import Path

from dotenv import load_dotenv

# .env лежит рядом с этим файлом; грузим по абсолютному пути,
# чтобы запуск из любой папки (miniapp/, tests/) видел один и тот же env.
load_dotenv(Path(__file__).resolve().parent / ".env")

# Корень проекта (logovo-clone/) — для абсолютных путей к БД и статике
PROJECT_ROOT = Path(__file__).resolve().parent.parent

BOT_TOKEN = os.environ.get("BOT_TOKEN", "")           # токен турнирного бота
ADMIN_IDS = {int(x) for x in os.environ.get("ADMIN_IDS", "").replace(",", " ").split() if x.strip().isdigit()}

# БД: DATABASE_URL (Postgres) или файл SQLite (абсолютный путь от корня проекта)
DATABASE_URL = os.environ.get("DATABASE_URL", "")
_db_path_raw = os.environ.get("DB_PATH", "logovo.db")
DB_PATH = _db_path_raw if os.path.isabs(_db_path_raw) else str(PROJECT_ROOT / _db_path_raw)

# Мини-апп
WEBAPP_HOST = os.environ.get("WEBAPP_HOST", "127.0.0.1")
WEBAPP_PORT = int(os.environ.get("WEBAPP_PORT", "9542"))   # 8080 занят Steam
WEBAPP_PUBLIC_URL = os.environ.get("WEBAPP_PUBLIC_URL", "")  # https://... для кнопки меню

# Внешние сервисы (ключи ротировать! у елобота ключи утекли в репо)
OPENROUTER_API_KEY = os.environ.get("OPENROUTER_API_KEY", "")
GROQ_API_KEY = os.environ.get("GROQ_API_KEY", "")
NIM_API_KEY = os.environ.get("NIM_API_KEY", "")
OCRSPACE_API_KEY = os.environ.get("OCRSPACE_API_KEY", "")
GEMINI_API_KEY = os.environ.get("GEMINI_API_KEY", "")
# gemini-2.0-flash выключен Google 01.06.2026 — модель вынесена в env, чтобы менять без правки кода
GEMINI_MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash")
# список free VL-моделей OpenRouter через запятую (пусто = дефолт из ocr.py); free-модели часто снимают
OPENROUTER_MODELS = [m.strip() for m in os.environ.get("OPENROUTER_MODELS", "").split(",") if m.strip()]
# локальная vision-модель через Ollama (http://127.0.0.1:11434); пусто = не используется
OLLAMA_URL = os.environ.get("OLLAMA_URL", "").rstrip("/")
OLLAMA_MODEL = os.environ.get("OLLAMA_MODEL", "qwen2.5vl:3b")   # ~3.2 ГБ
OLLAMA_FIRST = os.environ.get("OLLAMA_FIRST", "0") == "1"     # 1 = локальная модель раньше облака

# ===== Личные деньги юзера («дым») и лимиты ставок (как у оригинала) =====
START_BALANCE = int(os.environ.get("START_BALANCE", "422"))   # per план 09 (не 677)
MIN_BET = int(os.environ.get("MIN_BET", "10"))
MAX_BET = int(os.environ.get("MAX_BET", "50000"))
MAX_PAYOUT = int(os.environ.get("MAX_PAYOUT", "10000"))
MAX_OPEN_BETS = int(os.environ.get("MAX_OPEN_BETS", "6"))
MAX_OPEN_EXPOSURE = int(os.environ.get("MAX_OPEN_EXPOSURE", "20000"))
ODDS_MARGIN_PCT = float(os.environ.get("ODDS_MARGIN_PCT", "5"))   # публичная маржа
MAX_LEGS = int(os.environ.get("MAX_LEGS", "5"))                   # экспресс до 5 ног

# ===== Экономика клубов (отдельная от дыма) =====
CLUB_START_BUDGET = int(os.environ.get("CLUB_START_BUDGET", "20_000_000"))
TRANSFER_DEAL_THRESHOLD = int(os.environ.get("TRANSFER_DEAL_THRESHOLD", "2_000_000"))  # выше — судья
TRANSFER_COMMISSION_PCT = float(os.environ.get("TRANSFER_COMMISSION_PCT", "5"))        # сгорает
FREE_AGENT_K = int(os.environ.get("FREE_AGENT_K", "1000"))  # цена = рейтинг² × K

# ===== Турниры =====
DISPUTE_WINDOW_HOURS = int(os.environ.get("DISPUTE_WINDOW_HOURS", "24"))  # окно оспаривания
UNPLAYED_FINE = int(os.environ.get("UNPLAYED_FINE", "1_000_000"))         # штраф-долг за неигранный матч
DEFAULT_TOUR_DAYS = int(os.environ.get("DEFAULT_TOUR_DAYS", "3"))         # дефолт длительности тура
SEASON_PRIZE_CHAMPION = int(os.environ.get("SEASON_PRIZE_CHAMPION", "100_000_000"))
DEBT_REMINDER_TIMES = os.environ.get("DEBT_REMINDER_TIMES", "08:00,12:00,18:00").split(",")

# ===== Прогрессия (кабинет) =====
XP_PER_WIN = int(os.environ.get("XP_PER_WIN", "100"))      # XP за выигрыш купона
LEVEL_XP_STEP = int(os.environ.get("LEVEL_XP_STEP", "500"))  # уровень N требует N×500 XP

# ===== Экономика дыма: бонус-серия входов =====
STREAK_BONUS_BASE = int(os.environ.get("STREAK_BONUS_BASE", "50"))
STREAK_BONUS_STEP = int(os.environ.get("STREAK_BONUS_STEP", "10"))
STREAK_BONUS_CAP = int(os.environ.get("STREAK_BONUS_CAP", "150"))

# Безопасность: окно свежести initData (план 09 — обязательно, иначе вечный доступ)
INITDATA_MAX_AGE_HOURS = int(os.environ.get("INITDATA_MAX_AGE_HOURS", "24"))

TZ = os.environ.get("BOT_DISPLAY_TZ", "Europe/Moscow")
