"""ГЕЙТ 12: бот стартует (кириллические команды не валят PTB), команды получают
context.args, кнопки меню не сохраняются как ник, формат уведомления о расчёте
экспресса (проигрыш решает купон сразу), названия клубов с заглавной."""
import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "бот"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["DB_PATH"] = "/tmp/gate12.db"
if os.path.exists("/tmp/gate12.db"):
    os.remove("/tmp/gate12.db")

from telegram import Chat, Message, Update, User  # noqa: E402
from telegram.ext import Application  # noqa: E402

import bets_engine  # noqa: E402
import db  # noqa: E402
import league  # noqa: E402
import main as botmain  # noqa: E402
from clubs_catalog import find_club  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


# 1) все обработчики регистрируются (раньше ValueError на /сезон)
app = Application.builder().token("123456:TEST").build()
try:
    for kind, pat, fn in (*botmain.ht.HANDLERS, *botmain.hr.HANDLERS, *botmain.hd.HANDLERS, *botmain.ha.HANDLERS):
        if kind == "command":
            app.add_handler(botmain.command_handler(pat, fn))
    ok = True
except Exception as e:  # noqa: BLE001
    ok, err = False, e
check("все команды регистрируются", ok, "" if ok else repr(err))


def make_update(text: str) -> Update:
    u = User(1, "t", False)
    msg = Message(1, None, Chat(1, "private"), from_user=u, text=text)
    return Update(1, message=msg)


# 2) /спор не перехватывает /споры, аргументы доходят до обработчика
got = {}


async def fake(update, context):
    got["args"] = context.args

h_spor = botmain.command_handler("спор", fake)
check("/спор 12 2:1 матчится", bool(h_spor.check_update(make_update("/спор 12 2:1"))))
check("/споры не матчится хендлером /спор", not h_spor.check_update(make_update("/споры")))
check("/спор@bot матчится", bool(h_spor.check_update(make_update("/спор@kurilka_bot 5"))))
ctx = SimpleNamespace(args=None)
asyncio.run(botmain.command_handler("спор", fake).callback(make_update("/спор 12 2:1"), ctx))
check("context.args заполнен", got.get("args") == ["12", "2:1"], str(got.get("args")))
check("латинская команда — обычный CommandHandler",
      type(botmain.command_handler("start", fake)).__name__ == "CommandHandler")

# 3) названия клубов из каталога — с заглавной
check("каталог: Будё Глимт", find_club("будё глимт")[0] == "Будё Глимт")
check("каталог: Аль-Хиляль", find_club("аль-хиляль")[0] == "Аль-Хиляль")

# 4) проигранная нога рассчитывает экспресс сразу, текст как у оригинала
db.init_db()
t = league.create_league_season("Тест-лига")
tid = t if isinstance(t, int) else t["id"]
clubs = []
for name in ("Ницца", "Порту", "Бернли", "Будё Глимт"):
    canon, _ = find_club(name)
    clubs.append(league.create_club(canon, None, None, 1000))
c = db.db()
m1 = c.insert_returning_id(
    "INSERT INTO matches (tournament_id, home_club_id, away_club_id, status) VALUES (?,?,?, 'pending')",
    (tid, clubs[0], clubs[1]))
m2 = c.insert_returning_id(
    "INSERT INTO matches (tournament_id, home_club_id, away_club_id, status) VALUES (?,?,?, 'pending')",
    (tid, clubs[2], clubs[3]))
uid = c.insert_returning_id("INSERT INTO users (telegram_id, username, balance) VALUES (9001, 'x', 0)")
bid = c.insert_returning_id(
    "INSERT INTO bets (user_id, bet_type, amount, total_odds, potential_win, status) "
    "VALUES (?, 'express', 90, 8.17, 735, 'open')", (uid,))
c.execute("INSERT INTO bet_legs (bet_id, match_id, market_code, odds, result) VALUES (?,?,'1x2_p2',2.77,'pending')",
          (bid, m2))
c.execute("INSERT INTO bet_legs (bet_id, match_id, market_code, odds, result) VALUES (?,?,'1x2_p1',2.95,'pending')",
          (bid, m1))
c.execute("UPDATE matches SET score1=2, score2=0, status='confirmed' WHERE id=?", (m2,))
c.commit()
c.close()
r = bets_engine.settle_match(m2)
c = db.db()
bet = dict(c.execute("SELECT * FROM bets WHERE id=?", (bid,)).fetchone())
note = c.execute("SELECT text, kind FROM notifications WHERE user_id=? ORDER BY id DESC", (uid,)).fetchone()
c.close()
check("экспресс с проигранной ногой рассчитан сразу", bet["status"] == "lost", bet["status"])
text = note["text"] if note else ""
print(text)
check("уведомление kind=bet", note and note["kind"] == "bet")
check("заголовок", f"😔 Ставка #{bid} (Экспресс) не сыграла" in text)
check("счёт и нога", "⚽ Бернли 2:0 Будё Глимт" in text and "❌ Победит Будё Глимт (П2) · @2.77" in text)
check("несыгранный матч", "⚽ Ницца — : — Порту" in text and "⏳ Победит Ницца (П1)" in text)
check("возможный выигрыш", "🎯 Возможный выигрыш был: 735 🚬" in text)

# 5) доставка в ЛС: один раз, помечается tg_sent
sent = []


class FakeBot:
    async def send_message(self, chat_id, text):
        sent.append((chat_id, text))


job_ctx = SimpleNamespace(bot=FakeBot())
asyncio.run(botmain.job_push_bet_results(job_ctx))
asyncio.run(botmain.job_push_bet_results(job_ctx))
check("ЛС: доставлено ровно одно", len(sent) == 1 and sent[0][0] == 9001, str(len(sent)))
c = db.db()
left = c.execute("SELECT COUNT(*) n FROM notifications WHERE kind='bet' AND tg_sent=0").fetchone()["n"]
c.close()
check("ЛС: tg_sent проставлен", left == 0)

print("\nGATE 12:", "OK" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
