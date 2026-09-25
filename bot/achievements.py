"""Достижения и зал славы (кабинет, план 01 §6 + решение 09).

Прогресс считается ЛЕНИВО из состояния БД (ставки, серия входов, клуб, матчи, зал славы) —
в движок ставок не встраиваемся. Открытие фиксируется при оценке, награду юзер забирает кнопкой.
"""
import re
from collections import defaultdict

import db as appdb
from clubs_catalog import FORMAT_NAMES

RARITY_ORDER = {"legendary": 0, "epic": 1, "rare": 2, "common": 3}
RARITY_NAMES = {"common": "Обычное", "rare": "Редкое", "epic": "Эпическое", "legendary": "Легендарное"}

# code, icon, title, description, category, rarity, metric, target, reward_xp, reward_coins
# структура и награды — по 29 достижениям оригинала, механики — наши (решение 09)
CATALOG: list[tuple] = [
    ("first_bet", "🐣", "Первая затяжка", "Сделать свой первый прогноз", "volume", "common", "bets", 1, 75, 150),
    ("bets_10", "📊", "Любитель", "Сделать 10 любых прогнозов", "volume", "common", "bets", 10, 100, 250),
    ("bets_50", "🏅", "Регуляр", "Сделать 50 любых прогнозов", "volume", "rare", "bets", 50, 300, 750),
    ("bets_100", "💯", "Центурион", "Сделать 100 любых прогнозов", "volume", "epic", "bets", 100, 600, 1500),
    ("first_win", "⚔️", "Первая кровь", "Выиграть свой первый прогноз", "volume", "common", "wins", 1, 125, 250),
    ("wins_10", "🎯", "10 побед", "Выиграть 10 любых прогнозов", "volume", "common", "wins", 10, 150, 300),
    ("wins_50", "🏆", "50 побед", "Выиграть 50 любых прогнозов", "volume", "rare", "wins", 50, 400, 1000),
    ("wins_100", "👑", "100 побед", "Выиграть 100 любых прогнозов", "volume", "legendary", "wins", 100, 1200, 3000),
    ("streak_3", "🔥", "В ударе", "Выиграть 3 купона подряд", "streaks", "common", "win_streak", 3, 150, 300),
    ("streak_5", "🎯", "Снайпер", "Выиграть 5 купонов подряд", "streaks", "rare", "win_streak", 5, 300, 700),
    ("streak_10", "🛡", "Непобедимый", "Выиграть 10 купонов подряд", "streaks", "legendary", "win_streak", 10, 1200, 3000),
    ("express_3", "🚂", "Экспресс-старт", "Собрать экспресс из 3+ событий", "parlays", "common", "express_legs", 3, 100, 200),
    ("express_5", "🧨", "Полная обойма", "Собрать экспресс из 5 событий", "parlays", "rare", "express_legs", 5, 250, 500),
    ("express_x5", "💥", "Множитель x5", "Выиграть экспресс с кэфом 5.0+", "parlays", "rare", "express_odds", 5, 250, 600),
    ("express_x15", "🚀", "Ракета x15", "Выиграть экспресс с кэфом 15.0+", "parlays", "epic", "express_odds", 15, 600, 1500),
    ("express_x50", "🌌", "Космос x50", "Выиграть экспресс с кэфом 50.0+", "parlays", "legendary", "express_odds", 50, 1500, 4000),
    ("login_3", "📅", "Разминка", "Забирать бонус 3 дня подряд", "loyalty", "common", "login_streak", 3, 100, 250),
    ("login_7", "🗓", "Неделя в строю", "Забирать бонус 7 дней подряд", "loyalty", "rare", "login_streak", 7, 250, 600),
    ("login_30", "🚬", "Заядлый курильщик", "Забирать бонус 30 дней подряд", "loyalty", "legendary", "login_streak", 30, 1200, 3000),
    ("balance_5k", "💨", "Дымовая завеса", "Накопить 5 000 дыма на балансе", "wealth", "rare", "balance", 5000, 200, 500),
    ("balance_25k", "💰", "Мешок дыма", "Накопить 25 000 дыма на балансе", "wealth", "epic", "balance", 25000, 500, 1000),
    ("balance_100k", "🏦", "Табачный магнат", "Накопить 100 000 дыма на балансе", "wealth", "legendary", "balance", 100000, 1000, 2500),
    ("roi_plus", "📈", "В плюсе", "Быть в плюсе после 10+ рассчитанных купонов", "skill", "rare", "roi_plus", 1, 300, 700),
    ("promo_first", "🎟", "Кодовое слово", "Активировать промокод", "promo", "common", "promo", 1, 50, 100),
    ("club_owner", "🛡️", "Свой клуб", "Получить клуб в лиге", "club", "common", "has_club", 1, 100, 200),
    ("transfer_first", "🤝", "Первая сделка", "Провести трансфер своего клуба", "club", "common", "transfers", 1, 150, 300),
    ("goals_10", "⚽", "Бомбардир", "Забить 10 голов в матчах турниров", "club", "rare", "goals", 10, 300, 700),
    ("match_wins_10", "💪", "Хозяин поля", "Выиграть 10 матчей турниров", "club", "rare", "match_wins", 10, 300, 700),
    ("cup_winner", "🏆", "Обладатель кубка", "Выиграть ЛЧ, ЛЕ или ЛК своим клубом", "seasonal", "legendary", "cups", 1, 1500, 4000),
    ("season_champion", "🥇", "Чемпион сезона", "Занять 1-е место в дивизионе по итогам сезона", "seasonal", "legendary", "champion", 1, 2000, 5000),
]
METRIC_BY_CODE = {row[0]: row[6] for row in CATALOG}


