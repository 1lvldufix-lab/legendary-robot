"""Карточки игроков FC Mobile в составах клубов: добавление владельцем/админом,
характеристики, фото, проверка судьёй / OCR / RenderZ.

- Владелец добавляет в свой клуб, пока открыто редактирование составов (squad_edit_open),
  до squad_max_cards карточек. Его карточка ждёт судью (pending), если не выключены
  одобрения (cards_require_approval=0) или RenderZ не совпал полностью (cards_auto_approve_renderz).
- Админ добавляет в любой клуб и в пул свободных агентов (club_id NULL) — сразу approved.
- Торговать (рынок/обмен/агенты/тренировки) можно только approved.
- Судья: админ турнира клуба карточки или админ аппа, но никогда — по своему клубу.
"""
import io
import json
import logging
import re
import secrets
import unicodedata
from datetime import datetime, timedelta, timezone

import config
import core
import db as appdb
import settings as appsettings
import transfers as tr

log = logging.getLogger("bot.cards")

POSITIONS = ["GK", "CB", "LB", "RB", "LWB", "RWB", "CDM", "CM", "CAM", "LM", "RM", "LW", "RW", "CF", "ST"]
RU_POSITIONS = {"ВРТ": "GK", "ЦЗ": "CB", "ЛЗ": "LB", "ПЗ": "RB", "ЛАЗ": "LWB", "ПАЗ": "RWB", "ЦОП": "CDM",
                "ЦП": "CM", "ЦАП": "CAM", "ЛП": "LM", "ПП": "RM", "ЛВ": "LW", "ПВ": "RW", "ФРВ": "CF", "НАП": "ST"}
STATS = ("pac", "sho", "pas", "dri", "def", "phy")
TEXT_FIELDS = {"nation": 40, "league": 40, "real_club": 40, "program": 40}
STATUSES = ("pending", "approved", "rejected")

MAX_UPLOAD_BYTES = 8 * 1024 * 1024
MAX_UPLOAD_SIDE = 4000
STORE_SIDE = 1000
UPLOADS_PER_HOUR = 60
OCR_PER_HOUR = 20
UPLOAD_TTL_HOURS = 24
MEDIA_NAME_RE = re.compile(r"^[a-f0-9]{32}\.jpg$")
TS = "%Y-%m-%d %H:%M:%S"


class CardError(Exception):
    def __init__(self, code: str, message: str, status: int = 400):
        super().__init__(message)
        self.code = code
        self.status = status


def _now() -> datetime:
    return datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(dt: datetime | None = None) -> str:
    return (dt or _now()).strftime(TS)


# ===== нормализация и валидация =====

def normalize_position(value) -> str | None:
    s = str(value or "").strip().upper().replace("Ё", "Е")
    if s in POSITIONS:
        return s
    return RU_POSITIONS.get(s)


def position_aliases(pos: str) -> list[str]:
    """EN-код + его русские варианты (старые карточки могли храниться по-русски)."""
    return [pos] + [ru for ru, en in RU_POSITIONS.items() if en == pos]


def parse_positions(value) -> list[str]:
    """«LW,ST» / ["ЛВ","НАП"] → ["LW","ST"]; нераспознанное отбрасывается."""
    if not value:
        return []
    items = value if isinstance(value, (list, tuple)) else re.split(r"[,;/\s]+", str(value))
    out = []
    for it in items:
        p = normalize_position(it)
        if p and p not in out:
            out.append(p)
    return out


def norm_name(name: str | None) -> str:
    s = unicodedata.normalize("NFKD", (name or "").casefold())
    s = "".join(ch for ch in s if not unicodedata.combining(ch))
    return re.sub(r"[^0-9a-zа-я]+", "", s.replace("ё", "е"))


def _int_field(fields: dict, key: str, lo: int, hi: int, label: str, required: bool = False) -> int | None:
    v = fields.get(key)
    if v is None or (isinstance(v, str) and not v.strip()):
        if required:
            raise CardError("BAD_INPUT", f"{label}: обязательное поле")
        return None
    try:
        n = int(float(v)) if not isinstance(v, bool) else None
    except (TypeError, ValueError):
        n = None
    if n is None or not lo <= n <= hi:
        raise CardError("BAD_INPUT", f"{label}: число от {lo} до {hi}")
    return n


