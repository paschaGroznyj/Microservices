/* Память чата (только просмотр): строка «Журнал · Саммари · Контекст» под полем ввода,
   полоска заполнения контекста в верхней кромке поля, карточка-карусель над полем, модалка.
   Данные: GET /api/chats/{id}/memory/{stats|journal|summary} и /api/chats/{id}/steps (memory_gw → agent_service).
   Вызовы из app.js / app_sigma.js: MemoryPanel.mount(chatId) после renderMessageInput,
   MemoryPanel.update(chatId, messageId, context) по финалу ответа: context последнего шага
   приходит в payload ответа (agent_service → Kafka → гейтвей) и рисуется сразу, без опроса. */
(function () {
'use strict';
const S = (p, c = 'mm-ic', sw = 1.8) => `<svg class="${c}" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${p}</svg>`;
const P = {
  j: '<path d="M5 4.5A1.5 1.5 0 0 1 6.5 3H19v14H6.5A1.5 1.5 0 0 0 5 18.5v-14z"/><path d="M5 18.5A1.5 1.5 0 0 0 6.5 20H19"/><path d="M9 7.5h6"/>',
  s: '<path d="M4 6h16M4 12h11M4 18h7"/>', x: '<path d="M6 6l12 12M18 6L6 18"/>',
  l: '<path d="M15 6l-6 6 6 6"/>', r: '<path d="M9 6l6 6-6 6"/>',
  full: '<path d="M4 9V4h5M20 9V4h-5M4 15v5h5M20 15v5h-5"/>',
  // Шаги агента: ступня в греческой сандалии (профиль, ремешок на щиколотке и через подъём)
  t: '<path d="M8.2 6.5c-.1 2.4.1 4.3-.5 6.3-.5 1.6-1.9 2.6-2.1 4.1-.1 1.1.6 1.9 1.7 1.9h11.9c1 0 1.6-.7 1.3-1.5-.3-.7-1.1-1-2-1.1l-1.5 0c-2.2-.4-3.8-1.4-4.7-3.1-.6-1.2-.8-2.5-.9-4l-.1-2.6"/><path d="M6.4 11.6h6.3M10.8 18.8l2.5-5.1"/>',
};
const TYPE = { evolution: 'эволюция', summary: 'сводка', anomaly: 'аномалия', user_preference: 'предпочтение', identity: 'личность' };
const PAGE = 20;
const esc = s => String(s ?? '').replace(/[&<>"]/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;' }[c]));
const plain = s => String(s ?? '').replace(/^\s*\|?[-:| ]{3,}\|?\s*$/gm, '').replace(/^#+\s*/gm, '').replace(/\*\*|`/g, '')
  .replace(/\s*\|\s*/g, ' · ').replace(/^\s*-\s+/gm, '• ').replace(/\s+/g, ' ').replace(/(\s*·\s*){2,}/g, ' · ').replace(/^ ?· ?/, '').replace(/ ?· ?$/, '').trim();
const plural = (n, a, b, c) => { n = Math.abs(n) % 100; const k = n % 10; return n > 10 && n < 20 ? c : k === 1 ? a : k > 1 && k < 5 ? b : c; };
const fmtK = n => n >= 1000 ? (n / 1000).toFixed(1).replace('.', ',') + 'K' : String(n);
const nf = n => Number(n || 0).toLocaleString('ru');
const dt = iso => { if (!iso) return ''; const d = new Date(iso); const p = x => String(x).padStart(2, '0');
  return `${p(d.getDate())}.${p(d.getMonth() + 1)} ${p(d.getHours())}:${p(d.getMinutes())}`; };
const H = h => { const t = document.createElement('template'); t.innerHTML = h.trim(); return t.content.firstElementChild; };
function ring(pct) { const r = 5, C = 2 * Math.PI * r; pct = Math.max(0, Math.min(1, pct || 0));
  return `<svg class="mm-ring" viewBox="0 0 14 14" aria-hidden="true"><circle class="t" cx="7" cy="7" r="${r}" fill="none" stroke-width="2.2"/><circle class="a" cx="7" cy="7" r="${r}" fill="none" stroke-width="2.2" stroke-dasharray="${(C * pct).toFixed(2)} ${C.toFixed(2)}"/></svg>`; }

const st = { chat: null, data: null, entries: [], total: 0, loading: false, open: false, tab: 'j', i: 0, ctx: false, seq: 0, live: null, runs: null, tload: false, terr: false, tp: null };

function q() {
  const box = document.getElementById('chat-input-box');
  const area = box ? box.closest('.chat-input-area') : null;
  return { box, area };
}
async function api(tail, params, base = 'memory') {
  const u = new URL(`/api/chats/${encodeURIComponent(st.chat)}/${base}${tail ? '/' + tail : ''}`, location.origin);
  Object.entries(params || {}).forEach(([k, v]) => u.searchParams.set(k, v));
  const r = await fetch(u, { credentials: 'same-origin', cache: 'no-store', signal: st.abort?.signal });
  if (!r.ok) throw new Error('HTTP ' + r.status);
  return r.json();
}
function cov(sm) { return sm && sm.total_messages ? Math.round(sm.covered / sm.total_messages * 100) : 0; }

/* ── строка статуса и полоска ── */
function render() {
  const { box, area } = q(); if (!box || !area) return;
  area.querySelectorAll('[data-mm]').forEach(e => e.remove());
  const D = st.data || {journal: {active: 0, total_chars: 0}, summary: {text: ''}, context: {tokens: null, max_tokens: null}};
  const j = D.journal, sm = D.summary, c = D.context || {};
  const ag = c.agent && Number.isInteger(c.agent.steps) ? c.agent : null;
  const note = area.querySelector('.input-footer-note');
  if (note) note.style.display = 'none';
  const ctxTxt = c.tokens ? `<b>${fmtK(c.tokens)}${c.max_tokens ? `<span class="mm-lim">/${fmtK(c.max_tokens).replace(',0K', 'K')}</span>` : ''}</b>${c.cache_pct != null ? `<span class="mm-x">· кэш ${c.cache_pct}%</span>` : ''}` : '<b>—</b>';
  const el = H(`<div class="mm-sline" data-mm>
   <button type="button" class="mm-it" data-go="j" aria-expanded="false">${S(P.j)}<span class="mm-lb">Журнал</span> <b>${j.active}</b><span class="mm-x"> · ${fmtK(j.total_chars)} зн.</span></button><span class="mm-dot"></span>
   <button type="button" class="mm-it" data-go="s" aria-expanded="false">${S(P.s)}<span class="mm-lb">Саммари</span> <b>${sm.text ? cov(sm) + '%' : 'нет'}</b>${sm.text ? '<span class="mm-x"> истории</span>' : ''}</button><span class="mm-dot"></span>
   <button type="button" class="mm-it" data-go="c" aria-expanded="false" title="Промпт последнего шага. /200K — порог сжатия агентного режима, не окно модели; в режиме только модели не применяется">${ring(c.tokens / c.max_tokens)}<span class="mm-lb">Контекст</span> ${ctxTxt}</button><span class="mm-dot"></span>
   <button type="button" class="mm-it mm-it-t" data-go="t" aria-expanded="false" title="${ag ? `Последний ответ: ${ag.steps} ${plural(ag.steps, 'шаг', 'шага', 'шагов')} агента, ${ag.tool_calls} ${plural(ag.tool_calls, 'вызов', 'вызова', 'вызовов')} инструментов` : 'Шаги агента и вызовы инструментов'}">${S(P.t)}<span class="mm-lb">Шаги</span> <b>${ag ? ag.steps : '—'}</b>${ag ? `<span class="mm-tq"><span class="mm-tsep">·</span>${ag.tool_calls}<span class="mm-tw"> ${plural(ag.tool_calls, 'вызов', 'вызова', 'вызовов')}</span></span>` : ''}</button></div>`);
  box.after(el);
  el.addEventListener('click', e => { const b = e.target.closest('[data-go]'); if (!b) return; const g = b.dataset.go;
    if (st.open && st.tab === g) { closeCard(); return; } openCard(g); });
  if (c.tokens && c.max_tokens) {
    const w = Math.min(100, c.tokens / c.max_tokens * 100);
    box.prepend(H(`<div class="mm-bud" data-mm title="Контекст ${fmtK(c.tokens)} из ${c.max_tokens / 1000}K">${contextSegments(c) ? contextSegments(c).map((p,i,a)=>`<i class="${p.cls}" title="${p.label}: ≈${nf(p.tokens)} (оценка)" style="left:${a.slice(0,i).reduce((v,x)=>v+x.tokens,0)/c.tokens*w}%;width:${p.tokens/c.tokens*w}%"></i>`).join('') : `<i class="mm-c2" style="left:0;width:${w}%"></i>`}</div>`));
  }
  align();
  if (st.open) openCard(st.tab, st.i);
}
const contextLabels = ['Система', 'Журнал', 'Саммари', 'История', 'Трасса инструментов', 'Схемы инструментов'];
const contextKeys = ['system', 'journal', 'summary', 'history', 'tool_trace', 'tools'];
function contextSegments(c) {
  const b = c.context_breakdown;
  if (!b || b.estimated !== true || !b.tokens || !c.tokens) return null;
  const values = contextKeys.map(k => b.tokens[k]);
  if (values.some(v => !Number.isInteger(v) || v < 0) || values.reduce((a,b)=>a+b,0) !== c.tokens) return null;
  return values.map((tokens,i)=>({tokens, label:contextLabels[i], cls:`mm-part-${i}`}));
}
// Контекст — третья вкладка карточки mm-card5, в том же стиле, что журнал и саммари.
function toggleCtx(force) {
  const want = force ?? !(st.open && st.tab === 'c');
  if (want) openCard('c', 0); else if (st.open && st.tab === 'c') closeCard();
}
const EMPTY = { journal: { active: 0, total_chars: 0, types: [] }, summary: { text: '' }, context: {} };
const pctOf = (v, t) => { const p = v / t * 100; return p > 0 && p < 1 ? '<1' : String(Math.round(p)); };
// Проценты блоков методом наибольшего остатка: в сумме ровно 100, ненулевой блок не показывается как 0.
function pctList(vals, total) {
  const raw = vals.map(v => v / total * 100), out = raw.map(Math.floor);
  let left = 100 - out.reduce((a, b) => a + b, 0);
  raw.map((r, i) => [r - out[i], i]).sort((a, b) => b[0] - a[0]).forEach(([, i]) => { if (left > 0) { out[i]++; left--; } });
  return out.map((v, i) => vals[i] > 0 && v === 0 ? '<1' : String(v));
}
function cSlide() {
  const c = (st.data || EMPTY).context || {};
  if (!c.tokens) return '<div class="mm-sl"><div class="mm-empty">Нет данных о контексте: ответов агента после обновления ещё не было.</div></div>';
  const segs = contextSegments(c);
  const fill = c.max_tokens ? ` · ${pctOf(c.tokens, c.max_tokens)}% порога` : '';
  const bar = segs
    ? segs.filter(p => p.tokens > 0).map(p => `<i class="${p.cls}" title="${p.label} ≈${nf(p.tokens)}" style="flex:${p.tokens} 1 0"></i>`).join('')
    : '<i class="mm-c2" style="flex:1 1 0"></i>';
  const body = segs
    ? (pp => `<div class="mm-crows">${segs.map((p, i) => `<div class="mm-crow"><i class="${p.cls}"></i><span class="mm-cl">${p.label}</span><span class="mm-cv">≈${fmtK(p.tokens)}</span><span class="mm-cp">${pp[i]}%</span></div>`).join('')}</div>`)(pctList(segs.map(p => p.tokens), c.tokens))
    : '<div class="mm-cnote" style="margin-top:0">Состав неизвестен: ответ сохранён до разбивки промпта по блокам.</div>';
  return `<div class="mm-sl mm-sl-c"><div class="mm-sl-h"><span class="mm-tag">промпт</span><span class="mm-meta">${fmtK(c.tokens)} ток.${c.max_tokens ? ` из ${fmtK(c.max_tokens).replace(',0K', 'K')}` : ''}${fill}</span></div>
   <div class="mm-cbar" role="img" aria-label="Состав промпта последнего шага">${bar}</div>${body}
   <div class="mm-cnote">${segs ? '≈ — оценка блоков, итог — фактический счёт модели. ' : ''}${c.max_tokens ? `${fmtK(c.max_tokens).replace(',0K', 'K')} — порог сжатия агентного режима, не окно модели.` : 'Порог сжатия неизвестен.'}</div></div>`;
}

/* ── карточка-карусель ── */
const N = () => st.tab === 'j' ? st.total : st.tab === 't' ? Math.max(1, (st.runs || []).length) : 1;  // у саммари и контекста один слайд
// Шаги: слайд на ответ агента — шаги цикла, вызовы тулов, сводка по тулам и разбивка по шагам.
function tSlide(r) {
  const tools = r.tools.slice(0, 6), more = r.tools.length - tools.length;
  const rows = tools.map(t => `<div class="mm-crow"><i class="mm-tdot"></i><span class="mm-cl">${esc(t.name)}</span><span class="mm-cv">× ${t.count}</span></div>`).join('');
  const seq = r.by_step.map(s => `<span class="mm-ts" title="Шаг ${s.step}: ${esc(s.calls.map(c => c.name).join(', '))}"><b>${s.step}</b>${s.calls.length > 1 ? `×${s.calls.length}` : ''}${s.calls.some(c => !c.ok) ? '<em>!</em>' : ''}</span>`).join('');
  return `<div class="mm-sl mm-sl-t" data-mid="${esc(r.message_id)}"><div class="mm-sl-h"><span class="mm-tag">ответ</span><span class="mm-meta">${dt(r.created_at)}${r.model ? ' · ' + esc(r.model) : ''}</span></div>
   <div class="mm-tsum"><span><b>${r.steps}</b> ${plural(r.steps, 'шаг', 'шага', 'шагов')}</span><span><b>${r.tool_calls}</b> ${plural(r.tool_calls, 'вызов', 'вызова', 'вызовов')}</span>${r.failed ? `<span class="mm-terr"><b>${r.failed}</b> с ошибкой</span>` : ''}</div>
   ${r.tool_calls ? `<div class="mm-crows mm-trows">${rows}${more > 0 ? `<div class="mm-crow"><span class="mm-cl mm-cmore">ещё ${more} ${plural(more, 'тул', 'тула', 'тулов')}</span></div>` : ''}</div>
   <div class="mm-tseq" aria-label="Шаги с вызовами инструментов">${seq}</div>` : '<div class="mm-empty mm-empty-in">Ответ без вызова инструментов.</div>'}</div>`;
}
function tEmpty() {
  return `<div class="mm-sl"><div class="mm-empty">${st.tload ? 'Загрузка…' : st.terr ? 'Не удалось загрузить шаги.' : 'Нет данных о шагах: ответов агента после обновления ещё не было.'}</div></div>`;
}
function loadSteps() {
  if (st.tp) return st.tp;
  const seq = st.seq; st.tload = true; st.terr = false;
  const p = (async () => {
    try { const r = await api('', { limit: 50 }, 'steps'); if (seq !== st.seq) return; st.runs = r.runs || []; }
    catch (e) { if (seq === st.seq) { st.terr = true; st.runs = null; } console.warn('[memory] steps', e); }
    finally { if (st.tp === p) { st.tp = null; st.tload = false; } }
  })();
  st.tp = p; return p;
}
function jSlide(e) {
  return `<div class="mm-sl" data-id="${e.id}"><div class="mm-sl-h"><span class="mm-tag t-${esc(e.type)}">${esc(TYPE[e.type] || e.type)}</span><span class="mm-meta">#${e.id} · ${dt(e.created_at)} · ${nf(e.chars)} зн.</span></div>
   <div class="mm-txt">${esc(plain(e.content))}</div>
   <div class="mm-sl-f"><button type="button" class="mm-lnk" data-full="${e.id}">${S(P.full)}Открыть полностью</button></div></div>`;
}
function sSlide() {
  const sm = (st.data || EMPTY).summary;
  if (!sm.text) return `<div class="mm-sl"><div class="mm-empty">${st.sload ? 'Загрузка…' : 'Саммари ещё нет: история помещается в контекст целиком.'}</div></div>`;
  return `<div class="mm-sl"><div class="mm-sl-h"><span class="mm-tag t-summary">саммари</span><span class="mm-meta">до #${sm.upto_id} · ${nf(sm.covered)} из ${nf(sm.total_messages)} сообщ. · ${nf(sm.chars)} зн.</span></div>
   <div class="mm-txt">${esc(plain(sm.text))}</div>
   <div class="mm-sl-f"><button type="button" class="mm-lnk" data-full="summary">${S(P.full)}Открыть полностью</button></div></div>`;
}
function loadMore() {
  if (st.jp) return st.jp;
  if (st.entries.length >= st.total && st.total) return Promise.resolve();
  st.loading = true; const seq = st.seq;
  const p = (async () => {
    try { const r = await api('journal', { limit: PAGE, offset: st.entries.length });
      if (seq !== st.seq) return; st.entries.push(...r.entries); st.total = r.total;
    } catch (e) { if (seq === st.seq) st.jerr = true; console.warn('[memory] journal', e); }
    finally { if (st.jp === p) { st.jp = null; st.loading = false; } }
  })();
  st.jp = p; st.jerr = false; return p;
}
// Текст саммари берём отдельным GET /summary при открытии вкладки: в stats он мог ещё не
// прийти (ошибка stats, заглушка из realtime-контекста) или устареть после нового ответа.
function loadSummary() {
  if (st.sp) return st.sp;
  const seq = st.seq; st.sload = true;
  const p = (async () => {
    try { const r = await api('summary');
      if (seq !== st.seq) return;
      st.data ||= { ...EMPTY, context: {} };
      const old = st.data.summary || {};
      st.data.summary = { ...old, ...r, chars: (r.text || '').length, total_messages: old.total_messages ?? r.covered };
      const sb = q().area?.querySelector('.mm-sline [data-go="s"]');  // строку статуса не перерисовываем: render() снял бы карточку
      if (sb) { const sm = st.data.summary; sb.querySelector('b').textContent = sm.text ? cov(sm) + '%' : 'нет';
        if (sm.text && !sb.querySelector('.mm-x')) sb.insertAdjacentHTML('beforeend', '<span class="mm-x"> истории</span>'); }
    } catch (e) { console.warn('[memory] summary', e); }
    finally { if (st.sp === p) { st.sp = null; st.sload = false; } }
  })();
  st.sp = p; return p;
}
// Данные вкладки: журнал — первая страница, если пусто; саммари — свежий текст.
function ensureTab(tab) {
  const done = () => { if (st.open && st.tab === tab) { const k = st.i; renderCard(); st.i = k; place(false); } };
  if (tab === 'j' && !st.entries.length) loadMore().then(done);
  else if (tab === 's') loadSummary().then(done);
  else if (tab === 't' && !st.runs) loadSteps().then(done);
}
function openCard(tab, i) {
  st.open = true; st.tab = tab; st.i = i || 0;
  const { box, area } = q(); if (!box) return; area.querySelector('.mm-card5')?.remove();
  const c = H(`<div class="slash-cmd-card mm-card5" data-mm tabindex="-1" role="region" aria-label="Память чата">
   <div class="mm-c-h"><div class="mm-seg" role="tablist"><button type="button" role="tab" data-tab="j">Журнал <b>${(st.data || EMPTY).journal.active}</b></button><button type="button" role="tab" data-tab="s">Саммари</button><button type="button" role="tab" data-tab="c">Контекст</button><button type="button" role="tab" data-tab="t">Шаги</button></div>
    <span class="mm-sp"></span><span class="mm-cnt" aria-live="polite"></span>
    <button type="button" class="mm-ib" data-nav="-1" aria-label="Предыдущая">${S(P.l)}</button><button type="button" class="mm-ib" data-nav="1" aria-label="Следующая">${S(P.r)}</button>
    <button type="button" class="mm-ib" data-all aria-label="Открыть всё">${S(P.full)}</button><button type="button" class="mm-ib" data-close aria-label="Закрыть">${S(P.x)}</button></div>
   <div class="mm-types"></div><div class="mm-vp"><div class="mm-trk"></div></div><div class="mm-prog"><i></i></div></div>`);
  box.before(c); bindSwipe(c.querySelector('.mm-vp')); pin();
  c.addEventListener('click', e => { const t = e.target.closest('button'); if (!t) return;
    if (t.dataset.tab) { st.tab = t.dataset.tab; st.i = 0; renderCard(); ensureTab(st.tab); }
    else if (t.dataset.nav) go(+t.dataset.nav);
    else if ('close' in t.dataset) closeCard();
    else if ('all' in t.dataset) openModal(st.tab === 'j' ? (st.entries[st.i] || {}).id : 'summary');
    else if (t.dataset.full) openModal(t.dataset.full); });
  renderCard(); ensureTab(tab);
}
function renderCard() {
  const c = document.querySelector('.mm-card5'); if (!c) return; const D = st.data || EMPTY, cx = D.context || {};
  c.querySelectorAll('[data-tab]').forEach(b => { b.classList.toggle('is-on', b.dataset.tab === st.tab); b.setAttribute('aria-selected', b.dataset.tab === st.tab); });
  const sm = D.summary;
  c.querySelector('.mm-types').innerHTML = st.tab === 'j'
    ? (D.journal.types || []).map(t => `<span>${esc(TYPE[t.type] || t.type)} ${t.count}</span>`).join('')
    : st.tab === 't'
    ? ''
    : st.tab === 'c'
    ? (cx.tokens ? `<span>последний шаг</span>${cx.cache_pct != null ? `<span>кэш ${cx.cache_pct}%</span>` : ''}` : '')
    : (sm.text ? `<span>обновлено ${dt(sm.updated_at)}</span>${sm.total_messages > sm.covered ? `<span>вне саммари ${nf(sm.total_messages - sm.covered)} сообщ.</span>` : ''}` : '');
  q().area.querySelectorAll('.mm-sline [data-go]').forEach(b => { const on = b.dataset.go === st.tab; b.classList.toggle('is-on', on); b.setAttribute('aria-expanded', on); });
  const trk = c.querySelector('.mm-trk');
  trk.innerHTML = st.tab === 'j'
    ? (st.entries.length ? st.entries.map(jSlide).join('') : `<div class="mm-sl"><div class="mm-empty">${st.loading ? 'Загрузка…' : st.jerr ? 'Не удалось загрузить журнал.' : 'Журнал пуст.'}</div></div>`)
    : st.tab === 't' ? (st.runs && st.runs.length ? st.runs.map(tSlide).join('') : tEmpty())
    : st.tab === 'c' ? cSlide() : sSlide();
  // «Открыть полностью» только там, где текст не влез в превью
  requestAnimationFrame(() => c.querySelectorAll('.mm-sl').forEach(sl => { const t = sl.querySelector('.mm-txt'), f = sl.querySelector('.mm-sl-f');
    if (t && f) f.style.visibility = t.scrollHeight > t.clientHeight + 1 ? '' : 'hidden'; }));
  c.querySelector('[data-all]').style.display = st.tab === 'c' || st.tab === 't' ? 'none' : '';
  const n = N(); c.querySelectorAll('[data-nav]').forEach(b => b.style.display = n > 1 ? '' : 'none');
  const pr = c.querySelector('.mm-prog'); pr.style.visibility = n > 1 ? '' : 'hidden';
  pr.firstElementChild.style.width = (100 / Math.max(1, n)) + '%';
  c.querySelector('.mm-cnt').textContent = '';
  place(false);
}
function place(anim = true) {
  const c = document.querySelector('.mm-card5'); if (!c) return; const trk = c.querySelector('.mm-trk');
  trk.style.transition = anim ? 'transform .28s cubic-bezier(.2,.7,.2,1)' : 'none';
  trk.style.transform = `translateX(${-st.i * 100}%)`; const n = N();
  c.querySelector('[data-nav="-1"]').disabled = st.i === 0; c.querySelector('[data-nav="1"]').disabled = st.i >= n - 1;
  if ((st.tab === 'j' || st.tab === 't') && n > 1) { c.querySelector('.mm-cnt').textContent = `${st.i + 1} / ${n}`; c.querySelector('.mm-prog i').style.left = (st.i / n * 100) + '%'; }
}
async function go(d) {
  const n = N(); const ni = Math.max(0, Math.min(n - 1, st.i + d)); if (ni === st.i) { bounce(d); return; }
  if (st.tab === 'j' && ni >= st.entries.length) { await loadMore(); if (ni >= st.entries.length) return; st.i = ni; renderCard(); place(false); return; }
  st.i = ni; place();
  if (st.tab === 'j' && st.entries.length - st.i <= 3) loadMore().then(() => { if (st.open && st.tab === 'j') { const k = st.i; renderCard(); st.i = k; place(false); } });
}
function bounce(d) { const trk = document.querySelector('.mm-card5 .mm-trk'); if (!trk) return; trk.style.transition = 'transform .12s';
  trk.style.transform = `translateX(calc(${-st.i * 100}% - ${d * 14}px))`; setTimeout(() => place(), 120); }
// Карточка лежит поверх ленты над полем (position:absolute) и ничего не сдвигает.
// Нижний край держим над верхом поля: поле растёт при наборе — пересчитываем.
const ro = typeof ResizeObserver === 'function' ? new ResizeObserver(() => pin()) : null;
function pin() {
  const { area, box } = q(); const c = area?.querySelector('.mm-card5'); if (!c || !box) return;
  c.style.setProperty('--mm-cb', (area.clientHeight - box.offsetTop + 10) + 'px');
  if (ro && !c._ro) { ro.observe(box); ro.observe(area); c._ro = true; }
}
addEventListener('resize', pin);
function closeCard() {
  ro?.disconnect();
  const { area } = q(); area?.querySelectorAll('.mm-sline [data-go]').forEach(b => { b.classList.remove('is-on'); b.setAttribute('aria-expanded', 'false'); });
  st.open = false; document.querySelector('.mm-card5')?.remove();
}
function bindSwipe(vp) {
  let x0 = null, y0 = 0, dx = 0, lock = null, id = null; const trk = vp.firstElementChild;
  vp.addEventListener('pointerdown', e => { if (e.pointerType === 'mouse' && e.button !== 0) return; if (e.target.closest('button,a')) return; x0 = e.clientX; y0 = e.clientY; dx = 0; lock = null; id = e.pointerId; });
  vp.addEventListener('pointermove', e => { if (x0 === null || e.pointerId !== id) return; dx = e.clientX - x0; const dy = e.clientY - y0;
    if (lock === null && (Math.abs(dx) > 6 || Math.abs(dy) > 6)) { lock = Math.abs(dx) > Math.abs(dy) ? 'x' : 'y'; if (lock === 'x') { try { vp.setPointerCapture(id); } catch (_) {} } }
    if (lock !== 'x') return; const n = N(); let ddx = dx; if ((st.i === 0 && dx > 0) || (st.i === n - 1 && dx < 0)) ddx = dx / 3;
    trk.style.transition = 'none'; trk.style.transform = `translateX(calc(${-st.i * 100}% + ${ddx}px))`; });
  const end = e => { if (x0 === null || e.pointerId !== id) return; const w = vp.clientWidth;
    if (lock === 'x') { const th = Math.min(60, w * .18); if (dx < -th) go(1); else if (dx > th) go(-1); else place(); } x0 = null; lock = null; };
  vp.addEventListener('pointerup', end); vp.addEventListener('pointercancel', end);
}
document.addEventListener('keydown', e => {
  if (document.querySelector('.mm-modal')) { if (e.key === 'Escape') { closeModal(); e.preventDefault(); } return; }
  if (!st.open) return; const a = document.activeElement;
  const editable = a && (a.isContentEditable || /^(INPUT|TEXTAREA|SELECT)$/.test(a.tagName));
  if (e.key === 'Escape') { const t = st.tab; closeCard(); q().area?.querySelector(`[data-go="${t}"]`)?.focus(); return; }
  if (editable || e.altKey || e.ctrlKey || e.metaKey) return;
  if (e.key === 'ArrowRight') { go(1); e.preventDefault(); } else if (e.key === 'ArrowLeft') { go(-1); e.preventDefault(); }
});

/* ── модалка: весь журнал и саммари ── */
function rowsHtml(list, focus) {
  return list.map(e => `<tr class="mm-row-x${String(e.id) === String(focus) ? ' is-open is-ed' : ''}" data-id="${e.id}"><td class="num">${e.id}</td><td><span class="mm-tag t-${esc(e.type)}">${esc(TYPE[e.type] || e.type)}</span></td>
    <td><div class="mm-clamp">${esc(e.content)}</div></td><td class="num">${dt(e.created_at)}</td></tr>`).join('');
}
function closeModal() { document.querySelector('.mm-modal')?.remove(); }
async function openModal(focus) {
  closeModal(); const D = st.data, sm = D.summary;
  if (!st.entries.length) await loadMore();
  const m = H(`<div class="mm-modal" data-mm-modal>
   <div class="modal-card" role="dialog" aria-modal="true" aria-label="Память чата"><div class="modal-header"><h3>Память чата</h3><button class="modal-close" type="button" data-mclose aria-label="Закрыть">${S(P.x, '', 2)}</button></div>
   <div class="modal-body"><div class="mm-h4" style="margin-top:0">Журнал<span class="mm-sub">${D.journal.active} записей · ${nf(D.journal.total_chars)} зн. · архив ${D.journal.archived}</span></div>
   <div class="mm-tblw"><table class="mm-tbl"><thead><tr><th class="c-id">ID</th><th class="c-t">Тип</th><th>Текст</th><th class="c-d">Создана</th></tr></thead><tbody>${rowsHtml(st.entries, focus)}</tbody></table></div>
   <div class="mm-h4">Саммари<span class="mm-sub">${sm.text ? `${nf(sm.covered)} из ${nf(sm.total_messages)} сообщ. · до #${sm.upto_id} · ${nf(sm.chars)} зн.` : 'нет'}</span></div>
   ${sm.text ? `<div class="mm-sumtxt" tabindex="0">${esc(sm.text)}</div>` : '<div class="mm-empty">Саммари ещё нет.</div>'}</div>
   <div class="modal-footer"><button class="btn btn-secondary" type="button" data-mclose>Закрыть</button></div></div></div>`);
  document.body.appendChild(m);
  const card = m.querySelector('.modal-card');
  const bg = getComputedStyle(card).backgroundColor; const opaque = !/rgba\(.*,\s*0?\.\d+\)|transparent/.test(bg);
  m.style.setProperty('--mm-th', opaque ? bg : getComputedStyle(document.body).backgroundColor);
  m.addEventListener('click', e => { if (e.target === m || e.target.closest('[data-mclose]')) { closeModal(); return; }
    const tr = e.target.closest('tr.mm-row-x'); if (tr) tr.classList.toggle('is-open'); });
  const wrap = m.querySelector('.mm-tblw'), tb = m.querySelector('tbody');
  // Подгрузка при прокрутке. На ПК прокручивается таблица, на мобильном (max-height:none) — тело модалки.
  const more = async ev => {
    if (st.loading || st.entries.length >= st.total) return;
    const last = tb.lastElementChild; if (!last) return;
    if (last.getBoundingClientRect().top > ev.currentTarget.getBoundingClientRect().bottom + 80) return;
    const from = st.entries.length; await loadMore(); tb.insertAdjacentHTML('beforeend', rowsHtml(st.entries.slice(from)));
  };
  wrap.addEventListener('scroll', more, { passive: true });
  m.querySelector('.modal-body').addEventListener('scroll', more, { passive: true });
  const tgt = focus === 'summary' ? m.querySelector('.mm-sumtxt') : m.querySelector('tr.is-ed');
  if (tgt) tgt.scrollIntoView({ block: 'center' });
  m.querySelector('.modal-close').focus({ preventScroll: true });
}

function align() {
  const { area, box } = q(); if (!area || !box) return; const f = box.querySelector('.chat-input-field, textarea'); if (!f) return;
  const inset = parseFloat(getComputedStyle(box).borderLeftWidth) + parseFloat(getComputedStyle(f).paddingLeft);
  area.style.setProperty('--mm-in', inset + 'px');
}
addEventListener('resize', align);

function cancelRefresh() { clearTimeout(st.timer); st.abort?.abort(); st.abort = new AbortController(); st.jp = st.sp = st.tp = null; st.loading = st.sload = st.tload = false; }
function reset(chatId) { cancelRefresh(); st.chat = chatId; st.data = null; st.entries = []; st.total = 0; st.runs = null; st.open = false; st.ctx = false; st.i = 0; st.seq++; st.live = null; closeModal(); }
// Контекст из payload финала новее, чем то, что успел сохранить chats_service: пока stats
// отдаёт другой message_id, оставляем живой. Совпал — БД догнала, живой больше не нужен.
function withLive(d) {
  if (!st.live || !d) return d;
  return { ...d, context: { ...(d.context || {}), ...st.live.ctx, message_id: st.live.id } };
}
async function load(seq = st.seq) {
  try {
    const d = await api('stats');
    if (seq !== st.seq) return;
    st.data = withLive(d); st.total = d.journal.active; render();
    if (st.open) openCard(st.tab, 0);
  } catch (e) {
    if (seq !== st.seq) return;
    render();
  }
}
window.MemoryPanel = {
  mount(chatId) {
    if (!chatId) { this.unmount(); return; }
    if (String(chatId) !== String(st.chat)) { reset(String(chatId)); render(); load(); }
    else if (st.data && !q().area?.querySelector('.mm-sline')) render();
    else if (!st.data) { render(); load(); }
  },
  // Финал ответа: контекст последнего шага из payload — сразу; журнал и саммари — одним запросом stats.
  update(chatId, messageId, context) {
    if (chatId == null || String(chatId) !== String(st.chat)) return;
    if (context && typeof context === 'object' && messageId != null) {
      const id = String(messageId);
      const stamp = v => Number(/^msg-(\d+)-/.exec(v)?.[1] || 0);
      if (st.live && (st.live.id === id || stamp(id) && stamp(st.live.id) > stamp(id))) return;
      st.live = { id, ctx: context };
      st.data ||= {journal: {active: 0, total_chars: 0}, summary: {text: ''}, context: {}};
      if (st.data) { st.data = withLive(st.data); render(); }
    }
    this.refresh(chatId);
  },
  refresh(chatId) {
    if (chatId != null && String(chatId) !== String(st.chat)) return;
    if (!st.chat) return;
    cancelRefresh(); st.entries = []; st.runs = null; st.seq++;
    load(st.seq);
  },
  unmount() { reset(null); document.querySelectorAll('[data-mm]').forEach(e => e.remove()); },
  _state: st,
};
})();
