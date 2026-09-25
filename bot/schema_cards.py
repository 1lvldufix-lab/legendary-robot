"""Карточки игроков: характеристики, фото, проверка (судья / OCR / RenderZ)."""
from db_backend import column_exists, table_exists

SCHEMA = """
-- временные загрузки фото карточек: без card_id дольше 24 ч — удаляются
CREATE TABLE IF NOT EXISTS card_uploads (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    token TEXT UNIQUE, telegram_id INTEGER, path TEXT,
    card_id INTEGER,
    ocr_at TEXT, ocr_json TEXT,              -- результат распознавания этого фото
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_card_uploads_tg ON card_uploads(telegram_id, created_at);
-- кэш проверки RenderZ (24 ч): только имя/OVR/позиция/сборная, их данные не храним дольше
CREATE TABLE IF NOT EXISTS renderz_cache (
    url TEXT PRIMARY KEY, renderz_id INTEGER, data TEXT, fetched_at TEXT
);
"""

CARD_COLUMNS = [
    ("alt_positions", "TEXT"),
    ("pac", "INTEGER"), ("sho", "INTEGER"), ("pas", "INTEGER"),
    ("dri", "INTEGER"), ("def", "INTEGER"), ("phy", "INTEGER"),
    ("skill_moves", "INTEGER"), ("weak_foot", "INTEGER"), ("foot", "TEXT"), ("height_cm", "INTEGER"),
    ("nation", "TEXT"), ("league", "TEXT"), ("real_club", "TEXT"), ("program", "TEXT"),
    ("image_path", "TEXT"), ("renderz_url", "TEXT"), ("renderz_id", "INTEGER"),
    ("renderz_status", "TEXT"),                       # match/mismatch/NULL — для фильтра «проверено»
    ("verify_status", "TEXT DEFAULT 'approved'"),     # старые карточки остаются одобренными
    ("verify_note", "TEXT"), ("checks", "TEXT"),
    ("verified_by", "INTEGER"), ("verified_at", "TEXT"),
    ("added_by", "INTEGER"), ("source", "TEXT DEFAULT 'seed'"), ("updated_at", "TEXT"),
]

# старые сиды хранили русские позиции — приводим к кодам FC Mobile (фильтры рынка)
_RU_POS = {"ВРТ": "GK", "ЦЗ": "CB", "ЛЗ": "LB", "ПЗ": "RB", "ЛАЗ": "LWB", "ПАЗ": "RWB", "ЦОП": "CDM",
           "ЦП": "CM", "ЦАП": "CAM", "ЛП": "LM", "ПП": "RM", "ЛВ": "LW", "ПВ": "RW", "ФРВ": "CF", "НАП": "ST"}


def migrate(c) -> None:
    if not table_exists(c, "club_cards"):
        return
    for col, coltype in CARD_COLUMNS:
        if not column_exists(c, "club_cards", col):
            c.execute(f"ALTER TABLE club_cards ADD COLUMN {col} {coltype}")
    for ru, en in _RU_POS.items():
        c.execute("UPDATE club_cards SET position=? WHERE position=?", (en, ru))
    for sql in (
        "CREATE INDEX IF NOT EXISTS idx_club_cards_club ON club_cards(club_id, verify_status)",
        "CREATE INDEX IF NOT EXISTS idx_club_cards_rating ON club_cards(rating)",
        "CREATE INDEX IF NOT EXISTS idx_club_cards_position ON club_cards(position)",
        "CREATE INDEX IF NOT EXISTS idx_club_cards_status ON club_cards(verify_status)",
        "CREATE INDEX IF NOT EXISTS idx_transfer_lots_card ON transfer_lots(card_id, status)",
    ):
        c.execute(sql)
