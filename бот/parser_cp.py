"""Парсер Challenge Place (план 07, рекон 24.09).

Сайт за Cloudflare: отдельного API нет, весь дашборд лежит в
window.__INITIAL_STATE__ (SSR). Честный путь — реальный браузер:
- в проде: локальный headless-браузер (если установлен playwright);
- всегда: /скан <файл.html|файл.json> со сохранённой страницей.

Иерархия: stage → round → series → матчи (order 1..n, серии до 2 побед).
Всё импортное помечено source='challenge-place' + external_id — откатимо.
"""
import json
import logging
import re
from datetime import datetime

import db as appdb

log = logging.getLogger("bot.parser_cp")

STATE_RE = re.compile(r"window\.__INITIAL_STATE__\s*=\s*({.*?});?\s*</script>", re.DOTALL)
DASH_KEY = "room-challenge-dashboard"


def parse_state(text: str) -> dict:
    """HTML страницы / чистый JSON / компактная фикстура → нормализованный дашборд."""
    if not text or not text.strip():
        raise ValueError("пустой ввод")
    stripped = text.strip()
    if stripped.startswith("{"):
        data = json.loads(stripped)
    else:
        m = STATE_RE.search(stripped)
        if not m:
            raise ValueError("window.__INITIAL_STATE__ не найден — сохрани полную страницу")
        data = json.loads(m.group(1))

    dash = data.get("rooms", {}).get(DASH_KEY, data)
    if not isinstance(dash, dict):
        raise ValueError("room-challenge-dashboard не найден — структура сайта изменилась")

    def values(o):
        if o is None:
            return []
        return o if isinstance(o, list) else list(o.values())

    competitors = [{
        "external_id": c.get("id"),
        "name": (c.get("name") or "").strip(),
        "acronym": c.get("acronym"),
        "img": c.get("img"),
        "wins": (c.get("stats") or {}).get("wins", 0),
        "draws": (c.get("stats") or {}).get("draws", 0),
        "losses": (c.get("stats") or {}).get("losses", 0),
    } for c in values(dash.get("competitors"))]

    matches = []
    for bucket in ("latestMatches", "liveMatches", "upcomingMatches"):
        for m in values(dash.get(bucket)):
            matches.append({
                "external_id": m.get("id"),
                "stage_id": m.get("stageId"),
                "round_id": m.get("roundId"),
                "series_id": m.get("seriesId"),
                "order": m.get("order"),
                "date": m.get("date"),
                "home_external_id": m.get("homeCompetitor"),
                "away_external_id": m.get("awayCompetitor"),
                "home_score": m.get("homeScore"),
                "away_score": m.get("awayScore"),
                "winner_slot": m.get("winnerSlot"),
                "bucket": bucket,
            })
    return {"status": dash.get("status"), "competitors": competitors, "matches": matches}


# ===== сопоставление с нашей БД =====

def match_clubs(c, competitors: dict[str, dict], match: dict) -> tuple[int | None, int | None, list[str]]:
    """competitor → наш club: по имени (каталог лого), иначе по акрониму. Возвращает (home, away, проблемы)."""
    problems = []
    out = []
    for side in ("home", "away"):
        comp = competitors.get(match[f"{side}_external_id"])
        if not comp:
            out.append(None)
            continue
        row = c.execute("SELECT id FROM clubs WHERE LOWER(name)=LOWER(?)", (comp["name"],)).fetchone()
        if not row and comp.get("acronym"):
            row = c.execute("SELECT id FROM clubs WHERE LOWER(name)=LOWER(?)", (comp["acronym"],)).fetchone()
        if not row:
            problems.append(f"нет клуба «{comp['name']}»")
            out.append(None)
        else:
            out.append(row["id"])
    return out[0], out[1], problems


