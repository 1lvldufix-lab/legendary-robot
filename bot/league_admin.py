"""Управление лигами, дивизионами, клубами и участниками (мини-апп админка + команды бота).

Лига = турнир format='league' с 1..N дивизионами. Дивизионы именованные («Ла Лига», «Серия А»),
порядок sort_order = иерархия для повышения/вылета (promote_count; 0 — независимые лиги).
"""
import re

import config
import db as appdb
import league
from clubs_catalog import TEAM_LOGO_MAP, display_name, find_club, logo_path_for, normalize_club_name

MAX_DIVISIONS = 12


class LeagueError(ValueError):
    pass


def parse_division_names(text: str) -> list[str]:
    """«Ла Лига, Лига 1; Лига 2 | Серия А» или построчно → список имён без пустых и дублей."""
    names, seen = [], set()
    for part in re.split(r"[,;|\n]+", text or ""):
        name = " ".join(part.split())[:40]
        if name and name.casefold() not in seen:
            seen.add(name.casefold())
            names.append(name)
    return names


# ===== лиги и дивизионы =====

def create_league(name: str, division_names: list[str], rounds: int = 2, tour_days: int | None = None,
                  promote_count: int = 3) -> int:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise LeagueError("Укажи название сезона")
    if not division_names:
        division_names = ["Дивизион 1"]
    if len(division_names) > MAX_DIVISIONS:
        raise LeagueError(f"Максимум {MAX_DIVISIONS} дивизионов")
    rounds = 1 if int(rounds or 2) == 1 else 2
    tid = league.create_league_season(name, division_names, "manual", rounds,
                                      int(tour_days or config.DEFAULT_TOUR_DAYS))
    set_promote_count(tid, promote_count)
    return tid


def set_promote_count(tid: int, n: int) -> None:
    n = max(0, min(int(n or 0), 10))
    c = appdb.db()
    c.execute("UPDATE tournaments SET promote_count=? WHERE id=?", (n, tid))
    c.commit()
    c.close()


def rename_league(tid: int, name: str) -> None:
    name = " ".join((name or "").split())[:60]
    if not name:
        raise LeagueError("Пустое название")
    c = appdb.db()
    c.execute("UPDATE tournaments SET name=? WHERE id=?", (name, tid))
    c.commit()
    c.close()


def _league_or_error(tid: int) -> dict:
    t = league.get_tournament(tid)
    if not t or t["format"] != "league":
        raise LeagueError("Лига не найдена")
    if t["stage"] == "finished":
        raise LeagueError("Сезон завершён — правки закрыты")
    return t


def add_division(tid: int, name: str) -> int:
    _league_or_error(tid)
    name = " ".join((name or "").split())[:40]
    if not name:
        raise LeagueError("Пустое название дивизиона")
    c = appdb.db()
    try:
        n = c.execute("SELECT COUNT(*) n, COALESCE(MAX(sort_order),0) m FROM divisions "
                      "WHERE tournament_id=? AND is_active=1", (tid,)).fetchone()
        if n["n"] >= MAX_DIVISIONS:
            raise LeagueError(f"Максимум {MAX_DIVISIONS} дивизионов")
        # SQLite LOWER() не знает кириллицу — сравниваем в Python
        existing = {r["name"].casefold() for r in c.execute(
            "SELECT name FROM divisions WHERE tournament_id=? AND is_active=1", (tid,)).fetchall()}
        if name.casefold() in existing:
            raise LeagueError("Такой дивизион уже есть")
        did = c.insert_returning_id(
            "INSERT INTO divisions (tournament_id, name, code, sort_order) VALUES (?,?,?,?)",
            (tid, name, f"D{n['m'] + 1}", n["m"] + 1))
        c.commit()
        return did
    finally:
        c.close()


def _division(c, did: int) -> dict:
    row = c.execute("SELECT * FROM divisions WHERE id=? AND is_active=1", (did,)).fetchone()
    if not row:
        raise LeagueError("Дивизион не найден")
    return dict(row)


