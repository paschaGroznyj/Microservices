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
  // Шаги агента: ступня в сандалии по рисунку Павла (профиль: голень с манжетой, два ремешка через подъём), без дуги
  t: '<path d="M6.1 4.4C6.08 4.93 6.12 6.7 6 7.6C5.88 8.5 5.78 9 5.4 9.8C5.02 10.6 4.13 11.6 3.7 12.4C3.27 13.2 2.87 13.87 2.8 14.6C2.73 15.33 3.22 16.43 3.3 16.8M2.6 17C3.1 17.05 4.68 17.18 5.6 17.3C6.52 17.42 6.88 17.35 8.1 17.7C9.32 18.05 11.78 19.03 12.9 19.4C14.02 19.77 13.58 19.82 14.8 19.9C16.02 19.98 19.12 20.03 20.2 19.9C21.28 19.77 21.13 19.48 21.3 19.1C21.47 18.72 21.42 17.95 21.2 17.6C20.98 17.25 20.57 17.17 20 17C19.43 16.83 18.5 16.82 17.8 16.6C17.1 16.38 16.37 16.12 15.8 15.7C15.23 15.28 14.83 14.57 14.4 14.1C13.97 13.63 13.62 13.2 13.2 12.9C12.78 12.6 12.18 12.77 11.9 12.3C11.62 11.83 11.57 10.82 11.5 10.1C11.43 9.38 11.38 8.97 11.5 8C11.62 7.03 12.08 4.92 12.2 4.3M6 7.5L11.5 8M11.9 12.7C11.48 13.08 10.03 14.22 9.4 15C8.77 15.78 8.32 17 8.1 17.4M15.4 15.6C15.13 15.92 14.22 16.88 13.8 17.5C13.38 18.12 13.05 19 12.9 19.3"/>',
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
   <button type="button" class="mm-it mm-it-t" data-go="t" aria-expanded="false" title="${ag ? `Последний ответ: ${ag.steps} ${plural(ag.steps, 'шаг', 'шага', 'шагов')} агента, ${ag.tool_calls} ${plural(ag.tool_calls, 'вызов', 'вызова', 'вызовов')} инструментов` : 'Шаги агента и вызовы инструментов'}">${S(P.t, 'mm-ic', 1.4)}<span class="mm-lb">Шаги</span> <b>${ag ? ag.steps : '—'}</b>${ag ? `<span class="mm-tq"><span class="mm-tsep">·</span>${ag.tool_calls}<span class="mm-tw"> ${plural(ag.tool_calls, 'вызов', 'вызова', 'вызовов')}</span></span>` : ''}</button></div>`);
  box.after(el);
  el.addEventListener('click', e => { const b = e.target.closest('[data-go]'); if (!b) return; const g = b.dataset.go;
    if (st.open && st.tab === g) { closeCard(); return; } openCard(g); });
  if (c.tokens && c.max_tokens) {
    const w = Math.min(100, c.tokens / c.max_tokens * 100);
    box.prepend(H(`<div class="mm-bud" data-mm title="Контекст ${fmtK(c.tokens)} из ${c.max_tokens / 1000}K">${contextSegments(c) ? contextSegments(c).map((p,i,a)=>`<i class="${p.cls}" title="${p.label}: ≈${nf(p.tokens)} (оценка)" style="left:${a.slice(0,i).reduce((v,x)=>v+x.tokens,0)/c.tokens*w}%;width:${p.tokens/c.tokens*w}%"></i>`).join('') : `<i class="mm-c2" style="left:0;width:${w}%"></i>`}</div>`));
  }
  align();
  if (fitRO) { fitRO.disconnect(); fitRO.observe(area); }
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
// Граф шагов (Bitbucket-стиль): сверху вниз, параллельные вызовы шага — ветки, слияние кривыми Безье.
// Поля вызова hint/err/ms/t0 необязательны: у старых записей chats.tool_calls только name/ok.
const TG = { row: 22, lane: 17, x0: 7, tones: [1, 2, 4, 3] };
// 640 мс · 1,2 с · 14 с · 2,1 мин
function tDur(ms) {
  if (typeof ms !== 'number' || ms < 0) return '';
  if (ms < 1000) return `${ms} мс`;
  const s = ms / 1000; if (s < 10) return `${s.toFixed(1).replace('.', ',')} с`;
  if (s < 60) return `${Math.round(s)} с`;
  return `${(s / 60).toFixed(1).replace('.', ',')} мин`;
}
// Время шага: от старта первого параллельного вызова до конца самого долгого
function tWall(calls) {
  const t = calls.filter(c => typeof c.t0 === 'number' && typeof c.ms === 'number');
  if (t.length !== calls.length || !t.length) return null;
  return Math.max(...t.map(c => c.t0 + c.ms)) - Math.min(...t.map(c => c.t0));
}
function tGraph(r) {
  const R = TG.row, X = j => TG.x0 + j * TG.lane, Y = i => i * R + R / 2, k = R * 0.6;
  const rows = [{ kind: 'start' }];
  r.by_step.forEach(s => {
    const wall = tWall(s.calls), ms = s.calls.map(c => typeof c.ms === 'number' ? c.ms : -1), slow = ms.indexOf(Math.max(...ms));
    s.calls.forEach((c, j) => rows.push({ kind: 'call', step: s.step, j, n: s.calls.length, c, wall, slow: ms[slow] >= 0 ? slow : -1 }));
  });
  const final = (r.steps || 0) > r.by_step.length;
  if (final) rows.push({ kind: 'end', step: r.steps });
  const paths = [], nodes = [];
  let i = 1, lanes = 1;
  r.by_step.forEach(s => {
    const n = s.calls.length, last = i + n - 1, next = last + 1, hasNext = next < rows.length;
    lanes = Math.max(lanes, n);
    s.calls.forEach((c, j) => {
      const row = i + j, x = X(j), tone = j ? TG.tones[(j - 1) % TG.tones.length] : 0;
      if (j) {
        let d = `M${X(0)},${Y(i)} C${X(0)},${Y(i) + k} ${x},${Y(i + 1) - k} ${x},${Y(i + 1)}`;
        if (last > i + 1) d += ` L${x},${Y(last)}`;
        if (hasNext) { const m = Y(last) + R * 0.4 + (n - 1 - j) * 1.2; d += ` C${x},${Y(last) + R * 0.3} ${X(0)},${m - R * 0.25} ${X(0)},${m}`; }
        paths.push(`<path class="mm-tg-p t${tone}" d="${d}"/>`);
      }
      nodes.push(`<g class="mm-tg-o t${tone}${c.ok ? '' : ' is-err'}">${c.ok ? '' : `<circle class="mm-tg-h" cx="${x}" cy="${Y(row)}" r="7.5"/>`}<circle cx="${x}" cy="${Y(row)}" r="4"/></g>`);
    });
    i = next;
  });
  const lastRow = rows.length - 1;
  const trunk = `<path class="mm-tg-p t0" d="M${X(0)},${Y(0)} L${X(0)},${Y(lastRow)}"/>`;
  const ends = `<circle class="mm-tg-q0" cx="${X(0)}" cy="${Y(0)}" r="4"/>` + (final ? `<circle class="mm-tg-fin" cx="${X(0)}" cy="${Y(lastRow)}" r="4"/>` : '');
  const W = X(lanes - 1) + 12, H = rows.length * R;
  const svg = `<svg class="mm-tg-svg" width="${W}" height="${H}" viewBox="0 0 ${W} ${H}" aria-hidden="true">${trunk}${paths.join('')}${nodes.join('')}${ends}</svg>`;
  const html = rows.map(o => {
    if (o.kind === 'start') return '<div class="mm-tg-r is-q"><span class="mm-tg-t">запрос</span></div>';
    if (o.kind === 'end') return `<div class="mm-tg-r is-q"><span class="mm-tg-t">ответ</span><span class="mm-tg-s">шаг ${o.step}</span></div>`;
    const c = o.c, err = !c.ok, d = tDur(c.ms), slow = o.n > 1 && o.slow === o.j;
    const sub = err && c.err ? c.err : c.hint || '';
    const side = o.j === 0 ? `<span class="mm-tg-s">шаг ${o.step}${o.n > 1 ? ` · <i data-n="×${o.n}">${o.n} параллельно</i>` : ''}</span>` : '<span class="mm-tg-s"></span>';
    const tip = [`Шаг ${o.step}${o.n > 1 ? ` (${o.j + 1} из ${o.n} параллельно)` : ''}: ${c.name}`, c.hint, d && `${d}${slow ? ', самый долгий в шаге' : ''}`, o.j === 0 && o.wall != null && o.n > 1 ? `шаг целиком ${tDur(o.wall)}` : '', err ? `Ошибка${c.err ? ': ' + c.err : ''}` : ''].filter(Boolean).join('\n');
    return `<div class="mm-tg-r${err ? ' is-err' : ''}" title="${esc(tip)}"><span class="mm-tg-t">${esc(c.name)}</span>${sub ? `<span class="mm-tg-a">${esc(sub)}</span>` : ''}${err ? '<span class="mm-tg-e">ошибка</span>' : ''}${d ? `<span class="mm-tg-d${slow ? ' is-slow' : ''}">${d}</span>` : ''}${side}</div>`;
  }).join('');
  return `<div class="mm-tg" style="--tg-w:${W}px;--tg-row:${R}px">${svg}<div class="mm-tg-rows">${html}</div></div>`;
}
function tSlide(r) {
  const modelShort = r.model_label || (r.model ? r.model.split('/').pop() : '');
  const failed = r.by_step.reduce((a, s) => a + s.calls.filter(c => !c.ok).length, 0);
  return `<div class="mm-sl mm-sl-t" data-mid="${esc(r.message_id)}"><div class="mm-sl-h"><span class="mm-tag">ответ</span><span class="mm-meta">${dt(r.created_at)}${modelShort ? ' · ' + esc(modelShort) : ''}</span></div>
   <div class="mm-tsum"><span><b>${r.steps}</b> ${plural(r.steps, 'шаг', 'шага', 'шагов')}</span><span><b>${r.tool_calls}</b> ${plural(r.tool_calls, 'вызов', 'вызова', 'вызовов')}</span>${failed ? `<span class="mm-terr"><b>${failed}</b> ${plural(failed, 'ошибка', 'ошибки', 'ошибок')}</span>` : ''}</div>
   ${r.tool_calls ? tGraph(r) : '<div class="mm-empty mm-empty-in">Ответ без вызова инструментов.</div>'}</div>`;
}

// Автоскролл по Y: длинный граф сразу показывает первую ошибку (на трети высоты слайда).
// Без ошибок слайд остаётся сверху. Если пользователь сам крутил слайд, не трогаем его.
function tAutoScroll(root, only) {
  const sls = [...(root || document).querySelectorAll('.mm-card5 .mm-sl-t')];
  (only == null ? sls : [sls[only]].filter(Boolean)).forEach(sl => {
    if (!sl._tgUser) ['wheel', 'touchmove', 'keydown', 'pointerdown'].forEach(ev => sl.addEventListener(ev, () => { sl.dataset.tgUser = '1'; }, { passive: true, once: true }));
    if (!sl._tgUser) sl.addEventListener('scroll', () => sl.classList.toggle('is-scrolled', sl.scrollTop > 0), { passive: true });
    sl._tgUser = true;
    if (sl.dataset.tgUser || sl.scrollHeight <= sl.clientHeight + 1) return;
    const e = sl.querySelector('.mm-tg-r.is-err'); if (!e) return;
    const top = e.getBoundingClientRect().top - sl.getBoundingClientRect().top + sl.scrollTop;
    // над ошибкой две строки контекста, край — по границе строки
    sl.scrollTop = top + e.offsetHeight <= sl.clientHeight - 30 ? 0 : Math.max(0, top - 2 * e.offsetHeight);
    sl.classList.toggle('is-scrolled', sl.scrollTop > 0);
  });
}

function tEmpty() {
  if (st.tload && window.Skeleton) return window.Skeleton.mm('t');
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
  if (!sm.text && st.sload && window.Skeleton) return window.Skeleton.mm('s');
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
    if (t.dataset.tab) { st.tab = t.dataset.tab; st.i = 0; ensureTab(st.tab); renderCard(); }
    else if (t.dataset.nav) go(+t.dataset.nav);
    else if ('close' in t.dataset) closeCard();
    else if ('all' in t.dataset) openModal(st.tab === 'j' ? (st.entries[st.i] || {}).id : 'summary');
    else if (t.dataset.full) openModal(t.dataset.full); });
  // запрос вкладки стартует до отрисовки: флаги загрузки уже выставлены, карточка сразу показывает скелетон
  ensureTab(tab); renderCard();
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
    ? (st.entries.length ? st.entries.map(jSlide).join('') : st.loading && window.Skeleton ? window.Skeleton.mm('j') : `<div class="mm-sl"><div class="mm-empty">${st.loading ? 'Загрузка…' : st.jerr ? 'Не удалось загрузить журнал.' : 'Журнал пуст.'}</div></div>`)
    : st.tab === 't' ? (st.runs && st.runs.length ? st.runs.map(tSlide).join('') : tEmpty())
    : st.tab === 'c' ? cSlide() : sSlide();
  // «Открыть полностью» только там, где текст не влез в превью
  requestAnimationFrame(() => st.tab === 't' && typeof tAutoScroll === 'function' && tAutoScroll(c));
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
  if (st.tab === 't' && typeof tAutoScroll === 'function') tAutoScroll(c, st.i);
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
  fitLine();
}
addEventListener('resize', align);
// Ступени подписей по реальной ширине строки: 1 — без «· 57K зн.», «истории», «кэш»;
// 2 — без «/200K» и «Шаги»; 3 — без «Саммари»/«Контекст»; 4 — только иконки и числа.
function fitLine() {
  const el = q().area?.querySelector('.mm-sline'); if (!el) return;
  // при центровке вылет влево не попадает в scrollWidth — меряем от левого края
  el.style.justifyContent = 'flex-start';
  el.removeAttribute('data-fit');
  for (let k = 1; k <= 4 && el.scrollWidth > el.clientWidth + 1; k++) el.dataset.fit = k;
  el.style.justifyContent = '';
}
const fitRO = 'ResizeObserver' in window ? new ResizeObserver(() => fitLine()) : null;

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