def validate(fields: dict) -> dict:
    """Поля карточки из запроса → колонки club_cards. Бросает CardError с понятным текстом."""
    fields = fields or {}
    name = re.sub(r"\s+", " ", str(fields.get("name") or "")).strip()
    if not 2 <= len(name) <= 40:
        raise CardError("BAD_INPUT", "Имя игрока: от 2 до 40 символов")
    out = {"name": name, "rating": _int_field(fields, "rating", 40, 150, "OVR", required=True)}
    pos = normalize_position(fields.get("position"))
    if not pos:
        raise CardError("BAD_INPUT", "Позиция: одна из " + ", ".join(POSITIONS))
    out["position"] = pos
    raw_alt = fields.get("alt_positions")
    raw_items = raw_alt if isinstance(raw_alt, (list, tuple)) else re.split(r"[,;/\s]+", str(raw_alt or ""))
    raw_items = [x for x in raw_items if str(x or "").strip()]
    if any(not normalize_position(x) for x in raw_items):
        raise CardError("BAD_INPUT", "Доп. позиции: коды из " + ", ".join(POSITIONS))
    alts = [a for a in parse_positions(raw_items) if a != pos][:4]
    out["alt_positions"] = ",".join(alts) or None
    for s in STATS:
        out[s] = _int_field(fields, s, 0, 200, s.upper())
    out["skill_moves"] = _int_field(fields, "skill_moves", 1, 5, "Финты")
    out["weak_foot"] = _int_field(fields, "weak_foot", 1, 5, "Слабая нога")
    out["height_cm"] = _int_field(fields, "height_cm", 140, 230, "Рост")
    foot = str(fields.get("foot") or "").strip().upper()
    foot = {"LEFT": "L", "ЛЕВАЯ": "L", "Л": "L", "RIGHT": "R", "ПРАВАЯ": "R", "П": "R"}.get(foot, foot)
    if foot and foot not in ("L", "R"):
        raise CardError("BAD_INPUT", "Рабочая нога: L или R")
    out["foot"] = foot or None
    for key, limit in TEXT_FIELDS.items():
        v = re.sub(r"\s+", " ", str(fields.get(key) or "")).strip()
        if len(v) > limit:
            raise CardError("BAD_INPUT", f"{key}: не длиннее {limit} символов")
        out[key] = v or None
    return out


# ===== права =====

def _my_club(c, tg: int) -> int | None:
    row = c.execute("SELECT cp.club_id FROM club_players cp JOIN players p ON p.id=cp.player_id "
                    "WHERE p.telegram_id=?", (tg,)).fetchone()
    return row["club_id"] if row else None


def _edit_open() -> bool:
    return appsettings.setting_int("squad_edit_open", 1) == 1


def squad_max() -> int:
    return max(1, appsettings.setting_int("squad_max_cards", 60))


def _owner_tg(c, card) -> int | None:
    return tr.club_owner_tg(c, card["club_id"]) or card["added_by"]


def _in_deal(c, card_id: int) -> bool:
    return c.execute("SELECT 1 FROM transfers WHERE (card_id=? OR want_card_id=?) "
                     "AND status IN ('pending','needs_judge')", (card_id, card_id)).fetchone() is not None


def _can_decide(c, tg: int, card, admin: bool | None = None, my_club: int | None = -1) -> bool:
    if card["verify_status"] != "pending":
        return False
    my_club = _my_club(c, tg) if my_club == -1 else my_club
    if card["club_id"] is not None and card["club_id"] == my_club:
        return False  # по своему клубу не судят
    if (core.is_app_admin(tg) if admin is None else admin):
        return True
    tid = tr._club_tournament(c, card["club_id"])
    return bool(tid) and core.is_tournament_admin(tid, tg)


def _can_edit(c, tg: int, card, admin: bool, my_club: int | None) -> bool:
    if admin:
        return True
    return card["club_id"] is not None and card["club_id"] == my_club and card["verify_status"] in ("pending", "rejected")


def _delete_block(c, tg: int, card, admin: bool, my_club: int | None) -> str | None:
    """Причина запрета удаления или None."""
    if tr._on_market(c, card["id"]):
        return "Карточка выставлена на рынок — сначала сними лот"
    if admin:
        return None
    if card["club_id"] is None or card["club_id"] != my_club:
        return "Удалять можно только карточки своего клуба"
    if card["verify_status"] in ("pending", "rejected"):
        return None
    if not _edit_open():
        return "Редактирование составов закрыто админом"
    if _in_deal(c, card["id"]):
        return "Карточка участвует в сделке — дождись решения"
    return None


# ===== вывод =====

def _checks(raw) -> dict:
    try:
        data = json.loads(raw) if raw else {}
    except ValueError:
        data = {}
    return {"photo": bool(data.get("photo")), "ocr": data.get("ocr"), "renderz": data.get("renderz"),
            "renderz_match": data.get("renderz_match")}


