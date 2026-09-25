import { $, api, esc, fmt, hooks, openSheet, toast, logoHtml } from '../lib.js';

/* ===== трансферы: рынок, аукционы, обмены, судья (план 08 B2–B3) ===== */

const tr = {
  data: null, tab: 'market', filterPos: null, judge: [],
  lotId: null, sell: null, ex: null, busy: false,
};
const POSITIONS = ['', 'ВРТ', 'ЦЗ', 'ЛЗ', 'ЦП', 'ЛП', 'ФРВ', 'ПВ'];
const body = () => $('#transfers-body');
const parseMoney = (v) => Number(String(v ?? '').replace(/[^\d]/g, '')) || 0;
const roundUp = (n, step = 1000) => Math.ceil(n / step - 1e-9) * step;  // 1.4М×1.1 не должно стать 1 541 000
const plural = (n, one, few, many) => {
  const m10 = n % 10, m100 = n % 100;
  if (m10 === 1 && m100 !== 11) return one;
  return m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14) ? few : many;
};
const snipeSec = () => (tr.data?.auction?.snipe_minutes ?? 10) * 60;

/* ===== таймеры аукционов: одна «тикалка» на все [data-deadline] ===== */

function fmtLeft(sec) {
  if (sec <= 0) return 'завершён';
  const d = Math.floor(sec / 86400), h = Math.floor((sec % 86400) / 3600);
  const m = Math.floor((sec % 3600) / 60), s = Math.floor(sec % 60);
  if (d) return `${d} д ${h} ч`;
  if (h) return `${h} ч ${String(m).padStart(2, '0')} мин`;
  return `${m}:${String(s).padStart(2, '0')}`;
}

function timerHtml(sec) {
  if (sec == null) return '';
  // дедлайн от локальных часов + seconds_left сервера — без рассинхрона часовых поясов
  const cls = sec > 0 && sec < snipeSec() ? ' urgent' : '';
  return `<span class="auc-timer${cls}" data-deadline="${Date.now() + sec * 1000}">${fmtLeft(sec)}</span>`;
}

setInterval(() => {
  document.querySelectorAll('[data-deadline]').forEach((el) => {
    const left = (Number(el.dataset.deadline) - Date.now()) / 1000;
    el.textContent = left > 0 ? fmtLeft(left) : 'подводим итоги';
    el.classList.toggle('urgent', left > 0 && left < snipeSec());
    el.classList.toggle('done', left <= 0);
  });
}, 1000);

/* ===== главный рендер ===== */

async function renderTransfers() {
  let judge = { deals: [] };
  try {
    [tr.data, judge] = await Promise.all([
      api('/api/transfers/view'),
      api('/api/transfers/judge').catch(() => ({ deals: [] })),
    ]);
  } catch (e) {
    body().innerHTML = `<div class="empty-note">${esc(e.message)}</div>`;
    return;
  }
  tr.judge = judge.deals || [];
  syncJudgeTab();
  if (tr.data.no_club) {
    $('#tr-budget').textContent = '—';
    setLocked(0);
    if (tr.tab === 'judge' && tr.judge.length) return renderJudge();
    $('#tr-filters').hidden = true;
    body().innerHTML = '<div class="empty-note">Клуба нет — рынок недоступен. Запроси клуб во вкладке «Клуб».</div>';
    return;
  }
  $('#tr-budget').textContent = fmt(tr.data.budget) + ' ₼';
  setLocked(tr.data.locked);
  $('#tr-threshold').textContent = fmt(tr.data.threshold);
  $('#tr-comm').textContent = tr.data.commission_pct;

  if (tr.tab === 'market') return renderMarket();
  if (tr.tab === 'lots') return renderMyLots();
  if (tr.tab === 'offers') return renderOffers();
  if (tr.tab === 'judge') return renderJudge();
  return renderHistory();
}

function setLocked(sum) {
  let el = $('#tr-locked');
  if (!el) {
    el = document.createElement('div');
    el.id = 'tr-locked';
    el.className = 'tr-locked';
    $('#tr-budget').insertAdjacentElement('afterend', el);
  }
  el.hidden = !sum;
  el.textContent = sum ? `🔒 в ставках ${fmt(sum)} ₼` : '';
}

function syncJudgeTab() {
  const tabs = $('#tabs-transfers');
  let btn = tabs.querySelector('[data-tab="judge"]');
  if (tr.judge.length && !btn) {
    btn = document.createElement('button');
    btn.className = 'tab';
    btn.dataset.tab = 'judge';
    tabs.appendChild(btn);
  }
  if (btn && !tr.judge.length) {
    btn.remove();
    if (tr.tab === 'judge') { tr.tab = 'market'; tabs.querySelector('[data-tab="market"]')?.classList.add('active'); }
    return;
  }
  if (btn) {
    btn.innerHTML = `⚖️<span class="tr-badge">${tr.judge.length}</span>`;
    btn.title = 'Судья: сделки на одобрение';
    btn.classList.toggle('active', tr.tab === 'judge');
  }
}

