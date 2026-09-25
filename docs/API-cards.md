# API карточек игроков (backend `bot/cards.py`, `miniapp/routes_cards.py`)

Статус: реализовано. Все эндпоинты требуют `X-Telegram-Init-Data` (как остальной API).
Ошибки — `{"status":"error","error":"текст по-русски","code":"CODE"}` c HTTP 400/403/404/429.

## Объект карточки `card`
```json
{
  "id": 12, "club_id": 3, "club_name": "Милан",
  "name": "Gareth Bale", "rating": 122, "position": "RW", "alt_positions": ["RM", "ST"],
  "stats": {"pac": 120, "sho": 118, "pas": 110, "dri": 117, "def": 60, "phy": 100},
  "skill_moves": 4, "weak_foot": 4, "foot": "L", "height_cm": 186,
  "nation": "Wales", "league": "LaLiga", "real_club": "Real Madrid", "program": "Icons",
  "image_url": "/media/cards/<32hex>.jpg" | null,
  "renderz_url": "https://renderz.app/player/30919757-bale" | null,
  "verify_status": "pending" | "approved" | "rejected", "verify_note": "текст" | null,
  "checks": {"photo": true, "ocr": true | false | null, "renderz": "match" | "mismatch" | null,
             "renderz_match": {"name": true, "rating": true, "position": true, "all": true} | null},
  "source": "seed" | "owner" | "admin",
  "on_market": false,
  "created_at": "2026-09-25 10:00:00", "updated_at": "..." | null,
  "added_by": 123 | null, "verified_by": 456 | null, "verified_at": "..." | null
}
```
- `position` / `alt_positions` — всегда английские коды FC Mobile:
  `GK CB LB RB LWB RWB CDM CM CAM LM RM LW RW CF ST`. На вход принимаются и русские:
  ВРТ→GK, ЦЗ→CB, ЛЗ→LB, ПЗ→RB, ЛАЗ→LWB, ПАЗ→RWB, ЦОП→CDM, ЦП→CM, ЦАП→CAM, ЛП→LM, ПП→RM,
  ЛВ→LW, ПВ→RW, ФРВ→CF, НАП→ST.
  Старые русские позиции в БД мигрируются в английские при старте (`bot/schema_cards.py`).
- `stats.*`, `skill_moves`, `weak_foot`, `foot`, `height_cm`, `nation`… могут быть `null` (не указаны).
- `checks.ocr`: `null` — фото не распознавалось; `true` — введённые имя/OVR/позиция совпали с OCR
  этого же фото; `false` — расходятся (подсказка судье).
- Торговать (рынок, обмен, подписание агента, тренировки) можно только `approved`.

## Загрузка фото
- `POST /api/cards/upload` multipart, поле `image` (JPEG/PNG/WebP, ≤ 8 МБ, ≤ 4000 px по стороне).
  → `{"token": "<32hex>", "image_url": "/media/cards/<32hex>.jpg"}`.
  Картинка пережимается в JPEG ≤ 1000 px, EXIF удаляется. Неиспользованные загрузки удаляются через 24 ч.
  400 `BAD_IMAGE` / `TOO_BIG`; 429 `RATE_LIMIT` (60 загрузок в час).
- `GET /media/cards/{name}` — отдаёт картинку (без авторизации, имя `[a-f0-9]{32}.jpg`).

## Распознавание карточки (OCR)
- `POST /api/cards/ocr` — multipart `image` ИЛИ JSON `{"token": "..."}` (ранее загруженное фото).
  → `{"token", "image_url", "fields": {name, rating, position, alt_positions[], pac, sho, pas, dri, def,
  phy, skill_moves, weak_foot, foot, height_cm, nation, league, real_club, program},
  "confidence": 0.0-1.0, "warnings": ["..."], "provider": "gemini" | null}`.
  В `fields` только распознанные поля (остальные отсутствуют). Сбой распознавания → 200 с
  `fields: {}` и причиной в `warnings`. 400 — только невалидная картинка. 429 — лимит 20 OCR в час.