def image_url(path: str | None) -> str | None:
    return f"/media/{path}" if path else None


def card_public(row, club_name: str | None = None, on_market: bool | None = None) -> dict:
    r = dict(row)
    return {
        "id": r["id"], "club_id": r.get("club_id"), "club_name": club_name if club_name is not None else r.get("club_name"),
        "name": r["name"], "rating": r.get("rating"),
        "position": r.get("position"),  # хранится английским кодом (старые русские — мигрированы)
        "alt_positions": parse_positions(r.get("alt_positions")),
        "stats": {s: r.get(s) for s in STATS},
        "skill_moves": r.get("skill_moves"), "weak_foot": r.get("weak_foot"), "foot": r.get("foot"),
        "height_cm": r.get("height_cm"), "nation": r.get("nation"), "league": r.get("league"),
        "real_club": r.get("real_club"), "program": r.get("program"),
        "image_url": image_url(r.get("image_path")), "renderz_url": r.get("renderz_url"),
        "verify_status": r.get("verify_status") or "approved", "verify_note": r.get("verify_note"),
        "checks": _checks(r.get("checks")), "source": r.get("source") or "seed",
        "on_market": bool(on_market) if on_market is not None else bool(r.get("on_market")),
        "created_at": r.get("created_at"), "updated_at": r.get("updated_at"),
        "added_by": r.get("added_by"), "verified_by": r.get("verified_by"), "verified_at": r.get("verified_at"),
    }


_CARD_SQL = "SELECT cc.*, cl.name AS club_name FROM club_cards cc LEFT JOIN clubs cl ON cl.id=cc.club_id "


def _load(c, card_id: int):
    return c.execute(_CARD_SQL + "WHERE cc.id=?", (card_id,)).fetchone()


def _market_ids(c, ids: list[int]) -> set[int]:
    if not ids:
        return set()
    marks = ",".join("?" * len(ids))
    return {r["card_id"] for r in c.execute(
        f"SELECT card_id FROM transfer_lots WHERE status IN ('open','closing','needs_judge') AND card_id IN ({marks})",
        ids).fetchall()}


def get_card(card_id: int) -> dict:
    c = appdb.db()
    try:
        row = _load(c, card_id)
        if not row:
            raise CardError("NOT_FOUND", "Карточка не найдена", 404)
        return card_public(row, on_market=tr._on_market(c, card_id))
    finally:
        c.close()


def _audit(c, club_id, actor_tg, action: str, details: str) -> None:
    c.execute("INSERT INTO tournament_audit_log (tournament_id, actor_telegram_id, action, details) VALUES (?,?,?,?)",
              (tr._club_tournament(c, club_id), actor_tg, action, details))


def _label(f) -> str:
    return f"{f['name']} {f['rating']} {normalize_position(f['position']) or f['position']}"


# ===== загрузки фото =====

def _media_cards_dir():
    d = config.MEDIA_DIR / "cards"
    d.mkdir(parents=True, exist_ok=True)
    return d


def cleanup_uploads(now: datetime | None = None) -> int:
    """Удалить загрузки, не привязанные к карточке дольше 24 ч (файл + строка)."""
    cutoff = _ts((now or _now()) - timedelta(hours=UPLOAD_TTL_HOURS))
    c = appdb.db()
    try:
        rows = c.execute("SELECT id, path FROM card_uploads WHERE card_id IS NULL AND created_at<?",
                         (cutoff,)).fetchall()
        for r in rows:
            _remove_media(r["path"])
            c.execute("DELETE FROM card_uploads WHERE id=?", (r["id"],))
        c.commit()
        return len(rows)
    finally:
        c.close()


def _remove_media(path: str | None) -> None:
    if not path or not MEDIA_NAME_RE.match(path.rsplit("/", 1)[-1]):
        return
    f = config.MEDIA_DIR / "cards" / path.rsplit("/", 1)[-1]
    try:
        f.unlink()
    except FileNotFoundError:
        pass
    except OSError as e:
        log.warning("[cards] не удалить %s: %s", f, e)


