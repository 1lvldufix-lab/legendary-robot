"""Хендлеры результатов (блок 5): репорт скринами → OCR → финализация сразу,
оспаривание, судья, ручной ввод (план 05 раздел 2 + решение 09)."""
import asyncio
import hashlib
import html
import logging
import re

from telegram import InlineKeyboardButton, InlineKeyboardMarkup, Update
from telegram.ext import (CallbackQueryHandler, CommandHandler, ContextTypes,
                          MessageHandler, filters)

import core
import db as appdb
import league
import ocr
import results
import settings as appsettings

log = logging.getLogger("bot.results")


# ===== helpers =====

def _my_pending_matches(telegram_id: int) -> list[dict]:
    player = core.get_player(telegram_id)
    if not player:
        return []
    club = core.club_of_player(player["id"])
    if not club:
        return []
    c = appdb.db()
    rows = c.execute(
        # лига: pending в открытом туре моего дивизиона
        "SELECT m.* FROM matches m JOIN tours t ON t.tournament_id=m.tournament_id "
        " AND t.tour_number=m.tour_number "
        "WHERE t.status='open' AND m.status='pending' "
        "AND (m.home_club_id=? OR m.away_club_id=?) "
        "UNION "
        # кубок: pending без тура (сетка)
        "SELECT m.* FROM matches m JOIN tournaments tr ON tr.id=m.tournament_id "
        "WHERE tr.format!='league' AND m.status='pending' "
        "AND (m.home_club_id=? OR m.away_club_id=?) "
        "ORDER BY id",
        (club["id"], club["id"], club["id"], club["id"]),
    ).fetchall()
    c.close()
    return [dict(r) for r in rows]


def _club_name(c, club_id: int | None) -> str:
    if not club_id:
        return "—"
    row = c.execute("SELECT name FROM clubs WHERE id=?", (club_id,)).fetchone()
    return row["name"] if row else f"#{club_id}"


def _goal_events_from_text(text: str) -> list[dict]:
    """«Antony x2, Wirtz», «Antony 2, Wirtz» или «Antony, Antony, Wirtz» → события (без минут)."""
    events = []
    for part in (text or "").split(","):
        part = part.strip()
        if not part:
            continue
        m = re.match(r"^(.*?)(?:\s*[x×]\s*|\s+)(\d{1,2})$", part)
        if m:
            events += [{"side": "home", "name": m.group(1).strip(), "minute": None}
                       for _ in range(int(m.group(2)))]
        else:
            events.append({"side": "home", "name": part, "minute": None})
    return events


def _parse_extras(args: list[str]) -> tuple[int | None, int | None, list[dict]]:
    """Хвост команды «пен=5:4 голы=Имя x2, Имя Фамилия» → (пен1, пен2, голы).
    context.args режет по пробелам, поэтому голы собираем из всего хвоста и делим по запятым."""
    text = " ".join(args)
    p1 = p2 = None
    pm = re.search(r"пен=\s*(\d{1,2}\s*[:\-–]\s*\d{1,2})", text)
    if pm:
        p1, p2 = _parse_score(pm.group(1))
    gm = re.search(r"голы=(.*?)(?=\s+пен=|$)", text)
    goals = _goal_events_from_text(gm.group(1)) if gm else []
    return p1, p2, goals


async def _notify_player(bot, telegram_id: int | None, text: str, kb=None) -> None:
    if not telegram_id:
        return
    try:
        await bot.send_message(telegram_id, text, reply_markup=kb)
    except Exception:
        log.warning("не доставил ЛС %s", telegram_id)


async def _notify_judges(bot, tournament_id: int, text: str) -> None:
    import config
    ids = set(config.ADMIN_IDS)
    c = appdb.db()
    for r in c.execute("SELECT telegram_id FROM tournament_admins WHERE tournament_id=?",
                       (tournament_id,)).fetchall():
        ids.add(r["telegram_id"])
    c.close()
    for tid in ids:
        try:
            await bot.send_message(tid, text)
        except Exception:
            pass


# ===== 📨 Репорт =====

