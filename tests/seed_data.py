"""Тестовые данные для скриншотов и смока (план 09: 8–10 фейковых клубов
с реальными лого, матчи, кэфы). Снос: tests/wipe.py или флаг --wipe."""
import hashlib
import hmac
import json
import os
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "bot"))
sys.path.insert(0, str(ROOT))

os.environ.setdefault("DB_PATH", str(ROOT / "logovo.db"))

import db  # noqa: E402
import league  # noqa: E402
import markets  # noqa: E402
import config  # noqa: E402

CLUBS = ["Аякс", "Ливерпуль", "Милан", "Порту", "Бенфика",
         "Марсель", "Лидс", "Ренн", "Ницца", "Вест Хэм"]
OWNERS = [  # (telegram_id, username, game_nickname)
    (700001, "kurilka_test", "temiyy"),
    (700002, "smoke_test2", "Rusli"),
    (700003, "smoke_test3", "quete-основа"),
    (700004, "smoke_test4", "KadyrFc"),
    (700005, "smoke_test5", "Gamer5"),
    (700006, "smoke_test6", "Gamer6"),
    (700007, "smoke_test7", "Gamer7"),
    (700008, "smoke_test8", "Gamer8"),
    (700009, "smoke_test9", "Gamer9"),
    (700010, "smoke_test10", "Gamer10"),
]

SCORES_T1 = [(2, 1), (0, 3), (1, 1), (4, 2), (2, 0)]


def wipe():
    c = db.db()
    for t in ("matches", "match_goals", "ties", "tours", "clubs", "club_players", "club_cards",
              "divisions", "tournaments", "players", "users", "bets", "bet_legs", "markets",
              "odds_history", "favorites", "notifications", "balance_history", "user_streaks",
              "processed_screenshots", "transfers", "transfer_lots", "debts", "club_requests",
              "promo_codes", "promo_activations", "tournament_elo", "tournament_admins",
              "tournament_audit_log", "reminder_log", "challenge_links", "saved_coupons"):
        c.execute(f"DELETE FROM {t}")
    c.commit()
    c.close()


