"""Турнирное ядро: календарь round-robin, кубковые сетки, таблицы.

Чистая логика без telegram — вызывается и ботом, и мини-аппом (блок 6).
"""
import random
import sqlite3
from datetime import datetime, timedelta

import db as appdb


# ===== вспомогательные =====

def get_tournament(tournament_id: int) -> dict | None:
    c = appdb.db()
    row = c.execute("SELECT * FROM tournaments WHERE id=?", (tournament_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def get_club(club_id: int) -> dict | None:
    c = appdb.db()
    row = c.execute("SELECT * FROM clubs WHERE id=?", (club_id,)).fetchone()
    c.close()
    return dict(row) if row else None


def tournament_divisions(tournament_id: int) -> list[dict]:
    c = appdb.db()
    rows = c.execute(
        "SELECT * FROM divisions WHERE tournament_id=? AND is_active=1 ORDER BY sort_order",
        (tournament_id,),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def division_clubs(division_id: int) -> list[dict]:
    c = appdb.db()
    rows = c.execute(
        "SELECT * FROM clubs WHERE division_id=? ORDER BY id", (division_id,)
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def all_active_tournaments() -> list[dict]:
    c = appdb.db()
    rows = c.execute("SELECT * FROM tournaments WHERE stage!='finished' ORDER BY id").fetchall()
    c.close()
    return [dict(r) for r in rows]


# ===== сезоны / дивизионы =====

def create_league_season(name: str, n_divisions: int = 1, tour_mode: str = "manual",
                         rounds: int = 2, tour_days: int = 3) -> int:
    """Лига = контейнер, внутри 1..N дивизионов (решение 09)."""
    c = appdb.db()
    tid = c.insert_returning_id(
        "INSERT INTO tournaments (name, format, tour_mode, rounds, tour_days, total_tours, current_tour, stage) "
        "VALUES (?, 'league', ?, ?, ?, 0, 0, 'groups')",
        (name, tour_mode, int(rounds), int(tour_days)),
    )
    for i in range(1, n_divisions + 1):
        c.execute(
            "INSERT INTO divisions (tournament_id, name, code, sort_order) VALUES (?,?,?,?)",
            (tid, f"Дивизион {i}", f"D{i}", i),
        )
    c.commit()
    c.close()
    return tid


def create_cup(name: str, fmt: str, tour_mode: str = "manual", tour_days: int = 3) -> int:
    """Кубок ЛЧ/ЛЕ/ЛК — отдельный турнир, сетка генерится жеребьёвкой."""
    c = appdb.db()
    tid = c.insert_returning_id(
        "INSERT INTO tournaments (name, format, tour_mode, rounds, tour_days, total_tours, current_tour, stage) "
        "VALUES (?, ?, ?, 1, ?, 0, 0, 'playoff')",
        (name, fmt, tour_mode, int(tour_days)),
    )
    c.commit()
    c.close()
    return tid


def create_club(name: str, logo_path: str | None, division_id: int | None, budget: int) -> int:
    c = appdb.db()
    cid = c.insert_returning_id(
        "INSERT INTO clubs (name, logo_path, division_id, budget, elo) VALUES (?,?,?,?,1000)",
        (name, logo_path, division_id, budget),
    )
    c.commit()
    c.close()
    return cid


def assign_club_owner(club_id: int, player_id: int, role: str = "owner") -> None:
    """Один клуб на человека (UNIQUE player_id в club_players)."""
    c = appdb.db()
    c.execute(
        "INSERT INTO club_players (player_id, club_id, role) VALUES (?,?,?) "
        "ON CONFLICT(player_id) DO UPDATE SET club_id=excluded.club_id, role=excluded.role",
        (player_id, club_id, role),
    )
    c.execute("UPDATE clubs SET owner_player_id=? WHERE id=?", (player_id, club_id))
    c.commit()
    c.close()


# ===== календарь лиги =====

def round_robin(team_ids: list[int]) -> list[list[tuple[int, int]]]:
    """Круговой турнир методом «карусели»; None-пары (нечётное) отброшены."""
    ids = list(team_ids)
    if len(ids) < 2:
        return []
    if len(ids) % 2:
        ids.append(None)  # фиктивный «пасс»
    n = len(ids)
    rounds: list[list[tuple[int, int]]] = []
    for r in range(n - 1):
        pairs = []
        for i in range(n // 2):
            a, b = ids[i], ids[n - 1 - i]
            if a is None or b is None:
                continue
            pairs.append((a, b) if (r + i) % 2 == 0 else (b, a))
        rounds.append(pairs)
        ids = [ids[0], ids[-1], *ids[1:-1]]
    return rounds


def generate_league_calendar(tournament_id: int, division_id: int) -> int:
    """Генерит все туры дивизиона: (клубов−1) туров × круги. Тур 1 открыт,
    остальные — locked. Возвращает число матчей."""
    t = get_tournament(tournament_id)
    clubs = division_clubs(division_id)
    if not t or len(clubs) < 2:
        return 0
    ids = [cl["id"] for cl in clubs]
    owners = {cl["id"]: cl["owner_player_id"] for cl in clubs}
    rounds_needed = int(t["rounds"] or 1)
    tour_days = int(t["tour_days"] or 3)

    c = appdb.db()
    # чистим старый календарь дивизиона (перегенерация до открытия тура)
    c.execute(
        "DELETE FROM matches WHERE tournament_id=? AND stage='group' AND ("
        " home_club_id IN (SELECT id FROM clubs WHERE division_id=?) OR"
        " away_club_id IN (SELECT id FROM clubs WHERE division_id=?))",
        (tournament_id, division_id, division_id),
    )
    c.execute("DELETE FROM tours WHERE tournament_id=?", (tournament_id,))

    schedule = round_robin(ids)
    total = 0
    now = datetime.utcnow()
    for tour_no in range(1, len(schedule) * rounds_needed + 1):
        leg = (tour_no - 1) // len(schedule)
        pairs = schedule[(tour_no - 1) % len(schedule)]
        if leg % 2 == 1:
            pairs = [(b, a) for a, b in pairs]  # ответка: гости дома
        c.execute(
            "INSERT INTO tours (tournament_id, tour_number, status, deadline) VALUES (?,?,?,?)",
            (tournament_id, tour_no,
             "open" if tour_no == 1 else "locked",
             (now + timedelta(days=tour_days)).isoformat() if tour_no == 1 else None),
        )
        for home, away in pairs:
            c.execute(
                "INSERT INTO matches (tournament_id, home_club_id, away_club_id, home_player_id,"
                " away_player_id, stage, tour_number, status) VALUES (?,?,?,?,?,'group',?,'pending')",
                (tournament_id, home, away, owners.get(home), owners.get(away), tour_no),
            )
            total += 1
    c.execute(
        "UPDATE tournaments SET total_tours=?, current_tour=1 WHERE id=?",
        (len(schedule) * rounds_needed, tournament_id),
    )
    c.commit()
    c.close()
    return total


def ensure_tour(tournament_id: int, tour_number: int) -> dict:
    c = appdb.db()
    row = c.execute(
        "SELECT * FROM tours WHERE tournament_id=? AND tour_number=?",
        (tournament_id, tour_number),
    ).fetchone()
    c.close()
    return dict(row) if row else {}


def open_tour(tournament_id: int, tour_number: int, tour_days: int | None = None) -> bool:
    """Открыть тур (дедлайн = tour_days от сейчас), предыдущие open → locked.
    Кэфы тура пересчитываются по актуальному Elo."""
    t = get_tournament(tournament_id)
    if not t:
        return False
    days = tour_days or int(t["tour_days"] or 3)
    now = datetime.utcnow()
    c = appdb.db()
    c.execute(
        "UPDATE tours SET status='locked' WHERE tournament_id=? AND status='open'",
        (tournament_id,),
    )
    c.execute(
        "INSERT INTO tours (tournament_id, tour_number, status, deadline) VALUES (?,?,?,?) "
        "ON CONFLICT(tournament_id, tour_number) DO UPDATE SET status='open', deadline=excluded.deadline",
        (tournament_id, tour_number, "open", (now + timedelta(days=days)).isoformat()),
    )
    c.execute(
        "UPDATE tournaments SET current_tour=MAX(current_tour, ?) WHERE id=?",
        (tour_number, tournament_id),
    )
    c.commit()
    c.close()
    try:
        import markets as markets_engine
        markets_engine.refresh_tour(tournament_id, tour_number)
    except Exception:
        pass
    return True


def rotate_tours() -> list[tuple[int, int, int]]:
    """Джоба tour_rotate: у открытого тура вышел дедлайн → lock; открываем следующий
    (неигранные матчи остаются доигрываться — решение 09).
    Возвращает (турнир, открыт тур, закрыт тур)."""
    now = datetime.utcnow().isoformat()
    rotated: list[tuple[int, int, int]] = []
    for t in all_active_tournaments():
        if t["format"] != "league" or t["tour_mode"] != "manual":
            continue
        c = appdb.db()
        open_tour_row = c.execute(
            "SELECT * FROM tours WHERE tournament_id=? AND status='open' ORDER BY tour_number LIMIT 1",
            (t["id"],),
        ).fetchone()
        c.close()
        if not open_tour_row or not open_tour_row["deadline"]:
            continue
        if open_tour_row["deadline"] > now:
            continue
        nxt = open_tour_row["tour_number"] + 1
        if nxt <= (t["total_tours"] or 0):
            open_tour(t["id"], nxt)
            rotated.append((t["id"], nxt, open_tour_row["tour_number"]))
    return rotated


# ===== кубковые сетки =====

def _cup_stage_for(n: int) -> str:
    return {2: "final", 4: "sf", 8: "qf", 16: "r16", 32: "r32"}.get(n, f"r{n}")


def create_cup_bracket(tournament_id: int, club_ids: list[int], stage: str | None = None) -> list[dict]:
    """Жеребьёвка: случайные пары, серии до 2 побед (план 07). Создаёт ties +
    первые игры. Возвращает созданные ties."""
    ids = list(club_ids)
    random.shuffle(ids)
    n = 1
    while n * 2 <= len(ids):
        n *= 2
    if n < 2:
        return []
    # некратное число участников: лишние срезаются (не проходят квалификацию)
    seeded = ids[:n]
    if stage is None:
        stage = _cup_stage_for(n)
    c = appdb.db()
    c.execute("UPDATE tournaments SET stage='playoff' WHERE id=?", (tournament_id,))
    ties = []
    first_games = []
    for i in range(0, n, 2):
        a, b = seeded[i], seeded[i + 1]
        tie_id = c.insert_returning_id(
            "INSERT INTO ties (tournament_id, stage, club_a_id, club_b_id) VALUES (?,?,?,?)",
            (tournament_id, stage, a, b),
        )
        first_games.append((_insert_tie_game(c, tournament_id, tie_id, a, b, stage, 1), tie_id))
        ties.append({"tie_id": tie_id, "club_a": a, "club_b": b, "stage": stage})
    c.commit()
    c.close()
    # рынки серии — только после commit: generate_tie_markets читает ties своим соединением
    import markets as markets_engine
    for mid, tie_id in first_games:
        try:
            markets_engine.generate_tie_markets(mid, tie_id)
        except Exception:
            pass
    return ties


def _insert_tie_game(c, tournament_id: int, tie_id: int, club_a: int, club_b: int,
                     stage: str, game_no: int) -> int:
    """Игра серии: дом/гости чередуются (нечётная — клуб А дома)."""
    home, away = (club_a, club_b) if game_no % 2 == 1 else (club_b, club_a)
    return c.insert_returning_id(
        "INSERT INTO matches (tournament_id, home_club_id, away_club_id, stage, tie_id,"
        " game_in_tie, status) VALUES (?,?,?,?,?,?, 'pending')",
        (tournament_id, home, away, stage, tie_id, game_no),
    )


def _game_winner_side(tie, g) -> str | None:
    """'a'/'b' — кто выиграл игру серии; None — ничья без пенальти (серия продолжается)."""
    home_is_a = g["home_club_id"] == tie["club_a_id"]
    sa, sb = (g["score1"], g["score2"]) if home_is_a else (g["score2"], g["score1"])
    if sa == sb:
        # ничья в серии до 2 побед → пенальти решают игру (план 07/FC-практика)
        if g["pens1"] is None or g["pens2"] is None:
            return None
        sa, sb = (g["pens1"], g["pens2"]) if home_is_a else (g["pens2"], g["pens1"])
        if sa == sb:
            return None
    return "a" if sa > sb else "b"


def advance_tie(c, match_row: dict, score_home: int, score_away: int,
                pens_home: int | None, pens_away: int | None) -> list[int]:
    """После финализации игры серии (в т.ч. повторной — спор/правка): пересчитать
    wins с нуля по сыгранным играм, создать следующую игру или зафиксировать
    победителя. Счёт матча уже записан в c. → id отменённых лишних игр."""
    tie_id = match_row["tie_id"]
    if tie_id is None:
        return []
    return recompute_tie(c, tie_id)


def recompute_tie(c, tie_id: int) -> list[int]:
    tie = c.execute("SELECT * FROM ties WHERE id=?", (tie_id,)).fetchone()
    if not tie:
        return []
    a, b = tie["club_a_id"], tie["club_b_id"]
    games = c.execute(
        "SELECT * FROM matches WHERE tie_id=? ORDER BY game_in_tie, id", (tie_id,)).fetchall()
    wins = {"a": 0, "b": 0}
    last_played = 0
    for g in games:
        if g["status"] not in ("confirmed", "disputed") or g["score1"] is None:
            continue
        if max(wins.values()) >= 2:
            break  # серия уже решена — поздние игры не считаем
        last_played = g["game_in_tie"] or 1
        side = _game_winner_side(tie, g)
        if side:
            wins[side] += 1
    winner = a if wins["a"] >= 2 else (b if wins["b"] >= 2 else None)
    c.execute("UPDATE ties SET wins_a=?, wins_b=?, winner_club_id=? WHERE id=?",
              (wins["a"], wins["b"], winner, tie_id))
    pending = [g for g in games if g["status"] == "pending"]
    cancelled: list[int] = []
    if winner:
        # серия решена (например, после правки счёта) — лишние игры отменяем
        for g in pending:
            c.execute("UPDATE matches SET status='cancelled' WHERE id=?", (g["id"],))
            cancelled.append(g["id"])
    elif not pending:
        _insert_tie_game(c, tie["tournament_id"], tie_id, a, b, tie["stage"], last_played + 1)
    return cancelled


def cup_stage_winners(tournament_id: int, stage: str) -> list[int]:
    c = appdb.db()
    rows = c.execute(
        "SELECT winner_club_id FROM ties WHERE tournament_id=? AND stage=? AND winner_club_id IS NOT NULL",
        (tournament_id, stage),
    ).fetchall()
    c.close()
    return [r["winner_club_id"] for r in rows]


def next_cup_stage(stage: str) -> str | None:
    return {"r32": "r16", "r16": "qf", "qf": "sf", "sf": "final"}.get(stage)


def draw_next_cup_stage(tournament_id: int) -> int:
    """Когда все ties стадии решены — жеребьёвка следующей стадии. → ties создано."""
    t = get_tournament(tournament_id)
    if not t or t["stage"] != "playoff":
        return 0
    c = appdb.db()
    row = c.execute(
        "SELECT stage, COUNT(*) AS total, SUM(winner_club_id IS NOT NULL) AS done "
        "FROM ties WHERE tournament_id=? GROUP BY stage ORDER BY CASE stage "
        "WHEN 'r32' THEN 1 WHEN 'r16' THEN 2 WHEN 'qf' THEN 3 WHEN 'sf' THEN 4 ELSE 5 END LIMIT 1",
        (tournament_id,),
    ).fetchone()
    c.close()
    if not row or row["done"] < row["total"] or row["stage"] == "final":
        return 0
    winners = cup_stage_winners(tournament_id, row["stage"])
    nxt = next_cup_stage(row["stage"])
    if not nxt or len(winners) < 2:
        return 0
    ties = create_cup_bracket(tournament_id, winners, stage=nxt)
    return len(ties)


# ===== таблицы =====

def division_standings(division_id: int) -> list[dict]:
    """Таблица дивизиона по подтверждённым матчам: очки/И/В/Н/П/мячи."""
    clubs = division_clubs(division_id)
    if not clubs:
        return []
    ids = [cl["id"] for cl in clubs]
    stats = {cid: {"club_id": cid, "games": 0, "wins": 0, "draws": 0, "losses": 0,
                   "gf": 0, "ga": 0, "points": 0} for cid in ids}
    c = appdb.db()
    div = c.execute("SELECT tournament_id FROM divisions WHERE id=?", (division_id,)).fetchone()
    tournament_id = div["tournament_id"] if div else None
    placeholders = ",".join("?" * len(ids))
    # только матчи сезона этого дивизиона — прошлые сезоны не подмешиваем
    rows = c.execute(
        f"SELECT home_club_id, away_club_id, score1, score2 FROM matches "
        f"WHERE status='confirmed' AND stage='group' AND tournament_id=? "
        f"AND (home_club_id IN ({placeholders}) OR away_club_id IN ({placeholders}))",
        (tournament_id, *ids, *ids),
    ).fetchall()
    c.close()
    for m in rows:
        if m["score1"] is None:
            continue
        h, a = m["home_club_id"], m["away_club_id"]
        if h not in stats or a not in stats:
            continue
        stats[h]["games"] += 1
        stats[a]["games"] += 1
        stats[h]["gf"] += m["score1"]
        stats[h]["ga"] += m["score2"]
        stats[a]["gf"] += m["score2"]
        stats[a]["ga"] += m["score1"]
        if m["score1"] > m["score2"]:
            stats[h]["wins"] += 1; stats[h]["points"] += 3; stats[a]["losses"] += 1
        elif m["score1"] < m["score2"]:
            stats[a]["wins"] += 1; stats[a]["points"] += 3; stats[h]["losses"] += 1
        else:
            stats[h]["draws"] += 1; stats[a]["draws"] += 1
            stats[h]["points"] += 1; stats[a]["points"] += 1
    table = [dict(v) for v in stats.values()]
    table.sort(key=lambda r: (-r["points"], -(r["gf"] - r["ga"]), -r["gf"], r["club_id"]))
    for i, row in enumerate(table, 1):
        row["position"] = i
    return table


def tour_matches(tournament_id: int, tour_number: int) -> list[dict]:
    c = appdb.db()
    rows = c.execute(
        "SELECT * FROM matches WHERE tournament_id=? AND tour_number=? ORDER BY id",
        (tournament_id, tour_number),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def format_matches_for_tour(tournament_id: int, tour_number: int) -> str:
    lines = []
    names = {}
    c = appdb.db()
    for m in tour_matches(tournament_id, tour_number):
        for cid in (m["home_club_id"], m["away_club_id"]):
            if cid not in names:
                r = c.execute("SELECT name FROM clubs WHERE id=?", (cid,)).fetchone()
                names[cid] = r["name"] if r else f"#{cid}"
        score = f" {m['score1']}:{m['score2']}" if m["score1"] is not None else ""
        if m["status"] == "disputed":
            score += " (спор)"
        lines.append(f"#{m['id']} {names[m['home_club_id']]} — {names[m['away_club_id']]}{score}")
    c.close()
    return "\n".join(lines) if lines else "Матчей нет."


# ===== финал сезона (решение 09: применяет АДМИН кнопкой, не фоновый job) =====

def season_final_preview(tournament_id: int) -> dict:
    """Что произойдёт при финализации: чемпионы, призовые, повышения/вылеты."""
    import settings as appsettings
    t = get_tournament(tournament_id)
    if not t or t["format"] != "league":
        return {"error": "турнир не найден или не лига"}
    divs = tournament_divisions(tournament_id)
    c = appdb.db()
    prizes = {
        "champion": appsettings.setting_int("prize_champion", 100_000_000),
        "second": appsettings.setting_int("prize_second", 30_000_000),
        "third": appsettings.setting_int("prize_third", 15_000_000),
    }
    c.close()
    rows = []
    for i, d in enumerate(divs):
        table = division_standings(d["id"])
        for r in table:
            row = {"division": d["name"], "position": r["position"],
                   "club": get_club(r["club_id"])["name"], "points": r["points"]}
            if i == 0 and r["position"] == 1:
                row["prize"] = prizes["champion"]
            elif i == 0 and r["position"] == 2:
                row["prize"] = prizes["second"]
            elif (i == 0 and r["position"] == 3) or (i > 0 and r["position"] == 1):
                row["prize"] = prizes["third"]
            if r["position"] <= 3 and i > 0:
                row["move"] = f"↑ в {divs[i-1]['name']}"
            if r["position"] > len(table) - 3 and i < len(divs) - 1:
                row["move"] = f"↓ в {divs[i+1]['name']}"
            rows.append(row)
    return {"tournament": t["name"], "prizes": prizes, "rows": rows}


def finalize_season(tournament_id: int, actor_tg: int | None = None) -> dict:
    """Итоги сезона: призовые в бюджеты клубов, 3↑/3↓, турнир → finished.
    Elo нового сезона стартует с 1000 автоматически (новый турнир = новые строки)."""
    import settings as appsettings
    preview = season_final_preview(tournament_id)
    if preview.get("error"):
        return preview
    if get_tournament(tournament_id)["stage"] == "finished":
        return {"error": "сезон уже финализирован — призовые повторно не выплачиваются"}
    divs = tournament_divisions(tournament_id)
    c = appdb.db()
    # защита от гонки двух подтверждений: захватываем турнир атомарно
    cur = c.execute("UPDATE tournaments SET stage='finished' WHERE id=? AND stage!='finished'",
                    (tournament_id,))
    if cur.rowcount == 0:
        c.close()
        return {"error": "сезон уже финализирован — призовые повторно не выплачиваются"}
    paid, moves = [], []
    for i, d in enumerate(divs):
        table = division_standings(d["id"])
        for r in table:
            club = get_club(r["club_id"])
            prize = 0
            if i == 0 and r["position"] == 1:
                prize = appsettings.setting_int("prize_champion", 100_000_000)
            elif i == 0 and r["position"] == 2:
                prize = appsettings.setting_int("prize_second", 30_000_000)
            elif (i == 0 and r["position"] == 3) or (i > 0 and r["position"] == 1):
                prize = appsettings.setting_int("prize_third", 15_000_000)
            if prize:
                c.execute("UPDATE clubs SET budget=budget+? WHERE id=?", (prize, r["club_id"]))
                c.execute("INSERT INTO balance_history (user_id, delta, reason) VALUES (NULL, ?, ?)",
                          (prize, f"призовое за сезон: {club['name']} ({d['name']}, {r['position']} место)"))
                paid.append({"club": club["name"], "prize": prize, "note": f"{d['name']} {r['position']} место"})
            # 3 вверх / 3 вниз (только между существующими дивизионами)
            if r["position"] <= 3 and i > 0:
                c.execute("UPDATE clubs SET division_id=? WHERE id=?", (divs[i-1]["id"], r["club_id"]))
                moves.append({"club": club["name"], "move": f"↑ {d['name']} → {divs[i-1]['name']}"})
            elif r["position"] > len(table) - 3 and i < len(divs) - 1:
                c.execute("UPDATE clubs SET division_id=? WHERE id=?", (divs[i+1]["id"], r["club_id"]))
                moves.append({"club": club["name"], "move": f"↓ {d['name']} → {divs[i+1]['name']}"})
    # призовые за кубки сезона (победители final): только ещё не закрытые кубки,
    # после выплаты кубок → finished, чтобы следующий сезон не заплатил повторно
    cup_prize = appsettings.setting_int("prize_cup_winner", 50_000_000)
    season_id = get_tournament(tournament_id).get("season_id")
    cup_sql = "SELECT id, name, format FROM tournaments WHERE format!='league' AND stage!='finished'"
    cup_args: tuple = ()
    if season_id is not None:
        cup_sql += " AND (season_id IS NULL OR season_id=?)"
        cup_args = (season_id,)
    cups = [dict(r) for r in c.execute(cup_sql, cup_args).fetchall()]
    cup_winners = []
    for cup in cups:
        row = c.execute(
            "SELECT winner_club_id FROM ties WHERE tournament_id=? AND stage='final' AND winner_club_id IS NOT NULL",
            (cup["id"],)).fetchone()
        if row:
            club = get_club(row["winner_club_id"])
            c.execute("UPDATE clubs SET budget=budget+? WHERE id=?", (cup_prize, row["winner_club_id"]))
            c.execute("UPDATE tournaments SET stage='finished' WHERE id=?", (cup["id"],))
            cup_winners.append({"club": club["name"], "cup": cup["name"], "prize": cup_prize})
    c.execute(
        "INSERT INTO tournament_audit_log (tournament_id, actor_telegram_id, action, details) "
        "VALUES (?, ?, 'season_finalized', ?)",
        (tournament_id, actor_tg, f"призовых={len(paid)} обменов={len(moves)} кубков={len(cup_winners)}"),
    )
    c.commit()
    c.close()
    return {"ok": True, "tournament": preview["tournament"],
            "prize_rows": paid, "moves": moves, "cup_winners": cup_winners}
