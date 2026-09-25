"""API прогресса: достижения, зал славы, fair-play учёт тренировок (+ админка нарушений)."""
from aiohttp import web

from .helpers import require_active_user
from .routes import _user_row, err, j  # routes.py кладёт bot/ в sys.path

import achievements as ach  # noqa: E402
import training as tr  # noqa: E402


async def _body(request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


async def api_achievements(request):
    me = _user_row(require_active_user(request))
    items = ach.evaluate(me["id"])
    return j({"status": "ok", "achievements": items, "summary": {
        "total": len(items),
        "unlocked": sum(a["is_unlocked"] for a in items),
        "claimable": sum(1 for a in items if a["is_unlocked"] and not a["is_claimed"]),
    }})


async def api_achievement_claim(request):
    me = _user_row(require_active_user(request))
    try:
        r = ach.claim(me["id"], request.match_info["code"])
    except ach.AchievementError as e:
        return err(e.message, 404 if e.code == "NOT_FOUND" else 400, e.code)
    return j({"status": "ok", **r})


async def api_hall(request):
    me = _user_row(require_active_user(request))
    try:
        data = ach.hall(request.query.get("tab", "balance"), viewer_id=me["id"])
    except ach.AchievementError as e:
        return err(e.message, 400, e.code)
    return j({"status": "ok", **data})


async def api_training(request):
    me = _user_row(require_active_user(request))
    if request.method == "POST":
        b = await _body(request)
        try:
            r = tr.add_entry(me, b.get("card_id"), b.get("kind", "ovr"), b.get("count", 1), b.get("note", ""))
        except tr.TrainingError as e:
            return err(e.message, 400, e.code)
        return j({"status": "ok", "entry": r, **tr.overview(me)})
    return j({"status": "ok", **tr.overview(me)})


def _admin_or_403(request):
    me = _user_row(require_active_user(request))
    return me if me["is_admin"] else None


async def api_admin_violations(request):
    if not _admin_or_403(request):
        return err("Только админ", 403)
    return j({"status": "ok", "violations": tr.list_violations(request.query.get("status")),
              "limit": tr.weekly_limit()})


async def api_admin_violation_resolve(request):
    me = _admin_or_403(request)
    if not me:
        return err("Только админ", 403)
    b = await _body(request)
    try:
        vid = int(request.match_info["vid"])
        r = tr.resolve_violation(vid, b.get("action", "resolved"), b.get("note", ""), me["telegram_id"])
    except ValueError:
        return err("Неверный id")
    except tr.TrainingError as e:
        return err(e.message, 404 if e.code == "NOT_FOUND" else 400, e.code)
    return j({"status": "ok", **r})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/achievements", api_achievements)
    r.add_post("/api/achievements/{code}/claim", api_achievement_claim)
    r.add_get("/api/leaderboard/hall", api_hall)
    r.add_get("/api/training", api_training)
    r.add_post("/api/training", api_training)
    r.add_get("/api/admin/training/violations", api_admin_violations)
    r.add_post("/api/admin/training/violations/{vid}/resolve", api_admin_violation_resolve)
