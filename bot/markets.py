"""Кэфы из Elo + публичная маржа 5% (план 09).

Модель: разница Elo → ожидаемые голы команд (симметрично), матрица Пуассона
0..8 голов → вероятности 1х2, тоталов, БТТС, ИТБ, AH. Кэф = fair/(1+маржа).
Пишется в markets + снапшот в odds_history (публичная история движения).
"""
import math

import db as appdb
import elo as elo_engine
import settings as appsettings

MAX_GOALS = 8          # глубина пуассоновской матрицы
BASE_LAMBDA = 1.35     # базовые ожидаемые голы команды при равных Elo


def lambdas(elo_home: float, elo_away: float) -> tuple[float, float]:
    diff = (elo_home - elo_away) / 400.0
    lh = BASE_LAMBDA * (10 ** (diff * 0.5))
    la = BASE_LAMBDA * (10 ** (-diff * 0.5))
    return max(0.2, min(lh, 4.5)), max(0.2, min(la, 4.5))


def _pois(k: int, lam: float) -> float:
    return math.exp(-lam) * lam ** k / math.factorial(k)


def probabilities(elo_home: float, elo_away: float) -> dict[str, float]:
    """Вероятности исходов из пуассоновской матрицы."""
    lh, la = lambdas(elo_home, elo_away)
    ph = [_pois(k, lh) for k in range(MAX_GOALS + 1)]
    pa = [_pois(k, la) for k in range(MAX_GOALS + 1)]
    p1 = px = p2 = p_tb25 = p_btts = p_itb_h15 = p_itb_a15 = p_ah_h15 = 0.0
    for i in range(MAX_GOALS + 1):
        for j in range(MAX_GOALS + 1):
            w = ph[i] * pa[j]
            if i > j:
                p1 += w
                p_ah_h15 += w if i - j >= 2 else 0
            elif i == j:
                px += w
            else:
                p2 += w
            if i + j > 2.5:
                p_tb25 += w
            if i > 0 and j > 0:
                p_btts += w
            if i > 1.5:
                p_itb_h15 += w
            if j > 1.5:
                p_itb_a15 += w
    return {
        "p1": p1, "px": px, "p2": p2,
        "tb25": p_tb25, "tm25": 1 - p_tb25,
        "btts_yes": p_btts, "btts_no": 1 - p_btts,
        "itb_h15": p_itb_h15, "itb_a15": p_itb_a15,
        "ah_h15": p_ah_h15, "ah_a15": 1 - p_ah_h15,  # AH +1.5 гостей = не проиграть в 2+ (дополнение к ah_h15)
    }


MARKET_LABELS = {
    "1x2_p1": "П1", "1x2_x": "Ничья", "1x2_p2": "П2",
    "tb25": "ТБ 2.5", "tm25": "ТМ 2.5",
    "btts_yes": "БТТС да", "btts_no": "БТТС нет",
    "itb_h15": "ИТБ хоз 1.5", "itb_a15": "ИТБ гост 1.5",
    "ah_h15": "AH хоз -1.5", "ah_a15": "AH гост +1.5",
}
_BASE_PROBS = {
    "1x2_p1": "p1", "1x2_x": "px", "1x2_p2": "p2",
    "tb25": "tb25", "tm25": "tm25",
    "btts_yes": "btts_yes", "btts_no": "btts_no",
    "itb_h15": "itb_h15", "itb_a15": "itb_a15",
    "ah_h15": "ah_h15", "ah_a15": "ah_a15",
}


def compute_odds(elo_home: float, elo_away: float) -> dict[str, float]:
    """Код рынка → кэф с маржой (маржа публичная, план 09)."""
    probs = probabilities(elo_home, elo_away)
    margin = appsettings.setting_float("odds_margin_pct", 5) / 100.0
    out = {}
    for code, prob_key in _BASE_PROBS.items():
        p = max(0.01, min(0.97, probs[prob_key]))
        out[code] = round(1.0 / p / (1 + margin), 2)
    return out