/* ===== маркет ===== */

function auctionCard(l, opts = {}) {
  const bids = l.bids_count || 0;
  const state = opts.my != null
    ? (l.top_club_id === tr.data?.club_id
      ? `<span class="auc-state lead">лидируешь · ${fmt(opts.my)} ₼</span>`
      : '<span class="auc-state out">перебили</span>')
    : '';
  return `<div class="lot-card auc-card" data-tr-lot="${l.id}">
    <div class="lot-main">
      <div class="lot-name">${esc(l.name)} <span class="auc-tag">аукцион</span></div>
      <div class="lot-sub">${esc(l.position || '—')} · ${l.rating} OVR${l.seller_name ? ` · ${esc(l.seller_name)}` : ''}</div>
      <div class="auc-meta">
        <span class="auc-cur">${fmt(l.current_price)} ₼</span>
        <span class="auc-dot"></span><span>${bids ? `${bids} ${plural(bids, 'ставка', 'ставки', 'ставок')}` : 'старт'}</span>
        <span class="auc-dot"></span>${timerHtml(l.seconds_left)}
      </div>
      ${l.buyout_price ? `<div class="auc-buyout">выкуп ${fmt(l.buyout_price)} ₼</div>` : ''}
      ${state}
    </div>
    ${opts.action ?? (opts.noBtn ? '' : `<button class="lot-btn" data-tr-bid="${l.id}">Ставка</button>`)}
  </div>`;
}

const fixRow = (name, sub, price, actionHtml, attrs = '') => `<div class="lot-card"${attrs}>
  <div class="lot-main"><div class="lot-name">${esc(name)}</div><div class="lot-sub">${sub}</div></div>
  <div class="lot-price">${fmt(price)} ₼</div>${actionHtml}</div>`;

async function renderMarket() {
  const f = $('#tr-filters');
  f.hidden = false;
  f.innerHTML = POSITIONS.map((p) => `<button class="chip ${(tr.filterPos || '') === p ? 'active' : ''}" data-tr-pos="${p}">${p || 'Все'}</button>`).join('');
  const q = tr.filterPos ? `?position=${encodeURIComponent(tr.filterPos)}` : '';
  let market;
  try { market = await api(`/api/transfers/market${q}`); } catch (e) { body().innerHTML = `<div class="empty-note">${esc(e.message)}</div>`; return; }
  const { lots, free_agents } = market;
  const auctions = lots.filter((l) => l.kind === 'auction');
  const fixed = lots.filter((l) => l.kind !== 'auction');
  const mine = [...(tr.data.bids || []).map((b) => ({ ...b, _my: b.my_bid })), ...(tr.data.outbid || []).map((b) => ({ ...b, _my: 0 }))];
  body().innerHTML =
    (mine.length ? '<div class="group-title">Мои ставки</div>' + mine.map((l) => auctionCard(l, { my: l._my, noBtn: true })).join('') : '') +
    '<div class="group-title">Аукционы</div>' +
    (auctions.length ? auctions.map((l) => auctionCard(l)).join('') : '<div class="empty-note">Аукционов сейчас нет.</div>') +
    '<div class="group-title">Фикс-цена</div>' +
    (fixed.length ? fixed.map((l) => fixRow(l.name, `${esc(l.position || '—')} · ${l.rating} OVR · ${esc(l.seller_name || '')}`,
      l.price, `<button class="lot-btn" data-tr-buy="${l.id}" data-price="${l.price}">Купить</button>`, ` data-tr-lot="${l.id}"`)).join('')
      : '<div class="empty-note">Лотов пока нет.</div>') +
    (free_agents.length ? '<div class="group-title">Свободные агенты · рейтинг² × K</div>' +
      free_agents.map((a) => fixRow(a.name, `${esc(a.position || '—')} · ${a.rating} OVR · свободен`, a.effective_price,
        `<button class="lot-btn" data-tr-sign="${a.id}">Купить</button>`)).join('') : '');
}

/* ===== лист лота: ставка + история ===== */

