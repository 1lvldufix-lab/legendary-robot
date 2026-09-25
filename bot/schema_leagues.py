"""Лиги с именованными дивизионами: переключатель повышения/вылета на турнир."""
from db_backend import column_exists, table_exists

SCHEMA = ""


def migrate(c) -> None:
    # promote_count: сколько клубов меняются между соседними дивизионами (0 = независимые лиги)
    if table_exists(c, "tournaments") and not column_exists(c, "tournaments", "promote_count"):
        c.execute("ALTER TABLE tournaments ADD COLUMN promote_count INTEGER DEFAULT 3")
