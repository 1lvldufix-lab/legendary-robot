"""Хендлеры турнирного ядра (блок 4): сезоны, клубы, календарь, туры, таблицы.

Root-команды: /сезон /кубок /клуб /клубы /календарь /тур /турниры /пары /правкапары /судья.
Кнопки меню flesh: 🏆 Турниры, ⚽ Мой Клуб.
"""
import logging
import re
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

import config
import core
import db as appdb
import league
from clubs_catalog import FORMAT_NAMES, FORMAT_BY_INPUT, find_club, logo_path_for

log = logging.getLogger("bot.tournament")


def _parse_args(text: str) -> tuple[str, dict]:
    """/сезон Сезон-1 дивизионы=2 круги=2 → ('Сезон-1', {'дивизионы': '2', ...})"""
    parts = text.split()
    name_parts, opts = [], {}
    for p in parts:
        if "=" in p:
            k, v = p.split("=", 1)
            opts[k.lower()] = v
        else:
            name_parts.append(p)
    return " ".join(name_parts), opts


def _resolve_player(query: str) -> dict | None:
    """@username | telegram_id → players-строка."""
    q = query.strip().lstrip("@")
    c = appdb.db()
    if q.isdigit():
        row = c.execute("SELECT * FROM players WHERE telegram_id=?", (int(q),)).fetchone()
    else:
        row = c.execute("SELECT * FROM players WHERE username=?", (query.strip().lstrip("@"),)).fetchone()
    c.close()
    return dict(row) if row else None


def _root_only(update: Update) -> bool:
    if not core.is_root(update.effective_user.id):
        return False
    return True


async def deny(update: Update):
    await update.message.reply_text("⛔ Только для root.")


# ===== /сезон =====

