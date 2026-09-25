"""БД KURILKA SIGARKI: ядро елобота + лига клубов + ставки мини-аппа.

Пишем чистый SQLite-диалект — db_backend.py (из елобота) сам транслирует
в Postgres, если задан DATABASE_URL. Схема идемпотентная: можно звать на каждом старте.
Схема — план 00 + решения 09 + план 10 (блоки 1, 4–10).
"""
import os

import config
from db_backend import connect  # адаптер SQLite⇄Postgres из елобота

# db_backend читает DATABASE_URL/DB_PATH из env на каждый connect()
os.environ.setdefault("DB_PATH", config.DB_PATH)


def db():
    return connect()


def init_db() -> None:
    c = db()
    c.executescript("""
    -- ===== ядро турнира (перенос из елобота) =====
    CREATE TABLE IF NOT EXISTS players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        username TEXT UNIQUE,
        telegram_id INTEGER UNIQUE,
        game_nickname TEXT,                     -- ник в FC27 (для OCR-сопоставления)
        elo INTEGER DEFAULT 1000,
        goals_scored INTEGER DEFAULT 0,
        goals_conceded INTEGER DEFAULT 0,
        assists INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        banned_until TEXT, ban_reason TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS tournaments (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        season_id INTEGER,
        format TEXT DEFAULT 'league',           -- league/ucl/uel/uecl (лига/ЛЧ/ЛЕ/ЛК)
        tour_mode TEXT DEFAULT 'manual',        -- manual/challenge_place (решение 09: оба)
        rounds INTEGER DEFAULT 1,               -- кругов в лиге: 1 или 2
        tour_days INTEGER DEFAULT 3,            -- длительность тура (кастом создателя)
        stage TEXT DEFAULT 'groups',            -- groups/playoff/finished
        total_tours INTEGER DEFAULT 0,
        current_tour INTEGER DEFAULT 0,
        tours_enabled INTEGER DEFAULT 1,
        is_official INTEGER DEFAULT 1,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS matches (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER,
        home_club_id INTEGER, away_club_id INTEGER,
        home_player_id INTEGER, away_player_id INTEGER,
        score1 INTEGER, score2 INTEGER,
        pens1 INTEGER, pens2 INTEGER,           -- пенальти в скобках (кубки)
        goals_detail TEXT,                      -- JSON: [{name, minute, side, assist, pen}]
        stage TEXT DEFAULT 'group',             -- group/r16/qf/sf/final
        tie_id INTEGER, game_in_tie INTEGER,    -- серия кубка до 2 побед (план 07)
        tour_number INTEGER,
        status TEXT DEFAULT 'pending',          -- pending/reported/disputed/confirmed/cancelled
        source TEXT DEFAULT 'manual',           -- manual/ocr/challenge-place (импортное откатимо)
        external_id TEXT,                       -- id матча на Challenge Place
        elo_before TEXT,                        -- JSON снапшота рейтингов/формы до финализации (для спора)
        deadline TEXT, played_at TEXT,
        screenshot_hash TEXT, screenshot_file_id TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS ties (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, stage TEXT,
        club_a_id INTEGER, club_b_id INTEGER,
        wins_a INTEGER DEFAULT 0, wins_b INTEGER DEFAULT 0,
        winner_club_id INTEGER,                 -- заполняется по завершении серии
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS match_goals (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER, player_id INTEGER, raw_name TEXT,
        minute INTEGER, side TEXT, ord INTEGER,
        is_penalty INTEGER DEFAULT 0, assist_name TEXT
    );
    CREATE TABLE IF NOT EXISTS tournament_elo (
        tournament_id INTEGER, player_id INTEGER,
        elo INTEGER DEFAULT 1000, games INTEGER DEFAULT 0,
        wins INTEGER DEFAULT 0, draws INTEGER DEFAULT 0, losses INTEGER DEFAULT 0,
        PRIMARY KEY (tournament_id, player_id)
    );
    CREATE TABLE IF NOT EXISTS tournament_admins (
        tournament_id INTEGER, telegram_id INTEGER,
        PRIMARY KEY (tournament_id, telegram_id)
    );
    CREATE TABLE IF NOT EXISTS tournament_audit_log (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, actor_telegram_id INTEGER,
        action TEXT, details TEXT, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS processed_screenshots (
        sha256 TEXT, tournament_id INTEGER, match_id INTEGER, reporter_id INTEGER,
        PRIMARY KEY (sha256, tournament_id)
    );
    -- история «3 напоминания в день» не затирается (решение 09: PK с датой)
    CREATE TABLE IF NOT EXISTS reminder_log (
        tournament_id INTEGER, kind TEXT, date TEXT,
        sent_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (tournament_id, kind, date)
    );

    -- ===== слой «Лига клубов» =====
    CREATE TABLE IF NOT EXISTS divisions (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, name TEXT, code TEXT,
        group_chat_id INTEGER, is_active INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS clubs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT NOT NULL,
        logo_file_id TEXT, logo_path TEXT,      -- путь к лого из logovo-copy/src/assets/logos
        division_id INTEGER,
        owner_player_id INTEGER,
        budget INTEGER DEFAULT 0,               -- только трансферы/долги/призовые (решение 09)
        elo INTEGER DEFAULT 1000,
        form TEXT DEFAULT '',                   -- 'WDLLW' — последние 5
        created_at TEXT DEFAULT (datetime('now'))
    );
    -- игрок↔клуб, один клуб на человека; замены не трогают балансы (решение 09)
    CREATE TABLE IF NOT EXISTS club_players (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        player_id INTEGER UNIQUE,               -- players.id
        club_id INTEGER,
        role TEXT DEFAULT 'owner',              -- owner/member
        joined_at TEXT DEFAULT (datetime('now'))
    );
    -- заявка «Запросить клуб» из аппа (статус «ожидает» виден в аппе — решение 09)
    CREATE TABLE IF NOT EXISTS club_requests (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE,
        status TEXT DEFAULT 'pending',          -- pending/approved/rejected
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS tours (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, tour_number INTEGER,
        status TEXT DEFAULT 'open',             -- open/locked/played
        deadline TEXT,
        UNIQUE (tournament_id, tour_number)
    );
    CREATE TABLE IF NOT EXISTS transfers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER,
        from_club_id INTEGER, to_club_id INTEGER,
        card_id INTEGER, player_name TEXT,
        want_card_id INTEGER,                   -- обмен: какую карточку хотим взамен
        amount INTEGER DEFAULT 0,
        status TEXT DEFAULT 'pending',          -- pending/approved/rejected/cancelled
        created_by INTEGER, decided_by INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );
    -- составы клубов и свободные агенты (club_id IS NULL = свободный агент)
    CREATE TABLE IF NOT EXISTS club_cards (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        club_id INTEGER,
        name TEXT NOT NULL, position TEXT, rating INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );
    -- маркетплейс трансферов: лот фикс/аукцион (план 10 блок 8)
    CREATE TABLE IF NOT EXISTS transfer_lots (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER,
        seller_club_id INTEGER, card_id INTEGER,
        kind TEXT DEFAULT 'fix',                -- fix/auction
        price INTEGER, buyout_price INTEGER,    -- для аукциона: текущая ставка / выкуп
        status TEXT DEFAULT 'open',             -- open/sold/cancelled/needs_judge
        buyer_club_id INTEGER, decided_by INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS debts (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        club_id INTEGER, amount INTEGER, reason TEXT,   -- 'тур 7', 'трансфер'
        source TEXT,                                 -- challenge-place / manual
        status TEXT DEFAULT 'open',                  -- open/paid/cancelled
        created_at TEXT DEFAULT (datetime('now'))
    );
    -- ссылка Challenge Place на турнир+тур (решение 09: второй сезон не ломает первый)
    CREATE TABLE IF NOT EXISTS challenge_links (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        tournament_id INTEGER, tour_number INTEGER, url TEXT, parsed_at TEXT,
        UNIQUE (tournament_id, tour_number)
    );

    -- ===== ставки мини-аппа (контракт оригинала + решения 09) =====
    CREATE TABLE IF NOT EXISTS users (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        telegram_id INTEGER UNIQUE, username TEXT, first_name TEXT,
        photo_url TEXT,
        balance INTEGER DEFAULT 422,            -- per план 09 (старт 422, не 677)
        total_wagered INTEGER DEFAULT 0, total_won INTEGER DEFAULT 0,
        bets_count INTEGER DEFAULT 0, bets_won INTEGER DEFAULT 0,
        xp INTEGER DEFAULT 0, level INTEGER DEFAULT 1,
        is_admin INTEGER DEFAULT 0, has_access INTEGER DEFAULT 1,
        is_frozen INTEGER DEFAULT 0,            -- бан-заморозка: баланс цел (решение 09)
        freeze_reason TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS bets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER,
        bet_type TEXT DEFAULT 'single',          -- single/express
        amount INTEGER, total_odds REAL,
        potential_win INTEGER,
        status TEXT DEFAULT 'open',              -- open/won/lost/void
        is_frozen INTEGER DEFAULT 0,             -- матч в споре → купон заморожен
        idempotency_key TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS bet_legs (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        bet_id INTEGER, match_id INTEGER, market_code TEXT,   -- 1x2_p1/dc_1x/tb25/btts_yes...
        odds REAL, result TEXT,                  -- pending/won/lost/void
        settled_at TEXT
    );
    CREATE TABLE IF NOT EXISTS markets (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER, code TEXT, label TEXT, odds REAL,
        is_active INTEGER DEFAULT 1,
        updated_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS odds_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        match_id INTEGER, market_code TEXT, odds REAL,
        recorded_at TEXT DEFAULT (datetime('now'))
    );
    -- экономика дыма: бонус-серия и промокоды (решение 09)
    CREATE TABLE IF NOT EXISTS user_streaks (
        user_id INTEGER PRIMARY KEY,
        streak_days INTEGER DEFAULT 0,
        last_bonus_date TEXT                     -- YYYY-MM-DD; пропуск дня = сброс
    );
    CREATE TABLE IF NOT EXISTS promo_codes (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE NOT NULL,
        amount INTEGER NOT NULL,
        max_activations INTEGER DEFAULT 0,       -- 0 = без лимита
        deadline TEXT,
        is_active INTEGER DEFAULT 1,
        created_by INTEGER,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS promo_activations (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code_id INTEGER, user_id INTEGER,
        activated_at TEXT DEFAULT (datetime('now')),
        UNIQUE (code_id, user_id)
    );
    CREATE TABLE IF NOT EXISTS achievements (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        code TEXT UNIQUE, title TEXT, description TEXT, icon TEXT,
        reward_coins INTEGER DEFAULT 0, reward_xp INTEGER DEFAULT 0
    );
    CREATE TABLE IF NOT EXISTS user_achievements (
        user_id INTEGER, achievement_id INTEGER, unlocked_at TEXT DEFAULT (datetime('now')),
        PRIMARY KEY (user_id, achievement_id)
    );
    CREATE TABLE IF NOT EXISTS saved_coupons (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, legs TEXT, amount INTEGER, created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS favorites (
        user_id INTEGER, match_id INTEGER,
        PRIMARY KEY (user_id, match_id)
    );
    CREATE TABLE IF NOT EXISTS notifications (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, text TEXT, kind TEXT DEFAULT 'app',  -- app = центр уведомлений, bet = расчёт купона
        is_read INTEGER DEFAULT 0, tg_sent INTEGER DEFAULT 0,  -- tg_sent: продублировано в ЛС
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS balance_history (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        user_id INTEGER, delta INTEGER, reason TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );
    CREATE TABLE IF NOT EXISTS bot_settings (
        key TEXT PRIMARY KEY, value TEXT
    );
    -- свои OCR-провайдеры (OpenAI-совместимые), добавляет root из мини-аппа; ключ только тут
    CREATE TABLE IF NOT EXISTS ocr_providers (
        id INTEGER PRIMARY KEY AUTOINCREMENT,
        name TEXT, base_url TEXT NOT NULL, model TEXT NOT NULL, api_key TEXT,
        position TEXT DEFAULT 'first',          -- first/last относительно встроенных облачных
        enabled INTEGER DEFAULT 1, sort_order INTEGER DEFAULT 0,
        last_test TEXT, last_test_at TEXT,
        created_at TEXT DEFAULT (datetime('now'))
    );

    CREATE INDEX IF NOT EXISTS idx_matches_tour ON matches(tournament_id, tour_number);
    CREATE INDEX IF NOT EXISTS idx_matches_stage ON matches(tournament_id, stage);
    CREATE INDEX IF NOT EXISTS idx_markets_match ON markets(match_id);
    CREATE INDEX IF NOT EXISTS idx_bets_user ON bets(user_id, status);
    CREATE INDEX IF NOT EXISTS idx_bet_legs_bet ON bet_legs(bet_id);
    CREATE INDEX IF NOT EXISTS idx_bet_legs_match ON bet_legs(match_id);
    CREATE INDEX IF NOT EXISTS idx_clubs_division ON clubs(division_id);
    CREATE INDEX IF NOT EXISTS idx_club_players_club ON club_players(club_id);
    CREATE INDEX IF NOT EXISTS idx_matches_home ON matches(home_club_id);
    CREATE INDEX IF NOT EXISTS idx_matches_away ON matches(away_club_id);
    CREATE INDEX IF NOT EXISTS idx_transfers_status ON transfers(status);
    CREATE INDEX IF NOT EXISTS idx_transfer_lots_status ON transfer_lots(status);
    CREATE INDEX IF NOT EXISTS idx_notifications_user ON notifications(user_id, is_read);
    CREATE INDEX IF NOT EXISTS idx_odds_history_match ON odds_history(match_id, market_code);
    """)
    _migrate(c)
    c.commit()
    c.close()