def process_image(data: bytes) -> bytes:
    """Проверка и пережатие: реальная картинка ≤ 8 МБ и ≤ 4000 px → JPEG ≤ 1000 px без EXIF."""
    from PIL import Image, ImageOps, UnidentifiedImageError
    if not data:
        raise CardError("BAD_IMAGE", "Пустой файл")
    if len(data) > MAX_UPLOAD_BYTES:
        raise CardError("TOO_BIG", "Файл больше 8 МБ")
    try:
        img = Image.open(io.BytesIO(data))
        if img.format not in ("JPEG", "PNG", "WEBP"):
            raise CardError("BAD_IMAGE", "Нужна картинка JPEG, PNG или WebP")
        w, h = img.size
        if w > MAX_UPLOAD_SIDE or h > MAX_UPLOAD_SIDE:
            raise CardError("TOO_BIG", f"Картинка больше {MAX_UPLOAD_SIDE} px по стороне")
        if w < 50 or h < 50:
            raise CardError("BAD_IMAGE", "Картинка слишком маленькая")
        img.load()
        img = ImageOps.exif_transpose(img)  # поворот по EXIF, сами метаданные не сохраняем
        if img.mode in ("RGBA", "LA", "P"):
            img = img.convert("RGBA")
            bg = Image.new("RGB", img.size, (255, 255, 255))
            bg.paste(img, mask=img.split()[-1])
            img = bg
        elif img.mode != "RGB":
            img = img.convert("RGB")
        img.thumbnail((STORE_SIDE, STORE_SIDE))
        out = io.BytesIO()
        img.save(out, "JPEG", quality=85, optimize=True)
        return out.getvalue()
    except CardError:
        raise
    except (UnidentifiedImageError, OSError, ValueError, Image.DecompressionBombError):
        raise CardError("BAD_IMAGE", "Файл не похож на картинку")


def save_upload(telegram_id: int, data: bytes, now: datetime | None = None) -> dict:
    """Сохранить фото карточки во временные загрузки → {token, image_url}."""
    now = now or _now()
    c = appdb.db()
    try:
        n = c.execute("SELECT COUNT(*) AS n FROM card_uploads WHERE telegram_id=? AND created_at>=?",
                      (telegram_id, _ts(now - timedelta(hours=1)))).fetchone()["n"]
    finally:
        c.close()
    if n >= UPLOADS_PER_HOUR:
        raise CardError("RATE_LIMIT", "Слишком много загрузок — попробуй через час", 429)
    jpeg = process_image(data)
    try:
        cleanup_uploads(now)
    except Exception as e:  # уборка не должна ломать загрузку
        log.warning("[cards] уборка загрузок: %s", e)
    name = secrets.token_hex(16) + ".jpg"
    (_media_cards_dir() / name).write_bytes(jpeg)
    token = secrets.token_hex(16)
    path = f"cards/{name}"
    c = appdb.db()
    try:
        c.execute("INSERT INTO card_uploads (token, telegram_id, path, created_at) VALUES (?,?,?,?)",
                  (token, telegram_id, path, _ts(now)))
        c.commit()
    finally:
        c.close()
    return {"token": token, "image_url": image_url(path)}


def _upload_row(c, token: str, telegram_id: int, card_id: int | None = None):
    row = c.execute("SELECT * FROM card_uploads WHERE token=?", (str(token or ""),)).fetchone()
    if not row or row["telegram_id"] != telegram_id:
        raise CardError("BAD_TOKEN", "Фото не найдено — загрузи заново")
    if row["card_id"] is not None and row["card_id"] != card_id:
        raise CardError("BAD_TOKEN", "Это фото уже привязано к другой карточке")
    return row


def upload_info(token: str, telegram_id: int) -> dict:
    """Для OCR по token: {token, image_url, bytes}."""
    c = appdb.db()
    try:
        row = _upload_row(c, token, telegram_id)
    finally:
        c.close()
    f = config.MEDIA_DIR / "cards" / row["path"].rsplit("/", 1)[-1]
    if not f.exists():
        raise CardError("BAD_TOKEN", "Фото устарело — загрузи заново")
    return {"token": row["token"], "image_url": image_url(row["path"]), "bytes": f.read_bytes()}


def ocr_allowed(telegram_id: int, now: datetime | None = None) -> bool:
    now = now or _now()
    c = appdb.db()
    try:
        n = c.execute("SELECT COUNT(*) AS n FROM card_uploads WHERE telegram_id=? AND ocr_at>=?",
                      (telegram_id, _ts(now - timedelta(hours=1)))).fetchone()["n"]
    finally:
        c.close()
    return n < OCR_PER_HOUR


def mark_ocr(token: str, fields: dict) -> None:
    c = appdb.db()
    try:
        c.execute("UPDATE card_uploads SET ocr_at=?, ocr_json=? WHERE token=?",
                  (_ts(), json.dumps(fields or {}, ensure_ascii=False), token))
        c.commit()
    finally:
        c.close()


