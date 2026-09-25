"""ГЕЙТ 20: карточки игроков — характеристики и валидация (рус. позиции), добавление владельцем
(pending) и админом (approved / свободный агент), судья, дубли, закрытое редактирование, лимит,
торговля только одобренными, фото (валидация/EXIF/размер), media-маршрут, OCR карточки (мок каскада,
только vision), RenderZ (разбор фикстуры, сравнение, ссылки, кэш, авто-одобрение), фильтры рынка, права API."""
import asyncio
import hashlib
import hmac
import io
import json
import os
import shutil
import sys
import time
from pathlib import Path
from urllib.parse import urlencode

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bot"))
os.environ["BOT_TOKEN"] = "123456:TEST"
os.environ["ADMIN_IDS"] = "920001"
os.environ["DB_PATH"] = "/tmp/gate20.db"
os.environ["MEDIA_DIR"] = "/tmp/gate20_media"
for p in ("/tmp/gate20.db",):
    if os.path.exists(p):
        os.remove(p)
shutil.rmtree("/tmp/gate20_media", ignore_errors=True)

from aiohttp import FormData  # noqa: E402
from aiohttp.test_utils import TestClient, TestServer  # noqa: E402
from PIL import Image  # noqa: E402

import card_ocr  # noqa: E402
import cards  # noqa: E402
import config  # noqa: E402
import db  # noqa: E402
import league  # noqa: E402
import league_admin as la  # noqa: E402
import ocr  # noqa: E402
import renderz  # noqa: E402
import settings as appsettings  # noqa: E402
import training  # noqa: E402
import transfers as tr  # noqa: E402
from miniapp.helpers import upsert_user  # noqa: E402

fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


def expect(name, code, fn, *a, **kw):
    try:
        fn(*a, **kw)
        check(name, False, "ошибки не было")
    except (cards.CardError, tr.TransferError, training.TrainingError, renderz.RenderzError) as e:
        got = getattr(e, "code", "RenderzError")
        check(name, code is None or got == code, f"{got}: {e}")


def q1(sql, args=()):
    c = db.db()
    r = c.execute(sql, args).fetchone()
    c.close()
    return r


def ex(sql, args=()):
    c = db.db()
    c.execute(sql, args)
    c.commit()
    c.close()


FIXTURE = (Path(ROOT) / "tests" / "fixtures" / "renderz_player.html").read_text(encoding="utf-8")
RZ_URL = "https://renderz.app/player/12345678-kylian-mbappe"


def fixture_fetch(url):
    return FIXTURE


db.init_db()
ROOT_TG, ADMIN, OWNER_A, OWNER_B, JUDGE, PLAYER = 920001, 920002, 920010, 920011, 920012, 920013
users = {tg: upsert_user({"id": tg, "first_name": f"U{tg}"}) for tg in (ROOT_TG, ADMIN, OWNER_A, OWNER_B, JUDGE, PLAYER)}
ex("UPDATE users SET is_admin=1 WHERE telegram_id=?", (ADMIN,))

tid = la.create_league("Сезон карточек", ["Лига A"], rounds=1)
div = league.tournament_divisions(tid)[0]["id"]
A = la.add_club(div, "Барселона", owner=str(OWNER_A))["id"]
B = la.add_club(div, "Милан", owner=str(OWNER_B))["id"]
C = la.add_club(div, "Ювентус", owner=str(JUDGE))["id"]
la.toggle_judge(tid, JUDGE)
ex("UPDATE clubs SET budget=500000000")

# ===== валидация =====
f = cards.validate({"name": "  Kylian   Mbappé ", "rating": "115", "position": "нап", "alt_positions": "ЛВ, ФРВ,нап",
                    "pac": 130, "skill_moves": 5, "weak_foot": "4", "foot": "Right", "height_cm": 178})
check("рус. позиция → EN", f["position"] == "ST", f["position"])
check("доп. позиции: рус → EN, без основной", f["alt_positions"] == "LW,CF", f["alt_positions"])
check("имя очищено, нога R", f["name"] == "Kylian Mbappé" and f["foot"] == "R")
check("список доп. позиций", cards.validate({"name": "Xx", "rating": 60, "position": "ЦАП",
                                              "alt_positions": ["ЦП", "LM"]})["alt_positions"] == "CM,LM")
for label, bad in (("короткое имя", {"name": "X"}), ("длинное имя", {"name": "X" * 41}),
                   ("OVR 39", {"rating": 39}), ("OVR 151", {"rating": 151}), ("позиция XX", {"position": "XX"}),
                   ("доп. позиция ZZ", {"alt_positions": "LW,ZZ"}), ("стат 201", {"pac": 201}),
                   ("финты 6", {"skill_moves": 6}), ("слабая 0", {"weak_foot": 0}), ("нога X", {"foot": "X"}),
                   ("OVR текст", {"rating": "abc"})):
    expect(f"валидация: {label}", "BAD_INPUT", cards.validate, {"name": "Test", "rating": 80, "position": "CM", **bad})

# ===== владелец → pending, судья =====
dm_before = q1("SELECT COUNT(*) n FROM transfer_dm_queue")["n"]
r = cards.create_card(OWNER_A, A, {"name": "Gareth Bale", "rating": 122, "position": "ПВ", "alt_positions": "RM",
                                    "pac": 120, "sho": 118, "nation": "Wales", "league": "LaLiga"})