class AchievementError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def seed_catalog(c) -> None:
    """Идемпотентно: правки каталога в коде применяются к существующим строкам."""
    for i, (code, icon, title, desc, cat, rarity, _m, target, xp, coins) in enumerate(CATALOG):
        c.execute(
            "INSERT INTO achievements (code, title, description, icon, category, rarity, target, "
            "reward_xp, reward_coins, sort_order, is_active) VALUES (?,?,?,?,?,?,?,?,?,?,1) "
            "ON CONFLICT(code) DO UPDATE SET title=excluded.title, description=excluded.description, "
            "icon=excluded.icon, category=excluded.category, rarity=excluded.rarity, target=excluded.target, "
            "reward_xp=excluded.reward_xp, reward_coins=excluded.reward_coins, sort_order=excluded.sort_order",
            (code, title, desc, icon, cat, rarity, target, xp, coins, i),
        )
    # коды, выпавшие из каталога, прячем (открытия юзеров не трогаем)
    codes = [row[0] for row in CATALOG]
    marks = ",".join("?" * len(codes))
    c.execute(f"UPDATE achievements SET is_active=0 WHERE code NOT IN ({marks})", codes)


# ===== зал славы: снимок чемпионов =====

def _owner_of_club(c, club_id: int) -> tuple[int | None, int | None]:
    row = c.execute("SELECT player_id FROM club_players WHERE club_id=? AND role='owner' ORDER BY id LIMIT 1",
                    (club_id,)).fetchone()
    pid = row["player_id"] if row else None
    if pid is None:
        cl = c.execute("SELECT owner_player_id FROM clubs WHERE id=?", (club_id,)).fetchone()
        pid = cl["owner_player_id"] if cl else None
    if pid is None:
        return None, None
    p = c.execute("SELECT telegram_id FROM players WHERE id=?", (pid,)).fetchone()
    return pid, (p["telegram_id"] if p else None)


