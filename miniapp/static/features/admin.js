import { $, api, esc, fmt, hooks, state, toast } from '../lib.js';

/* ===== админ-панель (блок 10) ===== */

export async function openAdmin() {
  try {
    const { dashboard } = await api('/api/admin/dashboard');
    $('#admin-dashboard').innerHTML = [
      ['Юзеров', dashboard.users], ['Купонов', dashboard.open_bets],
      ['Экспозиция', fmt(dashboard.exposure)], ['Долгов', fmt(dashboard.open_debts)],
      ['Спорных', dashboard.disputed_matches], ['Ожидают', dashboard.pending_matches],
    ].map(([k, v]) => `<div class="stat-box"><div class="v">${v}</div><div class="k">${k}</div></div>`).join('');
    renderAdminPause(dashboard);
    renderAdminSeason();
    renderAdminBets();
    renderAdminPlayers();
    renderAdminAudit();
    renderAdminOcr();
    renderAdminSettings();
    hooks.adminCards.forEach((fn) => { try { fn(); } catch (e) { console.error(e); } });
    $('#admin-sheet').hidden = false;
  } catch (e) { toast(e.message); }
}

function renderAdminSeason() {
  const old = document.getElementById('admin-season');
  if (old) old.remove();
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'admin-season';
  card.innerHTML = `<div class="card-title">🏁 Финал сезона</div>
    <div class="sub" style="margin-bottom:8px">Призовые в бюджеты, 3↑/3↓, турнир → архив. Сначала покажу превью.</div>
    <button class="bonus-btn" id="season-btn">Показать итоги сезона</button>
    <div id="season-preview" style="margin-top:8px"></div>`;
  const pauseCard = $('#admin-pause')?.parentElement;
  if (pauseCard) pauseCard.parentElement.insertBefore(card, pauseCard);
  $('#season-btn').onclick = async () => {
    try {
      const r = await api('/api/admin/season/finalize', {
        method: 'POST', body: JSON.stringify({ tournament_id: Number(prompt('ID турнира (лиги):', '1')), confirm: false }) });
      if (r.status === 'preview') {
        $('#season-preview').innerHTML = (r.rows || []).filter((x) => x.prize || x.move).map((x) =>
          `<div class="bet-history-item"><div>${esc(x.club)}</div>
           <div class="sub">${x.prize ? fmt(x.prize) + ' ₼' : ''} ${x.move ? '· ' + esc(x.move) : ''}</div></div>`).join('')
          + '<button class="bonus-btn" id="season-confirm" style="margin-top:8px">✅ Подтвердить финализацию</button>';
        $('#season-confirm').onclick = async () => {
          const tid = Number(prompt('ID турнира ещё раз — подтверди:', '1'));
          const done = await api('/api/admin/season/finalize', {
            method: 'POST', body: JSON.stringify({ tournament_id: tid, confirm: true }) });
          toast(`Сезон закрыт: призовых ${done.prize_rows?.length || 0}, перемещений ${done.moves?.length || 0}`);
          $('#season-preview').innerHTML = '';
        };
      } else { toast('Не лига или не найдена'); }
    } catch (e) { toast(e.message); }
  };
}

function renderAdminPause(dashboard) {
  const el = $('#admin-pause');
  el.innerHTML = `<div class="sub" style="margin-bottom:8px">${dashboard.paused ? '⏸ На паузе: ' + esc(dashboard.paused_reason || '—') : '▶️ Приём ставок активен'}</div>
    <button class="bonus-btn" id="pause-toggle">${dashboard.paused ? 'Снять паузу' : 'Включить паузу'}</button>`;
  $('#pause-toggle').onclick = async () => {
    try {
      await api('/api/admin/pause', { method: 'POST', body: JSON.stringify({ scope: 'global', on: !dashboard.paused, reason: 'админ-панель' }) });
      toast(!dashboard.paused ? 'Пауза включена' : 'Пауза снята');
      openAdmin();
    } catch (e) { toast(e.message); }
  };
}

async function renderAdminBets() {
  const { bets } = await api('/api/admin/bets?status=open');
  $('#admin-bets').innerHTML = bets.length ? bets.slice(0, 10).map((b) => `<div class="bet-history-item">
      <div><div>#${b.id} ${esc(b.username || b.first_name || '')} · ${fmt(b.amount)} × ${b.total_odds}</div>
      <div class="sub">выплата ${fmt(b.potential_win)}</div></div>
      <button class="lot-btn secondary" data-void="${b.id}">Void</button>
    </div>`).join('') : '<div class="empty-note">Открытых купонов нет.</div>';
}

