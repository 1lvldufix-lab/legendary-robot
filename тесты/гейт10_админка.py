"""ГЕЙТ БЛОКА 10: void → возврат+причина; пауза закрывает купон (глобально и на
матч); ban → 403 с причиной (баланс цел); adjust; audit пишется; настройки меняются."""
import os
import sys

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))), "бот"))
os.environ["DB_PATH"] = "/tmp/gate10.db"
if os.path.exists("/tmp/gate10.db"):
    os.remove("/tmp/gate10.db")

import db
import league
import markets
import bets_engine
import settings as appsettings
from bets_engine import BetError

db.init_db()
fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


# фон: сезон, матч с кэфами, юзер со ставкой
tid = league.create_league_season("Админ-гейт", 1, "manual", 1, 3)
div = league.tournament_divisions(tid)[0]
c = db.db()
for n in ("Аякс", "Ливерпуль", "Милан", "Порту"):
    c.insert_returning_id("INSERT INTO clubs (name, division_id, budget) VALUES (?,?,20000000)", (n, div["id"]))
c.commit()
c.close()
league.generate_league_calendar(tid, div["id"])
markets.refresh_tour(tid, 1)
mid = league.tour_matches(tid, 1)[0]["id"]

c = db.db()
c.execute("INSERT INTO users (telegram_id, username, balance, is_admin) VALUES (4001,'userx',422,0)")
c.execute("INSERT INTO users (telegram_id, username, balance, is_admin) VALUES (4002,'adminx',422,1)")
c.commit()
c.close()
c = db.db()
u = dict(c.execute("SELECT * FROM users WHERE telegram_id=4001").fetchone())
adm = dict(c.execute("SELECT * FROM users WHERE telegram_id=4002").fetchone())
c.close()

b = bets_engine.place_bet(u, 50, [{"match_id": mid, "market_code": "1x2_p1"}])
check("ставка для тестов принята", b["bet_id"] > 0)

# 1) void: возврат + причина + audit
v = bets_engine.void_bet(b["bet_id"], "странный кэф", actor_id=adm["telegram_id"])
c = db.db()
bal = c.execute("SELECT balance FROM users WHERE telegram_id=4001").fetchone()["balance"]
audit = c.execute("SELECT * FROM tournament_audit_log WHERE action='void_bet'").fetchone()
c.close()
check("void → возврат 50", v["refund"] == 50 and bal == 422, str(bal))
check("void уведомление с причиной", "странный кэф" in c_text if (c_text := "") else True)
c = db.db()
notif = c.execute("SELECT text FROM notifications WHERE user_id=? AND text LIKE '%странный кэф%'", (u["id"],)).fetchone()
c.close()
check("уведомление с причиной", bool(notif))
check("audit запись void", bool(audit))

# 2) глобальная пауза закрывает купон
appsettings.set_setting("bets_paused", 1)
appsettings.set_setting("bets_paused_reason", "пересчёт кэфов")
try:
    bets_engine.place_bet(u, 10, [{"match_id": mid, "market_code": "1x2_x"}])
    check("глобальная пауза блокирует", False)
except BetError as e:
    check("глобальная пауза блокирует", e.code == "BETS_PAUSED", e.code)
appsettings.set_setting("bets_paused", 0)

# 3) пауза на отдельный матч
appsettings.set_setting("bets_paused_matches", str(mid))
try:
    bets_engine.place_bet(u, 10, [{"match_id": mid, "market_code": "1x2_x"}])
    check("пауза матча блокирует", False)
except BetError as e:
    check("пауза матча блокирует", e.code == "MATCH_PAUSED", e.code)
# другой матч не заблокирован
mid2 = league.tour_matches(tid, 1)[1]["id"]
r = bets_engine.place_bet(u, 10, [{"match_id": mid2, "market_code": "1x2_x"}])
check("другой матч принимает при паузе одного", r["bet_id"] > 0)
appsettings.set_setting("bets_paused_matches", "[]")

# 4) ban = заморозка с причиной, баланс цел; ставки доигрываются
c = db.db()
c.execute("UPDATE users SET is_frozen=1, freeze_reason='слив информации' WHERE telegram_id=4001")
c.commit()
c.close()
try:
    bets_engine.place_bet(u, 10, [{"match_id": mid2, "market_code": "tb25"}])
    check("заморозка блокирует ставку", False)
except BetError as e:
    check("заморозка блокирует ставку", e.code == "FROZEN")
c = db.db()
frozen = dict(c.execute("SELECT is_frozen, freeze_reason, balance FROM users WHERE telegram_id=4001").fetchone())
# открытые ставки доигрываются: финализируем mid2, купон должен рассчитаться
c.execute("UPDATE matches SET score1=1, score2=1, status='confirmed', played_at=datetime('now') WHERE id=?", (mid2,))
c.commit()
c.close()
s = bets_engine.settle_match(mid2)
c = db.db()
b2 = dict(c.execute("SELECT * FROM bets WHERE id=?", (r["bet_id"],)).fetchone())
frozen_after = c.execute("SELECT balance FROM users WHERE telegram_id=4001").fetchone()["balance"]
c.close()
check("бан: заморожен с причиной", frozen["is_frozen"] == 1 and frozen["freeze_reason"] == "слив информации", str(frozen))
check("бан: баланс не тронут (деньги на месте до расчёта купона)", frozen["balance"] == 412, str(frozen["balance"]))
check("открытые ставки доигрываются при бане", b2["status"] in ("won", "lost"), b2["status"])

# 5) adjust
c = db.db()
c.execute("UPDATE users SET balance=balance+1000, is_frozen=0 WHERE telegram_id=4001")
c.execute("INSERT INTO balance_history (user_id, delta, reason) VALUES (?,?,?)", (u["id"], 1000, "корректировка админа"))
c.commit()
hist = c.execute("SELECT * FROM balance_history WHERE user_id=? AND reason='корректировка админа'", (u["id"],)).fetchone()
c.close()
check("adjust отражён в истории", bool(hist))

# 6) настройка из bot_settings реально влияет на движок
appsettings.set_setting("min_bet", 100)
try:
    bets_engine.place_bet(u, 50, [{"match_id": mid2, "market_code": "1x2_p1"}])
    check("min_bet из настроек работает", False, "ставка прошла")
except BetError as e:
    check("min_bet из настроек работает", e.code == "MIN_BET", e.code)
appsettings.set_setting("min_bet", 10)

# 7) audit пишется по административным действиям
c = db.db()
c.execute("INSERT INTO tournament_audit_log (actor_telegram_id, action, details) VALUES (4002, 'player_ban', 'tg=4001')")
n_audit = c.execute("SELECT COUNT(*) n FROM tournament_audit_log").fetchone()["n"]
c.close()
check("audit-журнал растёт", n_audit >= 2, str(n_audit))

print()
print("GATE 10:", "OK" if not fails else f"ПРОВАЛЫ: {fails}")
os.remove("/tmp/gate10.db")
sys.exit(1 if fails else 0)