async function openLotSheet(id) {
  tr.lotId = id;
  let lot;
  try { lot = await api(`/api/transfers/lots/${id}`); } catch (e) { toast(e.message); return; }
  if (tr.lotId !== id) return;
  const isAuction = lot.kind === 'auction';
  const open = lot.status === 'open';
  const statusTxt = ({ sold: 'продан', expired: 'завершён без ставок', cancelled: 'снят', needs_judge: 'у судьи', closing: 'подводим итоги' })[lot.status];
  const stats = isAuction ? `<div class="auc-stats">
      <div><span>${lot.top_bid ? 'Текущая ставка' : 'Старт'}</span><b>${fmt(lot.current_price)} ₼</b></div>
      <div><span>Осталось</span><b>${open ? timerHtml(lot.seconds_left) : esc(statusTxt || lot.status)}</b></div>
      <div><span>Лидер</span><b>${lot.top_club_name ? esc(lot.top_club_name) : '—'}</b></div>
      <div><span>Выкуп</span><b>${lot.buyout_price ? fmt(lot.buyout_price) + ' ₼' : 'нет'}</b></div>
    </div>` : `<div class="auc-stats"><div><span>Цена</span><b>${fmt(lot.price)} ₼</b></div><div><span>Статус</span><b>${open ? 'в продаже' : esc(statusTxt || lot.status)}</b></div></div>`;

  let action = '';
  if (open && lot.is_mine) {
    action = `<div class="sub tr-note">Это твой лот.${isAuction && lot.top_bid ? ' Со ставками снять нельзя.' : ''}</div>`;
  } else if (open && isAuction) {
    const cur = lot.current_price;
    const min = lot.min_bid;
    const quick = [
      ['Мин', min],
      ['+5%', Math.max(min, roundUp(cur * 1.05))],
      ['+10%', Math.max(min, roundUp(cur * 1.10))],
    ];
    if (lot.buyout_price) quick.push(['Выкуп', lot.buyout_price]);
    action = `${lot.is_top ? '<div class="auc-state lead" style="margin-bottom:6px">Ты лидируешь — можно поднять ставку</div>' : ''}
      <input id="tr-bid-amount" class="amount-input" inputmode="numeric" value="${fmt(min)}" autocomplete="off">
      <div class="quick-amounts">${quick.map(([lbl, v]) => `<button data-tr-quick="${Math.min(v, lot.buyout_price || v)}">${lbl}</button>`).join('')}</div>
      <div class="sub tr-note">Мин. ставка ${fmt(min)} ₼ (шаг ${lot.step_pct}%). Сумма блокируется в бюджете клуба; перебьют — вернётся.
        Ставка в последние ${lot.snipe_minutes} мин продлевает аукцион на ${lot.snipe_minutes} мин.
        ${lot.my_budget != null ? `<br>Свободно: ${fmt(lot.my_budget + (lot.is_top ? lot.top_bid : 0))} ₼.` : ''}
        ${lot.threshold ? ` Выше ${fmt(lot.threshold)} ₼ — после финиша сделку одобряет судья.` : ''}</div>
      <button class="place-btn" data-tr-place="${lot.id}">Поставить ${fmt(min)} ₼</button>`;
  } else if (open) {
    action = `<button class="place-btn" data-tr-buy="${lot.id}" data-price="${lot.price}">Купить за ${fmt(lot.price)} ₼</button>`;
  }

  const bidStatus = { held: ['лидер', 'open'], outbid: ['перебита', 'void'], won: ['выиграла', 'won'], refunded: ['возврат', 'lost'] };
  const bids = isAuction ? '<div class="group-title">История ставок</div>' + (lot.bids.length ? `<div class="tr-list">${lot.bids.map((b) => {
    const [lbl, cls] = bidStatus[b.status] || [b.status, 'void'];
    return `<div class="bet-history-item"><div><div>${esc(b.club_name || 'клуб')}</div><div class="sub">${fmt(b.amount)} ₼ · ${esc(fmtUtc(b.created_at))}</div></div><div class="bh-status ${cls}">${lbl}</div></div>`;
  }).join('')}</div>` : '<div class="empty-note">Ставок ещё нет — будь первым.</div>') : '';
  const hist = lot.card_history?.length ? '<div class="group-title">Переходы карточки</div><div class="tr-list">' + lot.card_history.map((h) =>
    `<div class="bet-history-item"><div><div>${esc(h.from_name || 'свободный агент')} → ${esc(h.to_name || '—')}</div><div class="sub">${esc(fmtUtc(h.created_at))}</div></div><div class="lot-price">${fmt(h.amount)} ₼</div></div>`).join('') + '</div>' : '';

  openSheet(lot.name, `<div class="sub" style="margin:-4px 0 12px">${esc(lot.position || '—')} · ${lot.rating} OVR · продавец ${esc(lot.seller_name || '—')}</div>
    ${stats}${action}${bids}${hist}`);
}

function fmtUtc(s) {
  const d = s ? new Date(String(s).replace(' ', 'T') + 'Z') : null;
  return d && !isNaN(d) ? d.toLocaleString('ru-RU', { day: '2-digit', month: '2-digit', hour: '2-digit', minute: '2-digit' }) : (s || '');
}