bale = r["card"]
check("владелец: карточка pending", bale["verify_status"] == "pending" and bale["source"] == "owner", bale["verify_status"])
check("позиция хранится EN", bale["position"] == "RW")
check("судьям ушли ЛС", q1("SELECT COUNT(*) n FROM transfer_dm_queue WHERE telegram_id=?", (JUDGE,))["n"] >= 1
      and q1("SELECT COUNT(*) n FROM transfer_dm_queue")["n"] > dm_before)
check("владелец себе ЛС не получил", q1("SELECT COUNT(*) n FROM transfer_dm_queue WHERE telegram_id=?", (OWNER_A,))["n"] == 0)
expect("дубль (регистр/пробелы)", "DUP", cards.create_card, OWNER_A, A,
       {"name": "gareth  BALE", "rating": 122, "position": "RW"})
check("тот же игрок в другом клубе — можно",
      cards.create_card(OWNER_B, B, {"name": "Gareth Bale", "rating": 122, "position": "RW"})["card"]["club_id"] == B)
expect("владелец в чужой клуб", "FORBIDDEN", cards.create_card, OWNER_A, B, {"name": "Other", "rating": 80, "position": "CM"})
expect("владелец в свободные агенты", "FORBIDDEN", cards.create_card, OWNER_A, None, {"name": "Other", "rating": 80, "position": "CM"})

# pending не торгуется
expect("pending: на рынок нельзя", "NOT_APPROVED", tr.create_lot, A, bale["id"], "fix", 1000)
expect("pending: обмен нельзя", "NOT_APPROVED", tr.propose_exchange, B, A, None, bale["id"], 1000, OWNER_B)
expect("pending: тренировка нельзя", "NOT_APPROVED", training.add_entry, users[OWNER_A], bale["id"], "ovr", 1)

expect("владелец не судит свою", "FORBIDDEN", cards.decide, bale["id"], True, OWNER_A)
expect("игрок без прав не судит", "FORBIDDEN", cards.decide, bale["id"], True, OWNER_B)
check("очередь судьи содержит карточку", any(x["id"] == bale["id"] for x in cards.queue(JUDGE)))
check("очередь владельца B пуста (не судья)", cards.queue(OWNER_B) == [])
res = cards.decide(bale["id"], True, JUDGE, "ок")
check("судья одобрил", res["card"]["verify_status"] == "approved" and res["card"]["verified_by"] == JUDGE)
check("владелец получил уведомление", q1("SELECT COUNT(*) n FROM notifications n JOIN users u ON u.id=n.user_id "
                                         "WHERE u.telegram_id=? AND n.text LIKE '%одобрена%'", (OWNER_A,))["n"] == 1)
check("владелец получил ЛС", q1("SELECT COUNT(*) n FROM transfer_dm_queue WHERE telegram_id=?", (OWNER_A,))["n"] == 1)
expect("повторное решение", "NOT_PENDING", cards.decide, bale["id"], False, JUDGE)
check("аудит одобрения", q1("SELECT COUNT(*) n FROM tournament_audit_log WHERE action='card_approve' AND tournament_id=?",
                            (tid,))["n"] == 1)

juve = cards.create_card(JUDGE, C, {"name": "Del Piero", "rating": 110, "position": "CF"})["card"]
expect("судья не судит свой клуб", "FORBIDDEN", cards.decide, juve["id"], True, JUDGE)
check("своя карточка не в очереди судьи", all(x["id"] != juve["id"] for x in cards.queue(JUDGE)))
rej = cards.decide(juve["id"], False, ADMIN, "не тот OVR")
check("админ отклонил с причиной", rej["card"]["verify_status"] == "rejected" and rej["card"]["verify_note"] == "не тот OVR")
upd = cards.update_card(JUDGE, juve["id"], {"rating": 111})
check("правка отклонённой → снова pending", upd["card"]["verify_status"] == "pending" and upd["card"]["rating"] == 111)

lot = tr.create_lot(A, bale["id"], "fix", 5_000_000, actor_tg=OWNER_A)
check("одобренная карточка выставлена", lot["lot_id"] > 0)
expect("владелец правит одобренную", "APPROVED", cards.update_card, OWNER_A, bale["id"], {"rating": 123})
expect("чужую карточку не править", "FORBIDDEN", cards.update_card, OWNER_B, bale["id"], {"rating": 123})
expect("удаление карточки на рынке (владелец)", "FORBIDDEN", cards.delete_card, OWNER_A, bale["id"])
expect("удаление карточки на рынке (админ)", "FORBIDDEN", cards.delete_card, ADMIN, bale["id"])
check("админ правит одобренную", cards.update_card(ADMIN, bale["id"], {"program": "Icons"})["card"]["program"] == "Icons")

# ===== админ =====
fa = cards.create_card(ADMIN, None, {"name": "Kylian Mbappé", "rating": 115, "position": "ST", "alt_positions": "LW",
                                     "nation": "France", "league": "Ligue 1", "pac": 140, "sho": 120})["card"]
check("админ: свободный агент approved", fa["club_id"] is None and fa["verify_status"] == "approved" and fa["source"] == "admin")
adm_in_b = cards.create_card(ROOT_TG, B, {"name": "Paolo Maldini", "rating": 112, "position": "ЦЗ", "nation": "Italy",
                                           "league": "Serie A", "def": 130, "phy": 110})["card"]
check("root в любой клуб approved", adm_in_b["club_id"] == B and adm_in_b["verify_status"] == "approved")

