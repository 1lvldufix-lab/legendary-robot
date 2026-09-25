"""ГЕЙТ 13: приём скринов. Матч и стороны — по никам FC27 (скрин не знает наших
клубов/дивизионов), опечатки OCR и смешанная раскладка, развёрнутый скрин
(слева гости), чужой матч, неузнанные ники → уточнение, полный прогон
обработчика (пачка → OCR → финализация) с фейковым ботом."""
import asyncio
import json
import os
import sys
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bot"))
sys.path.insert(0, os.path.join(ROOT, "tests"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["DB_PATH"] = "/tmp/gate13.db"
if os.path.exists("/tmp/gate13.db"):
    os.remove("/tmp/gate13.db")

import db  # noqa: E402
import handlers_results as hr  # noqa: E402
import league  # noqa: E402
import ocr  # noqa: E402
import report_match as rm  # noqa: E402
from references import REFERENCE_SHOTS  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


db.init_db()
tid = league.create_league_season("Сезон 1")
div = league.tournament_divisions(tid)[0]
OWNERS = [(801, "Бетис", "temiyy"), (802, "Ливерпуль", "Rusli"),
          (803, "Порту", "quete-основа"), (804, "Ницца", "KadyrFc"), (805, "Лидс", None)]
club = {}
c = db.db()
for tg, name, nick in OWNERS:
    c.execute("INSERT INTO players (username, telegram_id, game_nickname) VALUES (?,?,?)", (f"u{tg}", tg, nick))
    c.execute("INSERT INTO users (telegram_id, username, balance) VALUES (?,?,1000)", (tg, f"u{tg}"))
c.commit()
pid = {r["telegram_id"]: r["id"] for r in c.execute("SELECT id, telegram_id FROM players").fetchall()}
c.close()
for tg, name, _ in OWNERS:
    club[tg] = league.create_club(name, None, div["id"], 1000)
    league.assign_club_owner(club[tg], pid[tg])

c = db.db()
c.execute("INSERT INTO tours (tournament_id, tour_number, status) VALUES (?,1,'open')", (tid,))


def add_match(h, a, tour=1):
    return c.insert_returning_id(
        "INSERT INTO matches (tournament_id, home_club_id, away_club_id, tour_number, status) "
        "VALUES (?,?,?,?,'pending')", (tid, club[h], club[a], tour))


# в календаре хозяин — Ливерпуль, а на скрине слева temiyy (Бетис) → надо развернуть
m_bl = add_match(802, 801)
m_pn = add_match(803, 804)
m_leeds = add_match(805, 801)   # у Бетиса второй матч — с Лидсом (ник не задан)
c.commit()
c.close()


def parsed_from(gt, **over):
    return {**ocr.normalize_result(dict(gt)), **over}


gt1 = REFERENCE_SHOTS["photo_1_2026-09-24_19-49-40.jpg"]   # temiyy 2:3 Rusli
p1 = parsed_from(gt1)
r = rm.resolve(p1, 801)
check("эталон 1: матч по никам", r["status"] == "auto" and r["match_id"] == m_bl, str(r))
check("эталон 1: слева гости → разворот", r["swapped"] is True)
o = rm.orient(p1, r["swapped"])
check("разворот счёта 2:3 → 3:2 для Ливерпуль—Бетис", (o["score_home"], o["score_away"]) == (3, 2))

r = rm.resolve(parsed_from(gt1, player_away="Ruslli"), 802)
check("опечатка OCR «Ruslli» узнана", r["status"] == "auto" and r["match_id"] == m_bl, str(r))
r = rm.resolve(parsed_from(gt1, player_home="tеmiyy"), 801)   # «е» кириллицей
check("смешанная раскладка узнана", r["status"] == "auto" and r["match_id"] == m_bl, str(r))
r = rm.resolve(parsed_from(gt1, player_home=None), 801)
check("одного уверенного ника достаточно", r["status"] == "auto" and r["match_id"] == m_bl and r["swapped"], str(r))
r = rm.resolve(parsed_from(gt1, player_away=None), 801)
check("один ник, но у клуба 2 матча → выбор матча", r["status"] == "ask_match", str(r))
check("…после выбора сторона понятна по нику", rm.side_for_match(parsed_from(gt1, player_away=None), m_leeds) is True)

gt_b = REFERENCE_SHOTS["photo_1_2026-09-24_19-49-50.jpg"]   # вариант Б: quete-основа 3:1 KadyrFc
r = rm.resolve(parsed_from(gt_b), 803)
check("вариант Б (без названий клубов)", r["status"] == "auto" and r["match_id"] == m_pn and not r["swapped"], str(r))
r = rm.resolve(parsed_from(gt_b), 801)
check("чужой матч → отказ", r["status"] == "error", str(r))

r = rm.resolve(parsed_from(gt1, player_home="xxx", player_away="yyy"), 801)
check("ники не узнаны, 2 матча → выбор матча",
      r["status"] == "ask_match" and set(r["candidates"]) == {m_bl, m_leeds}, str(r))
r = rm.resolve(parsed_from(gt1, player_home="xxx", player_away="yyy"), 805)
check("ники не узнаны, 1 матч → вопрос о стороне", r["status"] == "ask_side" and r["match_id"] == m_leeds, str(r))
check("сторона по нику в выбранном матче", rm.side_for_match(p1, m_bl) is True)
check("сторона без ников — None",
      rm.side_for_match(parsed_from(gt1, player_home=None, player_away=None), m_leeds) is None)

pens = rm.orient({"score_home": 3, "score_away": 3, "penalties": {"home": 5, "away": 4},
                  "goal_events": [{"side": "home", "name": "A", "minute": 1}], "players": []}, True)
check("разворот пенальти и голов",
      pens["penalties"] == {"home": 4, "away": 5} and pens["goal_events"][0]["side"] == "away")

# ===== полный прогон обработчика: пачка → OCR (мок) → финализация =====
sent = []


class FakeBot:
    async def send_message(self, chat_id, text, reply_markup=None):
        sent.append((chat_id, text))
        return SimpleNamespace(message_id=len(sent))

    async def edit_message_text(self, text, chat_id=None, message_id=None, reply_markup=None):
        sent.append((chat_id, text))


ocr.build_cascade = lambda: [("mock", lambda img: json.dumps(gt1, ensure_ascii=False))]
ctx = SimpleNamespace(bot=FakeBot(), user_data={"report_images": [b"shot-1", b"shot-2"]}, job_queue=None)
asyncio.run(hr._process_batch(ctx, 801, 801, reuse_images=True))
c = db.db()
m = dict(c.execute("SELECT * FROM matches WHERE id=?", (m_bl,)).fetchone())
shots = c.execute("SELECT COUNT(*) n FROM processed_screenshots WHERE match_id=?", (m_bl,)).fetchone()["n"]
c.close()
final = next((t for _, t in sent if t.startswith("✅")), "")
print(final)
check("обработчик: матч засчитан с разворотом",
      m["status"] == "confirmed" and (m["score1"], m["score2"]) == (3, 2), f"{m['score1']}:{m['score2']}")
check("обработчик: где матч (сезон/дивизион/тур)",
      "Сезон 1" in final and "Тур 1" in final and div["name"] in final)
check("обработчик: оба скрина в дедупе", shots == 2)

sent.clear()
ctx.user_data = {"report_images": [b"shot-1"]}
asyncio.run(hr._process_batch(ctx, 801, 801, reuse_images=True))
check("повторный скрин не принимается", any("уже засчитаны" in t for _, t in sent), str(sent))

print("\nGATE 13:", "OK" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
