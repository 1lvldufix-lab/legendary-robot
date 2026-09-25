"""Трансферы (план 10 блок 8 / решения 09).

- Маркетплейс: лот фикс/аукцион; сделка авто при деньгах и сумме до порога
  (старт 2М, bot_settings); выше — ждёт одобрения судьи (анти-колллюзия).
- Комиссия 5% с каждой сделки сгорает из экономики.
- Свободные агенты: цена = рейтинг² × K (K в админке, публичная математика).
- Обмены: деньги / игрок+деньги / игрок↔игрок; вторая сторона подтверждает,
  дальше тот же порог/судья.
- Состав клуба — без лимита карточек.
"""
import logging

import db as appdb
import settings as appsettings

log = logging.getLogger("bot.transfers")


class TransferError(Exception):
    def __init__(self, code: str, message: str):
        super().__init__(message)
        self.code = code


def _notify(c, telegram_id: int | None, text: str) -> None:
    if not telegram_id:
        return
    c.execute("INSERT INTO notifications (user_id, text, kind) SELECT id, ?, 'app' FROM users WHERE telegram_id=?",
              (text, telegram_id))


def free_agent_price(rating: int) -> int:
    k = appsettings.setting_int("free_agent_k", 1000)
    return int(rating) * int(rating) * k


def commission(amount: int) -> int:
    pct = appsettings.setting_float("transfer_commission_pct", 5)
    return int(amount * pct / 100)


def threshold() -> int:
    return appsettings.setting_int("transfer_deal_threshold", 2_000_000)


def club_owner_tg(c, club_id: int) -> int | None:
    row = c.execute(
        "SELECT p.telegram_id FROM club_players cp JOIN players p ON p.id=cp.player_id WHERE cp.club_id=?",
        (club_id,),
    ).fetchone()
    return row["telegram_id"] if row else None


def _move_card(c, card_id: int, to_club: int | None) -> None:
    c.execute("UPDATE club_cards SET club_id=? WHERE id=?", (to_club, card_id))


def _money(c, club_id: int, delta: int, reason: str) -> None:
    c.execute("UPDATE clubs SET budget=budget+? WHERE id=?", (delta, club_id))


def _positive_int(value, field: str) -> int:
    """Цена из запроса → int > 0, иначе внятная TransferError (не 500)."""
    try:
        v = int(value)
    except (TypeError, ValueError):
        raise TransferError("BAD_PRICE", f"{field}: нужно целое число")
    if v <= 0:
        raise TransferError("BAD_PRICE", f"{field}: должна быть больше нуля")
    return v


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
        raise TransferError("NO_FUNDS", f"Не хватает бюджета: цена {price:,} ₼".replace(",", " "))
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
    _notify(c, club_owner_tg(c, club_id), f"⚽ Свободный агент {card['name']} подписан за {price:,} ₼".replace(",", " "))
    c.commit()
    budget = c.execute("SELECT budget FROM clubs WHERE id=?", (club_id,)).fetchone()["budget"]
    c.close()
    return {"transfer_id": tid, "price": price, "budget": budget}


def create_lot(club_id: int, card_id: int, kind: str, price: int,
               buyout_price: int | None = None, actor_tg: int | None = None) -> dict:
    """Выставить карточку на маркетплейс (фикс или аукцион с выкупом)."""
    if kind not in ("fix", "auction"):
        raise TransferError("BAD_KIND", "kind = fix|auction")
    price = _positive_int(price, "Цена")
    if buyout_price in (None, "", 0, "0"):
        buyout_price = None
    else:
        buyout_price = _positive_int(buyout_price, "Цена выкупа")
        if buyout_price < price:
            raise TransferError("BAD_PRICE", "Цена выкупа не может быть ниже стартовой")
    c = appdb.db()
    card = c.execute("SELECT * FROM club_cards WHERE id=?", (card_id,)).fetchone()
    if not card or card["club_id"] != club_id:
        c.close()
        raise TransferError("NOT_YOURS", "Карточка не в твоём клубе")
    dup = c.execute("SELECT 1 FROM transfer_lots WHERE card_id=? AND status='open'", (card_id,)).fetchone()
    if dup:
        c.close()
        raise TransferError("DUP", "Карточка уже на рынке")
    lot_id = c.insert_returning_id(
        "INSERT INTO transfer_lots (seller_club_id, card_id, kind, price, buyout_price, status) "
        "VALUES (?,?,?,?,?, 'open')",
        (club_id, card_id, kind, int(price), buyout_price),
    )
    _notify(c, club_owner_tg(c, club_id), f"📋 {card['name']} выставлен на рынок за {int(price):,} ₼".replace(",", " "))
    c.commit()
    c.close()
    return {"lot_id": lot_id}


