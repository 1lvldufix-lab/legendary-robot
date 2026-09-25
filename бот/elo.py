"""ELO rating engine для турнира KURILKA SIGARKI.

Players start at 1000 ELO (per план 09). K-factor scales with goal difference
and player activity. Формула и K-факторы не менялись (план 10 блок 1) —
перекалиброваны только ранги под 1000-базис.
"""

K_BASE = 32
K_NEW_PLAYER_GAMES = 10   # higher K for first N confirmed matches
K_NEW = 48

# Lower bound for ELO. Allow negative ELO (losing streak) but not unbounded.
ELO_FLOOR = -500


def expected_score(rating_a: float, rating_b: float) -> float:
    """Expected score for player A against player B (0–1)."""
    return 1 / (1 + 10 ** ((rating_b - rating_a) / 400))


def goal_factor(gf: int, ga: int) -> float:
    """
    Multiplier based on goal difference (similar to FIFA World Ranking).
    Encourages high-scoring wins but doesn't distort ELO wildly.
    """
    diff = abs(gf - ga)
    if diff == 0:
        return 1.0
    if diff == 1:
        return 1.0
    if diff == 2:
        return 1.1
    if diff <= 4:
        return 1.0 + (diff - 1) * 0.1
    return 1.5  # cap at 1.5 for landslide wins


def compute_elo_change(
    rating_a: float,
    rating_b: float,
    score_a: int,
    score_b: int,
    games_a: int = 99,
    games_b: int = 99,
) -> tuple[float, float]:
    """
    Returns (new_elo_a, new_elo_b).
    games_a / games_b = total confirmed matches played (for K scaling).
    """
    ea = expected_score(rating_a, rating_b)
    eb = 1 - ea

    if score_a > score_b:
        sa, sb = 1.0, 0.0
    elif score_a < score_b:
        sa, sb = 0.0, 1.0
    else:
        sa, sb = 0.5, 0.5

    gf = goal_factor(score_a, score_b)
    ka = K_NEW if games_a < K_NEW_PLAYER_GAMES else K_BASE
    kb = K_NEW if games_b < K_NEW_PLAYER_GAMES else K_BASE

    delta_a = round(ka * gf * (sa - ea), 2)
    delta_b = round(kb * gf * (sb - eb), 2)

    new_a = max(ELO_FLOOR, rating_a + delta_a)
    new_b = max(ELO_FLOOR, rating_b + delta_b)

    return new_a, new_b


def rank_label(elo: float) -> str:
    """Ранги от 1000-базиса (план 10 блок 1): Окурок <1050, Пепел 1050–1149,
    Сигарета 1150–1249, Сигара 1250–1399, Легенда ≥1400."""
    if elo >= 1400:
        return "Легенда"
    if elo >= 1250:
        return "Сигара"
    if elo >= 1150:
        return "Сигарета"
    if elo >= 1050:
        return "Пепел"
    return "Окурок"