async function placeBid(lotId) {
  const amount = parseMoney($('#tr-bid-amount')?.value);
  if (!amount) { toast('Введи сумму ставки'); return; }
  if (tr.busy) return;
  tr.busy = true;
  try {
    const r = await api(`/api/transfers/lots/${lotId}/bid`, { method: 'POST', body: JSON.stringify({ amount }) });
    if (r.result === 'approved') toast('Выкуп! Карточка уже в твоём клубе');
    else if (r.result === 'needs_judge') toast('Выкуп выше порога — сделку одобрит судья');
    else toast(`Ставка ${fmt(r.amount)} ₼ принята${r.extended ? ' · аукцион продлён' : ''}`);
    await openLotSheet(lotId);
    renderTransfers();
  } catch (e) { toast(e.message); } finally { tr.busy = false; }
}

/* ===== мои лоты + продажа ===== */

function renderMyLots() {
  $('#tr-filters').hidden = true;
  const d = tr.data;
  const myLots = d.lots.map((l) => (l.kind === 'auction'
    ? auctionCard({ ...l, seller_name: l.top_club_name ? `лидер ${l.top_club_name}` : null }, {
      action: l.status === 'open'
        ? `<button class="lot-btn secondary" data-tr-unlot="${l.id}" ${l.top_bid ? 'disabled title="Есть ставки"' : ''}>Снять</button>`
        : '<span class="bh-status open">у судьи</span>',
    })
    : fixRow(l.name, `фикс · ${esc(l.position || '—')} · ${l.rating} OVR`, l.price,
      l.status === 'open' ? `<button class="lot-btn secondary" data-tr-unlot="${l.id}">Снять</button>` : '<span class="bh-status open">у судьи</span>')));
  body().innerHTML =
    '<div class="group-title">Мои лоты</div>' +
    (myLots.length ? myLots.join('') : '<div class="empty-note">Активных лотов нет.</div>') +
    '<div class="group-title">Состав · выставить на рынок</div>' +
    (d.squad.length ? d.squad.map((s) => `<div class="lot-card">
        <div class="lot-main"><div class="lot-name">${esc(s.name)}</div>
        <div class="lot-sub">${esc(s.position || '—')} · ${s.rating} OVR · ориентир ${fmt(s.sell_value)} ₼</div></div>
        ${s.on_market ? '<span class="bh-status open">на рынке</span>' : `<button class="lot-btn" data-tr-sell="${s.id}">Продать</button>`}
      </div>`).join('') : '<div class="empty-note">Состав пуст.</div>');
}

function openSellSheet(cardId) {
  const card = tr.data.squad.find((s) => s.id === cardId);
  if (!card) return;
  tr.sell = tr.sell?.card?.id === cardId ? tr.sell : { card, kind: 'fix', hours: tr.data.auction?.hours || 24, price: card.sell_value, buyout: '' };
  const s = tr.sell;
  const hours = [...new Set([6, 12, 24, 48, tr.data.auction?.hours || 24])].sort((a, b) => a - b);
  openSheet(`Продать ${card.name}`, `<div class="sub" style="margin:-4px 0 12px">${esc(card.position || '—')} · ${card.rating} OVR · ориентир ${fmt(card.sell_value)} ₼</div>
    <div class="chips tr-seg">
      <button class="chip ${s.kind === 'fix' ? 'active' : ''}" data-tr-sell-kind="fix">Фикс-цена</button>
      <button class="chip ${s.kind === 'auction' ? 'active' : ''}" data-tr-sell-kind="auction">🔨 Аукцион</button>
    </div>
    <div class="tr-field-lbl">${s.kind === 'auction' ? 'Стартовая цена' : 'Цена'}, ₼</div>
    <input id="tr-sell-price" class="amount-input" inputmode="numeric" value="${s.price ? fmt(s.price) : ''}" autocomplete="off">
    ${s.kind === 'auction' ? `
      <div class="tr-field-lbl">Цена выкупа, ₼ <span class="sub">(необязательно — закрывает аукцион сразу)</span></div>
      <input id="tr-sell-buyout" class="amount-input" inputmode="numeric" placeholder="без выкупа" value="${s.buyout ? fmt(s.buyout) : ''}" autocomplete="off">
      <div class="tr-field-lbl">Длительность</div>
      <div class="chips tr-seg">${hours.map((h) => `<button class="chip ${s.hours === h ? 'active' : ''}" data-tr-sell-hours="${h}">${h} ч</button>`).join('')}</div>` : ''}
    <div class="sub tr-note">Комиссия рынка ${tr.data.commission_pct}% сгорает. Сделка выше ${fmt(tr.data.threshold)} ₼ — через судью.</div>
    <button class="place-btn" data-tr-sell-go>Выставить</button>`);
}