def buy_lot(buyer_club_id: int, lot_id: int, actor_tg: int | None = None) -> dict:
    """Покупка лота: авто при деньгах и сумме ≤ порога; выше — needs_judge."""
    c = appdb.db()
    lot = c.execute("SELECT * FROM transfer_lots WHERE id=?", (lot_id,)).fetchone()
    if not lot or lot["status"] != "open":
        c.close()
        raise TransferError("NOT_OPEN", "Лот недоступен")
    if lot["seller_club_id"] == buyer_club_id:
        c.close()
        raise TransferError("SELF", "Нельзя купить свой лот")
    buyer = c.execute("SELECT * FROM clubs WHERE id=?", (buyer_club_id,)).fetchone()
    card = c.execute("SELECT * FROM club_cards WHERE id=?", (lot["card_id"],)).fetchone()
    if not buyer or not card:
        c.close()
        raise TransferError("NO_DATA", "Лот повреждён")
    price = lot["buyout_price"] or lot["price"]
    if buyer["budget"] < price:
        c.close()
        raise TransferError("NO_FUNDS", f"Не хватает бюджета: {price:,} ₼".replace(",", " "))

    if price > threshold():
        # крупная сделка — к судье (анти-колллюзия, решение 09)
        c.execute("UPDATE transfer_lots SET status='needs_judge', buyer_club_id=? WHERE id=?",
                  (buyer_club_id, lot_id))
        tid = c.insert_returning_id(
            "INSERT INTO transfers (from_club_id, to_club_id, card_id, player_name, amount, status, created_by) "
            "VALUES (?, ?, ?, ?, ?, 'pending', ?)",
            (lot["seller_club_id"], buyer_club_id, card["id"], card["name"], price, actor_tg),
        )
        _notify(c, club_owner_tg(c, buyer_club_id),
                f"⏳ Сделка {card['name']} на {price:,} ₼ ушла судье на одобрение".replace(",", " "))
        c.commit()
        c.close()
        return {"status": "needs_judge", "price": price, "transfer_id": tid}

    return _execute_deal(c, lot["seller_club_id"], buyer_club_id, card, price, actor_tg, lot_id=lot_id)


