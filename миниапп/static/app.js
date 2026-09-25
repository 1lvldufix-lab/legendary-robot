/* KURILKA SIGARKI — SPA ядра (блоки 6–7). Валюта «дым», только русский. */

const tg = window.Telegram?.WebApp;
tg?.ready();
tg?.expand();

const state = {
  user: null,
  divisions: [],
  lineMatches: [],
  favorites: new Set(JSON.parse(localStorage.getItem('favorites') || '[]')),
  coupon: JSON.parse(localStorage.getItem('coupon') || '[]'),
  currentDivision: null,
  matchDetail: null,
};

/* ===== API ===== */

async function api(endpoint, options = {}) {
  // dev-фолбэк для браузерных тестов вне Telegram: localStorage 'dev_initdata'
  // хранит заранее подписанную строку — сервер всё равно проверяет подпись и окно 24 ч.
  const initData = tg?.initData || localStorage.getItem('dev_initdata') || '';
  const res = await fetch(endpoint, {
    ...options,
    headers: {
      'Content-Type': 'application/json',
      'X-Telegram-Init-Data': initData,
      ...(options.headers || {}),
    },
  });
  let data = null;
  try { data = await res.json(); } catch (_) { /* proxy/html */ }
  if (!res.ok || !data || typeof data !== 'object') {
    data = data && typeof data === 'object' ? data : {};
    if (res.status === 403) {
      showLockdown(data.reason);
    }
    const err = new Error(data.error || data.message || `Ошибка сервера (${res.status})`);
    err.code = data.code;
    err.status = res.status;
    throw err;
  }
  return data;
}

/* ===== утилиты ===== */

const $ = (sel) => document.querySelector(sel);
const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));

function toast(text, ms = 2600) {
  const el = $('#toast');
  el.textContent = text;
  el.hidden = false;
  clearTimeout(toast._t);
  toast._t = setTimeout(() => { el.hidden = true; }, ms);
}

function logoHtml(club, cls = '') {
  if (club?.logo) return `<img src="${esc(club.logo)}" alt="" class="${cls}">`;
  return `<span class="mc-fallback ${cls}">🛡️</span>`;
}

function fmt(n) { return Number(n ?? 0).toLocaleString('ru-RU'); }

function showLockdown(reason) {
  $('#lockdown-reason').textContent = reason || '';
  $('#app-lockdown-screen').hidden = false;
  document.querySelector('.bottom-nav').style.display = 'none';
  document.querySelector('.views-container').style.display = 'none';
  document.querySelector('.app-header').style.display = 'none';
  $('#betbar').hidden = true;
}

/* ===== купон (локальное состояние) ===== */

function couponAdd(match, market) {
  if (state.frozen) return;
  const same = state.coupon.findIndex((l) => l.match_id === match.id);
  if (same >= 0 && state.coupon[same].market_code === market.code) {
    state.coupon.splice(same, 1);
  } else if (same >= 0) {
    state.coupon[same] = {
      match_id: match.id, market_code: market.code, label: market.label, odds: market.odds,
      match_label: `${match.home.name} — ${match.away.name}`,
    };
  } else {
    const limits = state.user?.bet_limits || {};
    if (state.coupon.length >= (limits.max_legs || 5)) {
      toast(`Экспресс — максимум ${limits.max_legs || 5} ног`);
      return;
    }
    state.coupon.push({
      match_id: match.id, market_code: market.code, label: market.label, odds: market.odds,
      match_label: `${match.home.name} — ${match.away.name}`,
    });
  }
  saveCoupon();
  renderAll();
}

function saveCoupon() {
  localStorage.setItem('coupon', JSON.stringify(state.coupon));
}

function couponTotals(amount) {
  const odds = state.coupon.reduce((acc, l) => acc * l.odds, 1);
  const maxPayout = state.user?.bet_limits?.max_payout || 10000;
  const raw = Math.floor(amount * odds);
  const payout = Math.min(raw, maxPayout);
  return { odds, raw, payout, trimmed: raw > maxPayout };
}

/* ===== рендеры ===== */

function renderHeader() {
  $('#balance-value').textContent = fmt(state.user?.balance);
}

