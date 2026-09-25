"""ГЕЙТ 17: прогресс — каталог достижений (идемпотентный сид), ленивые условия открытия,
награда забирается один раз, Зал Славы (баланс/выигрыш/чемпионы после 3↑/3↓ и кубок),
fair-play учёт тренировок: лимит → нарушение, админ-эндпоинты 403 для игроков."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from datetime import date, timedelta
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bot"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["ADMIN_IDS"] = "917001"
os.environ["DB_PATH"] = "/tmp/gate17.db"
if os.path.exists("/tmp/gate17.db"):
    os.remove("/tmp/gate17.db")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import achievements as ach  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import league  # noqa: E402
import settings as appsettings  # noqa: E402
import training as tr  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


def q(sql, args=()):
    c = db.db()
    rows = [dict(r) for r in c.execute(sql, args).fetchall()]
    c.close()
    return rows


def ex(sql, args=()):
    c = db.db()
    rid = c.insert_returning_id(sql, args) if sql.lstrip().upper().startswith("INSERT") else c.execute(sql, args)
    c.commit()
    c.close()
    return rid


# ===== каталог =====
db.init_db()
db.init_db()
cat = q("SELECT code, title, rarity, reward_xp, reward_coins FROM achievements WHERE is_active=1")
check("каталог: 25–30 достижений", 25 <= len(cat) <= 30, str(len(cat)))
check("каталог: коды уникальны, повторный init не дублирует", len({a["code"] for a in cat}) == len(cat)
      == len(q("SELECT id FROM achievements")))
check("каталог: у всех редкость и награды", all(a["rarity"] in ach.RARITY_ORDER and a["reward_xp"] > 0
                                                and a["reward_coins"] > 0 for a in cat))
ex("UPDATE achievements SET title='сломано' WHERE code='first_bet'")
db.init_db()
check("каталог: правка в БД перезаписывается кодом", q("SELECT title FROM achievements WHERE code='first_bet'")[0]["title"]
      == "Первая затяжка")

# ===== лига: 2 дивизиона × 7 клубов, финал сезона двигает 3↑/3↓ =====
tid = league.create_league_season("Сезон-1", n_divisions=2, rounds=1)
divs = league.tournament_divisions(tid)
clubs = {}
for d_i, d in enumerate(divs):
    clubs[d["id"]] = [league.create_club(f"Клуб {d_i + 1}-{k}", None, d["id"], 20_000_000) for k in range(7)]
PLAYER_TG, ADMIN_TG, NOCLUB_TG = 917002, 917001, 917003
ex("INSERT INTO players (username, telegram_id, game_nickname, goals_scored, wins) VALUES ('champ', ?, 'Champ', 12, 3)",
   (PLAYER_TG,))
ex("INSERT INTO players (username, telegram_id, game_nickname) VALUES ('boss', ?, 'Boss')", (ADMIN_TG,))
pid = q("SELECT id FROM players WHERE telegram_id=?", (PLAYER_TG,))[0]["id"]
admin_pid = q("SELECT id FROM players WHERE telegram_id=?", (ADMIN_TG,))[0]["id"]
d1, d2 = divs[0]["id"], divs[1]["id"]
my_club = clubs[d2][0]           # чемпион 2-го дивизиона → после финала уедет в 1-й
league.assign_club_owner(my_club, pid)
league.assign_club_owner(clubs[d1][0], admin_pid)

c = db.db()
for d in (d1, d2):
    league.generate_league_calendar(tid, d)
# первый клуб дивизиона выигрывает всё, остальные — ничьи
for m in c.execute("SELECT * FROM matches WHERE tournament_id=?", (tid,)).fetchall():
    leaders = (clubs[d1][0], clubs[d2][0])
    s1, s2 = (2, 0) if m["home_club_id"] in leaders else (0, 2) if m["away_club_id"] in leaders else (1, 1)
    c.execute("UPDATE matches SET score1=?, score2=?, status='confirmed' WHERE id=?", (s1, s2, m["id"]))
c.commit()
c.close()
fin = league.finalize_season(tid)
check("финал сезона выполнен", fin.get("ok") is True, str(fin)[:200])
check("чемпион 2-го дивизиона уже переехал в 1-й (таблица по division_id сломана)",
      q("SELECT division_id FROM clubs WHERE id=?", (my_club,))[0]["division_id"] == d1)

# кубок ЛЧ: финал выиграл наш клуб
cup = league.create_cup("Кубок осени", "ucl")
ex("INSERT INTO ties (tournament_id, stage, club_a_id, club_b_id, wins_a, wins_b, winner_club_id) "
   "VALUES (?, 'final', ?, ?, 2, 1, ?)", (cup, my_club, clubs[d1][1], my_club))

# ===== юзеры и ставки =====
for tg, name, bal, won in [(PLAYER_TG, "champ", 422, 900), (ADMIN_TG, "boss", 3000, 100), (NOCLUB_TG, "noclub", 100, 5000)]:
    ex("INSERT INTO users (telegram_id, username, first_name, balance, total_won) VALUES (?,?,?,?,?)",
       (tg, name, name, bal, won))
for i in range(25):  # массовка для топ-20
    ex("INSERT INTO users (telegram_id, username, balance, total_won) VALUES (?,?,?,?)",
       (950000 + i, f"u{i}", 1000 + i * 100, i * 10))
ex("INSERT INTO users (telegram_id, username, balance, is_frozen, freeze_reason) VALUES (959999, 'frozen', 999999, 1, 'тест')")
uid = q("SELECT id FROM users WHERE telegram_id=?", (PLAYER_TG,))[0]["id"]

c = db.db()


def bet(status, bet_type="single", odds=2.0, legs=1, amount=100):
    bid = c.insert_returning_id(
        "INSERT INTO bets (user_id, bet_type, amount, total_odds, potential_win, status) VALUES (?,?,?,?,?,?)",
        (uid, bet_type, amount, odds, int(amount * odds), status))
    for _ in range(legs):
        c.execute("INSERT INTO bet_legs (bet_id, match_id, market_code, odds, result) VALUES (?, 1, '1x2_p1', 1.7, ?)",
                  (bid, "won" if status == "won" else "lost"))


bet("won")
bet("won")
bet("won", "express", 16.2, 5)
bet("lost")
bet("void")
c.execute("INSERT INTO user_streaks (user_id, streak_days, last_bonus_date) VALUES (?, 2, ?)",
          (uid, date.today().isoformat()))
c.execute("INSERT INTO balance_history (user_id, delta, reason) VALUES (?, 110, 'бонус за серию (7 дн.)')", (uid,))
c.execute("INSERT INTO promo_codes (code, amount) VALUES ('SMOKE', 50)")
c.execute("INSERT INTO promo_activations (code_id, user_id) VALUES (1, ?)", (uid,))
c.commit()
c.close()


def sign(user_id: int) -> str:
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "T"})}
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


async def run_api():
    from miniapp.server import build_app
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    H = lambda tg: {"X-Telegram-Init-Data": sign(tg)}  # noqa: E731

    async def get(path, tg):
        r = await client.get(path, headers=H(tg))
        return r.status, await r.json()

    async def post(path, tg, body=None):
        r = await client.post(path, headers=H(tg), data=json.dumps(body or {}))
        return r.status, await r.json()

    # ===== достижения =====
    st, data = await get("/api/achievements", PLAYER_TG)
    A = {a["code"]: a for a in data.get("achievements", [])}
    check("API достижений: 200 и весь каталог", st == 200 and len(A) == len(cat), str(st))
    unlocked = {k for k, a in A.items() if a["is_unlocked"]}
    expect_on = {"first_bet", "first_win", "streak_3", "express_3", "express_5", "express_x5", "express_x15",
                 "login_3", "login_7", "promo_first", "club_owner", "goals_10", "season_champion", "cup_winner"}
    expect_off = {"bets_10", "streak_5", "express_x50", "login_30", "balance_5k", "roi_plus", "transfer_first",
                  "match_wins_10"}
    check("открыты по состоянию БД", expect_on <= unlocked, str(sorted(expect_on - unlocked)))
    check("не открыты раньше времени", not (expect_off & unlocked), str(sorted(expect_off & unlocked)))
    check("прогресс: 4 прогноза из 10 (void не считается)", A["bets_10"]["progress"] == 4 and A["bets_10"]["target"] == 10)
    check("серия входов — максимум из истории бонусов", A["login_30"]["progress"] == 7)
    check("summary: к получению = открытые", data["summary"]["claimable"] == len(unlocked))

    # ===== награда =====
    before = q("SELECT balance, xp, level FROM users WHERE id=?", (uid,))[0]
    st, r = await post("/api/achievements/season_champion/claim", PLAYER_TG)
    after = q("SELECT balance, xp, level FROM users WHERE id=?", (uid,))[0]
    check("claim: +5000 дыма и +2000 XP", st == 200 and not r["already_claimed"]
          and after["balance"] == before["balance"] + 5000 and after["xp"] == before["xp"] + 2000, str(r))
    step = appsettings.setting_int("level_xp_step", 500)
    exp_level = before["level"]
    while after["xp"] >= exp_level * step:
        exp_level += 1
    check("claim: уровень по формуле level_xp_step", after["level"] == exp_level == r["level"], f"{after} {exp_level}")
    st, r2 = await post("/api/achievements/season_champion/claim", PLAYER_TG)
    again = q("SELECT balance, xp FROM users WHERE id=?", (uid,))[0]
    check("повторный claim идемпотентен — не платит", st == 200 and r2["already_claimed"] and again == {
        "balance": after["balance"], "xp": after["xp"]})
    ach.claim(uid, "season_champion")
    check("в balance_history ровно одна выплата",
          len(q("SELECT id FROM balance_history WHERE user_id=? AND reason LIKE 'достижение%'", (uid,))) == 1)
    st, _ = await post("/api/achievements/balance_100k/claim", PLAYER_TG)
    check("claim закрытого → 400", st == 400)
    st, _ = await post("/api/achievements/nope/claim", PLAYER_TG)
    check("claim неизвестного → 404", st == 404)
    st, data = await get("/api/achievements", PLAYER_TG)
    A = {a["code"]: a for a in data["achievements"]}
    check("после награды баланс 5422 → открыт «5 000 дыма»", A["balance_5k"]["is_unlocked"] == 1)
    check("забранное помечено is_claimed", A["season_champion"]["is_claimed"] == 1 and A["cup_winner"]["is_claimed"] == 0)

    # ===== зал славы =====
    st, h = await get("/api/leaderboard/hall?tab=balance", NOCLUB_TG)
    L = h["leaders"]
    vals = [x["value"] for x in L]
    check("зал: топ-20 по балансу, по убыванию", st == 200 and len(L) == 20 and vals == sorted(vals, reverse=True))
    check("зал: замороженных нет", all(x["name"] != "frozen" for x in L))
    check("зал: лидер — чемпион с наградой", L[0]["name"] == "champ" and L[0]["team_name"] == "Клуб 2-0")
    check("зал: своя позиция вне топа", h["me"] and h["me"]["value"] == 100 and h["me"]["position"] > 20, str(h["me"]))
    st, h = await get("/api/leaderboard/hall?tab=won", PLAYER_TG)
    check("зал: вкладка «выиграно»", h["leaders"][0]["name"] == "noclub" and h["leaders"][0]["value"] == 5000)
    st, h = await get("/api/leaderboard/hall?tab=champions", PLAYER_TG)
    ch = h["champions"]
    league_ch = {x["title"]: x["club"] for x in ch if x["kind"] == "league"}
    check("чемпионы дивизионов после 3↑/3↓ — верные", league_ch == {
        "Сезон-1 · Дивизион 1": "Клуб 1-0", "Сезон-1 · Дивизион 2": "Клуб 2-0"}, str(league_ch))
    check("обладатель кубка ЛЧ", any(x["kind"] == "cup" and x["club"] == "Клуб 2-0" and x["title"].startswith("ЛЧ")
                                     for x in ch), str(ch))
    check("чемпион с владельцем", any(x["owner"] == "champ" for x in ch))
    st, _ = await get("/api/leaderboard/hall?tab=xxx", PLAYER_TG)
    check("зал: неизвестная вкладка → 400", st == 400)
    ach.evaluate(uid)
    check("снимок зала не дублируется", len(q("SELECT id FROM hall_of_fame")) == 3)

    # ===== тренировки =====
    check("лимит по умолчанию 10 (bot_settings)", tr.weekly_limit() == 10 and "training_limit_per_week" in appsettings.DEFAULTS)
    my_card = ex("INSERT INTO club_cards (club_id, name, position, rating) VALUES (?, 'Мбаппе', 'ФРВ', 91)", (my_club,))
    alien = ex("INSERT INTO club_cards (club_id, name, position, rating) VALUES (?, 'Чужой', 'ЦП', 80)", (clubs[d1][2],))
    st, t = await get("/api/training", PLAYER_TG)
    check("тренировки: обзор клуба", st == 200 and t["club_id"] == my_club and t["week_total"] == 0
          and t["cards"][0]["name"] == "Мбаппе")
    st, t = await post("/api/training", PLAYER_TG, {"card_id": my_card, "kind": "ovr", "count": 4})
    check("запись в пределах лимита — без нарушения", st == 200 and t["entry"]["violation_id"] is None and t["week_total"] == 4)
    st, t = await post("/api/training", PLAYER_TG, {"card_id": my_card, "kind": "rank", "count": 7})
    vid = t["entry"]["violation_id"]
    check("превышение лимита → нарушение (запись сохранена)", st == 200 and vid and t["week_total"] == 11)
    st, t = await post("/api/training", PLAYER_TG, {"card_id": my_card, "kind": "skill", "count": 1})
    check("то же нарушение недели обновляется", t["entry"]["violation_id"] == vid
          and q("SELECT total FROM training_violations WHERE id=?", (vid,))[0]["total"] == 12
          and len(q("SELECT id FROM training_violations")) == 1)
    check("счётчик и уровень карточки", t["cards"][0]["total"] == 12 and t["cards"][0]["level"] == 2)
    st, _ = await post("/api/training", PLAYER_TG, {"card_id": alien, "kind": "ovr", "count": 1})
    check("чужая карточка → 400", st == 400)
    st, _ = await post("/api/training", PLAYER_TG, {"card_id": my_card, "kind": "ovr", "count": 0})
    check("количество 0 → 400", st == 400)
    st, _ = await post("/api/training", PLAYER_TG, {"card_id": my_card, "kind": "buy", "count": 1})
    check("неизвестный тип → 400", st == 400)
    st, r = await post("/api/training", NOCLUB_TG, {"card_id": my_card, "kind": "ovr", "count": 1})
    check("без клуба → 400 NO_CLUB", st == 400 and r["code"] == "NO_CLUB")
    nxt = tr.add_entry(q("SELECT * FROM users WHERE id=?", (uid,))[0], my_card, "ovr", 1,
                       today=date.today() + timedelta(days=7))
    check("новая неделя — счётчик с нуля", nxt["week_total"] == 1 and nxt["violation_id"] is None)

    # ===== админка нарушений =====
    st, _ = await get("/api/admin/training/violations", PLAYER_TG)
    check("нарушения: игрок → 403", st == 403)
    st, _ = await post(f"/api/admin/training/violations/{vid}/resolve", PLAYER_TG, {"action": "penalized"})
    check("resolve: игрок → 403", st == 403)
    st, v = await get("/api/admin/training/violations", ADMIN_TG)
    check("нарушения: админ видит открытое", st == 200 and v["violations"][0]["status"] == "open"
          and v["violations"][0]["club_name"] == "Клуб 2-0" and len(v["violations"][0]["entries"]) == 3)
    st, r = await post(f"/api/admin/training/violations/{vid}/resolve", ADMIN_TG,
                       {"action": "penalized", "note": "штраф 1М"})
    check("админ: наказан", st == 200 and q("SELECT status, admin_note FROM training_violations WHERE id=?", (vid,))[0]
          == {"status": "penalized", "admin_note": "штраф 1М"})
    st, _ = await post(f"/api/admin/training/violations/{vid}/resolve", ADMIN_TG, {"action": "resolved"})
    check("повторное закрытие → 400", st == 400)
    check("игрок уведомлён", any("наказан" in n["text"] for n in q("SELECT text FROM notifications WHERE user_id=?", (uid,))))
    check("аудит записан", len(q("SELECT id FROM tournament_audit_log WHERE action='training_violation'")) == 1)
    st, t = await post("/api/training", PLAYER_TG, {"card_id": my_card, "kind": "ovr", "count": 1})
    check("после закрытия новое превышение — новое нарушение", t["entry"]["violation_id"] not in (None, vid))

    # ===== заморозка =====
    ex("UPDATE users SET is_frozen=1, freeze_reason='тест' WHERE telegram_id=?", (NOCLUB_TG,))
    st, _ = await get("/api/achievements", NOCLUB_TG)
    check("замороженный → 403", st == 403)
    await client.close()


asyncio.run(run_api())
print("\nGATE 17:", "OK" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
