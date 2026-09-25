"""API аукционов и обменов (план 08 B2–B3): ставки, карточка лота, клубы/составы
для обмена, очередь судьи. Подключается автоматически (server._setup_feature_routes)."""
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "bot"))

from aiohttp import web  # noqa: E402

import db as appdb  # noqa: E402
import transfers as tr  # noqa: E402

from .helpers import require_active_user  # noqa: E402


def _j(data, status=200):
    return web.json_response(data, status=status)


def _err(text, status=400, code=None):
    return web.json_response({"status": "error", "error": text, "code": code}, status=status)


def _my_club_id(u: dict) -> int | None:
    c = appdb.db()
    row = c.execute("SELECT cp.club_id FROM club_players cp JOIN players p ON p.id=cp.player_id "
                    "WHERE p.telegram_id=?", (u["telegram_id"],)).fetchone()
    c.close()
    return row["club_id"] if row else None


def _logo(path: str | None) -> str | None:
    return f"/assets/logos/{Path(path).name}" if path else None


async def api_lot_detail(request):
    u = require_active_user(request)
    try:
        lot = tr.lot_detail(int(request.match_info["lot_id"]), _my_club_id(u))
    except ValueError:
        return _err("Плохой id лота", 400, "BAD_INPUT")
    except tr.TransferError as e:
        return _err(str(e), 404, e.code)
    lot["seller_logo"] = _logo(lot.get("seller_logo"))
    return _j(lot)


async def api_lot_bid(request):
    u = require_active_user(request)
    club_id = _my_club_id(u)
    if not club_id:
        return _err("У тебя нет клуба — попроси root выдать", 400, "NO_CLUB")
    try:
        body = await request.json()
        lot_id = int(request.match_info["lot_id"])
        amount = body.get("amount")
    except (ValueError, TypeError, AttributeError):
        return _err("Нужен JSON {amount}", 400, "BAD_INPUT")
    try:
        r = tr.place_bid(club_id, lot_id, amount, u["telegram_id"])
    except tr.TransferError as e:
        return _err(str(e), 400, e.code)
    return _j({"status": "ok", "result": r["status"], **{k: v for k, v in r.items() if k != "status"}})


async def api_clubs(request):
    u = require_active_user(request)
    mine = _my_club_id(u)
    clubs = tr.clubs_list(exclude_club_id=mine)
    for cl in clubs:
        cl["logo"] = _logo(cl.pop("logo_path", None))
    return _j({"clubs": clubs, "my_club_id": mine})


async def api_club_squad(request):
    require_active_user(request)
    try:
        club_id = int(request.match_info["club_id"])
    except ValueError:
        return _err("Плохой id клуба", 400, "BAD_INPUT")
    return _j({"club_id": club_id, "squad": tr.club_squad(club_id)})


async def api_judge_queue(request):
    u = require_active_user(request)
    return _j({"deals": tr.judge_queue(u["telegram_id"])})


async def api_exchange_decline(request):
    u = require_active_user(request)
    try:
        r = tr.decline_exchange(int(request.match_info["transfer_id"]), u["telegram_id"])
    except ValueError:
        return _err("Плохой id", 400, "BAD_INPUT")
    except tr.TransferError as e:
        return _err(str(e), 400, e.code)
    return _j({"status": "ok", **r})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/transfers/lots/{lot_id}", api_lot_detail)
    r.add_post("/api/transfers/lots/{lot_id}/bid", api_lot_bid)
    r.add_get("/api/transfers/clubs", api_clubs)
    r.add_get("/api/transfers/clubs/{club_id}/squad", api_club_squad)
    r.add_get("/api/transfers/judge", api_judge_queue)
    r.add_post("/api/transfers/exchange/{transfer_id}/decline", api_exchange_decline)
