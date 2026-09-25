"""ГЕЙТ 18 (инфра): /scan по ссылке Challenge Place.
- валидация URL (SSRF): только https + challenge.place/challengeplace.com и поддомены;
- plain-путь: локальный aiohttp отдаёт HTML с tests/cp_fixture.json (интернет не трогаем,
  транспорт cp_fetch._open перенаправлен на 127.0.0.1), редирект на чужой хост — отказ;
- без playwright — понятная ошибка с командой установки;
- /scan <url> в хендлере импортирует матчи;
- если playwright установлен — рендер локальной SPA-страницы headless Chromium (иначе SKIP)."""
import asyncio
import json
import os
import sys
import threading
from types import SimpleNamespace

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "bot"))
os.environ.setdefault("BOT_TOKEN", "123456:TEST")
os.environ["DB_PATH"] = "/tmp/gate18.db"
os.environ["ADMIN_IDS"] = "555"
if os.path.exists("/tmp/gate18.db"):
    os.remove("/tmp/gate18.db")

from aiohttp import web  # noqa: E402

import cp_fetch  # noqa: E402
import db  # noqa: E402
import handlers_debts  # noqa: E402
import league  # noqa: E402
import parser_cp  # noqa: E402

db.init_db()
fails = []


def check(name, cond, detail=""):
    print(f"[{'OK ' if cond else 'FAIL'}] {name} {detail}")
    if not cond:
        fails.append(name)


def rejects(url):
    try:
        cp_fetch.validate_url(url)
        return False
    except cp_fetch.CPFetchError:
        return True


# 1) валидация URL
good = ["https://challenge.place/c/6ab2591ee769ae73c5b7a622", "https://www.challenge.place/c/x?tab=1",
        "https://challengeplace.com/c/x", "https://m.challengeplace.com/c/x", "HTTPS://Challenge.Place/c/x",
        "https://challenge.place:443/c/x"]
bad = ["http://challenge.place/c/x", "ftp://challenge.place/c/x", "https://evil.com/c/x",
       "https://challenge.place.evil.com/c/x", "https://evilchallenge.place/c/x",
       "https://127.0.0.1/c/x", "https://[::1]/c/x", "https://10.0.0.5/", "https://169.254.169.254/latest",
       "https://user:pw@challenge.place/c/x", "https://challenge.place@evil.com/",
       "https://challenge.place:8443/c/x", "file:///etc/passwd", "", "javascript:alert(1)"]
check("валидные ссылки проходят", all(not rejects(u) for u in good), str([u for u in good if rejects(u)]))
check("http/чужие хосты/IP/логин/порт — отказ", all(rejects(u) for u in bad), str([u for u in bad if not rejects(u)]))
check("нормализация: фрагмент и регистр", cp_fetch.validate_url("https://Challenge.Place/c/x#frag")
      == "https://challenge.place/c/x")
try:
    cp_fetch._check_resolved("localhost")
    check("хост во внутреннюю сеть — отказ", False)
except cp_fetch.CPFetchError:
    check("хост во внутреннюю сеть — отказ", True)

# 2) локальный сервер: SSR-страница, Cloudflare-заглушка, SPA без SSR, редирект наружу
with open(os.path.join(ROOT, "tests", "cp_fixture.json"), encoding="utf-8") as f:
    fixture = json.load(f)
state_js = json.dumps({"rooms": {"room-challenge-dashboard": fixture}}, ensure_ascii=False)
SSR_HTML = f"<html><body><div id=app></div><script>window.__INITIAL_STATE__={state_js};</script></body></html>"
CF_HTML = "<html><title>Just a moment...</title><body>Checking your browser</body></html>"
# SPA: стейт появляется из JS с задержкой, исходного <script>window.__INITIAL_STATE__=… нет
SPA_HTML = ("<html><body><div id=app>loading</div><script>const d = " + state_js + ";"
            "setTimeout(() => { window['__INITIAL_STATE__'] = d;"
            " document.getElementById('app').textContent = 'ok'; }, 300);</script></body></html>")
hits = []


async def h_page(request):
    hits.append(request.path)
    ua = request.headers.get("User-Agent", "")
    name = request.match_info["name"]
    if name == "ok":
        return web.Response(text=SSR_HTML, content_type="text/html", headers={"X-UA": ua})
    if name == "spa":
        return web.Response(text=SPA_HTML, content_type="text/html")
    if name == "redirect":
        raise web.HTTPFound("https://evil.example/steal")
    return web.Response(text=CF_HTML, status=403, content_type="text/html")


loop = asyncio.new_event_loop()
ready = threading.Event()
port_box = {}


def serve():
    asyncio.set_event_loop(loop)
    app = web.Application()
    app.router.add_get("/c/{name}", h_page)
    runner = web.AppRunner(app)
    loop.run_until_complete(runner.setup())
    site = web.TCPSite(runner, "127.0.0.1", 0)
    loop.run_until_complete(site.start())
    port_box["port"] = site._server.sockets[0].getsockname()[1]
    ready.set()
    loop.run_forever()


threading.Thread(target=serve, daemon=True).start()
ready.wait(10)
BASE = f"http://127.0.0.1:{port_box['port']}"


def local(url):
    """https://challenge.place/c/x → локальный сервер."""
    return BASE + url.split("challenge.place", 1)[1]


real_open = cp_fetch._open
cp_fetch._open = lambda url, timeout: real_open(local(url), timeout)
cp_fetch._check_resolved = lambda host: None   # без DNS/интернета

html = cp_fetch.fetch_plain("https://challenge.place/c/ok")
st = parser_cp.parse_state(html) if html else {"competitors": [], "matches": []}
check("plain: HTML с дашбордом → 31 competitor", len(st["competitors"]) == 31, str(len(st["competitors"])))
check("plain: 3 матча со счётом",
      sum(1 for m in st["matches"] if m["home_score"] is not None) == 3)