def _execute_deal(c, seller_club: int, buyer_club: int, card, price: int,
                  actor_tg: int | None, lot_id: int | None = None) -> dict:
    """Исполнение сделки: деньги, комиссия сгорает, карточка переезжает."""
    comm = commission(price)
    _move_card(c, card["id"], buyer_club)
    _money(c, buyer_club, -price, f"покупка {card['name']}")
    _money(c, seller_club, price - comm, f"продажа {card['name']} (комиссия {comm})")
    tid = c.insert_returning_id(
        "INSERT INTO transfers (from_club_id, to_club_id, card_id, player_name, amount, status, decided_by) "
        "VALUES (?, ?, ?, ?, ?, 'approved', ?)",
        (seller_club, buyer_club, card["id"], card["name"], price, actor_tg),
    )
    if lot_id:
        c.execute("UPDATE transfer_lots SET status='sold', buyer_club_id=? WHERE id=?", (buyer_club, lot_id))
    c.execute(
        "INSERT INTO tournament_audit_log (actor_telegram_id, action, details) VALUES (?, 'transfer_deal', ?)",
        (actor_tg, f"{card['name']} {seller_club}→{buyer_club} за {price}, комиссия {comm}"),
    )
    _notify(c, club_owner_tg(c, buyer_club), f"✅ Куплен {card['name']} за {price:,} ₼".replace(",", " "))
    _notify(c, club_owner_tg(c, seller_club), f"✅ Продан {card['name']} за {(price - comm):,} ₼ (комиссия {comm:,} сгорела)".replace(",", " "))
    c.commit()
    budgets = {}
    for cid in (seller_club, buyer_club):
        budgets[cid] = c.execute("SELECT budget FROM clubs WHERE id=?", (cid,)).fetchone()["budget"]
    c.close()
    return {"status": "approved", "transfer_id": tid, "price": price, "commission": comm, "budgets": budgets}


def propose_exchange(from_club_id: int, to_club_id: int, give_card_id: int,
                     want_card_id: int, money: int, actor_tg: int | None = None) -> dict:
    """Обмен: деньги / игрок+деньги / игрок↔игрок. Вторая сторона подтверждает."""
    c = appdb.db()
    give = c.execute("SELECT * FROM club_cards WHERE id=?", (give_card_id,)).fetchone()
    want = c.execute("SELECT * FROM club_cards WHERE id=?", (want_card_id,)).fetchone()
    if not give or give["club_id"] != from_club_id:
        c.close()
        raise TransferError("NOT_YOURS", "Отдаёмая карточка не в твоём клубе")
    if not want or want["club_id"] != to_club_id:
        c.close()
        raise TransferError("NOT_THEIRS", "Запрашиваемая карточка у другого клуба")
    if from_club_id == to_club_id:
        c.close()
        raise TransferError("SELF", "Обмен внутри клуба запрещён")
    tid = c.insert_returning_id(
        "INSERT INTO transfers (from_club_id, to_club_id, card_id, want_card_id, player_name, amount, status, created_by) "
        "VALUES (?, ?, ?, ?, ?, ?, 'pending', ?)",
        (from_club_id, to_club_id, give_card_id, want_card_id, give["name"], int(money), actor_tg),
    )
    _notify(c, club_owner_tg(c, to_club_id),
            f"🔄 Предложение обмена: отдают {give['name']}" + (f" + {int(money):,} ₼" if money else "")
            + f" за {want['name']}")
    c.commit()
    c.close()
    return {"transfer_id": tid}


def accept_exchange(transfer_id: int, by_telegram_id: int) -> dict:
    """Вторая сторона подтвердила → авто при |деньгах| ≤ порога, иначе судья."""
    c = appdb.db()
    t = c.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
    if not t or t["status"] != "pending":
        c.close()
        raise TransferError("NOT_PENDING", "Предложение недействительно")
    owner = club_owner_tg(c, t["to_club_id"])
    if owner != by_telegram_id:
        c.close()
        raise TransferError("NOT_YOURS", "Подтвердить может только получатель предложения")
    give = c.execute("SELECT * FROM club_cards WHERE id=?", (t["card_id"],)).fetchone()
    want_card_id = t["want_card_id"]
    if not want_card_id:
        c.close()
        raise TransferError("NO_DATA", "Предложение повреждено")
    want = c.execute("SELECT * FROM club_cards WHERE id=?", (want_card_id,)).fetchone()
    _check_exchange(c, t, give, want)
    money = int(t["amount"] or 0)

    if abs(money) > threshold():
        c.execute("UPDATE transfers SET status='needs_judge' WHERE id=?", (transfer_id,))
        c.commit()
        c.close()
        return {"status": "needs_judge"}

    return _execute_exchange(c, t, give, want, by_telegram_id)


