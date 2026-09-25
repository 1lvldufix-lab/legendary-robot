"""Точка входа KURILKA SIGARKI: турнирный бот (PTB) + сервер мини-аппа (aiohttp)
в одном процессе и одном asyncio-цикле.

Блок 3: каркас — logging, error handler, /start с game_nickname, нижнее меню
(план 05 раздел 8). Flesh кнопок — блоки 4–10.
"""
import asyncio
import logging
import re
import sys
from pathlib import Path

from telegram import Update
from telegram.ext import (Application, CallbackQueryHandler, CommandHandler,
                          ContextTypes, ConversationHandler, MessageHandler,
                          filters)

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

import config
import db as appdb
import core
import handlers_admin as ha
import handlers_debts as hd
import handlers_results as hr
import handlers_tournament as ht
from miniapp.server import start_site

logging.basicConfig(
    format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    level=logging.INFO,
)
logging.getLogger("httpx").setLevel(logging.WARNING)
log = logging.getLogger("bot.main")

# ЛС root-админам для пересылки «🐞 Связь»
CONTACT, REPLY = range(2)


# ===== /start =====

async def cmd_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    tg = update.effective_user
    appdb.init_db()
    core.ensure_player(tg.id, tg.username)
    player = core.get_player(tg.id)
    await update.message.reply_text(
        "🔥 KURILKA SIGARKI — турнир FC27 + ставки на дым.\n\n"
        "🏆 Турниры, ⚽ Мой Клуб, 💰 Долги, 📨 Репорт, 🔧 Настройки, "
        "🐞 Связь, 👮 Админ, ℹ️ Помощь.",
        reply_markup=core.menu_keyboard(),
    )
    if not player["game_nickname"]:
        await update.message.reply_text(
            "Пришли ник, под которым играешь в FC27 — по нему будем "
            "сопоставлять твои скрины результатов."
        )
        return
    await update.message.reply_text(core.send_menu_text(), reply_markup=core.webapp_keyboard())


# ===== запрос game_nickname (для OCR-сопоставления) =====

