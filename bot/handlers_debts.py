"""Хендлеры долгов (блок 9): 💰 Долги, напоминания 3×/день, /scan Challenge Place.

Публикации только в ЛС (решение 09). reminder_log пишется по дате (3 раза/день,
kind=debts_ЧЧ) — история не затирается.
"""
import logging
from datetime import datetime

from telegram import Update
from telegram.ext import ContextTypes

import core
import db as appdb
import parser_cp
import settings as appsettings

log = logging.getLogger("bot.debts")


# ===== 💰 Долги =====

async def menu_debts(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    player = core.get_player(update.effective_user.id)
    club = core.club_of_player(player["id"]) if player else None
    if not club:
        await update.message.reply_text("Клуба нет — и долгов нет.")
        return
    c = appdb.db()
    rows = c.execute(
        "SELECT * FROM debts WHERE club_id=? AND status='open' ORDER BY id", (club["id"],)).fetchall()
    budget = c.execute("SELECT budget FROM clubs WHERE id=?", (club["id"],)).fetchone()["budget"]
    c.close()
    total = sum(r["amount"] for r in rows)
    lines = [f"💰 {club['name']}: бюджет {budget:,} ₼".replace(",", " ")]
    if rows:
        lines.append(f"Открытые долги: {total:,} ₼".replace(",", " "))
        lines += [f"• #{r['id']} {r['amount']:,} ₼ — {r['reason']}".replace(",", " ") for r in rows]
    else:
        lines.append("Долгов нет. Красава.")
    await update.message.reply_text("\n".join(lines))


# ===== напоминания 3×/день (джоба) =====

async def job_debt_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    now = datetime.now()
    kind = f"debts_{now.strftime('%H')}"
    c = appdb.db()
    tournaments = [r["id"] for r in c.execute("SELECT id FROM tournaments WHERE stage!='finished'").fetchall()]
    notified = 0
    for tid in tournaments:
        already = c.execute(
            "SELECT 1 FROM reminder_log WHERE tournament_id=? AND kind=? AND date=?",
            (tid, kind, now.strftime("%Y-%m-%d")),
        ).fetchone()
        if already:
            continue
        c.execute(
            "INSERT OR IGNORE INTO reminder_log (tournament_id, kind, date) VALUES (?,?,?)",
            (tid, kind, now.strftime("%Y-%m-%d")),
        )
        c.commit()
    # должникам — ЛС (по всем открытым долгам, троттлинг по kind в reminder_log на клуб-уровне не нужен:
    # отправка идёт максимум 3 раза в день по расписанию)
    rows = c.execute(
        "SELECT d.club_id, d.amount, d.reason, cl.name AS club_name, p.telegram_id "
        "FROM debts d JOIN clubs cl ON cl.id=d.club_id "
        "LEFT JOIN club_players cp ON cp.club_id=d.club_id "
        "LEFT JOIN players p ON p.id=cp.player_id "
        "WHERE d.status='open'").fetchall()
    c.close()
    seen = set()
    for r in rows:
        if not r["telegram_id"] or r["telegram_id"] in seen:
            continue
        seen.add(r["telegram_id"])
        try:
            await context.bot.send_message(
                r["telegram_id"],
                f"💰 Напоминание: у клуба {r['club_name']} открыт долг "
                f"{r['amount']:,} ₼ ({r['reason']}).".replace(",", " "),
            )
            notified += 1
        except Exception:
            pass
    if notified:
        log.info("[debts] напоминаний отправлено: %s", notified)


# ===== /scan Challenge Place =====

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/scan <путь_к_html|url> [турнир_id] — root или судья."""
    if not (core.is_root(update.effective_user.id) or core.is_app_admin(update.effective_user.id)):
        await update.message.reply_text("⛔ Только root/админ.")
        return
    if not context.args:
        await update.message.reply_text(
            "Формат: /scan <файл.html|url> [турнир_id]\n"
            "Сайт за Cloudflare — надёжнее сохранить страницу (Ctrl+S) и дать файл.")
        return
    source = context.args[0]
    tournament_id = int(context.args[1]) if len(context.args) > 1 else None
    if not source.startswith("http"):
        # локальный файл читает сервер — только root и только сохранённая страница/фикстура
        if not core.is_root(update.effective_user.id):
            await update.message.reply_text("⛔ Скан из файла — только root. Дай ссылку на турнир.")
            return
        if not source.lower().endswith((".html", ".htm", ".json")):
            await update.message.reply_text("Файл должен быть .html/.htm/.json (сохранённая страница).")
            return
    await update.message.reply_text("⏳ Сканирую…")
    try:
        if source.startswith("http"):
            text = await parse_cp_async(source)
        else:
            import os
            if not os.path.exists(source):
                await update.message.reply_text("Файл не найден.")
                return
            with open(source, encoding="utf-8", errors="replace") as f:
                text = f.read()
        state = parser_cp.parse_state(text)
        if not tournament_id:
            c = appdb.db()
            row = c.execute("SELECT id FROM tournaments ORDER BY id DESC LIMIT 1").fetchone()
            c.close()
            tournament_id = row["id"] if row else None
        if not tournament_id:
            await update.message.reply_text("Нет турниров — сначала /season.")
            return
        report = parser_cp.import_state(state, tournament_id)
        core.audit(tournament_id, update.effective_user.id, "cp_scan",
                   f"created={report['created']} updated={report['updated']} alerts={len(report['alerts'])}")
        await update.message.reply_text(parser_cp.format_scan_report(report))
        # алерты root (публикации только в ЛС — решение 09)
        import config
        for aid in config.ADMIN_IDS:
            if report["alerts"]:
                try:
                    await context.bot.send_message(
                        aid, f"⚠️ Скан Challenge Place ({tournament_id}):\n" + "\n".join(report["alerts"][:20]))
                except Exception:
                    pass
    except Exception as e:
        log.exception("скан упал")
        await update.message.reply_text(f"⚠️ Скан не удался: {e}")


async def parse_cp_async(url: str) -> str:
    import asyncio
    return await asyncio.to_thread(parser_cp.try_fetch_via_browser, url)


# ===== /paid — закрыть долг =====

def _is_judge_anywhere(telegram_id: int) -> bool:
    c = appdb.db()
    row = c.execute("SELECT 1 FROM tournament_admins WHERE telegram_id=?", (telegram_id,)).fetchone()
    c.close()
    return bool(row)


def mark_debt(debt_id: int, status: str = "paid") -> dict | None:
    """open → paid/cancelled. None — долга нет или он уже закрыт."""
    c = appdb.db()
    row = c.execute(
        "SELECT d.*, cl.name AS club_name FROM debts d LEFT JOIN clubs cl ON cl.id=d.club_id "
        "WHERE d.id=? AND d.status='open'", (debt_id,)).fetchone()
    if not row:
        c.close()
        return None
    c.execute("UPDATE debts SET status=? WHERE id=?", (status, debt_id))
    owner = c.execute(
        "SELECT p.telegram_id FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=?",
        (row["club_id"],)).fetchone()
    c.commit()
    c.close()
    return {**dict(row), "owner_tg": owner["telegram_id"] if owner else None}


async def cmd_debt_paid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/paid <id долга> [списать] — root или судья."""
    uid = update.effective_user.id
    if not (core.is_root(uid) or _is_judge_anywhere(uid)):
        await update.message.reply_text("⛔ Только root или судья.")
        return
    args = list(context.args or [])
    if not args or not args[0].lstrip("#").isdigit():
        await update.message.reply_text(
            "Формат: /paid <id долга> [списать]\nid видно в «💰 Долги» (#12).")
        return
    debt_id = int(args[0].lstrip("#"))
    status = "cancelled" if len(args) > 1 and args[1].lower() in ("списать", "отмена") else "paid"
    d = mark_debt(debt_id, status)
    if not d:
        await update.message.reply_text("Открытого долга с таким id нет.")
        return
    verb = "списан" if status == "cancelled" else "оплачен"
    amount = f"{d['amount']:,}".replace(",", " ")
    core.audit(None, uid, f"debt_{status}", f"debt={debt_id} club={d['club_id']} amount={d['amount']}")
    await update.message.reply_text(f"✅ Долг #{debt_id} ({d['club_name']}, {amount} ₼) {verb}.")
    if d["owner_tg"]:
        try:
            await context.bot.send_message(
                d["owner_tg"], f"💰 Долг #{debt_id} на {amount} ₼ ({d['reason']}) {verb}.")
        except Exception:
            pass


HANDLERS = [
    ("text", "💰 Долги", menu_debts),
    ("command", "scan", cmd_scan),
    ("command", "paid", cmd_debt_paid),
]
