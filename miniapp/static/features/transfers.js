import { $, api, esc, fmt, hooks, openSheet, toast, logoHtml } from '../lib.js';
import { cardTile, normCard, loadQueue, queueHtml, onCardsChanged, STATS } from './cards.js';

/* ===== трансферы: рынок, аукционы, обмены, судья (план 08 B2–B3) ===== */

const tr = {
  data: null, tab: 'market', filterPos: null, judge: [], cardQueue: [],
  lotId: null, sell: null, ex: null, busy: false,
  q: '', f: {}, facets: { nations: [], leagues: [] }, marketSeq: 0,
};
// коды FC Mobile + русская подсказка на чипе
const POSITIONS = [['', 'Все'], ['GK', 'ВРТ'], ['CB', 'ЦЗ'], ['LB', 'ЛЗ'], ['RB', 'ПЗ'], ['CDM', 'ЦОП'], ['CM', 'ЦП'], ['CAM', 'ЦАП'],
  ['LM', 'ЛП'], ['RM', 'ПП'], ['LW', 'ЛВ'], ['RW', 'ПВ'], ['CF', 'ФРВ'], ['ST', 'НАП']];
const SORTS = [['new', 'Новые'], ['price_asc', 'Цена ↑'], ['price_desc', 'Цена ↓'], ['ovr_desc', 'OVR ↓'], ['ending', 'Скоро конец']];
const KINDS = [['', 'Все'], ['fix', 'Фикс'], ['auction', 'Аукцион'], ['agent', 'Агенты']];
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
  let judge = { deals: [] }, queue = null;
  try {
    [tr.data, judge, queue] = await Promise.all([
      api('/api/transfers/view'),
      api('/api/transfers/judge').catch(() => ({ deals: [] })),
      loadQueue(),
    ]);
  } catch (e) {
    body().innerHTML = `<div class="empty-note">${esc(e.message)}</div>`;
    return;
  }
  tr.judge = judge.deals || [];
  tr.cardQueue = queue || [];
  syncJudgeTab();
  if (tr.data.no_club) {
    $('#tr-budget').textContent = '—';
    setLocked(0);
    if (tr.tab === 'judge' && judgeCount()) return renderJudge();
    setMarketUi(false);
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

const judgeCount = () => tr.judge.length + tr.cardQueue.length;

function syncJudgeTab() {
  const tabs = $('#tabs-transfers');
  let btn = tabs.querySelector('[data-tab="judge"]');
  const n = judgeCount();
  if (n && !btn) {
    btn = document.createElement('button');
    btn.className = 'tab';
    btn.dataset.tab = 'judge';
    tabs.appendChild(btn);
  }
  if (btn && !n) {
    btn.remove();
    if (tr.tab === 'judge') { tr.tab = 'market'; tabs.querySelector('[data-tab="market"]')?.classList.add('active'); }
    return;
  }
  if (btn) {
    btn.innerHTML = `⚖️<span class="tr-badge">${n}</span>`;
    btn.title = 'Судья: сделки и карточки на проверку';
    btn.classList.toggle('active', tr.tab === 'judge');
  }
}

/* ===== маркет: поиск, фильтры, плитки карточек ===== */

const bidsTxt = (n) => (n ? `${n} ${plural(n, 'ставка', 'ставки', 'ставок')}` : 'без ставок');
const packActions = (html) => ` data-card-actions="${encodeURIComponent(html)}"`;

/* строка лота/агента: плитка карточки + цена и кнопка; тап по строке открывает лист карточки */
function lotTile(l, opts = {}) {
  const isAgent = opts.agent;
  const isAuction = !isAgent && l.kind === 'auction';
  const price = isAgent ? l.effective_price : isAuction ? l.current_price : l.price;
  let btn = opts.action;
  let sheetAct = '';
  if (btn == null) {
    if (isAgent) {
      btn = `<button class="lot-btn" data-tr-sign="${l.id}">Купить</button>`;
      sheetAct = `<button class="place-btn" data-tr-sign="${l.id}">Подписать за ${fmt(price)} ₼</button>`;
    } else if (isAuction) {
      btn = opts.noBtn ? '' : `<button class="lot-btn" data-tr-bid="${l.id}">Ставка</button>`;
      sheetAct = `<button class="place-btn" data-tr-bid="${l.id}">🔨 Ставка · сейчас ${fmt(price)} ₼</button>`;
    } else {
      btn = `<button class="lot-btn" data-tr-buy="${l.id}" data-price="${l.price}">Купить</button>`;
      sheetAct = `<button class="place-btn" data-tr-buy="${l.id}" data-price="${l.price}">Купить за ${fmt(price)} ₼</button>`;
    }
  }
  const my = opts.my != null
    ? (l.top_club_id === tr.data?.club_id
      ? `<span class="auc-state lead">лидируешь · ${fmt(opts.my)} ₼</span>`
      : '<span class="auc-state out">перебили</span>')
    : '';
  const sub = isAuction
    ? `<div class="auc-meta"><span class="auc-tag">аукцион</span><span>${bidsTxt(l.bids_count || 0)}</span></div>${l.buyout_price ? `<div class="auc-buyout">выкуп ${fmt(l.buyout_price)} ₼</div>` : ''}${my}`
    : '';
  const right = `<div class="lot-price">${fmt(price)} ₼</div>${isAuction && l.status !== 'needs_judge' ? timerHtml(l.seconds_left) : ''}${btn || ''}`;
  const seller = opts.meta ?? (isAgent ? 'свободен' : esc(l.seller_name || ''));
  return cardTile(l, { cls: isAuction ? 'auc-card' : '', right, sub, meta: seller, attrs: sheetAct && !opts.noSheetAct ? packActions(sheetAct) : '' });
}

function activeFilters() {
  const f = tr.f;
  let n = 0;
  ['ovr_min', 'ovr_max', 'price_min', 'price_max', 'kind', 'nation', 'league'].forEach((k) => { if (f[k]) n++; });
  if (f.verified) n++;
  if (f.alt) n++;
  STATS.forEach(([k]) => { if (f[`${k}_min`]) n++; });
  return n;
}

function marketQuery() {
  const p = new URLSearchParams();
  if (tr.q) p.set('q', tr.q);
  if (tr.filterPos) p.set('position', tr.filterPos);
  Object.entries(tr.f).forEach(([k, v]) => { if (v !== '' && v != null && v !== false) p.set(k, v === true ? '1' : v); });
  const s = p.toString();
  return s ? `?${s}` : '';
}

function setMarketUi(on) {
  $('#tr-filters').hidden = !on;
  const bar = $('#tr-search-bar');
  if (bar) bar.hidden = !on;
}

function ensureSearchBar() {
  let bar = $('#tr-search-bar');
  if (!bar) {
    bar = document.createElement('div');
    bar.id = 'tr-search-bar';
    bar.innerHTML = `<div class="mk-bar">
        <label class="mk-search"><input id="tr-q" type="search" placeholder="Игрок, клуб, сборная…" autocomplete="off" enterkeyhint="search">
        <button class="mk-clear" data-tr-q-clear hidden aria-label="Очистить">✕</button></label>
        <button class="mk-fbtn" data-tr-filters>⚙️ Фильтры<span class="tr-badge" hidden></span></button>
      </div>
      <div class="mk-active" id="tr-active" hidden></div>`;
    $('#tr-filters').insertAdjacentElement('beforebegin', bar);
    $('#tr-q').value = tr.q;
  }
  bar.hidden = false;
  const n = activeFilters();
  const fb = bar.querySelector('.mk-fbtn');
  fb.classList.toggle('on', n > 0);
  const badge = fb.querySelector('.tr-badge');
  badge.hidden = !n;
  badge.textContent = n;
  bar.querySelector('[data-tr-q-clear]').hidden = !tr.q;
  const act = $('#tr-active');
  const sortLbl = tr.f.sort && tr.f.sort !== 'new' ? SORTS.find(([v]) => v === tr.f.sort)?.[1] : '';
  act.hidden = !n && !sortLbl;
  act.innerHTML = `${n ? `<span>${n} ${plural(n, 'фильтр', 'фильтра', 'фильтров')}</span><button data-tr-freset>сбросить</button>` : ''}${sortLbl ? `<span class="mk-sort">сортировка: ${esc(sortLbl)}</span>` : ''}`;
}

async function renderMarket() {
  const f = $('#tr-filters');
  setMarketUi(true);
  ensureSearchBar();
  f.innerHTML = POSITIONS.map(([p, ru]) => `<button class="chip ${(tr.filterPos || '') === p ? 'active' : ''}" data-tr-pos="${p}" title="${ru}">${p || ru}${p ? `<small>${ru}</small>` : ''}</button>`).join('');
  const seq = ++tr.marketSeq;
  let market;
  try { market = await api(`/api/transfers/market${marketQuery()}`); } catch (e) {
    if (seq === tr.marketSeq) body().innerHTML = `<div class="empty-note">${esc(e.message)}</div>`;
    return;
  }
  if (seq !== tr.marketSeq || tr.tab !== 'market') return;  // пришёл устаревший ответ
  const { lots, free_agents } = market;
  if (market.facets) tr.facets = market.facets;
  const auctions = lots.filter((l) => l.kind === 'auction');
  const fixed = lots.filter((l) => l.kind !== 'auction');
  const kind = tr.f.kind || '';
  const filtering = !!(tr.q || tr.filterPos || activeFilters());
  const mine = [...(tr.data.bids || []).map((b) => ({ ...b, _my: b.my_bid })), ...(tr.data.outbid || []).map((b) => ({ ...b, _my: 0 }))];
  const group = (title, list, html, emptyTxt) => {
    if (filtering && !list.length) return '';
    return `<div class="group-title">${title}</div>` + (list.length ? list.map(html).join('') : `<div class="empty-note">${emptyTxt}</div>`);
  };
  let out = (mine.length && !filtering ? '<div class="group-title">Мои ставки</div>' + mine.map((l) => lotTile(l, { my: l._my, noBtn: true })).join('') : '');
  if (!kind || kind === 'auction') out += group('Аукционы', auctions, (l) => lotTile(l), 'Аукционов сейчас нет.');
  if (!kind || kind === 'fix') out += group('Фикс-цена', fixed, (l) => lotTile(l), 'Лотов пока нет.');
  if ((!kind || kind === 'agent') && free_agents.length) out += group('Свободные агенты · рейтинг² × K', free_agents, (a) => lotTile(a, { agent: true }), '');
  if (filtering && !lots.length && !free_agents.length) {
    out += '<div class="empty-note">По запросу ничего не нашлось.<br><button class="lot-btn secondary" data-tr-freset style="margin-top:10px">Сбросить фильтры</button></div>';
  }
  body().innerHTML = out;
}

/* ===== лист фильтров ===== */

function openFilterSheet() {
  const f = tr.f;
  const opt = (list, cur) => ['<option value="">Любая</option>', ...(list || []).map((x) => `<option value="${esc(x)}" ${x === cur ? 'selected' : ''}>${esc(x)}</option>`)].join('');
  const num = (id, ph) => `<input class="amount-input cf-in" id="trf-${id}" inputmode="numeric" placeholder="${ph}" value="${f[id] ? esc(id.startsWith('price') ? fmt(f[id]) : f[id]) : ''}" autocomplete="off">`;
  openSheet('⚙️ Фильтры рынка', `<div class="cf mk-sheet">
    <div class="cf-lbl">Сортировка</div>
    <select class="amount-input cf-in" id="trf-sort">${SORTS.map(([v, l]) => `<option value="${v}" ${(f.sort || 'new') === v ? 'selected' : ''}>${l}</option>`).join('')}</select>
    <div class="cf-lbl">Тип</div>
    <div class="tr-seg">${KINDS.map(([v, l]) => `<button type="button" class="chip ${(f.kind || '') === v ? 'active' : ''}" data-trf-kind="${v}">${l}</button>`).join('')}</div>
    <div class="cf-lbl">OVR</div>
    <div class="mk-range">${num('ovr_min', 'от 40')}<span>—</span>${num('ovr_max', 'до 150')}</div>
    <div class="cf-lbl">Цена, ₼</div>
    <div class="mk-range">${num('price_min', 'от')}<span>—</span>${num('price_max', 'до')}</div>
    <div class="cf-grid2">
      <label><span class="cf-lbl">Сборная</span><select class="amount-input cf-in" id="trf-nation">${opt(tr.facets.nations, f.nation)}</select></label>
      <label><span class="cf-lbl">Лига</span><select class="amount-input cf-in" id="trf-league">${opt(tr.facets.leagues, f.league)}</select></label>
    </div>
    <div class="cf-lbl">Минимум по характеристикам</div>
    <div class="cf-stats">${STATS.map(([k, lbl, ru]) => `<label><span>${lbl}<i>${ru}</i></span><input class="amount-input cf-in" id="trf-${k}_min" type="number" inputmode="numeric" min="0" max="200" placeholder="—" value="${esc(f[`${k}_min`] || '')}"></label>`).join('')}</div>
    <label class="mk-toggle"><span>Искать и по доп. позициям</span><input type="checkbox" id="trf-alt" ${f.alt ? 'checked' : ''}></label>
    <label class="mk-toggle"><span>Только сверенные с RenderZ ✅</span><input type="checkbox" id="trf-verified" ${f.verified ? 'checked' : ''}></label>
    <div class="mk-btns"><button class="lot-btn secondary" data-tr-freset>Сбросить</button><button class="place-btn" data-tr-fapply>Показать</button></div>
  </div>`);
  tr.fKind = f.kind || '';
}

function applyFilterSheet() {
  const v = (id) => $(`#trf-${id}`)?.value.trim() ?? '';
  const n = (id) => { const x = parseMoney(v(id)); return x || ''; };
  const f = {
    sort: v('sort') === 'new' ? '' : v('sort'), kind: tr.fKind || '',
    ovr_min: n('ovr_min'), ovr_max: n('ovr_max'), price_min: n('price_min'), price_max: n('price_max'),
    nation: v('nation'), league: v('league'),
    alt: $('#trf-alt')?.checked || false, verified: $('#trf-verified')?.checked || false,
  };
  STATS.forEach(([k]) => { f[`${k}_min`] = n(`${k}_min`); });
  if (f.ovr_min && f.ovr_max && f.ovr_min > f.ovr_max) [f.ovr_min, f.ovr_max] = [f.ovr_max, f.ovr_min];
  if (f.price_min && f.price_max && f.price_min > f.price_max) [f.price_min, f.price_max] = [f.price_max, f.price_min];
  tr.f = Object.fromEntries(Object.entries(f).filter(([, x]) => x !== '' && x !== false));
  $('#generic-sheet').hidden = true;
  renderMarket();
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
    ${lot.card_id ? `<button class="lot-btn secondary tr-card-link" data-card-view="${lot.card_id}">🃏 Карточка и характеристики</button>` : ''}
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
  setMarketUi(false);
  const d = tr.data;
  const judgeTag = '<span class="bh-status open">у судьи</span>';
  const myLots = d.lots.map((l) => (l.kind === 'auction'
    ? lotTile(l, {
      meta: l.top_club_name ? `лидер ${esc(l.top_club_name)}` : 'ставок нет',
      noSheetAct: true,
      action: l.status === 'open'
        ? `<button class="lot-btn secondary" data-tr-unlot="${l.id}" ${l.top_bid ? 'disabled title="Есть ставки"' : ''}>Снять</button>`
        : judgeTag,
    })
    : lotTile(l, {
      meta: 'фикс', noSheetAct: true,
      action: l.status === 'open' ? `<button class="lot-btn secondary" data-tr-unlot="${l.id}">Снять</button>` : judgeTag,
    })));
  const sellState = (s) => {
    const st = normCard(s).vstatus;
    if (s.on_market) return '<span class="bh-status open">на рынке</span>';
    if (st === 'pending') return '<span class="bh-status open">⏳ проверка</span>';
    if (st === 'rejected') return '<span class="bh-status lost">❌ отклонена</span>';
    return `<button class="lot-btn" data-tr-sell="${s.id}">Продать</button>`;
  };
  const blocked = d.squad.filter((s) => normCard(s).vstatus !== 'approved').length;
  body().innerHTML =
    '<div class="group-title">Мои лоты</div>' +
    (myLots.length ? myLots.join('') : '<div class="empty-note">Активных лотов нет.</div>') +
    '<div class="group-title">Состав · выставить на рынок</div>' +
    (blocked ? '<div class="sub tr-note" style="margin:-2px 2px 8px">Продавать можно только проверенные карточки ✅.</div>' : '') +
    (d.squad.length ? d.squad.map((s) => cardTile(s, {
      meta: normCard(s).vstatus === 'approved' ? `ориентир ${fmt(s.sell_value)} ₼` : '',
      right: sellState(s),
    })).join('') : '<div class="empty-note">Состав пуст.</div>');
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
  setMarketUi(false);
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
  setMarketUi(false);
  const kinds = { lot: 'Покупка лота', auction: 'Аукцион', exchange: 'Обмен', offer: 'Денежное предложение' };
  const cards = tr.cardQueue.length
    ? `<div class="group-title">🃏 Карточки на проверку · ${tr.cardQueue.length}</div>
      <div class="sub tr-note" style="margin:-2px 2px 8px">Сверь фото, OCR и RenderZ. Тап по карточке — подробности.</div>${queueHtml(tr.cardQueue)}`
    : '';
  body().innerHTML = cards + (cards && !tr.judge.length ? '' : '<div class="group-title">⚖️ Сделки</div>') +
    (cards && !tr.judge.length ? '' : '<div class="sub tr-note" style="margin-bottom:10px">Сделки выше порога ждут решения. Проверь цену и связь клубов (анти-сговор).</div>') +
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
    }).join('') : cards ? '' : '<div class="empty-note">Очередь пуста.</div>');
}

