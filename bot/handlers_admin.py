"""Админка (блок 10): root-команды бота + API-эндпоинты в routes.py.

Root-команды: /promo, /admin (toggle users.is_admin), /judge и /club — в блоках 4.
Бан = заморозка с причиной, баланс цел (решение 09). Void = возврат + причина.
"""
import logging
from datetime import date

from telegram import Update
from telegram.ext import ContextTypes

import core
import db as appdb

log = logging.getLogger("bot.admin")


def _resolve_player(query: str) -> dict | None:
    q = query.strip().lstrip("@")
    c = appdb.db()
    if q.isdigit():
        row = c.execute("SELECT * FROM players WHERE telegram_id=?", (int(q),)).fetchone()
    else:
        row = c.execute("SELECT * FROM players WHERE username=?", (q,)).fetchone()
    c.close()
    return dict(row) if row else None


async def cmd_promo(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/promo <КОД> <сумма> [активаций=0(∞)] [YYYY-MM-DD]"""
    if not core.is_root(update.effective_user.id):
        await update.message.reply_text("⛔ Только root.")
        return
    if len(context.args or []) < 2:
        await update.message.reply_text("Формат: /promo <КОД> <сумма> [активаций] [дедлайн YYYY-MM-DD]")
        return
    code = context.args[0].upper()
    try:
        amount = int(context.args[1])
        max_act = int(context.args[2]) if len(context.args) > 2 else 0
        deadline = context.args[3] if len(context.args) > 3 else None
        if deadline:
            date.fromisoformat(deadline)
    except ValueError:
        await update.message.reply_text("Сумма/активации — числа, дедлайн — YYYY-MM-DD.")
        return
    c = appdb.db()
    # upsert с сохранением id: активации привязаны к code_id, повторно не активировать
    c.execute(
        "INSERT INTO promo_codes (code, amount, max_activations, deadline, is_active, created_by) "
        "VALUES (?,?,?,?,1,?) ON CONFLICT(code) DO UPDATE SET amount=excluded.amount, "
        "max_activations=excluded.max_activations, deadline=excluded.deadline, is_active=1",
        (code, amount, max_act, deadline, update.effective_user.id),
    )
    c.commit()
    c.close()
    core.audit(None, update.effective_user.id, "create_promo", f"{code} amount={amount} max={max_act} until={deadline}")
    await update.message.reply_text(
        f"🎟 Промокод {code}: +{amount} дыма"
        + (f", активаций {max_act}" if max_act else ", без лимита")
        + (f", до {deadline}" if deadline else ""))


async def cmd_app_admin(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/admin <@user|id> — выдать/снять users.is_admin (toggle)."""
    if not core.is_root(update.effective_user.id):
        await update.message.reply_text("⛔ Только root.")
        return
    if not context.args:
        await update.message.reply_text("Формат: /admin <@user|id>")
        return
    player = _resolve_player(context.args[0])
    if not player:
        await update.message.reply_text("Игрок не найден (пусть нажмёт /start).")
        return
    c = appdb.db()
    row = c.execute("SELECT is_admin FROM users WHERE telegram_id=?", (player["telegram_id"],)).fetchone()
    if row:
        new_val = 0 if row["is_admin"] else 1
        c.execute("UPDATE users SET is_admin=? WHERE telegram_id=?", (new_val, player["telegram_id"]))
        msg = "выдан" if new_val else "снят"
    else:
        c.execute("INSERT INTO users (telegram_id, username, is_admin) VALUES (?,?,1)",
                  (player["telegram_id"], player["username"]))
        msg = "выдан"
    c.commit()
    c.close()
    core.audit(None, update.effective_user.id, f"app_admin_{msg}", str(player["telegram_id"]))
    await update.message.reply_text(f"✅ Админ мини-аппа: {msg}.")


HANDLERS = [
    ("command", "promo", cmd_promo),
    ("command", "admin", cmd_app_admin),
]


# ===== /final — итоги сезона (решение 09: применяет админ, не фоновый job) =====

async def cmd_season_final(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    """/final <турнир_id> → превью; подтверждение кнопкой."""
    import league
    from telegram import InlineKeyboardButton, InlineKeyboardMarkup
    if not core.is_root(update.effective_user.id):
        await update.message.reply_text("⛔ Только root.")
        return
    if not context.args or not context.args[0].isdigit():
        await update.message.reply_text("Формат: /final <турнир_id> — сначала покажу превью.")
        return
    tid = int(context.args[0])
    preview = league.season_final_preview(tid)
    if preview.get("error"):
        await update.message.reply_text(preview["error"])
        return
    lines = [f"🏁 Итоги «{preview['tournament']}»:"]

    def club_name(cname):
        return cname

    for row in preview["rows"]:
        if row.get("prize") or row.get("move"):
            parts = []
            if row.get("prize"):
                parts.append(f"призовое {row['prize']:,} ₼".replace(",", " "))
            if row.get("move"):
                parts.append(row["move"])
            lines.append(f"• {row['club']} ({row['division']}, {row['position']} мес.) — " + "; ".join(parts))
    kb = InlineKeyboardMarkup([[InlineKeyboardButton("✅ Подтвердить финализацию",
                                                    callback_data=f"finconf:{tid}")]])
    await update.message.reply_text("\n".join(lines) or "Ничего не изменится.", reply_markup=kb)


async def cb_season_final_confirm(update: Update, context: ContextTypes.DEFAULT_TYPE) -> None:
    import league
    if not core.is_root(update.effective_user.id):
        await update.callback_query.answer("Только root.")
        return
    await update.callback_query.answer()
    _, tid = update.callback_query.data.split(":")
    r = league.finalize_season(int(tid), update.effective_user.id)
    if r.get("error"):
        await update.callback_query.edit_message_text(r["error"])
        return
    lines = [f"🏁 Сезон «{r['tournament']}» закрыт."]
    lines += [f"🏆 {p['club']} — призовое {p['prize']:,} ₼ ({p['note']})".replace(",", " ") for p in r["prize_rows"]]
    lines += [f"{m['move']} — {m['club']}" for m in r["moves"]]
    lines += [f"🥇 {w['club']} — победитель {w['cup']}: {w['prize']:,} ₼".replace(",", " ") for w in r["cup_winners"]]
    lines.append("Elo нового сезона стартует с 1000 (создай: /season).")
    await update.callback_query.edit_message_text("\n".join(lines))
    core.audit(int(tid), update.effective_user.id, "season_finalized", "bot")


HANDLERS = HANDLERS + [
    ("command", "final", cmd_season_final),
    ("callback", r"^finconf:\d+$", cb_season_final_confirm),
]