async def on_text(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """Ловим текст: свободный ник после /start → сохраняем; кнопки меню → колбеки."""
    text = (update.message.text or "").strip()
    tg = update.effective_user
    player = core.get_player(tg.id)
    # кнопка меню или команда — не ник, даже если ник ещё не задан
    if (player and not player["game_nickname"] and not text.startswith("/")
            and text not in core.MENU_BUTTONS):
        await _save_nickname(update, text)
        return

    if text == "🔧 Настройки":
        nick = (player or {}).get("game_nickname") if player else None
        await update.message.reply_text(
            "🔧 Настройки\n\n"
            f"Ник FC27: {nick or 'не задан'}\n"
            "Сменить: /nick НовыйНик\n\n"
            "Результаты ставок приходят сюда в ЛС и в 🔔 мини-аппа.",
            reply_markup=core.webapp_keyboard(),
        )
        return
    if text == "👮 Админ":
        if not core.is_app_admin(tg.id):
            await update.message.reply_text("⛔ Раздел только для админов.")
            return
        await update.message.reply_text(ADMIN_HELP, reply_markup=core.webapp_keyboard())
        return
    if text == "ℹ️ Помощь":
        await update.message.reply_text(HELP_TEXT, reply_markup=core.webapp_keyboard())
        return
    if text.startswith("🐞"):
        return
    if not text.startswith("/"):
        await update.message.reply_text("Не понял. Используй кнопки меню внизу.")


HELP_TEXT = (
    "ℹ️ Как это работает:\n"
    "1. Root выдаёт тебе клуб командой.\n"
    "2. Играешь матчи тура в FC27 Mobile.\n"
    "3. Кидаешь 1–3 скрина «Статистика матча» прямо в чат — бот сам найдёт матч\n"
    "   по никам FC27 и засчитает счёт (подробнее — кнопка 📨 Репорт).\n"
    "4. Ставки на те же матчи — в мини-аппе.\n\n"
    "Команды: /nick /tournaments /manual /myid"
)

ADMIN_HELP = (
    "👮 Админ-команды\n\n"
    "Сезон: /season /cup /tour /pairs /editpair /final\n"
    "Клубы: /club /clubs /catalog\n"
    "Судейство: /judge /disputes /resolve /manual\n"
    "Деньги: /promo /paid /scan\n"
    "Права: /admin /judge · свой ID: /myid\n\n"
    "Ставки, void, пауза, игроки — в админ-панели мини-аппа (Кабинет → 👮)."
)


async def _save_nickname(update: Update, nick: str) -> None:
    tg = update.effective_user
    nick = nick.strip()[:32]
    c = appdb.db()
    c.execute("UPDATE players SET game_nickname=? WHERE telegram_id=?", (nick, tg.id))
    c.commit()
    c.close()
    core.audit(None, tg.id, "set_game_nickname", nick)
    await update.message.reply_text(
        f"Ник «{nick}» записан. Теперь скрины бьются по нему.", reply_markup=core.menu_keyboard()
    )
    await update.message.reply_text(core.send_menu_text(), reply_markup=core.webapp_keyboard())


async def cmd_nick(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/nick НовыйНик — смена ника FC27."""
    core.ensure_player(update.effective_user.id, update.effective_user.username)
    if not context.args:
        await update.message.reply_text("Формат: /nick НовыйНик")
        return
    await _save_nickname(update, " ".join(context.args))


async def cmd_myid(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/myid — свой Telegram ID (его вписывают в ADMIN_IDS, чтобы стать root)."""
    tg = update.effective_user
    role = "root ✅" if core.is_root(tg.id) else ("админ мини-аппа" if core.is_app_admin(tg.id) else "игрок")
    await update.message.reply_text(
        f"Твой Telegram ID: {tg.id}\nРоль: {role}\n\n"
        "Чтобы стать root: впиши ID в bot/.env → ADMIN_IDS=... и перезапусти бота.")


# меню «/» в Telegram: игрокам — короткое, root — полное (BotCommandScopeChat)
PLAYER_COMMANDS = [
    ("start", "Главное меню"), ("nick", "Ник FC27 для распознавания скринов"),
    ("tournaments", "Турниры"), ("manual", "Внести счёт вручную"), ("myid", "Мой Telegram ID"),
]
ADMIN_COMMANDS = PLAYER_COMMANDS + [
    ("season", "Создать сезон лиги"), ("cup", "Создать кубок"), ("club", "Выдать клуб игроку"),
    ("clubs", "Список клубов"), ("catalog", "Каталог клубов FC"), ("calendar", "Сгенерировать календарь"),
    ("tour", "Открыть тур"), ("pairs", "Пары тура"), ("editpair", "Править пару"),
    ("judge", "Выдать/снять судью"), ("disputes", "Спорные матчи"), ("resolve", "Решить спор"),
    ("final", "Итоги сезона"), ("promo", "Создать промокод"), ("paid", "Закрыть долг"),
    ("scan", "Импорт Challenge Place"), ("admin", "Выдать/снять админа мини-аппа"),
]


async def setup_command_menu(bot) -> None:
    from telegram import BotCommand, BotCommandScopeChat
    await bot.set_my_commands([BotCommand(n, d) for n, d in PLAYER_COMMANDS])
    for aid in config.ADMIN_IDS:
        try:
            await bot.set_my_commands([BotCommand(n, d) for n, d in ADMIN_COMMANDS],
                                      scope=BotCommandScopeChat(aid))
        except Exception as e:  # root ещё не писал боту — чата нет
            log.warning("меню команд root %s: %s", aid, e)


# ===== 🐞 Связь (план 05 раздел 6 — единственная фича оригинала, у нас тоже будет) =====

async def contact_start(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Опиши проблему одним сообщением — перешлю админам.")
    return CONTACT


async def contact_forward(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    msg = update.message
    admins = set(config.ADMIN_IDS)
    c = appdb.db()
    for r in c.execute("SELECT telegram_id FROM users WHERE is_admin=1").fetchall():
        admins.add(r["telegram_id"])
    c.close()
    for aid in admins:
        try:
            await context.bot.send_message(
                aid,
                f"🐞 Обращение от {update.effective_user.first_name} "
                f"(id {update.effective_user.id}, @{update.effective_user.username}):",
            )
            await msg.copy(chat_id=aid)
        except Exception:
            log.warning("не смог доставить обращение админу %s", aid)
    await msg.reply_text("Передал админам. Ответ придёт сюда.", reply_markup=core.menu_keyboard())
    return ConversationHandler.END


async def cancel_contact(update: Update, context: ContextTypes.DEFAULT_TYPE) -> int:
    await update.message.reply_text("Отменено.", reply_markup=core.menu_keyboard())
    return ConversationHandler.END


# ===== error handler =====

async def on_error(update: object, context: ContextTypes.DEFAULT_TYPE) -> None:
    log.error("ошибка при апдейте: %s", context.error, exc_info=context.error)
    if isinstance(update, Update) and update.effective_message:
        try:
            await update.effective_message.reply_text("⚠️ Внутренняя ошибка. Уже логируется.")
        except Exception:
            pass


async def post_init(app: Application) -> None:
    appdb.init_db()
    await start_site()
    # tour_rotate (план 05): вышел дедлайн тура → lock, открыть следующий (каждый час)
    app.job_queue.run_repeating(job_tour_rotate, interval=3600, first=30)
    # напоминания о долгах 3×/день по МСК (план 05/09): 08:00, 12:00, 18:00
    from zoneinfo import ZoneInfo
    msk = ZoneInfo(config.TZ)
    from datetime import time as dtime
    for hh in (8, 12, 18):
        app.job_queue.run_daily(job_debt_reminders, time=dtime(hh, 0, tzinfo=msk))
    # deadline_check (план 05): DM о близком дедлайне тура — каждые 15 мин
    app.job_queue.run_repeating(job_deadline_check, interval=900, first=60)
    # daily_backup (план 05): копия БД админам — раз в сутки в 04:00 МСК
    app.job_queue.run_daily(job_daily_backup, time=dtime(4, 0, tzinfo=msk))
    # расчёт купонов → ЛС (формат как у оригинала), выключается bot_settings.notify_bets_dm=0
    app.job_queue.run_repeating(job_push_bet_results, interval=30, first=15)
    try:
        await setup_command_menu(app.bot)
    except Exception as e:
        log.warning("не удалось выставить меню команд: %s", e)
    if not config.ADMIN_IDS:
        log.warning("ADMIN_IDS пуст — root нет. Узнай ID командой /myid и впиши в bot/.env")
    url = core.webapp_url()
    if url:
        from telegram import MenuButtonWebApp, WebAppInfo
        await app.bot.set_chat_menu_button(menu_button=MenuButtonWebApp("Мини-апп", WebAppInfo(url)))
    log.info("мини-апп поднят, бот готов")


async def job_push_bet_results(context: ContextTypes.DEFAULT_TYPE) -> None:
    import settings as appsettings
    c = appdb.db()
    try:
        if appsettings.setting_int("notify_bets_dm", 1) != 1:
            # выключено — помечаем, чтобы при включении не прилетела пачка старых
            c.execute("UPDATE notifications SET tg_sent=1 WHERE kind='bet' AND tg_sent=0")
            c.commit()
            return
        rows = c.execute(
            "SELECT n.id, n.text, u.telegram_id FROM notifications n JOIN users u ON u.id=n.user_id "
            "WHERE n.kind='bet' AND n.tg_sent=0 ORDER BY n.id LIMIT 30").fetchall()
        for r in rows:
            try:
                await context.bot.send_message(r["telegram_id"], r["text"])
            except Exception as e:  # бот заблокирован / нет чата — не ретраим бесконечно
                log.warning("[bet_dm] не доставлено %s: %s", r["telegram_id"], e)
            c.execute("UPDATE notifications SET tg_sent=1 WHERE id=?", (r["id"],))
            c.commit()
    finally:
        c.close()


async def job_tour_rotate(context: ContextTypes.DEFAULT_TYPE) -> None:
    import league
    import results
    for tid, opened, closed in league.rotate_tours():
        log.info("[tour_rotate] турнир %s: закрыт тур %s, открыт %s", tid, closed, opened)
        # неигранные матчи закрытого тура: штраф-долг обоим клубам (решение 09)
        for club_name, fine in results.fine_unplayed(tid, closed):
            log.info("[tour_rotate] штраф %s: %s", club_name, fine)


async def job_debt_reminders(context: ContextTypes.DEFAULT_TYPE) -> None:
    await hd.job_debt_reminders(context)


async def job_deadline_check(context: ContextTypes.DEFAULT_TYPE) -> None:
    """DM владельцам клубов с несыгранными матчами: дедлайн тура через <24 ч."""
    import logging
    import shutil
    from datetime import datetime, timedelta
    log = logging.getLogger("bot.deadline")
    now = datetime.utcnow()
    c = appdb.db()
    soon = (now + timedelta(hours=24)).isoformat()
    tours = c.execute(
        "SELECT * FROM tours WHERE status='open' AND deadline IS NOT NULL AND deadline<=?", (soon,)
    ).fetchall()
    notified = 0
    for t in tours:
        kind = f"deadline_{t['tour_number']}"
        already = c.execute(
            "SELECT 1 FROM reminder_log WHERE tournament_id=? AND kind=? AND date=?",
            (t["tournament_id"], kind, now.strftime('%Y-%m-%d')),
        ).fetchone()
        if already:
            continue
        c.execute(
            "INSERT OR IGNORE INTO reminder_log (tournament_id, kind, date) VALUES (?,?,?)",
            (t["tournament_id"], kind, now.strftime('%Y-%m-%d')),
        )
        c.commit()
        rows = c.execute(
            "SELECT m.*, p.telegram_id FROM matches m "
            "LEFT JOIN club_players cp ON cp.club_id IN (m.home_club_id, m.away_club_id) "
            "LEFT JOIN players p ON p.id=cp.player_id "
            "WHERE m.tournament_id=? AND m.tour_number=? AND m.status='pending'",
            (t["tournament_id"], t["tour_number"]),
        ).fetchall()
        seen = set()
        for r in rows:
            tg = r["telegram_id"]
            if not tg or tg in seen:
                continue
            seen.add(tg)
            try:
                left = (datetime.fromisoformat(t["deadline"]) - now)
                hours = max(1, round(left.total_seconds() / 3600))
                await context.bot.send_message(
                    tg, f"⏰ Дедлайн тура {t['tour_number']} — через ~{hours} ч. "
                        f"Не сыгранный матч: кинь скрин в 📨 Репорт.")
                notified += 1
            except Exception:
                pass
    c.close()
    if notified:
        log.info("[deadline] уведомлений: %s", notified)


async def job_daily_backup(context: ContextTypes.DEFAULT_TYPE) -> None:
    """Копия БД в backups/ + ЛС root (план 05)."""
    import logging
    import shutil
    from datetime import datetime
    log = logging.getLogger("bot.backup")
    src = config.DB_PATH
    import os
    if not os.path.exists(src):
        return
    bdir = Path(__file__).resolve().parent.parent / "backups"
    bdir.mkdir(exist_ok=True)
    dst = bdir / f"logovo-{datetime.now().strftime('%Y-%m-%d')}.db"
    shutil.copy2(src, dst)
    for aid in config.ADMIN_IDS:
        try:
            await context.bot.send_message(aid, f"💾 Бэкап готов: {dst.name} ({dst.stat().st_size // 1024} КБ)")
        except Exception:
            pass
    log.info("[backup] %s", dst)


def main() -> None:
    if not config.BOT_TOKEN:
        raise SystemExit("BOT_TOKEN не задан (.env / env)")
    app = (
        Application.builder().token(config.BOT_TOKEN).post_init(post_init).build()
    )

    app.add_handler(CommandHandler("start", cmd_start))
    app.add_handler(CommandHandler("nick", cmd_nick))
    app.add_handler(CommandHandler("myid", cmd_myid))

    # турнирное ядро (блок 4) + результаты/OCR (блок 5) + долги/скан (блок 9)
    for kind, pat, fn in (*ht.HANDLERS, *hr.HANDLERS, *hd.HANDLERS, *ha.HANDLERS):
        if kind == "command":
            app.add_handler(CommandHandler(pat, fn))
        elif kind == "callback":
            app.add_handler(CallbackQueryHandler(fn, pattern=pat))
        elif kind == "photo":
            # скрин фото или файлом (без сжатия — OCR читает лучше)
            app.add_handler(MessageHandler(filters.PHOTO | filters.Document.IMAGE, fn))
        else:
            app.add_handler(MessageHandler(filters.Regex(f"^{re.escape(pat)}$"), fn))

    contact_conv = ConversationHandler(
        entry_points=[MessageHandler(filters.Regex("^🐞 Связь$"), contact_start)],
        states={CONTACT: [MessageHandler(filters.TEXT & ~filters.COMMAND, contact_forward)]},
        fallbacks=[CommandHandler("cancel", cancel_contact)],
        per_message=False,
    )
    app.add_handler(contact_conv)

    # прочий текст (ники, кнопки-заготовки)
    app.add_handler(MessageHandler(filters.TEXT & ~filters.COMMAND, on_text))

    app.add_error_handler(on_error)
    log.info("[бот] polling запущен")
    app.run_polling(drop_pending_updates=True)


if __name__ == "__main__":
    main()
