"""Fair-play учёт тренировок (решение 09): игрок тренирует футболистов в САМОЙ FC Mobile,
апп только считает — лимит за неделю, счётчик по карточке/клубу, уровни, нарушения для админа.
Апп тренировки НЕ продаёт и НЕ проводит.
"""
from datetime import date, datetime, timedelta, timezone

import db as appdb
import settings as appsettings

KINDS = {"ovr": "Прокачка OVR", "rank": "Повышение ранга", "skill": "Навыки / бусты", "other": "Другое"}
MAX_PER_ENTRY = 50
# уровень карточки по сумме тренировок: (порог, уровень)
LEVELS = [(1, 1), (5, 2), (15, 3), (30, 4), (50, 5)]
VIOLATION_ACTIONS = {"resolved": "решено", "penalized": "наказан"}


class TrainingError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code
        self.message = message


def period_start(today: date | None = None) -> str:
    """Понедельник текущей недели (UTC) — граница периода лимита."""
    d = today or datetime.now(timezone.utc).date()
    return (d - timedelta(days=d.weekday())).isoformat()


def weekly_limit() -> int:
    return max(0, appsettings.setting_int("training_limit_per_week", 10))


def level_of(total: int) -> dict:
    lvl, nxt = 0, LEVELS[0][0]
    for i, (need, n) in enumerate(LEVELS):
        if total >= need:
            lvl = n
            nxt = LEVELS[i + 1][0] if i + 1 < len(LEVELS) else None
    return {"level": lvl, "next_at": nxt}


def _club_of(c, telegram_id: int) -> tuple[int | None, int | None]:
    row = c.execute(
        "SELECT cp.club_id, p.id pid FROM players p JOIN club_players cp ON cp.player_id=p.id "
        "WHERE p.telegram_id=?", (telegram_id,)).fetchone()
    return (row["club_id"], row["pid"]) if row else (None, None)


def _week_total(c, club_id: int, period: str) -> int:
    return c.execute("SELECT COALESCE(SUM(count), 0) n FROM training_log WHERE club_id=? AND period_start=?",
                     (club_id, period)).fetchone()["n"]


def add_entry(user: dict, card_id, kind: str, count, note: str = "", today: date | None = None) -> dict:
    """Записать тренировку. Сверх лимита запись принимается, но открывает нарушение для админа."""
    try:
        count = int(count)
        card_id = int(card_id)
    except (TypeError, ValueError):
        raise TrainingError("BAD_INPUT", "Укажи карточку и количество")
    if not 1 <= count <= MAX_PER_ENTRY:
        raise TrainingError("BAD_COUNT", f"Количество — от 1 до {MAX_PER_ENTRY}")
    if kind not in KINDS:
        raise TrainingError("BAD_KIND", "Неизвестный тип тренировки")
    note = (note or "").strip()[:200]
    c = appdb.db()
    try:
        club_id, pid = _club_of(c, user["telegram_id"])
        if not club_id:
            raise TrainingError("NO_CLUB", "У тебя нет клуба — учёт тренировок ведут владельцы клубов")
        card = c.execute("SELECT * FROM club_cards WHERE id=? AND club_id=?", (card_id, club_id)).fetchone()
        if not card:
            raise TrainingError("NO_CARD", "Карточка не из твоего состава")
        if (dict(card).get("verify_status") or "approved") != "approved":
            raise TrainingError("NOT_APPROVED", "Карточка ещё не одобрена судьёй")
        period = period_start(today)
        log_id = c.insert_returning_id(
            "INSERT INTO training_log (club_id, user_id, player_id, card_id, card_name, kind, count, note, period_start) "
            "VALUES (?,?,?,?,?,?,?,?,?)",
            (club_id, user["id"], pid, card_id, card["name"], kind, count, note, period))
        total, limit = _week_total(c, club_id, period), weekly_limit()
        violation = None
        if total > limit:
            open_v = c.execute("SELECT id FROM training_violations WHERE club_id=? AND period_start=? AND status='open'",
                               (club_id, period)).fetchone()
            if open_v:
                c.execute("UPDATE training_violations SET total=?, limit_value=?, last_log_id=? WHERE id=?",
                          (total, limit, log_id, open_v["id"]))
                violation = open_v["id"]
            else:
                violation = c.insert_returning_id(
                    "INSERT INTO training_violations (club_id, period_start, total, limit_value, last_log_id) "
                    "VALUES (?,?,?,?,?)", (club_id, period, total, limit, log_id))
                c.execute("INSERT INTO notifications (user_id, text, kind) VALUES (?,?, 'app')",
                          (user["id"], f"⚠️ Лимит тренировок превышен: {total}/{limit} за неделю. "
                                       f"Запись сохранена, админ рассмотрит нарушение."))
        c.commit()
    finally:
        c.close()
    return {"id": log_id, "week_total": total, "limit": limit, "violation_id": violation}