/* ===== история ===== */

function renderHistory() {
  setMarketUi(false);
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
  if (id === 'tr-q') {
    clearTimeout(tr.qTimer);
    const clr = $('[data-tr-q-clear]');
    if (clr) clr.hidden = !ev.target.value;
    tr.qTimer = setTimeout(() => {
      const v = ev.target.value.trim();
      if (v === tr.q) return;
      tr.q = v;
      if (tr.tab === 'market') renderMarket();
    }, 350);
  } else if (id === 'tr-bid-amount') {
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
    const r = await act(() => api(`/api/transfers/free-agent/${el.dataset.trSign}/sign`, { method: 'POST' }), (r) => `Агент подписан за ${fmt(r.price)} ₼`);
    if (r && el.closest('#generic-sheet')) $('#generic-sheet').hidden = true;
    return;
  }
  if (q('[data-tr-filters]')) return openFilterSheet();
  if ((el = q('[data-trf-kind]'))) {
    tr.fKind = el.dataset.trfKind;
    el.parentElement.querySelectorAll('.chip').forEach((c) => c.classList.toggle('active', c === el));
    return;
  }
  if (q('[data-tr-fapply]')) return applyFilterSheet();
  if (q('[data-tr-freset]')) {
    tr.f = {}; tr.filterPos = null; tr.q = '';
    const inp = $('#tr-q'); if (inp) inp.value = '';
    const sheet = $('#generic-sheet');
    if (sheet && q('#generic-sheet')) sheet.hidden = true;
    if (tr.tab === 'market') renderMarket();
    return;
  }
  if (q('[data-tr-q-clear]')) {
    ev.preventDefault();
    tr.q = ''; $('#tr-q').value = '';
    if (tr.tab === 'market') renderMarket();
    return;
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
onCardsChanged(() => { if (document.querySelector('#view-transfers.active')) renderTransfers(); });