check("plain: Cloudflare 403 → None (нужен браузер)", cp_fetch.fetch_plain("https://challenge.place/c/cf") is None)
check("plain: SPA без SSR → None", cp_fetch.fetch_plain("https://challenge.place/c/spa") is None)
try:
    cp_fetch.fetch_plain("https://challenge.place/c/redirect")
    check("редирект на чужой хост — отказ", False)
except cp_fetch.CPFetchError as e:
    check("редирект на чужой хост — отказ", "challenge.place" in str(e), str(e))
check("fetch_page: plain хватило, браузер не нужен",
      cp_fetch.has_dashboard(cp_fetch.fetch_page("https://challenge.place/c/ok")))

# 3) без playwright — понятная ошибка с командой установки
saved = {k: sys.modules.get(k) for k in ("playwright", "playwright.sync_api")}
sys.modules["playwright"] = None
sys.modules["playwright.sync_api"] = None
progress_msgs = []
try:
    cp_fetch.fetch_page("https://challenge.place/c/cf", progress=progress_msgs.append)
    check("нет playwright → ошибка с инструкцией", False)
except cp_fetch.CPFetchError as e:
    check("нет playwright → ошибка с инструкцией", cp_fetch.INSTALL_HINT in str(e) and "Ctrl+S" in str(e),
          str(e)[:80])
check("прогресс-сообщение перед браузером", len(progress_msgs) == 1, str(progress_msgs))
for k, v in saved.items():
    if v is None:
        sys.modules.pop(k, None)
    else:
        sys.modules[k] = v

# 4) хендлер /scan <url>: валидация, импорт, прогресс
tid = league.create_league_season("КП-сезон", 1, "manual", 1, 3)
c = db.db()
for comp in st["competitors"]:
    c.insert_returning_id("INSERT INTO clubs (name, budget) VALUES (?, 20000000)", (comp["name"],))
c.commit()
c.close()


def fake_update(uid):
    replies = []

    async def reply_text(text, **kw):
        replies.append(text)
    return SimpleNamespace(effective_user=SimpleNamespace(id=uid),
                           message=SimpleNamespace(reply_text=reply_text)), replies


def run_scan(uid, args):
    upd, replies = fake_update(uid)
    ctx = SimpleNamespace(args=args, bot=SimpleNamespace(send_message=None))
    asyncio.run(handlers_debts.cmd_scan(upd, ctx))
    return replies


r = run_scan(555, ["http://challenge.place/c/ok"])
check("/scan http:// — отказ", r and r[-1].startswith("⛔"), str(r))
r = run_scan(555, ["https://169.254.169.254/latest/meta-data"])
check("/scan IP — отказ", r and r[-1].startswith("⛔"), str(r))
hits.clear()
r = run_scan(777, ["https://challenge.place/c/ok"])
check("/scan не-админ — отказ, сеть не трогали", r and "⛔" in r[-1] and not hits, str(r))
r = run_scan(555, ["https://challenge.place/c/ok", str(tid)])
check("/scan <url> root: импорт 3 матчей", any("создано 3" in x for x in r), str(r)[-200:])
check("/scan <url>: прогресс-сообщения", any("Загружаю" in x for x in r) and any("импортирую" in x for x in r))
c = db.db()
n = c.execute("SELECT COUNT(*) AS n FROM matches WHERE source='challenge-place'").fetchone()["n"]
c.close()
check("матчи в БД помечены source", n == 3, str(n))

# 5) реальный headless Chromium (если установлен)
try:
    import playwright.sync_api  # noqa: F401
    have_pw = True
except ImportError:
    have_pw = False
if not have_pw:
    print("[SKIP] playwright не установлен — рендер не проверен "
          f"({cp_fetch.INSTALL_HINT})")
else:
    try:
        html = cp_fetch._render(BASE + "/c/spa", timeout=30)
        rendered_ok = True
    except cp_fetch.CPFetchError as e:
        rendered_ok = False
        print(f"[SKIP] Chromium не запустился: {str(e)[:160]}")
    if rendered_ok:
        st2 = parser_cp.parse_state(html)
        check("Chromium: SPA-стейт из JS → 31 competitor", len(st2["competitors"]) == 31)
        html = cp_fetch._render(BASE + "/c/ok", timeout=30)
        check("Chromium: SSR-страница парсится", cp_fetch.has_dashboard(html))
        try:
            cp_fetch._render(BASE + "/c/ok", timeout=30, check_url=cp_fetch.validate_url)
            check("Chromium: итоговый URL проверяется", False)
        except cp_fetch.CPFetchError:
            check("Chromium: итоговый URL проверяется", True)
        # полный путь fetch_page: plain не дал данных → браузер
        real_render = cp_fetch._render
        cp_fetch.fetch_rendered = lambda url, timeout=60: real_render(local(url), timeout)
        msgs = []
        html = cp_fetch.fetch_page("https://challenge.place/c/spa", timeout=30, progress=msgs.append)
        check("fetch_page: plain → Chromium фолбэк", cp_fetch.has_dashboard(html) and len(msgs) == 1)
        try:
            cp_fetch.fetch_page("https://challenge.place/c/cf", timeout=5)
            check("Chromium: Cloudflare-заглушка → совет сохранить страницу", False)
        except cp_fetch.CPFetchError as e:
            check("Chromium: Cloudflare-заглушка → совет сохранить страницу", "Ctrl+S" in str(e), str(e)[:60])

loop.call_soon_threadsafe(loop.stop)
print(f"\nГЕЙТ 18: {'PASS' if not fails else 'FAIL ' + str(fails)}")
sys.exit(1 if fails else 0)
