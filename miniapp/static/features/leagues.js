/* Админка: лиги с именованными дивизионами (Ла Лига, Серия А…), клубы, участники (ник, клуб, судья). */
import { $, api, esc, hooks, logoHtml, openSheet, toast } from '../lib.js';

const lg = { leagues: [], catalog: [], isRoot: false, peopleQ: '' };

async function loadLeagues() {
  const d = await api('/api/admin/leagues');
  lg.leagues = d.leagues;
  lg.catalog = d.catalog;
  lg.isRoot = d.is_root;
}

function allClubs() {
  return lg.leagues.flatMap((t) => t.divisions.flatMap((d) => d.clubs.map((c) => ({ ...c, division: d.name, league: t.name }))));
}

/* ===== карточка в админ-панели ===== */

hooks.adminCards.push(() => {
  if (document.getElementById('admin-leagues-card')) return;
  const card = document.createElement('div');
  card.className = 'card';
  card.id = 'admin-leagues-card';
  card.innerHTML = `<div class="card-title">🏆 Лиги и участники</div>
    <div class="sub" style="margin-bottom:10px">Сезон с дивизионами по названиям, клубы и владельцы, ники FC27, судьи.</div>
    <div class="lg-btns">
      <button class="bonus-btn" data-lg-open>🏟 Лиги и дивизионы</button>
      <button class="bonus-btn" data-people-open>👥 Ники и клубы игроков</button>
    </div>`;
  const pause = $('#admin-pause')?.parentElement;
  pause.parentElement.insertBefore(card, pause);
});

/* ===== лиги ===== */

async function openLeagues() {
  const body = openSheet('🏟 Лиги и дивизионы', '<div class="empty-note">Загрузка…</div>');
  try { await loadLeagues(); } catch (e) { body.innerHTML = `<div class="empty-note">${esc(e.message)}</div>`; return; }
  renderLeagues(body);
}

function renderLeagues(body = document.getElementById('generic-body')) {
  body.innerHTML = `
    <datalist id="lg-catalog">${lg.catalog.map((c) => `<option value="${esc(c.name)}">`).join('')}</datalist>
    <details class="lg-create" ${lg.leagues.length ? '' : 'open'}>
      <summary>➕ Новый сезон</summary>
      <form id="lg-create-form" autocomplete="off">
        <input class="amount-input" name="name" placeholder="Название: Сезон 1" required maxlength="60">
        <textarea class="amount-input lg-textarea" name="divisions" rows="5"
          placeholder="Дивизионы — по одному в строке, сверху самый сильный:&#10;Ла Лига&#10;Серия А&#10;Лига 1&#10;Лига 2&#10;Серия С"></textarea>
        <div class="lg-row3">
          <label>Круги<select class="amount-input" name="rounds"><option value="2">2 (дома и в гостях)</option><option value="1">1</option></select></label>
          <label>Дней на тур<input class="amount-input" name="tour_days" type="number" min="1" max="30" value="3"></label>
          <label>↑↓ между дивизионами<input class="amount-input" name="promote_count" type="number" min="0" max="10" value="3"></label>
        </div>
        <div class="sub" style="margin:-2px 0 8px">↑↓ = 0 — независимые лиги без повышения и вылета.</div>
        <button class="bonus-btn" type="submit">Создать сезон</button>
      </form>
    </details>
    ${lg.leagues.map(leagueHtml).join('') || '<div class="empty-note">Активных лиг нет — создай сезон выше.</div>'}`;
  body.querySelector('#lg-create-form').onsubmit = async (ev) => {
    ev.preventDefault();
    const f = Object.fromEntries(new FormData(ev.target).entries());
    try {
      const r = await api('/api/admin/leagues', { method: 'POST', body: JSON.stringify(f) });
      lg.leagues = r.leagues;
      toast('Сезон создан — добавь клубы в дивизионы');
      renderLeagues();
    } catch (e) { toast(e.message, 5000); }
  };
}

