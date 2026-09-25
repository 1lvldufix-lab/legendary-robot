"""Хендлеры долгов (блок 9): 💰 Долги, напоминания 3×/день, /скан Challenge Place.

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


# ===== /скан Challenge Place =====

async def cmd_scan(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/скан <путь_к_html|url> [турнир_id] — root или судья."""
    if not (core.is_root(update.effective_user.id) or core.is_app_admin(update.effective_user.id)):
        await update.message.reply_text("⛔ Только root/админ.")
        return
    if not context.args:
        await update.message.reply_text(
            "Формат: /скан <файл.html|url> [турнир_id]\n"
            "Сайт за Cloudflare — надёжнее сохранить страницу (Ctrl+S) и дать файл.")
        return
    source = context.args[0]
    tournament_id = int(context.args[1]) if len(context.args) > 1 else None
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
            await update.message.reply_text("Нет турниров — сначала /сезон.")
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


HANDLERS = [
    ("text", "💰 Долги", menu_debts),
    ("command", "скан", cmd_scan),
]