function renderLine() {
  const wrap = $('#line-matches');
  if (!state.lineMatches.length) {
    wrap.innerHTML = '<div class="empty-note">Открытых матчей нет — линия появится, когда root откроет тур.</div>';
    return;
  }
  wrap.innerHTML = state.lineMatches.map((m) => {
    const isFav = state.favorites.has(m.id);
    const main = m.markets.filter((k) => ['1x2_p1', '1x2_x', '1x2_p2'].includes(k.code));
    const sel = (code) => state.coupon.find((l) => l.match_id === m.id && l.market_code === code);
    return `<div class="match-card">
      <div class="mc-top">
        <span>Тур ${m.tour_number ?? '—'} · ${esc(m.tournament_name || '')}</span>
        <button class="fav-star ${isFav ? 'on' : ''}" data-fav="${m.id}">${isFav ? '★' : '☆'}</button>
      </div>
      <div class="mc-teams">
        <div class="mc-team">${logoHtml(m.home)}<span>${esc(m.home?.name || '—')}</span></div>
        <div class="mc-score">${m.status === 'confirmed' ? `${m.score_home}:${m.score_away}` : 'VS'}</div>
        <div class="mc-team right"><span>${esc(m.away?.name || '—')}</span>${logoHtml(m.away)}</div>
      </div>
      <div class="mc-markets">
        ${main.map((k) => `<button class="odd-btn ${sel(k.code) ? 'selected' : ''}" data-match="${m.id}" data-code="${k.code}">
            <span class="lbl">${esc(k.label)}</span><span class="val">${k.odds}</span>
          </button>`).join('')}
      </div>
    </div>`;
  }).join('');
}

function renderTables() {
  // чипы дивизионов
  const chips = $('#division-chips');
  chips.innerHTML = state.divisions.map((d) =>
    `<button class="chip ${state.currentDivision == d.id ? 'active' : ''}" data-div="${d.id}">${esc(d.name)}</button>`).join('')
    || '<span class="sub">Дивизионов пока нет.</span>';

  const sw = $('#standings-wrap');
  sw._data = sw._data || {};
  if (sw._currentDiv !== state.currentDivision) {
    sw._currentDiv = state.currentDivision;
    sw.innerHTML = '<div class="empty-note">Загружаю таблицу…</div>';
    api(`/api/standings?division_id=${state.currentDivision}`)
      .then(({ standings }) => {
        sw._data[state.currentDivision] = standings;
        sw.innerHTML = standingsTable(standings);
      })
      .catch((e) => { sw.innerHTML = `<div class="empty-note">${esc(e.message)}</div>`; });
  } else if (sw._data[state.currentDivision]) {
    sw.innerHTML = standingsTable(sw._data[state.currentDivision]);
  }
}

function standingsTable(rows) {
  if (!rows?.length) return '<div class="empty-note">В дивизионе ещё нет клубов.</div>';
  return `<table class="standings">
    <tr><th></th><th>Клуб</th><th>И</th><th>В</th><th>Н</th><th>П</th><th>М</th><th>О</th></tr>
    ${rows.map((r) => `<tr class="${r.position <= 3 ? 'trpromo' : (r.position >= rows.length - 2 ? 'trreleg' : '')}">
      <td class="pos">${r.position}</td>
      <td><div class="team-cell">${logoHtml(r)}<span>${esc(r.name)}</span></div></td>
      <td>${r.games}</td><td>${r.wins}</td><td>${r.draws}</td><td>${r.losses}</td>
      <td>${r.gf}:${r.ga}</td><td class="pts">${r.points}</td>
    </tr>`).join('')}
  </table>`;
}

function renderCoupon() {
  const body = $('#coupon-body');
  if (!state.coupon.length) {
    body.innerHTML = '<div class="coupon-empty">Купон пуст.<br>Выбери исход на линии 🔥</div>';
    return;
  }
  const limits = state.user?.bet_limits || {};
  const min = limits.min_bet ?? 10, max = limits.max_bet ?? 50000;
  const amount = couponAmount();
  const t = couponTotals(amount);
  body.innerHTML = `
    ${state.coupon.map((l, i) => `<div class="coupon-leg">
      <div><div class="leg-main">${esc(l.label)}</div><div class="leg-sub">${esc(l.match_label)}</div></div>
      <div style="display:flex;align-items:center;gap:8px">
        <span class="leg-odd">${l.odds}</span>
        <button class="leg-remove" data-rm="${i}">✕</button>
      </div>
    </div>`).join('')}
    <div class="coupon-summary">
      <div class="coupon-row"><span>${state.coupon.length > 1 ? `Экспресс (${state.coupon.length} ног)` : 'Ординар'}</span><span>${t.odds.toFixed(2)}</span></div>
      <input class="amount-input" id="coupon-amount" type="number" min="${min}" max="${max}" value="${amount}">
      <div class="coupon-row"><span>Возможная выплата</span><span>${fmt(t.payout)} дыма</span></div>
      ${t.trimmed ? `<div class="coupon-row"><span class="trim">Обрезано по лимиту выплаты (${fmt(limits.max_payout)})</span></div>` : ''}
      <button class="place-btn" id="place-bet">Поставить</button>
    </div>`;
}

