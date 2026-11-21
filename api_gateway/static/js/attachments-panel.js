// Правая панель вложений чата: вкладки «Медиа / Файлы / Ссылки», фильтр «Все / Я / Агент»,
// во вкладке «Файлы» переключатель «Список / Ветки» (дерево по цепочке форков).
// Данные: GET /api/chats/{id}/attachments (attachments_gw.py -> chats_service),
// превью сетки: GET /api/chats/{id}/thumb/{message_id}/{file_name}?w=216.
// Клик: картинка/видео -> лайтбокс приложения, файл -> text-preview-panel (FileTiles), ссылка -> новая вкладка.
// ПКМ (долгое нажатие на тач): «Перейти к сообщению» через app.navigateToSearchMessage, как в поиске.
(function () {
  'use strict';
  const LS = 'att:prefs';
  const PAGE = { media: 36, files: 30, links: 30 };
  const TABS = [['media', 'Медиа'], ['files', 'Файлы'], ['links', 'Ссылки']];
  const AUTH = [['all', 'Все'], ['me', 'Я'], ['agent', 'Агент']];
  const LABEL = { xlsx: 'XLS', xls: 'XLS', docx: 'DOC', doc: 'DOC', pptx: 'PPT', ppt: 'PPT', jpeg: 'JPG', markdown: 'MD' };
  const COLOR = {
    pdf: 'red', doc: 'blue', docx: 'blue', md: 'blue', txt: 'gray', ts: 'blue',
    xls: 'green', xlsx: 'green', csv: 'green', ppt: 'orange', pptx: 'orange', html: 'orange', htm: 'orange',
    png: 'purple', jpg: 'purple', jpeg: 'purple', gif: 'purple', webp: 'purple', svg: 'purple', css: 'purple',
    mp4: 'pink', mov: 'pink', webm: 'pink', mp3: 'pink', wav: 'pink',
    json: 'yellow', xml: 'yellow', js: 'yellow', yaml: 'yellow', yml: 'yellow', py: 'teal', ipynb: 'teal', sql: 'teal',
  };
  const VMIME = { mp4: 'video/mp4', m4v: 'video/mp4', mov: 'video/quicktime', webm: 'video/webm', mkv: 'video/x-matroska', avi: 'video/x-msvideo' };
  // Иконка кнопки: рамка с горами и солнцем («Греция»).
  const ICON = '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><rect x="3" y="4" width="18" height="16" rx="3"/><path d="M3.5 17.5l5.2-5.6 3.6 3.9 2.4-2.6 5.8 5.8"/><circle cx="16.3" cy="8.7" r="1.6"/></svg>';
  const X = '<svg width="16" height="16" viewBox="0 0 16 16" fill="none" aria-hidden="true"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>';
  const DL = '<button type="button" class="att-dl" aria-label="Скачать" tabindex="-1"><svg viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M7 2v7M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg></button>';
  const PLAY = '<span class="att-play" aria-hidden="true"><svg viewBox="0 0 16 16"><path d="M5 3.5v9l7.5-4.5z" fill="currentColor"/></svg></span>';

  const prefs = (() => { try { return JSON.parse(localStorage.getItem(LS)) || {}; } catch (_) { return {}; } })();
  const st = {
    chat: null, open: false, el: null, scrim: null,
    tab: TABS.some(t => t[0] === prefs.tab) ? prefs.tab : 'media',
    view: prefs.view === 'tree' ? 'tree' : 'list',
    author: AUTH.some(a => a[0] === prefs.author) ? prefs.author : 'all',
    seq: 0, items: [], cursor: null, done: false, loading: false,
    counts: null, chain: [], stale: false, cancelSk: null, io: null, last: null,
  };
  const save = () => { try { localStorage.setItem(LS, JSON.stringify({ tab: st.tab, view: st.view, author: st.author })); } catch (_) {} };
  const esc = s => String(s ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
  const mobile = () => window.matchMedia('(max-width: 600px)').matches;
  const app = () => window.__catchpmApp;
  const enc = encodeURIComponent;
  const extOf = n => { const s = String(n || ''); const i = s.lastIndexOf('.'); return i > 0 ? s.slice(i + 1).toLowerCase() : ''; };
  const lab = e => LABEL[e] || (e ? e.toUpperCase().slice(0, 4) : 'FILE');
  const fmtSize = b => (b ? (window.FileTiles?.fmtSize ? window.FileTiles.fmtSize(b) : `${Math.round(b / 1024)} КБ`) : '');
  const myInitials = () => (document.querySelector('.profile-av')?.textContent || '').trim().slice(0, 2) || 'Я';
  const av = a => a === 'agent' ? '<span class="att-av att-av--bot" aria-hidden="true">A</span>' : `<span class="att-av" aria-hidden="true">${esc(myInitials())}</span>`;
  const who = a => a === 'agent' ? 'Агент' : 'Вы';
  const plural = (n, a, b, c) => { const m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c; };

  const dayKey = iso => { const d = new Date(iso || 0); return `${d.getFullYear()}-${d.getMonth()}-${d.getDate()}`; };
  function dayLabel(iso) {
    const d = new Date(iso || 0), now = new Date();
    const t0 = new Date(now.getFullYear(), now.getMonth(), now.getDate()).getTime();
    const t = new Date(d.getFullYear(), d.getMonth(), d.getDate()).getTime();
    if (t === t0) return 'Сегодня';
    if (t === t0 - 864e5) return 'Вчера';
    const o = { day: 'numeric', month: 'long' };
    if (d.getFullYear() !== now.getFullYear()) o.year = 'numeric';
    return d.toLocaleDateString('ru-RU', o);
  }
  const hm = iso => new Date(iso || 0).toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
  const host = u => { try { return new URL(u).host.replace(/^www\./, ''); } catch (_) { return u; } };

  // ---------- URL файлов ----------
  const base = () => `/api/chats/${enc(st.chat)}`;
  const fileUrl = it => `${base()}/files/${enc(it.message_id)}/${enc(it.name)}`;
  const dbUrl = it => `${base()}/image/${enc(it.message_id)}/${enc(it.name)}${it.image_key ? `?image_key=${enc(it.image_key)}` : ''}`;
  const thumbUrl = it => `${base()}/thumb/${enc(it.message_id)}/${enc(it.name)}?w=216${it.image_key ? `&image_key=${enc(it.image_key)}` : ''}`;
  function mediaItem(it) {
    if (it.kind === 'video') return { isVideo: true, name: it.name, ext: it.ext, url: fileUrl(it), dlUrl: `${fileUrl(it)}?download=1`, mime: VMIME[it.ext] || 'video/mp4', file: it };
    const url = it.image_key ? dbUrl(it) : fileUrl(it);
    return { isVideo: false, name: it.name, ext: it.ext, url, fallbackUrl: it.image_key ? fileUrl(it) : dbUrl(it), dlUrl: url, imageKey: it.image_key || null, file: it };
  }

  // ---------- ветки ----------
  const forked = () => st.chain.length > 1;
  const chainOf = id => st.chain.find(c => String(c.chat_id) === String(id));
  function branchTag(it) {
    if (!forked()) return '';
    const c = chainOf(it.chat_id);
    const t = c?.title || (c && c.depth === 0 ? 'эта ветка' : 'родитель');
    return `<span class="att-br${c && c.depth > 0 ? ' att-br--main' : ''}" title="${esc(t)}">${esc(t)}</span>`;
  }

  // ---------- кнопка в шапке ----------
  function ensureButton() {
    const ha = document.querySelector('.header-actions');
    if (!ha) return null;
    let b = document.getElementById('att-open-btn');
    if (!b) {
      b = document.createElement('button');
      b.type = 'button'; b.className = 'info-btn att-open-btn'; b.id = 'att-open-btn';
      b.title = 'Медиа и файлы чата'; b.setAttribute('aria-label', 'Медиа и файлы чата');
      b.setAttribute('aria-expanded', 'false'); b.innerHTML = ICON;
      b.addEventListener('click', () => { if (!b.disabled) (st.open ? close() : open()); });
      // Место бывшей кнопки «Помощь» (она переехала в шапку профиля): перед переключателем темы.
      ha.insertBefore(b, ha.querySelector('#theme-toggle'));
      watchViews();
    }
    syncButton(b);
    return b;
  }
  // Кнопка видна всегда, но работает только в разделе «Агент» с открытым чатом.
  // В Скиллах, Графе и т. п. — затушена; открытая панель закрывается.
  const agentVisible = () => { const v = document.getElementById('view-agent'); return !!v && !v.classList.contains('hidden'); };
  const available = () => !!st.chat && agentVisible();
  function syncButton(b = document.getElementById('att-open-btn')) {
    if (!b) return;
    const ok = available();
    b.disabled = !ok;
    b.setAttribute('aria-disabled', String(!ok));
    if (!ok && st.open) close();
  }
  let viewsMo = null;
  function watchViews() {
    if (viewsMo) return;
    const views = document.querySelectorAll('.view');
    if (!views.length) return;
    viewsMo = new MutationObserver(() => syncButton());
    views.forEach(v => viewsMo.observe(v, { attributes: true, attributeFilter: ['class'] }));
  }

  // ---------- каркас панели ----------
  function shell() {
    const p = document.createElement('aside');
    p.className = 'att-panel'; p.setAttribute('role', 'dialog'); p.setAttribute('aria-label', 'Медиа и файлы чата');
    p.innerHTML = `<div class="att-grip" aria-hidden="true"></div><div class="att-h"><span class="att-h__t">Вложения</span>
      <div class="mm-seg att-auth" role="group" aria-label="Автор">${AUTH.map(([k, t]) => `<button type="button" data-auth="${k}">${t}</button>`).join('')}</div>
      <button type="button" class="mm-ib att-x" aria-label="Закрыть">${X}</button></div>
      <div class="att-tabs" role="tablist">${TABS.map(([k, t]) => `<button type="button" role="tab" class="att-tab" data-tab="${k}">${t}<b></b></button>`).join('')}</div>
      <div class="att-b"><div class="att-viewsw" hidden><span class="att-meta att-viewsw__m"></span>
        <div class="mm-seg" role="group" aria-label="Вид"><button type="button" data-view="list">Список</button><button type="button" data-view="tree">Ветки</button></div></div>
        <div class="att-list"></div><div class="att-sent"></div></div>`;
    p.addEventListener('click', onClick);
    p.addEventListener('contextmenu', onMenu);
    p.addEventListener('keydown', e => {
      if ((e.key === 'Enter' || e.key === ' ') && e.target.matches('[data-k]')) { e.preventDefault(); activate(e.target); }
      if ((e.key === 'ContextMenu' || (e.shiftKey && e.key === 'F10')) && e.target.matches('[data-k]')) {
        e.preventDefault(); const r = e.target.getBoundingClientRect(); showMenu(e.target, r.left + 12, r.bottom - 4);
      }
    });
    bindLongPress(p);
    return p;
  }
  const $ = s => st.el?.querySelector(s);

  function syncChrome() {
    if (!st.el) return;
    st.el.querySelectorAll('[data-auth]').forEach(b => { const on = b.dataset.auth === st.author; b.classList.toggle('is-on', on); b.setAttribute('aria-pressed', on); });
    st.el.querySelectorAll('[data-tab]').forEach(b => {
      const on = b.dataset.tab === st.tab; b.classList.toggle('is-on', on); b.setAttribute('aria-selected', on);
      const n = st.counts ? st.counts[b.dataset.tab] : null;
      b.querySelector('b').textContent = n ? String(n) : '';
    });
    const sw = $('.att-viewsw');
    sw.hidden = st.tab !== 'files';
    sw.querySelectorAll('[data-view]').forEach(b => { const on = b.dataset.view === st.view; b.classList.toggle('is-on', on); b.setAttribute('aria-pressed', on); });
    const n = st.counts?.files || 0, br = st.chain.length;
    $('.att-viewsw__m').textContent = n ? `${n} ${plural(n, 'файл', 'файла', 'файлов')}${br > 1 ? ` · ${br} ${plural(br, 'ветка', 'ветки', 'веток')}` : ''}` : '';
  }

  // ---------- открытие / закрытие ----------
  function open() {
    if (!available()) return;
    if (!st.el) {
      st.el = shell();
      if (mobile()) {
        st.scrim = document.createElement('div'); st.scrim.className = 'att-scrim';
        st.scrim.addEventListener('click', close);
        st.el.classList.add('att-panel--sheet');
        document.body.append(st.scrim, st.el);
      } else {
        const va = document.getElementById('view-agent');
        if (!va) { st.el = null; return; }
        va.appendChild(st.el);
        va.classList.add('att-open');
      }
      requestAnimationFrame(() => requestAnimationFrame(() => { st.el?.classList.add('is-in'); st.scrim?.classList.add('is-in'); }));
    }
    st.open = true;
    const b = ensureButton(); b?.classList.add('is-on'); b?.setAttribute('aria-expanded', 'true');
    syncChrome();
    reload();
    setTimeout(() => $('.att-tab.is-on')?.focus({ preventScroll: true }), 30);
  }
  function close() {
    if (!st.el) return;
    st.open = false; st.seq++; st.cancelSk?.(); st.cancelSk = null; closeMenu();
    st.io?.disconnect(); st.io = null;
    const el = st.el, scrim = st.scrim;
    st.el = st.scrim = null;
    if (el.contains(document.activeElement)) document.getElementById('att-open-btn')?.focus({ preventScroll: true });
    document.getElementById('view-agent')?.classList.remove('att-open');
    const b = document.getElementById('att-open-btn'); b?.classList.remove('is-on'); b?.setAttribute('aria-expanded', 'false');
    // Превью файла, открытое из панели, закрываем вместе с ней.
    if (el.querySelector('.is-preview-open')) app()?.closeTextPreview?.();
    el.classList.remove('is-in'); scrim?.classList.remove('is-in');
    let gone = false;
    const rm = () => { if (gone) return; gone = true; el.remove(); scrim?.remove(); };
    if (window.matchMedia('(prefers-reduced-motion: reduce)').matches) rm();
    else { el.addEventListener('transitionend', rm, { once: true }); setTimeout(rm, 400); }
  }

  // ---------- загрузка ----------
  function reload() {
    if (!st.el) return;
    st.seq++; st.cancelSk?.(); st.cancelSk = null;
    st.items = []; st.cursor = null; st.done = false; st.last = null; st.loading = false; st.stale = false;
    const list = $('.att-list'); list.innerHTML = '';
    list.className = `att-list att-list--${st.tab}${st.tab === 'files' && st.view === 'tree' ? ' att-list--tree' : ''}`;
    $('.att-sent').innerHTML = '';
    $('.att-b').scrollTop = 0;
    st.io?.disconnect();
    st.io = new IntersectionObserver(es => { if (es.some(e => e.isIntersecting)) loadPage(false); },
      { root: $('.att-b'), rootMargin: '0px 0px 480px 0px' });
    st.io.observe($('.att-sent'));
    loadPage(true);
  }
  async function loadPage(first) {
    if (!st.el || st.loading || st.done || (!first && !st.cursor)) return;
    st.loading = true;
    const seq = st.seq, tab = st.tab;
    const skHost = first ? $('.att-list') : $('.att-sent');
    skHost.innerHTML = '';
    const cancel = window.Skeleton?.defer ? window.Skeleton.defer(skHost, skeleton(tab, first), 100) : () => {};
    st.cancelSk = () => { cancel(); skHost.querySelector('.sk-wrap')?.remove(); };
    const qs = new URLSearchParams({ tab, author: st.author, limit: PAGE[tab] });
    if (!first) qs.set('cursor', st.cursor);
    let data = null, status = 0;
    try {
      const r = await fetch(`${base()}/attachments?${qs}`, { credentials: 'include', redirect: 'manual' });
      status = r.status;
      if (window.__isAuthRedirectOrUnauthorized?.(r)) window.__onAuthSessionExpired?.();
      else if (r.ok) data = await r.json();
    } catch (_) { /* сеть */ }
    if (seq !== st.seq || !st.el) return;
    st.cancelSk(); st.cancelSk = null; st.loading = false;
    if (!data) {
      if (status === 404 && first) { st.done = true; st.counts = { media: 0, files: 0, links: 0 }; st.chain = []; syncChrome(); renderEmpty(); return; }
      skHost.innerHTML = '<div class="att-err">Не удалось загрузить <button type="button" class="att-retry">Повторить</button></div>';
      return;
    }
    if (first) { st.counts = data.counts || st.counts; st.chain = data.chain || []; syncChrome(); }
    const items = data.items || [];
    const from = st.items.length;
    st.items.push(...items);
    st.cursor = data.next_cursor || null;
    st.done = !st.cursor;
    if (!st.items.length) { renderEmpty(); return; }
    append(items, from);
    if (st.done) $('.att-sent').innerHTML = st.items.length > 12 ? '<div class="att-end">Это всё</div>' : '';
    // Сентинел всё ещё в зоне видимости (короткая страница) — наблюдатель повторно не сработает, догружаем сами.
    else requestAnimationFrame(() => {
      const b = $('.att-b'), s = $('.att-sent');
      if (b && s && s.getBoundingClientRect().top < b.getBoundingClientRect().bottom + 480) loadPage(false);
    });
  }
  function skeleton(tab, first) {
    const n = first ? (tab === 'media' ? 12 : 7) : (tab === 'media' ? 6 : 3);
    let s = first ? '<span class="sk att-sk-day"></span>' : '';
    if (tab === 'media') s += `<div class="att-mgrid">${Array.from({ length: n }, (_, i) => `<span class="sk att-sk-im" style="--i:${i % 6}"></span>`).join('')}</div>`;
    else for (let i = 0; i < n; i++) s += `<div class="att-sk-row"><span class="sk att-sk-ic" style="--i:${i}"></span><span class="att-sk-col"><span class="sk" style="width:${[64, 48, 72, 56][i % 4]}%;--i:${i}"></span><span class="sk" style="width:${[36, 42, 30][i % 3]}%;--i:${i}"></span></span></div>`;
    return `<div class="sk-wrap att-sk" role="status" aria-busy="true" aria-label="Загрузка">${s}</div>`;
  }
  function renderEmpty() {
    const t = { media: 'Картинок и видео', files: 'Файлов', links: 'Ссылок' }[st.tab];
    const w = { all: '', me: ' от вас', agent: ' от агента' }[st.author];
    $('.att-list').innerHTML = `<div class="att-empty">${ICON}<span>${t}${w} пока нет</span></div>`;
  }

  // ---------- отрисовка: новые страницы дописываются, показанное не перерисовывается ----------
  function append(items, from) {
    const list = $('.att-list');
    const frag = document.createDocumentFragment();
    items.forEach((it, k) => {
      const idx = from + k;
      if (st.tab === 'files' && st.view === 'tree') { treeAppend(frag, it, idx); return; }
      const dk = dayKey(it.created_at);
      if (!st.last || st.last.day !== dk) {
        const h = document.createElement('div'); h.className = 'att-day'; h.textContent = dayLabel(it.created_at);
        frag.appendChild(h);
        let box = null;
        if (st.tab === 'media') { box = document.createElement('div'); box.className = 'att-mgrid'; frag.appendChild(box); }
        st.last = { day: dk, box };
      }
      const el = st.tab === 'media' ? mediaTile(it, idx) : st.tab === 'files' ? fileRow(it, idx) : linkRow(it, idx);
      (st.last.box || frag).appendChild(el);
    });
    list.appendChild(frag);
  }
  function node(html, cls) { const d = document.createElement('div'); d.className = cls; d.innerHTML = html; return d; }
  function mediaTile(it, idx) {
    const d = node('', `att-im${it.inherited ? ' att-inh' : ''}`);
    d.dataset.k = 'media'; d.dataset.i = idx; d.tabIndex = 0;
    d.setAttribute('role', 'button'); d.setAttribute('aria-label', `${it.kind === 'video' ? 'Видео' : 'Картинка'} ${it.name}`);
    d.title = it.name;
    if (it.kind === 'video') d.insertAdjacentHTML('beforeend', PLAY);
    else {
      const img = new Image();
      img.alt = ''; img.loading = 'lazy'; img.decoding = 'async'; img.draggable = false;
      img.onload = () => d.classList.add('is-ld');
      img.onerror = () => {
        if (!img.dataset.fb) { img.dataset.fb = '1'; img.src = mediaItem(it).url; return; }
        d.classList.add('is-broken'); img.remove();
      };
      img.src = thumbUrl(it);
      d.prepend(img);
    }
    d.insertAdjacentHTML('beforeend', `<span class="att-badge">${av(it.author)}</span>`);
    return d;
  }
  function fileAttrs(el, it, idx) {
    el.dataset.k = 'file'; el.dataset.i = idx; el.tabIndex = 0; el.setAttribute('role', 'button');
    el.dataset.name = it.name; el.dataset.url = fileUrl(it); el.dataset.dlUrl = `${fileUrl(it)}?download=1`;
  }
  function fileRow(it, idx) {
    const e = it.ext || extOf(it.name);
    const meta = [fmtSize(it.size), who(it.author), hm(it.created_at)].filter(Boolean).map(esc).join(' · ');
    const d = node(`<span class="tp-ty att-tg__ic" data-c="${COLOR[e] || 'gray'}">${esc(lab(e))}</span>
      <div class="att-tg__t"><div class="att-nm" title="${esc(it.name)}">${esc(it.name)}</div><div class="att-meta">${meta}${branchTag(it)}</div></div>${DL}`,
    `att-tg${it.inherited ? ' att-inh' : ''}`);
    fileAttrs(d, it, idx);
    return d;
  }
  function linkRow(it, idx) {
    const h = host(it.url);
    const d = node(`<span class="att-tg__ic att-lnk__ic" aria-hidden="true">${esc((h[0] || '#').toUpperCase())}</span>
      <div class="att-tg__t"><div class="att-nm" title="${esc(it.title || h)}">${esc(it.title || h)}</div>
      <div class="att-meta att-lnk__u" title="${esc(it.url)}">${esc(it.url.replace(/^https?:\/\//, ''))}</div>
      <div class="att-meta">${esc(who(it.author))} · ${esc(hm(it.created_at))}${branchTag(it)}</div></div>`,
    `att-tg att-lnk${it.inherited ? ' att-inh' : ''}`);
    d.dataset.k = 'link'; d.dataset.i = idx; d.tabIndex = 0; d.setAttribute('role', 'link');
    return d;
  }

  // Дерево веток (новые сверху): узлы текущей ветки — на цветной линии форка,
  // при переходе к сообщениям предка — изгиб «ответвление от …», дальше основная линия, приглушённо.
  function treeAppend(frag, it, idx) {
    const c = chainOf(it.chat_id), depth = c ? c.depth : 0;
    const L = st.last;
    if (!L || L.msg !== it.msg_id) {
      if (L && L.depth !== depth) {
        const t = c?.title ? `ответвление от «${c.title}»` : 'ответвление от родительского чата';
        frag.appendChild(node(`<svg width="26" height="26" viewBox="0 0 26 26" aria-hidden="true"><path d="M23 0 C23 15, 4 10, 4 26" fill="none"/></svg><span>${esc(t)}</span>`, 'att-fork-in'));
      }
      const fk = forked() && depth === 0 ? ' att-node--fk' : '';
      const n = node(`<span class="att-dot"></span><div class="att-node__h">${av(it.author)}<b>${esc(who(it.author))}</b><time>${esc(dayLabel(it.created_at))}, ${esc(hm(it.created_at))}</time></div>`,
        `att-node att-node--${it.author === 'agent' ? 'agent' : 'user'}${fk}${depth > 0 ? ' att-inh' : ''}`);
      frag.appendChild(n);
      st.last = { msg: it.msg_id, depth, el: n };
    }
    const e = it.ext || extOf(it.name);
    const r = node(`<span class="tp-ty" data-c="${COLOR[e] || 'gray'}">${esc(lab(e))}</span><span class="att-nm" title="${esc(it.name)}">${esc(it.name)}</span><span class="att-sz">${esc(fmtSize(it.size))}</span>${DL}`, 'att-row');
    fileAttrs(r, it, idx);
    st.last.el.appendChild(r);
  }

  // ---------- действия ----------
  function setPref(key, val) { if (st[key] === val) return; st[key] = val; save(); syncChrome(); reload(); }
  function onClick(e) {
    const t = e.target;
    if (t.closest('.att-x')) { close(); return; }
    const a = t.closest('[data-auth]'); if (a) { setPref('author', a.dataset.auth); return; }
    const tb = t.closest('[data-tab]'); if (tb) { setPref('tab', tb.dataset.tab); return; }
    const v = t.closest('[data-view]'); if (v) { setPref('view', v.dataset.view); return; }
    if (t.closest('.att-retry')) { if (!st.items.length) reload(); else { $('.att-sent').innerHTML = ''; loadPage(false); } return; }
    const el = t.closest('[data-k]'); if (!el) return;
    if (t.closest('.att-dl')) { e.stopPropagation(); download(st.items[+el.dataset.i]); return; }
    activate(el);
  }
  function activate(el) {
    const it = st.items[+el.dataset.i]; if (!it) return;
    if (el.dataset.k === 'media') {
      const media = st.items.filter(x => x.kind === 'image' || x.kind === 'video');
      if (mobile()) close();
      if (app()?._openMediaLightbox) app()._openMediaLightbox(media.map(mediaItem), Math.max(0, media.indexOf(it)));
      else window.open(mediaItem(it).url, '_blank', 'noopener');
      return;
    }
    if (el.dataset.k === 'link') { openLink(it.url); return; }
    const FT = window.FileTiles;
    if (!FT?.previewTypeOf?.(it.name, '')) { download(it); return; }
    st.el.querySelectorAll('.is-preview-open').forEach(x => x !== el && x.classList.remove('is-preview-open'));
    // На мобиле превью во весь экран: шторку прячем, но оставляем в DOM (иначе FileTiles закроет превью
    // осиротевшей строки), после закрытия превью шторка возвращается.
    if (mobile()) { st.el.classList.add('is-under-pv'); st.scrim?.classList.add('is-under-pv'); }
    FT.openFilePreview(el);
  }
  function openLink(url) { if (/^https?:\/\//i.test(url)) window.open(url, '_blank', 'noopener,noreferrer'); }
  function download(it) {
    if (!it || !it.name) return;
    const a = document.createElement('a');
    a.href = it.kind === 'image' ? mediaItem(it).dlUrl : `${fileUrl(it)}?download=1`;
    a.download = it.name; a.rel = 'noopener noreferrer';
    document.body.appendChild(a); a.click(); a.remove();
  }
  // Переход к сообщению той же логикой, что в поиске: сообщение предка открывается в текущем чате
  // (история форка подтягивает его по цепочке).
  function goTo(it) {
    if (!it) return;
    const a = app();
    if (mobile()) close();
    const dbId = it.msg_id != null ? String(it.msg_id) : null;
    if (a?.navigateToSearchMessage) a.navigateToSearchMessage(st.chat, { messageId: it.message_id || null, dbId });
  }

  // ---------- контекстное меню ----------
  let menu = null;
  function closeMenu() { menu?.remove(); menu = null; document.removeEventListener('pointerdown', outside, true); }
  function outside(e) { if (menu && !menu.contains(e.target)) closeMenu(); }
  function onMenu(e) {
    const el = e.target.closest('[data-k]'); if (!el) return;
    e.preventDefault();
    showMenu(el, e.clientX, e.clientY);
  }
  function showMenu(el, x, y) {
    const it = st.items[+el.dataset.i]; if (!it) return;
    closeMenu();
    const acts = [['go', 'Перейти к сообщению']];
    if (el.dataset.k === 'link') acts.push(['open', 'Открыть ссылку'], ['copy', 'Копировать ссылку']);
    else acts.push(['dl', 'Скачать']);
    menu = document.createElement('div');
    menu.className = 'att-menu'; menu.setAttribute('role', 'menu');
    menu.innerHTML = acts.map(([k, t]) => `<button type="button" role="menuitem" data-act="${k}">${t}</button>`).join('');
    document.body.appendChild(menu);
    const r = menu.getBoundingClientRect();
    menu.style.left = `${Math.max(8, Math.min(x, innerWidth - r.width - 8))}px`;
    menu.style.top = `${Math.max(8, Math.min(y, innerHeight - r.height - 8))}px`;
    menu.addEventListener('click', ev => {
      const b = ev.target.closest('[data-act]'); if (!b) return;
      const act = b.dataset.act; closeMenu();
      if (act === 'go') goTo(it);
      else if (act === 'dl') download(it);
      else if (act === 'open') openLink(it.url);
      else if (act === 'copy') navigator.clipboard?.writeText(it.url).catch(() => {});
    });
    menu.addEventListener('keydown', ev => {
      const bs = [...menu.querySelectorAll('button')]; const i = bs.indexOf(document.activeElement);
      if (ev.key === 'ArrowDown') { ev.preventDefault(); bs[(i + 1) % bs.length].focus(); }
      if (ev.key === 'ArrowUp') { ev.preventDefault(); bs[(i - 1 + bs.length) % bs.length].focus(); }
    });
    setTimeout(() => document.addEventListener('pointerdown', outside, true), 0);
    menu.querySelector('button').focus({ preventScroll: true });
  }
  // Долгое нажатие на тач-экране = ПКМ (iOS Safari не шлёт contextmenu).
  function bindLongPress(p) {
    let t = null, x = 0, y = 0, fired = false;
    p.addEventListener('pointerdown', e => {
      if (e.pointerType !== 'touch') return;
      const el = e.target.closest('[data-k]'); if (!el) return;
      x = e.clientX; y = e.clientY; fired = false;
      t = setTimeout(() => { fired = true; showMenu(el, x, y); }, 520);
    });
    const stop = () => { clearTimeout(t); t = null; };
    p.addEventListener('pointermove', e => { if (t && Math.hypot(e.clientX - x, e.clientY - y) > 10) stop(); });
    p.addEventListener('pointerup', stop); p.addEventListener('pointercancel', stop);
    p.addEventListener('click', e => { if (fired) { e.stopPropagation(); e.preventDefault(); fired = false; } }, true);
  }

  document.addEventListener('keydown', e => {
    if (e.key !== 'Escape') return;
    if (menu) { closeMenu(); e.stopPropagation(); return; }
    if (!st.open || document.getElementById('image-lightbox')) return;
    if (document.getElementById('text-preview-panel')?.classList.contains('open')) return;
    close();
  });
  document.addEventListener('filepreview:state', e => {
    if (e.detail?.phase !== 'exit') return;
    st.el?.querySelectorAll('.is-preview-open').forEach(x => x.classList.remove('is-preview-open'));
    st.el?.classList.remove('is-under-pv'); st.scrim?.classList.remove('is-under-pv');
  });
  window.addEventListener('resize', () => {
    // Переход между шторкой и боковой панелью — пересобрать в нужном месте.
    if (st.open && st.el && st.el.classList.contains('att-panel--sheet') !== mobile()) { close(); setTimeout(open, 0); }
  });

  window.AttachmentsPanel = {
    // Чат открыт / сменился (вызывается рядом с MemoryPanel.mount).
    mount(chatId) {
      const id = chatId ? String(chatId) : null;
      if (id === st.chat) { ensureButton(); return; }
      st.chat = id; st.counts = null; st.chain = [];
      ensureButton();
      if (!id) { close(); return; }
      if (st.open) reload();
    },
    unmount() { this.mount(null); },
    // Финал ответа агента: в чате могли появиться файлы. Не дёргаем, если пользователь листает или смотрит превью.
    refresh(chatId) {
      if (chatId != null && String(chatId) !== String(st.chat)) return;
      if (!st.open || !st.el) return;
      if (st.el.querySelector('.is-preview-open') || $('.att-b').scrollTop > 40) { st.stale = true; return; }
      reload();
    },
    open, close,
    isOpen: () => st.open,
  };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ensureButton); else ensureButton();
})();
