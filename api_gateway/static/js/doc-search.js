/* doc-search.js — поиск по файлу в правой панели превью (#text-preview-panel).
 * Работает во всех видах панели: превью (md/код/таблица/текст), «Исходник» и режим правки (doc-edit.js).
 * Подсветка через CSS Custom Highlight API: DOM не трогаем, поэтому правки и contenteditable не ломаются.
 * Без Highlight API текущее совпадение всё равно прокручивается в видимую область и показывается счётчик.
 */
(function () {
    'use strict';

    const $ = (id) => document.getElementById(id);
    const HL_ALL = 'docsearch';
    const HL_CUR = 'docsearch-cur';
    const MAX_HITS = 5000;
    const HAS_HL = typeof CSS !== 'undefined' && CSS.highlights && typeof Highlight === 'function';
    const BLOCK = 'p,li,h1,h2,h3,h4,h5,h6,pre,td,th,dt,dd,blockquote,figcaption,caption,.de-row,.de-txt';
    const SKIP = '.de-num,.de-undo,.de-selbtn,.de-cmt,.de-bar,.de-hint,.ds-bar,script,style,textarea,button,[hidden],[aria-hidden="true"]';

    const IC = {
        search: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true"><circle cx="11" cy="11" r="6.5" stroke="currentColor" stroke-width="2"/><path d="M16 16l4.5 4.5" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
        up: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true"><path d="M6 15l6-6 6 6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        down: '<svg viewBox="0 0 24 24" width="15" height="15" fill="none" aria-hidden="true"><path d="M6 9l6 6 6-6" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        close: '<svg viewBox="0 0 20 20" width="14" height="14" fill="none" aria-hidden="true"><path d="M5 5l10 10M15 5L5 15" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
    };

    const S = { open: false, q: '', cs: false, hits: [], cur: -1, mo: null, t: 0 };
    let btn = null, bar = null, inp = null, cnt = null, prevB = null, nextB = null, caseB = null;

    function panel() { return $('text-preview-panel'); }
    function panelOpen() { const p = panel(); return !!(p && p.classList.contains('open')); }

    /* Видимый контейнер с текстом: render (превью / режим правки) или body («Исходник» / текстовый чип). */
    function target() {
        const r = $('text-preview-render'), b = $('text-preview-body');
        if (r && !r.hidden) return r;
        if (b && !b.hidden) return b;
        return null;
    }
    function isFrame(el) { return !!(el && el.classList.contains('text-preview-render--frame')); }

    /* ---------- UI ---------- */
    function ensureBtn() {
        if (btn && btn.isConnected) return btn;
        const head = document.querySelector('#text-preview-panel .text-preview-header');
        if (!head) return null;
        btn = document.createElement('button');
        btn.type = 'button';
        btn.id = 'text-preview-search';
        btn.className = 'glass-mode-btn tp-search-btn';
        btn.title = 'Поиск по файлу (Ctrl+F)';
        btn.setAttribute('aria-label', 'Поиск по файлу');
        btn.setAttribute('aria-pressed', 'false');
        btn.innerHTML = IC.search;
        const before = $('text-preview-edit') || $('text-preview-dl') || $('text-preview-close');
        head.insertBefore(btn, before);
        btn.addEventListener('click', (e) => { e.preventDefault(); S.open ? close() : open(); });
        return btn;
    }

    function ensureBar() {
        if (bar && bar.isConnected) return bar;
        const wrap = document.querySelector('#text-preview-panel .text-preview-wrap');
        if (!wrap) return null;
        bar = document.createElement('div');
        bar.className = 'ds-bar';
        bar.hidden = true;
        bar.setAttribute('role', 'search');
        bar.innerHTML =
            `<label class="ds-field"><span class="ds-field__ic">${IC.search}</span>` +
            `<input class="ds-input" type="search" placeholder="Найти в файле" autocomplete="off" spellcheck="false" enterkeyhint="search" aria-label="Найти в файле"></label>` +
            `<span class="ds-count" aria-live="polite"></span>` +
            `<button type="button" class="ds-btn ds-case" title="Учитывать регистр" aria-label="Учитывать регистр" aria-pressed="false">Aa</button>` +
            `<button type="button" class="ds-btn ds-prev" title="Предыдущее (Shift+Enter)" aria-label="Предыдущее совпадение">${IC.up}</button>` +
            `<button type="button" class="ds-btn ds-next" title="Следующее (Enter)" aria-label="Следующее совпадение">${IC.down}</button>` +
            `<button type="button" class="ds-btn ds-close" title="Закрыть поиск (Esc)" aria-label="Закрыть поиск">${IC.close}</button>`;
        wrap.insertBefore(bar, wrap.firstChild);
        inp = bar.querySelector('.ds-input');
        cnt = bar.querySelector('.ds-count');
        prevB = bar.querySelector('.ds-prev');
        nextB = bar.querySelector('.ds-next');
        caseB = bar.querySelector('.ds-case');
        inp.addEventListener('input', () => { S.q = inp.value; run(true); });
        inp.addEventListener('keydown', (e) => {
            if (e.key === 'Enter') { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
            else if (e.key === 'Escape') { e.preventDefault(); e.stopPropagation(); close(); }
        });
        prevB.addEventListener('click', () => step(-1));
        nextB.addEventListener('click', () => step(1));
        caseB.addEventListener('click', () => {
            S.cs = !S.cs;
            caseB.classList.toggle('is-active', S.cs);
            caseB.setAttribute('aria-pressed', String(S.cs));
            run(true);
            inp.focus();
        });
        bar.querySelector('.ds-close').addEventListener('click', () => close());
        // Кнопки бара не забирают фокус у поля поиска.
        bar.addEventListener('mousedown', (e) => { if (e.target.closest('.ds-btn')) e.preventDefault(); });
        return bar;
    }

    function syncBtn() {
        const b = ensureBtn();
        if (!b) return;
        b.hidden = !panelOpen() || !target();
        b.classList.toggle('is-active', S.open);
        b.setAttribute('aria-pressed', String(S.open));
    }

    function open(seed) {
        if (!panelOpen()) return;
        ensureBtn(); ensureBar();
        if (!bar) return;
        S.open = true;
        bar.hidden = false;
        panel().classList.add('is-searching');
        if (typeof seed === 'string' && seed.trim() && seed.length <= 200 && !seed.includes('\n')) {
            inp.value = seed; S.q = seed;
        }
        observe();
        run(true);
        syncBtn();
        inp.focus({ preventScroll: true });
        inp.select();
    }

    function close() {
        S.open = false;
        if (bar) bar.hidden = true;
        const p = panel();
        if (p) p.classList.remove('is-searching');
        clearHL();
        S.hits = []; S.cur = -1;
        if (S.mo) { S.mo.disconnect(); S.mo = null; }
        syncBtn();
    }

    /* ---------- Поиск ---------- */
    function collect(root) {
        const nodes = [];      // {node, start}
        let text = '';
        let lastBlock = null;
        const hidden = new WeakMap();
        const isHidden = (el) => {
            if (!el) return false;
            if (hidden.has(el)) return hidden.get(el);
            const h = !!el.closest(SKIP) || el.getClientRects().length === 0;
            hidden.set(el, h);
            return h;
        };
        const w = document.createTreeWalker(root, NodeFilter.SHOW_TEXT, {
            acceptNode(n) {
                if (!n.nodeValue) return NodeFilter.FILTER_REJECT;
                return isHidden(n.parentElement) ? NodeFilter.FILTER_REJECT : NodeFilter.FILTER_ACCEPT;
            },
        });
        for (let n = w.nextNode(); n; n = w.nextNode()) {
            const blk = n.parentElement.closest(BLOCK) || root;
            if (lastBlock && blk !== lastBlock) text += '\n';   // граница блоков: совпадение не склеивает соседние строки
            lastBlock = blk;
            nodes.push({ node: n, start: text.length });
            text += n.nodeValue;
        }
        return { text, nodes };
    }

    function locate(nodes, pos, isEnd) {
        let lo = 0, hi = nodes.length - 1;
        const p = pos - (isEnd ? 1 : 0);
        while (lo < hi) {
            const mid = (lo + hi + 1) >> 1;
            if (nodes[mid].start <= p) lo = mid; else hi = mid - 1;
        }
        const it = nodes[lo];
        return { node: it.node, offset: Math.min(pos - it.start, it.node.nodeValue.length) };
    }

    function rectOf(r) {
        const rs = r.getClientRects();
        return rs.length ? rs[0] : r.getBoundingClientRect();
    }

    function run(resetPos) {
        clearHL();
        if (!S.open) return;
        const root = target();
        const q = S.q;
        if (!root) { S.hits = []; S.cur = -1; return paint(''); }
        if (isFrame(root)) { S.hits = []; S.cur = -1; return paint('В HTML-превью поиск недоступен, откройте «Исходник»', true); }
        if (!q) { S.hits = []; S.cur = -1; return paint(''); }
        const prevRange = !resetPos && S.hits[S.cur];
        const prevTop = prevRange ? rectOf(prevRange).top : null;
        const { text, nodes } = collect(root);
        const hay = S.cs ? text : text.toLocaleLowerCase();
        const needle = S.cs ? q : q.toLocaleLowerCase();
        const hits = [];
        if (nodes.length && needle) {
            let i = hay.indexOf(needle);
            while (i !== -1 && hits.length < MAX_HITS) {
                const a = locate(nodes, i, false), b = locate(nodes, i + needle.length, true);
                const r = document.createRange();
                try { r.setStart(a.node, a.offset); r.setEnd(b.node, b.offset); hits.push(r); } catch (_) {}
                i = hay.indexOf(needle, i + needle.length);
            }
        }
        S.hits = hits;
        if (!hits.length) { S.cur = -1; return paint('Нет совпадений', true); }
        if (resetPos || prevTop == null) {
            // первое совпадение не выше текущей видимой области
            const top = root.getBoundingClientRect().top;
            const k = hits.findIndex((r) => rectOf(r).bottom >= top + 4);
            S.cur = k === -1 ? 0 : k;
        } else {
            // после правки текста держимся совпадения, ближайшего к прежнему месту
            let best = 0, bd = Infinity;
            hits.forEach((r, k) => { const d = Math.abs(rectOf(r).top - prevTop); if (d < bd) { bd = d; best = k; } });
            S.cur = best;
        }
        paint();
        if (resetPos) reveal();
    }

    function step(d) {
        if (!S.hits.length) { if (S.q) run(true); return; }
        S.cur = (S.cur + d + S.hits.length) % S.hits.length;
        paint();
        reveal();
    }

    function scrollerX(el, root) {
        for (let e = el; e && e !== root.parentElement; e = e.parentElement) {
            if (e.scrollWidth > e.clientWidth + 1) {
                const ox = getComputedStyle(e).overflowX;
                if (ox === 'auto' || ox === 'scroll') return e;
            }
        }
        return null;
    }

    function reveal() {
        const r = S.hits[S.cur];
        const root = target();
        if (!r || !root) return;
        const rr = rectOf(r), cr = root.getBoundingClientRect();
        if (rr.top < cr.top + 8 || rr.bottom > cr.bottom - 8) {
            root.scrollTop += rr.top - cr.top - Math.round(root.clientHeight / 3);
        }
        const sx = scrollerX(r.startContainer.parentElement, root);
        if (sx) {
            const xr = sx.getBoundingClientRect(), r2 = rectOf(r);
            if (r2.left < xr.left + 8 || r2.right > xr.right - 8) sx.scrollLeft += r2.left - xr.left - Math.round(sx.clientWidth / 3);
        }
    }

    function clearHL() {
        if (!HAS_HL) return;
        CSS.highlights.delete(HL_ALL);
        CSS.highlights.delete(HL_CUR);
    }

    function paint(msg, warn) {
        if (!bar) return;
        const n = S.hits.length;
        if (HAS_HL) {
            clearHL();
            if (n) {
                const all = new Highlight(...S.hits.filter((_, k) => k !== S.cur));
                all.priority = 1;
                CSS.highlights.set(HL_ALL, all);
                const cur = new Highlight(S.hits[S.cur]);
                cur.priority = 2;
                CSS.highlights.set(HL_CUR, cur);
            }
        }
        if (msg) cnt.textContent = msg;
        else if (n) cnt.textContent = `${S.cur + 1} из ${n >= MAX_HITS ? MAX_HITS + '+' : n}`;
        else cnt.textContent = '';
        cnt.classList.toggle('is-warn', !!warn && !!S.q);
        cnt.classList.toggle('is-long', !!msg && msg.length > 16);
        prevB.disabled = nextB.disabled = n === 0;
    }

    /* Пересчёт при правках текста, смене вида (превью / исходник / правка) и перерисовке панели. */
    function observe() {
        if (S.mo) return;
        const wrap = document.querySelector('#text-preview-panel .text-preview-wrap');
        if (!wrap) return;
        S.mo = new MutationObserver((list) => {
            if (list.every((m) => bar && bar.contains(m.target))) return;
            clearTimeout(S.t);
            S.t = setTimeout(() => { if (S.open) run(false); syncBtn(); }, 160);
        });
        S.mo.observe(wrap, { childList: true, subtree: true, characterData: true, attributes: true, attributeFilter: ['hidden', 'class'] });
    }

    /* ---------- События ---------- */
    document.addEventListener('filepreview:state', () => {
        setTimeout(() => {
            if (!panelOpen()) close();
            else if (S.open) run(true);
            syncBtn();
        }, 0);
    });

    document.addEventListener('keydown', (e) => {
        if (!panelOpen()) return;
        const mod = e.ctrlKey || e.metaKey;
        const k = (e.key || '').toLowerCase();
        if (mod && !e.altKey && !e.shiftKey && (k === 'f' || k === 'а')) {
            const a = document.activeElement;
            const inChatInput = a && a.closest && a.closest('.message-input-container');
            if (inChatInput) return;
            e.preventDefault();
            open(String(getSelection() || ''));
            return;
        }
        if (!S.open) return;
        if (e.key === 'F3' || (mod && k === 'g')) { e.preventDefault(); step(e.shiftKey ? -1 : 1); }
    }, true);

    // Крестик панели снимает класс open без события: следим за классом панели.
    function watchPanel() {
        const p = panel();
        if (!p) return;
        new MutationObserver(() => {
            if (!panelOpen() && S.open) close();
            syncBtn();
        }).observe(p, { attributes: true, attributeFilter: ['class'] });
        ensureBtn();
        syncBtn();
    }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', watchPanel);
    else watchPanel();

    window.DocSearch = { open, close, step, state: () => ({ open: S.open, q: S.q, n: S.hits.length, cur: S.cur }) };
})();
