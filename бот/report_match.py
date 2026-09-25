"""Сопоставление скрина FC27 с матчем турнира (без Telegram — тестируется напрямую).

На скрине нет наших клубов и дивизионов: слева/справа — ники игроков FC27
(«temiyy», «quete-основа») и их игровые клубы («Real Betis»). Поэтому:
  ник со скрина → players.game_nickname → клуб (club_players) → pending-матч этих
  клубов → турнир / дивизион / тур. Сторона «слева на скрине» ≠ «хозяева» в
  календаре (в FC27 хозяин тот, кто позвал), поэтому ориентацию тоже берём по никам.
"""
import re
from difflib import SequenceMatcher

import core
import db as appdb

# кириллица, похожая на латиницу: OCR и игроки путают раскладку
_CONFUSABLES = str.maketrans("аевкмнорстухё", "aebkmhopctyxe")

NICK_OK = 0.8      # ник считаем узнанным
NICK_STRONG = 0.92  # одного такого ника хватает, если второй не читается


def normalize_nick(nick: str | None) -> str:
    s = (nick or "").casefold().translate(_CONFUSABLES)
    return re.sub(r"[^0-9a-zа-я]", "", s)


def nick_similarity(a: str | None, b: str | None) -> float:
    na, nb = normalize_nick(a), normalize_nick(b)
    if not na or not nb:
        return 0.0
    if na == nb:
        return 1.0
    return SequenceMatcher(None, na, nb).ratio()


def _club_nicks(c, club_id: int | None) -> list[str]:
    if not club_id:
        return []
    return [r["game_nickname"] for r in c.execute(
        "SELECT p.game_nickname FROM club_players cp JOIN players p ON p.id=cp.player_id "
        "WHERE cp.club_id=? AND p.game_nickname IS NOT NULL AND p.game_nickname!=''",
        (club_id,)).fetchall()]


def _best(nick: str | None, nicks: list[str]) -> float:
    return max((nick_similarity(nick, n) for n in nicks), default=0.0)


def reportable_matches(c, club_id: int | None = None) -> list[dict]:
    """Несыгранные матчи, которые можно репортить: лига — тур открыт или уже закрыт
    (неигранный «висит доигрываться», решение 09); кубок — любой pending."""
    sql = (
        "SELECT m.* FROM matches m JOIN tournaments tr ON tr.id=m.tournament_id "
        "LEFT JOIN tours t ON t.tournament_id=m.tournament_id AND t.tour_number=m.tour_number "
        "WHERE m.status IN ('pending','reported') AND m.home_club_id IS NOT NULL "
        "AND m.away_club_id IS NOT NULL "
        "AND (tr.format!='league' OR t.status IN ('open','locked')) "
        "AND COALESCE(tr.stage,'') NOT IN ('finished','archived')"
    )
    args: list = []
    if club_id:
        sql += " AND (m.home_club_id=? OR m.away_club_id=?)"
        args += [club_id, club_id]
    sql += " ORDER BY COALESCE(m.tour_number, 0), m.id"
    return [dict(r) for r in c.execute(sql, args).fetchall()]


