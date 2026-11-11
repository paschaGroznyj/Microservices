/* FlowMap: путь запроса (прототип для встраивания). API: window.FlowViz */
(() => {
'use strict';
const MODAL_HTML = "<div class=\"fm-back\" id=\"fmBack\">\n  <div class=\"fm-dlg\" role=\"dialog\" aria-label=\"Путь запроса\">\n    <div class=\"fm-head\">\n      <div class=\"fm-title\">ПУТЬ ЗАПРОСА</div>\n      <div class=\"fm-seg\" id=\"fmLayout\"><button data-l=\"flat\" class=\"is-on\" title=\"2D: плоская карта\" aria-label=\"2D\">2D</button><button data-l=\"iso\" title=\"2.5D: изометрия\" aria-label=\"2.5D\">2.5D</button><button data-l=\"both\" title=\"2D и 2.5D рядом\" aria-label=\"2D и 2.5D\">2D&amp;2.5D</button></div>\n      <div class=\"fm-seg\" id=\"fmMode\"><button data-m=\"watch\" class=\"is-on fm-ib\" title=\"Наблюдать\" aria-label=\"Наблюдать\"><svg class=\"fm-ic\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M2 12s3.6-6.5 10-6.5S22 12 22 12s-3.6 6.5-10 6.5S2 12 2 12z\"/><circle cx=\"12\" cy=\"12\" r=\"3\"/></svg></button><button data-m=\"control\" class=\"fm-ib\" title=\"Управлять\" aria-label=\"Управлять\"><svg class=\"fm-ic\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M8 13V6.5a1.5 1.5 0 0 1 3 0V12\"/><path d=\"M11 11V5a1.5 1.5 0 0 1 3 0v6\"/><path d=\"M14 11V6.5a1.5 1.5 0 0 1 3 0V13\"/><path d=\"M17 9.5a1.5 1.5 0 0 1 3 0V14a7 7 0 0 1-7 7h-1a6 6 0 0 1-5-2.7L4.3 14.6a1.6 1.6 0 0 1 2.6-1.8L8 14\"/></svg></button></div>\n      <div class=\"fm-seg\" id=\"fmFitSeg\"><button id=\"fmFit\" class=\"fm-ib\" title=\"Весь путь\" aria-label=\"Весь путь\"><svg class=\"fm-ic\" viewBox=\"0 0 24 24\" aria-hidden=\"true\"><path d=\"M15 5h4v4\"/><path d=\"M9 19H5v-4\"/><path d=\"M19 5 5 19\"/></svg></button></div>\n      <button class=\"fm-x\" id=\"fmClose\" aria-label=\"Закрыть\">×</button>\n    </div>\n    <div class=\"fm-body\">\n      <div class=\"fm-panel\" id=\"pFlat\"><canvas id=\"cvFlat\"></canvas><div class=\"fm-ptag\">2D</div><div class=\"fm-card\"></div></div>\n      <div class=\"fm-panel\" id=\"pIso\"><canvas id=\"cvIso\"></canvas><div class=\"fm-ptag\">2.5D</div><div class=\"fm-card\"></div></div>\n    </div>\n    <div class=\"fm-foot\">\n      <div class=\"fm-status\"><span id=\"fmStatus\">Ожидаю запрос</span><span id=\"fmTotal\"></span></div>\n      <div class=\"fm-tl\"><div class=\"fm-tl__row fm-tl__row--tools\" id=\"tlTools\"></div></div>\n    </div>\n  </div>\n</div>";
const $ = s => document.querySelector(s);
mountDom();
const RM = matchMedia('(prefers-reduced-motion: reduce)');
const OK = 0.32, OY = 0.52;   // 2.5D: X = x + y*OK, Y = y*OY - z
const MAXS = 2.2;
const NODES = [
  {id:'user',     kind:'circle',  x:40,  y:200, r:13, h:6,  lp:'top', label:'ВЫ',       name:'Вы',        tech:'браузер',        desc:'Отсюда уходит запрос и сюда возвращается ответ.'},
  {id:'gateway',  kind:'circle',  x:180, y:232, r:22, h:18, label:'GATEWAY',  name:'Шлюз',      tech:'api_gateway',    desc:'Принимает сообщение и проверяет доступ.'},
  {id:'chats',    kind:'circle',  x:320, y:180, r:24, h:22, lp:'top', label:'CHATS',    name:'Чаты',      tech:'chats_service',  desc:'Сохраняет сообщение и историю чата.'},
  {id:'db',       kind:'square',  x:320, y:322, w:34, h:14, label:'DB',       name:'База',      tech:'PostgreSQL',     desc:'Хранит историю переписки.'},
  {id:'kafka',    kind:'capsule', x:520, y:232, len:170, r:16, h:10, label:'KAFKA', name:'Очередь', tech:'Kafka',     desc:'Запрос ждёт, пока агент его заберёт.'},
  {id:'consumer', kind:'circle',  x:715, y:180, r:20, h:16, lp:'top', label:'CONSUMER', name:'Разборщик', tech:'Kafka consumer', desc:'Забирает запрос из очереди и будит агента.'},
  {id:'agent',    kind:'ring',    x:880, y:210, r:34, h:34, lp:'left', label:'AGENT',    name:'Агент',     tech:'agent_service',  desc:'Думает и вызывает инструменты.'},
  {id:'obs',      kind:'square',  x:760, y:318, w:30, h:14, label:'OBS',      name:'Хранилище', tech:'OBS',            desc:'Файлы из чата и ответов агента.'},
  {id:'searxng',  kind:'circle',  x:800, y:-4,  r:12, h:10, lp:'w', label:'SEARXNG',  name:'Поиск',     tech:'searxng',        desc:'Внешний поисковик для запросов в интернет.', ext:true},
  {id:'parsers',  kind:'circle',  x:690, y:52, r:12, h:10, lp:'w', label:'PARSERS',  name:'Парсер',    tech:'parsers_service', desc:'Скачивает и разбирает страницы по ссылке.', ext:true},
  {id:'sandbox_c',kind:'circle',  x:1290,y:40,   r:13, h:10, lp:'e', label:'SANDBOX', name:'Песочница', tech:'agent_sandbox',  desc:'Контейнер, где выполняется код.', ext:true},
  {id:'workspace',kind:'folder',  x:1290,y:-90, w:88, fh:54, r:44, h:16, lp:'ws', label:'WORKSPACE', name:'Рабочая область', tech:'workspace', desc:'Директория в песочнице, где агент собирает и правит файлы.', wsNode:true},
];
const N = Object.fromEntries(NODES.map(n => [n.id, n]));
const SLOT_DEF = [
  ['web','ВЕБ','Веб','Поиск и чтение страниц','веб-поиск'],
  ['sandbox','ПЕСОЧНИЦА','Песочница','Код и команды','песочница'],
  ['media','МЕДИА','Медиа','Картинки, видео, звук','медиа'],
  ['kb','БАЗА ЗНАНИЙ','База знаний','Знания и память агента','память'],
  ['tasks','ЗАДАЧИ','Задачи','Фоновые задачи','фоновые задачи'],
  ['mail','ПОЧТА','Почта','Файлы и письма','файлы и почта'],
  ['other','ПРОЧЕЕ','Прочее','Остальные инструменты','инструменты'],
];
const SLOTS = {}, SLOT_LIST = [];
SLOT_DEF.forEach(([id, label, name, desc, tech], i) => {
  const a = (-84 + i * 28) * Math.PI / 180, R = 132;
  const s = {id, kind:'diamond', x:N.agent.x + Math.cos(a) * R, y:N.agent.y + Math.sin(a) * R, r:13, h:10, label, name, desc, tech, ang:a, tool:true};
  SLOTS[id] = s; SLOT_LIST.push(s);
});
// Имена тулов на фронт не приходят: агент шлёт slot / label / ext / ws (agent_service/app/agent/flow_tools.py).
const EXT_IDS = new Set(['searxng', 'parsers', 'sandbox_c']);
const EXT_EDGES = [['web','searxng'], ['web','parsers'], ['sandbox','sandbox_c']];
const WB = (() => {
  let x0 = 1e9, y0 = 1e9, x1 = -1e9, y1 = -1e9;
  for (const n of NODES) { const hw = (n.len ? n.len / 2 : n.w ? n.w / 2 : n.r); x0 = Math.min(x0, n.x - hw - 36); x1 = Math.max(x1, n.x + hw + 44); y0 = Math.min(y0, n.y - (n.wsNode ? 84 : 50)); y1 = Math.max(y1, n.y + (n.wsNode ? 52 : 62)); }
  for (const sl of SLOT_LIST) { const dx = Math.cos(sl.ang), dy = Math.sin(sl.ang); const lx = sl.x + dx * 40, ly = sl.y + dy * 40; x0 = Math.min(x0, lx - (dx < -0.35 ? 100 : 50)); x1 = Math.max(x1, lx + (dx > 0.35 ? 135 : 55)); y0 = Math.min(y0, ly - (dy < -0.5 ? 52 : 22)); y1 = Math.max(y1, ly + (dy > 0.5 ? 52 : 22)); }
  return {x0, y0, x1, y1};
})();
const ROUTE = [[40,200,'user'],[180,232,'gateway'],[320,180,'chats'],[435,232,'kafka'],[605,232,'kafka'],[715,180,'consumer'],[880,210,'agent']];
const D = [0];
for (let i = 1; i < ROUTE.length; i++) D.push(D[i-1] + Math.hypot(ROUTE[i][0]-ROUTE[i-1][0], ROUTE[i][1]-ROUTE[i-1][1]));
const DMAX = D[D.length-1];
function routeAt(d) {
  d = Math.max(0, Math.min(DMAX, d));
  let i = 1; while (i < D.length - 1 && D[i] < d) i++;
  const k = (d - D[i-1]) / (D[i] - D[i-1] || 1);
  return [ROUTE[i-1][0] + (ROUTE[i][0]-ROUTE[i-1][0]) * k, ROUTE[i-1][1] + (ROUTE[i][1]-ROUTE[i-1][1]) * k, i - 1, k];
}
const EDGES = [['user','gateway',0],['gateway','chats',1],['chats',[435,232],2],[[605,232],'consumer',4],['consumer','agent',5]];
function circlePts(x, y, r, n = 24) { const p = []; for (let i = 0; i < n; i++) { const a = i / n * Math.PI * 2; p.push([x + Math.cos(a) * r, y + Math.sin(a) * r]); } return p; }
function capsulePts(x, y, len, r) { const p = [], hl = len / 2 - r; for (let i = 0; i <= 10; i++) { const a = -Math.PI/2 + i / 10 * Math.PI; p.push([x + hl + Math.cos(a) * r, y + Math.sin(a) * r]); } for (let i = 0; i <= 10; i++) { const a = Math.PI/2 + i / 10 * Math.PI; p.push([x - hl + Math.cos(a) * r, y + Math.sin(a) * r]); } return p; }
const sqPts = (x, y, w) => [[x-w/2,y-w/2],[x+w/2,y-w/2],[x+w/2,y+w/2],[x-w/2,y+w/2]];
const folderPts = (x, y, w, h) => {
  const x0 = x - w / 2, x1 = x + w / 2, y0 = y - h / 2, y1 = y + h / 2;
  return [[x0, y0 + 6], [x0 + 4, y0], [x0 + 24, y0], [x0 + 29, y0 + 6], [x1, y0 + 6], [x1, y1], [x0, y1]];
};
const diaPts = (x, y, s) => [[x,y-s],[x+s,y],[x,y+s],[x-s,y]];
for (const n of [...NODES, ...SLOT_LIST]) {
  n.pts = n.kind === 'capsule' ? capsulePts(n.x, n.y, n.len, n.r) : n.kind === 'folder' ? folderPts(n.x, n.y, n.w, n.fh) : n.kind === 'square' ? sqPts(n.x, n.y, n.w) : n.kind === 'diamond' ? diaPts(n.x, n.y, n.r) : circlePts(n.x, n.y, n.r);
  if (n.kind === 'ring') n.inner = circlePts(n.x, n.y, n.r * 0.6);
  n.ptsIso = n.pts;
  if (!n.r) n.r = n.w / 2;
}
let C = {}, MONO = 'monospace', DARK = false;
const hex = v => { v = (v || '').trim(); if (v.startsWith('#')) { if (v.length === 4) v = '#' + v[1]+v[1]+v[2]+v[2]+v[3]+v[3]; return [1,3,5].map(i => parseInt(v.slice(i, i+2), 16)); } const m = v.match(/\d+/g) || [128,128,128]; return [+m[0], +m[1], +m[2]]; };
const rgba = (c, a = 1) => `rgba(${c[0]},${c[1]},${c[2]},${a})`;
const mix = (a, b, k) => a.map((v, i) => Math.round(v + (b[i] - v) * k));
function readTheme() {
  const cs = getComputedStyle(document.documentElement), g = n => cs.getPropertyValue(n);
  DARK = document.documentElement.dataset.theme === 'dark';
  C = {bg:hex(g('--bg')), surface:hex(g('--surface')), s2:hex(g('--surface2')), border:hex(g('--border')), accent:hex(g('--accent')), accent2:hex(g('--accent2')), text:hex(g('--text-primary')), sec:hex(g('--text-secondary')), muted:hex(g('--text-muted')), danger:hex(g('--danger'))};
  C.accentL = mix(C.accent, [255,255,255], 0.18);
  C.stroke = mix(C.border, C.text, DARK ? 0.34 : 0.22);
  C.sideL = mix(C.surface, C.text, DARK ? 0.12 : 0.10);
  C.sideR = mix(C.surface, C.text, DARK ? 0.22 : 0.19);
  C.label = DARK ? mix(C.sec, C.text, 0.35) : C.sec;
  MONO = (g('--font-mono') || 'monospace').trim();
  const t = document.createElement('canvas'); t.width = t.height = 24;
  const tc = t.getContext('2d'); tc.fillStyle = rgba(C.muted, DARK ? 0.55 : 0.45); tc.beginPath(); tc.arc(12, 12, 1.1, 0, 6.3); tc.fill();
  C.tile = t;
  for (const R of RENDERERS) R.pattern = null;
}
const S = {
  active:false, phase:'idle', t0:0, tEnd:0, d:0, dir:1, allow:0, trail:[], pkz:0,
  calls:new Map(), discovered:new Set(), phases:[], toolLog:[], fx:[], pulses:[],
  lit:new Float32Array(EDGES.length), hl:{}, st:{}, others:0, ghosts:[], status:'Ожидаю запрос', lastInside:null, chat:null, rid:null,
  wsSeen:false, wsId:'', wsIds:new Set(), stepSeq:0, thinkT0:null, tailThinkMs:0, thinkBuf:'', thinkDone:'', tailText:'',
  err:{},
};
for (const n of [...NODES, ...SLOT_LIST]) { S.hl[n.id] = 0; S.st[n.id] = {visits:0, ms:0, calls:[]}; }
// errOn(id): 0 — ошибок нет, 1 — была ошибка, потом успех, 2 — последний вызов упал.
const errOn = id => { const e = S.err[id]; return !e || !e.n ? 0 : e.last ? 2 : 1; };
// T_OVR: при восстановлении хронологии из Redis события проигрываются в своём (прошлом) времени.
let T_OVR = null;
const now = () => T_OVR != null ? T_OVR : performance.now();
const fmtS = ms => {
  const s = Math.max(0, ms) / 1000;
  if (s >= 60) {
    const tot = Math.floor(s), m = Math.floor(tot / 60), r = tot % 60;
    return `${m} мин ${r} с`;
  }
  return s.toFixed(1).replace('.', ',') + ' с';
};
function setStatus(t) { S.status = t; }
// Смена чата: текущий путь гасим без анимации. Раскрытая панель и открытая модалка остаются.
function reset() {
  RS.gen++;
  S.active = false; S.phase = 'idle'; S.t0 = 0; S.tEnd = 0; S.d = 0; S.dir = 1; S.allow = 0; S.trail = [];
  S.calls.clear(); S.phases = []; S.toolLog = []; S.fx = []; S.pulses = []; S.lit.fill(0); S.chat = null; S.rid = null;
  S.wsSeen = false; S.wsId = ''; S.wsIds.clear(); S.stepSeq = 0; S.thinkT0 = null; S.tailThinkMs = 0; S.thinkBuf = ''; S.thinkDone = ''; S.tailText = ''; S.err = {}; closeThink();
  setStatus('Ожидаю запрос'); clearTimeout(miniHideT); MINI.on = HOST.expanded;
  layoutHost(); renderTimeline(true); kick();
}
function openPhase(k, label, t) { const p = S.phases[S.phases.length - 1]; if (p && !p.t1) p.t1 = t; S.phases.push({k, label, t0:t, t1:0}); }
// Событие относится к текущему запросу? chat/request_id сверяем, если они известны с обеих сторон.
function foreign(type, d) {
  if (type === 'send') return false;
  if (d.chat != null && S.chat != null && String(d.chat) !== String(S.chat)) return true;
  if (d.request_id && S.rid && d.request_id !== S.rid) return true;
  return false;
}
function emit(type, d = {}) {
  if (foreign(type, d)) return;
  const t = now();
  switch (type) {
    case 'send':
      S.chat = d.chat != null ? String(d.chat) : null; S.rid = null;
      S.active = true; S.phase = 'send'; S.t0 = t; S.tEnd = 0; S.d = 0; S.dir = 1; S.allow = D[1]; S.trail = [];
      S.calls.clear(); S.phases = []; S.toolLog = []; S.fx = []; S.pulses = []; S.lit.fill(0); S.lastInside = null; S.stepSeq = 0; S.thinkT0 = null; S.tailThinkMs = 0; S.thinkBuf = ''; S.thinkDone = ''; S.tailText = ''; S.err = {}; TL.follow = true; closeThink();
      openPhase('gw', 'Шлюз', t); setStatus('Отправляю…'); showMini(true); if (!RS.busy) pulseTapHint(); break;
    case 'gateway':
      if (!S.active) break;
      if (d.request_id) S.rid = d.request_id;
      if (S.phase !== 'send') break;
      S.phase = 'queue'; S.allow = Math.max(S.allow, D[4]); openPhase('queue', 'Очередь', t); setStatus('В очереди'); break;
    case 'accepted':
      // Агент забрал запрос из Kafka: пакет доходит до Consumer.
      if (!S.active || S.phase === 'agent' || S.phase === 'answer' || S.phase === 'done') break;
      if (S.phase === 'send') openPhase('queue', 'Очередь', t);
      S.phase = 'accepted'; S.allow = Math.max(S.allow, D[5]); setStatus('Агент принял запрос'); break;
    case 'agent_start':
      if (!S.active || S.phase === 'answer' || S.phase === 'done') break;
      if (S.phase === 'agent') break;   // дубль (восстановление + живой кадр)
      S.phase = 'agent'; S.allow = DMAX; S.thinkT0 = t; S.tailThinkMs = 0; openPhase('agent', 'Агент', t); setStatus('Агент думает'); break;
    case 'tool_start': {
      if (!S.active) break;
      if (S.phase !== 'agent' && S.phase !== 'answer') { S.phase = 'agent'; S.allow = DMAX; openPhase('agent', 'Агент', t); }
      const slot = SLOTS[d.slot] ? d.slot : 'other', label = d.label || 'Инструмент';
      const ext = EXT_IDS.has(d.ext) ? d.ext : null;
      const ws = d.ws || d.workspace_id || null;
      const key = d.id || ('t' + t);
      if (d.id && S.toolLog.some(l => l.key === d.id)) break;   // дубль после восстановления
      // Если активных вызовов нет — это новый последовательный шаг по горизонтали;
      // если уже есть активные вызовы — это параллельный вызов в том же шаге (на новой дорожке).
      const isNewStep = S.calls.size === 0;
      if (isNewStep) S.stepSeq++;
      const step = S.stepSeq;
      S.calls.set(key, {slot, label, ext, ws, t0:t, step}); if (ext) S.hl[ext] = 1;
      if (ws) {
        S.wsSeen = true; S.hl.workspace = 1;
        if (ws !== 'workspace') { S.wsId = String(ws); S.wsIds.add(S.wsId); }
        S.pulses.push({a:'sandbox_c', b:'workspace', t, dur:680});
      }
      // gap_ms — сколько агент думал перед этим шагом (считает бэк по request_id).
      const fbGap = isNewStep && S.thinkT0 != null ? Math.max(0, t - S.thinkT0) : null;
      const gap = isNewStep ? (Number.isFinite(+d.gap_ms) && d.gap_ms !== null ? +d.gap_ms : fbGap) : null;
      // Текст «Размышлений» этого интервала привязываем к шагу: по тапу на блок «думает» он откроется оверлеем.
      const thinkText = isNewStep ? [S.thinkDone, S.thinkBuf.trim()].filter(Boolean).join('\n\n') : '';
      if (isNewStep) { S.thinkT0 = null; S.tailThinkMs = 0; S.thinkBuf = ''; S.thinkDone = ''; }
      S.toolLog.push({key, slot, label, ws, step, t0:t, t1:0, ms:null, ok:true, gap, gapShown:gap, thinkText});
      S.discovered.add(slot); S.hl[slot] = 1; setStatus(label); break;
    }
    case 'tool_end': {
      const key = d.id; const c = key != null ? S.calls.get(key) : null; if (!c) break;
      S.calls.delete(key);
      const ok = d.ok !== false;
      const ws = d.ws || d.workspace_id || c.ws || null;
      if (ws) {
        S.wsSeen = true; S.hl.workspace = 1;
        if (ws !== 'workspace') { S.wsId = String(ws); S.wsIds.add(S.wsId); }
      }
      // Длительность берём с бэка (ms, monotonic вокруг ainvoke): кадры до фронта
      // могут прийти пачкой, и разница времени прихода даст ложные 0,1 с.
      const ms = Number.isFinite(+d.ms) && d.ms !== null ? +d.ms : t - c.t0;
      const log = [...S.toolLog].reverse().find(l => l.key === key && !l.t1);
      if (log) { log.t1 = log.t0 + ms; log.ms = ms; log.ok = ok; if (ws) log.ws = ws; }
      const st = S.st[c.slot]; st.calls.unshift({label:c.label, ms, ok}); st.calls.length = Math.min(st.calls.length, 5); st.visits++;
      if (ws) { const wst = S.st.workspace; wst.calls.unshift({label:c.label, ms, ok}); wst.calls.length = Math.min(wst.calls.length, 5); wst.visits++; wst.ms += ms; }
      S.fx.push({kind: ok ? 'ok' : 'err', slot:c.slot, t, dur: ok ? 700 : 2600});
      // Ошибка тула остаётся на карте до конца запроса: ромб, ребро от агента, внешний контейнер/workspace.
      for (const id of [c.slot, c.ext, ws ? 'workspace' : null]) {
        if (!id) continue; const e = S.err[id] || (S.err[id] = {n:0, last:false});
        if (!ok) e.n++; e.last = !ok;
      }
      const left = [...S.calls.values()];
      if (!left.length && S.phase === 'agent') S.thinkT0 = t;
      setStatus(left.length ? left[left.length-1].label : 'Агент думает'); break;
    }
    case 'think_text':
      // Живой чанк (тот же текст, что уходит в блок «Размышления»). Пока не пришёл reasoning_end,
      // это может оказаться и финальным ответом — поэтому держим отдельно от подтверждённого thinkDone.
      if (!S.active || S.phase === 'answer' || S.phase === 'done' || !d.text) return;
      S.thinkBuf = (S.thinkBuf + String(d.text)).slice(-THINK_MAX);
      if (TH.key === 'tail') fillThink(); return;
    case 'think_end':
      // reasoning_end: живой блок был размышлением перед тулом — подтверждаем текст.
      if (!S.active || !S.thinkBuf) return;
      S.thinkDone = (S.thinkDone ? S.thinkDone + '\n\n' : '') + S.thinkBuf.trim(); S.thinkBuf = '';
      if (TH.key === 'tail') fillThink(); return;
    case 'db_saved':
      // chats_service записал сообщение в БД: импульс CHATS → DB.
      if (!S.active) break;
      S.hl.chats = 1; S.hl.db = 1; S.pulses.push({a:'chats', b:'db', t, dur:620}); break;
    case 'obs':
      // Агент залил файл ответа в OBS по HTTP.
      if (!S.active) break;
      S.hl.obs = 1; S.hl.agent = Math.max(S.hl.agent, .6); S.pulses.push({a:'agent', b:'obs', t, dur:720, err:d.ok === false}); break;
    case 'answer':
      if (!S.active || S.phase === 'answer' || S.phase === 'done') break;
      if (S.thinkT0 != null) { S.tailThinkMs = Math.max(0, t - S.thinkT0); S.thinkT0 = null; }
      S.tailText = S.thinkDone; S.thinkDone = ''; S.thinkBuf = '';
      S.phase = 'answer'; S.dir = -1; S.allow = 0;
      for (const [k] of S.calls) { const log = S.toolLog.find(l => l.key === k && !l.t1); if (log) log.t1 = t; }
      S.calls.clear(); openPhase('answer', 'Ответ', t); setStatus('Пишу ответ'); break;
    case 'done': {
      if (!S.active || S.phase === 'done') break;
      if (S.thinkT0 != null) { S.tailThinkMs = Math.max(0, t - S.thinkT0); S.thinkT0 = null; }
      if (S.thinkDone) { S.tailText = S.thinkDone; S.thinkDone = ''; } S.thinkBuf = '';
      for (const [k] of S.calls) { const log = S.toolLog.find(l => l.key === k && !l.t1); if (log) { log.t1 = t; log.ok = d.ok !== false; } }
      S.calls.clear();
      const p = S.phases[S.phases.length - 1]; if (p && !p.t1) p.t1 = t;
      S.tEnd = t; S.phase = 'done'; S.dir = -1; S.allow = 0; setStatus('Готово'); break;
    }
  }
  renderTimeline(true); kick();
}
class MapR {
  constructor(cv, iso) {
    this.cv = cv; this.ctx = cv.getContext('2d'); this.iso = iso; this.W = 0; this.H = 0; this.dpr = 1;
    this.cam = {cx:0, cy:0, s:1}; this.manual = false; this.visible = false; this.hover = null; this.pattern = null;
    const pts = [[WB.x0,WB.y0],[WB.x1,WB.y0],[WB.x1,WB.y1],[WB.x0,WB.y1]].map(([x,y]) => this.P(x,y,0));
    this.plate = pts;
    const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
    const padTop = iso ? 52 : 14;
    this.bb = {x0:Math.min(...xs) - 24, x1:Math.max(...xs) + 24, y0:Math.min(...ys) - padTop, y1:Math.max(...ys) + 22};
    this.bind();
  }
  P(x, y, z = 0) { return this.iso ? [x + y * OK, y * OY - z] : [x, y]; }
  scr(x, y, z = 0) { const p = this.P(x, y, z), c = this.cam; return [(p[0] - c.cx) * c.s + this.W / 2, (p[1] - c.cy) * c.s + this.H / 2]; }
  resize() {
    const r = this.cv.getBoundingClientRect(); this.visible = r.width > 0 && r.height > 0;
    if (!this.visible) return;
    this.dpr = Math.min(2, devicePixelRatio || 1); this.W = r.width; this.H = r.height;
    this.cv.width = Math.round(r.width * this.dpr); this.cv.height = Math.round(r.height * this.dpr);
    if (!this.inited) { this.inited = true; this.toFit(true); }
    this.clamp();
  }
  // Экранный запас [слева, справа, сверху, снизу] под подписи фиксированного размера:
  // на узком экране мир ужат в 4–5 раз, а подписи нет, поэтому справа (веер тулов) и сверху (WORKSPACE) нужен запас.
  pad() { return this.W < 560 ? [18, 78, 34, 12] : [10, 14, 10, 10]; }
  minS() { const [l, r, t, b] = this.pad(); return Math.min(Math.max(40, this.W - l - r) / (this.bb.x1 - this.bb.x0), Math.max(40, this.H - t - b) / (this.bb.y1 - this.bb.y0)); }
  clamp() {
    const c = this.cam, b = this.bb; c.s = Math.max(this.minS(), Math.min(MAXS, c.s));
    const [l, r, t, bt] = this.pad(), wl = (this.W / 2 - l) / c.s, wr = (this.W / 2 - r) / c.s, ht = (this.H / 2 - t) / c.s, hb = (this.H / 2 - bt) / c.s;
    c.cx = (b.x1 - b.x0) <= wl + wr ? (b.x0 + b.x1) / 2 - (l - r) / 2 / c.s : Math.max(b.x0 + wl, Math.min(b.x1 - wr, c.cx));
    c.cy = (b.y1 - b.y0) <= ht + hb ? (b.y0 + b.y1) / 2 - (t - bt) / 2 / c.s : Math.max(b.y0 + ht, Math.min(b.y1 - hb, c.cy));
  }
  toFit(nowSet) { const s = this.minS(), [l, r, tp, bt] = this.pad(); const t = {cx:(this.bb.x0 + this.bb.x1) / 2 - (l - r) / 2 / s, cy:(this.bb.y0 + this.bb.y1) / 2 - (tp - bt) / 2 / s, s}; if (nowSet) Object.assign(this.cam, t); return t; }
  target() {
    if (VIEW.fit || !S.active) return this.toFit(false);
    const fs = this.minS();
    const calls = [...S.calls.values()];
    if (calls.length && S.phase === 'agent') {
      const pts = [this.P(N.consumer.x, N.agent.y, 0), ...SLOT_LIST.map(sl => this.P(sl.x + Math.cos(sl.ang) * 60, sl.y + Math.sin(sl.ang) * 40, 0))];
      if (S.wsSeen) { pts.push(this.P(N.workspace.x + 64, N.workspace.y - 66, 0)); pts.push(this.P(N.workspace.x - 64, N.workspace.y - 66, 0)); }
      const xs = pts.map(p => p[0]), ys = pts.map(p => p[1]);
      const w = Math.max(...xs) - Math.min(...xs) + 120, h = Math.max(...ys) - Math.min(...ys) + 60;
      return {cx:(Math.max(...xs) + Math.min(...xs)) / 2, cy:(Math.max(...ys) + Math.min(...ys)) / 2, s:Math.max(fs, Math.min(MAXS, this.W / w, this.H / h, fs * 1.9))};
    }
    const [x, y] = routeAt(S.d), p = this.P(x, y, 0);
    return {cx:p[0], cy:p[1], s:Math.min(MAXS, fs * 1.7)};
  }
  update(dt) {
    if (!this.visible) return false;
    if (this.manual && !VIEW.fit) { this.clamp(); return false; }
    const t = this.target(), c = this.cam, k = RM.matches ? 1 : 1 - Math.pow(0.004, dt / 1000);
    const before = [c.cx, c.cy, c.s];
    c.cx += (t.cx - c.cx) * k; c.cy += (t.cy - c.cy) * k; c.s += (t.s - c.s) * k;
    this.clamp();
    return Math.abs(c.cx - before[0]) + Math.abs(c.cy - before[1]) > 0.05 || Math.abs(c.s - before[2]) > 0.0005;
  }
  bind() {
    const cv = this.cv; let drag = null;
    cv.addEventListener('pointerdown', e => { drag = {x:e.clientX, y:e.clientY, cx:this.cam.cx, cy:this.cam.cy, moved:false}; cv.setPointerCapture(e.pointerId); });
    cv.addEventListener('pointermove', e => {
      if (drag) {
        const dx = e.clientX - drag.x, dy = e.clientY - drag.y;
        if (!drag.moved && Math.hypot(dx, dy) > 4) { drag.moved = true; cv.classList.add('is-drag'); setMode('control'); VIEW.fit = false; syncFit(); this.manual = true; }
        if (drag.moved) { this.cam.cx = drag.cx - dx / this.cam.s; this.cam.cy = drag.cy - dy / this.cam.s; this.clamp(); kick(); }
        return;
      }
      const r = cv.getBoundingClientRect(), h = this.hit(e.clientX - r.left, e.clientY - r.top);
      if ((h && h.id) !== (this.hover && this.hover.id)) { this.hover = h; cv.classList.toggle('is-hover', !!h); kick(); }
    });
    cv.addEventListener('pointerup', e => {
      cv.classList.remove('is-drag');
      if (drag && !drag.moved) { const r = cv.getBoundingClientRect(); const h = this.hit(e.clientX - r.left, e.clientY - r.top); showCard(this, h, e.clientX - r.left, e.clientY - r.top); }
      drag = null;
    });
    cv.addEventListener('pointerleave', () => { if (this.hover) { this.hover = null; cv.classList.remove('is-hover'); kick(); } });
    cv.addEventListener('wheel', e => {
      e.preventDefault(); setMode('control'); VIEW.fit = false; syncFit(); this.manual = true;
      const r = cv.getBoundingClientRect(), mx = e.clientX - r.left, my = e.clientY - r.top, c = this.cam;
      const wx = (mx - this.W / 2) / c.s + c.cx, wy = (my - this.H / 2) / c.s + c.cy;
      c.s *= Math.exp(-e.deltaY * 0.0015); this.clamp();
      c.cx = wx - (mx - this.W / 2) / c.s; c.cy = wy - (my - this.H / 2) / c.s; this.clamp(); kick();
    }, {passive:false});
    cv.addEventListener('dblclick', () => { this.manual = false; VIEW.fit = false; syncFit(); setMode('watch'); kick(); });
  }
  hit(mx, my) {
    let best = null, bd = 1e9;
    for (const n of [...NODES, ...SLOT_LIST]) {
      if (n.wsNode && !S.wsSeen) continue;
      const [x, y] = this.scr(n.x, n.y, this.iso ? n.h : 0);
      const rr = Math.max(16, n.r * this.cam.s * 1.15) + (n.kind === 'capsule' ? n.len / 2 * this.cam.s : 0);
      const dd = Math.hypot(mx - x, my - y);
      if (dd < rr && dd < bd) { bd = dd; best = n; }
    }
    return best;
  }
  prism(pts, h, top, stroke, lw, dash) {
    const ctx = this.ctx, n = pts.length;
    if (this.iso && h > 0) {
      const sp = pts.map(p => this.P(p[0], p[1], 0));
      let cx = 0, cy = 0; for (const p of sp) { cx += p[0]; cy += p[1]; } cx /= n; cy /= n;
      const L = new Path2D(), R = new Path2D();
      for (let i = 0; i < n; i++) {
        const a = pts[i], b = pts[(i + 1) % n], sa = sp[i], sb = sp[(i + 1) % n];
        let nx = sb[1] - sa[1], ny = sa[0] - sb[0];
        if (nx * ((sa[0] + sb[0]) / 2 - cx) + ny * ((sa[1] + sb[1]) / 2 - cy) < 0) { nx = -nx; ny = -ny; }
        if (ny <= 1e-4) continue;
        const path = nx > Math.hypot(nx, ny) * 0.55 ? R : L;
        const A0 = this.P(a[0], a[1], 0), B0 = this.P(b[0], b[1], 0), B1 = this.P(b[0], b[1], h), A1 = this.P(a[0], a[1], h);
        path.moveTo(A0[0], A0[1]); path.lineTo(B0[0], B0[1]); path.lineTo(B1[0], B1[1]); path.lineTo(A1[0], A1[1]); path.closePath();
      }
      ctx.fillStyle = rgba(C.sideL); ctx.fill(L); ctx.fillStyle = rgba(C.sideR); ctx.fill(R);
    }
    const T = new Path2D();
    pts.forEach((p, i) => { const q = this.P(p[0], p[1], h); i ? T.lineTo(q[0], q[1]) : T.moveTo(q[0], q[1]); });
    T.closePath();
    if (top) { ctx.fillStyle = top; ctx.fill(T); }
    if (stroke) { ctx.strokeStyle = stroke; ctx.lineWidth = lw; ctx.setLineDash(dash || []); ctx.stroke(T); ctx.setLineDash([]); }
  }
  ringPath(x, y, z, r, a0 = 0, a1 = Math.PI * 2, n = 40) {
    const p = new Path2D();
    for (let i = 0; i <= n; i++) { const a = a0 + (a1 - a0) * i / n, q = this.P(x + Math.cos(a) * r, y + Math.sin(a) * r, z); i ? p.lineTo(q[0], q[1]) : p.moveTo(q[0], q[1]); }
    return p;
  }
  draw(t) {
    if (!this.visible) return;
    const ctx = this.ctx, c = this.cam, s = c.s, px = 1 / s, iso = this.iso;
    ctx.setTransform(1, 0, 0, 1, 0, 0); ctx.clearRect(0, 0, this.cv.width, this.cv.height);
    ctx.setTransform(this.dpr * s, 0, 0, this.dpr * s, this.dpr * (this.W / 2 - c.cx * s), this.dpr * (this.H / 2 - c.cy * s));
    const plate = new Path2D(); this.plate.forEach((p, i) => i ? plate.lineTo(p[0], p[1]) : plate.moveTo(p[0], p[1])); plate.closePath();
    ctx.fillStyle = rgba(C.text, DARK ? 0.022 : 0.018); ctx.fill(plate);
    if (!this.pattern) { this.pattern = ctx.createPattern(C.tile, 'repeat'); if (iso) this.pattern.setTransform(new DOMMatrix([1, 0, OK, OY, 0, 0])); }
    ctx.fillStyle = this.pattern; ctx.fill(plate);
    ctx.strokeStyle = rgba(C.border); ctx.lineWidth = px; ctx.stroke(plate);
    const pt = (x, y, z) => this.P(x, y, z);
    const line = (a, b, col, w, dash, off) => { const A = pt(a[0], a[1], 0), B = pt(b[0], b[1], 0); ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); ctx.strokeStyle = col; ctx.lineWidth = w; ctx.setLineDash(dash || []); ctx.lineDashOffset = off || 0; ctx.stroke(); ctx.setLineDash([]); };
    const xy = v => typeof v === 'string' ? [N[v].x, N[v].y] : v;
    EDGES.forEach(([a, b], i) => {
      line(xy(a), xy(b), rgba(C.stroke, 0.9), 1.4 * px);
      if (S.lit[i] > 0.01) line(xy(a), xy(b), rgba(C.accent, S.lit[i]), 2.6 * px);
    });
    line([N.chats.x, N.chats.y], [N.db.x, N.db.y], rgba(C.stroke, 0.8), 1.2 * px, [4 * px, 4 * px]);
    if (S.hl.db > 0.02) line([N.chats.x, N.chats.y], [N.db.x, N.db.y], rgba(C.accent, Math.min(1, S.hl.db)), 2.2 * px);
    line([N.agent.x, N.agent.y], [N.obs.x, N.obs.y], rgba(C.stroke, 0.95), 1.3 * px, [4 * px, 3 * px]);
    if (S.hl.obs > 0.02) line([N.agent.x, N.agent.y], [N.obs.x, N.obs.y], rgba(C.accent, Math.min(1, S.hl.obs)), 2.2 * px);
    const extRun = new Set([...S.calls.values()].map(c => c.ext).filter(Boolean));
    const wsRun = [...S.calls.values()].some(c => c.ws);
    for (const [a, b] of EXT_EDGES) {
      const A = SLOTS[a], B = N[b];
      line([A.x, A.y], [B.x, B.y], rgba(C.stroke, 0.8), 1.1 * px, [3 * px, 3 * px]);
      if (extRun.has(b)) line([A.x, A.y], [B.x, B.y], rgba(C.accent, 0.9), 2 * px, [6 * px, 6 * px], RM.matches ? 0 : -t * 0.04 * px);
      else if (errOn(b) === 2) line([A.x, A.y], [B.x, B.y], rgba(C.danger, 0.85), 1.9 * px, [6 * px, 4 * px]);
      else if (S.hl[b] > 0.02) line([A.x, A.y], [B.x, B.y], rgba(C.accent, Math.min(1, S.hl[b])), 1.8 * px, [6 * px, 6 * px]);
    }
    if (S.wsSeen) {
      const W0 = N.workspace, SB = SLOTS.sandbox, SC = N.sandbox_c;
      if (!iso) {
        const zPts = [[W0.x - 62, W0.y - 42], [W0.x + 62, W0.y - 42], [W0.x + 62, W0.y + 40], [W0.x - 62, W0.y + 40]];
        this.prism(zPts, 0, rgba(C.accent, wsRun ? 0.07 : 0.025), rgba(wsRun ? C.accent : C.stroke, wsRun ? 0.65 : 0.38), 1.1 * px, [4 * px, 4 * px]);
      }
      line([SC.x, SC.y], [W0.x, W0.y], rgba(C.stroke, 0.7), 1.1 * px, [3 * px, 4 * px]);
      if (wsRun) {
        line([SC.x, SC.y], [W0.x, W0.y], rgba(C.accent, 0.75), 1.7 * px, [5 * px, 5 * px], RM.matches ? 0 : -t * 0.04 * px);
      } else if (S.hl.workspace > 0.02) {
        line([SC.x, SC.y], [W0.x, W0.y], rgba(C.accent, Math.min(1, S.hl.workspace)), 1.9 * px, [5 * px, 5 * px]);
      }
    }
    const busy = S.active && (S.phase === 'agent' || S.phase === 'answer');
    const runSlots = new Set([...S.calls.values()].map(c => c.slot));
    for (const sl of SLOT_LIST) {
      const disc = S.discovered.has(sl.id);
      line([N.agent.x, N.agent.y], [sl.x, sl.y], rgba(C.stroke, disc ? 0.6 : 0.4), px, [2 * px, 5 * px]);
      if (runSlots.has(sl.id)) line([N.agent.x, N.agent.y], [sl.x, sl.y], rgba(C.accent, 0.9), 2 * px, [6 * px, 6 * px], RM.matches ? 0 : -t * 0.04 * px);
      else if (errOn(sl.id) === 2) line([N.agent.x, N.agent.y], [sl.x, sl.y], rgba(C.danger, 0.8), 1.8 * px, [6 * px, 4 * px]);
    }
    const all = [...NODES, ...SLOT_LIST];
    if (iso) all.sort((a, b) => (a.y + a.r) - (b.y + b.r));
    if (iso) { ctx.fillStyle = rgba([0,0,0], DARK ? 0.35 : 0.07); for (const n of all) { if ((n.tool && !S.discovered.has(n.id)) || (n.wsNode && !S.wsSeen)) continue; const sh = new Path2D(); n.ptsIso.forEach((p, i) => { const q = pt(p[0] + 5, p[1] + 5, 0); i ? sh.lineTo(q[0], q[1]) : sh.moveTo(q[0], q[1]); }); ctx.fill(sh); } }
    for (const n of all) {
      if (n.wsNode && !S.wsSeen) continue;
      const hl = Math.min(1, S.hl[n.id] || 0) + (this.hover === n ? 0.35 : 0) + (n.wsNode && wsRun ? 0.7 : 0);
      const NP = iso ? n.ptsIso : n.pts;
      if (n.tool && !S.discovered.has(n.id)) { this.prism(NP, 0, null, rgba(mix(C.muted, C.text, DARK ? 0.25 : 0.12), this.hover === n ? 1 : 0.85), 1.1 * px, [3 * px, 3 * px]); continue; }
      const eo = errOn(n.id), nRun = n.tool ? runSlots.has(n.id) : n.wsNode ? wsRun : extRun.has(n.id);
      const isErr = eo === 2 && !nRun;
      const stroke = isErr ? rgba(C.danger) : hl > 0.02 ? rgba(mix(C.stroke, C.accent, Math.min(1, hl))) : rgba(C.stroke);
      const fillC = isErr ? mix(C.surface, C.danger, DARK ? 0.22 : 0.12) : n.wsNode && wsRun ? mix(C.surface, C.accent, 0.12) : C.surface;
      this.prism(NP, n.h, rgba(fillC), stroke, (isErr ? 2 : 1.2 + Math.min(1, hl) * 1.2) * px);
      if (n.kind === 'folder') {
        const z = iso ? n.h : 0, x0 = n.x - n.w / 2, x1 = n.x + n.w / 2, y0 = n.y - n.fh / 2;
        const seamA = pt(x0, y0 + 6, z), seamB = pt(x1, y0 + 6, z);
        ctx.strokeStyle = stroke; ctx.lineWidth = px; ctx.beginPath(); ctx.moveTo(seamA[0], seamA[1]); ctx.lineTo(seamB[0], seamB[1]); ctx.stroke();
        // Дерево директории в разрезе: ствол и 3 ветки файлов с анимацией обхода
        const tx = n.x - 26, ty0 = n.y - 12, ty2 = n.y + 18;
        const stA = pt(tx, ty0, z), stB = pt(tx, ty2, z);
        ctx.strokeStyle = rgba(wsRun ? C.accent : C.stroke, 0.9); ctx.lineWidth = 1.3 * px;
        ctx.beginPath(); ctx.moveTo(stA[0], stA[1]); ctx.lineTo(stB[0], stB[1]); ctx.stroke();
        const branches = [[-7, 18], [5, 30], [17, 14]];
        const actBr = wsRun && !RM.matches ? Math.floor(t / 220) % 3 : -1;
        branches.forEach(([dy, len], bi) => {
          const bA = pt(tx, n.y + dy, z), bB = pt(tx + len, n.y + dy, z);
          const onB = wsRun && (actBr === bi || actBr < 0);
          ctx.strokeStyle = rgba(onB ? C.accent : C.stroke, onB ? 1 : 0.85); ctx.lineWidth = (onB ? 1.8 : 1.2) * px;
          ctx.beginPath(); ctx.moveTo(bA[0], bA[1]); ctx.lineTo(bB[0], bB[1]); ctx.stroke();
          ctx.fillStyle = rgba(onB ? C.accent : C.stroke, onB ? 1 : 0.85);
          ctx.beginPath(); ctx.arc(bB[0], bB[1], (onB ? 2.6 : 1.9) * px, 0, 6.3); ctx.fill();
        });
      }
      if (n.kind === 'ring') this.prism(n.inner, n.h, rgba(mix(C.surface, C.accent, busy ? 0.16 : 0.05)), rgba(busy ? C.accent : C.stroke, busy ? 0.9 : 0.8), 1.2 * px);
      if (n.kind === 'capsule' && S.active && S.phase === 'queue') {
        ctx.strokeStyle = rgba(C.accent, 0.45); ctx.lineWidth = 1.2 * px; ctx.setLineDash([3 * px, 5 * px]); ctx.lineDashOffset = RM.matches ? 0 : -t * 0.02 * px;
        const a = pt(n.x - n.len / 2 + 14, n.y, n.h), b = pt(n.x + n.len / 2 - 14, n.y, n.h); ctx.beginPath(); ctx.moveTo(a[0], a[1]); ctx.lineTo(b[0], b[1]); ctx.stroke(); ctx.setLineDash([]);
      }
    }
    // Бейдж ошибки: красный кружок с ✕ и счётчиком в правом верхнем углу узла.
    for (const n of all) {
      const eo0 = errOn(n.id); if (!eo0 || (n.wsNode && !S.wsSeen)) continue;
      // Пока тул снова работает, ошибка прошлой попытки — бледным бейджем, без перекраски узла.
      const nRun = n.tool ? runSlots.has(n.id) : n.wsNode ? wsRun : extRun.has(n.id), eo = nRun ? 1 : eo0;
      // Бейдж снаружи контура (и снаружи кольца работающего тула), по диагонали от подписи.
      const r = 6.5 * px, gap = r + 3 * px, side = n.lp === 'e' ? -1 : 1;
      let q;
      // Подписи слотов и внешних контейнеров стоят сверху/сбоку — бейдж ставим снизу-справа, чтобы не лезть в текст.
      if (n.wsNode) q = pt(n.x + 62 + 9, n.y + 40 + 9, iso ? n.h : 0);
      else { const R0 = (n.tool && nRun ? 22 + 2.6 * px : n.kind === 'diamond' ? n.r * 0.75 : n.r) + gap * (n.kind === 'diamond' && !nRun ? 0.9 : 1); q = pt(n.x + side * R0 * 0.72, n.y + R0 * 0.72, iso ? n.h : 0); }
      ctx.fillStyle = rgba(eo === 2 ? C.danger : mix(C.surface, C.danger, 0.5)); ctx.strokeStyle = rgba(C.surface); ctx.lineWidth = 1.6 * px;
      ctx.beginPath(); ctx.arc(q[0], q[1], r, 0, 6.3); ctx.fill(); ctx.stroke();
      const e = S.err[n.id];
      if (e.n > 1) { ctx.fillStyle = rgba(C.surface); ctx.font = `700 ${9 * px}px ${MONO}`; ctx.textAlign = 'center'; ctx.textBaseline = 'middle'; ctx.fillText(String(e.n), q[0], q[1] + 0.5 * px); }
      else { const k = 2.8 * px; ctx.strokeStyle = rgba(C.surface); ctx.lineWidth = 1.7 * px; ctx.lineCap = 'round'; ctx.beginPath(); ctx.moveTo(q[0] - k, q[1] - k); ctx.lineTo(q[0] + k, q[1] + k); ctx.moveTo(q[0] + k, q[1] - k); ctx.lineTo(q[0] - k, q[1] + k); ctx.stroke(); ctx.lineCap = 'butt'; }
    }
    if (busy) {
      const a = N.agent; ctx.strokeStyle = rgba(C.accent, 0.55); ctx.lineWidth = 1.3 * px; ctx.setLineDash([5 * px, 7 * px]); ctx.lineDashOffset = RM.matches ? 0 : -t * 0.012 * px;
      ctx.stroke(this.ringPath(a.x, a.y, 0, a.r + 13)); ctx.setLineDash([]);
    }
    for (const cl of S.calls.values()) {
      const sl = SLOTS[cl.slot], z = iso ? sl.h : 0;
      ctx.strokeStyle = rgba(C.border, 0.9); ctx.lineWidth = 2 * px; ctx.stroke(this.ringPath(sl.x, sl.y, z, 22));
      const a0 = RM.matches ? -Math.PI / 2 : (t * 0.004) % (Math.PI * 2);
      ctx.strokeStyle = rgba(C.accent); ctx.lineWidth = 2.6 * px; ctx.lineCap = 'round'; ctx.stroke(this.ringPath(sl.x, sl.y, z, 22, a0, a0 + Math.PI * 0.6, 16)); ctx.lineCap = 'butt';
    }
    for (const f of S.fx) {
      const sl = SLOTS[f.slot], z = iso ? sl.h : 0, k = (t - f.t) / f.dur;
      if (k < 0 || k > 1) continue;
      if (f.kind === 'ok') { ctx.strokeStyle = rgba(C.accent2, 1 - k); ctx.lineWidth = 2.6 * px; ctx.stroke(this.ringPath(sl.x, sl.y, z, 22 + k * 10)); }
      else {
        const a = Math.max(0, Math.min(1, (1 - k) * 2.2)); const blink = k < 0.25 ? (Math.sin(k * 60) > 0 ? 1 : 0.35) : 1;
        ctx.strokeStyle = rgba(C.danger, a * blink); ctx.lineWidth = 2.4 * px; ctx.stroke(this.ringPath(sl.x, sl.y, z, 22));
        const q = pt(sl.x, sl.y, z), r = 6 * px * 1.4; ctx.beginPath(); ctx.moveTo(q[0] - r, q[1] - r); ctx.lineTo(q[0] + r, q[1] + r); ctx.moveTo(q[0] + r, q[1] - r); ctx.lineTo(q[0] - r, q[1] + r); ctx.stroke();
      }
    }
    for (const p of S.pulses) {
      const k = (t - p.t) / p.dur; if (k < 0 || k > 1) continue;
      const A = N[p.a || 'chats'] || SLOTS[p.a] || N.chats, B = N[p.b || 'db'] || SLOTS[p.b] || N.db;
      const kk = k < 0.5 ? k * 2 : 2 - k * 2, x = A.x + (B.x - A.x) * kk, y = A.y + (B.y - A.y) * kk;
      const q = pt(x, y, iso ? 8 : 0); ctx.fillStyle = rgba(p.err ? C.danger : C.accent, 0.85); ctx.beginPath(); ctx.arc(q[0], q[1], 3 * px, 0, 6.3); ctx.fill();
    }
    for (const g of S.ghosts) { const [x, y] = routeAt(g.d), q = pt(x, y, iso ? 10 : 0); ctx.fillStyle = rgba(C.sec, 0.4); ctx.beginPath(); ctx.arc(q[0], q[1], 3 * px, 0, 6.3); ctx.fill(); }
    if (S.active) {
      if (!RM.matches && S.trail.length > 1) {
        ctx.lineCap = 'round';
        for (let i = 1; i < S.trail.length; i++) {
          const a = S.trail[i - 1], b = S.trail[i], A = pt(a[0], a[1], iso ? a[2] : 0), B = pt(b[0], b[1], iso ? b[2] : 0);
          ctx.strokeStyle = rgba(C.accentL, i / S.trail.length * 0.6); ctx.lineWidth = (1 + 3 * i / S.trail.length) * px;
          ctx.beginPath(); ctx.moveTo(A[0], A[1]); ctx.lineTo(B[0], B[1]); ctx.stroke();
        }
        ctx.lineCap = 'butt';
      }
      const [x, y] = routeAt(S.d), q = pt(x, y, iso ? S.pkz : 0);
      ctx.fillStyle = rgba(C.accent, 0.16); ctx.beginPath(); ctx.arc(q[0], q[1], 11 * px, 0, 6.3); ctx.fill();
      ctx.fillStyle = rgba(C.accent); ctx.beginPath(); ctx.arc(q[0], q[1], 5 * px, 0, 6.3); ctx.fill();
      ctx.fillStyle = rgba(C.surface); ctx.beginPath(); ctx.arc(q[0], q[1], 1.8 * px, 0, 6.3); ctx.fill();
    }
    ctx.setTransform(this.dpr, 0, 0, this.dpr, 0, 0);
    ctx.font = `600 10px ${MONO}`; ctx.textBaseline = 'top';
    try { ctx.letterSpacing = '1px'; } catch (_) {}
    const ry = n => (iso ? (n.fh ? n.fh / 2 : n.r) * OY : (n.fh ? n.fh / 2 : n.r)) * s;
    for (const n of NODES) {
      if (n.wsNode && !S.wsSeen) continue;
      const isAct = S.hl[n.id] > 0.3 || (n.wsNode && wsRun);
      const nErr = errOn(n.id) === 2 && !(n.wsNode ? wsRun : extRun.has(n.id));
      ctx.fillStyle = rgba(nErr ? C.danger : isAct ? C.accent : C.label);
      const lp = n.lp || 'bottom';
      if (lp === 'left' || lp === 'w') {
        const [x, y] = this.scr(n.x, n.y, iso ? n.h : 0), rr = (n.r + (lp === 'w' ? 4 : 26)) * s + 6;
        ctx.textAlign = 'right'; ctx.textBaseline = 'middle'; ctx.fillText(n.label, x - rr, y);
      } else if (lp === 'e') {
        const [x, y] = this.scr(n.x, n.y, iso ? n.h : 0);
        ctx.textAlign = 'left'; ctx.textBaseline = 'middle'; ctx.fillText(n.label, x + n.r * s + 7, y);
      } else if (lp === 'ws') {
        const [x, y] = this.scr(n.x, n.y - 42, iso ? n.h : 0);
        ctx.textAlign = 'center'; ctx.textBaseline = 'bottom';
        let yy = y - 5;
        if (S.wsId) {
          ctx.font = `500 9.5px ${MONO}`; try { ctx.letterSpacing = '0px'; } catch (_) {}
          ctx.fillStyle = rgba(wsRun ? C.accent : C.sec, 0.92); ctx.fillText(S.wsId, x, yy); yy -= 13;
          ctx.font = `600 10px ${MONO}`; try { ctx.letterSpacing = '1px'; } catch (_) {}
          ctx.fillStyle = rgba(isAct ? C.accent : C.label);
        }
        ctx.fillText(n.label, x, yy);
      } else if (lp === 'top') {
        const [x, y] = this.scr(n.x, n.y, iso ? n.h : 0);
        ctx.textAlign = 'center'; ctx.textBaseline = 'bottom'; ctx.fillText(n.label, x, y - ry(n) - (iso ? 5 : 8));
      } else {
        const [x, y] = this.scr(n.x, n.y, 0);
        const ly = y + ry(n) + (iso ? 6 : 8);
        ctx.textAlign = 'center'; ctx.textBaseline = 'top'; ctx.fillText(n.label, x, ly);
        if (n.wsNode && S.wsId) {
          ctx.font = `500 9.5px ${MONO}`; try { ctx.letterSpacing = '0px'; } catch (_) {}
          ctx.fillStyle = rgba(wsRun ? C.accent : C.sec, 0.92);
          ctx.fillText(S.wsId, x, ly + 13);
          ctx.font = `600 10px ${MONO}`; try { ctx.letterSpacing = '1px'; } catch (_) {}
        }
      }
    }
    for (const sl of SLOT_LIST) {
      const disc = S.discovered.has(sl.id);
      const [ax, ay] = this.scr(N.agent.x, N.agent.y, 0), [x, y] = this.scr(sl.x, sl.y, 0);
      let dx = x - ax, dy = y - ay; const L = Math.hypot(dx, dy) || 1; dx /= L; dy /= L;
      const off = Math.max(26 * s, sl.r * s) + 10, lx = x + dx * off, ly = y + dy * off;
      ctx.textAlign = dx > 0.35 ? 'left' : dx < -0.35 ? 'right' : 'center';
      ctx.textBaseline = dy < -0.5 ? 'bottom' : dy > 0.5 ? 'top' : 'middle';
      const run = [...S.calls.values()].filter(c => c.slot === sl.id);
      const se = errOn(sl.id);
      ctx.fillStyle = rgba(run.length ? C.accent : se === 2 ? C.danger : C.label, disc ? 1 : 0.55);
      ctx.fillText(sl.label, lx, ly);
      // На мобилке (≤768) строку ОШИБКА не рисуем: хватает красного ромба и бейджа ✕.
      if (!run.length && se && innerWidth > 768) {
        // Вторая строка у подписи слота, со стороны узла не залезает: время последнего вызова + ОШИБКА.
        const e = S.err[sl.id], last = (S.st[sl.id].calls[0] || {}).ms;
        ctx.font = `500 10px ${MONO}`; ctx.fillStyle = rgba(C.danger, se === 2 ? 1 : 0.7);
        const tx = (se === 2 ? 'ОШИБКА' : 'БЫЛА ОШИБКА') + (e.n > 1 ? ` ×${e.n}` : '') + (se === 2 && last != null ? ` · ${fmtS(last)}` : '');
        ctx.fillText(tx, lx, ctx.textBaseline === 'bottom' ? ly - 13 : ly + 13);
        ctx.font = `600 10px ${MONO}`;
      }
      if (run.length) {
        const ms = now() - Math.min(...run.map(c => c.t0));
        ctx.font = `500 10px ${MONO}`; ctx.fillStyle = rgba(C.accent, 0.9);
        ctx.fillText(fmtS(ms) + (run.length > 1 ? ` ×${run.length}` : ''), lx, ctx.textBaseline === 'bottom' ? ly - 13 : ly + 13);
        ctx.font = `600 10px ${MONO}`;
      }
    }
    ctx.textBaseline = 'alphabetic';
    try { ctx.letterSpacing = '0px'; } catch (_) {}
  }
}
const MINI = {el:$('#fmMini'), cv:$('#fmMiniCv'), on:false};
MINI.ctx = MINI.cv.getContext('2d');
function drawMini() {
  const cv = MINI.cv, r = cv.getBoundingClientRect(); if (!r.width) return;
  const dpr = Math.min(2, devicePixelRatio || 1);
  if (cv.width !== Math.round(r.width * dpr)) { cv.width = Math.round(r.width * dpr); cv.height = Math.round(r.height * dpr); }
  const W = r.width, span = Math.max(180, W - 78), kx = span / 260, X = v => 18 + v * kx;
  const MX = {user:X(0), gateway:X(46), chats:X(92), kA:X(130), kB:X(184), consumer:X(218), agent:X(260)};
  const xs = [MX.user, MX.gateway, MX.chats, MX.kA, MX.kB, MX.consumer, MX.agent];
  const c = MINI.ctx, Y = 30; c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, r.height);
  c.strokeStyle = rgba(C.stroke); c.lineWidth = 1.2;
  c.beginPath(); c.moveTo(MX.user, Y); c.lineTo(MX.agent, Y); c.stroke();
  if (S.t0) { const [, , i0, k0] = routeAt(S.d), xp = xs[i0] + (xs[Math.min(i0 + 1, 6)] - xs[i0]) * k0; c.strokeStyle = rgba(C.accent, .55); c.lineWidth = 1.6; c.beginPath(); c.moveTo(MX.user, Y); c.lineTo(xp, Y); c.stroke(); }
  const dot = (x, rr, fill, stroke) => { c.beginPath(); c.arc(x, Y, rr, 0, 6.3); c.fillStyle = fill; c.fill(); c.strokeStyle = stroke; c.lineWidth = 1.2; c.stroke(); };
  dot(MX.user, 3.5, rgba(C.surface), rgba(C.stroke));
  dot(MX.gateway, 5, rgba(C.surface), rgba(S.hl.gateway > .3 ? C.accent : C.stroke));
  dot(MX.chats, 5, rgba(C.surface), rgba(S.hl.chats > .3 ? C.accent : C.stroke));
  c.beginPath(); c.roundRect(MX.kA - 4, Y - 4.5, MX.kB - MX.kA + 8, 9, 4.5); c.fillStyle = rgba(C.surface); c.fill(); c.strokeStyle = rgba(S.phase === 'queue' ? C.accent : C.stroke); c.stroke();
  dot(MX.consumer, 4.5, rgba(C.surface), rgba(S.hl.consumer > .3 ? C.accent : C.stroke));
  const busy = S.phase === 'agent' || S.phase === 'answer';
  dot(MX.agent, 8, rgba(mix(C.surface, C.accent, busy ? .15 : 0)), rgba(busy ? C.accent : C.stroke));
  dot(MX.agent, 4, rgba(C.surface), rgba(busy ? C.accent : C.stroke));
  const runSlots = new Set([...S.calls.values()].map(cl => cl.slot));
  for (const sl of SLOT_LIST) {
    const x = MX.agent + Math.cos(sl.ang) * 19, y = Y + Math.sin(sl.ang) * 11;
    c.beginPath(); c.moveTo(x, y - 2.6); c.lineTo(x + 2.6, y); c.lineTo(x, y + 2.6); c.lineTo(x - 2.6, y); c.closePath();
    c.fillStyle = runSlots.has(sl.id) ? rgba(C.accent) : errOn(sl.id) === 2 ? rgba(C.danger) : S.discovered.has(sl.id) ? rgba(C.stroke) : rgba(C.muted, .35); c.fill();
  }
  if (S.active) {
    const [, , i, k] = routeAt(S.d), x = xs[i] + (xs[Math.min(i + 1, 6)] - xs[i]) * k;
    c.fillStyle = rgba(C.accent, .18); c.beginPath(); c.arc(x, Y, 7, 0, 6.3); c.fill(); c.fillStyle = rgba(C.accent); c.beginPath(); c.arc(x, Y, 3.4, 0, 6.3); c.fill();
  }
  c.font = `600 11.5px ${SANS || getComputedStyle(document.body).fontFamily}`; c.textBaseline = 'middle'; c.textAlign = 'left';
  const calls = [...S.calls.values()];
  let txt = S.status;
  if (calls.length) { const cl = calls[calls.length - 1]; txt = SLOTS[cl.slot].name + ' · ' + fmtS(now() - cl.t0) + (calls.length > 1 ? ` · +${calls.length - 1}` : ''); }
  else if (S.phase === 'done' && S.tEnd) txt = 'Готово · ' + fmtS(S.tEnd - S.t0);
  c.fillStyle = rgba(calls.length ? C.accent : C.text, calls.length ? 1 : 0.8); c.fillText(fitTxt(c, txt, Math.max(80, W - 42)), 10, 11);
  try { c.letterSpacing = '0px'; } catch (_) {}
}
let miniHideT = 0;