def generate_markets(match_id: int) -> int:
    """Сгенерить/обновить markets для матча (по Elo клубов). → число рынков."""
    c = appdb.db()
    m = c.execute("SELECT * FROM matches WHERE id=?", (match_id,)).fetchone()
    if not m or m["home_club_id"] is None or m["away_club_id"] is None:
        c.close()
        return 0
    elo_h = c.execute("SELECT elo FROM clubs WHERE id=?", (m["home_club_id"],)).fetchone()
    elo_a = c.execute("SELECT elo FROM clubs WHERE id=?", (m["away_club_id"],)).fetchone()
    c.close()
    odds = compute_odds(elo_h["elo"] if elo_h else 1000, elo_a["elo"] if elo_a else 1000)
    c = appdb.db()
    n = 0
    for code, odd in odds.items():
        row = c.execute(
            "SELECT id, odds FROM markets WHERE match_id=? AND code=?", (match_id, code)
        ).fetchone()
        if row:
            if abs((row["odds"] or 0) - odd) >= 0.01:
                c.execute("UPDATE markets SET odds=?, updated_at=datetime('now') WHERE id=?", (odd, row["id"]))
                c.execute(
                    "INSERT INTO odds_history (match_id, market_code, odds) VALUES (?,?,?)",
                    (match_id, code, odd),
                )
        else:
            c.execute(
                "INSERT INTO markets (match_id, code, label, odds) VALUES (?,?,?,?)",
                (match_id, code, MARKET_LABELS[code], odd),
            )
            c.execute(
                "INSERT INTO odds_history (match_id, market_code, odds) VALUES (?,?,?)",
                (match_id, code, odd),
            )
        n += 1
    c.commit()
    c.close()
    return n


def refresh_tour(tournament_id: int, tour_number: int | None = None) -> int:
    """Обновить кэфы тура (после Elo-сдвигов / при открытии тура)."""
    c = appdb.db()
    if tour_number is None:
        rows = [dict(r) for r in c.execute(
            "SELECT id FROM matches WHERE tournament_id=? AND status='pending'", (tournament_id,)
        ).fetchall()]
    else:
        rows = [dict(r) for r in c.execute(
            "SELECT id FROM matches WHERE tournament_id=? AND tour_number=? AND status='pending'",
            (tournament_id, tour_number),
        ).fetchall()]
    c.close()
    return sum(generate_markets(r["id"]) for r in rows)


def generate_tie_markets(match_id: int, tie_id: int) -> int:
    """Рынок «исход противостояния» (2:0/2:1/1:2/0:2 — серии до 2 побед, план 09).
    Вешается на первую игру серии."""
    c = appdb.db()
    tie = c.execute("SELECT * FROM ties WHERE id=?", (tie_id,)).fetchone()
    if not tie:
        c.close()
        return 0
    elo_a = c.execute("SELECT elo FROM clubs WHERE id=?", (tie["club_a_id"],)).fetchone()
    elo_b = c.execute("SELECT elo FROM clubs WHERE id=?", (tie["club_b_id"],)).fetchone()
    c.close()
    pw = max(0.05, min(0.95, elo_engine.expected_score(elo_a["elo"] if elo_a else 1000,
                                                       elo_b["elo"] if elo_b else 1000)))
    qw = 1 - pw
    probs = {
        "tie_2_0": pw * pw,
        "tie_2_1": 2 * pw * pw * qw,
        "tie_1_2": 2 * pw * qw * qw,
        "tie_0_2": qw * qw,
    }
    margin = appsettings.setting_float("odds_margin_pct", 5) / 100.0
    c = appdb.db()
    n = 0
    for code, p in probs.items():
        odd = round(1.0 / max(p, 0.01) / (1 + margin), 2)
        row = c.execute("SELECT id FROM markets WHERE match_id=? AND code=?", (match_id, code)).fetchone()
        if not row:
            c.execute("INSERT INTO markets (match_id, code, label, odds) VALUES (?,?,?,?)",
                      (match_id, code, f"Серия {code[4:].replace('_', ':')}", odd))
            c.execute("INSERT INTO odds_history (match_id, market_code, odds) VALUES (?,?,?)",
                      (match_id, code, odd))
            n += 1
    c.commit()
    c.close()
    return n


def resolve_leg(match_id: int, market_code: str, score1: int, score2: int) -> str:
    """Исход ноги: won/lost (void не возникает на наших рынках при известном счёте)."""
    h, a = score1, score2
    if market_code == "1x2_p1":
        return "won" if h > a else "lost"
    if market_code == "1x2_x":
        return "won" if h == a else "lost"
    if market_code == "1x2_p2":
        return "won" if a > h else "lost"
    if market_code == "tb25":
        return "won" if h + a > 2.5 else "lost"
    if market_code == "tm25":
        return "won" if h + a < 2.5 else "lost"
    if market_code == "btts_yes":
        return "won" if h > 0 and a > 0 else "lost"
    if market_code == "btts_no":
        return "won" if h == 0 or a == 0 else "lost"
    if market_code == "itb_h15":
        return "won" if h > 1.5 else "lost"
    if market_code == "itb_a15":
        return "won" if a > 1.5 else "lost"
    if market_code == "ah_h15":
        return "won" if h - a >= 2 else "lost"
    if market_code == "ah_a15":
        return "won" if a - h >= -1 else "lost"   # +1.5: не проиграть в 2+ мяча
    return "void"