function couponAmount() {
  const el = $('#coupon-amount');
  const limits = state.user?.bet_limits || {};
  return el ? Number(el.value) : (limits.min_bet ?? 10);
}

function renderProfile() {
  api('/api/progression').then((p) => {
    const pct = Math.min(100, Math.round((p.xp / Math.max(1, p.xp_needed)) * 100));
    $('#profile-body').innerHTML = `
      <div class="profile-hero">
        <div class="ph-top">
          <div class="avatar">${state.user?.photo_url ? `<img src="${esc(state.user.photo_url)}" width="46" style="border-radius:50%">` : '👤'}</div>
          <div>
            <div class="ph-name">${esc(state.user?.first_name || 'Игрок')}</div>
            <div class="ph-rank">${esc(p.rank)} · уровень ${p.level}</div>
          </div>
        </div>
        <div class="xp-bar"><div class="xp-fill" style="width:${pct}%"></div></div>
        <div class="xp-label"><span>${p.xp} XP</span><span>до уровня ${p.level + 1}: ${Math.max(0, p.xp_needed - p.xp)} XP</span></div>
        <div class="stat-grid">
          <div class="stat-box"><div class="v">${fmt(p.balance)}</div><div class="k">дым</div></div>
          <div class="stat-box"><div class="v">${p.bets_count}</div><div class="k">ставок</div></div>
          <div class="stat-box"><div class="v">${fmt(p.total_won)}</div><div class="k">выиграно</div></div>
        </div>
      </div>
      <div class="card">
        <div class="card-title">🎁 Бонус за серию входов</div>
        <div class="sub" style="margin-bottom:9px">Серия: ${p.streak_days} дн. (50 → +10/день, кап 150)</div>
        <button class="bonus-btn" id="streak-btn">Забрать бонус</button>
      </div>
      <div class="card">
        <div class="card-title">🎟 Промокод</div>
        <div class="promo-row">
          <input class="amount-input" id="promo-code" placeholder="КОД" style="margin:0">
          <button class="bonus-btn" id="promo-btn" style="width:auto;padding:12px 16px">Ввести</button>
        </div>
      </div>
      <div class="card" id="bets-history-card">
        <div class="card-title">История ставок</div>
        <div class="empty-note">Загрузка…</div>
      </div>`;
    $('#streak-btn').onclick = () => claimStreak();
    $('#promo-btn').onclick = () => applyPromo();
    if (state.user.is_admin && !document.getElementById('admin-btn')) {
      const btn = document.createElement('button');
      btn.id = 'admin-btn';
      btn.className = 'bonus-btn';
      btn.style.marginBottom = '12px';
      btn.textContent = '👮 Админ-панель';
      btn.onclick = openAdmin;
      $('#profile-body').prepend(btn);
    }
    api('/api/predictions?limit=10').then(({ predictions }) => {
      const el = $('#bets-history-card');
      el.querySelector('.empty-note')?.remove();
      el.insertAdjacentHTML('beforeend', predictions.length ? predictions.map((b) => `
        <div class="bet-history-item">
          <div><div>${b.bet_type === 'express' ? `Экспресс ×${b.legs}` : 'Ординар'} · ${fmt(b.amount)}</div>
          <div class="sub">${b.legs_label || ''}</div></div>
          <div class="bh-status ${b.status}">${({won: '+' + fmt(b.payout ?? 0), lost: 'проигрыш', void: 'возврат', open: 'в игре'})[b.status] || b.status}</div>
        </div>`).join('') : '<div class="empty-note">Ставок ещё нет.</div>');
    }).catch(() => {});
  }).catch((e) => { $('#profile-body').innerHTML = `<div class="empty-note">${esc(e.message)}</div>`; });
}