def rename_division(did: int, name: str) -> None:
    name = " ".join((name or "").split())[:40]
    if not name:
        raise LeagueError("Пустое название")
    c = appdb.db()
    try:
        d = _division(c, did)
        _league_or_error(d["tournament_id"])
        c.execute("UPDATE divisions SET name=? WHERE id=?", (name, did))
        c.commit()
    finally:
        c.close()


def move_division(did: int, direction: str) -> None:
    """up/down в иерархии: меняемся sort_order с соседом."""
    c = appdb.db()
    try:
        d = _division(c, did)
        _league_or_error(d["tournament_id"])
        divs = [dict(r) for r in c.execute(
            "SELECT id, sort_order FROM divisions WHERE tournament_id=? AND is_active=1 ORDER BY sort_order, id",
            (d["tournament_id"],)).fetchall()]
        i = next(k for k, x in enumerate(divs) if x["id"] == did)
        j = i - 1 if direction == "up" else i + 1
        if not 0 <= j < len(divs):
            return
        divs[i], divs[j] = divs[j], divs[i]
        for k, x in enumerate(divs, start=1):  # перенумеровываем — старые базы могли иметь одинаковые sort_order
            c.execute("UPDATE divisions SET sort_order=? WHERE id=?", (k, x["id"]))
        c.commit()
    finally:
        c.close()


def delete_division(did: int) -> None:
    c = appdb.db()
    try:
        d = _division(c, did)
        _league_or_error(d["tournament_id"])
        has_clubs = c.execute("SELECT 1 FROM clubs WHERE division_id=?", (did,)).fetchone()
        if has_clubs:
            raise LeagueError("Сначала убери клубы из дивизиона")
        c.execute("UPDATE divisions SET is_active=0 WHERE id=?", (did,))
        c.commit()
    finally:
        c.close()


def _division_started(c, did: int, tid: int) -> bool:
    return bool(c.execute(
        "SELECT 1 FROM matches WHERE tournament_id=? AND stage='group' AND status!='pending' AND "
        "(home_club_id IN (SELECT id FROM clubs WHERE division_id=?) OR "
        " away_club_id IN (SELECT id FROM clubs WHERE division_id=?)) LIMIT 1",
        (tid, did, did)).fetchone())


def generate_calendar(tid: int, division_id: int | None = None) -> dict:
    """Календарь по дивизионам. Дивизион, где уже сыграны матчи, не трогаем."""
    t = _league_or_error(tid)
    c = appdb.db()
    divs = [dict(r) for r in c.execute(
        "SELECT * FROM divisions WHERE tournament_id=? AND is_active=1 ORDER BY sort_order", (tid,)).fetchall()]
    if division_id:
        divs = [d for d in divs if d["id"] == int(division_id)]
    started = [d["name"] for d in divs if _division_started(c, d["id"], tid)]
    c.close()
    if started:
        raise LeagueError(f"Уже сыграны матчи: {', '.join(started)} — календарь не перегенерирую")
    made, skipped = {}, []
    for d in divs:
        n = league.generate_league_calendar(tid, d["id"])
        if n:
            made[d["name"]] = n
        else:
            skipped.append(d["name"])
    if not made:
        raise LeagueError("Нужно минимум 2 клуба в дивизионе")
    return {"tournament": t["name"], "matches": made, "skipped": skipped}


# ===== клубы =====

def catalog() -> list[dict]:
    return [{"name": display_name(k), "logo": f"/assets/logos/{v}"} for k, v in sorted(TEAM_LOGO_MAP.items())]