function readSellInputs() {
  if (!tr.sell) return;
  const p = $('#tr-sell-price'); const b = $('#tr-sell-buyout');
  if (p) tr.sell.price = parseMoney(p.value);
  if (b) tr.sell.buyout = parseMoney(b.value);
}

async function submitSell() {
  readSellInputs();
  const s = tr.sell;
  if (!s?.price) { toast('Укажи цену'); return; }
  const payload = { card_id: s.card.id, kind: s.kind, price: s.price };
  if (s.kind === 'auction') { payload.hours = s.hours; if (s.buyout) payload.buyout_price = s.buyout; }
  try {
    await api('/api/transfers/lots', { method: 'POST', body: JSON.stringify(payload) });
    toast(s.kind === 'auction' ? `${s.card.name}: аукцион на ${s.hours} ч запущен` : `${s.card.name} выставлен за ${fmt(s.price)} ₼`);
    tr.sell = null;
    $('#generic-sheet').hidden = true;
    renderTransfers();
  } catch (e) { toast(e.message); }
}

/* ===== обмены ===== */

function offerLine(o, dir) {
  const give = o.give_name ? esc(o.give_name) : '';
  const money = o.amount ? `${fmt(Math.abs(o.amount))} ₼` : '';
  const theyGive = [give, money].filter(Boolean).join(' + ');
  return dir === 'in'
    ? `дают: ${theyGive || '—'} · просят: ${esc(o.want_name || '—')}`
    : `отдаёшь: ${theyGive || '—'} · за: ${esc(o.want_name || '—')}`;
}

function renderOffers() {
  $('#tr-filters').hidden = true;
  const d = tr.data;
  body().innerHTML = '<button class="bonus-btn" data-tr-ex-start style="margin-bottom:12px">🔄 Предложить обмен или цену</button>' +
    '<div class="group-title">Входящие</div>' +
    (d.offers_in.length ? d.offers_in.map((o) => `<div class="lot-card">
      <div class="lot-main"><div class="lot-name">${o.give_name ? 'Обмен' : 'Предложение'} от ${esc(o.from_name || 'клуба')}</div>
      <div class="lot-sub">${offerLine(o, 'in')}</div></div>
      <div class="tr-actions"><button class="lot-btn" data-tr-accept="${o.id}">Принять</button>
      <button class="lot-btn secondary" data-tr-decline="${o.id}">Нет</button></div>
    </div>`).join('') : '<div class="empty-note">Входящих предложений нет.</div>') +
    '<div class="group-title">Мои предложения</div>' +
    (d.offers_out.length ? d.offers_out.map((o) => `<div class="lot-card">
      <div class="lot-main"><div class="lot-name">→ ${esc(o.to_name || 'клуб')}</div>
      <div class="lot-sub">${offerLine(o, 'out')}</div></div>
      ${o.status === 'needs_judge' ? '<span class="bh-status open">у судьи</span>' : `<button class="lot-btn secondary" data-tr-decline="${o.id}">Отозвать</button>`}
    </div>`).join('') : '<div class="empty-note">Ты пока ничего не предлагал.</div>');
}