async def menu_report(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    matches = _my_pending_matches(update.effective_user.id)
    if not matches:
        await update.message.reply_text(
            "ТвоихPending-матчей в открытом туре нет.\n"
            "Если матч должен быть — попроси root проверить календарь."
        )
        return
    c = appdb.db()
    kb = [[InlineKeyboardButton(
        f"#{m['id']} {_club_name(c, m['home_club_id'])} — {_club_name(c, m['away_club_id'])}",
        callback_data=f"report:{m['id']}")] for m in matches]
    c.close()
    await update.message.reply_text(
        "Выбери матч, потом кидай 1–3 скрина FC27 (статистика и/или лента голов).",
        reply_markup=InlineKeyboardMarkup(kb),
    )


async def cb_pick_report_match(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    _, mid = update.callback_query.data.split(":")
    context.user_data["report_match"] = int(mid)
    context.user_data["report_goals"] = []
    context.user_data["ocr_skip"] = 0
    await update.callback_query.edit_message_text(
        f"Матч #{mid} выбран. Кидай скрины — счёт возьму из шапки, финализирую сразу.")


# ===== фото → OCR → финализация =====

async def on_photo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg = update.effective_user
    match_id = context.user_data.get("report_match")
    if not match_id:
        pend = _my_pending_matches(tg.id)
        if len(pend) == 1:
            match_id = pend[0]["id"]
            context.user_data["report_match"] = match_id
            context.user_data["report_goals"] = []
        else:
            await update.message.reply_text("Сначала выбери матч: 📨 Репорт.")
            return

    c = appdb.db()
    m = c.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m or m["status"] not in ("pending", "reported"):
        c.close()
        await update.message.reply_text("Матч уже финализирован или не найден.")
        return
    m = dict(m)

    photo = update.message.photo[-1]
    file = await update.message.effective_attachment[-1].get_file()
    data = await file.download_as_bytearray()
    data = bytes(data)
    sha = hashlib.sha256(data).hexdigest()
    dup = c.execute(
        "SELECT 1 FROM processed_screenshots WHERE sha256=? AND tournament_id=?",
        (sha, m["tournament_id"]),
    ).fetchone()
    c.close()
    if dup:
        await update.message.reply_text("Этот скрин уже обрабатывался (дедуп). Пришли следующий.")
        return
    # хэш пишем только после финализации — иначе «🔄 Другой моделью» упрётся в дедуп

    await update.message.reply_text("⏳ Распознаю скрин (OCR-каскад)…")
    skip = context.user_data.get("ocr_skip", 0)
    cascade = ocr.build_cascade()
    if skip and skip < len(cascade):
        cascade = cascade[skip:]
    parsed = await asyncio.to_thread(ocr.parse_screenshots, [data], cascade or None)
    if not parsed:
        remaining = len(ocr.build_cascade()) - skip - 1 if cascade else 0
        from telegram import InlineKeyboardButton, InlineKeyboardMarkup
        if remaining > 0:
            kb = InlineKeyboardMarkup([[InlineKeyboardButton(
                "🔄 Другой моделью", callback_data="ocralt")]])
            await update.message.reply_text(
                "⚠️ Не удалось распознать этим провайдером. Попробовать следующим?",
                reply_markup=kb)
        else:
            await update.message.reply_text(
                "⚠️ Провайдеры кончились. Введи вручную: /вручную <id матча> <счёт> "
                "[голы=Имя x2, Имя]\nНапример: /вручную 41 2:1 голы=Антоний x2, Виртц"
            )
        return
    context.user_data["ocr_skip"] = 0

    merged_goals = list(context.user_data.get("report_goals") or [])
    for g in parsed.get("goal_events") or []:
        g.setdefault("minute", None)
        merged_goals.append(g)
    context.user_data["report_goals"] = merged_goals

    summary = results.finalize_match(
        match_id, parsed["score_home"], parsed["score_away"],
        (parsed.get("penalties") or {}).get("home") if parsed.get("penalties") else None,
        (parsed.get("penalties") or {}).get("away") if parsed.get("penalties") else None,
        merged_goals, actor="ocr",
    )
    c = appdb.db()
    c.execute(
        "INSERT INTO processed_screenshots (sha256, tournament_id, match_id, reporter_id) VALUES (?,?,?,?) "
        "ON CONFLICT DO NOTHING",
        (sha, m["tournament_id"], match_id, tg.id),
    )
    c.commit()
    home = _club_name(c, summary["home_club"])
    away = _club_name(c, summary["away_club"])
    # соперник — тот, кто не репортер
    player = core.get_player(tg.id)
    club = core.club_of_player(player["id"]) if player else None
    opp_club = summary["away_club"] if club and club["id"] == summary["home_club"] else summary["home_club"]
    row = c.execute(
        "SELECT p.telegram_id FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=?",
        (opp_club,),
    ).fetchone()
    opp_tg = row["telegram_id"] if row else None
    judges = [r["telegram_id"] for r in c.execute(
        "SELECT telegram_id FROM tournament_admins WHERE tournament_id=?",
        (summary["tournament_id"],)).fetchall()]
    c.close()

    text = (f"✅ Матч #{match_id} финализирован: {home} {summary['score']}{summary['pens']} {away}\n"
            f"Голов распознано: {len(merged_goals)}. Провайдер: "
            f"{', '.join(w.split('провайдеры: ')[-1] for w in parsed['warnings'] if 'провайдеры' in w) or '—'}")
    if parsed.get("warnings"):
        extra = [w for w in parsed["warnings"] if "провайдеры" not in w]
        if extra:
            text += "\n⚠️ " + "; ".join(extra)
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("⚔️ Оспорить", callback_data=f"disp:{match_id}")]])
    await update.message.reply_text(text)
    context.user_data.pop("report_match", None)
    context.user_data.pop("report_goals", None)

    window = appsettings.setting_int("dispute_window_hours", 24)
    await _notify_player(context.bot, opp_tg,
                         f"Соперник прислал результат {home} {summary['score']}{summary['pens']} {away}.\n"
                         f"Не согласен? Оспорь в течение {window} ч:",
                         kb)
    for j in judges:
        await _notify_player(context.bot, j, f"Матч #{match_id}: {home} {summary['score']}{summary['pens']} {away} (OCR).")