def media_file(name: str):
    """Путь к файлу /media/cards/<name> или None (имя строго [a-f0-9]{32}.jpg — без обхода путей)."""
    if not MEDIA_NAME_RE.match(name or ""):
        return None
    f = config.MEDIA_DIR / "cards" / name
    return f if f.is_file() else None


# ===== проверки OCR / RenderZ =====

def _ocr_check(ocr_json: str | None, f: dict) -> bool | None:
    """Введённое совпало с распознанным с того же фото? None — распознавания не было."""
    try:
        o = json.loads(ocr_json) if ocr_json else None
    except ValueError:
        o = None
    if not o or not o.get("name") or o.get("rating") is None:
        return None
    import renderz
    same_name = renderz.name_similarity(f["name"], {"name": o["name"]}) >= renderz.NAME_OK
    same_pos = not o.get("position") or normalize_position(o["position"]) == f["position"]
    return bool(same_name and int(o["rating"]) == int(f["rating"]) and same_pos)


def _renderz_check(url: str | None, f: dict, warnings: list, fetch=None) -> tuple[str | None, int | None, dict | None]:
    """→ (канонический url, renderz_id, match|None). Сетевые сбои — предупреждение, не ошибка."""
    if not url:
        return None, None, None
    import renderz
    try:
        canon, rid = renderz.parse_url(url)
    except renderz.RenderzError as e:
        raise CardError("BAD_RENDERZ", str(e))
    if appsettings.setting_int("renderz_verify", 1) != 1:
        return canon, rid, None
    try:
        rz = renderz.lookup(canon, fetch=fetch)
    except renderz.RenderzError as e:
        warnings.append(f"RenderZ: {e}")
        return canon, rid, None
    match = renderz.compare(f, rz)
    if not match["all"]:
        diff = [lbl for k, lbl in (("name", "имя"), ("rating", "OVR"), ("position", "позиция")) if not match[k]]
        warnings.append(f"RenderZ не совпал: {', '.join(diff)} "
                        f"(там {rz.get('name')} {rz.get('rating')} {rz.get('position') or '?'})")
    return canon, rid, match


def _auto_status(match: dict | None) -> str:
    if appsettings.setting_int("cards_require_approval", 1) != 1:
        return "approved"
    if (match and match.get("all") and appsettings.setting_int("cards_auto_approve_renderz", 1) == 1
            and appsettings.setting_int("renderz_verify", 1) == 1):
        return "approved"
    return "pending"


def _dup_exists(c, club_id, f: dict, exclude_id: int | None = None) -> bool:
    where, args = ("club_id=?", [club_id]) if club_id is not None else ("club_id IS NULL", [])
    rows = c.execute(f"SELECT id, name, position FROM club_cards WHERE {where} AND rating=?",
                     args + [f["rating"]]).fetchall()
    key = norm_name(f["name"])
    return any(r["id"] != exclude_id and norm_name(r["name"]) == key
               and (normalize_position(r["position"]) or r["position"]) == f["position"] for r in rows)


# ===== создание / правка / удаление =====