function renderClub() {
  api('/api/cabinet/overview').then((data) => {
    const el = $('#club-body');
    if (!data.club) {
      el.innerHTML = `
        <div class="card club-hero">
          <div style="font-size:40px">🛡️</div>
          <div class="club-name">Клуба нет</div>
          <div class="club-division" style="margin-bottom:14px">Попроси root выдать клуб — или оставь заявку</div>
          ${data.club_request === 'pending'
            ? '<button class="req-btn" disabled>Заявка отправлена — ждём</button>'
            : '<button class="req-btn" id="req-club">Запросить клуб</button>'}
        </div>`;
      $('#req-club')?.addEventListener('click', requestClub);
      return;
    }
    const cl = data.club;
    el.innerHTML = `
      <div class="card club-hero">
        ${logoHtml(cl)}
        <div class="club-name">${esc(cl.name)}</div>
        <div class="club-division">${esc(cl.division || '—')} · Elo ${cl.elo}</div>
        <div class="form-letters" style="justify-content:center;margin-top:10px">
          ${(cl.form || '').split('').map((f) => `<span class="${f}">${f}</span>`).join('') || '<span class="sub">форма не набрана</span>'}
        </div>
      </div>
      <div class="card"><div class="stat-grid">
        <div class="stat-box"><div class="v">${fmt(cl.budget)}</div><div class="k">бюджет ₼</div></div>
        <div class="stat-box"><div class="v">${cl.squad_size}</div><div class="k">карт в составе</div></div>
      </div></div>
      <div class="card" id="squad-card"><div class="card-title">Состав</div><div class="empty-note">Загрузка…</div></div>`;
    api('/api/cabinet/squad').then(({ squad }) => {
      const sc = $('#squad-card');
      sc.querySelector('.empty-note')?.remove();
      sc.insertAdjacentHTML('beforeend', squad.length ? squad.map((p) => `
        <div class="squad-item">
          <div><span class="squad-pos">${esc(p.position || '—')}</span>${esc(p.name)}</div>
          <div class="squad-rating">${p.rating}</div>
        </div>`).join('') : '<div class="empty-note">Состав пуст — трансферы появятся в блоке 8.</div>');
    });
  }).catch((e) => { $('#club-body').innerHTML = `<div class="empty-note">${esc(e.message)}</div>`; });
}

/* ===== карточка матча ===== */

async function openMatch(matchId) {
  const data = await api(`/api/matches/${matchId}`);
  state.matchDetail = data;
  const md = $('#match-detail');
  const hist = {};
  for (const h of data.odds_history || []) {
    (hist[h.market_code] = hist[h.market_code] || []).push(h.odds);
  }
  const oddsSpark = (code) => {
    const arr = hist[code] || [];
    if (arr.length < 2) return '';
    return `<div class="sub">Кэф двигался: ${arr.map((o) => o).join(' → ')}</div>`;
  };
  md.innerHTML = `
    <div class="mc-teams" style="margin-top:6px">
      <div class="mc-team">${logoHtml(data.home)}<span>${esc(data.home?.name || '—')}</span></div>
      <div class="mc-score">${data.status === 'confirmed'
        ? `${data.score_home}:${data.score_away}${data.pens_home != null ? `<span class="pens"> (${data.pens_home}:${data.pens_away})</span>` : ''}` : 'VS'}</div>
      <div class="mc-team right"><span>${esc(data.away?.name || '—')}</span>${logoHtml(data.away)}</div>
    </div>
    <div class="form-letters" style="margin-top:10px">
      <span class="sub" style="margin-right:4px">форма:</span>
      ${(data.home?.form || '').split('').map((f) => `<span class="${f}">${f}</span>`).join('')}
      <span style="width:10px"></span>
      ${(data.away?.form || '').split('').map((f) => `<span class="${f}">${f}</span>`).join('')}
    </div>
    ${data.goals?.length ? `<div style="margin-top:12px">${data.goals.map((g) => `
      <div class="goal-row"><span>${g.minute != null ? g.minute + "'" : '•'} ${esc(g.raw_name)}</span>
      <span class="sub">${g.side === 'home' ? esc(data.home?.name || 'дома') : esc(data.away?.name || 'гости')}</span></div>`).join('')}</div>` : ''}
    <div class="section-title" style="font-size:16px;margin-top:14px">Рынки</div>
    <div class="mc-markets" style="flex-wrap:wrap">
      ${data.markets.map((k) => `<button class="odd-btn" style="min-width:90px" data-match="${data.id}" data-code="${k.code}">
        <span class="lbl">${esc(k.label)}</span><span class="val">${k.odds}</span></button>`).join('')}
    </div>
    ${data.markets.map((k) => oddsSpark(k.code)).filter(Boolean).join('')}
  `;
  $('#match-sheet').hidden = false;
}

/* ===== действия ===== */

