"""Авторизация и хелперы юзера (общие для server.py и routes.py)."""
import hashlib
import hmac
import json
import time

from aiohttp import web

import config
import db as appdb


def validate_init_data(init_data: str) -> dict | None:
    """dict пользователя, если подпись валидна и auth_date в окне 24 ч, иначе None."""
    from urllib.parse import parse_qsl
    try:
        pairs = dict(parse_qsl(init_data, keep_blank_values=True))
    except Exception:
        return None
    received_hash = pairs.pop("hash", None)
    if not received_hash:
        return None
    data_check_string = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret_key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    computed = hmac.new(secret_key, data_check_string.encode(), hashlib.sha256).hexdigest()
    if not hmac.compare_digest(computed, received_hash):
        return None
    try:
        auth_date = int(pairs.get("auth_date", "0"))
    except ValueError:
        return None
    if not auth_date:
        return None
    max_age = config.INITDATA_MAX_AGE_HOURS * 3600
    if time.time() - auth_date > max_age:
        return None
    try:
        user = json.loads(pairs.get("user", "{}"))
    except Exception:
        return None
    if not user.get("id"):
        return None
    return user


def require_user(request: web.Request) -> dict:
    raw = request.headers.get("X-Telegram-Init-Data", "")
    user = validate_init_data(raw)
    if not user:
        raise web.HTTPUnauthorized(text="bad initData")
    return user


def upsert_user(tg_user: dict) -> dict:
    c = appdb.db()
    row = c.execute("SELECT * FROM users WHERE telegram_id=?", (tg_user["id"],)).fetchone()
    if row is None:
        c.execute(
            "INSERT INTO users (telegram_id, username, first_name, photo_url, balance) VALUES (?,?,?,?,?)",
            (tg_user["id"], tg_user.get("username"), tg_user.get("first_name"),
             tg_user.get("photo_url"), config.START_BALANCE),
        )
        c.commit()
        row = c.execute("SELECT * FROM users WHERE telegram_id=?", (tg_user["id"],)).fetchone()
    c.close()
    return dict(row)


def load_settings_map() -> dict[str, str]:
    c = appdb.db()
    rows = c.execute("SELECT key, value FROM bot_settings").fetchall()
    c.close()
    return {r["key"]: r["value"] for r in rows}


def setting_int(m: dict, key: str, default: int) -> int:
    try:
        return int(m.get(key, default))
    except (TypeError, ValueError):
        return default


def bet_limits() -> dict:
    s = load_settings_map()
    return {
        "min_bet": setting_int(s, "min_bet", config.MIN_BET),
        "max_bet": setting_int(s, "max_bet", config.MAX_BET),
        "max_payout": setting_int(s, "max_payout", config.MAX_PAYOUT),
        "max_open_bets": setting_int(s, "max_open_bets", config.MAX_OPEN_BETS),
        "max_open_exposure": setting_int(s, "max_open_exposure", config.MAX_OPEN_EXPOSURE),
        "max_legs": setting_int(s, "max_legs", config.MAX_LEGS),
        "odds_margin_pct": float(s.get("odds_margin_pct", config.ODDS_MARGIN_PCT)),
    }


def user_payload(u: dict) -> dict:
    level, xp = u.get("level", 1), u.get("xp", 0)
    return {
        "user_id": u["telegram_id"],
        "username": u.get("username"),
        "first_name": u.get("first_name"),
        "photo_url": u.get("photo_url"),
        "balance": u["balance"],
        "xp": xp,
        "level": level,
        "is_admin": bool(u["is_admin"]),
        "is_frozen": bool(u["is_frozen"]),
        "freeze_reason": u.get("freeze_reason"),
        "bet_limits": bet_limits(),
    }


def require_active_user(request: web.Request) -> dict:
    """Авторизация + 403 замороженным с причиной (решение 09)."""
    tg_user = require_user(request)
    u = upsert_user(tg_user)
    if u["is_frozen"]:
        raise web.HTTPForbidden(
            text=json.dumps({"status": "error", "error": "Доступ закрыт",
                             "reason": u.get("freeze_reason") or "не указана"}),
            content_type="application/json",
        )
    return u