def import_state(state: dict, tournament_id: int) -> dict:
    """Импорт дашборда: матчи с счётом → status='reported' (source=challenge-place),
    расхождения с нашими confirmed-счётами → список алертов (долги/споры — решает админ)."""
    competitors = {c["external_id"]: c for c in state["competitors"]}
    c = appdb.db()
    created, updated, alerts = 0, 0, []
    for m in state["matches"]:
        if m["home_score"] is None:
            continue  # без счёта — не результат
        home, away, problems = match_clubs(c, competitors, m)
        if home is None or away is None:
            alerts.append(f"матч {m['external_id']}: " + "; ".join(problems))
            continue
        existing = c.execute(
            "SELECT * FROM matches WHERE external_id=?", (m["external_id"],)).fetchone()
        if existing:
            our = c.execute(
                "SELECT * FROM matches WHERE id=?", (existing["id"],)).fetchone()
            if our["status"] == "confirmed" and (
                our["score1"] != m["home_score"] or our["score2"] != m["away_score"]):
                alerts.append(
                    f"счёт разошёлся: матч #{our['id']} у нас {our['score1']}:{our['score2']}, "
                    f"на сайте {m['home_score']}:{m['away_score']}")
            c.execute(
                "UPDATE matches SET score1=?, score2=?, status=CASE WHEN status='pending' THEN 'reported' ELSE status END, "
                "source='challenge-place' WHERE id=?", (m["home_score"], m["away_score"], our["id"]))
            updated += 1
        else:
            c.insert_returning_id(
                "INSERT INTO matches (tournament_id, home_club_id, away_club_id, score1, score2, "
                "status, source, external_id, played_at) VALUES (?,?,?,?,?, 'reported', 'challenge-place', ?, datetime('now'))",
                (tournament_id, home, away, m["home_score"], m["away_score"], m["external_id"]),
            )
            created += 1
    c.commit()
    c.close()
    return {"created": created, "updated": updated, "alerts": alerts,
            "competitors": len(state["competitors"]), "scored_matches": len(state["matches"])}


def try_fetch_via_browser(url: str) -> str:
    """Headless-браузер: открывает турнир, ждёт Cloudflare, достаёт стейт.
    Cloudflare детектит playwright-автоматизацию (проверено 25.09: и headless-shell,
    и headed Chrome через CDP ловят челлендж) — тогда поднимаем внятную ошибку:
    сохранить страницу (Ctrl+S) и дать боту файл, или скан через browser-use."""
    try:
        from playwright.sync_api import sync_playwright
    except ImportError:
        raise RuntimeError(
            "playwright не установлен: venv/bin/pip install playwright && "
            "venv/bin/python -m playwright install chromium. Либо сохрани страницу "
            "(Ctrl+S) и дай боту файл: /скан <путь>.")
    html = None
    with sync_playwright() as p:
        for channel in (None, "chrome"):   # playwright chromium, затем установленный Chrome
            try:
                browser = p.chromium.launch(headless=True, channel=channel)
            except Exception:
                continue
            page = browser.new_page()
            try:
                page.goto(url, wait_until="domcontentloaded", timeout=60000)
                page.wait_for_timeout(10000)  # челлендж Cloudflare + гидрация SPA
                state_json = page.evaluate(
                    "() => { const s = window.__INITIAL_STATE__;"
                    " return s ? JSON.stringify(s.rooms['room-challenge-dashboard'] ?? s) : null; }")
                if state_json:
                    browser.close()
                    return state_json
                html = page.content()
            finally:
                browser.close()
    if html and "challenge-dashboard" not in html:
        raise RuntimeError(
            "Cloudflare не пропустил автоматизацию (Just a moment). Сохрани страницу "
            "турнира в обычном браузере (Ctrl+S, «веб-страница целиком») и дай боту "
            "файл: /скан <путь> — парсер вытащит window.__INITIAL_STATE__ из HTML.")
    return html or ""


def format_scan_report(report: dict) -> str:
    lines = [f"📊 Challenge Place: участников {report['competitors']}, "
             f"матчей со счётом {report['scored_matches']}.",
             f"Импорт: создано {report['created']}, обновлено {report['updated']}."]
    if report["alerts"]:
        lines.append("⚠️ Расхождения:")
        lines += [f"  • {a}" for a in report["alerts"]]
    else:
        lines.append("Расхождений нет.")
    return "\n".join(lines)