async def cmd_season(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _root_only(update):
        return await deny(update)
    name, opts = _parse_args(context.args and " ".join(context.args) or "")
    if not name:
        await update.message.reply_text(
            "Формат: /сезон <название> [дивизионы=N] [режим=manual|challenge_place] "
            "[круги=1|2] [дней=N]\nПример: /сезон Сезон-1 дивизионы=2 круги=2 дней=3"
        )
        return
    n_div = int(opts.get("дивизионы", 1))
    tour_mode = opts.get("режим", "manual")
    if tour_mode not in ("manual", "challenge_place"):
        tour_mode = "manual"  # per план 09: дефолт
    rounds = int(opts.get("круги", 2))
    if rounds not in (1, 2):
        rounds = 2
    days = int(opts.get("дней", config.DEFAULT_TOUR_DAYS))
    tid = league.create_league_season(name, n_div, tour_mode, rounds, days)
    core.audit(tid, update.effective_user.id, "create_season", f"{name} div={n_div} mode={tour_mode} rounds={rounds} days={days}")
    await update.message.reply_text(
        f"✅ Сезон «{name}» создан: дивизионов {n_div}, режим {tour_mode}, "
        f"кругов {rounds}, тур {days} дн. Теперь выдай клубы: /клуб <игрок> <клуб> [дивизион=N], "
        f"затем календарь: /календарь {tid}"
    )


# ===== /кубок =====

async def cmd_cup(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _root_only(update):
        return await deny(update)
    name, opts = _parse_args(context.args and " ".join(context.args) or "")
    if not name:
        await update.message.reply_text(
            "Формат: /кубок <название> формат=ЛЧ|ЛЕ|ЛК [клубы=все|1,2,3] [дивизион=N] [дней=N]"
        )
        return
    fmt = FORMAT_BY_INPUT.get(opts.get("формат", "лч").lower())
    if not fmt:
        await update.message.reply_text("формат=ЛЧ|ЛЕ|ЛК")
        return
    days = int(opts.get("дней", config.DEFAULT_TOUR_DAYS))
    tid = league.create_cup(name, fmt, "manual", days)

    c = appdb.db()
    if opts.get("клубы", "все") == "все" and "дивизион" not in opts:
        rows = c.execute("SELECT id FROM clubs WHERE owner_player_id IS NOT NULL").fetchall()
    elif "дивизион" in opts:
        rows = c.execute(
            "SELECT id FROM clubs WHERE division_id=? AND owner_player_id IS NOT NULL",
            (int(opts["дивизион"]),),
        ).fetchall()
    else:
        ids = [int(x) for x in re.split("[,; ]+", opts.get("клубы", "")) if x.isdigit()]
        rows = [{"id": i} for i in ids]
    c.close()
    club_ids = [r["id"] for r in rows]
    if len(club_ids) < 2:
        await update.message.reply_text(f"Кубок «{name}» создан (#{tid}), но участников <2 — сетка не сгенерирована.")
        return
    ties = league.create_cup_bracket(tid, club_ids)
    core.audit(tid, update.effective_user.id, "create_cup", f"{name} {fmt} participants={len(club_ids)} ties={len(ties)}")
    await update.message.reply_text(
        f"✅ Кубок «{name}» ({FORMAT_NAMES[fmt]}): участников {len(club_ids)}, "
        f"стадия {ties[0]['stage']}, серий {len(ties)}. Серии до 2 побед."
    )


# ===== /клуб =====

async def cmd_club(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _root_only(update):
        return await deny(update)
    args = list(context.args or [])
    if len(args) < 2:
        await update.message.reply_text("Формат: /клуб <@user|id> <клуб> [дивизион=N]\nПример: /клуб @vasya Реал Мадрид дивизион=1")
        return
    player = _resolve_player(args[0])
    if not player:
        await update.message.reply_text("Игрок не найден. Пусть сначала нажмёт /start в боте.")
        return
    club_query, opts = _parse_args(" ".join(args[1:]))
    found = find_club(club_query)
    if not found:
        await update.message.reply_text("Клуб не найден в каталоге FC (80 команд). Смотри /каталог.")
        return
    canon, logo_file = found

    c = appdb.db()
    row = c.execute("SELECT id FROM clubs WHERE name=?", (canon,)).fetchone()
    c.close()
    if row:
        club_id = row["id"]
    else:
        div_id = None
        if "дивизион" in opts:
            c = appdb.db()
            d = c.execute(
                "SELECT id FROM divisions WHERE tournament_id=(SELECT id FROM tournaments WHERE stage!='finished' AND format='league' ORDER BY id DESC LIMIT 1) "
                "AND sort_order=?", (int(opts["дивизион"]),),
            ).fetchone()
            c.close()
            if d:
                div_id = d["id"]
        club_id = league.create_club(canon, logo_path_for(logo_file), div_id, config.CLUB_START_BUDGET)

    league.assign_club_owner(club_id, player["id"])
    # перезаписать игрока в матчей, если календарь уже генерился
    c = appdb.db()
    c.execute("UPDATE matches SET home_player_id=? WHERE home_club_id=? AND home_player_id IS NULL", (player["id"], club_id))
    c.execute("UPDATE matches SET away_player_id=? WHERE away_club_id=? AND away_player_id IS NULL", (player["id"], club_id))
    c.commit()
    c.close()
    core.audit(None, update.effective_user.id, "issue_club", f"club={canon} player={player['telegram_id']}")
    await update.message.reply_text(
        f"✅ {canon} отдана игроку {player['username'] or player['telegram_id']} "
        f"(бюджет {config.CLUB_START_BUDGET:,} ₼).".replace(",", " ")
    )


async def cmd_catalog(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    from clubs_catalog import TEAM_LOGO_MAP
    names = " • ".join(sorted(TEAM_LOGO_MAP))
    for chunk_start in range(0, len(names), 3800):
        await update.message.reply_text(names[chunk_start:chunk_start + 3800])


async def cmd_clubs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _root_only(update):
        return await deny(update)
    c = appdb.db()
    rows = c.execute(
        "SELECT cl.id, cl.name, cl.division_id, cl.budget, p.username "
        "FROM clubs cl LEFT JOIN club_players cp ON cp.club_id=cl.id "
        "LEFT JOIN players p ON p.id=cp.player_id ORDER BY cl.division_id, cl.name"
    ).fetchall()
    c.close()
    lines = [f"#{r['id']} {r['name']} — {r['username'] or 'без владельца'} "
             f"(д{r['division_id'] or '—'}, {r['budget']:,})".replace(",", " ") for r in rows]
    await update.message.reply_text("\n".join(lines) if lines else "Клубов нет.")


# ===== /календарь =====

async def cmd_calendar(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _root_only(update):
        return await deny(update)
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Формат: /календарь <турнир_id> [дивизион=N]\n(без дивизион=N — все дивизиона)")
        return
    tid = int(context.args[0])
    t = league.get_tournament(tid)
    if not t:
        await update.message.reply_text("Турнир не найден.")
        return
    divs = league.tournament_divisions(tid)
    if "дивизион" in (context.args[1:] or []) or (len(context.args) > 1 and context.args[1].startswith("дивизион=")):
        opt = context.args[1].split("=", 1)[1]
        divs = [d for d in divs if d["sort_order"] == int(opt)]
    total = 0
    for d in divs:
        total += league.generate_league_calendar(tid, d["id"])
    if total == 0:
        await update.message.reply_text("Календарь не сгенерирован: в дивизионах меньше 2 клубов.")
        return
    core.audit(tid, update.effective_user.id, "generate_calendar", f"matches={total}")
    await update.message.reply_text(
        f"✅ Календарь «{t['name']}»: туров {t['total_tours']}, матчей {total}. "
        f"Тур 1 открыт (дедлайн {t['tour_days']} дн.). Правка пар — до открытия тура: /правкапары <матч> <дом> <гости>"
    )


# ===== /тур =====

async def cmd_open_tour(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _root_only(update):
        return await deny(update)
    if len(context.args or []) < 2 or not context.args[0].isdigit() or not context.args[1].isdigit():
        await update.message.reply_text("Формат: /тур <турнир_id> <номер> [дней=N]")
        return
    tid, no = int(context.args[0]), int(context.args[1])
    days = None
    for a in context.args[2:]:
        if a.startswith("дней="):
            days = int(a.split("=")[1])
    ok = league.open_tour(tid, no, days)
    await update.message.reply_text("✅ Тур открыт." if ok else "Турнир не найден.")


# ===== /турниры, /пары, /правкапары, /судья =====

async def cmd_tournaments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = []
    for t in league.all_active_tournaments():
        fmt = FORMAT_NAMES.get(t["format"], t["format"])
        lines.append(f"#{t['id']} «{t['name']}» [{fmt}] тур {t['current_tour']}/{t['total_tours']} ({t['tour_mode']})")
    await update.message.reply_text("\n".join(lines) or "Турниров нет.")


async def cmd_pairs(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _root_only(update):
        return await deny(update)
    if len(context.args or []) < 2:
        await update.message.reply_text("Формат: /пары <турнир_id> <тур>")
        return
    text = league.format_matches_for_tour(int(context.args[0]), int(context.args[1]))
    await update.message.reply_text(text)


async def cmd_edit_pair(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    if not _root_only(update):
        return await deny(update)
    if len(context.args or []) < 3 or not all(x.isdigit() for x in context.args[:3]):
        await update.message.reply_text("Формат: /правкапары <матч_id> <дом_club_id> <гости_club_id>")
        return
    mid, home, away = int(context.args[0]), int(context.args[1]), int(context.args[2])
    c = appdb.db()
    m = c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
    if not m:
        await update.message.reply_text("Матч не найден.")
        c.close()
        return
    tour = league.ensure_tour(m["tournament_id"], m["tour_number"] or 0)
    if tour and tour.get("status") == "open":
        await update.message.reply_text("Тур уже открыт — правка пар запрещена (решение 09).")
        c.close()
        return
    c.execute(
        "UPDATE matches SET home_club_id=?, away_club_id=? WHERE id=? AND status='pending'",
        (home, away, mid),
    )
    c.commit()
    c.close()
    core.audit(m["tournament_id"], update.effective_user.id, "edit_pair", f"match={mid} → {home} vs {away}")
    await update.message.reply_text("✅ Пара обновлена.")


async def cmd_judge(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Root: /судья <турнир_id> <@user|id> — выдать/снять (toggle)."""
    if not _root_only(update):
        return await deny(update)
    if len(context.args or []) < 2:
        await update.message.reply_text("Формат: /судья <турнир_id> <@user|id>")
        return
    tid = int(context.args[0])
    player = _resolve_player(context.args[1])
    if not player:
        await update.message.reply_text("Игрок не найден.")
        return
    c = appdb.db()
    row = c.execute(
        "SELECT 1 FROM tournament_admins WHERE tournament_id=? AND telegram_id=?",
        (tid, player["telegram_id"]),
    ).fetchone()
    if row:
        c.execute("DELETE FROM tournament_admins WHERE tournament_id=? AND telegram_id=?",
                  (tid, player["telegram_id"]))
        msg = "снят"
    else:
        c.execute("INSERT OR IGNORE INTO tournament_admins (tournament_id, telegram_id) VALUES (?,?)",
                  (tid, player["telegram_id"]))
        msg = "выдан"
    c.commit()
    c.close()
    core.audit(tid, update.effective_user.id, f"judge_{msg}", str(player["telegram_id"]))
    await update.message.reply_text(f"✅ Судья {msg}.")


# ===== кнопки меню =====

async def menu_tournaments(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    lines = []
    for t in league.all_active_tournaments():
        fmt = FORMAT_NAMES.get(t["format"], t["format"])
        c = appdb.db()
        if t["format"] == "league":
            divs = league.tournament_divisions(t["id"])
            extra = ", ".join(d["name"] for d in divs)
            lines.append(f"🏆 «{t['name']}» [{fmt}] — {extra}; тур {t['current_tour']}/{t['total_tours']}")
        else:
            lines.append(f"🏆 «{t['name']}» [{fmt}] — стадия {t['stage']}")
        c.close()
    player = core.get_player(update.effective_user.id)
    club = core.club_of_player(player["id"]) if player else None
    if club:
        table = league.division_standings(club["division_id"]) if club["division_id"] else []
        if table:
            lines.append("\nТаблица твоего дивизиона:")
            for row in table[:10]:
                name = league.get_club(row["club_id"])["name"]
                lines.append(f"{row['position']}. {name} — {row['points']} очк. ({row['games']} игр, {row['gf']}:{row['ga']})")
    await update.message.reply_text("\n".join(lines) or "Турниров нет — root создаёт командой /сезон.")


async def menu_my_club(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    player = core.get_player(update.effective_user.id)
    if not player:
        await update.message.reply_text("Сначала /start.")
        return
    club = core.club_of_player(player["id"])
    if not club:
        await update.message.reply_text(
            "Клуба нет. Попроси root выдать клуб командой /клуб — или отправь заявку в мини-аппе "
            "(«Мой клуб» → «Запросить клуб»)."
        )
        return
    c = appdb.db()
    cards = c.execute("SELECT COUNT(*) AS n FROM club_cards WHERE club_id=?", (club["id"],)).fetchone()["n"]
    c.close()
    await update.message.reply_text(
        f"⚽ {club['name']}\nБюджет: {club['budget']:,} ₼\nСостав: {cards} карт\n"
        f"Форма: {club['form'] or '—'}\nElo: {club['elo']}".replace(",", " ")
    )


HANDLERS = [
    ("command", "сезон", cmd_season),
    ("command", "кубок", cmd_cup),
    ("command", "клуб", cmd_club),
    ("command", "каталог", cmd_catalog),
    ("command", "клубы", cmd_clubs),
    ("command", "календарь", cmd_calendar),
    ("command", "тур", cmd_open_tour),
    ("command", "турниры", cmd_tournaments),
    ("command", "пары", cmd_pairs),
    ("command", "правкапары", cmd_edit_pair),
    ("command", "судья", cmd_judge),
    ("text", "🏆 Турниры", menu_tournaments),
    ("text", "⚽ Мой Клуб", menu_my_club),
]