function leagueHtml(t) {
  const clubsTotal = t.divisions.reduce((a, d) => a + d.clubs.length, 0);
  return `<div class="lg-league" data-tid="${t.id}">
    <div class="lg-head">
      <div><div class="lg-title">${esc(t.name)} <button class="lg-icon" data-lg-rename="${t.id}" title="Переименовать">✎</button></div>
        <div class="sub">${t.divisions.length} дивиз. · ${clubsTotal} клубов · ${t.rounds} круга · тур ${t.tour_days} дн.
          ${t.open_tour ? ` · открыт тур ${t.open_tour}/${t.total_tours}` : ''}</div></div>
    </div>
    <div class="lg-toolbar">
      <label class="sub">↑↓ <select class="lg-mini" data-lg-promote="${t.id}">
        ${[0, 1, 2, 3, 4, 5].map((n) => `<option value="${n}" ${n === t.promote_count ? 'selected' : ''}>${n === 0 ? 'нет' : n}</option>`).join('')}
      </select></label>
      <button class="lot-btn" data-lg-calendar="${t.id}">📅 Календарь</button>
    </div>
    ${t.divisions.map((d, i) => divisionHtml(t, d, i)).join('')}
    <form class="lg-add-div" data-lg-adddiv="${t.id}"><input class="amount-input" name="name" placeholder="Новый дивизион: Серия С" maxlength="40" required><button class="lot-btn secondary">＋</button></form>
  </div>`;
}

function divisionHtml(t, d, i) {
  const last = i === t.divisions.length - 1;
  return `<div class="lg-div">
    <div class="lg-div-head">
      <span class="lg-rank">${i + 1}</span>
      <b>${esc(d.name)}</b>
      <span class="sub">${d.clubs.length} кл.${d.matches ? ` · ${d.matches} матчей` : ''}${d.started ? ' · идёт' : ''}</span>
      <span class="lg-div-actions">
        <button class="lg-icon" data-div-move="${d.id}" data-dir="up" ${i === 0 ? 'disabled' : ''}>↑</button>
        <button class="lg-icon" data-div-move="${d.id}" data-dir="down" ${last ? 'disabled' : ''}>↓</button>
        <button class="lg-icon" data-div-rename="${d.id}" data-name="${esc(d.name)}">✎</button>
        <button class="lg-icon" data-div-del="${d.id}" ${d.clubs.length ? 'disabled' : ''}>🗑</button>
      </span>
    </div>
    ${d.clubs.map((c) => `<div class="lg-club">
      ${logoHtml(c)}
      <div class="lot-main"><div class="lot-name">${esc(c.name)}</div>
        <div class="lot-sub">${c.owner_tg ? `${c.owner_username ? '@' + esc(c.owner_username) : 'ID ' + c.owner_tg}${c.owner_nick ? ` · ник ${esc(c.owner_nick)}` : ' · ник не задан'}` : 'без владельца'}</div></div>
      <button class="lot-btn secondary" data-club-owner="${c.id}">👤</button>
      ${d.started ? '' : `<button class="lot-btn secondary" data-club-remove="${c.id}">✕</button>`}
    </div>`).join('')}
    ${d.started ? '' : `<form class="lg-add-club" data-lg-addclub="${d.id}" autocomplete="off">
      <input class="amount-input" name="club" list="lg-catalog" placeholder="Клуб из каталога FC" required>
      <input class="amount-input" name="owner" placeholder="Владелец: @ник или ID (можно позже)">
      <label class="sub lg-custom"><input type="checkbox" name="custom"> свой клуб (нет в каталоге, без лого)</label>
      <button class="lot-btn">＋ Добавить клуб</button>
    </form>`}
  </div>`;
}

async function leaguePost(url, payload, okText, method = 'POST') {
  try {
    const r = await api(url, { method, body: payload ? JSON.stringify(payload) : undefined });
    if (r.leagues) lg.leagues = r.leagues;
    if (okText) toast(typeof okText === 'function' ? okText(r) : okText, 4000);
    renderLeagues();
    return r;
  } catch (e) { toast(e.message, 5000); return null; }
}

document.addEventListener('submit', (ev) => {
  const addClub = ev.target.closest('[data-lg-addclub]');
  const addDiv = ev.target.closest('[data-lg-adddiv]');
  if (!addClub && !addDiv) return;
  ev.preventDefault();
  const f = Object.fromEntries(new FormData(ev.target).entries());
  if (addClub) {
    leaguePost(`/api/admin/divisions/${addClub.dataset.lgAddclub}`, { club: f.club, owner: f.owner, custom: !!f.custom }, `${f.club} добавлен`);
  } else {
    leaguePost(`/api/admin/leagues/${addDiv.dataset.lgAdddiv}`, { add_division: f.name }, `Дивизион «${f.name}» добавлен`);
  }
});

