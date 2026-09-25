import { $, api, esc, fmt, hooks, toast } from '../lib.js';

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

hooks.views.transfers = renderTransfers;