def _record(c, tid: int, div_id: int, kind: str, title: str, club_id: int, points: int | None) -> None:
    club = c.execute("SELECT name FROM clubs WHERE id=?", (club_id,)).fetchone()
    pid, tg = _owner_of_club(c, club_id)
    c.execute(
        "INSERT OR IGNORE INTO hall_of_fame (tournament_id, division_id, kind, title, club_id, club_name, "
        "owner_player_id, owner_telegram_id, points) VALUES (?,?,?,?,?,?,?,?,?)",
        (tid, div_id, kind, title, club_id, club["name"] if club else f"#{club_id}", pid, tg, points),
    )


def _league_champions(c, t: dict) -> list[tuple[int, str, int, int]]:
    """Чемпионы дивизионов завершённой лиги → [(division_id, title, club_id, points)].

    После финала сезона клубы уже переехали 3↑/3↓, поэтому состав дивизиона восстанавливаем
    по сыгранному календарю (связные группы клубов), а таблицу — как league.division_standings.
    """
    games = c.execute(
        "SELECT home_club_id h, away_club_id a, score1, score2, status FROM matches "
        "WHERE tournament_id=? AND stage='group' AND status!='cancelled'", (t["id"],)).fetchall()
    parent: dict[int, int] = {}

    def find(x):
        parent.setdefault(x, x)
        while parent[x] != x:
            parent[x] = parent[parent[x]]
            x = parent[x]
        return x

    for g in games:
        if g["h"] and g["a"]:
            parent[find(g["h"])] = find(g["a"])
    groups: dict[int, list[int]] = defaultdict(list)
    for cid in list(parent):
        groups[find(cid)].append(cid)

    stats = {cid: {"points": 0, "gf": 0, "ga": 0, "games": 0} for cid in parent}
    for g in games:
        if g["status"] != "confirmed" or g["score1"] is None or g["score2"] is None:
            continue
        h, a, s1, s2 = g["h"], g["a"], g["score1"], g["score2"]
        for cid, gf, ga in ((h, s1, s2), (a, s2, s1)):
            st = stats[cid]
            st["games"] += 1
            st["gf"] += gf
            st["ga"] += ga
            st["points"] += 3 if gf > ga else 1 if gf == ga else 0

    divs = [dict(r) for r in c.execute(
        "SELECT id, name FROM divisions WHERE tournament_id=? ORDER BY sort_order, id", (t["id"],)).fetchall()]
    names = {d["id"]: d["name"] for d in divs}
    key = lambda cid: (-stats[cid]["points"], -(stats[cid]["gf"] - stats[cid]["ga"]), -stats[cid]["gf"], cid)  # noqa: E731
    ordered = sorted(groups.values(), key=min)
    tables = [sorted(members, key=key) for members in ordered]
    current = {}
    for cid in parent:
        row = c.execute("SELECT division_id FROM clubs WHERE id=?", (cid,)).fetchone()
        current[cid] = row["division_id"] if row else None

    def fit(table, i):
        # сколько клубов стоят там, где их оставил бы финал сезона (топ-3 ↑, последние 3 ↓)
        n, hits = len(table), 0
        for pos, cid in enumerate(table, 1):
            j = i - 1 if pos <= 3 and i > 0 else i + 1 if pos > n - 3 and i < len(divs) - 1 else i
            hits += current.get(cid) == divs[j]["id"]
        return hits

    assign: dict[int, int] = {}
    if len(divs) == 1 and len(tables) == 1:
        assign[0] = divs[0]["id"]
    elif divs and len(tables) <= len(divs) <= 6:
        from itertools import permutations
        best_perm, best_score = None, 0
        for perm in permutations(range(len(divs)), len(tables)):
            score = sum(fit(tables[g], i) for g, i in enumerate(perm))
            if score > best_score:
                best_perm, best_score = perm, score
        if best_perm:
            assign = {g: divs[i]["id"] for g, i in enumerate(best_perm)}

    out = []
    for g, table in enumerate(tables):
        played = [cid for cid in table if stats[cid]["games"]]
        if not played:
            continue
        best = played[0]
        did = assign.get(g, -(g + 1))  # дивизион не опознан — синтетический ключ
        out.append((did, f"{t['name']} · {names.get(did, 'Дивизион')}", best, stats[best]["points"]))
    return out