async function renderAdminPlayers(q = '') {
  const el = $('#admin-players');
  if (!el.querySelector('#admin-player-q')) {
    el.innerHTML = `<input class="amount-input" id="admin-player-q" placeholder="Поиск: @ник, имя или ID" style="margin:0 0 8px;font-size:14px">
      <div id="admin-player-list"></div>`;
    let t;
    el.querySelector('#admin-player-q').addEventListener('input', (e) => {
      clearTimeout(t);
      t = setTimeout(() => renderAdminPlayers(e.target.value.trim()), 250);
    });
  }
  const { players } = await api(`/api/admin/players${q ? `?q=${encodeURIComponent(q)}` : ''}`);
  const me = state.user?.user_id;
  $('#admin-player-list').innerHTML = players.slice(0, 30).map((p) => `<div class="bet-history-item">
      <div><div>${esc(p.username ? '@' + p.username : (p.first_name || p.telegram_id))} ${p.is_frozen ? '🧊' : ''} ${p.is_admin ? '👑' : ''}</div>
      <div class="sub">${fmt(p.balance)} дыма · ур. ${p.level} · ID ${p.telegram_id}</div></div>
      <div class="adm-actions">
        <button class="lot-btn secondary" data-ban="${p.telegram_id}">${p.is_frozen ? 'Разбан' : 'Бан'}</button>
        ${p.telegram_id !== me ? `<button class="lot-btn secondary" data-adjust="${p.telegram_id}">±дым</button>` : ''}
        ${state.user?.is_root && p.telegram_id !== me ? `<button class="lot-btn secondary" data-admin-toggle="${p.telegram_id}" data-on="${p.is_admin ? 1 : 0}">${p.is_admin ? 'Снять админа' : 'Сделать админом'}</button>` : ''}
      </div>
    </div>`).join('') || '<div class="empty-note">Никого не нашёл.</div>';
}

/* ===== настройки (bot_settings) ===== */

const SETTING_GROUPS = [
  ['🎟 Ставки', {
    min_bet: 'Мин. ставка, дым', max_bet: 'Макс. ставка, дым', max_payout: 'Макс. выплата, дым',
    max_open_bets: 'Открытых купонов на игрока', max_open_exposure: 'Макс. сумма выплат по открытым', max_legs: 'Событий в экспрессе',
    odds_margin_pct: 'Маржа кэфов, %', notify_bets_dm: 'Итог ставки в ЛС (1/0)', resettle_allow_negative: 'Пересчёт может увести в минус (1/0)',
  }],
  ['⭐ Прогрессия', {
    xp_per_win: 'XP за выигрыш', level_xp_step: 'XP на уровень (×N)', streak_bonus_base: 'Бонус серии: старт',
    streak_bonus_step: 'Бонус серии: шаг', streak_bonus_cap: 'Бонус серии: максимум',
  }],
  ['💱 Трансферы', {
    transfer_deal_threshold: 'Сделка через судью от, ₼', transfer_commission_pct: 'Комиссия, %', free_agent_k: 'Цена агента: K (рейтинг²×K)',
    auction_hours: 'Аукцион, часов', auction_min_step_pct: 'Мин. шаг ставки, %', auction_snipe_minutes: 'Антиснайпинг, мин',
  }],
  ['🏆 Турнир', {
    default_tour_days: 'Дней на тур', dispute_window_hours: 'Окно спора, ч', unplayed_fine: 'Штраф за неигранный, ₼',
    prize_champion: 'Приз чемпиону, ₼', prize_second: 'Приз 2 месту, ₼', prize_third: 'Приз 3 месту, ₼', prize_cup_winner: 'Приз за кубок, ₼',
    training_limit_per_week: 'Тренировок в неделю (лимит)',
  }],
];

