"""ГЕЙТ 19: лиги с именованными дивизионами (Ла Лига, Серия А…), клубы и владельцы,
ники от админа (уникальность, предрегистрация по ID), календарь по дивизионам разного
размера (туры не теряются), повышение/вылет N или выключено, зоны в таблице, права API,
/season с названиями, /setnick."""
import asyncio
import hashlib
import hmac
import json
import os
import sys
import time
from types import SimpleNamespace
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bot"))
os.environ["BOT_TOKEN"] = "123456:TEST"
os.environ["ADMIN_IDS"] = "910001"
os.environ["DB_PATH"] = "/tmp/gate19.db"
if os.path.exists("/tmp/gate19.db"):
    os.remove("/tmp/gate19.db")

from aiohttp.test_utils import TestClient, TestServer  # noqa: E402

import config  # noqa: E402
import core  # noqa: E402
import db  # noqa: E402
import league  # noqa: E402
import league_admin as la  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


def expect_error(name, fn, *a, **kw):
    try:
        fn(*a, **kw)
        check(name, False, "ошибки не было")
    except la.LeagueError as e:
        check(name, True, str(e))


db.init_db()

# ===== создание =====
check("разбор имён", la.parse_division_names("Ла Лига, Серия А; Лига 1 | Лига 2\nСерия С, ла лига") ==
      ["Ла Лига", "Серия А", "Лига 1", "Лига 2", "Серия С"])
NAMES = ["Ла Лига", "Серия А", "Лига 1", "Лига 2", "Серия С"]
tid = la.create_league("Сезон 1", NAMES, rounds=1, tour_days=3, promote_count=2)
divs = league.tournament_divisions(tid)
check("5 дивизионов с именами по порядку", [d["name"] for d in divs] == NAMES)
check("promote_count сохранён", league.get_tournament(tid)["promote_count"] == 2)
expect_error("без названия сезона", la.create_league, "", ["A"])
expect_error("дубль дивизиона", la.add_division, tid, "серия а")

la.move_division(divs[4]["id"], "up")
check("↑ меняет порядок", [d["name"] for d in league.tournament_divisions(tid)][3:] == ["Серия С", "Лига 2"])
la.move_division(divs[4]["id"], "down")
la.rename_division(divs[3]["id"], "Лига 2 (Франция)")
check("переименование", league.tournament_divisions(tid)[3]["name"] == "Лига 2 (Франция)")
extra = la.add_division(tid, "Серия Б")
la.delete_division(extra)
check("пустой дивизион удаляется", len(league.tournament_divisions(tid)) == 5)

# ===== клубы и участники =====
la_liga, serie_a = divs[0]["id"], divs[1]["id"]
for n in ("Барселона", "Реал Мадрид", "Атлетико Мадрид", "Валенсия"):
    la.add_club(la_liga, n)
for n in ("Милан", "Ювентус", "Наполи", "Рома", "Лацио", "Аталанта"):
    la.add_club(serie_a, n)
expect_error("клуба нет в каталоге", la.add_club, serie_a, "Кротоне")
cr = la.add_club(divs[4]["id"], "Кротоне", allow_custom=True)
check("свой клуб повторно (другой регистр) — тот же", la.add_club(divs[4]["id"], "КРОТОНЕ", allow_custom=True)["id"] == cr["id"])
check("свой клуб без лого", cr["name"] == "Кротоне" and cr["logo"] is None)
la.remove_club_from_division(cr["id"])

# игрок без /start: предрегистрация по ID + ник, потом /start дописывает username
bar = next(c for c in la.overview()[0]["divisions"][0]["clubs"] if c["name"] == "Барселона")
info = la.assign_owner(bar["id"], "920001")
check("владелец по ID без /start", info["owner_tg"] == 920001)
la.set_nick("920001", "temiyy")
core.ensure_player(920001, "temiyy_tg")
check("/start дописал username", la.resolve_player("@temiyy_tg")["game_nickname"] == "temiyy")
expect_error("ник занят (регистр/раскладка)", la.set_nick, "920002", "TЕMIYY")   # «Е» кириллицей
expect_error("@ник без /start не создаётся", la.resolve_or_create_player, "@nobody")
p2 = la.set_player_club("920002", next(c for c in la.overview()[0]["divisions"][0]["clubs"] if c["name"] == "Реал Мадрид")["id"])
check("клуб игроку через set_player_club", p2["club"] == "Реал Мадрид" and p2["division"] == "Ла Лига", str(p2))
la.assign_owner(bar["id"], "920003")
check("новый владелец вытесняет старого", core.club_of_player(la.resolve_player("920001")["id"]) is None)
check("people: поиск по нику", [p["telegram_id"] for p in la.people("temiyy")] == [920001])
check("судья включается/выключается", la.toggle_judge(tid, "920002") is True and la.toggle_judge(tid, "920002") is False)

