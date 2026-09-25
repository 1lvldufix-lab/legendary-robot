"""ГЕЙТ 11: регрессии фиксов ревью — пересчёт ставок при споре/правке результата,
рынки и расчёт серий кубка (точный счёт, tie-ноги в экспрессе, повторная
финализация), финал сезона без двойных призовых, валидация цены лота, обмен
через судью, AH +1.5, таблица по турниру, окно/участник спора."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "бот"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["DB_PATH"] = "/tmp/gate11.db"
if os.path.exists("/tmp/gate11.db"):
    os.remove("/tmp/gate11.db")

import db
import league
import markets
import results
import bets_engine
import transfers

db.init_db()
fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


def user(tg_id, balance=1000):
    c = db.db()
    c.execute("INSERT OR IGNORE INTO users (telegram_id, username, balance) VALUES (?,?,?)",
              (tg_id, f"u{tg_id}", balance))
    c.commit()
    row = dict(c.execute("SELECT * FROM users WHERE telegram_id=?", (tg_id,)).fetchone())
    c.close()
    return row


def q1(sql, args=()):
    c = db.db()
    r = c.execute(sql, args).fetchone()
    c.close()
    return dict(r) if r else None


def urow(u):
    return q1("SELECT * FROM users WHERE id=?", (u["id"],))


def bet(bid):
    return q1("SELECT * FROM bets WHERE id=?", (bid,))


def budget(club_id):
    return q1("SELECT budget FROM clubs WHERE id=?", (club_id,))["budget"]


def mk(mid, code):
    return {"match_id": mid, "market_code": code}


# ===== сезон: 4 клуба, владельцы у двух =====
tid = league.create_league_season("Гейт-11", 1, "manual", 1, 3)
div = league.tournament_divisions(tid)[0]
c = db.db()
pl1 = c.insert_returning_id("INSERT INTO players (username, telegram_id) VALUES ('own1', 3001)")
pl2 = c.insert_returning_id("INSERT INTO players (username, telegram_id) VALUES ('own2', 3002)")
c.commit()
c.close()
cl1 = league.create_club("Аякс", None, div["id"], 20_000_000)
cl2 = league.create_club("Ливерпуль", None, div["id"], 20_000_000)
cl3 = league.create_club("Милан", None, div["id"], 20_000_000)
cl4 = league.create_club("Порту", None, div["id"], 20_000_000)
league.assign_club_owner(cl1, pl1)
league.assign_club_owner(cl2, pl2)
league.generate_league_calendar(tid, div["id"])
markets.refresh_tour(tid, 1)
ms = league.tour_matches(tid, 1)
m1, m2 = ms[0]["id"], ms[1]["id"]
owner_tg_m1 = 3001 if cl1 in (ms[0]["home_club_id"], ms[0]["away_club_id"]) else 3002

# ===== #1 спор меняет исход → откат выплаты и пересчёт =====
uA, uB, uC = user(4001), user(4002), user(4003)
bA = bets_engine.place_bet(uA, 100, [mk(m1, "1x2_p1")])["bet_id"]
bB = bets_engine.place_bet(uB, 100, [mk(m1, "1x2_p2")])["bet_id"]
bC = bets_engine.place_bet(uC, 50, [mk(m1, "1x2_p1"), mk(m2, "1x2_p1")])["bet_id"]
potA, potB = bet(bA)["potential_win"], bet(bB)["potential_win"]

results.finalize_match(m1, 3, 0, None, None, [], actor="manual")
check("#1 первичный расчёт: A выиграл, B проиграл", bet(bA)["status"] == "won" and bet(bB)["status"] == "lost")
check("#1 экспресс C ждёт второй матч", bet(bC)["status"] == "open")
check("#1 баланс A после выигрыша", urow(uA)["balance"] == 900 + potA, str(urow(uA)["balance"]))

check("#13 спор чужаком запрещён", results.dispute_match(m1, 4001) != "ok")
check("#13 спор участником", results.dispute_match(m1, owner_tg_m1) == "ok")
results.resolve_dispute(m1, 0, 2, None, None, [], decided_by=1)
a, b_ = urow(uA), urow(uB)
check("#1 выплата A откатана", a["balance"] == 900 and bet(bA)["status"] == "lost",
      f"bal={a['balance']} st={bet(bA)['status']}")
check("#1 статы A откатаны", a["total_won"] == 0 and a["bets_won"] == 0, f"{a['total_won']}/{a['bets_won']}")
check("#1 B пересчитан в выигрыш", bet(bB)["status"] == "won" and b_["balance"] == 900 + potB,
      f"bal={b_['balance']} pot={potB}")
check("#1 экспресс C проигран сразу (нога m1 lost)", bet(bC)["status"] == "lost")
check("#1 запись пересчёта в balance_history",
      q1("SELECT COUNT(*) n FROM balance_history WHERE reason=?", (f"пересчёт купона #{bA}",))["n"] == 1)
check("#1 заморозка снята", q1("SELECT MAX(is_frozen) f FROM bets")["f"] == 0)

results.resolve_dispute(m1, 0, 2, None, None, [], decided_by=1)
results.finalize_match(m1, 0, 2, None, None, [], actor="manual")
check("#1 идемпотентно: тот же счёт ничего не двигает",
      urow(uA)["balance"] == 900 and urow(uB)["balance"] == 900 + potB
      and q1("SELECT COUNT(*) n FROM balance_history WHERE reason LIKE 'пересчёт%'")["n"] == 1)

c = db.db()
c.execute("UPDATE matches SET played_at=datetime('now','-48 hours') WHERE id=?", (m1,))
c.commit()
c.close()
check("#13 окно спора закрыто через 48ч", "Окно" in results.dispute_match(m1, owner_tg_m1))

# ===== #12 _confirmed_games считает по players.id =====
c = db.db()
check("#12 сыгранные матчи игрока > 0", results._confirmed_games(c, owner_tg_m1) >= 1)
c.close()

# ===== #7 таблица только по своему турниру =====
other = league.create_league_season("Чужой", 1, "manual", 1, 3)
c = db.db()
c.execute("INSERT INTO matches (tournament_id, home_club_id, away_club_id, score1, score2, stage, status) "
          "VALUES (?,?,?,9,0,'group','confirmed')", (other, cl3, cl4))
c.commit()
c.close()
tbl = {r["club_id"]: r for r in league.division_standings(div["id"])}
check("#7 чужой турнир не попал в таблицу", tbl[cl3]["gf"] < 9, str(tbl[cl3]))

# ===== #2 кубок: рынки серии, точный счёт, tie-нога в экспрессе; #3 повторная финализация =====
cup = league.create_cup("Кубок-11", "ucl")
tie = league.create_cup_bracket(cup, [cl3, cl4])[0]
g1 = q1("SELECT * FROM matches WHERE tie_id=? AND game_in_tie=1", (tie["tie_id"],))
tie_mk = q1("SELECT COUNT(*) n FROM markets WHERE match_id=? AND code LIKE 'tie_%'", (g1["id"],))["n"]
check("#2a рынки серии созданы на игре 1", tie_mk == 4, str(tie_mk))


def a_wins(game):
    """Счёт игры серии, где побеждает клуб А."""
    return (1, 0) if game["home_club_id"] == tie["club_a"] else (0, 1)


def b_wins(game):
    return (0, 1) if game["home_club_id"] == tie["club_a"] else (1, 0)


uD, uE, uF = user(4004), user(4005), user(4006)
b20 = bets_engine.place_bet(uD, 20, [mk(g1["id"], "tie_2_0")])["bet_id"]
b21 = bets_engine.place_bet(uE, 20, [mk(g1["id"], "tie_2_1")])["bet_id"]
league.open_tour(tid, 2)
m_t2 = league.tour_matches(tid, 2)[0]["id"]
bF = bets_engine.place_bet(uF, 20, [mk(g1["id"], "tie_2_0"), mk(m_t2, "1x2_x")])["bet_id"]

results.finalize_match(g1["id"], *a_wins(g1), None, None, [], actor="manual")
check("#2 после игры 1 tie-ставки ждут", bet(b20)["status"] == "open" and bet(b21)["status"] == "open")
g2 = q1("SELECT * FROM matches WHERE tie_id=? AND game_in_tie=2", (tie["tie_id"],))
results.finalize_match(g2["id"], *a_wins(g2), None, None, [], actor="manual")
t = q1("SELECT * FROM ties WHERE id=?", (tie["tie_id"],))
check("#2 серия 2:0 за клубом А", t["wins_a"] == 2 and t["wins_b"] == 0 and t["winner_club_id"] == tie["club_a"])
check("#2b/#2c tie_2_0 выиграл, tie_2_1 проиграл (точный счёт)",
      bet(b20)["status"] == "won" and bet(b21)["status"] == "lost",
      f"{bet(b20)['status']}/{bet(b21)['status']}")

results.finalize_match(g2["id"], *a_wins(g2), None, None, [], actor="manual")
t = q1("SELECT * FROM ties WHERE id=?", (tie["tie_id"],))
games = q1("SELECT COUNT(*) n FROM matches WHERE tie_id=?", (tie["tie_id"],))["n"]
check("#3 повтор не удваивает wins и не плодит игр", t["wins_a"] == 2 and games == 2,
      f"wins_a={t['wins_a']} games={games}")

results.finalize_match(m_t2, 1, 1, None, None, [], actor="manual")
bf = bet(bF)
check("#2d экспресс с tie-ногой выигран полным кэфом (не void)",
      bf["status"] == "won" and bf["potential_win"] == min(int(20 * bf["total_odds"]), 10000),
      f"{bf['status']} {bf['potential_win']} x{bf['total_odds']}")

d_bal = urow(uD)["balance"]
paid20 = bet(b20)["potential_win"]
results.finalize_match(g2["id"], *b_wins(g2), None, None, [], actor="manual")
t = q1("SELECT * FROM ties WHERE id=?", (tie["tie_id"],))
g3 = q1("SELECT * FROM matches WHERE tie_id=? AND game_in_tie=3", (tie["tie_id"],))
check("#3 правка игры 2 → серия 1:1 без победителя, есть игра 3",
      t["wins_a"] == 1 and t["wins_b"] == 1 and t["winner_club_id"] is None and g3 is not None, str(t))
check("#1/#2 tie_2_0 переоткрыт с откатом выплаты",
      bet(b20)["status"] == "open" and urow(uD)["balance"] == d_bal - paid20,
      f"{bet(b20)['status']} {urow(uD)['balance']}")
results.finalize_match(g3["id"], *a_wins(g3), None, None, [], actor="manual")
check("#2c после 2:1 — tie_2_1 выиграл, tie_2_0 проиграл",
      bet(b21)["status"] == "won" and bet(b20)["status"] == "lost",
      f"{bet(b21)['status']}/{bet(b20)['status']}")

# ===== #11 AH +1.5 гостей = дополнение AH −1.5 хозяев =====
p = markets.probabilities(1100, 950)
check("#11 ah_a15 + ah_h15 = 1", abs(p["ah_a15"] + p["ah_h15"] - 1) < 1e-9, f"{p['ah_a15']:.3f}")

# ===== #6 цена лота и обмен через судью =====
c = db.db()
card1 = c.insert_returning_id("INSERT INTO club_cards (club_id, name, position, rating) VALUES (?,?,?,?)",
                              (cl1, "Игрок-1", "ST", 80))
card2 = c.insert_returning_id("INSERT INTO club_cards (club_id, name, position, rating) VALUES (?,?,?,?)",
                              (cl2, "Игрок-2", "CB", 78))
c.commit()
c.close()
for bad in (0, -5, "abc", None):
    try:
        transfers.create_lot(cl1, card1, "fix", bad)
        check(f"#6 цена {bad!r} отклонена", False)
    except transfers.TransferError as e:
        check(f"#6 цена {bad!r} отклонена", e.code == "BAD_PRICE")
try:
    transfers.create_lot(cl1, card1, "auction", 100, "дорого")
    check("#6 кривой выкуп отклонён", False)
except transfers.TransferError as e:
    check("#6 кривой выкуп отклонён", e.code == "BAD_PRICE")

big = transfers.threshold() + 1_000_000
ex = transfers.propose_exchange(cl1, cl2, card1, card2, big, 3001)["transfer_id"]
try:
    transfers.judge_decide(ex, True, 1)
    check("#6 судья не одобряет обмен до согласия", False)
except transfers.TransferError:
    check("#6 судья не одобряет обмен до согласия", True)
check("#6 крупный обмен → судье", transfers.accept_exchange(ex, 3002)["status"] == "needs_judge")
bud1, bud2 = budget(cl1), budget(cl2)
transfers.judge_decide(ex, True, 1)
own1 = q1("SELECT club_id FROM club_cards WHERE id=?", (card1,))["club_id"]
own2 = q1("SELECT club_id FROM club_cards WHERE id=?", (card2,))["club_id"]
check("#6 обмен судьёй: карточки поменялись", own1 == cl2 and own2 == cl1, f"{own1}/{own2}")
check("#6 обмен судьёй: доплату платит инициатор",
      budget(cl1) == bud1 - big and budget(cl2) == bud2 + big)

# ===== #4 финал сезона: один раз, кубок платится один раз =====
def total_budget():
    return q1("SELECT SUM(budget) s FROM clubs")["s"]


b0 = total_budget()
r1 = league.finalize_season(tid, 1)
b1 = total_budget()
check("#4 первая финализация платит", r1.get("ok") and b1 > b0 and len(r1["cup_winners"]) == 1, str(r1)[:200])
r2 = league.finalize_season(tid, 1)
check("#4 повторная финализация отклонена", bool(r2.get("error")) and total_budget() == b1)
check("#4 кубок закрыт после выплаты", league.get_tournament(cup)["stage"] == "finished")
r3 = league.finalize_season(other, 1)
check("#4 следующий сезон не платит старый кубок", r3.get("ok") and not r3["cup_winners"], str(r3)[:200])

print()
if fails:
    print("GATE 11: FAIL —", fails)
    sys.exit(1)
print("GATE 11: OK")