async function renderAdminSettings() {
  let card = document.getElementById('admin-settings-card');
  if (!card) {
    card = document.createElement('div');
    card.className = 'card';
    card.id = 'admin-settings-card';
    card.innerHTML = '<div class="card-title">⚙️ Настройки</div><div id="admin-settings"></div>';
    const audit = $('#admin-audit')?.parentElement;
    audit.parentElement.insertBefore(card, audit);
  }
  const el = card.querySelector('#admin-settings');
  let data;
  try { data = await api('/api/admin/settings'); } catch (e) { el.innerHTML = `<div class="empty-note">${esc(e.message)}</div>`; return; }
  const settings = data.settings;
  const known = new Set(SETTING_GROUPS.flatMap(([, g]) => Object.keys(g)));
  known.add('bets_paused'); known.add('bets_paused_reason');
  const other = Object.keys(settings).filter((k) => !known.has(k)).sort();
  const groups = [...SETTING_GROUPS, ...(other.length ? [['🧩 Прочее', Object.fromEntries(other.map((k) => [k, k]))]] : [])];
  el.innerHTML = `<form id="settings-form">${groups.map(([title, keys]) => {
    const rows = Object.entries(keys).filter(([k]) => k in settings);
    if (!rows.length) return '';
    return `<details class="set-group"><summary>${title}</summary>${rows.map(([k, label]) => `
      <label class="set-row"><span>${esc(label)}</span>
        <input class="amount-input" name="${esc(k)}" value="${esc(settings[k])}" inputmode="decimal"></label>`).join('')}</details>`;
  }).join('')}
    <button class="bonus-btn" type="submit" style="margin-top:10px">💾 Сохранить изменения</button></form>`;
  $('#settings-form').onsubmit = async (ev) => {
    ev.preventDefault();
    const changed = {};
    for (const [k, v] of new FormData(ev.target).entries()) if (String(settings[k]) !== v) changed[k] = v;
    if (!Object.keys(changed).length) return toast('Ничего не изменилось');
    try {
      await api('/api/admin/settings', { method: 'POST', body: JSON.stringify({ settings: changed }) });
      toast(`Сохранено: ${Object.keys(changed).length}`);
      renderAdminSettings();
    } catch (e) { toast(e.message, 5000); }
  };
}

async function renderAdminAudit() {
  const { audit } = await api('/api/admin/audit');
  $('#admin-audit').innerHTML = audit.length ? audit.slice(0, 12).map((a) => `<div class="bet-history-item">
      <div>${esc(a.action)}</div><div class="sub">${esc((a.details || '').slice(0, 40))}</div></div>`).join('')
    : '<div class="empty-note">Журнал пуст.</div>';
}

document.addEventListener('click', async (ev) => {
  if (ev.target.id === 'admin-close') { $('#admin-sheet').hidden = true; return; }
  const voidBtn = ev.target.closest('[data-void]');
  if (voidBtn) {
    const reason = prompt('Причина void:') || 'решение админа';
    try {
      await api(`/api/admin/bets/${voidBtn.dataset.void}/void`, { method: 'POST', body: JSON.stringify({ reason }) });
      toast('Возврат выполнен');
      openAdmin();
    } catch (e) { toast(e.message); }
    return;
  }
  const banBtn = ev.target.closest('[data-ban]');
  if (banBtn) {
    const tg = banBtn.dataset.ban;
    const freeze = !banBtn.textContent.startsWith('Разбан');
    const reason = freeze ? (prompt('Причина заморозки:') || 'нарушение правил') : '';
    try {
      await api(`/api/admin/players/${tg}`, { method: 'POST', body: JSON.stringify({ action: freeze ? 'ban' : 'unban', reason }) });
      toast(freeze ? 'Заморожен (баланс цел)' : 'Разбанен');
      openAdmin();
    } catch (e) { toast(e.message); }
    return;
  }
  const admBtn = ev.target.closest('[data-admin-toggle]');
  if (admBtn) {
    const on = admBtn.dataset.on === '1';
    if (!confirm(on ? 'Снять права админа?' : 'Выдать права админа мини-аппа?')) return;
    try {
      await api(`/api/admin/players/${admBtn.dataset.adminToggle}`, { method: 'POST', body: JSON.stringify({ action: on ? 'unmake_admin' : 'make_admin' }) });
      toast(on ? 'Админ снят' : 'Админ назначен');
      renderAdminPlayers($('#admin-player-q')?.value.trim() || '');
    } catch (e) { toast(e.message); }
    return;
  }
  const adjBtn = ev.target.closest('[data-adjust]');
  if (adjBtn) {
    const delta = Number(prompt('Дельта дыма (можно минус):', '100'));
    if (!delta) return;
    try {
      await api(`/api/admin/players/${adjBtn.dataset.adjust}`, { method: 'POST', body: JSON.stringify({ action: 'adjust', delta, reason: 'админ-панель' }) });
      toast('Баланс скорректирован');
      openAdmin();
    } catch (e) { toast(e.message); }
  }
});


/* ===== свои OCR-провайдеры (только root) ===== */

