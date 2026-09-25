"""Эталоны «Нова тека» (11 скринов, 5 матчей) — ground truth, снятый глазами
2026-09-25 с каждого файла. Используется тестами OCR-конвейера (гейт блока 5).

Серия _19-49-40 — temiyy (Real Betis) vs Rusli (Liverpool), три матча.
Серия _19-49-50 — quete-основа (состав Барселоны) vs KadyrFc, два матча.
"""

ЭТАЛОНЫ: dict[str, dict] = {
    # --- матч 2:3 (90:00) ---
    "photo_1_2026-09-24_19-49-40.jpg": {
        "team_home": "Real Betis", "team_away": "Liverpool",
        "player_home": "temiyy", "player_away": "Rusli",
        "score_home": 2, "score_away": 3, "timer": "90:00", "penalties": None,
        "players": [
            {"side": "home", "name": "Isco", "goals": 1, "assists": 0, "pos": "ЦАЗ"},
            {"side": "home", "name": "Abde", "goals": 1, "assists": 0, "pos": "ЛВ"},
            {"side": "away", "name": "Szoboszlai", "goals": 1, "assists": 0, "pos": "ЦАЗ"},
            {"side": "away", "name": "Ekitiké", "goals": 2, "assists": 0, "pos": "ФРВ"},
        ],
        "goal_events": [],
        "warnings": ["видны не все строки таблицы"],
    },
    # --- матч 3:3, овертайм 120:00, пенальти (5):(4) — ОБЯЗАТЕЛЬНЫЙ КЕЙС ---
    "photo_2_2026-09-24_19-49-40.jpg": {
        "team_home": "Real Betis", "team_away": "Liverpool",
        "player_home": "temiyy", "player_away": "Rusli",
        "score_home": 3, "score_away": 3, "timer": "120:00",
        "penalties": {"home": 5, "away": 4},
        "players": [],
        "goal_events": [
            {"side": "away", "name": "Chiesa", "minute": 18},
            {"side": "away", "name": "Wirtz", "minute": 33},
            {"side": "home", "name": "Antony", "minute": 40},
            {"side": "home", "name": "Isco", "minute": 59},
            {"side": "away", "name": "Wirtz", "minute": 95},
            {"side": "home", "name": "Abde", "minute": 106},
        ],
        "warnings": [],
    },
    "photo_3_2026-09-24_19-49-40.jpg": {
        "team_home": "Real Betis", "team_away": "Liverpool",
        "player_home": "temiyy", "player_away": "Rusli",
        "score_home": 3, "score_away": 3, "timer": "120:00",
        "penalties": {"home": 5, "away": 4},
        "players": [],
        "goal_events": [],
        "warnings": ["видны не все строки таблицы"],
    },
    # --- матч 4:0 (90:00) ---
    "photo_4_2026-09-24_19-49-40.jpg": {
        "team_home": "Real Betis", "team_away": "Liverpool",
        "player_home": "temiyy", "player_away": "Rusli",
        "score_home": 4, "score_away": 0, "timer": "90:00", "penalties": None,
        "players": [],
        "goal_events": [
            {"side": "home", "name": "Parrott", "minute": 22},
            {"side": "home", "name": "Abde", "minute": 34},
            {"side": "home", "name": "Isco", "minute": 51},
            {"side": "home", "name": "Fran García", "minute": 92},
        ],
        "warnings": [],
    },
    "photo_5_2026-09-24_19-49-40.jpg": {
        "team_home": "Real Betis", "team_away": "Liverpool",
        "player_home": "temiyy", "player_away": "Rusli",
        "score_home": 4, "score_away": 0, "timer": "90:00", "penalties": None,
        "players": [{"side": "home", "name": "Fran García", "goals": 1, "assists": 0, "pos": "ЛЗ"}],
        "goal_events": [],
        "warnings": ["видны не все строки таблицы"],
    },
    "photo_6_2026-09-24_19-49-40.jpg": {
        "team_home": "Real Betis", "team_away": "Liverpool",
        "player_home": "temiyy", "player_away": "Rusli",
        "score_home": 4, "score_away": 0, "timer": "90:00", "penalties": None,
        "players": [
            {"side": "home", "name": "Parrott", "goals": 1, "assists": 0, "pos": "ФРВ"},
            {"side": "home", "name": "Isco", "goals": 1, "assists": 0, "pos": "ЦАЗ"},
            {"side": "home", "name": "Antony", "goals": 0, "assists": 2, "pos": "ПВ"},
            {"side": "home", "name": "Abde", "goals": 1, "assists": 0, "pos": "ЛВ"},
        ],
        "goal_events": [],
        "warnings": [],
    },
    # --- quete-основа vs KadyrFc: 3:1 (90:00) ---
    "photo_1_2026-09-24_19-49-50.jpg": {
        "team_home": None, "team_away": None,          # вариант Б: названий команд нет
        "player_home": "quete-основа", "player_away": "KadyrFc",
        "score_home": 3, "score_away": 1, "timer": "90:00", "penalties": None,
        "players": [],
        "goal_events": [
            {"side": "home", "name": "Lamine Yamal", "minute": 18},
            {"side": "away", "name": "Al Khaibari", "minute": 41},
            {"side": "home", "name": "Gordon", "minute": 51},
            {"side": "home", "name": "Gordon", "minute": 69},
        ],
        "warnings": [],
    },
    "photo_2_2026-09-24_19-49-50.jpg": {
        "team_home": None, "team_away": None,
        "player_home": "quete-основа", "player_away": "KadyrFc",
        "score_home": 3, "score_away": 1, "timer": "90:00", "penalties": None,
        "players": [
            {"side": "home", "name": "Lamine Yamal", "goals": 1, "assists": 0, "pos": "ПВ"},
            {"side": "home", "name": "Gordon", "goals": 2, "assists": 1, "pos": "ФРВ"},
        ],
        "goal_events": [],
        "warnings": ["видны не все строки таблицы"],
    },
    # --- quete-основа vs KadyrFc: 5:2 (90:00), два скролла ленты голов + статистика ---
    "photo_3_2026-09-24_19-49-50.jpg": {
        "team_home": None, "team_away": None,
        "player_home": "quete-основа", "player_away": "KadyrFc",
        "score_home": 5, "score_away": 2, "timer": "90:00", "penalties": None,
        "players": [],
        "goal_events": [
            {"side": "home", "name": "Gordon", "minute": 6},
            {"side": "away", "name": "Al Hamdan", "minute": 11},
            {"side": "home", "name": "Lamine Yamal", "minute": 22},
            {"side": "away", "name": "C. Ronaldo", "minute": 49},
            {"side": "home", "name": "Lamine Yamal", "minute": 54},
            {"side": "home", "name": "Raphinha", "minute": 82},
        ],
        "warnings": [],
    },
    "photo_4_2026-09-24_19-49-50.jpg": {
        "team_home": None, "team_away": None,
        "player_home": "quete-основа", "player_away": "KadyrFc",
        "score_home": 5, "score_away": 2, "timer": "90:00", "penalties": None,
        "players": [],
        "goal_events": [
            {"side": "away", "name": "Al Hamdan", "minute": 11},
            {"side": "home", "name": "Lamine Yamal", "minute": 22},
            {"side": "away", "name": "C. Ronaldo", "minute": 49},
            {"side": "home", "name": "Lamine Yamal", "minute": 54},
            {"side": "home", "name": "Raphinha", "minute": 82},
            {"side": "home", "name": "Pedri", "minute": 91},
        ],
        "warnings": [],
    },
    "photo_5_2026-09-24_19-49-50.jpg": {
        "team_home": None, "team_away": None,
        "player_home": "quete-основа", "player_away": "KadyrFc",
        "score_home": 5, "score_away": 2, "timer": "90:00", "penalties": None,
        "players": [
            {"side": "home", "name": "Pedri", "goals": 1, "assists": 1, "pos": "ЦП"},
            {"side": "home", "name": "Lamine Yamal", "goals": 2, "assists": 1, "pos": "ПВ"},
            {"side": "home", "name": "Gordon", "goals": 1, "assists": 1, "pos": "ФРВ"},
            {"side": "home", "name": "Raphinha", "goals": 1, "assists": 2, "pos": "ПВ"},
            {"side": "away", "name": "Al Hamdan", "goals": 1, "assists": 1, "pos": "ФРВ"},
            {"side": "away", "name": "C. Ronaldo", "goals": 1, "assists": 0, "pos": "ФРВ"},
        ],
        "goal_events": [],
        "warnings": [],
    },
}

# пачки скринов одного матча (порядок как присылает юзер) → ожидание после merge
ПАЧКИ = [
    {"files": ["photo_2_2026-09-24_19-49-40.jpg", "photo_3_2026-09-24_19-49-40.jpg"],
     "expect": {"score": (3, 3), "timer": "120:00", "pens": (5, 4), "goals": 6}},
    {"files": ["photo_3_2026-09-24_19-49-50.jpg", "photo_4_2026-09-24_19-49-50.jpg"],
     "expect": {"score": (5, 2), "goals": 7}},   # дедуп скроллов ленты голов
    {"files": ["photo_4_2026-09-24_19-49-40.jpg", "photo_5_2026-09-24_19-49-40.jpg",
               "photo_6_2026-09-24_19-49-40.jpg"],
     "expect": {"score": (4, 0), "goals": 4}},
    {"files": ["photo_1_2026-09-24_19-49-50.jpg", "photo_2_2026-09-24_19-49-50.jpg"],
     "expect": {"score": (3, 1), "goals": 4}},
    {"files": ["photo_1_2026-09-24_19-49-40.jpg"],
     "expect": {"score": (2, 3), "goals": 0}},   # только таблица, ленты нет
]