# ===== настройки =====
appsettings.set_setting("squad_edit_open", "0")
expect("редактирование закрыто", "SQUAD_CLOSED", cards.create_card, OWNER_A, A, {"name": "Neymar", "rating": 100, "position": "LW"})
check("админ добавляет при закрытом", cards.create_card(ADMIN, A, {"name": "Neymar", "rating": 100, "position": "LW"})
      ["card"]["verify_status"] == "approved")
check("my: can_edit=false при закрытом", cards.my_cards(OWNER_A)["can_edit"] is False)
appsettings.set_setting("squad_edit_open", "1")
n_a = q1("SELECT COUNT(*) n FROM club_cards WHERE club_id=?", (A,))["n"]
appsettings.set_setting("squad_max_cards", str(n_a))
expect("лимит состава", "LIMIT", cards.create_card, OWNER_A, A, {"name": "Xavi", "rating": 105, "position": "CM"})
appsettings.set_setting("squad_max_cards", "60")
appsettings.set_setting("cards_require_approval", "0")
xavi = cards.create_card(OWNER_A, A, {"name": "Xavi", "rating": 105, "position": "CM"})["card"]
check("одобрения выключены → approved", xavi["verify_status"] == "approved")
appsettings.set_setting("cards_require_approval", "1")

pend = cards.create_card(OWNER_A, A, {"name": "Pedri", "rating": 99, "position": "CM"})["card"]
check("удаление pending владельцем", cards.delete_card(OWNER_A, pend["id"])["ok"] and
      q1("SELECT 1 FROM club_cards WHERE id=?", (pend["id"],)) is None)
check("удаление одобренной (не на рынке) владельцем", cards.delete_card(OWNER_A, xavi["id"])["ok"])
ex("UPDATE club_cards SET verify_status='pending' WHERE id=?", (fa["id"],))
expect("pending агента не подписать", "NOT_APPROVED", tr.sign_free_agent, B, fa["id"], OWNER_B)
ex("UPDATE club_cards SET verify_status='approved' WHERE id=?", (fa["id"],))

# ===== фото =====


def jpeg_with_exif(w, h):
    img = Image.new("RGB", (w, h), (200, 30, 30))
    exif = Image.Exif()
    exif[0x010F] = "SecretCam"      # Make
    exif[0x8825] = {2: (55.0, 45.0, 0.0)}  # GPS
    buf = io.BytesIO()
    img.save(buf, "JPEG", exif=exif.tobytes())
    return buf.getvalue()


raw = jpeg_with_exif(2000, 1500)
check("исходник с EXIF", b"SecretCam" in raw)
up = cards.save_upload(OWNER_A, raw)
name = up["image_url"].rsplit("/", 1)[-1]
saved = Path("/tmp/gate20_media/cards") / name
img = Image.open(saved)
check("загрузка: файл в MEDIA_DIR/cards", saved.exists() and up["image_url"].startswith("/media/cards/"))
check("загрузка: ≤1000 px, JPEG", max(img.size) == 1000 and img.format == "JPEG", str(img.size))
check("загрузка: EXIF удалён", b"SecretCam" not in saved.read_bytes() and not img.getexif())
png = io.BytesIO()
Image.new("RGBA", (300, 200), (0, 0, 255, 128)).save(png, "PNG")
check("PNG с прозрачностью → JPEG", cards.save_upload(OWNER_A, png.getvalue())["token"])
expect("мусор вместо картинки", "BAD_IMAGE", cards.save_upload, OWNER_A, b"not an image at all" * 10)
expect("больше 8 МБ", "TOO_BIG", cards.save_upload, OWNER_A, b"\xff" * (8 * 1024 * 1024 + 1))
big = io.BytesIO()
Image.new("L", (4100, 60)).save(big, "PNG")
expect("больше 4000 px", "TOO_BIG", cards.save_upload, OWNER_A, big.getvalue())
gif = io.BytesIO()
Image.new("RGB", (100, 100)).save(gif, "GIF")
expect("GIF не принимается", "BAD_IMAGE", cards.save_upload, OWNER_A, gif.getvalue())

withphoto = cards.create_card(OWNER_A, A, {"name": "Lamine Yamal", "rating": 101, "position": "RW"},
                              upload_token=up["token"])["card"]
check("карточка с фото", withphoto["image_url"] == up["image_url"] and withphoto["checks"]["photo"] is True
      and withphoto["checks"]["ocr"] is None)
expect("фото повторно к другой карточке", "BAD_TOKEN", cards.create_card, OWNER_A, A,
       {"name": "Gavi", "rating": 95, "position": "CM"}, upload_token=up["token"])
up_b = cards.save_upload(OWNER_B, jpeg_with_exif(400, 300))
expect("чужое фото", "BAD_TOKEN", cards.create_card, OWNER_A, A, {"name": "Gavi", "rating": 95, "position": "CM"},
       upload_token=up_b["token"])
old = cards.save_upload(OWNER_B, jpeg_with_exif(400, 300))
old_file = Path("/tmp/gate20_media/cards") / old["image_url"].rsplit("/", 1)[-1]
ex("UPDATE card_uploads SET created_at='2000-01-01 00:00:00' WHERE token=?", (old["token"],))
removed = cards.cleanup_uploads()
check("уборка: старая загрузка удалена", removed >= 1 and not old_file.exists()
      and q1("SELECT 1 FROM card_uploads WHERE token=?", (old["token"],)) is None)
check("уборка: привязанное фото осталось", saved.exists())
check("media_file: обход путей", cards.media_file("../gate20.db") is None and cards.media_file("..%2f") is None
      and cards.media_file(name) is not None)