def _check_exchange(c, t, give, want) -> None:
    """Владение карточками и бюджет — на момент исполнения, не предложения."""
    if not give or give["club_id"] != t["from_club_id"]:
        c.close()
        raise TransferError("MOVED", "Отдаваемая карточка уже не в клубе-инициаторе")
    if not want or want["club_id"] != t["to_club_id"]:
        c.close()
        raise TransferError("MOVED", "Запрашиваемая карточка уже не в клубе-получателе")
    money = int(t["amount"] or 0)
    payer_id = t["from_club_id"] if money > 0 else t["to_club_id"]
    payer = c.execute("SELECT budget FROM clubs WHERE id=?", (payer_id,)).fetchone()
    if money and (not payer or payer["budget"] < abs(money)):
        c.close()
        raise TransferError("NO_FUNDS", "Не хватает бюджета на обмен")


def _execute_exchange(c, t, give, want, actor_tg: int | None) -> dict:
    """Исполнение обмена: give → to_club, want → from_club; деньги >0 платит инициатор."""
    money = int(t["amount"] or 0)
    _move_card(c, give["id"], t["to_club_id"])
    _move_card(c, want["id"], t["from_club_id"])
    if money:
        # знак money: + инициатор доплачивает, − доплачивает получатель
        _money(c, t["from_club_id"], -money, f"обмен: {give['name']}↔{want['name']}")
        _money(c, t["to_club_id"], money, f"обмен: {give['name']}↔{want['name']}")
    c.execute("UPDATE transfers SET status='approved', decided_by=? WHERE id=?", (actor_tg, t["id"]))
    c.execute(
        "INSERT INTO tournament_audit_log (actor_telegram_id, action, details) VALUES (?, 'transfer_exchange', ?)",
        (actor_tg, f"{give['name']}↔{want['name']} {t['from_club_id']}↔{t['to_club_id']} доплата {money}"),
    )
    for cid in (t["from_club_id"], t["to_club_id"]):
        _notify(c, club_owner_tg(c, cid), f"✅ Обмен состоялся: {give['name']} ↔ {want['name']}")
    c.commit()
    c.close()
    return {"status": "approved"}


def judge_decide(transfer_id: int, approve: bool, judge_tg: int) -> dict:
    """Судья одобряет/отклоняет крупную сделку."""
    c = appdb.db()
    t = c.execute("SELECT * FROM transfers WHERE id=?", (transfer_id,)).fetchone()
    if not t or t["status"] not in ("pending", "needs_judge"):
        c.close()
        raise TransferError("NOT_PENDING", "Сделка не ждёт решения")
    if not approve:
        c.execute("UPDATE transfers SET status='rejected', decided_by=? WHERE id=?", (judge_tg, transfer_id))
        c.execute("UPDATE transfer_lots SET status='open', buyer_club_id=NULL WHERE card_id=? AND status='needs_judge'",
                  (t["card_id"],))
        _notify(c, club_owner_tg(c, t["to_club_id"]), f"❌ Сделка {t['player_name']} отклонена судьёй")
        c.commit()
        c.close()
        return {"status": "rejected"}

    if t["want_card_id"]:
        # обмен: судья решает только после согласия второй стороны
        if t["status"] != "needs_judge":
            c.close()
            raise TransferError("NOT_PENDING", "Обмен ещё не подтверждён второй стороной")
        give = c.execute("SELECT * FROM club_cards WHERE id=?", (t["card_id"],)).fetchone()
        want = c.execute("SELECT * FROM club_cards WHERE id=?", (t["want_card_id"],)).fetchone()
        _check_exchange(c, t, give, want)
        return _execute_exchange(c, t, give, want, judge_tg)

    card = c.execute("SELECT * FROM club_cards WHERE id=?", (t["card_id"],)).fetchone()
    money = int(t["amount"] or 0)
    if t["from_club_id"] is None:
        # свободный агент не бывает pending — пропускаем
        c.close()
        raise TransferError("NO_DATA", "Странная сделка")
    if card and card["club_id"] != t["from_club_id"]:
        c.close()
        raise TransferError("MOVED", "Карточка уже не у продавца")
    buyer_budget = c.execute("SELECT budget FROM clubs WHERE id=?", (t["to_club_id"],)).fetchone()["budget"]
    if buyer_budget < money:
        c.close()
        raise TransferError("NO_FUNDS", "У покупателя не хватает бюджета")
    return _execute_deal(c, t["from_club_id"], t["to_club_id"], card, money, judge_tg)


