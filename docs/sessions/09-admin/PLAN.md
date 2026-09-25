# Сессия 9 — Админка в мини-аппе + риск-лимиты

**Читай перед началом:** `docs/plans/01-miniapp-features.md` (раздел 7, admin.js), `docs/plans/03-improvements.md` (п. 8).

## Задачи
- [ ] Включить admin.js-панель по `users.is_admin`: сезоны, риск-лимиты, exposure, алерты (`/api/admin/risk/*`).
- [ ] Свои панели: долги (одобрить/простить), трансферы (одобрить), туры (создать/закрыть), рассылка.
- [ ] Ручной расчёт купона админом (спорные случаи) с баланс-коррекцией (образец — ручной резолв Дракобета).
- [ ] Права: root (ADMIN_IDS) → tournament_admins → капитаны; журнал всех действий в audit log.

## Точный контракт админки оригинала (вытащен из admin.js — фронт ходит ровно сюда)
- `GET  /api/admin/panel/me` — проверка прав
- `GET  /api/admin/panel/dashboard` — сводка
- `GET/POST /api/admin/panel/bets`, `…/bets/{id}`, `…/bets/{id}/void` — разбор/аннулирование ставок
- `GET/POST /api/admin/panel/limits` — лимиты ставок
- `GET  /api/admin/panel/markets`, `POST …/markets/{id}/action` — управление рынками (открыть/закрыть/пауза)
- `POST /api/admin/panel/selections/{id}/odds` — ручная правка кэфа
- `POST /api/admin/panel/pause` — пауза приёма ставок
- `GET  /api/admin/panel/players`, `…/players/{id}`, `POST …/players/{id}/adjust` (правка баланса), `…/ban`, `…/unban`
- `GET  /api/admin/panel/picks`, `…/picks/review` — «пики»/рекомендации
- `GET  /api/admin/risk/exposure|alerts`, `…/risk/limits` (из api.js)
- `GET  /api/admin/season`, `POST /api/admin/season/finalize` — закрытие сезона
- `GET  /api/admin/audit-log` — журнал

## Критерий готовности
Админ создаёт тур, правит лимиты, разбирает спорный купон и трансфер — не выходя из мини-аппа; все действия в журнале.