# ===== OCR карточки (мок каскада) =====
calls = []


def text_ocr(img, prompt=None):
    calls.append("ocrspace")
    return "raw text"


def broken(img, prompt=None):
    raise RuntimeError("503")


def mock_vision(img, prompt=None):
    calls.append(("mock", prompt == card_ocr.CARD_PROMPT))
    return "Вот ответ:\n```json\n" + json.dumps({
        "name": "Kylian Mbappé", "rating": 115, "position": "НАП", "alt_positions": ["ЛВ", "ST"],
        "pac": 140, "sho": 250, "pas": 100, "dri": 125, "def": None, "phy": 90, "skill_moves": 5, "weak_foot": 4,
        "foot": "Right", "height_cm": 178, "nation": "France", "league": "LALIGA EA SPORTS", "club": "Real Madrid",
        "program": "TOTY", "confidence": 0.93, "warnings": []}, ensure_ascii=False) + "\n```"


orig_cascade = ocr.build_cascade
ocr.build_cascade = lambda: [("gemini", broken), ("ocrspace", text_ocr), ("tesseract", text_ocr), ("mock", mock_vision)]
res = card_ocr.parse_card(b"img")
flds = res["fields"]
check("OCR: текстовые провайдеры пропущены", "ocrspace" not in calls)
check("OCR: провайдер получил CARD_PROMPT", ("mock", True) in calls and res["provider"] == "mock")
check("OCR: поля заполнены", flds.get("name") == "Kylian Mbappé" and flds.get("rating") == 115
      and flds.get("position") == "ST" and flds.get("alt_positions") == ["LW"], str(flds))
check("OCR: club → real_club, нога, рост", flds.get("real_club") == "Real Madrid" and flds.get("foot") == "R"
      and flds.get("height_cm") == 178)
check("OCR: стат вне диапазона отброшен", "sho" not in flds and flds.get("pac") == 140 and "def" not in flds)
ocr.build_cascade = lambda: [("gemini", broken), ("mock", lambda img, prompt=None: "не знаю")]
res = card_ocr.parse_card(b"img")
check("OCR сбой → пустые поля + причина", res["fields"] == {} and res["warnings"] and res["provider"] is None)
ocr.build_cascade = lambda: [("ocrspace", text_ocr)]
check("OCR без vision → понятное предупреждение", card_ocr.parse_card(b"x")["warnings"][0].startswith("Распознавание недоступно"))
ocr.build_cascade = lambda: [("mock", mock_vision)]

# ===== RenderZ =====
for good in ("https://renderz.app/player/12345678-kylian-mbappe", "https://www.renderz.app/player/12345678-kylian-mbappe/",
             "https://fifarenderz.com/player/12345678-kylian-mbappe?utm=1#x", "https://renderz.app/player/12345678"):
    try:
        canon, rid = renderz.parse_url(good)
        check(f"ссылка ок: {good}", rid == 12345678 and canon.startswith("https://renderz.app/player/12345678"), canon)
    except renderz.RenderzError as e:
        check(f"ссылка ок: {good}", False, str(e))
for bad in ("http://renderz.app/player/1-x", "https://evil.com/player/1-x", "https://renderz.app.evil.com/player/1-x",
            "https://evilrenderz.app/player/1-x", "https://renderz.app/api/player/1", "https://renderz.app/api/players?q=a",
            "https://renderz.app/players", "https://user@renderz.app/player/1-x", "https://renderz.app:8443/player/1-x",
            "javascript:alert(1)", "", "https://renderz.app/player/1-x/../../api/x"):
    expect(f"ссылка отклонена: {bad or 'пусто'}", None, renderz.parse_url, bad)

rz = renderz.parse_html(FIXTURE, 12345678)
check("разбор: имя/OVR из og:title+JSON-LD", rz["name"] == "Kylian Mbappé" and rz["rating"] == 115)
check("разбор: позиция своей карточки (не соседней)", rz["position"] == "ST", str(rz["position"]))
check("разбор: сборная", rz["nation"] == "France")
expect("разбор: пустая страница", None, renderz.parse_html, "<html></html>", 1)
for nm in ("Mbappe", "Kylian Mbappé", "Мбаппе", "K. Mbappé", "KYLIAN MBAPPE"):
    m = renderz.compare({"name": nm, "rating": 115, "position": "НАП"}, rz)
    check(f"сравнение имени: {nm}", m["all"], str(m))
check("OVR не совпал", not renderz.compare({"name": "Mbappe", "rating": 114, "position": "ST"}, rz)["rating"])
check("позиция из доп.", renderz.compare({"name": "Mbappe", "rating": 115, "position": "LW", "alt_positions": "ST"}, rz)["position"])
check("позиция не совпала", not renderz.compare({"name": "Mbappe", "rating": 115, "position": "CB"}, rz)["position"])
check("другое имя", not renderz.compare({"name": "Haaland", "rating": 115, "position": "ST"}, rz)["name"])

fetches = []


def counting_fetch(url):
    fetches.append(url)
    return FIXTURE


info = renderz.lookup("https://www.renderz.app/player/12345678-kylian-mbappe", fetch=counting_fetch)
info2 = renderz.lookup(RZ_URL, fetch=lambda u: (_ for _ in ()).throw(AssertionError("сеть при кэше")))
check("lookup: одна загрузка, потом кэш", len(fetches) == 1 and info2["rating"] == 115 and info["renderz_id"] == 12345678)
cached = q1("SELECT data FROM renderz_cache WHERE url=?", (RZ_URL,))
check("кэш: только имя/OVR/позиция/сборная", set(json.loads(cached["data"])) <= {"name", "rating", "position", "nation", "aliases"})
ex("UPDATE renderz_cache SET fetched_at='2000-01-01 00:00:00'")
renderz.lookup(RZ_URL, fetch=counting_fetch)
check("кэш старше 24 ч → перезагрузка", len(fetches) == 2)