// ── Хост мини-вида: вертикальная панель в правом поле #view-agent или горизонтальная полоса над вводом ──
const RAIL_H = 456, RAIL_TOP = 82, COL = 720;
const HOST = {orient:'v', expanded:false, gutter:0};
const RAIL = {el:$('#fmRail'), cv:$('#fmRailCv')}; RAIL.ctx = RAIL.cv.getContext('2d');
let SANS = '';
function layoutHost() {
  const v = document.getElementById('view-agent');
  // Узлы держим по ссылке: renderMessageInput пересоздаёт композер через innerHTML,
  // и полоса, жившая в .chat-input-area, выпадает из DOM вместе с ним.
  const rail = RAIL.el, mini = MINI.el;
  if (!rail || !mini) return;
  if (v && !rail.isConnected) v.appendChild(rail);
  let vert = false;
  if (v && v.offsetParent !== null) {
    const r = v.getBoundingClientRect();
    const gutter = (r.width - COL) / 2; HOST.gutter = gutter;
    
    // ВЕРТИКАЛЬНАЯ ПАНЕЛЬ включается если экран десктопный и есть хоть какое-то правое поле.
    // Высота инпута (inTop) больше НЕ влияет на ориентацию, так как panel и input не пересекаются по горизонтали!
    vert = innerWidth > 768 && gutter >= 148;
    
    if (vert) {
      const w = Math.round(Math.min(184, gutter - 28));
      rail.style.width = w + 'px';
      rail.style.left = Math.round(r.width / 2 + COL / 2 + Math.max(12, (gutter - w) / 2 - 4)) + 'px';
      rail.style.top = Math.max(56, Math.min(RAIL_TOP, r.height - RAIL_H - 12)) + 'px';
    }
    
    const inp = v.querySelector('.chat-input-area');
    if (!vert && inp && mini.parentNode !== inp) inp.appendChild(mini);
    if (!vert && inp) { 
      const bx = inp.querySelector('.chat-input-box')?.getBoundingClientRect(), ia = inp.getBoundingClientRect(); 
      if (bx) { 
        mini.style.setProperty('--fm-in-l', Math.round(bx.left - ia.left) + 'px'); 
        mini.style.setProperty('--fm-in-r', Math.round(ia.right - bx.right) + 'px'); 
      } 
    }
  } else {
    // Если #view-agent скрыт (переключили вкладку), сохраняем десктопный вид, чтобы не мелькала плашка
    vert = innerWidth > 768;
  }
  HOST.orient = vert ? 'v' : 'h';
  rail.classList.toggle('is-on', MINI.on && vert);
  mini.classList.toggle('is-on', MINI.on && !vert);
  document.querySelectorAll('.wave-indicator.fm-tap').forEach(w => { w.classList.toggle('is-linked', MINI.on); w.setAttribute('aria-expanded', MINI.on ? 'true' : 'false'); });
  kick();
}
let tapHintT = 0;
function pulseTapHint() {
  clearTimeout(tapHintT);
  document.querySelectorAll('.wave-indicator.fm-tap').forEach(w => {
    w.classList.remove('is-hint');
    void w.offsetWidth;
    w.classList.add('is-hint');
  });
  tapHintT = setTimeout(() => {
    document.querySelectorAll('.wave-indicator.fm-tap.is-hint').forEach(w => w.classList.remove('is-hint'));
  }, 3600);
}
function showMini(on) { clearTimeout(miniHideT); MINI.on = !!on && HOST.expanded; layoutHost(); }
function expand() {
  clearTimeout(tapHintT);
  document.querySelectorAll('.wave-indicator.fm-tap.is-hint').forEach(w => w.classList.remove('is-hint'));
  if (HOST.expanded && MINI.on) { openModal(); return; }
  HOST.expanded = true; MINI.on = true; layoutHost();
}
function collapse() { HOST.expanded = false; MINI.on = false; layoutHost(); }
// Плейсхолдер animations.js: ступень 0 → тап раскрывает панель, повторный тап — модалку.
function bindPlaceholder(ph) {
  const w = ph && ph.querySelector('.wave-indicator'); if (!w) return;
  if (!w.classList.contains('fm-tap')) {
    w.classList.add('fm-tap'); w.setAttribute('role', 'button'); w.tabIndex = 0;
    w.setAttribute('aria-label', 'Показать путь запроса'); w.title = 'Показать путь запроса';
    const go = e => { e.preventDefault(); e.stopPropagation(); expand(); };
    w.addEventListener('click', go);
    w.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') go(e); });
  }
  w.classList.toggle('is-linked', MINI.on); w.setAttribute('aria-expanded', MINI.on ? 'true' : 'false');
  pulseTapHint();
}
function fitTxt(c, t, w) { if (c.measureText(t).width <= w) return t; while (t.length > 1 && c.measureText(t + '…').width > w) t = t.slice(0, -1); return t + '…'; }
const RAIL_NODES = [['user', 'ВЫ'], ['gateway', 'GATEWAY'], ['chats', 'CHATS'], ['kafka', 'KAFKA'], ['consumer', 'CONSUMER'], ['agent', 'AGENT']];
function drawRail() {
  const cv = RAIL.cv, r = cv.getBoundingClientRect(); if (!r.width) return;
  const dpr = Math.min(2, devicePixelRatio || 1);
  const pw = Math.round(r.width * dpr), ph = Math.round(r.height * dpr);
  if (cv.width !== pw || cv.height !== ph) { cv.width = pw; cv.height = ph; }
  if (!SANS) SANS = getComputedStyle(document.body).fontFamily || 'system-ui';
  const W = r.width, H = r.height, c = RAIL.ctx, t = now();
  c.setTransform(dpr, 0, 0, dpr, 0, 0); c.clearRect(0, 0, W, H);
  const X = 27, YS = [48, 82, 116, 144, 156, 186, 220];
  const NY = {user:YS[0], gateway:YS[1], chats:YS[2], kafka:(YS[3] + YS[4]) / 2, consumer:YS[5], agent:YS[6]};
  const busy = S.active && (S.phase === 'agent' || S.phase === 'answer');
  const ls = v => { try { c.letterSpacing = v; } catch (_) {} };
  c.textBaseline = 'middle'; c.textAlign = 'left';
  c.font = `600 10px ${MONO}`; ls('1px'); c.fillStyle = rgba(C.label); c.fillText('ПУТЬ ЗАПРОСА', 12, 17);
  ls('0px');
  c.strokeStyle = rgba(C.border); c.lineWidth = 1; c.beginPath(); c.moveTo(12, 30.5); c.lineTo(W - 12, 30.5); c.stroke();
  // ось
  c.strokeStyle = rgba(C.stroke); c.lineWidth = 1.2; c.beginPath(); c.moveTo(X, YS[0]); c.lineTo(X, YS[6]); c.stroke();
  let yp = YS[0];
  if (S.t0) { const [, , i, k] = routeAt(S.d); yp = YS[i] + (YS[Math.min(i + 1, 6)] - YS[i]) * k;
    c.strokeStyle = rgba(C.accent, .55); c.lineWidth = 1.8; c.beginPath(); c.moveTo(X, YS[0]); c.lineTo(X, yp); c.stroke(); }
  const on = id => id === 'kafka' ? (S.active && S.phase === 'queue') : id === 'agent' ? busy : S.hl[id] > .3;
  const dot = (y, rr, id) => { c.beginPath(); c.arc(X, y, rr, 0, 6.3); c.fillStyle = rgba(id === 'agent' && busy ? mix(C.surface, C.accent, .15) : C.surface); c.fill(); c.strokeStyle = rgba(on(id) ? C.accent : C.stroke); c.lineWidth = 1.2; c.stroke(); };
  dot(YS[0], 3.5, 'user'); dot(YS[1], 5, 'gateway'); dot(YS[2], 5, 'chats');
  c.beginPath(); c.roundRect(X - 4.5, YS[3] - 4, 9, YS[4] - YS[3] + 8, 4.5); c.fillStyle = rgba(C.surface); c.fill(); c.strokeStyle = rgba(on('kafka') ? C.accent : C.stroke); c.stroke();
  if (on('kafka') && !RM.matches) { c.save(); c.setLineDash([2, 4]); c.lineDashOffset = -t / 60; c.strokeStyle = rgba(C.accent, .7); c.beginPath(); c.moveTo(X, YS[3] - 1); c.lineTo(X, YS[4] + 1); c.stroke(); c.restore(); }
  dot(YS[5], 4.5, 'consumer'); dot(YS[6], 8, 'agent');
  c.beginPath(); c.arc(X, YS[6], 4, 0, 6.3); c.strokeStyle = rgba(busy ? C.accent : C.stroke); c.stroke();
  if (busy && !RM.matches) { c.save(); c.setLineDash([3, 4]); c.lineDashOffset = -t / 50; c.strokeStyle = rgba(C.accent, .5); c.beginPath(); c.arc(X, YS[6], 12, 0, 6.3); c.stroke(); c.restore(); }
  c.font = `600 10.5px ${MONO}`; ls('.5px');
  for (const [id, lb] of RAIL_NODES) { c.fillStyle = on(id) ? rgba(C.accent) : rgba(mix(C.label, C.text, DARK ? .25 : .2)); c.fillText(lb, 44, NY[id]); }
  ls('0px');
  // пакет
  if (S.active) { c.fillStyle = rgba(C.accent, .18); c.beginPath(); c.arc(X, yp, 7, 0, 6.3); c.fill(); c.fillStyle = rgba(C.accent); c.beginPath(); c.arc(X, yp, 3.4, 0, 6.3); c.fill(); }
  // инструменты
  const T0 = 274, STEP = 20;
  c.font = `600 9.5px ${MONO}`; ls('1px'); c.fillStyle = rgba(mix(C.label, C.text, DARK ? .1 : 0)); c.fillText('ИНСТРУМЕНТЫ', 44, 248); ls('0px');
  const runBy = new Map(); for (const cl of S.calls.values()) { const a = runBy.get(cl.slot); if (!a || cl.t0 < a.t0) runBy.set(cl.slot, {t0:cl.t0, n:(a ? a.n : 0) + 1}); else a.n++; }
  const logBy = new Map(); for (const l of S.toolLog) { const a = logBy.get(l.slot) || {n:0, ms:0, err:false}; a.n++; if (l.t1) a.ms += l.t1 - l.t0; a.err = l.t1 ? !l.ok : a.err; logBy.set(l.slot, a); }
  c.save(); c.setLineDash([2, 3]); c.strokeStyle = rgba(C.stroke, .8); c.lineWidth = 1; c.beginPath(); c.moveTo(X, YS[6] + 13); c.lineTo(X, T0 + (SLOT_LIST.length - 1) * STEP); c.stroke(); c.restore();
  SLOT_LIST.forEach((sl, i) => {
    const y = T0 + i * STEP, run = runBy.get(sl.id), lg = logBy.get(sl.id), seen = S.discovered.has(sl.id);
    if (run) { c.strokeStyle = rgba(C.accent, .9); c.lineWidth = 1.8; c.beginPath(); c.moveTo(X, YS[6] + 13); c.lineTo(X, y - 6); c.stroke(); }
    const d = run ? 5 : 4;
    c.beginPath(); c.moveTo(X, y - d); c.lineTo(X + d, y); c.lineTo(X, y + d); c.lineTo(X - d, y); c.closePath();
    if (run) { c.fillStyle = rgba(C.accent); c.fill(); c.strokeStyle = rgba(C.accent, .18); c.lineWidth = 1.4; c.beginPath(); c.arc(X, y, 8.5, 0, 6.3); c.stroke(); if (!RM.matches) { const a = (t / 400) % (Math.PI * 2); c.strokeStyle = rgba(C.accent, .75); c.beginPath(); c.arc(X, y, 8.5, a, a + 1.6); c.stroke(); } }
    else if (lg && lg.err) { c.fillStyle = rgba(C.danger); c.fill(); }
    else if (seen) { c.fillStyle = rgba(C.stroke); c.fill(); }
    else { c.fillStyle = rgba(C.surface); c.fill(); c.save(); c.setLineDash([2, 1.5]); c.strokeStyle = rgba(mix(C.muted, C.text, DARK ? .45 : .25)); c.lineWidth = 1.1; c.stroke(); c.restore(); }
    c.font = `${run ? 600 : 500} 11.5px ${SANS}`;
    c.fillStyle = run ? rgba(C.accent) : lg && lg.err ? rgba(C.danger) : seen ? rgba(mix(C.sec, C.text, DARK ? .25 : .35)) : rgba(mix(C.sec, C.muted, DARK ? .15 : .35));
    let info = '';
    if (run) info = fmtS(t - run.t0) + (run.n > 1 ? ` ×${run.n}` : '');
    else if (lg) info = (lg.n > 1 ? `×${lg.n} · ` : '') + fmtS(lg.ms);
    ls('0px'); c.font = `500 10px ${MONO}`; const iw = info ? c.measureText(info).width + 8 : 0;
    c.font = `${run ? 600 : 500} 11.5px ${SANS}`; c.fillText(fitTxt(c, sl.name, W - 44 - 12 - iw), 44, y + .5);
    if (info) { c.font = `500 10px ${MONO}`; c.textAlign = 'right'; c.fillStyle = run ? rgba(C.accent) : lg.err ? rgba(C.danger) : rgba(C.sec); c.fillText(info, W - 12, y + .5); c.textAlign = 'left'; }
  });
  // статус
  const yS = H - 48; c.strokeStyle = rgba(C.border); c.lineWidth = 1; c.beginPath(); c.moveTo(12, yS + .5); c.lineTo(W - 12, yS + .5); c.stroke();
  const calls = [...S.calls.values()];
  let st = S.status; if (calls.length) st = calls[calls.length - 1].label;
  c.font = `${calls.length ? 600 : 500} 11.5px ${SANS}`; c.fillStyle = calls.length ? rgba(C.accent) : rgba(C.text, .85); c.fillText(fitTxt(c, st, W - 24), 12, yS + 16);
  const total = S.t0 ? fmtS((S.tEnd || t) - S.t0) : '';
  c.font = `500 11px ${SANS}`; c.fillStyle = rgba(C.sec); c.fillText(total ? fitTxt(c, 'Весь запрос ' + total + (calls.length > 1 ? ` · ${calls.length} паралл.` : ''), W - 24) : '', 12, yS + 34);
}

