"""Текст уведомления о расчёте купона (формат как у оригинала «Логово»).

Пример:
    😔 Ставка #592 (Ординар) не сыграла
    ⚽ Атлетик Бильбао 3:2 Бешикташ
       ❌ Победит Бешикташ (П2) · @3.50

    💵 Ставка: 100 🚬 × 3.50
    🎯 Возможный выигрыш был: 350 🚬
    👛 Баланс: 5 160 🚬
"""

COIN = "🚬"

_RESULT_ICON = {"won": "✅", "lost": "❌", "void": "↩️", "pending": "⏳"}


def fmt_num(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def fmt_odds(o) -> str:
    return f"{float(o or 0):.2f}"


def market_text(code: str, home: str, away: str, label: str | None = None) -> str:
    """Человеческое описание рынка: «Победит Ницца (П1)»."""
    texts = {
        "1x2_p1": f"Победит {home} (П1)",
        "1x2_x": "Ничья (Х)",
        "1x2_p2": f"Победит {away} (П2)",
        "tb25": "Тотал больше 2.5",
        "tm25": "Тотал меньше 2.5",
        "btts_yes": "Обе забьют — да",
        "btts_no": "Обе забьют — нет",
        "itb_h15": f"ИТ {home} больше 1.5",
        "itb_a15": f"ИТ {away} больше 1.5",
        "ah_h15": f"Фора {home} −1.5",
        "ah_a15": f"Фора {away} +1.5",
        "tie_2_0": f"Серия: {home} 2:0",
        "tie_2_1": f"Серия: {home} 2:1",
        "tie_1_2": f"Серия: {away} 2:1",
        "tie_0_2": f"Серия: {away} 2:0",
    }
    return texts.get(code) or label or code


def _club_name(c, club_id) -> str:
    row = c.execute("SELECT name FROM clubs WHERE id=?", (club_id,)).fetchone() if club_id else None
    return row["name"] if row else "—"


def leg_lines(c, leg: dict) -> list[str]:
    m = c.execute("SELECT * FROM matches WHERE id=?", (leg["match_id"],)).fetchone()
    if not m:
        return [f"⚽ Матч #{leg['match_id']}", f"   {_RESULT_ICON.get(leg['result'], '•')} "
                f"{leg['market_code']} · @{fmt_odds(leg['odds'])}"]
    home, away = _club_name(c, m["home_club_id"]), _club_name(c, m["away_club_id"])
    if m["score1"] is not None and m["status"] == "confirmed":
        score = f"{m['score1']}:{m['score2']}"
        if m["pens1"] is not None:
            score += f" (пен. {m['pens1']}:{m['pens2']})"
    else:
        score = "— : —"
    mk = c.execute("SELECT label FROM markets WHERE match_id=? AND code=?",
                   (leg["match_id"], leg["market_code"])).fetchone()
    what = market_text(leg["market_code"], home, away, mk["label"] if mk else None)
    icon = _RESULT_ICON.get(leg["result"], "•")
    return [f"⚽ {home} {score} {away}", f"   {icon} {what} · @{fmt_odds(leg['odds'])}"]


def settlement_text(c, bet: dict, legs: list[dict], status: str, payout: int) -> str:
    """Полный текст уведомления. c — открытое соединение (баланс уже обновлён)."""
    kind = "Экспресс" if len(legs) > 1 else "Ординар"
    head = {
        "won": f"🎉 Ставка #{bet['id']} ({kind}) сыграла!",
        "lost": f"😔 Ставка #{bet['id']} ({kind}) не сыграла",
        "void": f"↩️ Ставка #{bet['id']} ({kind}) — возврат",
    }[status]
    lines = [head]
    for leg in legs:
        lines += leg_lines(c, leg)
    lines.append("")
    lines.append(f"💵 Ставка: {fmt_num(bet['amount'])} {COIN} × {fmt_odds(bet['total_odds'])}")
    if status == "won":
        lines.append(f"🏆 Выигрыш: {fmt_num(payout)} {COIN}")
    elif status == "lost":
        lines.append(f"🎯 Возможный выигрыш был: {fmt_num(bet['potential_win'])} {COIN}")
    else:
        lines.append(f"💸 Возвращено: {fmt_num(payout)} {COIN}")
    bal = c.execute("SELECT balance FROM users WHERE id=?", (bet["user_id"],)).fetchone()
    if bal:
        lines.append(f"👛 Баланс: {fmt_num(bal['balance'])} {COIN}")
    if status == "lost":
        lines += ["", "Следующий купон зайдёт! 💪"]
    return "\n".join(lines)