# авто-одобрение по RenderZ
auto = cards.create_card(OWNER_B, B, {"name": "Mbappe", "rating": 115, "position": "ST"},
                         renderz_url=RZ_URL, renderz_fetch=fixture_fetch)
check("RenderZ совпал → approved без судьи", auto["card"]["verify_status"] == "approved"
      and auto["card"]["checks"]["renderz"] == "match" and auto["card"]["renderz_url"] == RZ_URL, str(auto["card"]["checks"]))
mis = cards.create_card(OWNER_A, A, {"name": "Mbappe", "rating": 120, "position": "ST"},
                        renderz_url=RZ_URL, renderz_fetch=fixture_fetch)
check("RenderZ не совпал → pending + предупреждение", mis["card"]["verify_status"] == "pending"
      and mis["card"]["checks"]["renderz"] == "mismatch" and any("OVR" in w for w in mis["warnings"]), str(mis["warnings"]))
expect("плохая ссылка RenderZ при создании", "BAD_RENDERZ", cards.create_card, OWNER_A, A,
       {"name": "Busquets", "rating": 99, "position": "CDM"}, renderz_url="https://renderz.app/api/player/1")
appsettings.set_setting("cards_auto_approve_renderz", "0")
noauto = cards.create_card(OWNER_A, A, {"name": "Mbappe", "rating": 115, "position": "ST"},
                           renderz_url=RZ_URL, renderz_fetch=fixture_fetch)["card"]
check("авто-одобрение выключено → pending", noauto["verify_status"] == "pending" and noauto["checks"]["renderz"] == "match")
appsettings.set_setting("cards_auto_approve_renderz", "1")
appsettings.set_setting("renderz_verify", "0")
off = cards.create_card(OWNER_B, B, {"name": "Kaka", "rating": 108, "position": "CAM"},
                        renderz_url="https://renderz.app/player/555-kaka",
                        renderz_fetch=lambda u: (_ for _ in ()).throw(AssertionError("сеть при выключенной проверке")))["card"]
check("проверка RenderZ выключена → только ссылка", off["checks"]["renderz"] is None and off["verify_status"] == "pending"
      and off["renderz_url"] == "https://renderz.app/player/555-kaka")
appsettings.set_setting("renderz_verify", "1")

# OCR-сверка введённого с фото
up_o = cards.save_upload(OWNER_B, jpeg_with_exif(600, 800))
cards.mark_ocr(up_o["token"], card_ocr.parse_card(b"x")["fields"])
ocr_ok = cards.create_card(OWNER_B, B, {"name": "Kylian Mbappé", "rating": 115, "position": "ST", "nation": "France",
                                        "league": "Ligue 1"}, upload_token=up_o["token"])
check("OCR совпал с введённым", ocr_ok["card"]["checks"]["ocr"] is True)
up_o2 = cards.save_upload(OWNER_A, jpeg_with_exif(600, 800))
cards.mark_ocr(up_o2["token"], card_ocr.parse_card(b"x")["fields"])
ocr_bad = cards.create_card(OWNER_A, A, {"name": "Mbappé", "rating": 125, "position": "ST"}, upload_token=up_o2["token"])
check("OCR расходится с введённым", ocr_bad["card"]["checks"]["ocr"] is False)

# ===== рынок: фильтры =====
cards.decide(ocr_ok["card"]["id"], True, JUDGE)
lot_maldini = tr.create_lot(B, adm_in_b["id"], "auction", 3_000_000, 9_000_000, OWNER_B, hours=2)
lot_mbappe = tr.create_lot(B, auto["card"]["id"], "fix", 20_000_000, actor_tg=OWNER_B)
lot_mbappe2 = tr.create_lot(B, ocr_ok["card"]["id"], "auction", 1_000_000, actor_tg=OWNER_B, hours=1)


def ml(**kw):
    return tr.market_list(**kw)


def lot_cards(m):
    return {x["card_id"] for x in m["lots"]}


m = ml()
check("рынок: только approved", all(x["verify_status"] == "approved" for x in m["lots"] + m["free_agents"]))
check("лот содержит поля карточки", any(x["card_id"] == adm_in_b["id"] and x["stats"]["def"] == 130 and x["nation"] == "Italy"
                                         and x["seller_name"] == "Милан" and "min_bid" in x for x in m["lots"]))
check("агент — объект карточки + цена", any(a["id"] == fa["id"] and a["effective_price"] == 115 * 115 * 1000
                                           and a["seller_name"] is None and a["stats"]["pac"] == 140 for a in m["free_agents"]))
check("фасеты", "ST" in m["facets"]["positions"] and "CB" in m["facets"]["positions"]
      and {"France", "Italy", "Wales"} <= set(m["facets"]["nations"]) and "Serie A" in m["facets"]["leagues"], str(m["facets"]))
check("фасеты: порядок позиций FC", m["facets"]["positions"] == sorted(m["facets"]["positions"], key=cards.POSITIONS.index))
m = ml(q="мбаппе")
check("поиск кириллицей по латинскому имени", {auto["card"]["id"], ocr_ok["card"]["id"]} <= lot_cards(m)
      and any(a["id"] == fa["id"] for a in m["free_agents"]) and bale["id"] not in lot_cards(m))