def add_club(division_id: int, club_name: str, owner: str | None = None, allow_custom: bool = False) -> dict:
    """Клуб в дивизион: из каталога FC (с лого) или свой (allow_custom). Уже существующий клуб
    переносится (если в старом дивизионе не сыграно матчей)."""
    c = appdb.db()
    try:
        d = _division(c, division_id)
    finally:
        c.close()
    _league_or_error(d["tournament_id"])
    found = find_club(club_name or "")
    if found:
        name, logo = found[0], logo_path_for(found[1])
    elif allow_custom and (club_name or "").strip():
        name, logo = " ".join(club_name.split())[:40], None
    else:
        raise LeagueError("Клуба нет в каталоге FC — включи «свой клуб» или выбери из списка")
    c = appdb.db()
    try:
        row = next((dict(r) for r in c.execute("SELECT * FROM clubs").fetchall()
                    if " ".join(r["name"].casefold().split()) == " ".join(name.casefold().split())), None)
        if row:
            if row["division_id"] and row["division_id"] != division_id:
                old = c.execute("SELECT * FROM divisions WHERE id=?", (row["division_id"],)).fetchone()
                if old and old["is_active"] and _division_started(c, row["division_id"], old["tournament_id"]):
                    raise LeagueError(f"{name} уже играет в «{old['name']}» — перенос после конца сезона")
            c.execute("UPDATE clubs SET division_id=? WHERE id=?", (division_id, row["id"]))
            club_id = row["id"]
        else:
            club_id = None
        c.commit()
    finally:
        c.close()
    if club_id is None:
        club_id = league.create_club(name, logo, division_id, config.CLUB_START_BUDGET)
    if owner:
        assign_owner(club_id, owner)
    return club_info(club_id)


def remove_club_from_division(club_id: int) -> None:
    c = appdb.db()
    try:
        cl = c.execute("SELECT * FROM clubs WHERE id=?", (club_id,)).fetchone()
        if not cl:
            raise LeagueError("Клуб не найден")
        if cl["division_id"]:
            d = c.execute("SELECT * FROM divisions WHERE id=?", (cl["division_id"],)).fetchone()
            if d and _division_started(c, cl["division_id"], d["tournament_id"]):
                raise LeagueError("В дивизионе уже сыграны матчи — клуб не убрать до конца сезона")
        c.execute("UPDATE clubs SET division_id=NULL WHERE id=?", (club_id,))
        c.commit()
    finally:
        c.close()


def club_info(club_id: int) -> dict:
    c = appdb.db()
    row = c.execute(
        "SELECT cl.id, cl.name, cl.logo_path, cl.division_id, cl.budget, cl.elo, p.telegram_id AS owner_tg, "
        "p.username AS owner_username, p.game_nickname AS owner_nick FROM clubs cl "
        "LEFT JOIN club_players cp ON cp.club_id=cl.id AND cp.role='owner' "
        "LEFT JOIN players p ON p.id=cp.player_id WHERE cl.id=?", (club_id,)).fetchone()
    c.close()
    if not row:
        raise LeagueError("Клуб не найден")
    d = dict(row)
    logo = d.pop("logo_path") or ""
    d["logo"] = f"/assets/logos/{logo.rsplit('/', 1)[-1]}" if logo else None
    return d


def assign_owner(club_id: int, who: str | int) -> dict:
    """Владелец клуба: @username или Telegram ID. Нет в боте — заводим по ID (предрегистрация)."""
    player = resolve_or_create_player(who)
    if not league.get_club(club_id):
        raise LeagueError("Клуб не найден")
    c = appdb.db()
    # прежний владелец этого клуба становится свободным (один клуб — один владелец)
    c.execute("DELETE FROM club_players WHERE club_id=? AND player_id!=?", (club_id, player["id"]))
    c.commit()
    c.close()
    league.assign_club_owner(club_id, player["id"])
    c = appdb.db()
    # несыгранные матчи клуба теперь на новом владельце
    c.execute("UPDATE matches SET home_player_id=? WHERE home_club_id=? AND status='pending'", (player["id"], club_id))
    c.execute("UPDATE matches SET away_player_id=? WHERE away_club_id=? AND status='pending'", (player["id"], club_id))
    c.commit()
    c.close()
    return club_info(club_id)


