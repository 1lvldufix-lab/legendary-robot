"""Схема кубков: позиция серии в сетке + итог кубка (победитель, выплата призовых).

Подхватывается db.init_db (_schema_extensions).
"""

SCHEMA = """
CREATE TABLE IF NOT EXISTS cup_results (
    tournament_id INTEGER PRIMARY KEY,
    winner_club_id INTEGER,
    finished_at TEXT DEFAULT (datetime('now')),
    prize_paid INTEGER DEFAULT 0,           -- 1 = призовые выплачены (finalize_season), повторно не платим
    prize_club_id INTEGER, prize_amount INTEGER, prize_paid_at TEXT
);
"""


def migrate(c) -> None:
    from db_backend import column_exists, table_exists

    if not table_exists(c, "ties"):
        return
    if not column_exists(c, "ties", "bracket_pos"):
        c.execute("ALTER TABLE ties ADD COLUMN bracket_pos INTEGER")
    # старые сетки без позиции: порядок создания = порядок в сетке
    rows = c.execute(
        "SELECT id, tournament_id, stage FROM ties WHERE bracket_pos IS NULL ORDER BY id").fetchall()
    for r in rows:
        n = c.execute(
            "SELECT COALESCE(MAX(bracket_pos), -1) + 1 AS n FROM ties WHERE tournament_id=? AND stage=?",
            (r["tournament_id"], r["stage"])).fetchone()["n"]
        c.execute("UPDATE ties SET bracket_pos=? WHERE id=?", (n, r["id"]))
    c.execute("CREATE UNIQUE INDEX IF NOT EXISTS idx_ties_bracket ON ties(tournament_id, stage, bracket_pos)")