async function claimStreak() {
  try {
    const r = await api('/api/bonus/streak', { method: 'POST' });
    state.user.balance = r.balance;
    renderHeader();
    toast(`+${r.amount} дыма! Серия: ${r.streak_days} дн.`);
    renderProfile();
  } catch (e) { toast(e.message); }
}

async function applyPromo() {
  const code = $('#promo-code')?.value.trim();
  if (!code) return toast('Введи код');
  try {
    const r = await api('/api/promo/redeem', { method: 'POST', body: JSON.stringify({ code }) });
    state.user.balance = r.balance;
    renderHeader();
    toast(`+${r.amount} дыма по промокоду!`);
  } catch (e) { toast(e.message); }
}

async function requestClub() {
  try {
    const r = await api('/api/club-request', { method: 'POST' });
    toast(r.status === 'pending' ? 'Заявка отправлена root — ждём' : 'Заявка уже есть: ' + r.status);
    renderClub();
  } catch (e) { toast(e.message); }
}

async function placeBet() {
  const amount = couponAmount();
  const limits = state.user?.bet_limits || {};
  if (!amount || amount < (limits.min_bet ?? 10)) return toast(`Минимум ${limits.min_bet} дыма`);
  if (amount > (limits.max_bet ?? 50000)) return toast(`Максимум ${limits.max_bet} дыма`);
  try {
    const idem = crypto.randomUUID?.() || String(Date.now());
    const r = await api('/api/predictions', {
      method: 'POST',
      body: JSON.stringify({ amount, selections: state.coupon, idempotency_key: idem }),
    });
    state.user.balance = r.balance;
    state.coupon = [];
    saveCoupon();
    renderHeader();
    renderCoupon();
    updateBetbar();
    toast('Ставка принята! 🔥');
  } catch (e) {
    if (e.code === 'ODDS_CHANGED') toast('Кэф изменился — обнови купон');
    else toast(e.message);
  }
}

/* ===== избранное ===== */

async function toggleFav(matchId, btn) {
  const on = state.favorites.has(matchId);
  try {
    if (on) {
      await api(`/api/favorites/${matchId}`, { method: 'DELETE' });
      state.favorites.delete(matchId);
    } else {
      await api('/api/favorites', { method: 'POST', body: JSON.stringify({ match_id: matchId }) });
      state.favorites.add(matchId);
    }
    localStorage.setItem('favorites', JSON.stringify([...state.favorites]));
    btn.textContent = on ? '☆' : '★';
    btn.classList.toggle('on', !on);
  } catch (e) { toast(e.message); }
}

/* ===== уведомления ===== */

async function openNotifications() {
  const data = await api('/api/notifications');
  $('#notif-list').innerHTML = data.notifications.length
    ? data.notifications.map((n) => `<div class="bet-history-item"><div>${esc(n.text)}</div><div class="sub">${esc((n.created_at || '').slice(5, 16))}</div></div>`).join('')
    : '<div class="empty-note">Пока пусто.</div>';
  $('#notif-sheet').hidden = false;
  $('#bell-badge').hidden = data.unread === 0;
  api('/api/notifications/read', { method: 'POST' }).then(() => { $('#bell-badge').hidden = true; }).catch(() => {});
}

/* ===== betbar ===== */

function updateBetbar() {
  const bar = $('#betbar');
  if (!state.coupon.length) { bar.hidden = true; return; }
  const t = couponTotals(couponAmount());
  $('#betbar-count').textContent = state.coupon.length;
  $('#betbar-odds').textContent = t.odds.toFixed(2);
  bar.hidden = false;
}

/* ===== переключение экранов ===== */

function showView(name) {
  document.querySelectorAll('.view').forEach((v) => v.classList.remove('active'));
  $(`#view-${name}`).classList.add('active');
  document.querySelectorAll('.nav-btn').forEach((b) => b.classList.toggle('active', b.dataset.view === name));
  $('#betbar').hidden = !(name === 'coupon' && state.coupon.length) && !(name === 'line' && state.coupon.length);
  if (name === 'tables') renderTables();
  if (name === 'coupon') { renderCoupon(); $('#betbar').hidden = true; }
  if (name === 'profile') renderProfile();
  if (name === 'club') renderClub();
  if (name === 'transfers') renderTransfers();
}

function renderAll() {
  renderHeader();
  renderLine();
  renderCoupon();
  updateBetbar();
}

/* ===== init ===== */