check("поиск без диакритики", fa["id"] in {a["id"] for a in ml(q="MBAPPE")["free_agents"]})
check("поиск по лиге", lot_cards(ml(q="serie")) == {adm_in_b["id"]})
m = ml(position="LW")
check("позиция без alt", fa["id"] not in {a["id"] for a in m["free_agents"]})
check("позиция + alt=1", fa["id"] in {a["id"] for a in ml(position="ЛВ", alt=True)["free_agents"]})
check("позиция по-русски", lot_cards(ml(position="ЦЗ")) == {adm_in_b["id"]})
check("OVR диапазон", lot_cards(ml(ovr_min=112, ovr_max=116)) == {adm_in_b["id"], auto["card"]["id"], ocr_ok["card"]["id"]})
check("OVR старый параметр min_rating", bale["id"] in lot_cards(ml(min_rating=120)) and adm_in_b["id"] not in lot_cards(ml(min_rating=120)))
check("цена диапазон", lot_cards(ml(price_min=2_000_000, price_max=6_000_000)) == {adm_in_b["id"], bale["id"]})
check("цена старый параметр max_price", auto["card"]["id"] not in lot_cards(ml(max_price=10_000_000)))
check("kind=fix", {x["kind"] for x in ml(kind="fix")["lots"]} == {"fix"} and ml(kind="fix")["free_agents"] == [])
check("kind=auction", {x["kind"] for x in ml(kind="auction")["lots"]} == {"auction"})
check("kind=agent", ml(kind="agent")["lots"] == [] and ml(kind="agent")["free_agents"])
check("verified=1 — только RenderZ match", lot_cards(ml(verified=True)) == {auto["card"]["id"]} and ml(verified=True)["free_agents"] == [])
check("сборная/лига", lot_cards(ml(nation="italy")) == {adm_in_b["id"]} and
      {a["id"] for a in ml(league="Ligue 1")["free_agents"]} == {fa["id"]})
check("мин. статы", lot_cards(ml(stat_mins={"def": 120})) == {adm_in_b["id"]}
      and lot_cards(ml(stat_mins={"pac": 119, "sho": 118})) == {bale["id"]})
prices = [x["effective_price"] for x in ml(sort="price_asc")["lots"]]
check("сортировка цена ↑", prices == sorted(prices), str(prices))
prices = [x["effective_price"] for x in ml(sort="price_desc")["lots"]]
check("сортировка цена ↓", prices == sorted(prices, reverse=True))
ovrs = [x["rating"] for x in ml(sort="ovr_desc")["lots"]]
check("сортировка OVR ↓", ovrs == sorted(ovrs, reverse=True))
end = ml(sort="ending")["lots"]
check("сортировка ending: аукционы по времени", [x["card_id"] for x in end[:2]] == [ocr_ok["card"]["id"], adm_in_b["id"]]
      and end[-1]["kind"] == "fix")