async function exRender() {
  const ex = tr.ex;
  const back = '<button class="tr-back" data-tr-ex-back>← назад</button>';
  if (ex.step === 'club') {
    if (!ex.clubs) {
      try { ex.clubs = (await api('/api/transfers/clubs')).clubs; } catch (e) { toast(e.message); return; }
    }
    openSheet('Обмен · выбери клуб', `<div class="sub" style="margin:-4px 0 10px">Шаг 1 из 3 — с кем меняемся</div><div class="tr-list">` +
      (ex.clubs.length ? ex.clubs.map((c) => `<button class="tr-pick" data-tr-ex-club="${c.id}">
        ${logoHtml(c, 'tr-logo')}<span class="tr-pick-main"><b>${esc(c.name)}</b><span class="sub">${c.cards} ${plural(c.cards, 'карточка', 'карточки', 'карточек')}</span></span><span class="tr-chev">›</span></button>`).join('')
        : '<div class="empty-note">Других клубов нет.</div>') + '</div>');
    return;
  }
  if (ex.step === 'want') {
    if (!ex.squad) {
      try { ex.squad = (await api(`/api/transfers/clubs/${ex.club.id}/squad`)).squad; } catch (e) { toast(e.message); return; }
    }
    openSheet(`Обмен · ${ex.club.name}`, `${back}<div class="sub" style="margin:4px 0 10px">Шаг 2 из 3 — какую карточку хочешь</div><div class="tr-list">` +
      (ex.squad.length ? ex.squad.map((s) => `<button class="tr-pick" data-tr-ex-want="${s.id}" ${s.on_market ? 'disabled' : ''}>
        <span class="tr-ovr">${s.rating}</span><span class="tr-pick-main"><b>${esc(s.name)}</b><span class="sub">${esc(s.position || '—')}${s.on_market ? ' · на рынке — купи в «Маркете»' : ''}</span></span><span class="tr-chev">›</span></button>`).join('')
        : '<div class="empty-note">Состав клуба пуст.</div>') + '</div>');
    return;
  }
  // шаг 3: что даём
  const mine = tr.data.squad.filter((s) => !s.on_market);
  const needCard = ex.mode !== 'money', needMoney = ex.mode !== 'card';
  const give = mine.find((s) => s.id === ex.give);
  const summary = [needCard ? (give ? esc(give.name) : '…карточка') : '', needMoney ? (ex.money ? `${fmt(ex.money)} ₼` : '…сумма') : ''].filter(Boolean).join(' + ');
  openSheet(`Обмен · ${ex.want.name}`, `${back}<div class="sub" style="margin:4px 0 10px">Шаг 3 из 3 — что отдаёшь за <b>${esc(ex.want.name)}</b> (${esc(ex.club.name)})</div>
    <div class="chips tr-seg">
      <button class="chip ${ex.mode === 'money' ? 'active' : ''}" data-tr-ex-mode="money">Деньги</button>
      <button class="chip ${ex.mode === 'card' ? 'active' : ''}" data-tr-ex-mode="card">Моя карточка</button>
      <button class="chip ${ex.mode === 'both' ? 'active' : ''}" data-tr-ex-mode="both">Карточка + ₼</button>
    </div>
    ${needCard ? `<div class="tr-field-lbl">Твоя карточка</div><div class="tr-list tr-scroll">${mine.length ? mine.map((s) => `<button class="tr-pick ${ex.give === s.id ? 'selected' : ''}" data-tr-ex-give="${s.id}">
      <span class="tr-ovr">${s.rating}</span><span class="tr-pick-main"><b>${esc(s.name)}</b><span class="sub">${esc(s.position || '—')}</span></span><span class="tr-radio"></span></button>`).join('') : '<div class="empty-note">Нет свободных карточек.</div>'}</div>` : ''}
    ${needMoney ? `<div class="tr-field-lbl">${ex.mode === 'both' ? 'Доплата' : 'Сумма'}, ₼ <span class="sub">· бюджет ${fmt(tr.data.budget)}</span></div>
      <input id="tr-ex-money" class="amount-input" inputmode="numeric" value="${ex.money ? fmt(ex.money) : ''}" placeholder="0" autocomplete="off">` : ''}
    <div class="tr-summary"><span>Ты отдаёшь</span><b>${summary}</b><span>Получаешь</span><b>${esc(ex.want.name)}</b></div>
    <div class="sub tr-note">Вторая сторона подтверждает. ${needMoney ? `Сумма выше ${fmt(tr.data.threshold)} ₼ — сделку одобряет судья.` : ''}${ex.mode === 'money' ? ` Комиссия ${tr.data.commission_pct}% сгорает с продажи.` : ''}</div>
    <button class="place-btn" data-tr-ex-send>Отправить предложение</button>`);
}

async function exSend() {
  const ex = tr.ex;
  const m = $('#tr-ex-money');
  if (m) ex.money = parseMoney(m.value);
  const payload = { to_club_id: ex.club.id, want_card_id: ex.want.id, money: ex.mode === 'card' ? 0 : ex.money };
  if (ex.mode !== 'money') {
    if (!ex.give) { toast('Выбери свою карточку'); return; }
    payload.give_card_id = ex.give;
  }
  if (ex.mode !== 'card' && !payload.money) { toast('Укажи сумму'); return; }
  try {
    await api('/api/transfers/exchange', { method: 'POST', body: JSON.stringify(payload) });
    toast(`Предложение отправлено в ${ex.club.name}`);
    tr.ex = null;
    $('#generic-sheet').hidden = true;
    tr.tab = 'offers';
    document.querySelectorAll('#tabs-transfers .tab').forEach((t) => t.classList.toggle('active', t.dataset.tab === 'offers'));
    renderTransfers();
  } catch (e) { toast(e.message); }
}

/* ===== судья ===== */