## Проверка по RenderZ
- `POST /api/cards/renderz-check` `{"url": "https://renderz.app/player/<id>-<slug>", "fields": {name, rating, position, alt_positions}?}`
  → `{"ok": true, "renderz": {"name", "rating", "position", "nation", "url", "renderz_id"},
  "match": {"name": bool, "rating": bool, "position": bool, "all": bool} | null}`
  (`match` = null, если `fields` не передан). 400 `{"error"}` — плохая ссылка / страница недоступна /
  проверка выключена админом (`renderz_verify=0`). Хосты: `renderz.app`, `www.renderz.app`,
  `fifarenderz.com`, `www.fifarenderz.com`; только https и путь `/player/<id>[-slug]`.
  Бот скачивает одну страницу только по действию пользователя, кэш 24 ч.
  Имя сравнивается без регистра/диакритики, с транслитом кириллицы («Мбаппе» = «Mbappé»), фамилия
  целиком засчитывается («Mbappe» = «Kylian Mbappé»); порог схожести 0.85. Кириллица, которая не
  транслитерируется в оригинальное написание («Бейл» ≠ «Bale»), не совпадёт → карточка уйдёт судье.
  429 — не больше 30 проверок в час на пользователя.

## Карточки
- `GET /api/cards/my` → `{"club": {"id","name"} | null, "cards": [card...], "can_edit": bool,
  "squad_max": int, "is_admin": bool, "require_approval": bool}`.
- `POST /api/cards` → `{"card": card, "warnings": []}`. Тело:
  `{club_id?, name, rating, position, alt_positions ("LW,ST" или ["LW","ST"]), pac, sho, pas, dri, def, phy,
  skill_moves, weak_foot, foot ("L"/"R"), height_cm, nation, league, real_club, program, token?, renderz_url?}`.
  `club_id` — только админ (ключ передан и `null` = свободный агент); без ключа — свой клуб.
  Валидация: имя 2–40, OVR 40–150, статы 0–200, финты/слабая нога 1–5, рост 140–230.
  Владелец: только пока открыто редактирование составов (`squad_edit_open=1`), лимит `squad_max_cards`.
  Статус: админ → approved; владелец → pending (approved, если `cards_require_approval=0` или
  RenderZ совпал полностью и `cards_auto_approve_renderz=1`). Дубль (имя+OVR+позиция в том же клубе) → 400 `DUP`.
- `POST /api/cards/{id}` — правка, те же поля (частично). Владелец — только pending/rejected
  (после правки снова pending); админ — всегда. → `{"card", "warnings"}`.
- `DELETE /api/cards/{id}` → `{"ok": true}`.
- `GET /api/cards/{id}` → `{"card", "history": [{"date","from_club","to_club","amount","status","kind"}],
  "can_edit", "can_delete", "can_decide", "owner": {"telegram_id","username","game_nickname"} | null}`.
  `kind`: `created | free_agent | fix | auction | exchange | deal`; история — новые сверху, `created` последним.
- `GET /api/cards/queue` → `{"cards": [card + "owner": {"telegram_id","username"} | null]}` — pending-карточки,
  которые зритель может решить (админ — все; судья — клубы своих турниров; свой клуб — никогда).
- `POST /api/cards/{id}/decide` `{"approve": bool, "note": "причина"}` → `{"card"}`.

## Рынок
`GET /api/transfers/market?q=&position=&alt=1&ovr_min=&ovr_max=&price_min=&price_max=&kind=fix|auction|agent&verified=1&nation=&league=&sort=price_asc|price_desc|ovr_desc|new|ending&pac_min=&sho_min=&pas_min=&dri_min=&def_min=&phy_min=`
(старые `position`, `min_rating`, `max_price` работают).
→ `{"lots": [...], "free_agents": [...], "facets": {"positions": [], "nations": [], "leagues": []}}`.
- `lots[]` — прежние поля лота (`id` лота, `card_id`, `kind`, `price`, `buyout_price`, `seller_club_id`,
  `seller_name`, `effective_price`, для аукциона `seconds_left/current_price/min_bid`…) + поля карточки
  (`name, rating, position, alt_positions, stats, skill_moves, weak_foot, foot, height_cm, nation, league,
  real_club, program, image_url, renderz_url, verify_status, checks, source`).
- `free_agents[]` — объект `card` + `effective_price` + `seller_name: null`.
- На рынке только `approved`. `q` — поиск по имени/клубу/сборной/лиге/программе (без учёта регистра и
  диакритики). `position` — рус/англ код; `alt=1` — ещё и по доп. позициям. `verified=1` — только
  подтверждённые RenderZ (`checks.renderz == "match"`). `kind=agent` — только свободные агенты,
  `fix|auction` — только лоты. Сортировка по умолчанию `new`; `ending` — аукционы по времени окончания.
  Цена свободного агента = OVR² × K. Лимит — 100 записей на список. Плохое число в фильтре → 400 `BAD_FILTER`.
- `facets` — по всем карточкам рынка до фильтров.

## Настройки (bot_settings, админка)
`squad_edit_open` 1, `squad_max_cards` 60, `cards_require_approval` 1, `cards_auto_approve_renderz` 1,
`renderz_verify` 1.