async function init() {
  try {
    const boot = await api('/api/bootstrap');
    state.user = boot.user;
    state.divisions = boot.divisions;
    state.currentDivision = boot.divisions[0]?.id ?? null;
    document.documentElement.dataset.theme = localStorage.getItem('theme') || 'emerald';

    const [line, favs] = await Promise.all([
      api('/api/line'),
      api('/api/favorites'),
    ]);
    state.lineMatches = line.matches;
    state.favorites = new Set(favs.favorites);
    const notif = await api('/api/notifications');
    $('#bell-badge').hidden = !notif.unread;

    renderAll();
  } catch (e) {
    if (e.status !== 401) toast('Не удалось загрузиться: ' + e.message);
  }
}

/* ===== события ===== */

document.addEventListener('click', (ev) => {
  const nav = ev.target.closest('.nav-btn');
  if (nav) return showView(nav.dataset.view);

  const fav = ev.target.closest('[data-fav]');
  if (fav) return toggleFav(Number(fav.dataset.fav), fav);

  const odd = ev.target.closest('[data-match][data-code]');
  if (odd) {
    const m = state.lineMatches.find((x) => x.id === Number(odd.dataset.match))
      || (state.matchDetail?.id === Number(odd.dataset.match) ? state.matchDetail : null);
    const k = m?.markets.find((x) => x.code === odd.dataset.code);
    if (m && k) { couponAdd(m, k); renderLine(); renderCoupon(); }
    return;
  }

  const card = ev.target.closest('.match-card');
  if (card && !ev.target.closest('button')) {
    const favBtn = card.querySelector('[data-fav]');
    if (favBtn) openMatch(Number(favBtn.dataset.fav));
    return;
  }

  const rm = ev.target.closest('[data-rm]');
  if (rm) {
    state.coupon.splice(Number(rm.dataset.rm), 1);
    saveCoupon(); renderCoupon(); renderLine(); updateBetbar();
    return;
  }

  const chip = ev.target.closest('[data-div]');
  if (chip) {
    state.currentDivision = Number(chip.dataset.div);
    $('#standings-wrap')._currentDiv = null;
    renderTables();
    return;
  }
});

$('#betbar').addEventListener('click', () => showView('coupon'));
$('#bell-btn').addEventListener('click', openNotifications);
$('#notif-close').addEventListener('click', () => { $('#notif-sheet').hidden = true; });
$('#match-close').addEventListener('click', () => { $('#match-sheet').hidden = true; });
$('#match-sheet').addEventListener('click', (e) => { if (e.target.id === 'match-sheet') $('#match-sheet').hidden = true; });

document.addEventListener('click', (ev) => {
  if (ev.target.id === 'place-bet') placeBet();
});
document.addEventListener('input', (ev) => {
  if (ev.target.id === 'coupon-amount') {
    const t = couponTotals(couponAmount());
    const rows = document.querySelectorAll('.coupon-row');
    if (rows[1]) rows[1].children[1].textContent = fmt(t.payout) + ' дыма';
    updateBetbar();
  }
});

// вкладки таблиц
$('#tabs-tables')?.addEventListener('click', (ev) => {
  const tab = ev.target.closest('.tab');
  if (!tab) return;
  document.querySelectorAll('#tabs-tables .tab').forEach((t) => t.classList.remove('active'));
  tab.classList.add('active');
  const isStandings = tab.dataset.tab === 'standings';
  $('#standings-wrap').hidden = !isStandings;
  $('#results-wrap').hidden = isStandings;
  if (!isStandings) {
    api(`/api/results?division_id=${state.currentDivision}`).then(({ results }) => {
      $('#results-wrap').innerHTML = results.length ? results.map((m) => `
        <div class="match-card" style="margin-bottom:8px">
          <div class="mc-top"><span>Тур ${m.tour_number ?? '—'}</span><span class="mc-status">${({confirmed: 'сыгран', disputed: 'спор'})[m.status] || m.status}</span></div>
          <div class="mc-teams">
            <div class="mc-team">${logoHtml(m.home)}<span>${esc(m.home?.name || '')}</span></div>
            <div class="mc-score">${m.score_home}:${m.score_away}${m.pens_home != null ? `<span class="pens"> (${m.pens_home}:${m.pens_away})</span>` : ''}</div>
            <div class="mc-team right"><span>${esc(m.away?.name || '')}</span>${logoHtml(m.away)}</div>
          </div>
        </div>`).join('') : '<div class="empty-note">Сыгранных матчей ещё нет.</div>';
    });
  }
});

init();

/* ===== трансферы (блок 8) ===== */

const trState = { data: null, market: null, tab: 'market', filterPos: null };