def overview(user: dict, today: date | None = None, history_limit: int = 20) -> dict:
    c = appdb.db()
    try:
        period, limit = period_start(today), weekly_limit()
        club_id, _pid = _club_of(c, user["telegram_id"])
        base = {"kinds": KINDS, "limit": limit, "period_start": period, "max_per_entry": MAX_PER_ENTRY}
        if not club_id:
            return {**base, "club_id": None, "week_total": 0, "cards": [], "history": [], "violations": []}
        totals = {r["card_id"]: r["n"] for r in c.execute(
            "SELECT card_id, SUM(count) n FROM training_log WHERE club_id=? GROUP BY card_id", (club_id,)).fetchall()}
        week = {r["card_id"]: r["n"] for r in c.execute(
            "SELECT card_id, SUM(count) n FROM training_log WHERE club_id=? AND period_start=? GROUP BY card_id",
            (club_id, period)).fetchall()}
        cards = []
        for r in c.execute("SELECT id, name, position, rating FROM club_cards WHERE club_id=? "
                           "ORDER BY rating DESC, id", (club_id,)).fetchall():
            total = totals.get(r["id"], 0)
            cards.append({"id": r["id"], "name": r["name"], "position": r["position"], "rating": r["rating"],
                          "total": total, "week": week.get(r["id"], 0), **level_of(total)})
        history = [dict(r) for r in c.execute(
            "SELECT id, card_id, card_name, kind, count, note, period_start, created_at FROM training_log "
            "WHERE club_id=? ORDER BY id DESC LIMIT ?", (club_id, history_limit)).fetchall()]
        for h in history:
            h["kind_name"] = KINDS.get(h["kind"], h["kind"])
        violations = [dict(r) for r in c.execute(
            "SELECT id, period_start, total, limit_value, status, admin_note, created_at FROM training_violations "
            "WHERE club_id=? ORDER BY id DESC LIMIT 10", (club_id,)).fetchall()]
        club_total = sum(totals.values())
        return {**base, "club_id": club_id, "week_total": _week_total(c, club_id, period),
                "club_total": club_total,
                "cards": cards, "history": history, "violations": violations}
    finally:
        c.close()


def list_violations(status: str | None = None, limit: int = 50) -> list[dict]:
    c = appdb.db()
    try:
        sql = ("SELECT v.*, cl.name club_name FROM training_violations v LEFT JOIN clubs cl ON cl.id=v.club_id")
        args: tuple = ()
        if status in ("open", "closed"):
            sql += " WHERE v.status='open'" if status == "open" else " WHERE v.status!='open'"
        sql += " ORDER BY CASE WHEN v.status='open' THEN 0 ELSE 1 END, v.id DESC LIMIT ?"
        args += (limit,)
        rows = [dict(r) for r in c.execute(sql, args).fetchall()]
        for v in rows:
            v["entries"] = [dict(r) for r in c.execute(
                "SELECT card_name, kind, count, created_at FROM training_log WHERE club_id=? AND period_start=? "
                "ORDER BY id", (v["club_id"], v["period_start"])).fetchall()]
        return rows
    finally:
        c.close()


def resolve_violation(violation_id: int, action: str, note: str, actor_tg: int) -> dict:
    if action not in VIOLATION_ACTIONS:
        raise TrainingError("BAD_ACTION", "Действие: resolved или penalized")
    note = (note or "").strip()[:300]
    c = appdb.db()
    try:
        v = c.execute("SELECT * FROM training_violations WHERE id=?", (violation_id,)).fetchone()
        if not v:
            raise TrainingError("NOT_FOUND", "Нарушение не найдено")
        cur = c.execute("UPDATE training_violations SET status=?, admin_note=?, resolved_by=?, "
                        "resolved_at=datetime('now') WHERE id=? AND status='open'",
                        (action, note, actor_tg, violation_id))
        if cur.rowcount == 0:
            raise TrainingError("CLOSED", "Нарушение уже рассмотрено")
        c.execute("INSERT INTO tournament_audit_log (tournament_id, actor_telegram_id, action, details) "
                  "VALUES (NULL, ?, 'training_violation', ?)",
                  (actor_tg, f"violation={violation_id} club={v['club_id']} {action} {note}".strip()))
        # уведомляем всех, кто писал тренировки клуба за этот период
        for r in c.execute("SELECT DISTINCT user_id FROM training_log WHERE club_id=? AND period_start=? "
                           "AND user_id IS NOT NULL", (v["club_id"], v["period_start"])).fetchall():
            text = (f"🏋️ Нарушение лимита тренировок ({v['total']}/{v['limit_value']}): "
                    f"{VIOLATION_ACTIONS[action]}" + (f". {note}" if note else ""))
            c.execute("INSERT INTO notifications (user_id, text, kind) VALUES (?,?, 'app')", (r["user_id"], text))
        c.commit()
    finally:
        c.close()
    return {"id": violation_id, "status": action}