def create_card(actor_tg: int, club_id: int | None, fields: dict, upload_token: str | None = None,
                renderz_url: str | None = None, renderz_fetch=None) -> dict:
    """Новая карточка → {"card", "warnings"}. club_id None = свободный агент (только админ)."""
    f = validate(fields)
    admin = core.is_app_admin(actor_tg)
    c = appdb.db()
    try:
        my_club = _my_club(c, actor_tg)
        if club_id is not None:
            club_id = int(club_id)
            if not c.execute("SELECT 1 FROM clubs WHERE id=?", (club_id,)).fetchone():
                raise CardError("NO_CLUB", "Клуб не найден", 404)
        if not admin:
            if club_id is None or club_id != my_club:
                raise CardError("FORBIDDEN", "Добавлять карточки можно только в свой клуб", 403)
            if not _edit_open():
                raise CardError("SQUAD_CLOSED", "Редактирование составов закрыто админом", 403)
            n = c.execute("SELECT COUNT(*) AS n FROM club_cards WHERE club_id=? "
                          "AND COALESCE(verify_status,'approved')!='rejected'", (club_id,)).fetchone()["n"]
            if n >= squad_max():
                raise CardError("LIMIT", f"В составе уже {n} карточек — лимит {squad_max()}")
        if _dup_exists(c, club_id, f):
            raise CardError("DUP", f"{_label(f)} уже есть в этом составе")
        upload = _upload_row(c, upload_token, actor_tg) if upload_token else None
    finally:
        c.close()

    warnings: list[str] = []
    canon, rid, match = _renderz_check(renderz_url, f, warnings, renderz_fetch)  # сеть — вне транзакции
    checks = {"photo": bool(upload), "ocr": _ocr_check(upload["ocr_json"], f) if upload else None,
              "renderz": None if match is None else ("match" if match["all"] else ("partial" if match.get("partial") else "mismatch")),
              "renderz_match": match}
    status = "approved" if admin else _auto_status(match)
    now = _ts()
    cols = dict(f, club_id=club_id, image_path=upload["path"] if upload else None, renderz_url=canon,
                renderz_id=rid, renderz_status=checks["renderz"], verify_status=status,
                checks=json.dumps(checks, ensure_ascii=False), added_by=actor_tg,
                source="admin" if admin else "owner", created_at=now, updated_at=now)
    if status == "approved":
        cols.update(verified_by=actor_tg if admin else None, verified_at=now,
                    verify_note=None if admin else ("RenderZ совпал" if match and match["all"] else "без проверки"))
    c = appdb.db()
    try:
        if _dup_exists(c, club_id, f):  # повтор после сетевой паузы (двойной тап)
            raise CardError("DUP", f"{_label(f)} уже есть в этом составе")
        keys = list(cols)
        card_id = c.insert_returning_id(
            f"INSERT INTO club_cards ({', '.join(keys)}) VALUES ({', '.join('?' * len(keys))})",
            [cols[k] for k in keys])
        if upload:
            c.execute("UPDATE card_uploads SET card_id=? WHERE id=?", (card_id, upload["id"]))
        _audit(c, club_id, actor_tg, "card_create", f"#{card_id} {_label(f)} клуб {club_id or 'агенты'} → {status}")
        if status == "pending":
            club = c.execute("SELECT name FROM clubs WHERE id=?", (club_id,)).fetchone()
            tr._call_judges(c, tr._club_tournament(c, club_id),
                            f"🃏 Карточка на проверку: {_label(f)} ({club['name'] if club else 'клуб'}) — "
                            "мини-апп → Рынок → Проверка карточек", skip=(actor_tg,))
        c.commit()
        card = card_public(_load(c, card_id), on_market=False)
    finally:
        c.close()
    return {"card": card, "warnings": warnings}


_UNSET = object()


def update_card(actor_tg: int, card_id: int, fields: dict, upload_token: str | None = None,
                renderz_url=_UNSET, renderz_fetch=None) -> dict:
    """Правка: владелец — пока pending/rejected (снова на проверку), админ — всегда."""
    admin = core.is_app_admin(actor_tg)
    c = appdb.db()
    try:
        card = _load(c, card_id)
        if not card:
            raise CardError("NOT_FOUND", "Карточка не найдена", 404)
        my_club = _my_club(c, actor_tg)
        if not _can_edit(c, actor_tg, card, admin, my_club):
            if card["club_id"] is not None and card["club_id"] == my_club:
                raise CardError("APPROVED", "Одобренную карточку правит только админ", 403)
            raise CardError("FORBIDDEN", "Это не твоя карточка", 403)
        merged = {k: card[k] for k in ("name", "rating", "position", "alt_positions", *STATS, "skill_moves",
                                       "weak_foot", "foot", "height_cm", *TEXT_FIELDS)}
        merged.update({k: v for k, v in (fields or {}).items() if k in merged})
        f = validate(merged)
        if _dup_exists(c, card["club_id"], f, exclude_id=card_id):
            raise CardError("DUP", f"{_label(f)} уже есть в этом составе")
        upload = _upload_row(c, upload_token, actor_tg, card_id) if upload_token else None
        # без нового фото OCR-сверка идёт по уже привязанному
        src = upload or c.execute("SELECT * FROM card_uploads WHERE card_id=? ORDER BY id DESC",
                                  (card_id,)).fetchone()
        old_checks = _checks(card["checks"])
    finally:
        c.close()

    warnings: list[str] = []
    url = card["renderz_url"] if renderz_url is _UNSET else (renderz_url or None)
    identity_changed = any(f[k] != card[k] for k in ("name", "rating", "position", "alt_positions"))
    if url and (renderz_url is not _UNSET or identity_changed or old_checks["renderz"] is None):
        canon, rid, match = _renderz_check(url, f, warnings, renderz_fetch)
    elif url:
        canon, rid, match = url, card["renderz_id"], old_checks["renderz_match"]
    else:
        canon, rid, match = None, None, None
    checks = {"photo": bool(upload) or bool(card["image_path"]),
              "ocr": _ocr_check(src["ocr_json"], f) if src else None,
              "renderz": None if match is None else ("match" if match["all"] else ("partial" if match.get("partial") else "mismatch")),
              "renderz_match": match}
    cols = dict(f, renderz_url=canon, renderz_id=rid, renderz_status=checks["renderz"],
                checks=json.dumps(checks, ensure_ascii=False), updated_at=_ts())
    if upload:
        cols["image_path"] = upload["path"]
    if not admin:
        status = _auto_status(match)
        cols.update(verify_status=status, verified_by=None, verified_at=_ts() if status == "approved" else None,
                    verify_note=None)
    c = appdb.db()
    try:
        c.execute(f"UPDATE club_cards SET {', '.join(k + '=?' for k in cols)} WHERE id=?",
                  [*cols.values(), card_id])
        if upload:
            c.execute("UPDATE card_uploads SET card_id=? WHERE id=?", (card_id, upload["id"]))
            if card["image_path"] and card["image_path"] != upload["path"]:
                c.execute("DELETE FROM card_uploads WHERE card_id=? AND path=?", (card_id, card["image_path"]))
                _remove_media(card["image_path"])
        _audit(c, card["club_id"], actor_tg, "card_update", f"#{card_id} {_label(f)}")
        if not admin and cols["verify_status"] == "pending":
            tr._call_judges(c, tr._club_tournament(c, card["club_id"]),
                            f"🃏 Карточка исправлена, нужна проверка: {_label(f)} ({card['club_name'] or 'клуб'})",
                            skip=(actor_tg,))
        c.commit()
        out = card_public(_load(c, card_id), on_market=tr._on_market(c, card_id))
    finally:
        c.close()
    return {"card": out, "warnings": warnings}