document.addEventListener('change', (ev) => {
  const pr = ev.target.closest('[data-lg-promote]');
  if (pr) leaguePost(`/api/admin/leagues/${pr.dataset.lgPromote}`, { promote_count: Number(pr.value) },
    Number(pr.value) ? `Повышение/вылет: ${pr.value}` : 'Независимые дивизионы — без повышения и вылета');
});

document.addEventListener('click', async (ev) => {
  const b = ev.target.closest('[data-lg-open],[data-people-open],[data-lg-rename],[data-lg-calendar],[data-div-move],[data-div-rename],[data-div-del],[data-club-owner],[data-club-remove],[data-person],[data-person-save],[data-judge]');
  if (!b) return;
  const d = b.dataset;
  if ('lgOpen' in d) return openLeagues();
  if ('peopleOpen' in d) return openPeople();
  if (d.lgRename) {
    const t = lg.leagues.find((x) => x.id === Number(d.lgRename));
    const name = prompt('Название сезона:', t?.name || '');
    if (name) leaguePost(`/api/admin/leagues/${d.lgRename}`, { name }, 'Переименовано');
  } else if (d.lgCalendar) {
    if (!confirm('Сгенерировать календарь по всем дивизионам? Тур 1 откроется сразу.')) return;
    leaguePost(`/api/admin/leagues/${d.lgCalendar}`, { calendar: true },
      (r) => `Календарь: ${Object.entries(r.matches || {}).map(([k, v]) => `${k} — ${v}`).join(', ')}${r.skipped?.length ? `. Пропущены (мало клубов): ${r.skipped.join(', ')}` : ''}`);
  } else if (d.divMove) {
    leaguePost(`/api/admin/divisions/${d.divMove}`, { move: d.dir });
  } else if (d.divRename) {
    const name = prompt('Название дивизиона:', d.name);
    if (name) leaguePost(`/api/admin/divisions/${d.divRename}`, { name }, 'Переименовано');
  } else if (d.divDel) {
    if (confirm('Удалить пустой дивизион?')) leaguePost(`/api/admin/divisions/${d.divDel}`, null, 'Дивизион удалён', 'DELETE');
  } else if (d.clubOwner) {
    const who = prompt('Владелец клуба: @ник или Telegram ID (пусто — снять владельца):');
    if (who === null) return;
    leaguePost(`/api/admin/clubs/${d.clubOwner}`, who.trim() ? { owner: who.trim() } : { unassign: true },
      who.trim() ? 'Владелец назначен' : 'Владелец снят');
  } else if (d.clubRemove) {
    if (confirm('Убрать клуб из дивизиона?')) leaguePost(`/api/admin/clubs/${d.clubRemove}`, { remove: true }, 'Клуб убран');
  } else if (d.person) {
    openPerson(d.person);
  } else if (d.personSave) {
    savePerson(d.personSave);
  } else if (d.judge) {
    try {
      await api(`/api/admin/people/${d.judge}`, { method: 'POST', body: JSON.stringify({ judge_tournament: Number(d.tid) }) });
      toast('Судейство обновлено');
      openPerson(d.judge);
    } catch (e) { toast(e.message); }
  }
});

/* ===== участники ===== */

async function openPeople() {
  const body = openSheet('👥 Ники и клубы игроков', `
    <input class="amount-input" id="people-q" placeholder="Поиск: @ник, ник FC27, клуб, ID" style="font-size:14px">
    <details class="lg-create"><summary>➕ Добавить игрока по Telegram ID</summary>
      <form id="people-add" autocomplete="off">
        <div class="sub" style="margin-bottom:6px">Если игрок ещё не нажал /start. ID он узнаёт командой /myid.</div>
        <input class="amount-input" name="telegram_id" inputmode="numeric" placeholder="Telegram ID" required>
        <input class="amount-input" name="nick" placeholder="Ник в FC27">
        <button class="bonus-btn">Добавить</button>
      </form></details>
    <div id="people-list"><div class="empty-note">Загрузка…</div></div>`);
  let t;
  body.querySelector('#people-q').addEventListener('input', (e) => {
    clearTimeout(t);
    t = setTimeout(() => { lg.peopleQ = e.target.value.trim(); renderPeople(); }, 250);
  });
  body.querySelector('#people-add').onsubmit = async (ev) => {
    ev.preventDefault();
    const f = Object.fromEntries(new FormData(ev.target).entries());
    try {
      await api('/api/admin/people', { method: 'POST', body: JSON.stringify(f) });
      toast('Игрок добавлен');
      ev.target.reset();
      renderPeople();
    } catch (e) { toast(e.message, 5000); }
  };
  lg.peopleQ = '';
  if (!lg.leagues.length) { try { await loadLeagues(); } catch (_) { /* покажем без клубов */ } }
  renderPeople();
}

