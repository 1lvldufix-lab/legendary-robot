"""Схема аукционов карточек (план 08 B3): ставки = блокировка суммы в бюджете клуба."""
from datetime import datetime, timedelta, timezone

from db_backend import column_exists, table_exists

SCHEMA = """
-- ставка = эскроу: сумма списана из clubs.budget, пока status='held'
CREATE TABLE IF NOT EXISTS transfer_bids (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    lot_id INTEGER, club_id INTEGER, amount INTEGER,
    status TEXT DEFAULT 'held',             -- held/outbid/won/refunded
    created_by INTEGER,
    created_at TEXT DEFAULT (datetime('now'))
);
CREATE INDEX IF NOT EXISTS idx_transfer_bids_lot ON transfer_bids(lot_id, status);
CREATE INDEX IF NOT EXISTS idx_transfer_bids_club ON transfer_bids(club_id, status);
-- очередь ЛС бота по трансферам: забирает close_expired_auctions() из job раз в минуту
CREATE TABLE IF NOT EXISTS transfer_dm_queue (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    telegram_id INTEGER, text TEXT, sent INTEGER DEFAULT 0,
    created_at TEXT DEFAULT (datetime('now'))
);
"""


def migrate(c) -> None:
    additions = {
        # ends_at — UTC 'YYYY-MM-DD HH:MM:SS'; top_* — текущая лидирующая ставка
        "transfer_lots": [("ends_at", "TEXT"), ("top_bid", "INTEGER"), ("top_club_id", "INTEGER"),
                          ("bids_count", "INTEGER DEFAULT 0"), ("closed_at", "TEXT")],
        "transfers": [("lot_id", "INTEGER")],
    }
    for table, cols in additions.items():
        for col, coltype in cols:
            if table_exists(c, table) and not column_exists(c, table, col):
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

    # старые аукционы без таймера: 24 ч от создания, иначе никогда не закроются
    for r in c.execute("SELECT id, created_at FROM transfer_lots "
                       "WHERE kind='auction' AND status='open' AND ends_at IS NULL").fetchall():
        try:
            start = datetime.strptime(str(r["created_at"])[:19].replace("T", " "), "%Y-%m-%d %H:%M:%S")
        except (TypeError, ValueError):
            start = datetime.now(timezone.utc).replace(tzinfo=None)
        c.execute("UPDATE transfer_lots SET ends_at=? WHERE id=?",
                  ((start + timedelta(hours=24)).strftime("%Y-%m-%d %H:%M:%S"), r["id"]))
