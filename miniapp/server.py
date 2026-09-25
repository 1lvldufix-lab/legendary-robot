"""Сервер мини-аппа: тонкая сборка aiohttp (авторизация в helpers, API в routes).

Запускается в одном asyncio-цикле с ботом (bot/main.py) или standalone:
    python miniapp/server.py
"""
import sys
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parent.parent
for p in (str(PROJECT_ROOT / "bot"), str(PROJECT_ROOT)):
    if p not in sys.path:
        sys.path.insert(0, p)

import config  # noqa: E402
import db as appdb  # noqa: E402
from aiohttp import web  # noqa: E402

from .helpers import require_active_user, require_user, upsert_user  # noqa: E402,F401
from .routes import LOGO_DIR, setup_routes  # noqa: E402

STATIC_DIR = Path(__file__).resolve().parent / "static"


async def api_bootstrap(request):
    u = require_active_user(request)
    c = appdb.db()
    divisions = [dict(r) for r in c.execute(
        "SELECT * FROM divisions WHERE is_active=1 ORDER BY sort_order").fetchall()]
    c.close()
    from .helpers import user_payload
    return web.json_response({
        "status": "ok",
        "user": user_payload(u),
        "divisions": divisions,
        "open_tours_count": 0,
    })


async def api_wallet(request):
    u = require_active_user(request)
    return web.json_response({"status": "ok", "balance": u["balance"]})


async def index(request):
    return web.FileResponse(STATIC_DIR / "index.html")


def build_app() -> web.Application:
    app = web.Application()
    app.router.add_get("/", index)
    app.router.add_get("/app", index)
    app.router.add_get("/api/bootstrap", api_bootstrap)
    app.router.add_get("/api/wallet", api_wallet)
    setup_routes(app)
    app.router.add_static("/static/", STATIC_DIR)
    if LOGO_DIR.exists():
        app.router.add_static("/assets/logos/", LOGO_DIR)
    return app


async def start_site() -> None:
    """Поднимает HTTP-сервер в текущем asyncio-цикле (вызывать из post_init)."""
    STATIC_DIR.mkdir(exist_ok=True)
    appdb.init_db()
    runner = web.AppRunner(build_app())
    await runner.setup()
    site = web.TCPSite(runner, config.WEBAPP_HOST, config.WEBAPP_PORT)
    await site.start()
    print(f"[мини-апп] http://{config.WEBAPP_HOST}:{config.WEBAPP_PORT}/app")


if __name__ == "__main__":
    # standalone-режим: живём и слушаем порт, не выходим (per план 09 п.3)
    appdb.init_db()
    import aiohttp.web as _web
    _web.run_app(build_app(), host=config.WEBAPP_HOST, port=config.WEBAPP_PORT,
                 print=lambda s: print(f"[мини-апп] http://{config.WEBAPP_HOST}:{config.WEBAPP_PORT}/app"))