def delete_card(actor_tg: int, card_id: int) -> dict:
    admin = core.is_app_admin(actor_tg)
    c = appdb.db()
    try:
        card = _load(c, card_id)
        if not card:
            raise CardError("NOT_FOUND", "Карточка не найдена", 404)
        why = _delete_block(c, actor_tg, card, admin, _my_club(c, actor_tg))
        if why:
            raise CardError("FORBIDDEN", why, 403)
        # незавершённые обмены с этой карточкой теряют смысл
        c.execute("UPDATE transfers SET status='cancelled', decided_by=? WHERE (card_id=? OR want_card_id=?) "
                  "AND status IN ('pending','needs_judge')", (actor_tg, card_id, card_id))
        paths = [r["path"] for r in c.execute("SELECT path FROM card_uploads WHERE card_id=?", (card_id,)).fetchall()]
        c.execute("DELETE FROM card_uploads WHERE card_id=?", (card_id,))
        c.execute("DELETE FROM club_cards WHERE id=?", (card_id,))
        _audit(c, card["club_id"], actor_tg, "card_delete", f"#{card_id} {_label(card)}")
        owner = _owner_tg(c, card)
        if admin and owner and owner != actor_tg:
            tr._tell(c, owner, f"🗑 Админ удалил карточку {_label(card)}")
        c.commit()
    finally:
        c.close()
    for p in set(paths + [card["image_path"]]):
        _remove_media(p)
    return {"ok": True}


def decide(card_id: int, approve: bool, judge_tg: int, note: str | None = None) -> dict:
    note = (note or "").strip()[:300] or None
    c = appdb.db()
    try:
        card = _load(c, card_id)
        if not card:
            raise CardError("NOT_FOUND", "Карточка не найдена", 404)
        if card["verify_status"] != "pending":
            raise CardError("NOT_PENDING", "Карточка уже проверена")
        if not _can_decide(c, judge_tg, card):
            raise CardError("FORBIDDEN", "Решать может судья турнира этого клуба (не по своему клубу)", 403)
        status = "approved" if approve else "rejected"
        c.execute("UPDATE club_cards SET verify_status=?, verify_note=?, verified_by=?, verified_at=?, updated_at=? "
                  "WHERE id=?", (status, note, judge_tg, _ts(), _ts(), card_id))
        _audit(c, card["club_id"], judge_tg, f"card_{'approve' if approve else 'reject'}",
               f"#{card_id} {_label(card)}" + (f": {note}" if note else ""))
        text = (f"✅ Карточка {_label(card)} одобрена — можно торговать" if approve else
                f"❌ Карточка {_label(card)} отклонена" + (f": {note}" if note else "") + ". Исправь и отправь снова")
        tr._tell(c, _owner_tg(c, card), text)
        c.commit()
        out = card_public(_load(c, card_id), on_market=False)
    finally:
        c.close()
    return {"card": out}