# ===== календарь: дивизионы разного размера =====
r = la.generate_calendar(tid)
check("календарь по двум дивизионам", r["matches"] == {"Ла Лига": 6, "Серия А": 15}, str(r))
c = db.db()
tours = [x["tour_number"] for x in c.execute("SELECT tour_number FROM tours WHERE tournament_id=? ORDER BY 1", (tid,)).fetchall()]
orphan = c.execute("SELECT COUNT(*) n FROM matches m LEFT JOIN tours t ON t.tournament_id=m.tournament_id "
                   "AND t.tour_number=m.tour_number WHERE m.tournament_id=? AND t.id IS NULL", (tid,)).fetchone()["n"]
c.close()
check("туры длинного дивизиона не потеряны (баг)", tours == [1, 2, 3, 4, 5] and orphan == 0, f"{tours} orphan={orphan}")
check("total_tours = максимум", league.get_tournament(tid)["total_tours"] == 5)

# сыграли матч → перегенерация и уход клуба запрещены
c = db.db()
mid = c.execute("SELECT m.id FROM matches m JOIN clubs cl ON cl.id=m.home_club_id WHERE cl.division_id=? LIMIT 1",
                (serie_a,)).fetchone()["id"]
c.execute("UPDATE matches SET score1=1, score2=0, status='confirmed' WHERE id=?", (mid,))
c.commit()
c.close()
expect_error("календарь не перегенерируется после матчей", la.generate_calendar, tid, serie_a)
milan = next(x for x in la.overview()[0]["divisions"][1]["clubs"] if x["name"] == "Милан")
expect_error("клуб не уходит из начатого дивизиона", la.remove_club_from_division, milan["id"])


# ===== повышение/вылет: N и выключено =====
def fresh_clubs(t, div, names):
    ids = []
    for n in names:
        ids.append(league.create_club(f"{n}-{t}", None, div, 1000))
    return ids


for pc in (2, 0):
    t = la.create_league(f"PC{pc}", ["Верх", "Низ"], rounds=1, promote_count=pc)
    top, low = [d["id"] for d in league.tournament_divisions(t)]
    tops, lows = fresh_clubs(t, top, "ABCDE"), fresh_clubs(t, low, "FGHIJ")
    la.generate_calendar(t)
    c = db.db()
    for m in c.execute("SELECT * FROM matches WHERE tournament_id=?", (t,)).fetchall():
        # сильнее тот, у кого меньше id → таблица предсказуема
        c.execute("UPDATE matches SET score1=?, score2=?, status='confirmed' WHERE id=?",
                  (1 if m["home_club_id"] < m["away_club_id"] else 0, 0 if m["home_club_id"] < m["away_club_id"] else 1, m["id"]))
    c.commit()
    c.close()
    res = league.finalize_season(t)
    if pc:
        up = {league.get_club(i)["division_id"] for i in lows[:2]}
        down = {league.get_club(i)["division_id"] for i in tops[-2:]}
        check("promote=2: двое вверх, двое вниз", up == {top} and down == {low} and len(res["moves"]) == 4, str(res["moves"]))
    else:
        check("promote=0: никто не двигается", not res["moves"] and league.get_club(lows[0])["division_id"] == low)


# ===== API =====
def sign(uid):
    pairs = {"auth_date": str(int(time.time())), "user": json.dumps({"id": uid, "first_name": "T"})}
    dcs = "\n".join(f"{k}={pairs[k]}" for k in sorted(pairs))
    key = hmac.new(b"WebAppData", config.BOT_TOKEN.encode(), hashlib.sha256).digest()
    pairs["hash"] = hmac.new(key, dcs.encode(), hashlib.sha256).hexdigest()
    return {"X-Telegram-Init-Data": urlencode(pairs)}