async function renderAdminOcr() {
  const card = $('#admin-ocr-card');
  if (!state.user?.is_root) { card.hidden = true; return; }
  card.hidden = false;
  const el = $('#admin-ocr');
  let data;
  try { data = await api('/api/admin/ocr-providers'); } catch (e) { el.innerHTML = `<div class="empty-note">${esc(e.message)}</div>`; return; }
  const host = (u) => { try { return new URL(u).host; } catch (_) { return u; } };
  el.innerHTML = `
    <div class="sub" style="margin-bottom:10px">OpenAI-совместимые API (<code>/chat/completions</code> с картинкой). Порядок каскада:
      <b>${data.cascade.length ? data.cascade.map(esc).join(' → ') : 'пусто — нет ни одного ключа'}</b></div>
    ${data.providers.map((p) => `<div class="ocr-prov ${p.enabled ? '' : 'off'}">
      <div class="lot-main">
        <div class="lot-name">${esc(p.name)} <span class="sub">· ${p.position === 'first' ? 'первым' : 'после встроенных'}</span></div>
        <div class="lot-sub">${esc(p.model)} · ${esc(host(p.base_url))} · ключ ${p.has_key ? esc(p.key_masked) : 'нет'}</div>
        ${p.last_test ? `<div class="lot-sub">${esc(p.last_test)}</div>` : ''}
      </div>
      <div class="ocr-actions">
        <button class="lot-btn secondary" data-ocr-test="${p.id}">Проверить</button>
        <button class="lot-btn secondary" data-ocr-toggle="${p.id}" data-on="${p.enabled ? 1 : 0}">${p.enabled ? 'Выкл' : 'Вкл'}</button>
        <button class="lot-btn secondary" data-ocr-pos="${p.id}" data-pos="${p.position}">${p.position === 'first' ? '↓' : '↑'}</button>
        <button class="lot-btn secondary" data-ocr-key="${p.id}">Ключ</button>
        <button class="lot-btn secondary" data-ocr-del="${p.id}">✕</button>
      </div>
    </div>`).join('') || '<div class="empty-note" style="padding:10px">Своих провайдеров нет.</div>'}
    <form id="ocr-add" class="ocr-form" autocomplete="off">
      <input class="amount-input" name="name" placeholder="Название (напр. AMD)" maxlength="40">
      <input class="amount-input" name="base_url" placeholder="Base URL: https://…/v1" required>
      <input class="amount-input" name="model" placeholder="Модель: DeepSeek-V4.1-Flash" required>
      <input class="amount-input" name="api_key" type="password" placeholder="API-ключ">
      <select class="amount-input" name="position">
        <option value="first">Первым (до встроенных)</option>
        <option value="last">Запасным (после встроенных)</option>
      </select>
      <button class="bonus-btn" type="submit">➕ Добавить провайдера</button>
    </form>
    <div class="sub" style="margin-top:12px">Встроенные (ключи в bot/.env):
      ${data.builtin.map((b) => `${b.configured ? '✅' : '▫️'} ${esc(b.name)}`).join(' · ')}</div>`;
  $('#ocr-add').onsubmit = async (ev) => {
    ev.preventDefault();
    const body = Object.fromEntries(new FormData(ev.target).entries());
    try {
      const r = await api('/api/admin/ocr-providers', { method: 'POST', body: JSON.stringify(body) });
      toast(`Добавлен: ${r.provider.name}. Жми «Проверить»`);
      renderAdminOcr();
    } catch (e) { toast(e.message); }
  };
}

document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-ocr-test],[data-ocr-toggle],[data-ocr-pos],[data-ocr-key],[data-ocr-del]');
  if (!b) return;
  const d = b.dataset;
  try {
    if (d.ocrTest) {
      b.disabled = true; b.textContent = '⏳…';
      const r = await api(`/api/admin/ocr-providers/${d.ocrTest}/test`, { method: 'POST' });
      toast(`${r.text} · ${(r.ms / 1000).toFixed(1)} с`, 6000);
    } else if (d.ocrToggle) {
      await api(`/api/admin/ocr-providers/${d.ocrToggle}`, { method: 'POST', body: JSON.stringify({ enabled: d.on !== '1' }) });
    } else if (d.ocrPos) {
      await api(`/api/admin/ocr-providers/${d.ocrPos}`, { method: 'POST', body: JSON.stringify({ position: d.pos === 'first' ? 'last' : 'first' }) });
    } else if (d.ocrKey) {
      const key = prompt('Новый API-ключ (пусто — не менять):');
      if (!key) return;
      await api(`/api/admin/ocr-providers/${d.ocrKey}`, { method: 'POST', body: JSON.stringify({ api_key: key }) });
      toast('Ключ обновлён');
    } else if (d.ocrDel) {
      if (!confirm('Удалить провайдера?')) return;
      await api(`/api/admin/ocr-providers/${d.ocrDel}`, { method: 'DELETE' });
    }
  } catch (e) { toast(e.message); }
  renderAdminOcr();
});

hooks.profileCards.push((container) => {
  if (!state.user?.is_admin) return;
  const btn = document.createElement('button');
  btn.id = 'admin-btn';
  btn.className = 'bonus-btn';
  btn.style.marginBottom = '12px';
  btn.textContent = '👮 Админ-панель';
  btn.onclick = openAdmin;
  container.prepend(btn);
});
