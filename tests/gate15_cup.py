"""ГЕЙТ 15: кубок — автопрогресс сетки r16→qf→sf→final, рынки новых игр и серий,
расчёт tie-ставок, идемпотентная перефинализация, пересборка пары при смене победителя
(и предупреждение, если следующая серия уже играется), пассы (bye), призовые кубка
ровно один раз, /api/cups; политика баланса при пересчёте купона (clamp 0 / минус)."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bot"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["DB_PATH"] = "/tmp/gate15.db"
if os.path.exists("/tmp/gate15.db"):
    os.remove("/tmp/gate15.db")

import config  # noqa: E402
import db  # noqa: E402
import league  # noqa: E402
import markets  # noqa: E402
import results  # noqa: E402
import bets_engine  # noqa: E402
import settings as appsettings  # noqa: E402

db.init_db()
fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


def q1(sql, args=()):
    c = db.db()
    r = c.execute(sql, args).fetchone()
    c.close()
    return dict(r) if r else None


def qa(sql, args=()):
    c = db.db()
    r = [dict(x) for x in c.execute(sql, args).fetchall()]
    c.close()
    return r


_uid = [5000]


def user(balance=1000):
    _uid[0] += 1
    c = db.db()
    c.execute("INSERT INTO users (telegram_id, username, balance) VALUES (?,?,?)",
              (_uid[0], f"u{_uid[0]}", balance))
    c.commit()
    row = dict(c.execute("SELECT * FROM users WHERE telegram_id=?", (_uid[0],)).fetchone())
    c.close()
    return row


def bal(u):
    return q1("SELECT balance FROM users WHERE id=?", (u["id"],))["balance"]


def bet(bid):
    return q1("SELECT * FROM bets WHERE id=?", (bid,))


def mk(mid, code):
    return {"match_id": mid, "market_code": code}


def ties(cup, stage=None):
    sql, args = "SELECT * FROM ties WHERE tournament_id=?", [cup]
    if stage:
        sql += " AND stage=?"
        args.append(stage)
    return qa(sql + " ORDER BY bracket_pos, id", args)


def game(tie_id, no):
    return q1("SELECT * FROM matches WHERE tie_id=? AND game_in_tie=? AND status!='cancelled' "
              "ORDER BY id DESC", (tie_id, no))


def score_for(tie, g, a_wins: bool):
    """Счёт игры, где выигрывает клуб А (или Б)."""
    a_home = g["home_club_id"] == tie["club_a_id"]
    return (1, 0) if a_home == a_wins else (0, 1)


def play(tie_id, pattern="aa"):
    """Сыграть серию по шаблону ('aa' = 2:0 А, 'aba' = 2:1 А, 'bb' = 0:2 и т.д.)."""
    for i, side in enumerate(pattern, 1):
        t = q1("SELECT * FROM ties WHERE id=?", (tie_id,))
        g = game(tie_id, i)
        results.finalize_match(g["id"], *score_for(t, g, side == "a"), None, None, [], actor="manual")
    return q1("SELECT * FROM ties WHERE id=?", (tie_id,))


clubs = [league.create_club(f"Клуб-{i}", None, None, 1_000_000) for i in range(1, 21)]

# ===== 1. кубок на 16: r16 → qf → sf → final сам =====
cup = league.create_cup("Кубок-15", "ucl")
r16 = league.create_cup_bracket(cup, clubs[:16])
t16 = ties(cup, "r16")
check("1 жеребьёвка: 8 серий 1/8, позиции 0..7",
      len(r16) == 8 and [t["bracket_pos"] for t in t16] == list(range(8)))
g1 = game(t16[0]["id"], 1)
codes = {r["code"] for r in qa("SELECT code FROM markets WHERE match_id=?", (g1["id"],))}
check("1 рынки на игре 1: 1х2 + серия", {"1x2_p1", "tie_2_0", "tie_0_2"} <= codes, str(sorted(codes)))

u20, u02, u21, uex = user(), user(), user(), user()
b20 = bets_engine.place_bet(u20, 100, [mk(g1["id"], "tie_2_0")])["bet_id"]
b02 = bets_engine.place_bet(u02, 100, [mk(g1["id"], "tie_0_2")])["bet_id"]
b21 = bets_engine.place_bet(u21, 100, [mk(g1["id"], "tie_2_1")])["bet_id"]
bex = bets_engine.place_bet(uex, 100, [mk(g1["id"], "tie_2_0"), mk(game(t16[1]["id"], 1)["id"], "tb25")])["bet_id"]

for i, t in enumerate(t16[:-1]):
    play(t["id"], "aa" if i % 2 == 0 else "aba")
check("1 пока не все серии решены — 1/4 нет", not ties(cup, "qf"))
g2 = game(t16[0]["id"], 2)
check("1 игра 2 серии получила рынки", q1("SELECT COUNT(*) n FROM markets WHERE match_id=?", (g2["id"],))["n"] > 0)
check("1 tie_2_0 выиграл, tie_0_2 / tie_2_1 проиграли",
      bet(b20)["status"] == "won" and bet(b02)["status"] == "lost" and bet(b21)["status"] == "lost",
      f"{bet(b20)['status']}/{bet(b02)['status']}/{bet(b21)['status']}")
check("1 экспресс с tie-ногой проигран (tb25 на 1:0)", bet(bex)["status"] == "lost", bet(bex)["status"])
check("1 выплата tie_2_0 на баланс", bal(u20) == 900 + bet(b20)["potential_win"])

play(t16[-1]["id"], "bb")
tqf = ties(cup, "qf")
w16 = [q1("SELECT winner_club_id w FROM ties WHERE id=?", (t["id"],))["w"] for t in t16]
check("1 после последней серии — 1/4: 4 серии по сетке (1–2, 3–4, …)",
      len(tqf) == 4 and all((x["club_a_id"], x["club_b_id"]) == (w16[2 * k], w16[2 * k + 1])
                            for k, x in enumerate(tqf)), str([(x["club_a_id"], x["club_b_id"]) for x in tqf]))
qf_g1 = game(tqf[0]["id"], 1)
qf_codes = {r["code"] for r in qa("SELECT code FROM markets WHERE match_id=?", (qf_g1["id"],))}
check("1 рынки 1/4 (игра + серия)", "1x2_p1" in qf_codes and "tie_2_1" in qf_codes)

# идемпотентность: та же финализация и лишние синхронизации не плодят стадий
last = game(t16[-1]["id"], 2)
results.finalize_match(last["id"], last["score1"], last["score2"], None, None, [], actor="manual")
league.sync_cup(cup)
check("1 повтор/синк не плодит серий", len(ties(cup, "qf")) == 4 and league.draw_next_cup_stage(cup) == 0
      and q1("SELECT COUNT(*) n FROM matches WHERE tie_id=?", (tqf[0]["id"],))["n"] == 1)

for t in tqf:
    play(t["id"], "aa")
tsf = ties(cup, "sf")
check("1 1/2 создан", len(tsf) == 2, str(len(tsf)))
for t in tsf:
    play(t["id"], "abb")
tfin = ties(cup, "final")
check("1 финал создан", len(tfin) == 1)
check("1 кубок ещё идёт", league.get_tournament(cup)["stage"] == "playoff")
fin = play(tfin[0]["id"], "aa")
res = q1("SELECT * FROM cup_results WHERE tournament_id=?", (cup,))
check("1 финал решён → кубок finished, победитель записан",
      league.get_tournament(cup)["stage"] == "finished" and res and res["winner_club_id"] == fin["winner_club_id"]
      and res["prize_paid"] == 0, str(res))
txt = league.format_bracket(cup)
check("1 /bracket текст", "Финал" in txt and "Победитель" in txt and "1/8 финала" in txt)
results.finalize_match(game(tfin[0]["id"], 2)["id"], *[game(tfin[0]["id"], 2)[k] for k in ("score1", "score2")],
                       None, None, [], actor="manual")
check("1 повтор финала не дублирует cup_results",
      q1("SELECT COUNT(*) n FROM cup_results WHERE tournament_id=?", (cup,))["n"] == 1)

# ===== 2. смена победителя: пересборка пары следующей стадии =====
cup2 = league.create_cup("Кубок-15-2", "uel")
league.create_cup_bracket(cup2, clubs[16:20])
s1, s2 = ties(cup2, "sf")
play(s1["id"], "aba")
play(s2["id"], "aa")
f0 = ties(cup2, "final")[0]
wa = q1("SELECT * FROM ties WHERE id=?", (s1["id"],))["club_a_id"]
check("2 финал: победители 1/2", (f0["club_a_id"], f0["club_b_id"]) == (wa, s2["club_a_id"]))
uf1, uft = user(), user()
fg = game(f0["id"], 1)
bf1 = bets_engine.place_bet(uf1, 100, [mk(fg["id"], "1x2_p1")])["bet_id"]
bft = bets_engine.place_bet(uft, 100, [mk(fg["id"], "tie_2_0")])["bet_id"]
# правка игры 3: серия 1:2 — победитель сменился, финал ещё не играли → пересборка на месте
g3 = game(s1["id"], 3)
t1 = q1("SELECT * FROM ties WHERE id=?", (s1["id"],))
results.finalize_match(g3["id"], *score_for(t1, g3, False), None, None, [], actor="dispute")
f1 = ties(cup2, "final")
check("2 пара финала пересобрана (та же серия, новый клуб А)",
      len(f1) == 1 and f1[0]["id"] == f0["id"] and f1[0]["club_a_id"] == s1["club_b_id"], str(f1))
check("2 старая игра финала отменена, новая с рынками",
      q1("SELECT status FROM matches WHERE id=?", (fg["id"],))["status"] == "cancelled"
      and q1("SELECT COUNT(*) n FROM markets WHERE match_id=?", (game(f0["id"], 1)["id"],))["n"] > 4)
check("2 ставки на старую пару — void, деньги вернулись",
      bet(bf1)["status"] == "void" and bet(bft)["status"] == "void" and bal(uf1) == 1000 and bal(uft) == 1000,
      f"{bet(bf1)['status']}/{bet(bft)['status']} {bal(uf1)}/{bal(uft)}")
# правка игры 2 в пользу А: серия 2:0 А — финал (ещё не играли) пересобирается обратно
g2s = game(s1["id"], 2)
t1 = q1("SELECT * FROM ties WHERE id=?", (s1["id"],))
results.finalize_match(g2s["id"], *score_for(t1, g2s, True), None, None, [], actor="dispute")
t1 = q1("SELECT * FROM ties WHERE id=?", (s1["id"],))
check("2 серия снова решена 2:0 А — финал вернулся к А",
      t1["winner_club_id"] == t1["club_a_id"] and ties(cup2, "final")[0]["club_a_id"] == t1["club_a_id"])
# финал начали играть → смена победителя 1/2 не трогает пару, пишет предупреждение
ff = ties(cup2, "final")[0]
play(ff["id"], "a")
g1s = game(s1["id"], 1)
t1 = q1("SELECT * FROM ties WHERE id=?", (s1["id"],))
results.finalize_match(g1s["id"], *score_for(t1, g1s, False), None, None, [], actor="dispute")
g2s = game(s1["id"], 2)
results.finalize_match(g2s["id"], *score_for(t1, g2s, False), None, None, [], actor="dispute")
t1 = q1("SELECT * FROM ties WHERE id=?", (s1["id"],))
ff2 = ties(cup2, "final")[0]
warn = q1("SELECT COUNT(*) n FROM tournament_audit_log WHERE tournament_id=? AND action='cup_warning'", (cup2,))["n"]
check("2 финал уже играется — пара оставлена, предупреждение в аудите",
      t1["winner_club_id"] == t1["club_b_id"] and ff2["club_a_id"] == ff["club_a_id"] and warn == 1,
      f"warn={warn}")
league.sync_cup(cup2)
check("2 повторный синк не дублирует предупреждение",
      q1("SELECT COUNT(*) n FROM tournament_audit_log WHERE tournament_id=? AND action='cup_warning'", (cup2,))["n"] == 1)

# ===== 3. пассы (bye) =====
cup3 = league.create_cup("Кубок-15-3", "uecl")
br = league.create_cup_bracket(cup3, clubs[:6])
t3 = ties(cup3, "qf")
byes = [t for t in t3 if t["club_b_id"] is None]
check("3 6 клубов → 1/4 из 4 серий, 2 пасса с победителем без игр",
      len(t3) == 4 and len(byes) == 2 and all(t["winner_club_id"] == t["club_a_id"] for t in byes)
      and all(q1("SELECT COUNT(*) n FROM matches WHERE tie_id=?", (t["id"],))["n"] == 0 for t in byes)
      and len({x for t in t3 for x in (t["club_a_id"], t["club_b_id"]) if x}) == 6)
check("3 пассы не соседствуют (в паре 1/2 максимум один)",
      all(not (t3[2 * k]["club_b_id"] is None and t3[2 * k + 1]["club_b_id"] is None) for k in range(2)))
for t in t3:
    if t["club_b_id"] is not None:
        play(t["id"], "aa")
t3sf = ties(cup3, "sf")
w3 = [q1("SELECT winner_club_id w FROM ties WHERE id=?", (t["id"],))["w"] for t in t3]
check("3 1/2 после пассов по сетке", len(t3sf) == 2 and (t3sf[0]["club_a_id"], t3sf[0]["club_b_id"]) == (w3[0], w3[1]))
cup4 = league.create_cup("Кубок-15-4", "uecl")
t4 = league.create_cup_bracket(cup4, clubs[:3])
real = [t for t in ties(cup4, "sf") if t["club_b_id"] is not None]
play(real[0]["id"], "bb")
check("3 3 клуба: 1/2 с пассом → финал после одной серии", len(ties(cup4, "final")) == 1)
play(ties(cup4, "final")[0]["id"], "aa")
check("3 кубок из 3 клубов завершён", league.get_tournament(cup4)["stage"] == "finished")

# старый кубок: закрыт прежним finalize_season (призовые выплачены), cup_results нет
cup5 = league.create_cup("Кубок-15-старый", "ucl")
league.create_cup_bracket(cup5, clubs[:2])
c = db.db()
c.execute("UPDATE tournaments SET stage='finished' WHERE id=?", (cup5,))
c.commit()
c.close()
play(ties(cup5, "final")[0]["id"], "aa")
check("3 старый закрытый кубок: помечен как оплаченный",
      q1("SELECT prize_paid FROM cup_results WHERE tournament_id=?", (cup5,))["prize_paid"] == 1)

# ===== 4. finalize_season: кубок платится ровно один раз =====
prize = appsettings.setting_int("prize_cup_winner", 50_000_000)
season = league.create_league_season("Сезон-15", 1, "manual", 1, 3)
div = league.tournament_divisions(season)[0]
lc = [league.create_club(f"Лига-{i}", None, div["id"], 0) for i in range(4)]
league.generate_league_calendar(season, div["id"])
win1 = q1("SELECT winner_club_id w FROM cup_results WHERE tournament_id=?", (cup,))["w"]
win4 = q1("SELECT winner_club_id w FROM cup_results WHERE tournament_id=?", (cup4,))["w"]
before = {cid: q1("SELECT budget FROM clubs WHERE id=?", (cid,))["budget"] for cid in set(clubs)}
r = league.finalize_season(season, 1)
paid_cups = sorted(x["cup"] for x in r.get("cup_winners", []))
check("4 финал сезона платит завершённые кубки (не старый оплаченный)",
      r.get("ok") and "Кубок-15" in paid_cups and "Кубок-15-4" in paid_cups and "Кубок-15-старый" not in paid_cups,
      str(paid_cups))
after = {cid: q1("SELECT budget FROM clubs WHERE id=?", (cid,))["budget"] for cid in set(clubs)}
exp1 = prize * (1 + (win1 == win4))
check("4 победитель получил призовые один раз", after[win1] - before[win1] == exp1, f"{after[win1] - before[win1]}")
season2 = league.create_league_season("Сезон-15-2", 1, "manual", 1, 3)
div2 = league.tournament_divisions(season2)[0]
league.create_club("Лига2-1", None, div2["id"], 0)
league.create_club("Лига2-2", None, div2["id"], 0)
r2 = league.finalize_season(season2, 1)
check("4 следующий сезон не платит кубки повторно",
      r2.get("ok") and not r2["cup_winners"] and q1("SELECT budget FROM clubs WHERE id=?", (win1,))["budget"] == after[win1],
      str(r2.get("cup_winners")))

# ===== 5. пересчёт купона: баланс не уходит в минус (по умолчанию) =====
lg = league.create_league_season("Лига-баланс", 1, "manual", 1, 3)
ldiv = league.tournament_divisions(lg)[0]
for i in range(4):
    league.create_club(f"Баланс-{i}", None, ldiv["id"], 0)
league.generate_league_calendar(lg, ldiv["id"])
markets.refresh_tour(lg, 1)
m1, m2 = [m["id"] for m in league.tour_matches(lg, 1)][:2]
check("5 дефолт resettle_allow_negative=0", appsettings.get_setting("resettle_allow_negative") == "0")
uc = user(1000)
bc = bets_engine.place_bet(uc, 500, [mk(m1, "1x2_p1")])["bet_id"]
results.finalize_match(m1, 2, 0, None, None, [], actor="manual")
won = bet(bc)["potential_win"]
check("5 купон выигран", bet(bc)["status"] == "won" and bal(uc) == 500 + won)
c = db.db()
c.execute("UPDATE users SET balance=30 WHERE id=?", (uc["id"],))  # игрок успел потратить выигрыш
c.commit()
c.close()
results.finalize_match(m1, 0, 2, None, None, [], actor="dispute")
short = won - 30
hist = q1("SELECT * FROM balance_history WHERE user_id=? AND reason LIKE ? ORDER BY id DESC",
          (uc["id"], f"пересчёт купона #{bc}%"))
check("5 clamp: баланс 0, списано сколько было", bal(uc) == 0 and bet(bc)["status"] == "lost", str(bal(uc)))
check("5 clamp: история с фактической суммой и недостачей",
      hist and hist["delta"] == -30 and hist["reason"] == f"пересчёт купона #{bc} (не хватило {short})", str(hist))
check("5 clamp: аудит + уведомление",
      q1("SELECT COUNT(*) n FROM tournament_audit_log WHERE action='resettle_shortfall' AND details LIKE ?",
         (f"купон #{bc} %",))["n"] == 1
      and q1("SELECT COUNT(*) n FROM notifications WHERE user_id=? AND kind='app' AND text LIKE ?",
             (uc["id"], f"%#{bc}%не хватило {short}%"))["n"] == 1)
results.finalize_match(m1, 2, 0, None, None, [], actor="dispute")
check("5 clamp: повторный выигрыш удерживает недостачу (итог = одна выплата)",
      bet(bc)["status"] == "won" and bal(uc) == 30, str(bal(uc)))
results.finalize_match(m1, 0, 2, None, None, [], actor="dispute")
check("5 clamp: и снова проигрыш — списано до нуля", bal(uc) == 0, str(bal(uc)))

appsettings.set_setting("resettle_allow_negative", "1")
un = user(1000)
bn = bets_engine.place_bet(un, 500, [mk(m2, "1x2_p1")])["bet_id"]
results.finalize_match(m2, 3, 1, None, None, [], actor="manual")
wn = bet(bn)["potential_win"]
c = db.db()
c.execute("UPDATE users SET balance=30 WHERE id=?", (un["id"],))
c.commit()
c.close()
results.finalize_match(m2, 1, 3, None, None, [], actor="dispute")
check("5 allow_negative=1: баланс уходит в минус, причина прежняя",
      bal(un) == 30 - wn and q1("SELECT COUNT(*) n FROM balance_history WHERE reason=?",
                                (f"пересчёт купона #{bn}",))["n"] == 1, str(bal(un)))
appsettings.set_setting("resettle_allow_negative", "0")


# ===== 6. /api/cups =====
def sign(user_id: int) -> str:
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps({"id": user_id, "first_name": "T"})}
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    secret = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(secret, dcs.encode(), hashlib.sha256).hexdigest()
    return urlencode(pairs)


async def run_api():
    from aiohttp.test_utils import TestClient, TestServer
    from miniapp.server import build_app
    client = TestClient(TestServer(build_app()))
    await client.start_server()
    try:
        r = await client.get("/api/cups")
        check("6 /api/cups без initData — 401", r.status == 401, str(r.status))
        h = {"X-Telegram-Init-Data": sign(777001)}
        r = await client.get("/api/cups", headers=h)
        data = await r.json()
        byid = {x["id"]: x for x in data.get("cups", [])}
        p = byid.get(cup)
        check("6 список кубков", r.status == 200 and cup in byid and cup3 in byid, str(r.status))
        check("6 стадии по порядку", p and [s["code"] for s in p["stages"]] == ["r16", "qf", "sf", "final"])
        t0 = p["stages"][0]["ties"][0]
        check("6 серия: клубы, лого-ключ, счёт, победитель, игры",
              t0["club_a"]["name"] and "logo" in t0["club_a"] and t0["wins_a"] == 2
              and t0["winner_club_id"] == t0["club_a"]["id"] and len(t0["games"]) == 2
              and t0["games"][0]["score_a"] == 1 and t0["games"][0]["score_b"] == 0, json.dumps(t0)[:300])
        check("6 кубок завершён, победитель", p["finished"] and p["winner"]["id"] == win1)
        p3 = byid[cup3]
        check("6 пасс в payload", any(t["bye"] and t["club_b"] is None for t in p3["stages"][0]["ties"]))
        r = await client.get(f"/api/cups?tournament_id={cup2}", headers=h)
        d2 = await r.json()
        check("6 фильтр по турниру", [x["id"] for x in d2["cups"]] == [cup2]
              and all(g["status"] != "cancelled" for s in d2["cups"][0]["stages"] for t in s["ties"] for g in t["games"]))
    finally:
        await client.close()


asyncio.run(run_api())

print()
if fails:
    print("GATE 15: FAIL —", fails)
    sys.exit(1)
print("GATE 15: OK")