def _migrate(c) -> None:
    """Лёгкие миграции для старых баз: CREATE TABLE IF NOT EXISTS не добавляет колонки."""
    from db_backend import column_exists, table_exists

    additions = {
        "matches": [("pens1", "INTEGER"), ("pens2", "INTEGER"), ("tie_id", "INTEGER"),
                    ("game_in_tie", "INTEGER"), ("elo_before", "TEXT"),
                    ("source", "TEXT DEFAULT 'manual'"), ("external_id", "TEXT")],
        "tournaments": [("format", "TEXT DEFAULT 'league'"), ("tour_mode", "TEXT DEFAULT 'manual'"),
                        ("rounds", "INTEGER DEFAULT 1"), ("tour_days", "INTEGER DEFAULT 3")],
        "users": [("is_frozen", "INTEGER DEFAULT 0"), ("freeze_reason", "TEXT"),
                  ("xp", "INTEGER DEFAULT 0"), ("level", "INTEGER DEFAULT 1")],
        "bets": [("is_frozen", "INTEGER DEFAULT 0"), ("idempotency_key", "TEXT")],
        "transfers": [("want_card_id", "INTEGER"), ("tournament_id", "INTEGER"), ("card_id", "INTEGER")],
        "reminder_log": [("date", "TEXT")],
        "challenge_links": [("tournament_id", "INTEGER")],
        "bet_legs": [("settled_at", "TEXT")],
        "notifications": [("tg_sent", "INTEGER DEFAULT 0")],
    }
    for table, cols in additions.items():
        for col, coltype in cols:
            if table_exists(c, table) and not column_exists(c, table, col):
                c.execute(f"ALTER TABLE {table} ADD COLUMN {col} {coltype}")

    # старые базы хранили названия клубов строчными («аякс») — приводим к виду каталога
    if table_exists(c, "clubs"):
        from clubs_catalog import TEAM_LOGO_MAP, display_name
        for canon in TEAM_LOGO_MAP:
            pretty = display_name(canon)
            if pretty != canon:
                c.execute("UPDATE clubs SET name=? WHERE name=?", (pretty, canon))