def resolve(parsed: dict, reporter_tg: int, preferred_match_id: int | None = None) -> dict:
    """→ {"status": "auto"|"ask_match"|"ask_side"|"error", "match_id", "swapped",
    "candidates": [match_id...], "message"}.

    auto — матч и сторона определены по никам, можно финализировать сразу.
    ask_side — матч известен, но по никам не понять, кто слева на скрине.
    ask_match — у репортёра несколько матчей, ники не помогли выбрать.
    """
    left, right = parsed.get("player_home"), parsed.get("player_away")
    player = core.get_player(reporter_tg)
    club = core.club_of_player(player["id"]) if player else None
    c = appdb.db()
    try:
        all_open = reportable_matches(c)
        is_judge = any(core.is_tournament_admin(m["tournament_id"], reporter_tg)
                       for m in {m["tournament_id"]: m for m in all_open}.values())
        mine = [m for m in all_open if club and club["id"] in (m["home_club_id"], m["away_club_id"])]
        pool = all_open if is_judge else mine
        if preferred_match_id:
            pool = [m for m in all_open if m["id"] == preferred_match_id
                    and (is_judge or m in mine)]
        if not pool:
            return {"status": "error", "match_id": None, "swapped": False, "candidates": [],
                    "message": "У тебя нет несыгранных матчей. Клуб выдаёт root; календарь — /календарь."}

        # ники ищем по всем открытым матчам: так ловим и «скрин чужого матча»
        search = pool if preferred_match_id else all_open
        scored = []
        for m in search:
            hn, an = _club_nicks(c, m["home_club_id"]), _club_nicks(c, m["away_club_id"])
            straight = (_best(left, hn), _best(right, an))
            swapped = (_best(left, an), _best(right, hn))
            for sw, (s1, s2) in ((False, straight), (True, swapped)):
                scored.append((s1 + s2, min(s1, s2), max(s1, s2), sw, m))
        scored.sort(key=lambda x: (-x[0], x[4]["tour_number"] or 0, x[4]["id"]))
        total, low, high, sw, m = scored[0]
        recognized = (low >= NICK_OK) or (high >= NICK_STRONG and low < 0.5)
        # другой матч (не та же пара) с почти тем же баллом → по никам не выбрать
        ambiguous = any(s[0] >= total - 0.05 and not _same_pair(s[4], m) for s in scored[1:])

        if recognized and not ambiguous:
            if not is_judge and m not in mine:
                return {"status": "error", "match_id": m["id"], "swapped": sw, "candidates": [],
                        "message": "Ники со скрина — не твой матч. Репорт шлёт участник или судья."}
            return {"status": "auto", "match_id": m["id"], "swapped": sw, "candidates": [m["id"]],
                    "message": ""}

        candidates = [x["id"] for x in pool]
        if len(pool) == 1:
            return {"status": "ask_side", "match_id": pool[0]["id"], "swapped": False,
                    "candidates": candidates, "message": _why(left, right)}
        return {"status": "ask_match", "match_id": None, "swapped": False,
                "candidates": candidates[:8], "message": _why(left, right)}
    finally:
        c.close()


def _same_pair(a: dict, b: dict) -> bool:
    return {a["home_club_id"], a["away_club_id"]} == {b["home_club_id"], b["away_club_id"]}


def _why(left, right) -> str:
    if not left and not right:
        return "Ники на скрине не прочитались."
    return f"Ники со скрина «{left or '?'}» / «{right or '?'}» не совпали с никами игроков турнира."


def side_for_match(parsed: dict, match_id: int) -> bool | None:
    """Выбран матч вручную: сторона по никам, если получится (True = слева гости)."""
    c = appdb.db()
    m = c.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m:
        c.close()
        return None
    hn, an = _club_nicks(c, m["home_club_id"]), _club_nicks(c, m["away_club_id"])
    c.close()
    left, right = parsed.get("player_home"), parsed.get("player_away")
    straight = _best(left, hn) + _best(right, an)
    swapped = _best(left, an) + _best(right, hn)
    if max(straight, swapped) < NICK_OK or abs(straight - swapped) < 0.2:
        return None
    return swapped > straight


def orient(parsed: dict, swapped: bool) -> dict:
    """Левая сторона скрина → хозяева матча в календаре (разворот счёта/пенальти/голов)."""
    out = dict(parsed)
    if not swapped:
        return out
    flip = {"home": "away", "away": "home"}
    out["score_home"], out["score_away"] = parsed["score_away"], parsed["score_home"]
    out["player_home"], out["player_away"] = parsed.get("player_away"), parsed.get("player_home")
    out["team_home"], out["team_away"] = parsed.get("team_away"), parsed.get("team_home")
    pens = parsed.get("penalties")
    if pens:
        out["penalties"] = {"home": pens["away"], "away": pens["home"]}
    out["goal_events"] = [{**g, "side": flip.get(g.get("side"), g.get("side"))}
                          for g in parsed.get("goal_events") or []]
    out["players"] = [{**p, "side": flip.get(p.get("side"), p.get("side"))}
                      for p in parsed.get("players") or []]
    return out


def match_context(c, m: dict) -> str:
    """«🏆 Сезон 1 · Дивизион A · Тур 2» — где этот матч в турнире."""
    parts = []
    tr = c.execute("SELECT name, format FROM tournaments WHERE id=?", (m["tournament_id"],)).fetchone()
    if tr:
        parts.append(tr["name"])
    div = c.execute(
        "SELECT d.name FROM clubs cl JOIN divisions d ON d.id=cl.division_id "
        "WHERE cl.id=? AND d.tournament_id=?", (m["home_club_id"], m["tournament_id"])).fetchone()
    if div:
        parts.append(div["name"])
    if m.get("tour_number") is not None:
        parts.append(f"Тур {m['tour_number']}")
    elif m.get("stage"):
        stage = {"r16": "1/8 финала", "qf": "1/4 финала", "sf": "1/2 финала", "final": "Финал"}
        parts.append(stage.get(m["stage"], str(m["stage"])))
    return "🏆 " + " · ".join(parts) if parts else ""