function renderJudge() {
  $('#tr-filters').hidden = true;
  const kinds = { lot: 'Покупка лота', auction: 'Аукцион', exchange: 'Обмен', offer: 'Денежное предложение' };
  body().innerHTML = '<div class="sub tr-note" style="margin-bottom:10px">Сделки выше порога ждут решения. Проверь цену и связь клубов (анти-сговор).</div>' +
    (tr.judge.length ? tr.judge.map((t) => {
      const isEx = t.deal_kind === 'exchange' || t.deal_kind === 'offer';
      const what = isEx
        ? `${esc(t.from_name)} отдаёт ${[t.give_name ? esc(t.give_name) : '', t.amount ? fmt(Math.abs(t.amount)) + ' ₼' : ''].filter(Boolean).join(' + ')} → получает ${esc(t.want_name)} от ${esc(t.to_name)}`
        : `${esc(t.from_name)} → ${esc(t.to_name)} · ${esc(t.give_pos || '')} ${t.give_rating || ''} OVR`;
      return `<div class="lot-card judge-card">
        <div class="lot-main"><div class="lot-name">${kinds[t.deal_kind] || 'Сделка'}: ${esc(isEx ? t.want_name : t.player_name)}</div>
        <div class="lot-sub">${what}</div>
        <div class="auc-meta"><span class="auc-cur">${fmt(Math.abs(t.amount || 0))} ₼</span><span class="auc-dot"></span><span>${esc(fmtUtc(t.created_at))}</span></div></div>
        <div class="tr-actions"><button class="lot-btn" data-tr-judge="${t.id}" data-ok="1">Одобрить</button>
        <button class="lot-btn secondary" data-tr-judge="${t.id}" data-ok="0">Отклонить</button></div>
      </div>`;
    }).join('') : '<div class="empty-note">Очередь пуста.</div>');
}

/* ===== история ===== */

function renderHistory() {
  $('#tr-filters').hidden = true;
  const d = tr.data;
  const st = { approved: ['состоялся', 'won'], rejected: ['отклонён', 'lost'], cancelled: ['отозван', 'void'], needs_judge: ['у судьи', 'open'] };
  body().innerHTML = d.history.length ? `<div class="tr-list">${d.history.map((h) => {
    const kind = !h.from_club_id ? 'агент' : h.want_card_id ? 'обмен' : h.lot_kind === 'auction' ? 'аукцион' : 'лот';
    const [lbl, cls] = st[h.status] || [h.status, 'void'];
    return `<div class="bet-history-item">
      <div><div>${esc(h.player_name)}</div>
      <div class="sub">${kind} · ${esc(h.from_name || 'свободный')} → ${esc(h.to_name || '—')} · ${fmt(Math.abs(h.amount || 0))} ₼</div></div>
      <div class="bh-status ${cls}">${lbl}</div></div>`;
  }).join('')}</div>` : '<div class="empty-note">Сделок ещё не было.</div>';
}

/* ===== события ===== */

async function act(fn, okText) {
  try {
    const r = await fn();
    toast(typeof okText === 'function' ? okText(r) : okText);
    renderTransfers();
    return r;
  } catch (e) { toast(e.message); return null; }
}

document.addEventListener('input', (ev) => {
  const id = ev.target.id;
  if (id === 'tr-bid-amount') {
    const btn = document.querySelector('[data-tr-place]');
    const v = parseMoney(ev.target.value);
    if (btn) btn.textContent = v ? `Поставить ${fmt(v)} ₼` : 'Поставить';
  } else if (id === 'tr-sell-price' || id === 'tr-sell-buyout') {
    readSellInputs();
  } else if (id === 'tr-ex-money' && tr.ex) {
    tr.ex.money = parseMoney(ev.target.value);
    const sum = document.querySelector('.tr-summary b');
    if (sum) {
      const give = tr.data.squad.find((s) => s.id === tr.ex.give);
      sum.textContent = [tr.ex.mode !== 'money' ? (give?.name || '…карточка') : '', tr.ex.money ? `${fmt(tr.ex.money)} ₼` : '…сумма'].filter(Boolean).join(' + ');
    }
  }
});