def cancel_lot(club_id: int, lot_id: int) -> dict:
    c = appdb.db()
    lot = c.execute("SELECT * FROM transfer_lots WHERE id=? AND status='open'", (lot_id,)).fetchone()
    if not lot or lot["seller_club_id"] != club_id:
        c.close()
        raise TransferError("NOT_YOURS", "Лот не твой или уже не открыт")
    c.execute("UPDATE transfer_lots SET status='cancelled' WHERE id=?", (lot_id,))
    c.commit()
    c.close()
    return {"status": "cancelled"}


def club_transfers_view(club_id: int) -> dict:
    """Всё для полноэкранного экрана трансферов: бюджет, состав с ценами, лоты, предложения."""
    c = appdb.db()
    club = c.execute("SELECT * FROM clubs WHERE id=?", (club_id,)).fetchone()
    squad = [dict(r) for r in c.execute(
        "SELECT * FROM club_cards WHERE club_id=? ORDER BY rating DESC", (club_id,)).fetchall()]
    for s in squad:
        s["sell_value"] = max(0, free_agent_price(s["rating"]) // 2)  # ориентир цены продажи
    lots = [dict(r) for r in c.execute(
        "SELECT l.*, cc.name, cc.position, cc.rating FROM transfer_lots l "
        "JOIN club_cards cc ON cc.id=l.card_id WHERE l.seller_club_id=? AND l.status='open'",
        (club_id,)).fetchall()]
    offers_in = [dict(r) for r in c.execute(
        "SELECT t.*, cc.name AS give_name, cw.name AS want_name, cl.name AS from_name "
        "FROM transfers t JOIN club_cards cc ON cc.id=t.card_id "
        "LEFT JOIN club_cards cw ON cw.id=t.want_card_id "
        "LEFT JOIN clubs cl ON cl.id=t.from_club_id "
        "WHERE t.to_club_id=? AND t.status='pending'", (club_id,)).fetchall()]
    history = [dict(r) for r in c.execute(
        "SELECT * FROM transfers WHERE (from_club_id=? OR to_club_id=?) AND status!='pending' "
        "ORDER BY id DESC LIMIT 30", (club_id, club_id)).fetchall()]
    c.close()
    return {
        "budget": club["budget"] if club else 0,
        "threshold": threshold(),
        "commission_pct": appsettings.setting_float("transfer_commission_pct", 5),
        "squad": squad, "lots": lots, "offers_in": offers_in, "history": history,
    }


def market_list(division_id: int | None = None, position: str | None = None,
                min_rating: int | None = None, max_price: int | None = None) -> dict:
    """Маркетплейс: открытые лоты (кроме своих — фильтр на клиенте) + свободные агенты."""
    c = appdb.db()
    sql, args = ("SELECT l.*, cc.name, cc.position, cc.rating, cl.name AS seller_name "
                 "FROM transfer_lots l JOIN club_cards cc ON cc.id=l.card_id "
                 "LEFT JOIN clubs cl ON cl.id=l.seller_club_id WHERE l.status='open'"), []
    if position:
        sql += " AND cc.position=?"
        args.append(position)
    if min_rating:
        sql += " AND cc.rating>=?"
        args.append(min_rating)
    rows = [dict(r) for r in c.execute(sql + " ORDER BY l.id DESC LIMIT 100", args).fetchall()]
    for r in rows:
        r["effective_price"] = r["buyout_price"] or r["price"]

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