def sync_hall_of_fame(c) -> None:
    """Дописывает в зал славы новых чемпионов (лиги stage='finished') и обладателей кубков."""
    known = {(r["tournament_id"], r["division_id"]) for r in
             c.execute("SELECT tournament_id, division_id FROM hall_of_fame").fetchall()}
    known_t = {tid for tid, _ in known}
    for t in c.execute("SELECT * FROM tournaments WHERE format='league' AND stage='finished'").fetchall():
        t = dict(t)
        if t["id"] in known_t:
            continue
        for did, title, club_id, pts in _league_champions(c, t):
            _record(c, t["id"], did, "league", title, club_id, pts)
    for row in c.execute(
            "SELECT t.id, t.name, t.format, ti.winner_club_id FROM tournaments t "
            "JOIN ties ti ON ti.tournament_id=t.id AND ti.stage='final' "
            "WHERE t.format!='league' AND ti.winner_club_id IS NOT NULL").fetchall():
        if (row["id"], 0) in known:
            continue
        title = f"{FORMAT_NAMES.get(row['format'], row['format'])} · {row['name']}"
        _record(c, row["id"], 0, "cup", title, row["winner_club_id"], None)


# ===== метрики юзера =====

_STREAK_RE = re.compile(r"бонус за серию \((\d+)")


def _metrics(c, user: dict) -> dict:
    uid = user["id"]
    bets = [dict(r) for r in c.execute(
        "SELECT id, bet_type, amount, total_odds, potential_win, status FROM bets WHERE user_id=? ORDER BY id",
        (uid,)).fetchall()]
    legs = {r["bet_id"]: r["n"] for r in c.execute(
        "SELECT bet_id, COUNT(*) n FROM bet_legs WHERE bet_id IN (SELECT id FROM bets WHERE user_id=?) "
        "GROUP BY bet_id", (uid,)).fetchall()}
    live = [b for b in bets if b["status"] != "void"]
    settled = [b for b in bets if b["status"] in ("won", "lost")]
    streak = best = 0
    for b in settled:
        streak = streak + 1 if b["status"] == "won" else 0
        best = max(best, streak)
    express = [b for b in live if b["bet_type"] == "express"]
    profit = sum(b["potential_win"] or 0 for b in settled if b["status"] == "won") - sum(b["amount"] or 0 for b in settled)

    # серия входов: текущая + максимум из истории бонусов (серия могла сброситься до оценки)
    login = 0
    row = c.execute("SELECT streak_days FROM user_streaks WHERE user_id=?", (uid,)).fetchone()
    if row:
        login = row["streak_days"] or 0
    for r in c.execute("SELECT reason FROM balance_history WHERE user_id=? AND reason LIKE ?",
                       (uid, "бонус за серию%")).fetchall():
        m = _STREAK_RE.search(r["reason"] or "")
        if m:
            login = max(login, int(m.group(1)))

    player = c.execute("SELECT * FROM players WHERE telegram_id=?", (user["telegram_id"],)).fetchone()
    club_id = None
    if player:
        cp = c.execute("SELECT club_id FROM club_players WHERE player_id=?", (player["id"],)).fetchone()
        club_id = cp["club_id"] if cp else None
    transfers = 0
    if club_id:
        transfers = c.execute(
            "SELECT COUNT(*) n FROM transfers WHERE status='approved' AND (from_club_id=? OR to_club_id=?)",
            (club_id, club_id)).fetchone()["n"]
    pid = player["id"] if player else -1

    def hall(kind):
        return c.execute(
            "SELECT COUNT(*) n FROM hall_of_fame WHERE kind=? AND (owner_telegram_id=? OR owner_player_id=?)",
            (kind, user["telegram_id"], pid)).fetchone()["n"]

    return {
        "bets": len(live),
        "wins": sum(1 for b in bets if b["status"] == "won"),
        "win_streak": best,
        "express_legs": max((legs.get(b["id"], 0) for b in express), default=0),
        "express_odds": max((b["total_odds"] or 0 for b in express if b["status"] == "won"), default=0),
        "login_streak": login,
        "balance": user["balance"] or 0,
        "roi_plus": 1 if len(settled) >= 10 and profit > 0 else 0,
        "promo": c.execute("SELECT COUNT(*) n FROM promo_activations WHERE user_id=?", (uid,)).fetchone()["n"],
        "has_club": 1 if club_id else 0,
        "transfers": transfers,
        "goals": (player["goals_scored"] or 0) if player else 0,
        "match_wins": (player["wins"] or 0) if player else 0,
        "cups": hall("cup"),
        "champion": hall("league"),
    }


