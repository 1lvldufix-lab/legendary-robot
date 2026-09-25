"""API сетки кубков: GET /api/cups[?tournament_id=N] — стадии, серии, клубы, счёт серий, игры.

Подхватывается server._setup_feature_routes (routes_<фича>.py с setup(app)).
"""
from aiohttp import web

from .helpers import require_active_user
from .routes import _club_public, _club_row  # .routes кладёт bot/ в sys.path

import db as appdb  # noqa: E402
import league  # noqa: E402
from clubs_catalog import FORMAT_NAMES  # noqa: E402


def _game_public(tie: dict, g: dict) -> dict:
    sa, sb, pa, pb, _ = league._game_for_a(tie, g)
    return {
        "id": g["id"], "game_no": g["game_in_tie"], "status": g["status"],
        "home_club_id": g["home_club_id"], "away_club_id": g["away_club_id"],
        "score_home": g["score1"], "score_away": g["score2"],
        "pens_home": g["pens1"], "pens_away": g["pens2"],
        # тот же счёт относительно клуба А серии — так проще рисовать сетку
        "score_a": sa, "score_b": sb, "pens_a": pa, "pens_b": pb,
    }


def cup_payload(tournament_id: int) -> dict | None:
    br = league.cup_bracket(tournament_id)
    if not br:
        return None
    t = br["tournament"]
    c = appdb.db()
    clubs: dict = {}

    def club(cid):
        if cid is None:
            return None
        if cid not in clubs:
            clubs[cid] = _club_public(_club_row(c, cid))
        return clubs[cid]

    stages = []
    for st in br["stages"]:
        ties = []
        for x in st["ties"]:
            ties.append({
                "id": x["id"], "pos": x["bracket_pos"],
                "club_a": club(x["club_a_id"]), "club_b": club(x["club_b_id"]),
                "wins_a": x["wins_a"] or 0, "wins_b": x["wins_b"] or 0,
                "winner_club_id": x["winner_club_id"],
                "bye": x["club_b_id"] is None,
                "games": [_game_public(x, g) for g in x["games"]],
            })
        stages.append({"code": st["code"], "name": st["name"], "ties": ties})
    out = {
        "id": t["id"], "name": t["name"], "format": t["format"],
        "format_name": FORMAT_NAMES.get(t["format"], t["format"]),
        "stage": t["stage"], "finished": t["stage"] == "finished",
        "winner": club(br["winner_club_id"]),
        "stages": stages,
    }
    c.close()
    return out


async def api_cups(request):
    require_active_user(request)
    q = request.rel_url.query
    c = appdb.db()
    if q.get("tournament_id"):
        try:
            ids = [int(q["tournament_id"])]
        except ValueError:
            c.close()
            return web.json_response({"status": "error", "error": "tournament_id"}, status=400)
    else:
        # сначала идущие кубки, потом завершённые; свежие выше
        ids = [r["id"] for r in c.execute(
            "SELECT id FROM tournaments WHERE format!='league' "
            "ORDER BY CASE WHEN stage='finished' THEN 1 ELSE 0 END, id DESC LIMIT 20").fetchall()]
    c.close()
    cups = [p for p in (cup_payload(i) for i in ids) if p]
    return web.json_response({"status": "ok", "cups": cups})


def setup(app: web.Application) -> None:
    app.router.add_get("/api/cups", api_cups)