async function renderTransfers() {
  try {
    trState.data = await api('/api/transfers/view');
  } catch (e) {
    $('#transfers-body').innerHTML = `<div class="empty-note">${esc(e.message)}</div>`;
    return;
  }
  if (trState.data.no_club) {
    $('#tr-budget').textContent = '—';
    $('#transfers-body').innerHTML = '<div class="empty-note">Клуба нет — рынок недоступен. Запроси клуб во вкладке «Клуб».</div>';
    return;
  }
  $('#tr-budget').textContent = fmt(trState.data.budget) + ' ₼';
  $('#tr-threshold').textContent = fmt(trState.data.threshold);
  $('#tr-comm').textContent = trState.data.commission_pct;

  if (trState.tab === 'market') return renderTransferMarket();
  if (trState.tab === 'lots') return renderMyLots();
  if (trState.tab === 'offers') return renderOffers();
  return renderTransferHistory();
}

async function renderTransferMarket() {
  $('#tr-filters').hidden = false;
  $('#tr-filters').innerHTML = ['', 'ВРТ', 'ЦЗ', 'ЛЗ', 'ЦП', 'ЛП', 'ФРВ', 'ПВ']
    .map((p) => `<button class="chip ${trState.filterPos === p || (!p && !trState.filterPos) ? 'active' : ''}" data-pos="${p}">${p || 'Все'}</button>`).join('');
  const q = trState.filterPos ? `?position=${encodeURIComponent(trState.filterPos)}` : '';
  const { lots, free_agents } = await api(`/api/transfers/market${q}`);
  const row = (name, sub, price, actionHtml) => `<div class="lot-card">
    <div class="lot-main"><div class="lot-name">${esc(name)}</div><div class="lot-sub">${sub}</div></div>
    <div class="lot-price">${fmt(price)} ₼</div>${actionHtml}</div>`;
  $('#transfers-body').innerHTML =
    (free_agents.length ? '<div class="sub" style="margin:4px 0 8px">Свободные агенты (рейтинг² × K)</div>' +
      free_agents.map((a) => row(a.name, `${a.position || '—'} · рейтинг ${a.rating} · свободен`,
        a.effective_price,
        `<button class="lot-btn" data-sign="${a.id}">Купить</button>`)).join('') : '') +
    '<div class="sub" style="margin:12px 0 8px">Лоты клубов</div>' +
    (lots.length ? lots.map((l) => row(l.name,
      `${l.position || '—'} · рейтинг ${l.rating} · ${esc(l.seller_name || '')} · ${l.kind === 'auction' ? 'аукцион' : 'фикс'}`,
      l.effective_price,
      `<button class="lot-btn" data-buy="${l.id}">Купить</button>`)).join('')
      : '<div class="empty-note">Лотов пока нет.</div>');
}

function renderMyLots() {
  $('#tr-filters').hidden = true;
  const d = trState.data;
  $('#transfers-body').innerHTML =
    '<div class="sub" style="margin:4px 0 8px">Выставить на рынок</div>' +
    (d.squad.length ? d.squad.map((s) => `<div class="lot-card">
        <div class="lot-main"><div class="lot-name">${esc(s.name)}</div>
        <div class="lot-sub">${s.position || '—'} · рейтинг ${s.rating}</div></div>
        <button class="lot-btn" data-sell="${s.id}" data-name="${esc(s.name)}">Продать</button>
      </div>`).join('') : '<div class="empty-note">Состав пуст.</div>') +
    '<div class="sub" style="margin:12px 0 8px">Мои открытые лоты</div>' +
    (d.lots.length ? d.lots.map((l) => `<div class="lot-card">
        <div class="lot-main"><div class="lot-name">${esc(l.name)}</div>
        <div class="lot-sub">${l.kind === 'auction' ? 'аукцион' : 'фикс'} · ${fmt(l.buyout_price || l.price)} ₼</div></div>
        <button class="lot-btn secondary" data-unlot="${l.id}">Снять</button>
      </div>`).join('') : '<div class="empty-note">Активных лотов нет.</div>');
}

function renderOffers() {
  $('#tr-filters').hidden = true;
  const d = trState.data;
  $('#transfers-body').innerHTML = d.offers_in.length ? d.offers_in.map((o) => `<div class="lot-card">
      <div class="lot-main"><div class="lot-name">Обмен от ${esc(o.from_name || 'клуба')}</div>
      <div class="lot-sub">отдают: ${esc(o.give_name)}${o.amount ? ` + ${fmt(Math.abs(o.amount))} ₼` : ''} · просят: ${esc(o.want_name || '—')}</div></div>
      <button class="lot-btn" data-accept="${o.id}">Принять</button>
    </div>`).join('') : '<div class="empty-note">Входящих предложений нет.</div>';
}

