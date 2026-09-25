"""Трансферы (план 10 блок 8 / решения 09 / план 08 B2–B3).

- Маркетплейс: лот фикс/аукцион; сделка авто при деньгах и сумме до порога
  (старт 2М, bot_settings); выше — ждёт одобрения судьи (анти-колллюзия).
- Аукцион: шаг ставки (auction_min_step_pct), таймер (auction_hours), анти-снайпинг
  (auction_snipe_minutes), выкуп закрывает сразу. Ставка = эскроу: сумма списана из
  бюджета клуба-лидера, перебили — вернулась. Закрытие — close_expired_auctions().
- Комиссия 5% с каждой продажи сгорает из экономики.
- Свободные агенты: цена = рейтинг² × K (K в админке, публичная математика).
- Обмены: деньги / игрок+деньги / игрок↔игрок; вторая сторона подтверждает,
  дальше тот же порог/судья.
- Состав клуба — без лимита карточек.
"""
import logging
import math
from datetime import datetime, timedelta, timezone

import config
import db as appdb
import settings as appsettings

log = logging.getLogger("bot.transfers")

TS_FMT = "%Y-%m-%d %H:%M:%S"
ACTIVE_LOT = ("open", "closing", "needs_judge")


class TransferError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


# ===== служебные =====

def _m(n) -> str:
    return f"{int(n or 0):,}".replace(",", " ")


def _now(now: datetime | None = None) -> datetime:
    return now or datetime.now(timezone.utc).replace(tzinfo=None)


def _ts(dt: datetime | None) -> str | None:
    return dt.strftime(TS_FMT) if dt else None


def _parse_ts(s) -> datetime | None:
    if not s:
        return None
    try:
        return datetime.strptime(str(s)[:19].replace("T", " "), TS_FMT)
    except ValueError:
        return None


def _notify(c, telegram_id: int | None, text: str) -> None:
    if not telegram_id:
        return
    c.execute("INSERT INTO notifications (user_id, text, kind) SELECT id, ?, 'app' FROM users WHERE telegram_id=?",
              (text, telegram_id))


def _dm(c, telegram_id: int | None, text: str) -> None:
    """В очередь ЛС бота (трансфер — «турнирное» событие, решение 09)."""
    if telegram_id:
        c.execute("INSERT INTO transfer_dm_queue (telegram_id, text) VALUES (?,?)", (telegram_id, text))


def _tell(c, telegram_id: int | None, text: str) -> None:
    _notify(c, telegram_id, text)
    _dm(c, telegram_id, text)


def free_agent_price(rating: int) -> int:
    k = appsettings.setting_int("free_agent_k", 1000)
    return int(rating) * int(rating) * k


def commission(amount: int) -> int:
    pct = appsettings.setting_float("transfer_commission_pct", 5)
    return int(amount * pct / 100)


def threshold() -> int:
    return appsettings.setting_int("transfer_deal_threshold", 2_000_000)


def auction_step_pct() -> float:
    return appsettings.setting_float("auction_min_step_pct", 5)


def auction_hours() -> int:
    return max(1, appsettings.setting_int("auction_hours", 24))


def snipe_minutes() -> int:
    return max(0, appsettings.setting_int("auction_snipe_minutes", 10))


def min_next_bid(lot, step_pct: float | None = None) -> int:
    """Первая ставка = стартовая цена, дальше +шаг% от текущей (минимум +1)."""
    top = lot["top_bid"]
    if not top:
        return int(lot["price"])
    pct = auction_step_pct() if step_pct is None else step_pct
    return max(int(top) + 1, math.ceil(int(top) * (1 + pct / 100)))


def club_owner_tg(c, club_id: int | None) -> int | None:
    if not club_id:
        return None
    row = c.execute(
        "SELECT p.telegram_id FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=?",
        (club_id,),
    ).fetchone()
    return row["telegram_id"] if row else None


def _club_tournament(c, club_id: int | None) -> int | None:
    if not club_id:
        return None
    row = c.execute("SELECT d.tournament_id FROM clubs cl JOIN divisions d ON d.id=cl.division_id WHERE cl.id=?",
                    (club_id,)).fetchone()
    return row["tournament_id"] if row else None


def _judge_tgs(c, tournament_id: int | None) -> set[int]:
    tgs = set(config.ADMIN_IDS)
    if tournament_id:
        tgs |= {r["telegram_id"] for r in c.execute(
            "SELECT telegram_id FROM tournament_admins WHERE tournament_id=?", (tournament_id,)).fetchall()}
    return tgs


def _call_judges(c, tournament_id: int | None, text: str, skip: tuple = ()) -> None:
    for tg in _judge_tgs(c, tournament_id):
        if tg not in skip:
            _dm(c, tg, text)


def _move_card(c, card_id: int, to_club: int | None) -> None:
    c.execute("UPDATE club_cards SET club_id=? WHERE id=?", (to_club, card_id))


def _money(c, club_id: int, delta: int, reason: str) -> None:
    c.execute("UPDATE clubs SET budget=budget+? WHERE id=?", (delta, club_id))


def _budget(c, club_id: int) -> int:
    row = c.execute("SELECT budget FROM clubs WHERE id=?", (club_id,)).fetchone()
    return row["budget"] if row else 0


def _on_market(c, card_id: int | None) -> bool:
    if not card_id:
        return False
    return c.execute("SELECT 1 FROM transfer_lots WHERE card_id=? AND status IN ('open','closing','needs_judge')",
                     (card_id,)).fetchone() is not None