# ===== чтение для экранов =====

def _history(c, card) -> list[dict]:
    rows = c.execute(
        "SELECT t.*, fc.name AS from_name, tc.name AS to_name, l.kind AS lot_kind FROM transfers t "
        "LEFT JOIN clubs fc ON fc.id=t.from_club_id LEFT JOIN clubs tc ON tc.id=t.to_club_id "
        "LEFT JOIN transfer_lots l ON l.id=t.lot_id "
        "WHERE t.card_id=? OR t.want_card_id=? ORDER BY t.id DESC LIMIT 50", (card["id"], card["id"])).fetchall()
    out = []
    for t in rows:
        if t["want_card_id"]:
            kind = "exchange"
        elif t["lot_id"]:
            kind = t["lot_kind"] or "fix"
        elif t["from_club_id"] is None:
            kind = "free_agent"
        else:
            kind = "deal"
        frm, to = t["from_name"], t["to_name"]
        if t["want_card_id"] == card["id"]:  # в обмене эта карточка идёт в обратную сторону
            frm, to = to, frm
        out.append({"date": t["created_at"], "from_club": frm, "to_club": to, "amount": t["amount"],
                    "status": t["status"], "kind": kind})
    out.append({"date": card["created_at"], "from_club": None, "to_club": card["club_name"], "amount": None,
                "status": card["verify_status"] or "approved", "kind": "created"})
    return out


def card_detail(card_id: int, viewer_tg: int) -> dict:
    admin = core.is_app_admin(viewer_tg)
    c = appdb.db()
    try:
        card = _load(c, card_id)
        if not card:
            raise CardError("NOT_FOUND", "Карточка не найдена", 404)
        my_club = _my_club(c, viewer_tg)
        owner_tg = tr.club_owner_tg(c, card["club_id"])
        owner = c.execute("SELECT telegram_id, username, game_nickname FROM players WHERE telegram_id=?",
                          (owner_tg,)).fetchone() if owner_tg else None
        return {
            "card": card_public(card, on_market=tr._on_market(c, card_id)),
            "owner": dict(owner) if owner else None,
            "history": _history(c, card),
            "can_edit": _can_edit(c, viewer_tg, card, admin, my_club),
            "can_delete": _delete_block(c, viewer_tg, card, admin, my_club) is None,
            "can_decide": _can_decide(c, viewer_tg, card, admin, my_club),
        }
    finally:
        c.close()


def my_cards(tg: int) -> dict:
    admin = core.is_app_admin(tg)
    c = appdb.db()
    try:
        club_id = _my_club(c, tg)
        club = c.execute("SELECT id, name FROM clubs WHERE id=?", (club_id,)).fetchone() if club_id else None
        rows = c.execute(_CARD_SQL + "WHERE cc.club_id=? ORDER BY cc.rating DESC, cc.id", (club_id,)).fetchall() \
            if club else []
        market = _market_ids(c, [r["id"] for r in rows])
        return {
            "club": {"id": club["id"], "name": club["name"]} if club else None,
            "cards": [card_public(r, on_market=r["id"] in market) for r in rows],
            "can_edit": bool(club) and (admin or _edit_open()),
            "squad_max": squad_max(),
            "is_admin": admin,
            "require_approval": appsettings.setting_int("cards_require_approval", 1) == 1,
        }
    finally:
        c.close()


def queue(tg: int) -> list[dict]:
    """Pending-карточки, по которым зритель может вынести решение."""
    admin = core.is_app_admin(tg)
    c = appdb.db()
    try:
        my_club = _my_club(c, tg)
        rows = c.execute(_CARD_SQL + "WHERE cc.verify_status='pending' ORDER BY cc.id").fetchall()
        out = []
        for r in rows:
            if not _can_decide(c, tg, r, admin, my_club):
                continue
            item = card_public(r, on_market=False)
            otg = tr.club_owner_tg(c, r["club_id"]) or r["added_by"]
            o = c.execute("SELECT telegram_id, username FROM players WHERE telegram_id=?", (otg,)).fetchone() \
                if otg else None
            item["owner"] = dict(o) if o else ({"telegram_id": otg, "username": None} if otg else None)
            out.append(item)
        return out
    finally:
        c.close()


def is_tradable(card) -> bool:
    """Рынок/обмен/агенты/тренировки — только одобренные карточки."""
    if card is None:
        return False
    return (dict(card).get("verify_status") or "approved") == "approved"
