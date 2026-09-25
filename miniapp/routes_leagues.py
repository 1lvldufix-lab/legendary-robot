"""API админки: лиги с именованными дивизионами, клубы, участники (ник, клуб, судья).
Структура и участники — админы мини-аппа (root тоже); выдача судей — только root."""
from aiohttp import web

import config
import league_admin as la

from .helpers import require_active_user
from .routes import _audit_now, _require_admin, err, j


def _admin(request) -> dict:
    return _require_admin(require_active_user(request))


async def _body(request) -> dict:
    try:
        data = await request.json()
    except Exception:
        return {}
    return data if isinstance(data, dict) else {}


def _int(v, name: str) -> int:
    try:
        return int(v)
    except (TypeError, ValueError):
        raise la.LeagueError(f"{name} — число")


def _handler(fn):
    async def wrapped(request):
        try:
            me = _admin(request)
        except PermissionError as e:
            return err(str(e), 403)
        try:
            return await fn(request, me)
        except la.LeagueError as e:
            return err(str(e))
    return wrapped


@_handler
async def api_leagues(request, me):
    if request.method == "POST":
        b = await _body(request)
        names = b.get("divisions")
        names = la.parse_division_names("\n".join(names) if isinstance(names, list) else str(names or ""))
        tid = la.create_league(b.get("name"), names, _int(b.get("rounds", 2), "Круги"),
                               _int(b.get("tour_days") or config.DEFAULT_TOUR_DAYS, "Дней на тур"),
                               _int(b.get("promote_count", 3), "Повышение/вылет"))
        _audit_now(me["telegram_id"], "league_create", f"#{tid} {b.get('name')} div={names}")
        return j({"status": "ok", "tournament_id": tid, "leagues": la.overview()})
    return j({"leagues": la.overview(), "catalog": la.catalog(),
              "is_root": me["telegram_id"] in config.ADMIN_IDS})


@_handler
async def api_league(request, me):
    tid = _int(request.match_info["tid"], "id лиги")
    b = await _body(request)
    if b.get("name"):
        la._league_or_error(tid)
        la.rename_league(tid, b["name"])
    if b.get("promote_count") is not None:
        la._league_or_error(tid)
        la.set_promote_count(tid, _int(b["promote_count"], "Повышение/вылет"))
    if b.get("add_division"):
        la.add_division(tid, b["add_division"])
    result = {}
    if b.get("calendar"):
        result = la.generate_calendar(tid, b.get("division_id"))
    _audit_now(me["telegram_id"], "league_update", f"#{tid} {sorted(b)}")
    return j({"status": "ok", **result, "leagues": la.overview()})


@_handler
async def api_division(request, me):
    did = _int(request.match_info["did"], "id дивизиона")
    if request.method == "DELETE":
        la.delete_division(did)
    else:
        b = await _body(request)
        if b.get("name"):
            la.rename_division(did, b["name"])
        if b.get("move") in ("up", "down"):
            la.move_division(did, b["move"])
        if b.get("club"):
            la.add_club(did, b["club"], b.get("owner") or None, bool(b.get("custom")))
    _audit_now(me["telegram_id"], "division_update", f"#{did} {request.method}")
    return j({"status": "ok", "leagues": la.overview()})


@_handler
async def api_club(request, me):
    cid = _int(request.match_info["cid"], "id клуба")
    b = await _body(request)
    if b.get("remove"):
        la.remove_club_from_division(cid)
    elif b.get("owner"):
        la.assign_owner(cid, b["owner"])
    elif b.get("unassign"):
        la.unassign_owner(cid)
    _audit_now(me["telegram_id"], "club_update", f"#{cid} {sorted(b)}")
    return j({"status": "ok", "leagues": la.overview()})


@_handler
async def api_people(request, me):
    if request.method == "POST":  # предрегистрация по Telegram ID (+ ник)
        b = await _body(request)
        tg = str(b.get("telegram_id") or "").strip()
        if not tg.isdigit():
            raise la.LeagueError("Telegram ID — число (игрок узнаёт его командой /myid)")
        la.resolve_or_create_player(tg)
        if b.get("nick"):
            la.set_nick(tg, b["nick"])
        _audit_now(me["telegram_id"], "player_register", f"tg={tg} nick={b.get('nick')}")
    return j({"people": la.people(request.rel_url.query.get("q", ""))[:200]})


@_handler
async def api_person(request, me):
    tg = request.match_info["tg"]
    b = await _body(request)
    changes = []
    if "nick" in b:
        la.set_nick(tg, b.get("nick"))
        changes.append(f"nick={b.get('nick')}")
    if "club_id" in b:
        la.set_player_club(tg, b.get("club_id") or None)
        changes.append(f"club={b.get('club_id')}")
    if b.get("judge_tournament"):
        if me["telegram_id"] not in config.ADMIN_IDS:
            return err("Судей назначает только root", 403)
        on = la.toggle_judge(_int(b["judge_tournament"], "id турнира"), tg)
        changes.append(f"judge={'on' if on else 'off'}")
    _audit_now(me["telegram_id"], "person_update", f"tg={tg} {' '.join(changes)}")
    person = next((p for p in la.people() if str(p["telegram_id"]) == str(tg).lstrip("@")
                   or (p["username"] or "").lower() == str(tg).lstrip("@").lower()), None)
    return j({"status": "ok", "person": person})


def setup(app: web.Application) -> None:
    r = app.router
    r.add_get("/api/admin/leagues", api_leagues)
    r.add_post("/api/admin/leagues", api_leagues)
    r.add_post("/api/admin/leagues/{tid}", api_league)
    r.add_post("/api/admin/divisions/{did}", api_division)
    r.add_delete("/api/admin/divisions/{did}", api_division)
    r.add_post("/api/admin/clubs/{cid}", api_club)
    r.add_get("/api/admin/people", api_people)
    r.add_post("/api/admin/people", api_people)
    r.add_post("/api/admin/people/{tg}", api_person)