# ===== оспаривание =====

async def cb_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    await update.callback_query.answer()
    _, mid = update.callback_query.data.split(":")
    mid = int(mid)
    msg = results.dispute_match(mid, update.effective_user.id)
    if msg != "ok":
        await update.callback_query.edit_message_text(msg)
        return
    c = appdb.db()
    m = dict(c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone())
    home, away = _club_name(c, m["home_club_id"]), _club_name(c, m["away_club_id"])
    c.close()
    await update.callback_query.edit_message_text(
        f"⚔️ Матч #{mid} ({home} — {away}) помечен «спорный». Ставки на него заморожены. "
        f"Судья решит: /спор {mid} <счёт>")
    await _notify_judges(
        context.bot, m["tournament_id"],
        f"⚔️ Спор по матчу #{mid}: {home} {m['score1']}:{m['score2']} {away}. "
        f"Реши: /спор {mid} <счёт> [пен=h:a] [голы=...]")

async def cmd_disputes(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    c = appdb.db()
    rows = c.execute("SELECT * FROM matches WHERE status='disputed' ORDER BY id").fetchall()
    if not rows:
        c.close()
        await update.message.reply_text("Спорных матчей нет.")
        return
    lines = []
    for m in rows:
        lines.append(f"#{m['id']} {_club_name(c, m['home_club_id'])} — {_club_name(c, m['away_club_id'])} "
                     f"({m['score1']}:{m['score2']})")
    c.close()
    await update.message.reply_text("\n".join(lines))


def _parse_score(s: str) -> tuple[int, int]:
    m = re.match(r"^(\d{1,2})\s*[:\-–]\s*(\d{1,2})$", s.strip())
    if not m:
        raise ValueError("счёт вида 2:1")
    return int(m.group(1)), int(m.group(2))


def _is_judge_of(update: Update, tournament_id: int) -> bool:
    return core.is_tournament_admin(tournament_id, update.effective_user.id)


async def cmd_resolve_dispute(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/спор <match_id> <счёт> [пен=h:a] [голы=...]"""
    if len(context.args or []) < 2:
        await update.message.reply_text("Формат: /спор <матч> <счёт> [пен=5:4] [голы=Имя x2, Имя]")
        return
    if not context.args[0].isdigit():
        await update.message.reply_text("id матча — число.")
        return
    mid = int(context.args[0])
    c = appdb.db()
    m = c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
    c.close()
    if not m:
        await update.message.reply_text("Матч не найден.")
        return
    if not _is_judge_of(update, m["tournament_id"]):
        await update.message.reply_text("⛔ Только судья турнира.")
        return
    try:
        s1, s2 = _parse_score(context.args[1])
        p1, p2, goals = _parse_extras(context.args[2:])
    except ValueError as e:
        await update.message.reply_text(f"Не разобрал: {e}")
        return
    summary = results.resolve_dispute(mid, s1, s2, p1, p2, goals, update.effective_user.id)
    c = appdb.db()
    await update.message.reply_text(
        f"✅ Спор решён: {_club_name(c, summary['home_club'])} {summary['score']}{summary['pens']} "
        f"{_club_name(c, summary['away_club'])}. Купоны разморожены, ставки пересчитаны.")
    c.close()


# ===== ручной ввод =====

async def cmd_manual(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/вручную <match_id> <счёт> [пен=h:a] [голы=Имя x2, Имя]"""
    if len(context.args or []) < 2:
        await update.message.reply_text(
            "Формат: /вручную <матч> <счёт> [пен=5:4] [голы=Имя x2, Имя]\n"
            "Голы — авторы из состава твоего клуба и соперника, через запятую.")
        return
    if not context.args[0].isdigit():
        await update.message.reply_text("id матча — число.")
        return
    mid = int(context.args[0])
    c = appdb.db()
    m = c.execute("SELECT * FROM matches WHERE id=?", (mid,)).fetchone()
    c.close()
    if not m:
        await update.message.reply_text("Матч не найден.")
        return
    m = dict(m)
    player = core.get_player(update.effective_user.id)
    club = core.club_of_player(player["id"]) if player else None
    is_owner = club and club["id"] in (m["home_club_id"], m["away_club_id"])
    is_judge = _is_judge_of(update, m["tournament_id"])
    if not (is_owner or is_judge):
        await update.message.reply_text("⛔ Ты не участник матча и не судья.")
        return
    if m["status"] == "cancelled":
        await update.message.reply_text("Матч отменён — вносить нечего.")
        return
    # владелец вносит только несыгранный матч; правка итога — судья/root (с пересчётом ставок)
    if m["status"] not in ("pending", "reported") and not is_judge:
        await update.message.reply_text(
            "⛔ Матч уже финализирован. Не согласен — «⚔️ Оспорить», исправит судья.")
        return
    try:
        s1, s2 = _parse_score(context.args[1])
        p1, p2, goals = _parse_extras(context.args[2:])
    except ValueError as e:
        await update.message.reply_text(f"Не разобрал: {e}")
        return
    # имена без стороны: раскладываем по составам клубов
    c = appdb.db()

    def roster_names(club_id):
        if not club_id:
            return set()
        return {r["name"].lower() for r in c.execute(
            "SELECT name FROM club_cards WHERE club_id=?", (club_id,)).fetchall()}

    home_names, away_names = roster_names(m["home_club_id"]), roster_names(m["away_club_id"])
    c.close()
    for g in goals:
        n = g["name"].lower()
        if n in home_names:
            g["side"] = "home"
        elif n in away_names:
            g["side"] = "away"
    summary = results.finalize_match(mid, s1, s2, p1, p2, goals, actor="manual")
    c = appdb.db()
    await update.message.reply_text(
        f"✅ Внесено вручную: {_club_name(c, summary['home_club'])} {summary['score']}{summary['pens']} "
        f"{_club_name(c, summary['away_club'])}")
    c.close()


async def cb_ocr_alternate(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Кнопка «🔄 Другой моделью» (план 06): следующая попытка — со следующего провайдера."""
    await update.callback_query.answer()
    context.user_data["ocr_skip"] = context.user_data.get("ocr_skip", 0) + 1
    await update.callback_query.edit_message_text(
        "Ок, кидай тот же скрин — возьму следующий провайдер каскада.")


HANDLERS = [
    ("text", "📨 Репорт", menu_report),
    ("callback", r"^ocralt$", cb_ocr_alternate),
    ("callback", r"^report:\d+$", cb_pick_report_match),
    ("photo", None, on_photo),
    ("callback", r"^disp:\d+$", cb_dispute),
    ("command", "споры", cmd_disputes),
    ("command", "спор", cmd_resolve_dispute),
    ("command", "вручную", cmd_manual),
]
