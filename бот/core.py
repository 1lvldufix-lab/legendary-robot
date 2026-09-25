"""Общие хелперы бота: права, аудит, меню, игроки."""
import logging

from telegram import ReplyKeyboardMarkup

import config
import db as appdb

log = logging.getLogger("bot.core")


# ===== права (три уровня, решение 09) =====

def is_root(telegram_id: int) -> bool:
    return telegram_id in config.ADMIN_IDS


def is_app_admin(telegram_id: int) -> bool:
    """root или users.is_admin (админ мини-аппа)."""
    if is_root(telegram_id):
        return True
    c = appdb.db()
    row = c.execute("SELECT is_admin FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    c.close()
    return bool(row and row["is_admin"])


def is_tournament_admin(tournament_id: int, telegram_id: int) -> bool:
    if is_root(telegram_id):
        return True
    c = appdb.db()
    row = c.execute(
        "SELECT 1 FROM tournament_admins WHERE tournament_id=? AND telegram_id=?",
        (tournament_id, telegram_id),
    ).fetchone()
    c.close()
    return bool(row)


# ===== аудит =====

def audit(tournament_id: int | None, actor_telegram_id: int | None, action: str, details: str = "") -> None:
    c = appdb.db()
    c.execute(
        "INSERT INTO tournament_audit_log (tournament_id, actor_telegram_id, action, details) VALUES (?,?,?,?)",
        (tournament_id, actor_telegram_id, action, details),
    )
    c.commit()
    c.close()


# ===== игроки =====

def ensure_player(telegram_id: int, username: str | None = None) -> int:
    """Создаёт игрока, если нет; возвращает players.id."""
    c = appdb.db()
    c.execute(
        "INSERT OR IGNORE INTO players (username, telegram_id) VALUES (?,?)",
        (username, telegram_id),
    )
    c.commit()
    row = c.execute("SELECT id FROM players WHERE telegram_id=?", (telegram_id,)).fetchone()
    c.close()
    return row["id"]


def get_player(telegram_id: int) -> dict | None:
    c = appdb.db()
    row = c.execute("SELECT * FROM players WHERE telegram_id=?", (telegram_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def club_of_player(player_id: int) -> dict | None:
    """Клуб игрока через club_players (один клуб на человека — решение 09)."""
    c = appdb.db()
    row = c.execute(
        "SELECT cl.* FROM clubs cl JOIN club_players cp ON cp.club_id=cl.id "
        "WHERE cp.player_id=?",
        (player_id,),
    ).fetchone()
    c.close()
    return dict(row) if row else None


# ===== меню (план 05, раздел 8) =====

MENU_ROWS = [
    ["🏆 Турниры", "⚽ Мой Клуб"],
    ["💰 Долги", "📨 Репорт"],
    ["🔧 Настройки", "🐞 Связь"],
    ["👮 Админ", "ℹ️ Помощь"],
]


def menu_keyboard() -> ReplyKeyboardMarkup:
    return ReplyKeyboardMarkup(MENU_ROWS, resize_keyboard=True)


def send_menu_text() -> str:
    return (
        "Главное меню. Выбирай кнопку внизу.\n"
        "Мини-апп: " + (config.WEBAPP_PUBLIC_URL or f"http://127.0.0.1:{config.WEBAPP_PORT}/app")
    )