def seed():
    db.init_db()
    wipe()
    tid = league.create_league_season("Сезон-1", n_divisions=1, tour_mode="manual", rounds=2, tour_days=3)
    div = league.tournament_divisions(tid)[0]

    c = db.db()
    for tg_id, username, nick in OWNERS:
        c.execute("INSERT OR IGNORE INTO players (username, telegram_id, game_nickname) VALUES (?,?,?)",
                  (username, tg_id, nick))
    c.commit()
    player_ids = {r["telegram_id"]: r["id"] for r in c.execute("SELECT id, telegram_id FROM players").fetchall()}
    c.close()

    club_ids = []
    from clubs_catalog import find_club, logo_path_for
    for name in CLUBS:
        canon, logo_file = find_club(name)
        cid = league.create_club(canon, logo_path_for(logo_file), div["id"], config.CLUB_START_BUDGET)
        club_ids.append(cid)
    for cid, (tg_id, _, _) in zip(club_ids, OWNERS):
        league.assign_club_owner(cid, player_ids[tg_id])

    league.generate_league_calendar(tid, div["id"])

    # тур 1: играем и подтверждаем → результаты + Elo + таблица
    c = db.db()
    for m, (s1, s2) in zip(league.tour_matches(tid, 1), SCORES_T1):
        c.execute("UPDATE matches SET score1=?, score2=?, status='confirmed', played_at=datetime('now') WHERE id=?",
                  (s1, s2, m["id"]))
        c.execute("INSERT INTO match_goals (match_id, raw_name, minute, side, ord) VALUES "
                  "(?,'Антоний',23,'home',0),(?,'Виртц',67,'away',1)",
                  (m["id"], m["id"]))
    c.commit()
    c.close()
    # пересчёт Elo/формы честно — через finalize-логику нельзя (счёт уже стоит); делаем руками:
    import elo as elo_engine
    c = db.db()
    for m, (s1, s2) in zip(league.tour_matches(tid, 1), SCORES_T1):
        r1 = c.execute("SELECT elo FROM clubs WHERE id=?", (m["home_club_id"],)).fetchone()["elo"]
        r2 = c.execute("SELECT elo FROM clubs WHERE id=?", (m["away_club_id"],)).fetchone()["elo"]
        n1, n2 = elo_engine.compute_elo_change(r1, r2, s1, s2)
        c.execute("UPDATE clubs SET elo=? WHERE id=?", (round(n1), m["home_club_id"]))
        c.execute("UPDATE clubs SET elo=? WHERE id=?", (round(n2), m["away_club_id"]))
        for cid, res in ((m["home_club_id"], "W" if s1 > s2 else ("D" if s1 == s2 else "L")),
                         (m["away_club_id"], "L" if s1 > s2 else ("D" if s1 == s2 else "W"))):
            old = c.execute("SELECT form FROM clubs WHERE id=?", (cid,)).fetchone()["form"] or ""
            c.execute("UPDATE clubs SET form=? WHERE id=?", ((old + res)[-5:], cid))
    c.commit()
    c.close()

    # тур 2: открыт, кэфы по Elo → линия
    league.open_tour(tid, 2, tour_days=3)
    markets.refresh_tour(tid, 2)
    markets.refresh_tour(tid, 3)

    # юзеры аппа
    c = db.db()
    for tg_id, username, _ in OWNERS:
        c.execute(
            "INSERT OR IGNORE INTO users (telegram_id, username, first_name, balance, is_admin) "
            "VALUES (?,?,?,?,?)",
            (tg_id, username, username.split("_")[0], 422 if tg_id != 700001 else 1337,
             1 if tg_id in (700001, 6163072393) else 0),
        )
        c.execute("INSERT OR IGNORE INTO notifications (user_id, text, kind) VALUES "
                  f"((SELECT id FROM users WHERE telegram_id={tg_id}), "
                  "'Добро пожаловать в KURILKA SIGARKI! Стартовый бонус на счету.', 'app')")
    c.execute("INSERT OR IGNORE INTO favorites (user_id, match_id) "
              "SELECT (SELECT id FROM users WHERE telegram_id=700001), id FROM matches "
              "WHERE tournament_id=? AND tour_number=2 LIMIT 1", (tid,))
    c.commit()
    c.close()

    # составы клубов (карты) + рынок трансферов для скриншотов
    names = [("Бергвейн", "ПВ", 84), ("Де Йонг", "ЦП", 87), ("Тимбер", "ЦЗ", 82),
             ("Пасвер", "ВРТ", 81), ("Бро́бби", "ФРВ", 79)]
    c = db.db()
    club_rows = [dict(r) for r in c.execute("SELECT id FROM clubs ORDER BY id LIMIT 5").fetchall()]
    for i, (nm, pos, rt) in enumerate(names):
        c.execute("INSERT INTO club_cards (club_id, name, position, rating) VALUES (?,?,?,?)",
                  (club_rows[i % len(club_rows)]["id"], nm, pos, rt))
    # свободные агенты
    for nm, pos, rt in (("Тотти", "ЦП", 80), ("Кака", "ЦП", 88), ("Дзеко", "ФРВ", 82)):
        c.execute("INSERT INTO club_cards (club_id, name, position, rating) VALUES (NULL,?,?,?)", (nm, pos, rt))
    c.commit()
    c.close()

    # тестовые ставки юзера 700001: одна рассчитанная (won), одна в игре
    c = db.db()
    uid = c.execute("SELECT id FROM users WHERE telegram_id=700001").fetchone()["id"]
    t1_match = c.execute("SELECT id FROM matches WHERE tournament_id=? AND tour_number=1 AND status='confirmed' LIMIT 1",
                         (tid,)).fetchone()["id"]
    markets.generate_markets(t1_match)
    t2_match = c.execute("SELECT id FROM matches WHERE tournament_id=? AND tour_number=2 LIMIT 1", (tid,)).fetchone()["id"]
    odd_won = c.execute("SELECT odds FROM markets WHERE match_id=? AND code='tb25'", (t1_match,)).fetchone()["odds"]
    odd_open = c.execute("SELECT odds FROM markets WHERE match_id=? AND code='1x2_p1'", (t2_match,)).fetchone()["odds"]
    bet1 = c.insert_returning_id(
        "INSERT INTO bets (user_id, bet_type, amount, total_odds, potential_win, status) VALUES (?, 'single', 100, ?, ?, 'won')",
        (uid, odd_won, int(100 * odd_won)))
    c.execute("INSERT INTO bet_legs (bet_id, match_id, market_code, odds, result) VALUES (?,?, 'tb25', ?, 'won')",
              (bet1, t1_match, odd_won))
    bet2 = c.insert_returning_id(
        "INSERT INTO bets (user_id, bet_type, amount, total_odds, potential_win, status) VALUES (?, 'single', 50, ?, ?, 'open')",
        (uid, odd_open, min(int(50 * odd_open), 10000)))
    c.execute("INSERT INTO bet_legs (bet_id, match_id, market_code, odds, result) VALUES (?,?, '1x2_p1', ?, 'pending')",
              (bet2, t2_match, odd_open))
    c.execute("UPDATE users SET balance=balance+100, total_wagered=150, bets_count=2, bets_won=1, "
              "xp=100 WHERE telegram_id=700001")
    c.execute("INSERT INTO balance_history (user_id, delta, reason) VALUES (?,?, 'выплата по купону (тест-сид)')",
              (uid, int(100 * odd_won)))
    c.commit()
    c.close()

    import transfers as tr
    c = db.db()
    cl_ids = [r["id"] for r in c.execute("SELECT id FROM clubs ORDER BY id LIMIT 3").fetchall()]
    sell1 = c.execute("SELECT id FROM club_cards WHERE club_id=? LIMIT 1", (cl_ids[1],)).fetchone()
    c.execute("INSERT INTO club_cards (club_id, name, position, rating) VALUES (?,?,?,?)",
              (cl_ids[2], "Нунье", "ЛП", 81))
    sell2 = c.execute("SELECT id FROM club_cards WHERE club_id=? AND name='Нунье'", (cl_ids[2],)).fetchone()
    c.commit()
    c.close()
    tr.create_lot(cl_ids[1], sell1["id"], "fix", 1_500_000)
    tr.create_lot(cl_ids[2], sell2["id"], "auction", 900_000, buyout_price=3_500_000)
    return tid


def sign_init_data(telegram_id: int, first_name: str = "Курилка") -> str:
    token = config.BOT_TOKEN
    pairs = {
        "auth_date": str(int(time.time())),
        "query_id": "AAFdev",
        "user": json.dumps({"id": telegram_id, "first_name": first_name,
                            "username": "kurilka_test"}, ensure_ascii=False),
    }
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", token.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


if __name__ == "__main__":
    if "--wipe" in sys.argv:
        db.init_db()
        wipe()
        print("Тестовые данные снесены.")
    else:
        tid = seed()
        print(f"Сид готов: сезон #{tid}, 10 клубов, тур 1 сыгран, тур 2+3 открыт с кэфами.")
        data = sign_init_data(700001)
        Path("/tmp/initdata.txt").write_text(data)
        print("initData для браузера: /tmp/initdata.txt")