document.addEventListener('click', async (ev) => {
  const t = ev.target;
  const q = (sel) => t.closest(sel);
  let el;

  if ((el = q('[data-tr-sign]'))) {
    return act(() => api(`/api/transfers/free-agent/${el.dataset.trSign}/sign`, { method: 'POST' }), (r) => `Агент подписан за ${fmt(r.price)} ₼`);
  }
  if ((el = q('[data-tr-buy]'))) {
    const r = await act(() => api(`/api/transfers/lots/${el.dataset.trBuy}/buy`, { method: 'POST' }),
      (r) => (r.status === 'needs_judge' ? 'Крупная сделка ушла судье на одобрение' : 'Сделка состоялась!'));
    if (r) $('#generic-sheet') && ($('#generic-sheet').hidden = true);
    return;
  }
  if ((el = q('[data-tr-bid]'))) return openLotSheet(Number(el.dataset.trBid));
  if ((el = q('[data-tr-quick]'))) {
    const inp = $('#tr-bid-amount');
    inp.value = fmt(el.dataset.trQuick);
    inp.dispatchEvent(new Event('input', { bubbles: true }));
    return;
  }
  if ((el = q('[data-tr-place]'))) return placeBid(Number(el.dataset.trPlace));
  if ((el = q('[data-tr-unlot]'))) {
    return act(() => api(`/api/transfers/lots/${el.dataset.trUnlot}`, { method: 'DELETE' }), 'Лот снят');
  }
  if ((el = q('[data-tr-sell]'))) return openSellSheet(Number(el.dataset.trSell));
  if ((el = q('[data-tr-sell-kind]'))) { readSellInputs(); tr.sell.kind = el.dataset.trSellKind; return openSellSheet(tr.sell.card.id); }
  if ((el = q('[data-tr-sell-hours]'))) { readSellInputs(); tr.sell.hours = Number(el.dataset.trSellHours); return openSellSheet(tr.sell.card.id); }
  if (q('[data-tr-sell-go]')) return submitSell();

  if ((el = q('[data-tr-accept]'))) {
    return act(() => api(`/api/transfers/exchange/${el.dataset.trAccept}/accept`, { method: 'POST' }),
      (r) => (r.status === 'needs_judge' ? 'Сумма выше порога — ждём судью' : 'Сделка состоялась!'));
  }
  if ((el = q('[data-tr-decline]'))) {
    return act(() => api(`/api/transfers/exchange/${el.dataset.trDecline}/decline`, { method: 'POST' }),
      (r) => (r.status === 'cancelled' ? 'Предложение отозвано' : 'Предложение отклонено'));
  }
  if (q('[data-tr-ex-start]')) { tr.ex = { step: 'club', mode: 'money', money: 0, give: null }; return exRender(); }
  if (tr.ex) {
    if ((el = q('[data-tr-ex-club]'))) {
      tr.ex.club = tr.ex.clubs.find((c) => c.id === Number(el.dataset.trExClub));
      tr.ex.squad = null; tr.ex.step = 'want';
      return exRender();
    }
    if ((el = q('[data-tr-ex-want]'))) {
      tr.ex.want = tr.ex.squad.find((s) => s.id === Number(el.dataset.trExWant));
      tr.ex.step = 'give';
      return exRender();
    }
    if ((el = q('[data-tr-ex-mode]'))) {
      const m = $('#tr-ex-money'); if (m) tr.ex.money = parseMoney(m.value);
      tr.ex.mode = el.dataset.trExMode; return exRender();
    }
    if ((el = q('[data-tr-ex-give]'))) {
      const m = $('#tr-ex-money'); if (m) tr.ex.money = parseMoney(m.value);
      tr.ex.give = Number(el.dataset.trExGive); return exRender();
    }
    if (q('[data-tr-ex-back]')) { tr.ex.step = tr.ex.step === 'give' ? 'want' : 'club'; return exRender(); }
    if (q('[data-tr-ex-send]')) return exSend();
  }
  if ((el = q('[data-tr-judge]'))) {
    const ok = el.dataset.ok === '1';
    el.closest('.tr-actions')?.querySelectorAll('button').forEach((b) => { b.disabled = true; });
    const r = await act(() => api(`/api/transfers/deals/${el.dataset.trJudge}/decide`, { method: 'POST', body: JSON.stringify({ approve: ok }) }),
      (r) => (r.status === 'approved' ? 'Сделка одобрена' : 'Сделка отклонена'));
    if (!r) el.closest('.tr-actions')?.querySelectorAll('button').forEach((b) => { b.disabled = false; });
    return;
  }
  if ((el = q('[data-tr-lot]'))) return openLotSheet(Number(el.dataset.trLot));
  if ((el = q('[data-tr-pos]'))) {
    tr.filterPos = el.dataset.trPos || null;
    if (tr.tab === 'market') renderMarket();
    return;
  }
  const tab = q('#tabs-transfers .tab');
  if (tab) {
    document.querySelectorAll('#tabs-transfers .tab').forEach((x) => x.classList.remove('active'));
    tab.classList.add('active');
    tr.tab = tab.dataset.tab;
    renderTransfers();
  }
});

// живые ставки: пока открыт рынок — тихо обновляем раз в 20 с (лист лота не трогаем)
setInterval(() => {
  const onView = document.querySelector('#view-transfers.active');
  const sheetOpen = $('#generic-sheet') && !$('#generic-sheet').hidden;
  if (onView && !sheetOpen && tr.tab === 'market' && !document.hidden) renderTransfers();
}, 20000);

hooks.views.transfers = renderTransfers;