async def api_checks():
    from miniapp.server import build_app
    cl = TestClient(TestServer(build_app()))
    await cl.start_server()
    root, adm, player = sign(910001), sign(910002), sign(910003)
    for h in (adm, player):
        await cl.get("/api/bootstrap", headers=h)
    c = db.db()
    c.execute("UPDATE users SET is_admin=1 WHERE telegram_id=910002")
    c.commit()
    c.close()
    r = await cl.get("/api/admin/leagues", headers=player)
    check("API: игрок → 403", r.status == 403)
    r = await cl.post("/api/admin/leagues", headers=adm, data=json.dumps(
        {"name": "Сезон API", "divisions": "Ла Лига\nСерия А\nСерия С", "rounds": 2, "tour_days": 3, "promote_count": 3}))
    data = await r.json()
    new = next(t for t in data["leagues"] if t["name"] == "Сезон API")
    check("API: админ создаёт сезон с именами", [d["name"] for d in new["divisions"]] == ["Ла Лига", "Серия А", "Серия С"])
    d0 = new["divisions"][0]["id"]
    r = await cl.post(f"/api/admin/divisions/{d0}", headers=adm, data=json.dumps({"club": "Севилья", "custom": True}))
    check("API: клуб в дивизион", r.status == 200)
    r = await cl.post("/api/admin/divisions/abc", headers=adm, data=json.dumps({"name": "x"}))
    check("API: мусорный id → 400", r.status == 400)
    r = await cl.post("/api/admin/people", headers=adm, data=json.dumps({"telegram_id": "930001", "nick": "KadyrFc"}))
    check("API: предрегистрация с ником", r.status == 200 and any(p["nick"] == "KadyrFc" for p in (await r.json())["people"]))
    r = await cl.post("/api/admin/people/930002", headers=adm, data=json.dumps({"nick": "kadyrfc"}))
    check("API: дубль ника → 400", r.status == 400)
    r = await cl.post("/api/admin/people/930001", headers=adm, data=json.dumps({"judge_tournament": new["id"]}))
    check("API: судью назначает только root", r.status == 403)
    r = await cl.post("/api/admin/people/930001", headers=root, data=json.dumps({"judge_tournament": new["id"]}))
    check("API: root назначает судью", r.status == 200 and (await r.json())["person"]["judge_of"] == ["Сезон API"])
    r = await cl.get(f"/api/standings?division_id={serie_a}", headers=player)
    z = (await r.json())["zones"]
    check("зоны: Серия А (2-й из 5, promote=2) — вверх и вниз по 2", z == {"up": 2, "down": 2}, str(z))
    r = await cl.get(f"/api/standings?division_id={la_liga}", headers=player)
    check("зоны: высший дивизион без повышения", (await r.json())["zones"]["up"] == 0)
    r = await cl.get("/api/bootstrap", headers=player)
    names = {d["tournament_name"] for d in (await r.json())["divisions"]}
    check("bootstrap: дивизионы только текущих лиг", "PC2" not in names and "Сезон 1" in names, str(names))
    await cl.close()

asyncio.run(api_checks())

# ===== бот: /season с названиями и /setnick =====
import handlers_tournament as ht  # noqa: E402
import main as botmain  # noqa: E402

replies = []


def fake_update(uid, text):
    async def reply(t, **kw):
        replies.append(t)
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid, username="root"),
                           message=SimpleNamespace(text=text, reply_text=reply))


asyncio.run(ht.cmd_season(fake_update(910001, ""), SimpleNamespace(
    args="Сезон Бот divisions=Ла Лига, Серия А, Лига 1 rounds=1 promote=0".split())))
t = next(x for x in league.all_active_tournaments() if x["name"] == "Сезон Бот")
check("/season с названиями дивизионов", [d["name"] for d in league.tournament_divisions(t["id"])] == ["Ла Лига", "Серия А", "Лига 1"]
      and t["rounds"] == 1 and t["promote_count"] == 0, replies[-1])
asyncio.run(ht.cmd_season(fake_update(910001, ""), SimpleNamespace(args="Сезон Число divisions=2".split())))
t = next(x for x in league.all_active_tournaments() if x["name"] == "Сезон Число")
check("/season divisions=2 по-старому", [d["name"] for d in league.tournament_divisions(t["id"])] == ["Дивизион 1", "Дивизион 2"])
asyncio.run(botmain.cmd_setnick(fake_update(910003, ""), SimpleNamespace(args=["940001", "x"])))
check("/setnick: игроку нельзя", replies[-1].startswith("⛔"))
asyncio.run(botmain.cmd_setnick(fake_update(910001, ""), SimpleNamespace(args=["940001", "Rusli"])))
check("/setnick: root ставит ник", la.resolve_player("940001")["game_nickname"] == "Rusli", replies[-1])

print("\nGATE 19:", "OK" if not fails else f"FAIL {fails}")
sys.exit(1 if fails else 0)