function renderTransferHistory() {
  $('#tr-filters').hidden = true;
  const d = trState.data;
  $('#transfers-body').innerHTML = d.history.length ? d.history.map((h) => `<div class="bet-history-item">
      <div><div>${esc(h.player_name)}</div>
      <div class="sub">${h.from_club_id ? 'переход' : 'агент'} · ${fmt(h.amount)} ₼</div></div>
      <div class="bh-status ${h.status === 'approved' ? 'won' : 'lost'}">${({approved: 'состоялся', rejected: 'отклонён', needs_judge: 'у судьи', pending: 'ждёт'})[h.status] || h.status}</div>
    </div>`).join('') : '<div class="empty-note">Сделок ещё не было.</div>';
}

async function sellCard(cardId, name) {
  const price = Number(prompt(`Цена продажи ${name} (₼):`, '1000000'));
  if (!price) return;
  try {
    await api('/api/transfers/lots', { method: 'POST', body: JSON.stringify({ card_id: cardId, kind: 'fix', price }) });
    toast(`${name} выставлен за ${fmt(price)} ₼`);
    renderTransfers();
  } catch (e) { toast(e.message); }
}

document.addEventListener('click', async (ev) => {
  const sign = ev.target.closest('[data-sign]');
  if (sign) {
    try {
      const r = await api(`/api/transfers/free-agent/${sign.dataset.sign}/sign`, { method: 'POST' });
      toast(`Агент подписан за ${fmt(r.price)} ₼`);
      renderTransfers();
    } catch (e) { toast(e.message); }
    return;
  }
  const buy = ev.target.closest('[data-buy]');
  if (buy) {
    try {
      const r = await api(`/api/transfers/lots/${buy.dataset.buy}/buy`, { method: 'POST' });
      toast(r.status === 'needs_judge' ? 'Крупная сделка ушла судье на одобрение' : 'Сделка состоялась!');
      renderTransfers();
    } catch (e) { toast(e.message); }
    return;
  }
  const sell = ev.target.closest('[data-sell]');
  if (sell) { sellCard(Number(sell.dataset.sell), sell.dataset.name); return; }
  const unlot = ev.target.closest('[data-unlot]');
  if (unlot) {
    try {
      await api(`/api/transfers/lots/${unlot.dataset.unlot}`, { method: 'DELETE' });
      toast('Лот снят');
      renderTransfers();
    } catch (e) { toast(e.message); }
    return;
  }
  const accept = ev.target.closest('[data-accept]');
  if (accept) {
    try {
      const r = await api(`/api/transfers/exchange/${accept.dataset.accept}/accept`, { method: 'POST' });
      toast(r.status === 'needs_judge' ? 'Обмен ушёл судье (сумма выше порога)' : 'Обмен состоялся!');
      renderTransfers();
    } catch (e) { toast(e.message); }
    return;
  }
  const pos = ev.target.closest('[data-pos]');
  if (pos) {
    trState.filterPos = pos.dataset.pos || null;
    if (trState.tab === 'market') renderTransferMarket();
    return;
  }
  const trTab = ev.target.closest('#tabs-transfers .tab');
  if (trTab) {
    document.querySelectorAll('#tabs-transfers .tab').forEach((t) => t.classList.remove('active'));
    trTab.classList.add('active');
    trState.tab = trTab.dataset.tab;
    renderTransfers();
  }
});


/* ===== админ-панель (блок 10) ===== */

async function openAdmin() {
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

async function renderAdminPlayers() {
  const { players } = await api('/api/admin/players');
  $('#admin-players').innerHTML = players.slice(0, 10).map((p) => `<div class="bet-history-item">
      <div><div>${esc(p.username || p.first_name || p.telegram_id)} ${p.is_frozen ? '🧊' : ''} ${p.is_admin ? '👑' : ''}</div>
      <div class="sub">${fmt(p.balance)} дыма · ур. ${p.level}</div></div>
      <div style="display:flex;gap:4px">
        <button class="lot-btn secondary" data-ban="${p.telegram_id}">${p.is_frozen ? 'Разбан' : 'Бан'}</button>
        <button class="lot-btn secondary" data-adjust="${p.telegram_id}">+дым</button>
      </div>
    </div>`).join('');
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
