"""Снос тестовых данных (план 09): чистит все рабочие таблицы, схему оставляет.
Запуск: venv/bin/python tests/wipe.py [путь_к_бд]"""
import os
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT))

db_path = sys.argv[1] if len(sys.argv) > 1 else str(ROOT / "logovo.db")
os.environ["DB_PATH"] = db_path

import db  # noqa: E402

db.init_db()
c = db.db()
tables = [r[0] for r in c.execute(
    "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%' AND name NOT LIKE 'bot_settings'").fetchall()]
for t in tables:
    c.execute(f"DELETE FROM {t}")
c.commit()
c.close()
print(f"Снесено: {len(tables)} таблиц в {db_path} (bot_settings не тронуты).")