expect("плохой sort", "BAD_FILTER", ml, sort="random")
expect("плохой kind", "BAD_FILTER", ml, kind="loan")
ocr.build_cascade = orig_cascade


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
    ha, hb, hj, hp, hadm = sign(OWNER_A), sign(OWNER_B), sign(JUDGE), sign(PLAYER), sign(ADMIN)
    try:
        r = await cl.get("/api/cards/my", headers=ha)
        d = await r.json()
        check("API my", r.status == 200 and d["club"]["id"] == A and d["can_edit"] and d["squad_max"] == 60
              and any(x["id"] == bale["id"] and x["on_market"] for x in d["cards"]))
        r = await cl.get("/api/cards/my", headers=hp)
        d = await r.json()
        check("API my без клуба", d["club"] is None and d["cards"] == [] and d["can_edit"] is False)
        r = await cl.get("/api/cards/my")
        check("API без initData → 401", r.status == 401)

        body = {"name": "Ansu Fati", "rating": 90, "position": "ЛВ", "alt_positions": "ST,CF", "pac": 110,
                "skill_moves": 4, "weak_foot": 3, "foot": "R", "nation": "Spain"}
        r = await cl.post("/api/cards", headers=ha, json=body)
        d = await r.json()
        ansu = d.get("card") or {}
        check("API create → pending", r.status == 200 and ansu.get("verify_status") == "pending"
              and ansu.get("alt_positions") == ["ST", "CF"] and ansu.get("position") == "LW", str(d)[:200])
        r = await cl.post("/api/cards", headers=ha, json={**body, "rating": 20})
        check("API create валидация → 400", r.status == 400 and (await r.json())["code"] == "BAD_INPUT")
        r = await cl.post("/api/cards", headers=ha, json={**body, "club_id": B})
        check("API create в чужой клуб → 403", r.status == 403)
        r = await cl.post("/api/cards", headers=ha, json={**body, "club_id": None})
        check("API владелец в агенты → 403", r.status == 403)
        r = await cl.post("/api/cards", headers=hp, json=body)
        check("API без клуба → 400 NO_CLUB", r.status == 400 and (await r.json())["code"] == "NO_CLUB")
        r = await cl.post("/api/cards", headers=hadm, json={**body, "name": "Free Guy", "club_id": None})
        d = await r.json()
        check("API админ → агент approved", r.status == 200 and d["card"]["club_id"] is None
              and d["card"]["verify_status"] == "approved")

        r = await cl.post(f"/api/cards/{ansu['id']}", headers=hb, json={"rating": 91})
        check("API чужую не править → 403", r.status == 403)
        r = await cl.post(f"/api/cards/{ansu['id']}", headers=ha, json={"rating": 91, "sho": 88})
        d = await r.json()
        check("API правка своей pending", r.status == 200 and d["card"]["rating"] == 91 and d["card"]["stats"]["sho"] == 88)
        r = await cl.post(f"/api/cards/{ansu['id']}/decide", headers=hp, json={"approve": True})
        check("API игрок не судит → 403", r.status == 403)
        r = await cl.post(f"/api/cards/{ansu['id']}/decide", headers=ha, json={"approve": True})
        check("API владелец не судит свою → 403", r.status == 403)
        r = await cl.get("/api/cards/queue", headers=hj)
        d = await r.json()
        item = next((x for x in d["cards"] if x["id"] == ansu["id"]), None)
        check("API очередь судьи", item is not None and item["club_name"] == "Барселона"
              and item["owner"]["telegram_id"] == OWNER_A)
        r = await cl.get("/api/cards/queue", headers=hp)
        check("API очередь игрока пуста", (await r.json())["cards"] == [])
        r = await cl.get(f"/api/cards/{ansu['id']}", headers=hj)
        d = await r.json()
        check("API detail: судья может решить", d["can_decide"] and not d["can_edit"] and d["history"][-1]["kind"] == "created")
        r = await cl.post(f"/api/cards/{ansu['id']}/decide", headers=hj, json={"approve": "yes"})
        check("API decide без bool → 400", r.status == 400)
        r = await cl.post(f"/api/cards/{ansu['id']}/decide", headers=hj, json={"approve": False, "note": "фото нет"})
        d = await r.json()
        check("API судья отклонил", r.status == 200 and d["card"]["verify_status"] == "rejected")
        r = await cl.get(f"/api/cards/{ansu['id']}", headers=ha)
        d = await r.json()
        check("API detail владельца: можно править/удалить", d["can_edit"] and d["can_delete"] and not d["can_decide"])
        r = await cl.get("/api/cards/99999", headers=ha)
        check("API detail 404", r.status == 404)
        r = await cl.delete(f"/api/cards/{ansu['id']}", headers=hb)
        check("API чужую не удалить → 403", r.status == 403)
        r = await cl.delete(f"/api/cards/{ansu['id']}", headers=ha)
        check("API удаление своей отклонённой", r.status == 200 and (await r.json())["ok"])

        # история карточки: подписание агента
        tr.sign_free_agent(A, fa["id"], OWNER_A)
        r = await cl.get(f"/api/cards/{fa['id']}", headers=hp)
        d = await r.json()
        check("API история: свободный агент", d["history"][0]["kind"] == "free_agent"
              and d["history"][0]["to_club"] == "Барселона" and d["card"]["club_name"] == "Барселона")

        # фото / OCR по API
        form = FormData()
        form.add_field("image", jpeg_with_exif(1200, 900), filename="card.jpg", content_type="image/jpeg")
        r = await cl.post("/api/cards/upload", headers=ha, data=form)
        d = await r.json()
        check("API upload", r.status == 200 and len(d["token"]) == 32 and d["image_url"].startswith("/media/cards/"))
        r2 = await cl.get(d["image_url"])
        check("media: отдаётся jpeg", r2.status == 200 and r2.headers["Content-Type"] == "image/jpeg"
              and (await r2.read())[:2] == b"\xff\xd8")
        for bad in ("/media/cards/..%2F..%2Fgate20.db", "/media/cards/../../etc/passwd", "/media/cards/abc.jpg",
                    "/media/cards/" + "A" * 32 + ".jpg", "/media/cards/%2e%2e%2fgate20.db"):
            r3 = await cl.get(bad)
            check(f"media: {bad} → 404", r3.status == 404, str(r3.status))
        form = FormData()
        form.add_field("image", b"garbage" * 100, filename="x.jpg", content_type="image/jpeg")
        r = await cl.post("/api/cards/upload", headers=ha, data=form)
        check("API upload мусор → 400", r.status == 400 and (await r.json())["code"] == "BAD_IMAGE")
        form = FormData()
        form.add_field("image", b"\xff" * (8 * 1024 * 1024 + 10), filename="x.jpg", content_type="image/jpeg")
        r = await cl.post("/api/cards/upload", headers=ha, data=form)
        check("API upload > 8 МБ → 400 TOO_BIG", r.status == 400 and (await r.json())["code"] == "TOO_BIG")
        r = await cl.post("/api/cards/upload", headers=ha, json={"x": 1})
        check("API upload без файла → 400", r.status == 400)

        ocr.build_cascade = lambda: [("mock", mock_vision)]
        form = FormData()
        form.add_field("image", jpeg_with_exif(800, 1100), filename="card.jpg", content_type="image/jpeg")
        r = await cl.post("/api/cards/ocr", headers=hb, data=form)
        d = await r.json()
        check("API OCR multipart", r.status == 200 and d["fields"].get("rating") == 115 and d["provider"] == "mock"
              and d["token"] and d["confidence"] == 0.93, str(d)[:200])
        tok = (await (await cl.post("/api/cards/upload", headers=hb, data=_form())).json())["token"]
        r = await cl.post("/api/cards/ocr", headers=hb, json={"token": tok})
        d = await r.json()
        check("API OCR по token", r.status == 200 and d["fields"].get("position") == "ST" and d["token"] == tok)
        r = await cl.post("/api/cards/ocr", headers=ha, json={"token": tok})
        check("API OCR чужой token → 400", r.status == 400)
        ocr.build_cascade = lambda: [("mock", broken)]
        r = await cl.post("/api/cards/ocr", headers=hb, json={"token": tok})
        d = await r.json()
        check("API OCR сбой → 200 и пустые поля", r.status == 200 and d["fields"] == {} and d["warnings"])
        form = FormData()
        form.add_field("image", b"nope" * 50, filename="x.jpg", content_type="image/jpeg")
        r = await cl.post("/api/cards/ocr", headers=hb, data=form)
        check("API OCR плохая картинка → 400", r.status == 400)
        ex("UPDATE card_uploads SET ocr_at=? WHERE telegram_id=?", (cards._ts(), OWNER_B))
        for _ in range(cards.OCR_PER_HOUR):
            ex("INSERT INTO card_uploads (token, telegram_id, path, ocr_at, created_at) VALUES (?,?,?,?,?)",
               (os.urandom(16).hex(), OWNER_B, "cards/x.jpg", cards._ts(), cards._ts()))
        r = await cl.post("/api/cards/ocr", headers=hb, json={"token": tok})
        check("API OCR лимит → 429", r.status == 429)
        ocr.build_cascade = orig_cascade

        # RenderZ по API (сеть подменена фикстурой)
        orig_fetch = renderz._fetch_html
        renderz._fetch_html = fixture_fetch
        ex("DELETE FROM renderz_cache")
        r = await cl.post("/api/cards/renderz-check", headers=ha,
                          json={"url": RZ_URL, "fields": {"name": "Мбаппе", "rating": 115, "position": "ST"}})
        d = await r.json()
        check("API renderz-check совпал", r.status == 200 and d["ok"] and d["match"]["all"]
              and d["renderz"] == {"name": "Kylian Mbappé", "rating": 115, "position": "ST", "nation": "France",
                                   "url": RZ_URL, "renderz_id": 12345678}, str(d))
        r = await cl.post("/api/cards/renderz-check", headers=ha, json={"url": RZ_URL, "fields": {"name": "Mbappe", "rating": 99, "position": "ST"}})
        d = await r.json()
        check("API renderz-check не совпал", d["match"] == {"name": True, "rating": False, "position": True, "all": False})
        r = await cl.post("/api/cards/renderz-check", headers=ha, json={"url": RZ_URL})
        check("API renderz-check без полей → match null", (await r.json())["match"] is None)
        r = await cl.post("/api/cards/renderz-check", headers=ha, json={"url": "https://futbin.com/player/1"})
        check("API renderz-check чужой сайт → 400", r.status == 400 and (await r.json())["error"])
        appsettings.set_setting("renderz_verify", "0")
        r = await cl.post("/api/cards/renderz-check", headers=ha, json={"url": RZ_URL})
        check("API renderz-check выключен → 400", r.status == 400)
        appsettings.set_setting("renderz_verify", "1")
        renderz._fetch_html = orig_fetch

        # рынок по API
        r = await cl.get("/api/transfers/market?q=mbappe&sort=price_asc&kind=fix", headers=ha)
        d = await r.json()
        check("API рынок: фильтры", r.status == 200 and [x["card_id"] for x in d["lots"]] == [auto["card"]["id"]]
              and d["free_agents"] == [] and "facets" in d)
        r = await cl.get("/api/transfers/market?position=CB&def_min=120", headers=ha)
        check("API рынок: позиция+стат", [x["card_id"] for x in (await r.json())["lots"]] == [adm_in_b["id"]])
        r = await cl.get("/api/transfers/market", headers=hb)
        check("API рынок: свои лоты скрыты", all(x["seller_club_id"] != B for x in (await r.json())["lots"]))
        r = await cl.get("/api/transfers/market?min_rating=120&max_price=6000000", headers=hb)
        check("API рынок: старые параметры", [x["card_id"] for x in (await r.json())["lots"]] == [bale["id"]])
        r = await cl.get("/api/transfers/market?ovr_min=abc", headers=ha)
        check("API рынок: плохое число → 400", r.status == 400 and (await r.json())["code"] == "BAD_FILTER")
        r = await cl.get("/api/transfers/market?sort=zzz", headers=ha)
        check("API рынок: плохой sort → 400", r.status == 400)
    finally:
        await cl.close()


def _form():
    form = FormData()
    form.add_field("image", jpeg_with_exif(700, 900), filename="c.jpg", content_type="image/jpeg")
    return form


asyncio.run(api_checks())

# позиция не прочиталась → «частично», не расхождение; окно не залезает в соседнюю карточку
_rz = {"name": "Gareth Bale", "rating": 122, "position": None, "aliases": ["Bale"]}
_m = renderz.compare({"name": "Bale", "rating": 122, "position": "ПВ"}, _rz)
check("renderz: без позиции → partial, не all", _m["partial"] and not _m["all"] and _m["position"] is None, str(_m))
_h = 'x={id:1,cardName:"A",rating:90},{id:2,cardName:"B",rating:91,position:"CB"}'
check("renderz: позиция соседа не подхватывается", renderz.parse_html(
    '<meta property="og:title" content="A — 90 OVR · FC Mobile | RenderZ">' + _h, 1)["position"] is None)

print()
print("GATE 20:", "ВСЁ ЗЕЛЁНОЕ" if not fails else f"ПРОВАЛЫ: {fails}")
sys.exit(1 if fails else 0)