async function renderPeople() {
  const el = document.getElementById('people-list');
  if (!el) return;
  try {
    const { people } = await api(`/api/admin/people${lg.peopleQ ? `?q=${encodeURIComponent(lg.peopleQ)}` : ''}`);
    el.innerHTML = people.map((p) => `<div class="bet-history-item">
      <div><div>${esc(p.username ? '@' + p.username : (p.first_name || 'ID ' + p.telegram_id))}${p.is_root ? ' 👑' : ''}${p.judge_of.length ? ' ⚖️' : ''}</div>
        <div class="sub">${p.nick ? `ник <b>${esc(p.nick)}</b>` : '<span class="lg-warn">ник не задан</span>'} · ${p.club ? `${esc(p.club)}${p.division ? ` (${esc(p.division)})` : ''}` : 'без клуба'}</div></div>
      <button class="lot-btn secondary" data-person="${p.telegram_id}">✎</button>
    </div>`).join('') || '<div class="empty-note">Никого не нашёл.</div>';
  } catch (e) { el.innerHTML = `<div class="empty-note">${esc(e.message)}</div>`; }
}

async function openPerson(tg) {
  if (!lg.leagues.length) { try { await loadLeagues(); } catch (_) { /* без клубов */ } }
  let p;
  try { p = (await api(`/api/admin/people?q=${encodeURIComponent(tg)}`)).people.find((x) => String(x.telegram_id) === String(tg)); } catch (e) { toast(e.message); return; }
  if (!p) { toast('Игрок не найден'); return; }
  const clubs = allClubs();
  const judgeHtml = lg.isRoot ? `<div class="card-title" style="margin-top:14px">⚖️ Судья</div>
    ${lg.leagues.map((t) => `<button class="lot-btn ${p.judge_of.includes(t.name) ? '' : 'secondary'}" data-judge="${p.telegram_id}" data-tid="${t.id}" style="margin:0 6px 6px 0">${p.judge_of.includes(t.name) ? '✓ ' : ''}${esc(t.name)}</button>`).join('') || '<div class="sub">Нет активных лиг</div>'}` : '';
  openSheet(p.username ? '@' + p.username : `ID ${p.telegram_id}`, `
    <div class="sub" style="margin-bottom:10px">Telegram ID ${p.telegram_id}${p.first_name ? ` · ${esc(p.first_name)}` : ''}</div>
    <label class="lg-label">Ник в FC27 (по нему бот узнаёт скрины)
      <input class="amount-input" id="person-nick" value="${esc(p.nick || '')}" maxlength="32" placeholder="как в игре, посимвольно"></label>
    <label class="lg-label">Клуб
      <select class="amount-input" id="person-club">
        <option value="">— без клуба —</option>
        ${clubs.map((c) => `<option value="${c.id}" ${c.id === p.club_id ? 'selected' : ''}>${esc(c.name)} · ${esc(c.division)}${c.owner_tg && c.owner_tg !== p.telegram_id ? ' (занят)' : ''}</option>`).join('')}
      </select></label>
    <button class="bonus-btn" data-person-save="${p.telegram_id}">💾 Сохранить</button>
    ${judgeHtml}`);
}

async function savePerson(tg) {
  const nick = $('#person-nick').value.trim();
  const club = $('#person-club').value;
  try {
    await api(`/api/admin/people/${tg}`, { method: 'POST', body: JSON.stringify({ nick, club_id: club ? Number(club) : null }) });
    toast('Сохранено');
    await loadLeagues();
    openPeople();
  } catch (e) { toast(e.message, 5000); }
}

// «✎ Ник/клуб» в списке игроков админки (features/admin.js) открывает карточку участника
export function openPersonSheet(tg) { return openPerson(tg); }
