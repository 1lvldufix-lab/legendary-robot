"""Схема фичи «Прогресс»: достижения (доп. колонки), зал славы, fair-play учёт тренировок.

Подхватывается db.init_db автоматически (bot/schema_*.py).
"""

SCHEMA = """
-- зал славы: снимок чемпионов/обладателей кубков (после 3↑/3↓ таблицу уже не пересчитать)
CREATE TABLE IF NOT EXISTS hall_of_fame (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    tournament_id INTEGER NOT NULL,
    division_id INTEGER NOT NULL DEFAULT 0,     -- 0 = кубок (ЛЧ/ЛЕ/ЛК)
    kind TEXT NOT NULL,                         -- league/cup
    title TEXT,                                 -- «Сезон-1 · Высший дивизион» / «ЛЧ»
    club_id INTEGER, club_name TEXT,
    owner_player_id INTEGER, owner_telegram_id INTEGER,
    points INTEGER,
    recorded_at TEXT DEFAULT (datetime('now')),
    UNIQUE (tournament_id, division_id)
);
-- тренировки проводятся в самой FC Mobile — апп только ведёт учёт (решение 09)
CREATE TABLE IF NOT EXISTS training_log (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    user_id INTEGER, player_id INTEGER,         -- кто записал: users.id / players.id
    card_id INTEGER, card_name TEXT,            -- club_cards.id + имя на момент записи
    kind TEXT DEFAULT 'ovr',                    -- ovr/rank/skill/other
    count INTEGER DEFAULT 1,
    note TEXT,
    period_start TEXT,                          -- понедельник недели (YYYY-MM-DD, UTC)
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE TABLE IF NOT EXISTS training_violations (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    club_id INTEGER NOT NULL,
    period_start TEXT,
    total INTEGER, limit_value INTEGER,
    last_log_id INTEGER,
    status TEXT DEFAULT 'open',                 -- open/resolved/penalized
    admin_note TEXT, resolved_by INTEGER, resolved_at TEXT,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_training_log_club ON training_log(club_id, period_start);
CREATE INDEX IF NOT EXISTS idx_training_violations_status ON training_violations(status);
"""


def migrate(c) -> None:
    from db_backend import column_exists, table_exists

    additions = {
        "achievements": [("category", "TEXT"), ("rarity", "TEXT DEFAULT 'common'"),
                         ("target", "INTEGER DEFAULT 1"), ("sort_order", "INTEGER DEFAULT 0"),
                         ("is_active", "INTEGER DEFAULT 1")],
        "user_achievements": [("claimed_at", "TEXT")],
    }
    for table, cols in additions.items():
        for col, coltype in cols:
            if table_exists(c, table) and not column_exists(c, table, col):
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")
    import achievements
    achievements.seed_catalog(c)