def evaluate(user_id: int) -> list[dict]:
    """Пересчитывает прогресс, фиксирует новые открытия → список достижений со статусами."""
    c = appdb.db()
    try:
        user = c.execute("SELECT * FROM users WHERE id=?", (user_id,)).fetchone()
        if not user:
            raise AchievementError("NO_USER", "Юзер не найден")
        sync_hall_of_fame(c)
        metrics = _metrics(c, dict(user))
        mine = {r["achievement_id"]: dict(r) for r in c.execute(
            "SELECT * FROM user_achievements WHERE user_id=?", (user_id,)).fetchall()}
        rows = [dict(r) for r in c.execute(
            "SELECT * FROM achievements WHERE is_active=1 ORDER BY sort_order, id").fetchall()]
        out = []
        for a in rows:
            value = metrics.get(METRIC_BY_CODE.get(a["code"], ""), 0)
            target = a["target"] or 1
            ua = mine.get(a["id"])
            if ua is None and value >= target:
                c.execute("INSERT OR IGNORE INTO user_achievements (user_id, achievement_id) VALUES (?,?)",
                          (user_id, a["id"]))
                ua = dict(c.execute("SELECT * FROM user_achievements WHERE user_id=? AND achievement_id=?",
                                    (user_id, a["id"])).fetchone())
            out.append({
                "id": a["code"], "code": a["code"],
                "name": a["title"], "description": a["description"], "badge_icon": a["icon"],
                "category": a["category"], "rarity": a["rarity"],
                "rarity_name": RARITY_NAMES.get(a["rarity"], a["rarity"]),
                "reward_xp": a["reward_xp"], "reward_coins": a["reward_coins"],
                "progress": round(min(value, target), 2), "target": target,
                "is_unlocked": 1 if ua else 0,
                "is_claimed": 1 if ua and ua.get("claimed_at") else 0,
                "unlocked_at": ua["unlocked_at"] if ua else None,
            })
        c.commit()
        return out
    finally:
        c.close()


def claim(user_id: int, code: str) -> dict:
    """Забрать награду. Идемпотентно: вторая попытка не платит (claimed_at ставится атомарно)."""
    items = {a["code"]: a for a in evaluate(user_id)}
    a = items.get(code)
    if not a:
        raise AchievementError("NOT_FOUND", "Достижение не найдено")
    if not a["is_unlocked"]:
        raise AchievementError("LOCKED", "Достижение ещё не открыто")
    from bets_engine import _apply_xp  # та же формула уровней, что и за выигрыши
    c = appdb.db()
    try:
        ach_id = c.execute("SELECT id FROM achievements WHERE code=?", (code,)).fetchone()["id"]
        cur = c.execute("UPDATE user_achievements SET claimed_at=datetime('now') "
                        "WHERE user_id=? AND achievement_id=? AND claimed_at IS NULL", (user_id, ach_id))
        paid = cur.rowcount == 1
        if paid:
            coins, xp = a["reward_coins"] or 0, a["reward_xp"] or 0
            if coins:
                c.execute("UPDATE users SET balance=balance+? WHERE id=?", (coins, user_id))
                c.execute("INSERT INTO balance_history (user_id, delta, reason) VALUES (?,?,?)",
                          (user_id, coins, f"достижение «{a['name']}»"))
            if xp:
                _apply_xp(c, user_id, xp)
            c.execute("INSERT INTO notifications (user_id, text, kind) VALUES (?,?, 'app')",
                      (user_id, f"{a['badge_icon']} Достижение «{a['name']}»: +{coins} дыма, +{xp} XP"))
        c.commit()
        u = c.execute("SELECT balance, xp, level FROM users WHERE id=?", (user_id,)).fetchone()
    finally:
        c.close()
    return {"code": code, "already_claimed": not paid,
            "reward_coins": a["reward_coins"] if paid else 0, "reward_xp": a["reward_xp"] if paid else 0,
            "balance": u["balance"], "xp": u["xp"], "level": u["level"]}