def unassign_owner(club_id: int) -> None:
    c = appdb.db()
    c.execute("DELETE FROM club_players WHERE club_id=?", (club_id,))
    c.execute("UPDATE clubs SET owner_player_id=NULL WHERE id=?", (club_id,))
    c.commit()
    c.close()


# ===== участники =====

def resolve_player(who: str | int) -> dict | None:
    q = str(who or "").strip().lstrip("@")
    if not q:
        return None
    c = appdb.db()
    if q.isdigit():
        row = c.execute("SELECT * FROM players WHERE telegram_id=?", (int(q),)).fetchone()
    else:
        row = c.execute("SELECT * FROM players WHERE LOWER(username)=LOWER(?)", (q,)).fetchone()
    c.close()
    return dict(row) if row else None


def resolve_or_create_player(who: str | int) -> dict:
    p = resolve_player(who)
    if p:
        return p
    q = str(who or "").strip().lstrip("@")
    if not q.isdigit():
        raise LeagueError(f"@{q} ещё не писал боту. Пусть нажмёт /start или укажи Telegram ID (его даст /myid)")
    c = appdb.db()
    # username из users (если открывал мини-апп), иначе пусто — /start его допишет
    u = c.execute("SELECT username FROM users WHERE telegram_id=?", (int(q),)).fetchone()
    c.execute("INSERT OR IGNORE INTO players (telegram_id, username) VALUES (?,?)",
              (int(q), u["username"] if u else None))
    c.commit()
    c.close()
    return resolve_player(q)


def nick_taken_by(nick: str, except_tg: int | None = None) -> dict | None:
    """Ник, совпадающий с чужим после нормализации (как в report_match), ломает поиск матча по скрину."""
    from report_match import normalize_nick
    key = normalize_nick(nick)
    if not key:
        return None
    c = appdb.db()
    rows = [dict(r) for r in c.execute(
        "SELECT telegram_id, username, game_nickname FROM players WHERE game_nickname IS NOT NULL").fetchall()]
    c.close()
    return next((r for r in rows if r["telegram_id"] != except_tg and normalize_nick(r["game_nickname"]) == key), None)


def set_nick(who: str | int, nick: str | None) -> dict:
    player = resolve_or_create_player(who)
    nick = " ".join((nick or "").split())[:32] or None
    if nick:
        other = nick_taken_by(nick, player["telegram_id"])
        if other:
            owner = f"@{other['username']}" if other["username"] else f"ID {other['telegram_id']}"
            raise LeagueError(f"Ник «{nick}» уже у {owner} — скрины перепутаются")
    c = appdb.db()
    c.execute("UPDATE players SET game_nickname=? WHERE id=?", (nick, player["id"]))
    c.commit()
    c.close()
    return resolve_player(player["telegram_id"])


def people(query: str = "") -> list[dict]:
    """Участники турнира (players) + открывавшие мини-апп (users): ник, клуб, судейство."""
    c = appdb.db()
    rows = [dict(r) for r in c.execute(
        "SELECT p.id AS player_id, p.telegram_id, p.username, p.game_nickname AS nick, "
        "cl.id AS club_id, cl.name AS club, d.name AS division, u.first_name "
        "FROM players p LEFT JOIN club_players cp ON cp.player_id=p.id "
        "LEFT JOIN clubs cl ON cl.id=cp.club_id LEFT JOIN divisions d ON d.id=cl.division_id "
        "LEFT JOIN users u ON u.telegram_id=p.telegram_id ORDER BY p.id").fetchall()]
    known = {r["telegram_id"] for r in rows}
    for u in c.execute("SELECT telegram_id, username, first_name FROM users ORDER BY id").fetchall():
        if u["telegram_id"] not in known:
            rows.append({"player_id": None, "telegram_id": u["telegram_id"], "username": u["username"],
                         "nick": None, "club_id": None, "club": None, "division": None,
                         "first_name": u["first_name"]})
    judges: dict[int, list[str]] = {}
    for r in c.execute("SELECT a.telegram_id, t.name FROM tournament_admins a "
                       "JOIN tournaments t ON t.id=a.tournament_id WHERE t.stage!='finished'").fetchall():
        judges.setdefault(r["telegram_id"], []).append(r["name"])
    c.close()
    q = (query or "").strip().lstrip("@").casefold()
    out = []
    for r in rows:
        r["judge_of"] = judges.get(r["telegram_id"], [])
        r["is_root"] = r["telegram_id"] in config.ADMIN_IDS
        hay = " ".join(str(r.get(k) or "") for k in ("username", "nick", "club", "first_name", "telegram_id")).casefold()
        if not q or q in hay:
            out.append(r)
    return out