def _positive_int(value, field: str) -> int:
    """Цена из запроса → int > 0, иначе внятная TransferError (не 500)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise TransferError("BAD_PRICE", f"{field}: нужно целое число")
    if v <= 0:
        raise TransferError("BAD_PRICE", f"{field}: должна быть больше нуля")
    return v


def _release_bid(c, bid, status: str = "refunded") -> None:
    """Снять блокировку: деньги ставки возвращаются в бюджет клуба."""
    c.execute("UPDATE transfer_bids SET status=? WHERE id=? AND status='held'", (status, bid["id"]))
    _money(c, bid["club_id"], int(bid["amount"]), "возврат ставки")


def _held_bid(c, lot_id: int):
    return c.execute("SELECT * FROM transfer_bids WHERE lot_id=? AND status='held' ORDER BY id DESC",
                     (lot_id,)).fetchone()


# ===== свободные агенты =====

def sign_free_agent(club_id: int, card_id: int, actor_tg: int | None = None) -> dict:
    """Купля свободного агента: цена = рейтинг² × K, авто при деньгах."""
    c = appdb.db()
    card = c.execute("SELECT * FROM club_cards WHERE id=?", (card_id,)).fetchone()
    club = c.execute("SELECT * FROM clubs WHERE id=?", (club_id,)).fetchone()
    if not card or card["club_id"] is not None:
        c.close()
        raise TransferError("NOT_FREE", "Карточка не свободный агент")
    if not club:
        c.close()
        raise TransferError("NO_CLUB", "Клуб не найден")
    price = free_agent_price(card["rating"])
    if club["budget"] < price:
        c.close()
        raise TransferError("NO_FUNDS", f"Не хватает бюджета: цена {_m(price)} ₼")
    _move_card(c, card_id, club_id)
    _money(c, club_id, -price, f"свободный агент {card['name']}")
    tid = c.insert_returning_id(
        "INSERT INTO transfers (tournament_id, from_club_id, to_club_id, card_id, player_name, amount, status, created_by) "
        "VALUES (NULL, NULL, ?, ?, ?, ?, 'approved', ?)",
        (club_id, card_id, card["name"], price, actor_tg),
    )
    c.execute(
        "INSERT INTO tournament_audit_log (tournament_id, actor_telegram_id, action, details) VALUES (NULL, ?, 'free_agent', ?)",
        (actor_tg, f"{card['name']} → клуб {club_id} за {price}"),
    )
    _notify(c, club_owner_tg(c, club_id), f"⚽ Свободный агент {card['name']} подписан за {_m(price)} ₼")
    c.commit()
    budget = _budget(c, club_id)
    c.close()
    return {"transfer_id": tid, "price": price, "budget": budget}


# ===== лоты: фикс и аукцион =====

def create_lot(club_id: int, card_id: int, kind: str, price: int,
               buyout_price: int | None = None, actor_tg: int | None = None,
               hours: int | None = None, now: datetime | None = None) -> dict:
    """Выставить карточку на маркетплейс (фикс или аукцион со стартовой ценой и выкупом)."""
    if kind not in ("fix", "auction"):
        raise TransferError("BAD_KIND", "kind = fix|auction")
    price = _positive_int(price, "Цена")
    if buyout_price in (None, "", 0, "0"):
        buyout_price = None
    else:
        buyout_price = _positive_int(buyout_price, "Цена выкупа")
        if buyout_price < price:
            raise TransferError("BAD_PRICE", "Цена выкупа не может быть ниже стартовой")
    ends_at = None
    if kind == "auction":
        h = auction_hours() if hours in (None, "", 0, "0") else _positive_int(hours, "Длительность")
        if h > 168:
            raise TransferError("BAD_HOURS", "Аукцион — не дольше 7 дней")
        ends_at = _ts(_now(now) + timedelta(hours=h))
    else:
        buyout_price = None  # у фикса одна цена
    c = appdb.db()
    card = c.execute("SELECT * FROM club_cards WHERE id=?", (card_id,)).fetchone()
    if not card or card["club_id"] != club_id:
        c.close()
        raise TransferError("NOT_YOURS", "Карточка не в твоём клубе")
    if _on_market(c, card_id):
        c.close()
        raise TransferError("DUP", "Карточка уже на рынке")
    lot_id = c.insert_returning_id(
        "INSERT INTO transfer_lots (seller_club_id, card_id, kind, price, buyout_price, status, ends_at, bids_count) "
        "VALUES (?,?,?,?,?, 'open', ?, 0)",
        (club_id, card_id, kind, int(price), buyout_price, ends_at),
    )
    if kind == "auction":
        text = f"🔨 {card['name']} выставлен на аукцион: старт {_m(price)} ₼" + (
            f", выкуп {_m(buyout_price)} ₼" if buyout_price else "")
    else:
        text = f"📋 {card['name']} выставлен на рынок за {_m(price)} ₼"
    _notify(c, club_owner_tg(c, club_id), text)
    c.commit()
    c.close()
    return {"lot_id": lot_id, "ends_at": ends_at}


def buy_lot(buyer_club_id: int, lot_id: int, actor_tg: int | None = None) -> dict:
    """Покупка лота: авто при деньгах и сумме ≤ порога; выше — needs_judge.
    Для аукциона «купить» = выкуп (через ставку по цене выкупа)."""
    c = appdb.db()
    lot = c.execute("SELECT * FROM transfer_lots WHERE id=?", (lot_id,)).fetchone()
    if not lot or lot["status"] != "open":
        c.close()
        raise TransferError("NOT_OPEN", "Лот недоступен")
    if lot["seller_club_id"] == buyer_club_id:
        c.close()
        raise TransferError("SELF", "Нельзя купить свой лот")
    if lot["kind"] == "auction":
        c.close()
        if not lot["buyout_price"]:
            raise TransferError("AUCTION", "Это аукцион без выкупа — делай ставку")
        return place_bid(buyer_club_id, lot_id, lot["buyout_price"], actor_tg)
    buyer = c.execute("SELECT * FROM clubs WHERE id=?", (buyer_club_id,)).fetchone()
    card = c.execute("SELECT * FROM club_cards WHERE id=?", (lot["card_id"],)).fetchone()
    if not buyer or not card:
        c.close()
        raise TransferError("NO_DATA", "Лот повреждён")
    if card["club_id"] != lot["seller_club_id"]:
        c.close()
        raise TransferError("MOVED", "Карточка уже не у продавца")
    price = lot["price"]
    if buyer["budget"] < price:
        c.close()
        raise TransferError("NO_FUNDS", f"Не хватает бюджета: {_m(price)} ₼")

    if price > threshold():
        # крупная сделка — к судье (анти-колллюзия, решение 09); деньги спишутся при одобрении
        c.execute("UPDATE transfer_lots SET status='needs_judge', buyer_club_id=? WHERE id=?",
                  (buyer_club_id, lot_id))
        trn = _club_tournament(c, lot["seller_club_id"]) or _club_tournament(c, buyer_club_id)
        tid = c.insert_returning_id(
            "INSERT INTO transfers (tournament_id, from_club_id, to_club_id, card_id, player_name, amount, status, "
            "created_by, lot_id) VALUES (?, ?, ?, ?, ?, ?, 'needs_judge', ?, ?)",
            (trn, lot["seller_club_id"], buyer_club_id, card["id"], card["name"], price, actor_tg, lot_id),
        )
        _notify(c, club_owner_tg(c, buyer_club_id), f"⏳ Сделка {card['name']} на {_m(price)} ₼ ушла судье на одобрение")
        _call_judges(c, trn, f"⚖️ Сделка ждёт одобрения: {card['name']} за {_m(price)} ₼ (мини-апп → Рынок → Судья)")
        c.commit()
        c.close()
        return {"status": "needs_judge", "price": price, "transfer_id": tid}

    return _execute_deal(c, lot["seller_club_id"], buyer_club_id, card, price, actor_tg, lot_id=lot_id)


def _execute_deal(c, seller_club: int, buyer_club: int, card, price: int, actor_tg: int | None,
                  lot_id: int | None = None, prepaid: bool = False, transfer_id: int | None = None) -> dict:
    """Исполнение сделки: деньги, комиссия сгорает, карточка переезжает.
    prepaid — деньги покупателя уже в эскроу (выигранная ставка)."""
    comm = commission(price)
    _move_card(c, card["id"], buyer_club)
    if not prepaid:
        _money(c, buyer_club, -price, f"покупка {card['name']}")
    _money(c, seller_club, price - comm, f"продажа {card['name']} (комиссия {comm})")
    if transfer_id:
        c.execute("UPDATE transfers SET status='approved', decided_by=? WHERE id=?", (actor_tg, transfer_id))
        tid = transfer_id
    else:
        tid = c.insert_returning_id(
            "INSERT INTO transfers (from_club_id, to_club_id, card_id, player_name, amount, status, decided_by, lot_id) "
            "VALUES (?, ?, ?, ?, ?, 'approved', ?, ?)",
            (seller_club, buyer_club, card["id"], card["name"], price, actor_tg, lot_id),
        )
    if lot_id:
        c.execute("UPDATE transfer_lots SET status='sold', buyer_club_id=? WHERE id=?", (buyer_club, lot_id))
    c.execute(
        "INSERT INTO tournament_audit_log (actor_telegram_id, action, details) VALUES (?, 'transfer_deal', ?)",
        (actor_tg, f"{card['name']} {seller_club}→{buyer_club} за {price}, комиссия {comm}"),
    )
    _notify(c, club_owner_tg(c, buyer_club), f"✅ Куплен {card['name']} за {_m(price)} ₼")
    _notify(c, club_owner_tg(c, seller_club), f"✅ Продан {card['name']} за {_m(price - comm)} ₼ (комиссия {_m(comm)} сгорела)")
    c.commit()
    budgets = {cid: _budget(c, cid) for cid in (seller_club, buyer_club)}
    c.close()
    return {"status": "approved", "transfer_id": tid, "price": price, "commission": comm, "budgets": budgets}


def place_bid(club_id: int, lot_id: int, amount, actor_tg: int | None = None,
              now: datetime | None = None) -> dict:
    """Ставка на аукцион. Сумма блокируется (списывается в эскроу), прошлый лидер
    получает свою ставку назад. Ставка ≥ выкупа закрывает лот сразу."""
    now = _now(now)
    amount = _positive_int(amount, "Ставка")
    step, snipe = auction_step_pct(), snipe_minutes()
    c = appdb.db()
    try:
        lot = c.execute("SELECT * FROM transfer_lots WHERE id=?", (lot_id,)).fetchone()
        if not lot or lot["kind"] != "auction":
            raise TransferError("NOT_AUCTION", "Это не аукцион")
        if lot["status"] != "open":
            raise TransferError("NOT_OPEN", "Аукцион уже закрыт")
        ends = _parse_ts(lot["ends_at"])
        if ends and ends <= now:
            raise TransferError("ENDED", "Время аукциона вышло")
        if lot["seller_club_id"] == club_id:
            raise TransferError("SELF", "Нельзя ставить на свой лот")
        club = c.execute("SELECT * FROM clubs WHERE id=?", (club_id,)).fetchone()
        card = c.execute("SELECT * FROM club_cards WHERE id=?", (lot["card_id"],)).fetchone()
        if not club:
            raise TransferError("NO_CLUB", "Клуб не найден")
        if not card or card["club_id"] != lot["seller_club_id"]:
            raise TransferError("MOVED", "Карточка уже не у продавца")
        buyout = lot["buyout_price"]
        is_buyout = bool(buyout) and amount >= int(buyout)
        if is_buyout:
            amount = int(buyout)
        else:
            need = min_next_bid(lot, step)
            if amount < need:
                raise TransferError("LOW_BID", f"Минимальная ставка — {_m(need)} ₼ (шаг {step:g}%)")
        prev_top, prev_club = int(lot["top_bid"] or 0), lot["top_club_id"]
        own_held = prev_top if prev_club == club_id else 0
        free = int(club["budget"]) + own_held
        if free < amount:
            raise TransferError("NO_FUNDS", f"Не хватает бюджета: нужно {_m(amount)} ₼, свободно {_m(free)} ₼")

        new_ends, extended = ends, False
        if ends and not is_buyout and snipe and ends - now <= timedelta(minutes=snipe):
            new_ends, extended = ends + timedelta(minutes=snipe), True
        # оптимистичная блокировка: лот не перебили между чтением и записью
        cur = c.execute(
            "UPDATE transfer_lots SET top_bid=?, top_club_id=?, bids_count=COALESCE(bids_count,0)+1, ends_at=? "
            "WHERE id=? AND status='open' AND COALESCE(top_bid,0)=?",
            (amount, club_id, _ts(new_ends), lot_id, prev_top),
        )
        if cur.rowcount != 1:
            raise TransferError("RACE", "Ставку только что перебили — обнови лот")
        for b in c.execute("SELECT * FROM transfer_bids WHERE lot_id=? AND status='held'", (lot_id,)).fetchall():
            _release_bid(c, b, "outbid")
        bid_id = c.insert_returning_id(
            "INSERT INTO transfer_bids (lot_id, club_id, amount, status, created_by, created_at) VALUES (?,?,?, 'held', ?, ?)",
            (lot_id, club_id, amount, actor_tg, _ts(now)),
        )
        _money(c, club_id, -amount, f"ставка на {card['name']}")
        if prev_club and prev_club != club_id:
            _tell(c, club_owner_tg(c, prev_club),
                  f"⚠️ Твою ставку на {card['name']} перебили: {_m(amount)} ₼. {_m(prev_top)} ₼ вернулись в бюджет")
        _notify(c, club_owner_tg(c, lot["seller_club_id"]), f"🔨 Новая ставка на {card['name']}: {_m(amount)} ₼")
        c.commit()
    except Exception:
        c.rollback()
        c.close()
        raise
    c.close()

    res = {"status": "bid", "bid_id": bid_id, "amount": amount, "ends_at": _ts(new_ends),
           "extended": extended, "buyout": is_buyout}
    if is_buyout:
        fin = _finish_auction(lot_id, now, actor_tg)
        res.update({k: v for k, v in fin.items() if k != "messages"})
    c = appdb.db()
    res["budget"] = _budget(c, club_id)
    c.close()
    if not is_buyout:
        res["min_next"] = max(amount + 1, math.ceil(amount * (1 + step / 100)))
        res["seconds_left"] = max(0, int((new_ends - now).total_seconds())) if new_ends else None
    return res


def _finish_auction(lot_id: int, now: datetime, actor_tg: int | None = None) -> dict:
    """Закрыть один аукцион. Идемпотентно: второй вызов на тот же лот — skip."""
    c = appdb.db()
    cur = c.execute("UPDATE transfer_lots SET status='closing', closed_at=? WHERE id=? AND status='open'",
                    (_ts(now), lot_id))
    if cur.rowcount != 1:
        c.rollback()
        c.close()
        return {"status": "skip"}
    lot = c.execute("SELECT * FROM transfer_lots WHERE id=?", (lot_id,)).fetchone()
    card = c.execute("SELECT * FROM club_cards WHERE id=?", (lot["card_id"],)).fetchone()
    name = card["name"] if card else "карточка"
    seller = lot["seller_club_id"]
    seller_tg = club_owner_tg(c, seller)
    bid = _held_bid(c, lot_id)

    if not bid:
        c.execute("UPDATE transfer_lots SET status='expired' WHERE id=?", (lot_id,))
        _tell(c, seller_tg, f"⌛ Аукцион {name} завершён без ставок — карточка осталась в клубе")
        c.commit()
        c.close()
        return {"status": "expired"}

    buyer, amount = bid["club_id"], int(bid["amount"])
    buyer_tg = club_owner_tg(c, buyer)
    if not card or card["club_id"] != seller:
        _release_bid(c, bid)
        c.execute("UPDATE transfer_lots SET status='cancelled' WHERE id=?", (lot_id,))
        _tell(c, buyer_tg, f"↩️ Аукцион {name} отменён (карточки нет у продавца) — {_m(amount)} ₼ вернулись")
        c.commit()
        c.close()
        return {"status": "cancelled"}

    if amount > threshold():
        # эскроу остаётся до решения судьи
        trn = _club_tournament(c, seller) or _club_tournament(c, buyer)
        tid = c.insert_returning_id(
            "INSERT INTO transfers (tournament_id, from_club_id, to_club_id, card_id, player_name, amount, status, "
            "created_by, lot_id) VALUES (?, ?, ?, ?, ?, ?, 'needs_judge', ?, ?)",
            (trn, seller, buyer, card["id"], name, amount, actor_tg, lot_id),
        )
        c.execute("UPDATE transfer_lots SET status='needs_judge', buyer_club_id=? WHERE id=?", (buyer, lot_id))
        _tell(c, buyer_tg, f"🏆 Аукцион {name} выигран за {_m(amount)} ₼ — сумма выше порога, ждём судью")
        _tell(c, seller_tg, f"🏆 Аукцион {name} закрыт на {_m(amount)} ₼ — сумма выше порога, ждём судью")
        _call_judges(c, trn, f"⚖️ Аукцион ждёт одобрения: {name} за {_m(amount)} ₼ (мини-апп → Рынок → Судья)",
                     skip=(buyer_tg, seller_tg))
        c.commit()
        c.close()
        return {"status": "needs_judge", "transfer_id": tid, "price": amount}

    c.execute("UPDATE transfer_bids SET status='won' WHERE id=?", (bid["id"],))
    comm = commission(amount)
    _dm(c, buyer_tg, f"🏆 Аукцион выигран: {name} за {_m(amount)} ₼ — карточка в твоём клубе")
    _dm(c, seller_tg, f"🏆 Аукцион закрыт: {name} продан за {_m(amount)} ₼, тебе {_m(amount - comm)} ₼ (комиссия {_m(comm)})")
    return _execute_deal(c, seller, buyer, card, amount, actor_tg, lot_id=lot_id, prepaid=True)


def drain_dm_queue(limit: int = 200) -> list[tuple[int, str]]:
    """Забрать неотправленные ЛС (помечаются отправленными сразу)."""
    c = appdb.db()
    rows = c.execute("SELECT id, telegram_id, text FROM transfer_dm_queue WHERE sent=0 ORDER BY id LIMIT ?",
                     (limit,)).fetchall()
    if rows:
        c.execute("UPDATE transfer_dm_queue SET sent=1 WHERE sent=0 AND id<=?", (rows[-1]["id"],))
        c.commit()
    c.close()
    return [(r["telegram_id"], r["text"]) for r in rows]


def close_expired_auctions(now: datetime | None = None, drain: bool = True) -> list[tuple[int, str]]:
    """Закрыть все истёкшие аукционы (идемпотентно). Для job раз в минуту в bot/main.py:
    возвращает [(telegram_id, text)] для ЛС бота — вся очередь трансферов, включая
    «ставку перебили» и решения судьи. drain=False — только закрыть (ленивый вызов из API)."""
    now = _now(now)
    c = appdb.db()
    ids = [r["id"] for r in c.execute(
        "SELECT id FROM transfer_lots WHERE kind='auction' AND status='open' AND ends_at IS NOT NULL AND ends_at<=?",
        (_ts(now),)).fetchall()]
    c.close()
    for lot_id in ids:
        try:
            _finish_auction(lot_id, now)
        except Exception:
            log.exception("close auction %s", lot_id)
    return drain_dm_queue() if drain else []


# ===== обмены и предложения =====

def propose_exchange(from_club_id: int, to_club_id: int, give_card_id: int | None,
                     want_card_id: int, money: int, actor_tg: int | None = None) -> dict:
    """Обмен: деньги / игрок+деньги / игрок↔игрок. Вторая сторона подтверждает.
    give_card_id=None — чистое денежное предложение за карточку (money > 0)."""
    try:
        money = int(money or 0)
    except (TypeError, ValueError):
        raise TransferError("BAD_PRICE", "Доплата: нужно целое число")
    give_card_id = int(give_card_id) if give_card_id else None
    if not give_card_id and money <= 0:
        raise TransferError("BAD_PRICE", "Предложи деньги или свою карточку")
    c = appdb.db()
    give = c.execute("SELECT * FROM club_cards WHERE id=?", (give_card_id,)).fetchone() if give_card_id else None
    want = c.execute("SELECT * FROM club_cards WHERE id=?", (want_card_id,)).fetchone()
    if give_card_id and (not give or give["club_id"] != from_club_id):
        c.close()
        raise TransferError("NOT_YOURS", "Отдаёмая карточка не в твоём клубе")
    if not want or want["club_id"] != to_club_id:
        c.close()
        raise TransferError("NOT_THEIRS", "Запрашиваемая карточка у другого клуба")
    if from_club_id == to_club_id:
        c.close()
        raise TransferError("SELF", "Обмен внутри клуба запрещён")
    if _on_market(c, give_card_id):
        c.close()
        raise TransferError("ON_MARKET", "Твоя карточка выставлена на рынок — сначала сними лот")
    if money > 0 and _budget(c, from_club_id) < money:
        c.close()
        raise TransferError("NO_FUNDS", f"Не хватает бюджета на доплату {_m(money)} ₼")
    tid = c.insert_returning_id(
        "INSERT INTO transfers (from_club_id, to_club_id, card_id, want_card_id, player_name, amount, status, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (from_club_id, to_club_id, give_card_id, want_card_id, (give or want)["name"], money, actor_tg),
    )
    frm = c.execute("SELECT name FROM clubs WHERE id=?", (from_club_id,)).fetchone()
    if give:
        text = (f"🔄 {frm['name'] if frm else 'Клуб'} предлагает обмен: {give['name']}"
                + (f" + {_m(money)} ₼" if money > 0 else "") + f" за {want['name']}")
    else:
        text = f"💰 {frm['name'] if frm else 'Клуб'} предлагает {_m(money)} ₼ за {want['name']}"
    _tell(c, club_owner_tg(c, to_club_id), text)
    c.commit()
    c.close()
    return {"transfer_id": tid}


def accept_exchange(transfer_id: int, by_telegram_id: int) -> dict:
    """Вторая сторона подтвердила → авто при |деньгах| ≤ порога, иначе судья."""
    c = appdb.db()
    t = c.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
    if not t or t["status"] != "pending" or not t["want_card_id"]:
        c.close()
        raise TransferError("NOT_PENDING", "Предложение недействительно")
    owner = club_owner_tg(c, t["to_club_id"])
    if owner != by_telegram_id:
        c.close()
        raise TransferError("NOT_YOURS", "Подтвердить может только получатель предложения")
    give = c.execute("SELECT * FROM club_cards WHERE id=?", (t["card_id"],)).fetchone() if t["card_id"] else None
    want = c.execute("SELECT * FROM club_cards WHERE id=?", (t["want_card_id"],)).fetchone()
    _check_exchange(c, t, give, want)
    money = int(t["amount"] or 0)

    if abs(money) > threshold():
        trn = _club_tournament(c, t["from_club_id"]) or _club_tournament(c, t["to_club_id"])
        c.execute("UPDATE transfers SET status='needs_judge', tournament_id=COALESCE(tournament_id, ?) WHERE id=?",
                  (trn, transfer_id))
        _tell(c, club_owner_tg(c, t["from_club_id"]), f"⏳ Предложение по {want['name']} принято — сумма выше порога, ждём судью")
        _call_judges(c, trn, f"⚖️ Обмен ждёт одобрения: {want['name']}, доплата {_m(abs(money))} ₼ (мини-апп → Рынок → Судья)")
        c.commit()
        c.close()
        return {"status": "needs_judge"}

    return _execute_exchange(c, t, give, want, by_telegram_id)


def decline_exchange(transfer_id: int, by_telegram_id: int) -> dict:
    """Получатель отклоняет, инициатор отзывает (только пока pending)."""
    c = appdb.db()
    t = c.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
    if not t or t["status"] != "pending" or not t["want_card_id"]:
        c.close()
        raise TransferError("NOT_PENDING", "Предложение недействительно")
    to_tg, from_tg = club_owner_tg(c, t["to_club_id"]), club_owner_tg(c, t["from_club_id"])
    if by_telegram_id == to_tg:
        status, other, text = "rejected", from_tg, f"❌ Предложение по {t['player_name']} отклонено"
    elif by_telegram_id == from_tg:
        status, other, text = "cancelled", to_tg, f"↩️ Предложение по {t['player_name']} отозвано"
    else:
        c.close()
        raise TransferError("NOT_YOURS", "Это не твоё предложение")
    c.execute("UPDATE transfers SET status=?, decided_by=? WHERE id=?", (status, by_telegram_id, transfer_id))
    _tell(c, other, text)
    c.commit()
    c.close()
    return {"status": status}


def _check_exchange(c, t, give, want) -> None:
    """Владение карточками и бюджет — на момент исполнения, не предложения."""
    if t["card_id"] and (not give or give["club_id"] != t["from_club_id"]):
        c.close()
        raise TransferError("MOVED", "Отдаваемая карточка уже не в клубе-инициаторе")
    if not want or want["club_id"] != t["to_club_id"]:
        c.close()
        raise TransferError("MOVED", "Запрашиваемая карточка уже не в клубе-получателе")
    if _on_market(c, t["card_id"]) or _on_market(c, t["want_card_id"]):
        c.close()
        raise TransferError("ON_MARKET", "Карточка выставлена на рынок — сначала сними лот")
    money = int(t["amount"] or 0)
    payer_id = t["from_club_id"] if money > 0 else t["to_club_id"]
    payer = c.execute("SELECT budget FROM clubs WHERE id=?", (payer_id,)).fetchone()
    if money and (not payer or payer["budget"] < abs(money)):
        c.close()
        raise TransferError("NO_FUNDS", "Не хватает бюджета на обмен")


def _execute_exchange(c, t, give, want, actor_tg: int | None) -> dict:
    """Исполнение: give → to_club, want → from_club; деньги >0 платит инициатор.
    Чисто денежное предложение — это продажа: комиссия сгорает, как у лота."""
    money = int(t["amount"] or 0)
    if give:
        _move_card(c, give["id"], t["to_club_id"])
    _move_card(c, want["id"], t["from_club_id"])
    comm = 0
    label = f"{give['name']}↔{want['name']}" if give else f"{want['name']} за {_m(money)}"
    if money:
        # знак money: + инициатор доплачивает, − доплачивает получатель
        if not give:
            comm = commission(money)
        _money(c, t["from_club_id"], -money, f"обмен: {label}")
        _money(c, t["to_club_id"], money - comm, f"обмен: {label}")
    c.execute("UPDATE transfers SET status='approved', decided_by=? WHERE id=?", (actor_tg, t["id"]))
    c.execute(
        "INSERT INTO tournament_audit_log (actor_telegram_id, action, details) VALUES (?, 'transfer_exchange', ?)",
        (actor_tg, f"{label} {t['from_club_id']}↔{t['to_club_id']} доплата {money}, комиссия {comm}"),
    )
    for cid in (t["from_club_id"], t["to_club_id"]):
        _tell(c, club_owner_tg(c, cid), f"✅ Сделка состоялась: {label} ₼" if not give else f"✅ Обмен состоялся: {label}")
    c.commit()
    c.close()
    return {"status": "approved", "commission": comm}


# ===== судья =====

def _deal_lot(c, t):
    if t["lot_id"]:
        return c.execute("SELECT * FROM transfer_lots WHERE id=?", (t["lot_id"],)).fetchone()
    if t["want_card_id"] or not t["card_id"]:
        return None
    # старые сделки без lot_id
    return c.execute("SELECT * FROM transfer_lots WHERE card_id=? AND status='needs_judge' ORDER BY id DESC",
                     (t["card_id"],)).fetchone()


def judge_rights(telegram_id: int, transfer_id: int) -> tuple[bool, str]:
    """Кто решает крупную сделку: root / админ аппа / судья турнира одного из клубов.
    Владелец клуба-участника свою сделку не судит (конфликт интересов)."""
    c = appdb.db()
    t = c.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
    if not t:
        c.close()
        return False, "Сделка не найдена"
    ok, why = _can_judge(c, telegram_id, t)
    c.close()
    return ok, why


def _can_judge(c, telegram_id: int, t) -> tuple[bool, str]:
    if telegram_id in (club_owner_tg(c, t["from_club_id"]), club_owner_tg(c, t["to_club_id"])):
        return False, "Нельзя судить сделку своего клуба"
    if telegram_id in config.ADMIN_IDS:
        return True, ""
    u = c.execute("SELECT is_admin FROM users WHERE telegram_id=?", (telegram_id,)).fetchone()
    if u and u["is_admin"]:
        return True, ""
    tids = {t["tournament_id"], _club_tournament(c, t["from_club_id"]), _club_tournament(c, t["to_club_id"])} - {None}
    for tid in tids:
        if c.execute("SELECT 1 FROM tournament_admins WHERE tournament_id=? AND telegram_id=?",
                     (tid, telegram_id)).fetchone():
            return True, ""
    return False, "Только судья турнира этой сделки"


def judge_queue(telegram_id: int) -> list[dict]:
    """Сделки needs_judge, которые этот пользователь вправе решить."""
    c = appdb.db()
    rows = c.execute(
        "SELECT t.*, gc.name AS give_name, gc.position AS give_pos, gc.rating AS give_rating, "
        "wc.name AS want_name, wc.position AS want_pos, wc.rating AS want_rating, "
        "fc.name AS from_name, tc.name AS to_name, l.kind AS lot_kind "
        "FROM transfers t LEFT JOIN club_cards gc ON gc.id=t.card_id "
        "LEFT JOIN club_cards wc ON wc.id=t.want_card_id "
        "LEFT JOIN clubs fc ON fc.id=t.from_club_id LEFT JOIN clubs tc ON tc.id=t.to_club_id "
        "LEFT JOIN transfer_lots l ON l.id=t.lot_id "
        "WHERE t.status='needs_judge' ORDER BY t.id").fetchall()
    out = []
    for r in rows:
        if not _can_judge(c, telegram_id, r)[0]:
            continue
        d = dict(r)
        if d["want_card_id"]:
            d["deal_kind"] = "exchange" if d["card_id"] else "offer"
        else:
            d["deal_kind"] = "auction" if d["lot_kind"] == "auction" else "lot"
        out.append(d)
    c.close()
    return out


def judge_decide(transfer_id: int, approve: bool, judge_tg: int) -> dict:
    """Судья одобряет/отклоняет крупную сделку (права проверяет API: judge_rights)."""
    c = appdb.db()
    t = c.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
    if not t or t["status"] not in ("pending", "needs_judge"):
        c.close()
        raise TransferError("NOT_PENDING", "Сделка не ждёт решения")
    lot = _deal_lot(c, t)
    buyer_tg, seller_tg = club_owner_tg(c, t["to_club_id"]), club_owner_tg(c, t["from_club_id"])
    if not approve:
        c.execute("UPDATE transfers SET status='rejected', decided_by=? WHERE id=?", (judge_tg, transfer_id))
        if lot and lot["kind"] == "auction":
            bid = _held_bid(c, lot["id"])
            if bid:
                _release_bid(c, bid)
            c.execute("UPDATE transfer_lots SET status='cancelled' WHERE id=? AND status='needs_judge'", (lot["id"],))
            _tell(c, buyer_tg, f"❌ Судья отклонил аукцион {t['player_name']} — {_m(t['amount'])} ₼ вернулись в бюджет")
            _tell(c, seller_tg, f"❌ Судья отклонил аукцион {t['player_name']} — карточка осталась в клубе")
        else:
            if lot:
                c.execute("UPDATE transfer_lots SET status='open', buyer_club_id=NULL WHERE id=? AND status='needs_judge'",
                          (lot["id"],))
            _tell(c, buyer_tg, f"❌ Сделка {t['player_name']} отклонена судьёй")
            if t["want_card_id"]:
                _tell(c, seller_tg, f"❌ Сделка {t['player_name']} отклонена судьёй")
        c.commit()
        c.close()
        return {"status": "rejected"}

    if t["want_card_id"]:
        # обмен: судья решает только после согласия второй стороны
        if t["status"] != "needs_judge":
            c.close()
            raise TransferError("NOT_PENDING", "Обмен ещё не подтверждён второй стороной")
        give = c.execute("SELECT * FROM club_cards WHERE id=?", (t["card_id"],)).fetchone() if t["card_id"] else None
        want = c.execute("SELECT * FROM club_cards WHERE id=?", (t["want_card_id"],)).fetchone()
        _check_exchange(c, t, give, want)
        return _execute_exchange(c, t, give, want, judge_tg)

    card = c.execute("SELECT * FROM club_cards WHERE id=?", (t["card_id"],)).fetchone()
    money = int(t["amount"] or 0)
    if t["from_club_id"] is None:
        # свободный агент не бывает pending — пропускаем
        c.close()
        raise TransferError("NO_DATA", "Странная сделка")
    if not card or card["club_id"] != t["from_club_id"]:
        c.close()
        raise TransferError("MOVED", "Карточка уже не у продавца")
    prepaid = False
    if lot and lot["kind"] == "auction":
        bid = _held_bid(c, lot["id"])
        if not bid or bid["club_id"] != t["to_club_id"]:
            c.close()
            raise TransferError("NO_DATA", "Ставка победителя не найдена")
        c.execute("UPDATE transfer_bids SET status='won' WHERE id=?", (bid["id"],))
        prepaid = True
    elif _budget(c, t["to_club_id"]) < money:
        c.close()
        raise TransferError("NO_FUNDS", "У покупателя не хватает бюджета")
    _dm(c, buyer_tg, f"✅ Судья одобрил: {card['name']} за {_m(money)} ₼ теперь в твоём клубе")
    _dm(c, seller_tg, f"✅ Судья одобрил продажу {card['name']} за {_m(money)} ₼")
    return _execute_deal(c, t["from_club_id"], t["to_club_id"], card, money, judge_tg,
                         lot_id=lot["id"] if lot else None, prepaid=prepaid, transfer_id=transfer_id)


def cancel_lot(club_id: int, lot_id: int) -> dict:
    c = appdb.db()
    lot = c.execute("SELECT * FROM transfer_lots WHERE id=? AND status='open'", (lot_id,)).fetchone()
    if not lot or lot["seller_club_id"] != club_id:
        c.close()
        raise TransferError("NOT_YOURS", "Лот не твой или уже не открыт")
    if lot["kind"] == "auction" and lot["top_bid"]:
        c.close()
        raise TransferError("HAS_BIDS", "На аукционе уже есть ставки — снять нельзя")
    c.execute("UPDATE transfer_lots SET status='cancelled' WHERE id=?", (lot_id,))
    c.commit()
    c.close()
    return {"status": "cancelled"}


# ===== чтение для экранов =====

def _auction_fields(r: dict, now: datetime, step: float) -> dict:
    if r.get("kind") != "auction":
        return r
    ends = _parse_ts(r.get("ends_at"))
    r["seconds_left"] = max(0, int((ends - now).total_seconds())) if ends else None
    r["current_price"] = r.get("top_bid") or r["price"]
    r["min_bid"] = min_next_bid(r, step)
    if r.get("buyout_price"):
        r["min_bid"] = min(r["min_bid"], int(r["buyout_price"]))
    return r


def club_transfers_view(club_id: int) -> dict:
    """Всё для полноэкранного экрана трансферов: бюджет, состав с ценами, лоты, предложения, ставки."""
    close_expired_auctions(drain=False)
    now, step = _now(), auction_step_pct()
    c = appdb.db()
    club = c.execute("SELECT * FROM clubs WHERE id=?", (club_id,)).fetchone()
    squad = [dict(r) for r in c.execute(
        "SELECT * FROM club_cards WHERE club_id=? ORDER BY rating DESC", (club_id,)).fetchall()]
    on_market = {r["card_id"] for r in c.execute(
        "SELECT card_id FROM transfer_lots WHERE seller_club_id=? AND status IN ('open','closing','needs_judge')",
        (club_id,)).fetchall()}
    for s in squad:
        s["sell_value"] = max(0, free_agent_price(s["rating"]) // 2)  # ориентир цены продажи
        s["on_market"] = s["id"] in on_market
    lots = [_auction_fields(dict(r), now, step) for r in c.execute(
        "SELECT l.*, cc.name, cc.position, cc.rating, tc.name AS top_club_name FROM transfer_lots l "
        "JOIN club_cards cc ON cc.id=l.card_id LEFT JOIN clubs tc ON tc.id=l.top_club_id "
        "WHERE l.seller_club_id=? AND l.status IN ('open','needs_judge') ORDER BY l.id DESC",
        (club_id,)).fetchall()]
    offers_in = [dict(r) for r in c.execute(
        "SELECT t.*, cc.name AS give_name, cw.name AS want_name, cl.name AS from_name "
        "FROM transfers t LEFT JOIN club_cards cc ON cc.id=t.card_id "
        "LEFT JOIN club_cards cw ON cw.id=t.want_card_id "
        "LEFT JOIN clubs cl ON cl.id=t.from_club_id "
        "WHERE t.to_club_id=? AND t.status='pending' AND t.want_card_id IS NOT NULL ORDER BY t.id DESC",
        (club_id,)).fetchall()]
    offers_out = [dict(r) for r in c.execute(
        "SELECT t.*, cc.name AS give_name, cw.name AS want_name, cl.name AS to_name "
        "FROM transfers t LEFT JOIN club_cards cc ON cc.id=t.card_id "
        "LEFT JOIN club_cards cw ON cw.id=t.want_card_id "
        "LEFT JOIN clubs cl ON cl.id=t.to_club_id "
        "WHERE t.from_club_id=? AND t.status IN ('pending','needs_judge') AND t.want_card_id IS NOT NULL "
        "ORDER BY t.id DESC", (club_id,)).fetchall()]
    bids = [_auction_fields(dict(r), now, step) for r in c.execute(
        "SELECT l.*, cc.name, cc.position, cc.rating, b.amount AS my_bid, cl.name AS seller_name "
        "FROM transfer_bids b JOIN transfer_lots l ON l.id=b.lot_id JOIN club_cards cc ON cc.id=l.card_id "
        "LEFT JOIN clubs cl ON cl.id=l.seller_club_id "
        "WHERE b.club_id=? AND b.status='held' ORDER BY l.ends_at", (club_id,)).fetchall()]
    # аукционы, где нас перебили, но лот ещё идёт
    outbid = [_auction_fields(dict(r), now, step) for r in c.execute(
        "SELECT DISTINCT l.*, cc.name, cc.position, cc.rating, cl.name AS seller_name "
        "FROM transfer_bids b JOIN transfer_lots l ON l.id=b.lot_id JOIN club_cards cc ON cc.id=l.card_id "
        "LEFT JOIN clubs cl ON cl.id=l.seller_club_id "
        "WHERE b.club_id=? AND b.status='outbid' AND l.status='open' AND l.top_club_id!=?",
        (club_id, club_id)).fetchall()]
    locked = sum(int(b["my_bid"]) for b in bids)
    history = [dict(r) for r in c.execute(
        "SELECT t.*, fc.name AS from_name, tc.name AS to_name, l.kind AS lot_kind FROM transfers t "
        "LEFT JOIN clubs fc ON fc.id=t.from_club_id LEFT JOIN clubs tc ON tc.id=t.to_club_id "
        "LEFT JOIN transfer_lots l ON l.id=t.lot_id "
        "WHERE (t.from_club_id=? OR t.to_club_id=?) AND t.status!='pending' "
        "ORDER BY t.id DESC LIMIT 30", (club_id, club_id)).fetchall()]
    c.close()
    return {
        "club_id": club_id,
        "budget": club["budget"] if club else 0,
        "locked": locked,
        "threshold": threshold(),
        "commission_pct": appsettings.setting_float("transfer_commission_pct", 5),
        "auction": {"step_pct": step, "hours": auction_hours(), "snipe_minutes": snipe_minutes()},
        "squad": squad, "lots": lots, "offers_in": offers_in, "offers_out": offers_out,
        "bids": bids, "outbid": outbid, "history": history,
    }


def market_list(division_id: int | None = None, position: str | None = None,
                min_rating: int | None = None, max_price: int | None = None) -> dict:
    """Маркетплейс: открытые лоты (кроме своих — фильтр на клиенте) + свободные агенты."""
    close_expired_auctions(drain=False)
    now, step = _now(), auction_step_pct()
    c = appdb.db()
    sql, args = ("SELECT l.*, cc.name, cc.position, cc.rating, cl.name AS seller_name, tc.name AS top_club_name "
                 "FROM transfer_lots l JOIN club_cards cc ON cc.id=l.card_id "
                 "LEFT JOIN clubs cl ON cl.id=l.seller_club_id LEFT JOIN clubs tc ON tc.id=l.top_club_id "
                 "WHERE l.status='open'"), []
    if position:
        sql += " AND cc.position=?"
        args.append(position)
    if min_rating:
        sql += " AND cc.rating>=?"
        args.append(min_rating)
    rows = [dict(r) for r in c.execute(sql + " ORDER BY l.id DESC LIMIT 100", args).fetchall()]
    for r in rows:
        _auction_fields(r, now, step)
        r["effective_price"] = r["current_price"] if r["kind"] == "auction" else r["price"]

    sql2, args2 = ("SELECT cc.*, NULL AS seller_name FROM club_cards cc "
                   "WHERE cc.club_id IS NULL"), []
    if position:
        sql2 += " AND cc.position=?"
        args2.append(position)
    if min_rating:
        sql2 += " AND cc.rating>=?"
        args2.append(min_rating)
    agents = [dict(r) for r in c.execute(sql2 + " ORDER BY cc.rating DESC LIMIT 100", args2).fetchall()]
    for a in agents:
        a["effective_price"] = free_agent_price(a["rating"])
    if max_price:
        rows = [r for r in rows if r["effective_price"] <= max_price]
        agents = [a for a in agents if a["effective_price"] <= max_price]
    c.close()
    return {"lots": rows, "free_agents": agents}


def lot_detail(lot_id: int, viewer_club_id: int | None = None) -> dict:
    """Карточка лота: ставки, время, минимальная ставка, история переходов карточки."""
    close_expired_auctions(drain=False)
    now, step = _now(), auction_step_pct()
    c = appdb.db()
    row = c.execute(
        "SELECT l.*, cc.name, cc.position, cc.rating, cl.name AS seller_name, cl.logo_path AS seller_logo, "
        "tc.name AS top_club_name FROM transfer_lots l JOIN club_cards cc ON cc.id=l.card_id "
        "LEFT JOIN clubs cl ON cl.id=l.seller_club_id LEFT JOIN clubs tc ON tc.id=l.top_club_id WHERE l.id=?",
        (lot_id,)).fetchone()
    if not row:
        c.close()
        raise TransferError("NOT_FOUND", "Лот не найден")
    lot = _auction_fields(dict(row), now, step)
    lot["bids"] = [dict(r) for r in c.execute(
        "SELECT b.id, b.club_id, b.amount, b.status, b.created_at, cl.name AS club_name "
        "FROM transfer_bids b LEFT JOIN clubs cl ON cl.id=b.club_id WHERE b.lot_id=? ORDER BY b.id DESC LIMIT 50",
        (lot_id,)).fetchall()]
    lot["card_history"] = [dict(r) for r in c.execute(
        "SELECT t.id, t.amount, t.created_at, t.from_club_id, t.to_club_id, fc.name AS from_name, tc.name AS to_name "
        "FROM transfers t LEFT JOIN clubs fc ON fc.id=t.from_club_id LEFT JOIN clubs tc ON tc.id=t.to_club_id "
        "WHERE (t.card_id=? OR t.want_card_id=?) AND t.status='approved' ORDER BY t.id DESC LIMIT 20",
        (lot["card_id"], lot["card_id"])).fetchall()]
    lot["is_mine"] = viewer_club_id is not None and lot["seller_club_id"] == viewer_club_id
    lot["is_top"] = viewer_club_id is not None and lot.get("top_club_id") == viewer_club_id
    lot["step_pct"] = step
    lot["snipe_minutes"] = snipe_minutes()
    lot["threshold"] = threshold()
    lot["my_budget"] = _budget(c, viewer_club_id) if viewer_club_id else None
    c.close()
    return lot


def clubs_list(exclude_club_id: int | None = None) -> list[dict]:
    """Клубы для выбора второй стороны обмена."""
    c = appdb.db()
    rows = [dict(r) for r in c.execute(
        "SELECT cl.id, cl.name, cl.logo_path, cl.budget, COUNT(cc.id) AS cards "
        "FROM clubs cl LEFT JOIN club_cards cc ON cc.club_id=cl.id "
        "GROUP BY cl.id, cl.name, cl.logo_path, cl.budget ORDER BY cl.name").fetchall()]
    c.close()
    return [r for r in rows if r["id"] != exclude_club_id]


def club_squad(club_id: int) -> list[dict]:
    c = appdb.db()
    rows = [dict(r) for r in c.execute(
        "SELECT id, name, position, rating FROM club_cards WHERE club_id=? ORDER BY rating DESC", (club_id,)).fetchall()]
    for r in rows:
        r["on_market"] = _on_market(c, r["id"])
    c.close()
    return rows