# ===== зал славы =====

HALL_TABS = {"balance": "balance", "won": "total_won"}


def hall(tab: str = "balance", viewer_id: int | None = None, limit: int = 20) -> dict:
    c = appdb.db()
    try:
        if tab == "champions":
            sync_hall_of_fame(c)
            c.commit()
            rows = c.execute(
                "SELECT h.*, cl.logo_path, u.username, u.first_name, p.username p_username "
                "FROM hall_of_fame h LEFT JOIN clubs cl ON cl.id=h.club_id "
                "LEFT JOIN users u ON u.telegram_id=h.owner_telegram_id "
                "LEFT JOIN players p ON p.id=h.owner_player_id "
                "ORDER BY h.tournament_id DESC, h.kind DESC, h.division_id").fetchall()
            return {"tab": tab, "champions": [{
                "kind": r["kind"], "title": r["title"], "tournament_id": r["tournament_id"],
                "club": r["club_name"], "club_id": r["club_id"], "points": r["points"],
                "logo": f"/assets/logos/{r['logo_path'].rsplit('/', 1)[-1]}" if r["logo_path"] else None,
                "owner": r["username"] or r["p_username"] or r["first_name"],
                "recorded_at": r["recorded_at"],
            } for r in rows]}
        col = HALL_TABS.get(tab)
        if not col:
            raise AchievementError("BAD_TAB", "Неизвестная вкладка")
        rows = c.execute(
            f"SELECT u.id, u.telegram_id, u.username, u.first_name, u.balance, u.total_won, u.bets_count, "
            f"u.bets_won, u.level, cl.name team_name FROM users u "
            f"LEFT JOIN players p ON p.telegram_id=u.telegram_id "
            f"LEFT JOIN club_players cp ON cp.player_id=p.id "
            f"LEFT JOIN clubs cl ON cl.id=cp.club_id "
            f"WHERE u.is_frozen=0 ORDER BY u.{col} DESC, u.id LIMIT ?", (limit,)).fetchall()
        leaders = [{
            "position": i, "user_id": r["telegram_id"],
            "name": r["username"] or r["first_name"] or f"Игрок {r['telegram_id']}",
            "value": r[col], "balance": r["balance"], "total_won": r["total_won"],
            "bets_count": r["bets_count"], "bets_won": r["bets_won"], "level": r["level"],
            "team_name": r["team_name"], "is_me": r["id"] == viewer_id,
        } for i, r in enumerate(rows, 1)]
        me = None
        if viewer_id and not any(x["is_me"] for x in leaders):
            mine = c.execute(f"SELECT {col} v FROM users WHERE id=?", (viewer_id,)).fetchone()
            if mine:
                ahead = c.execute(f"SELECT COUNT(*) n FROM users WHERE is_frozen=0 AND ({col}>? "
                                  f"OR ({col}=? AND id<?))", (mine["v"], mine["v"], viewer_id)).fetchone()["n"]
                me = {"position": ahead + 1, "value": mine["v"]}
        return {"tab": tab, "leaders": leaders, "me": me}
    finally:
        c.close()
