"""ГЕЙТ 12: бот стартует (команды только латиницей — PTB/Telegram), команды получают
context.args, root из ADMIN_IDS — админ мини-аппа, кнопки меню не сохраняются как ник, формат уведомления о расчёте
экспресса (проигрыш решает купон сразу), названия клубов с заглавной."""
import asyncio
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bot"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["DB_PATH"] = "/tmp/gate12.db"
os.environ["ADMIN_IDS"] = "555"
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


# 1) команды только латиницей (Telegram) и все регистрируются
import re  # noqa: E402

from telegram.ext import CommandHandler  # noqa: E402

ALL = (*botmain.ht.HANDLERS, *botmain.hr.HANDLERS, *botmain.hd.HANDLERS, *botmain.ha.HANDLERS)
names = [pat for kind, pat, _ in ALL if kind == "command"]
bad = [n for n in names if not re.fullmatch(r"[a-z0-9_]{1,32}", n)]
check("все команды латиницей", not bad, str(bad))
app = Application.builder().token("123456:TEST").build()
try:
    for n in names:
        app.add_handler(CommandHandler(n, lambda u, c: None))
    ok, err = True, None
except Exception as e:  # noqa: BLE001
    ok, err = False, e
check("все команды регистрируются", ok, repr(err))
known = set(names) | {"start", "nick", "myid"}
menu_missing = [n for n, _ in botmain.ADMIN_COMMANDS if n not in known]
check("меню команд ссылается на существующие", not menu_missing, str(menu_missing))


def make_update(text: str) -> Update:
    u = User(1, "t", False)
    msg = Message(1, None, Chat(1, "private"), from_user=u, text=text,
                  entities=[__import__("telegram").MessageEntity("bot_command", 0, len(text.split()[0]))])
    app.bot._bot_user = User(42, "kurilka", True, username="kurilka_bot")  # без сети
    msg.set_bot(app.bot)
    return Update(1, message=msg)


# 2) /resolve не перехватывает /disputes, аргументы доходят
h = CommandHandler("resolve", lambda u, c: None)
res = h.check_update(make_update("/resolve 12 2:1"))
check("/resolve 12 2:1 → args", bool(res) and list(res[0]) == ["12", "2:1"], str(res))
check("/disputes не матчится /resolve", not h.check_update(make_update("/disputes")))

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

# 5b) job трансферов: очередь ЛС уходит боту
import transfers  # noqa: E402

sent.clear()
orig_close = transfers.close_expired_auctions
transfers.close_expired_auctions = lambda: [(9001, "🔨 Аукцион завершён"), (9002, "Ставку перебили")]
asyncio.run(botmain.job_transfers(job_ctx))
transfers.close_expired_auctions = orig_close
check("job трансферов шлёт ЛС из очереди", [t for _, t in sent] == ["🔨 Аукцион завершён", "Ставку перебили"], str(sent))

# 6) root из ADMIN_IDS — сразу админ мини-аппа; англ. параметры команд
from miniapp.helpers import upsert_user  # noqa: E402
import handlers_tournament as ht  # noqa: E402

check("root → is_admin в мини-аппе", upsert_user({"id": 555, "first_name": "Root"})["is_admin"] == 1)
check("обычный игрок не админ", upsert_user({"id": 556, "first_name": "P"})["is_admin"] == 0)
name, opts = ht._parse_args("Сезон-1 divisions=2 rounds=1 days=4 clubs=all")
check("англ. параметры = русские", name == "Сезон-1" and opts == {"дивизионы": "2", "круги": "1", "дней": "4", "клубы": "все"}, str(opts))
_, opts = ht._parse_args("дивизионы=3")
check("русские параметры по-прежнему", opts == {"дивизионы": "3"})

print("\nGATE 12:", "OK" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