let tlLast = 0;
function renderTimeline(force) {
  const t = now(); if (!force && t - tlLast < 200) return; tlLast = t;
  const tools = $('#tlTools');
  $('#fmStatus').textContent = S.status + (S.calls.size ? ' · ' + fmtS(t - Math.min(...[...S.calls.values()].map(c => c.t0))) : '');
  if (!S.t0) { tools.innerHTML = ''; $('#fmTotal').textContent = ''; return; }
  const end = S.tEnd || t, total = Math.max(1, end - S.t0), W = tools.clientWidth || 600;
  $('#fmTotal').textContent = fmtS(total);
  // Полоса инструментов (fm-tl__row--tools): читаемая ширина каждого сегмента в px + горизонтальный скролл.
  // Группируем параллельные вызовы одного шага в колонки по l.step, внутри колонки — по дорожкам (lane)
  const groups = [];
  for (const l of S.toolLog) {
    const dur = l.ms != null ? Math.max(1, l.ms) : Math.max(1, (l.t1 || end) - l.t0);
    const g = groups[groups.length - 1];
    if (g && g.step === (l.step || 0) && l.step) {
      const lane = g.items.length;
      g.items.push({l, lane, dur});
    } else {
      const gap = l.gap > 0 ? l.gap : 0;
      groups.push({step:l.step || 0, gap, items:[{l, lane:0, dur}]});
    }
  }
  const cw = 6.8, GAP_PX = 0, segs = [];
  let xCursor = 0, maxLanes = 1;
  for (const g of groups) {
    if (g.gap > 0) {
      const gTxt = `думает ${fmtS(g.gap)}`;
      const gw = thinkW(gTxt, g.gap);
      segs.push({cls:'fm-seg-g', k:'think', x:xCursor, w:gw, lane:0, txt:gTxt, title:`думает · ${fmtS(g.gap)} · размышления`, extra:'', think:{key:'s' + g.step, ms:g.gap, text:g.items[0].l.thinkText || ''}});
      xCursor += gw + GAP_PX;
    }
    maxLanes = Math.max(maxLanes, g.items.length);
    let colW = 64;
    for (const it of g.items) {
      const l = it.l, wsTag = l.ws ? (l.ws !== 'workspace' ? ` [${String(l.ws).slice(0, 6)}]` : ' [ws]') : '';
      const name = (l.ok ? '' : '✕ ') + SLOTS[l.slot].name + wsTag;
      it.txt = `${name} ${fmtS(it.dur)}`;
      it.title = `${l.label}${l.ws ? ' · ' + l.ws : ''} · ${fmtS(it.dur)}`;
      it.minW = Math.max(Math.ceil(it.txt.length * cw + 14), 64 + Math.round(14 * Math.pow(it.dur / 1000, 0.42)));
      colW = Math.max(colW, it.minW);
    }
    for (const it of g.items) {
      const l = it.l, extra = (!l.t1 ? ' is-run' : '') + (!l.ok ? ' is-err' : '');
      segs.push({cls:'fm-seg-t', k:l.slot, x:xCursor, w:colW, lane:it.lane, txt:it.txt, title:it.title, extra});
    }
    xCursor += colW + GAP_PX;
  }
  // Живой или финальный сегмент размышления агента (чтобы пока агент думает, справа не было пустого пространства)
  const isLiveThink = S.phase === 'agent' && S.calls.size === 0 && S.thinkT0 != null;
  const tailThink = isLiveThink ? Math.max(100, end - S.thinkT0) : (S.tailThinkMs >= 100 && S.toolLog.length ? S.tailThinkMs : 0);
  if (tailThink > 0) {
    const gTxt = `думает ${fmtS(tailThink)}`;
    const gw = thinkW(gTxt, tailThink);
    segs.push({cls:'fm-seg-g', k:'think', x:xCursor, w:gw, lane:0, txt:gTxt, title:`думает · ${fmtS(tailThink)} · размышления`, extra:isLiveThink ? ' is-run' : '', isTail:true, think:{key:'tail', ms:tailThink, text:null}});
    xCursor += gw + GAP_PX;
  }
  if (!segs.length) { tools.innerHTML = ''; TL.track = null; tools.style.display = 'none'; closeThink(); return; }
  tools.style.display = '';
  const rawTrackW = Math.max(1, xCursor - GAP_PX);
  const trackW = Math.max(W, Math.round(rawTrackW));
  const trackH = maxLanes * 19 - 2;
  // Высота авто: контент + свой отступ + полоса прокрутки (классический скроллбар не ложится поверх блоков).
  tools.style.height = '';
  const maxX = Math.max(...segs.map(s => s.x));
  TL.segs = segs;

  let track = TL.track && TL.track.parentNode === tools ? TL.track : null;
  if (!track) { tools.innerHTML = '<div class="fm-tl__track"></div>'; track = TL.track = tools.firstChild; }
  const keep = tools.scrollLeft;
  
  if (track.style.width !== trackW + 'px') track.style.width = trackW + 'px';
  if (track.style.height !== trackH + 'px') track.style.height = trackH + 'px';

  // Синхронизация DOM без innerHTML (чтобы не сбрасывать :hover, focus и mousedown)
  const children = track.children;
  for (let i = 0; i < segs.length; i++) {
    const s = segs[i];
    let el = children[i];
    if (!el) { el = document.createElement('div'); track.appendChild(el); }
    
    const sx = Math.round(s.x);
    const sw = (rawTrackW <= W && s.x === maxX) ? Math.max(36, trackW - sx) : Math.max(36, Math.round(s.w));
    const sel = s.think && TH.key === s.think.key ? ' is-sel' : '';
    const cls = s.cls + s.extra + sel;
    
    if (el.className !== cls) el.className = cls;
    if (el.dataset.k !== s.k) el.dataset.k = s.k;
    
    if (s.think) {
      if (el.dataset.i !== String(i)) el.dataset.i = i;
      if (el.getAttribute('role') !== 'button') el.setAttribute('role', 'button');
      if (el.getAttribute('tabindex') !== '0') el.setAttribute('tabindex', '0');
      if (el.getAttribute('aria-haspopup') !== 'dialog') el.setAttribute('aria-haspopup', 'dialog');
      if (sel) { if (el.getAttribute('aria-expanded') !== 'true') el.setAttribute('aria-expanded', 'true'); }
      else { if (el.hasAttribute('aria-expanded')) el.removeAttribute('aria-expanded'); }
    } else {
      if (el.hasAttribute('data-i')) el.removeAttribute('data-i');
      if (el.hasAttribute('role')) el.removeAttribute('role');
      if (el.hasAttribute('tabindex')) el.removeAttribute('tabindex');
      if (el.hasAttribute('aria-haspopup')) el.removeAttribute('aria-haspopup');
      if (el.hasAttribute('aria-expanded')) el.removeAttribute('aria-expanded');
    }
    
    const l = sx + 'px', w = sw + 'px', top = (s.lane * 19) + 'px';
    if (el.style.left !== l) el.style.left = l;
    if (el.style.width !== w) el.style.width = w;
    if (el.style.top !== top) el.style.top = top;
    
    if (el.title !== s.title) el.title = s.title;
    if (el.textContent !== s.txt) el.textContent = s.txt;
  }
  while (children.length > segs.length) track.removeChild(children[children.length - 1]);

  const maxL = Math.max(0, tools.scrollWidth - tools.clientWidth);
  // Автопрокрутка к живому краю — только пока пользователь сам не увёл полосу влево.
  if (TL.follow && (S.calls.size || isLiveThink)) setScroll(tools, maxL);
  else if (tools.scrollLeft !== Math.min(keep, maxL)) setScroll(tools, Math.min(keep, maxL));
  if (TH.key) {
    const i = segs.findIndex(s => s.think && s.think.key === TH.key);
    if (i < 0) closeThink();
    else { TH.from = children[i]; if (TH.key === 'tail') fillThink(); if (TH.from) placeThink(TH.from); }
  }
}
// ── Скролл полосы инструментов + оверлей «Размышления» по тапу на блок «думает» ──
const THINK_MAX = 20000;
const TL = {track:null, segs:[], follow:true, set:-1};
// followY: оверлей едет за живым текстом, пока пользователь у нижнего края (как TL.follow по X).
const TH = {key:null, el:null, followY:true};
function thinkW(txt, ms) { return Math.max(Math.ceil(txt.length * 6.8 + 12), 60 + Math.round(10 * Math.pow(ms / 1000, 0.45))); }
function setScroll(el, x) { el.scrollLeft = x; TL.set = el.scrollLeft; }
function thinkTextFor(key) {
  if (key === 'tail') return [S.thinkDone, S.thinkBuf.trim(), S.tailText].filter(Boolean).join('\n\n');
  const seg = TL.segs.find(s => s.think && s.think.key === key);
  return seg ? seg.think.text : '';
}
function esc(s) { return String(s).replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c])); }
function mdHtml(t) {
  try { if (window.marked && window.DOMPurify) return window.DOMPurify.sanitize(window.marked.parse(t, {breaks:true})); } catch (_) {}
  return '<p>' + esc(t).replace(/\n{2,}/g, '</p><p>').replace(/\n/g, '<br>') + '</p>';
}
function fillThink() {
  const el = TH.el; if (!el || !TH.key) return;
  const seg = TL.segs.find(s => s.think && s.think.key === TH.key);
  const ms = seg ? seg.think.ms : 0, live = TH.key === 'tail' && S.phase === 'agent' && !S.calls.size;
  el.querySelector('.fm-think__t').innerHTML = `Размышления · ${fmtS(ms)}${live ? '<span class="fm-think__live">идёт</span>' : ''}`;
  const txt = thinkTextFor(TH.key), body = el.querySelector('.fm-think__b');
  if (body.dataset.src === txt) return;
  const keep = body.scrollTop;
  body.dataset.src = txt;
  body.innerHTML = txt ? mdHtml(txt) : '<p class="fm-think__empty">Текст размышлений для этого шага не пришёл: модель сразу вызвала инструмент.</p>';
  // Автоскролл по Y: пока у нижнего края — держимся за концом текста; увёл вверх — позиция не прыгает.
  if (TH.followY) thinkToEnd(body); else body.scrollTop = keep;
}
function thinkToEnd(body) { body.scrollTop = body.scrollHeight; TH.setY = body.scrollTop; }
function openThink(segEl) {
  const s = TL.segs[+segEl.dataset.i]; if (!s || !s.think) return;
  if (TH.key === s.think.key) { closeThink(); return; }
  const foot = $('.fm-foot');
  if (!TH.el) {
    TH.el = document.createElement('div'); TH.el.className = 'fm-think'; TH.el.setAttribute('role', 'dialog'); TH.el.setAttribute('aria-label', 'Размышления');
    TH.el.innerHTML = '<div class="fm-think__h"><span class="fm-think__t"></span><button class="fm-think__x" aria-label="Закрыть">×</button></div><div class="fm-think__b"></div>';
    TH.el.querySelector('.fm-think__x').addEventListener('click', e => { e.stopPropagation(); closeThink(true); });
    TH.el.addEventListener('pointerdown', e => e.stopPropagation());
    const b = TH.el.querySelector('.fm-think__b');
    // Ручная прокрутка отличается от нашей по значению scrollTop: своё смещение (TH.setY) follow не сбрасывает.
    b.addEventListener('scroll', () => {
      if (b.scrollTop === TH.setY) return;
      TH.followY = b.scrollTop + b.clientHeight >= b.scrollHeight - 16;
    }, {passive:true});
    foot.appendChild(TH.el);
  }
  TH.key = s.think.key; TH.from = segEl;
  // Живой блок открываем на конце текста и едем за ним; завершённый — с начала.
  TH.followY = TH.key === 'tail'; TH.setY = -1;
  const body = TH.el.querySelector('.fm-think__b'); body.dataset.src = '\u0000'; body.scrollTop = 0;
  fillThink(); placeThink(segEl); if (TH.followY) thinkToEnd(body);
  TH.el.classList.add('is-on');
  for (const x of document.querySelectorAll('.fm-seg-g[data-i]')) { const on = x === segEl; x.classList.toggle('is-sel', on); on ? x.setAttribute('aria-expanded', 'true') : x.removeAttribute('aria-expanded'); }
}
function placeThink(segEl) {
  const foot = $('.fm-foot'), fr = foot.getBoundingClientRect(), sr = segEl.getBoundingClientRect();
  const tr = $('#tlTools').getBoundingClientRect(), pl = tr.left - fr.left, pr = fr.right - tr.right;
  const w = Math.min(520, tr.width);
  // Стрелка — в центр видимой части выбранного блока.
  const vl = Math.max(sr.left, tr.left), vr = Math.min(sr.right, tr.right);
  const cx = (vl + vr) / 2 - fr.left;
  const left = Math.max(pl, Math.min(fr.width - pr - w, cx - w / 2));
  TH.el.style.width = w + 'px'; TH.el.style.left = left + 'px';
  // Оверлей над строкой статуса: этапы, статус и полоса тулов остаются видны, стрелка — над выбранным блоком.
  const stt = $('.fm-status').getBoundingClientRect();
  TH.el.style.bottom = Math.round(fr.bottom - stt.top + 14) + 'px';
  TH.el.classList.toggle('no-arrow', vr - vl < 8);
  TH.el.style.setProperty('--fm-think-arrow', Math.max(14, Math.min(w - 14, cx - left)) + 'px');
}
function closeThink(focus) {
  const from = TH.from; TH.key = null; TH.from = null;
  if (TH.el) TH.el.classList.remove('is-on');
  for (const x of document.querySelectorAll('.fm-seg-g.is-sel')) { x.classList.remove('is-sel'); x.removeAttribute('aria-expanded'); }
  if (focus && from && from.isConnected) from.focus({preventScroll:true});
}
function showCard(R, n, x, y) {
  for (const c of document.querySelectorAll('.fm-card')) c.classList.remove('is-on');
  if (!n) return;
  const card = R.cv.parentElement.querySelector('.fm-card'), st = S.st[n.id];
  const tech = n.wsNode ? (S.wsId ? `workspace · ${S.wsId}` : 'workspace') : n.tech;
  let html = `<h4>${n.name}</h4><code>${tech}</code><p>${n.desc}</p>`;
  if (n.wsNode) {
    const ids = [...S.wsIds];
    if (ids.length > 1) html += `<p>Области: ${ids.join(', ')}</p>`;
    html += `<ul>${st.calls.map(c => `<li><span>${c.label}</span><span class="${c.ok ? '' : 'err'}">${c.ok ? '' : 'ошибка · '}${fmtS(c.ms)}</span></li>`).join('') || '<li><span>работа с файлами…</span><span></span></li>'}</ul>`;
  } else if (n.tool) {
    if (!S.discovered.has(n.id)) html += `<p>Агент ещё не заходил сюда в этой сессии.</p>`;
    else html += `<ul>${st.calls.map(c => `<li><span>${c.label}</span><span class="${c.ok ? '' : 'err'}">${c.ok ? '' : 'ошибка · '}${fmtS(c.ms)}</span></li>`).join('') || '<li><span>выполняется…</span><span></span></li>'}</ul>`;
  } else html += `<ul><li><span>пакет был здесь</span><span>${st.visits} раз</span></li><li><span>всего времени</span><span>${fmtS(st.ms)}</span></li></ul>`;
  card.innerHTML = html; card.classList.add('is-on');
  const cw = card.offsetWidth, chh = card.offsetHeight;
  card.style.left = Math.max(8, Math.min(R.W - cw - 8, x + 14)) + 'px';
  card.style.top = Math.max(8, Math.min(R.H - chh - 8, y + 14)) + 'px';
}
const VIEW = {layout:'flat', mode:'watch', fit:false, open:false};
const RENDERERS = [];
const rFlat = new MapR($('#cvFlat'), false), rIso = new MapR($('#cvIso'), true);
RENDERERS.push(rFlat, rIso);
function setLayout(l) {
  VIEW.layout = l; document.querySelector('.fm-dlg')?.setAttribute('data-layout', l);
  document.querySelectorAll('#fmLayout button').forEach(b => b.classList.toggle('is-on', b.dataset.l === l));
  $('#pFlat').style.display = l === 'iso' ? 'none' : ''; $('#pIso').style.display = l === 'flat' ? 'none' : '';
  for (const R of RENDERERS) { R.inited = false; R.resize(); } kick();
}
function setMode(m) {
  VIEW.mode = m; document.querySelectorAll('#fmMode button').forEach(b => b.classList.toggle('is-on', b.dataset.m === m));
  if (m === 'watch') for (const R of RENDERERS) R.manual = false;
  kick();
}
function syncFit() { $('#fmFit').classList.toggle('is-on', VIEW.fit); }
function openModal() { VIEW.open = true; $('#fmBack').classList.add('is-open'); setLayout(VIEW.layout); renderTimeline(true); kick(); }
function closeModal() { closeThink(); const wasOpen = VIEW.open; VIEW.open = false; if (wasOpen && MINI.on) setTimeout(() => { const el = HOST.orient === 'v' ? RAIL.el : MINI.el; if (el && el.isConnected) el.focus({preventScroll:true}); }, 0); $('#fmBack').classList.remove('is-open'); for (const c of document.querySelectorAll('.fm-card')) c.classList.remove('is-on'); for (const R of RENDERERS) R.visible = false; }
const STATS = {renders:0, ms:0, msAvg:0, msMax:0, fps:0, frames:0, fpsT:0};
let raf = 0, slowT = 0, lastT = 0, lastDraw = 0;
function kick() { if (!raf) raf = requestAnimationFrame(frame); }
function update(dt, t) {
  let moving = false;
  if (S.active) {
    const prev = S.d;
    if (RM.matches) S.d = S.allow;
    else if (S.dir > 0) {
      const slow = S.phase === 'queue' && S.d >= D[3] - 1;
      S.d = Math.min(S.allow, S.d + (slow ? 60 : 640) * dt / 1000);
    } else S.d = Math.max(0, S.d - 900 * dt / 1000);
    if (S.d !== prev) moving = true;
    if (S.d !== prev || !S.trail.length) {
      const lo = Math.min(prev, S.d), hi = Math.max(prev, S.d);
      for (let i = 0; i < D.length; i++) if (D[i] >= lo - 0.01 && D[i] <= hi + 0.01) { const id = ROUTE[i][2]; S.hl[id] = 1; if (id === 'chats' && S.dir > 0) S.pulses.push({t, dur:520}); }
    }
    const [x, y, seg] = routeAt(S.d);
    const ei = EDGES.findIndex(e => e[2] === seg); if (ei >= 0 && prev !== S.d) S.lit[ei] = 1;
    let inside = null, z = 9;
    for (const n of NODES) {
      const dd = n.kind === 'capsule' ? (Math.abs(y - n.y) < n.r && Math.abs(x - n.x) < n.len / 2 ? 0 : 99) : Math.hypot(x - n.x, y - n.y);
      if (dd < n.r) { inside = n.id; z = Math.max(z, n.h + 4); break; }
    }
    S.pkz += (z - S.pkz) * Math.min(1, dt / 90);
    if (inside) { S.st[inside].ms += dt; if (inside !== S.lastInside) S.st[inside].visits++; }
    S.lastInside = inside;
    if (inside === 'agent' && S.phase === 'agent') S.hl.agent = Math.max(S.hl.agent, 0.6);
    if (!RM.matches) { S.trail.push([x, y, S.pkz]); if (S.trail.length > 14) S.trail.shift(); if (!moving) S.trail.shift(); }
    if (S.phase === 'done' && S.d <= 0.01) { S.active = false; S.hl.user = 1; renderTimeline(true); }
  }
  for (const cl of S.calls.values()) if (cl.ext) S.hl[cl.ext] = 1;
  const k = Math.pow(0.08, dt / 1000);
  for (const id in S.hl) if (S.hl[id] > 0.001) { if (!(id === 'agent' && S.phase === 'agent')) S.hl[id] *= k; moving = true; } else S.hl[id] = 0;
  for (let i = 0; i < S.lit.length; i++) if (S.lit[i] > 0.01) { S.lit[i] *= k; moving = true; } else S.lit[i] = 0;
  S.fx = S.fx.filter(f => t - f.t < f.dur); S.pulses = S.pulses.filter(p => t - p.t < p.dur);
  if (S.fx.length || S.pulses.length) moving = true;
  const want = VIEW.open ? Math.min(5, Math.max(0, S.others)) : 0;
  while (S.ghosts.length < want) S.ghosts.push({d:Math.random() * DMAX, dir:Math.random() < .5 ? 1 : -1, sp:110 + Math.random() * 120});
  S.ghosts.length = want;
  if (!RM.matches) for (const g of S.ghosts) { g.d += g.dir * g.sp * dt / 1000; if (g.d > DMAX) { g.d = DMAX; g.dir = -1; } if (g.d < 0) { g.d = 0; g.dir = 1; } }
  return moving;
}
function frame(ts) {
  raf = 0;
  if (ts - lastDraw < 31) { raf = requestAnimationFrame(frame); return; }
  const dt = lastT ? Math.min(100, ts - lastT) : 16; lastT = ts; lastDraw = ts;
  const t0 = performance.now();
  let more = update(dt, ts);
  if (VIEW.open) for (const R of RENDERERS) { if (R.visible) { if (R.update(dt)) more = true; R.draw(ts); } }
  if (MINI.on) { if (HOST.orient === 'v') drawRail(); else drawMini(); }
  const ms = performance.now() - t0;
  STATS.renders++; STATS.ms = ms; STATS.msAvg = STATS.msAvg ? STATS.msAvg * 0.9 + ms * 0.1 : ms; STATS.msMax = Math.max(STATS.msMax, ms); STATS.frames++;
  if (ts - STATS.fpsT > 1000) { STATS.fps = STATS.frames * 1000 / (ts - STATS.fpsT); STATS.frames = 0; STATS.fpsT = ts; }
  if (VIEW.open) renderTimeline(false);
  hud();
  const live = S.active || (VIEW.open && S.ghosts.length > 0);
  if (RM.matches) { if (more) raf = requestAnimationFrame(frame); else if (live) { clearTimeout(slowT); slowT = setTimeout(kick, 500); } else lastT = 0; }
  else if (more || live) raf = requestAnimationFrame(frame);
  else lastT = 0;
}
function hud() { const h = $('#fvHud'); if (h.style.display !== 'block') return; h.textContent = `fps ${STATS.fps.toFixed(0)}  кадр ${STATS.msAvg.toFixed(2)} мс\nрендеров ${STATS.renders}  фаза ${S.phase}`; }
let demoT = [];
function demo(name) {
  demoT.forEach(clearTimeout); demoT = [];
  const at = (ms, type, d) => demoT.push(setTimeout(() => emit(type, d), ms));
  const T = {web:{slot:'web', label:'Ищу в интернете', ext:'searxng'}, code:{slot:'sandbox', label:'Запускаю код', ext:'sandbox_c', ws:'d976e0e002fd'},
    wsf:{slot:'sandbox', label:'Правлю файл', ws:'d976e0e002fd'},
    img:{slot:'media', label:'Рисую картинку'}, kb:{slot:'kb', label:'Смотрю в базе знаний'}, mem:{slot:'kb', label:'Листаю журнал'},
    task:{slot:'tasks', label:'Ставлю фоновую задачу'}, file:{slot:'mail', label:'Отправляю файл'}};
  const tool = (t0, t1, id, k, ok = true, gap = null) => { at(t0, 'tool_start', {...T[k], id, gap_ms:gap}); at(t1, 'tool_end', {...T[k], id, ok, ms:t1 - t0}); };
  at(0, 'send'); at(160, 'gateway'); at(1300, 'agent_start');
  if (name === 'simple') { at(3600, 'answer'); at(4600, 'done'); }
  if (name === 'tools') { tool(2000, 4300, 'd1', 'web', true, 700); tool(4700, 7600, 'd2', 'code', true, 400); tool(7900, 10300, 'd3', 'img', true, 300); at(10700, 'answer'); at(12000, 'done'); }
  if (name === 'burst') {
    tool(1900, 5200, 'a', 'web', true, 600); tool(2050, 3600, 'b', 'kb', true, 600); tool(2200, 4300, 'c', 'mem', true, 600);
    tool(5500, 6600, 'd4', 'wsf', true, 300); tool(6800, 7700, 'd5', 'file', true, 200); at(8100, 'answer'); at(9300, 'done');
  }
  if (name === 'error') { tool(2000, 4200, 'e1', 'code', false, 700); tool(4600, 6900, 'e2', 'code', true, 400); at(7300, 'answer'); at(8500, 'done'); }
}
$('#fmMini').onclick = e => { if (e.target.closest('.fm-mini__x')) { e.stopPropagation(); collapse(); } else expand(); };
$('#fmRail').onclick = e => { if (e.target.closest('.fm-rail__x')) collapse(); else openModal(); };
for (const el of [$('#fmMini'), $('#fmRail')]) el.addEventListener('keydown', e => { if (e.key === 'Enter' || e.key === ' ') { e.preventDefault(); if (e.target.closest('.fm-rail__x,.fm-mini__x')) collapse(); else openModal(); } });
$('#fmClose').onclick = closeModal;
$('#fmBack').addEventListener('click', e => { if (e.target === e.currentTarget) closeModal(); });
document.addEventListener('keydown', e => { if (e.key === 'Escape' && VIEW.open) { const c = document.querySelector('.fm-card.is-on'); if (TH.key) closeThink(true); else if (c) c.classList.remove('is-on'); else closeModal(); } });
{
  const tools = $('#tlTools');
  // Ручной скролл: пользователь увёл полосу от правого края — перестаём автоскроллить, вернул к краю — снова следим.
  tools.addEventListener('scroll', () => {
    // Своё программное смещение отличаем по значению, а не по времени.
    if (Math.abs(tools.scrollLeft - TL.set) < 2) return;
    TL.set = -1;
    TL.follow = tools.scrollLeft >= tools.scrollWidth - tools.clientWidth - 24;
    if (TH.key && TH.from && TH.from.isConnected) placeThink(TH.from);
  }, {passive:true});
  let pdHandled = false;
  tools.addEventListener('pointerdown', e => {
    if (e.pointerType === 'touch' || (e.button != null && e.button !== 0)) return;
    const s = e.target.closest('.fm-seg-g[data-i]');
    if (!s) return;
    pdHandled = true;
    e.stopPropagation();
    openThink(s);
  });
  tools.addEventListener('click', e => {
    const s = e.target.closest('.fm-seg-g[data-i]');
    if (!s) return;
    e.stopPropagation();
    if (pdHandled) { pdHandled = false; return; }
    openThink(s);
  });
  tools.addEventListener('keydown', e => { const s = e.target.closest('.fm-seg-g[data-i]'); if (s && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); openThink(s); } });
  // Тап мимо оверлея закрывает его (но не модалку).
  $('#fmBack').addEventListener('pointerdown', e => { if (TH.key && !e.target.closest('.fm-think,.fm-seg-g[data-i]')) closeThink(); });
}
document.querySelectorAll('#fmLayout button').forEach(b => b.onclick = () => setLayout(b.dataset.l));
document.querySelectorAll('#fmMode button').forEach(b => b.onclick = () => setMode(b.dataset.m));
$('#fmFit').onclick = () => { VIEW.fit = !VIEW.fit; syncFit(); if (VIEW.fit) for (const R of RENDERERS) R.manual = false; kick(); };
new MutationObserver(() => { readTheme(); kick(); }).observe(document.documentElement, {attributes:true, attributeFilter:['data-theme']});
const ro = new ResizeObserver(() => { if (VIEW.open) for (const R of RENDERERS) R.resize(); kick(); });
ro.observe($('#pFlat')); ro.observe($('#pIso'));
const roHost = new ResizeObserver(() => { if (MINI.on) layoutHost(); }); { const v = document.getElementById('view-agent'); if (v) roHost.observe(v); }
addEventListener('resize', () => { if (MINI.on) layoutHost(); });
{ const mic = document.querySelector('#view-agent .message-input-container'); if (mic) new MutationObserver(() => { if (MINI.on) layoutHost(); }).observe(mic, {childList:true}); }
readTheme();
// ── Восстановление хронологии после F5 / смены чата: Redis flow:{chat_id} + agent:reasoning ──
// Сервер отдаёт события с ts (мс epoch) и своё now: переводим в performance.now() по разнице
// с серверным now, поэтому расхождение часов клиента не влияет на длительности.
const RS = {gen:0, busy:false};
async function restore(chatId, opts) {
  if (chatId == null) return false;
  if (S.active && !S.rid) return false;                                   // отправка в полёте
  const gen = RS.gen;
  let j;
  try {
    const r = await fetch(`/api/chats/${encodeURIComponent(chatId)}/flow`, {credentials:'include', redirect:'manual'});
    if (!r.ok) return false;
    j = await r.json();
  } catch (_) { return false; }
  if (gen !== RS.gen) return false;                                        // за время запроса сменили чат
  return replay(chatId, j, opts);
}
function replay(chatId, j, opts) {
  const all = (j && Array.isArray(j.events)) ? j.events : [];
  let rid = null;
  for (let i = all.length - 1; i >= 0 && !rid; i--) if (all[i].rid) rid = all[i].rid;
  const evs = all.filter(e => !rid || !e.rid || e.rid === rid).sort((a, b) => (a.ts || 0) - (b.ts || 0));
  if (!evs.length) return false;
  if (S.rid && S.rid === rid && String(S.chat) === String(chatId)) return false;   // уже ведём этот запрос вживую
  const rs = j.reasoning && (!j.reasoning.request_id || !rid || j.reasoning.request_id === rid) ? j.reasoning : {};
  const steps = Array.isArray(rs.steps) ? rs.steps : [];
  const base = performance.now() - (+j.now || Date.now());
  const at = e => Math.min(performance.now(), base + (+e.ts || +j.now || Date.now()));
  const thOpen = TH.key;
  RS.busy = true;
  try {
    const chat = String(chatId);
    if (evs[0].ev !== 'send') { T_OVR = at(evs[0]); emit('send', {chat}); T_OVR = null; }
    let si = 0;
    for (const e of evs) {
      const d = {chat, request_id: rid || undefined};
      for (const k of ['slot', 'label', 'ext', 'ws', 'ok', 'ms', 'gap_ms', 'role']) if (e[k] != null) d[k] = e[k];
      if (e.call_id) d.id = e.call_id;
      T_OVR = at(e);
      if (e.ev === 'send') emit('send', {chat});
      else if (e.ev === 'think_end') { const txt = steps[si++]; if (txt) emit('think_text', {chat, text:txt}); emit('think_end', {chat}); }
      else emit(e.ev, d);
      T_OVR = null;
    }
    // Живой блок, который ещё не закрыт reasoning_end.
    if (S.active && S.phase === 'agent' && rs.live_text) emit('think_text', {chat, text:rs.live_text});
    // Если в Redis флаг agent:running уже снят (running === false), а хронология оборвалась
    // без события done (например, пустой ответ или рестарт), завершаем путь на последнем событии.
    const isRunning = (opts && typeof opts.running === 'boolean') ? opts.running : (j && typeof j.running === 'boolean' ? j.running : null);
    if (isRunning === false && S.active && S.phase !== 'done') {
      T_OVR = at(evs[evs.length - 1]);
      emit('done', {chat, request_id: rid || undefined});
      T_OVR = null;
    }
  } finally { T_OVR = null; RS.busy = false; }
  // Пакет сразу на месте: без пролёта по уже пройденному пути.
  if (S.active) {
    if (S.phase === 'done') { S.d = 0; S.active = false; S.hl.user = 1; }
    else { S.d = S.allow; S.trail = []; }
    // Пройденный путь: одной вспышкой подсвечиваем рёбра и узлы маршрута до текущей точки пакета.
    const upto = S.active ? S.d : DMAX;
    for (let dd = 0; dd <= upto; dd += 6) { const seg = routeAt(dd)[2], ei = EDGES.findIndex(e => e[2] === seg); if (ei >= 0) S.lit[ei] = 1; }
    for (let i = 0; i < D.length; i++) if (D[i] <= upto + 0.01) S.hl[ROUTE[i][2]] = 1;
  }
  if (thOpen && TL.segs.some(x => x.think && x.think.key === thOpen)) {} else if (thOpen) closeThink();
  renderTimeline(true); kick();
  return true;
}
window.FlowViz = {emit, reset, restore, replay, demo, open:openModal, close:closeModal, expand, collapse, bindPlaceholder, host:HOST, layoutHost, setLayout, setMode, setOthers:n => { S.others = n; kick(); }, state:S, view:VIEW, stats:STATS, renderers:{flat:rFlat, iso:rIso}, SLOTS, tl:TL};

function mountDom() {
  if (document.getElementById('fmBack')) return;
  const host = document.getElementById('view-agent') || document.body;
  host.insertAdjacentHTML('beforeend', '<div class="fm-rail" id="fmRail" role="button" tabindex="0"><canvas id="fmRailCv"></canvas><button class="fm-rail__x" type="button" aria-label="Свернуть путь запроса" title="Свернуть">×</button></div>');
  document.body.insertAdjacentHTML('beforeend', '<div class="fm-mini" id="fmMini" role="button" tabindex="0"><canvas id="fmMiniCv"></canvas><button class="fm-mini__x" type="button" aria-label="Скрыть путь запроса" title="Скрыть">×</button></div><div id="fvHud"></div>' + MODAL_HTML);
}
})();