def set_player_club(who: str | int, club_id: int | None) -> dict:
    player = resolve_or_create_player(who)
    if club_id:
        assign_owner(int(club_id), player["telegram_id"])
    else:
        c = appdb.db()
        cp = c.execute("SELECT club_id FROM club_players WHERE player_id=?", (player["id"],)).fetchone()
        c.close()
        if cp:
            unassign_owner(cp["club_id"])
    return next(p for p in people() if p["telegram_id"] == player["telegram_id"])


def toggle_judge(tid: int, who: str | int) -> bool:
    player = resolve_or_create_player(who)
    if not league.get_tournament(tid):
        raise LeagueError("Турнир не найден")
    c = appdb.db()
    row = c.execute("SELECT 1 FROM tournament_admins WHERE tournament_id=? AND telegram_id=?",
                    (tid, player["telegram_id"])).fetchone()
    if row:
        c.execute("DELETE FROM tournament_admins WHERE tournament_id=? AND telegram_id=?", (tid, player["telegram_id"]))
    else:
        c.execute("INSERT INTO tournament_admins (tournament_id, telegram_id) VALUES (?,?)", (tid, player["telegram_id"]))
    c.commit()
    c.close()
    return not row


# ===== обзор для админки =====

def overview() -> list[dict]:
    c = appdb.db()
    leagues = [dict(r) for r in c.execute(
        "SELECT * FROM tournaments WHERE format='league' AND stage!='finished' ORDER BY id DESC").fetchall()]
    out = []
    for t in leagues:
        divs = [dict(r) for r in c.execute(
            "SELECT * FROM divisions WHERE tournament_id=? AND is_active=1 ORDER BY sort_order, id",
            (t["id"],)).fetchall()]
        for d in divs:
            ids = [r["id"] for r in c.execute("SELECT id FROM clubs WHERE division_id=? ORDER BY name",
                                               (d["id"],)).fetchall()]
            d["clubs"] = [club_info(i) for i in ids]
            d["started"] = _division_started(c, d["id"], t["id"])
            d["matches"] = c.execute(
                "SELECT COUNT(*) n FROM matches WHERE tournament_id=? AND stage='group' AND "
                "home_club_id IN (SELECT id FROM clubs WHERE division_id=?)", (t["id"], d["id"])).fetchone()["n"]
        open_tour = c.execute("SELECT tour_number FROM tours WHERE tournament_id=? AND status='open' "
                              "ORDER BY tour_number LIMIT 1", (t["id"],)).fetchone()
        out.append({
            "id": t["id"], "name": t["name"], "rounds": t["rounds"], "tour_days": t["tour_days"],
            "promote_count": t.get("promote_count") if t.get("promote_count") is not None else 3,
            "total_tours": t["total_tours"], "open_tour": open_tour["tour_number"] if open_tour else None,
            "divisions": divs,
        })
    c.close()
    return out


def is_catalog_name(name: str) -> bool:
    key = normalize_club_name(name or "")
    return any(normalize_club_name(k) == key for k in TEAM_LOGO_MAP)
