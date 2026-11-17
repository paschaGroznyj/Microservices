/* doc-edit.js — режим правки документа в правой панели превью (#text-preview-panel).
 *
 * Кнопка «Править» в шапке панели включает построчный редактор для файлов из сообщений
 * (txt/md/json/csv/код). Правки строк и комментарии к строкам и фрагментам копятся на фронте
 * (sessionStorage, по чату и файлу). «Отправить агенту» собирает текстовый промпт и
 * JSON document_edit_request (как image/video_edit_request в media-pro.js) и отправляет
 * его в чат через app.sendMessage. Длинный JSON json_pack.py превращает во вложение.
 * Состояние панели берётся из FileTiles (событие filepreview:state, currentFile()).
 */
(function () {
    'use strict';

    const MAX_LINES = 4000;
    const STORE_KEY = 'docedit:v1';
    const MONO_TYPES = new Set(['code', 'json', 'csv', 'html']);
    const IC = {
        pen: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true"><path d="M10.6 2.6l2.8 2.8M2.5 13.5l.6-3.1 7.9-7.9a1.4 1.4 0 012 0l.5.5a1.4 1.4 0 010 2l-7.9 7.9-3.1.6z" stroke="currentColor" stroke-width="1.4" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        cmt: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="M3 3.5h10a1 1 0 011 1v6a1 1 0 01-1 1H7l-3 2.5v-2.5H3a1 1 0 01-1-1v-6a1 1 0 011-1z" stroke="currentColor" stroke-width="1.4" stroke-linejoin="round"/></svg>',
        x: '<svg viewBox="0 0 14 14" width="12" height="12" fill="none" aria-hidden="true"><path d="M3.5 3.5l7 7M10.5 3.5l-7 7" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
        send: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none" aria-hidden="true"><path d="M8 13V3M3.5 7.5L8 3l4.5 4.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        check: '<svg viewBox="0 0 16 16" width="15" height="15" fill="none" aria-hidden="true"><path d="M3.5 8.5l3 3 6-7" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        undo: '<svg viewBox="0 0 16 16" width="14" height="14" fill="none" aria-hidden="true"><path d="M3 6.5h6.5a3.5 3.5 0 010 7H6M3 6.5L6 3.5M3 6.5l3 3" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    };

    const $ = (id) => document.getElementById(id);
    const app = () => window.__catchpmApp || null;
    const toast = (t) => { const a = app(); if (a && typeof a.showToast === 'function') a.showToast(t); else console.info('[doc-edit]', t); };
    const plural = (n, one, few, many) => {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return one;
        if (m10 >= 2 && m10 <= 4 && (m100 < 10 || m100 >= 20)) return few;
        return many;
    };

    /* url плитки → { chat, message_id, name } (как parseUrl в media-pro.js) */
    function parseUrl(url) {
        const m = String(url || '').match(/\/api\/chats\/([^/]+)\/(?:image|files)\/([^/?#]+)\/([^/?#]+)/);
        if (!m) return null;
        const dec = (x) => { try { return decodeURIComponent(x); } catch (_) { return x; } };
        return { chat: dec(m[1]), message_id: dec(m[2]), name: dec(m[3]) };
    }
    function hashText(t) {
        let h = 2166136261 >>> 0;
        for (let i = 0; i < t.length; i++) { h ^= t.charCodeAt(i); h = Math.imul(h, 16777619) >>> 0; }
        return h.toString(16) + ':' + t.length;
    }
    function gatewayName() {
        return (document.body && document.body.dataset.gateway) === 'sigma' ? 'Sigma' : 'CatchPM';
    }
    function chatId() {
        const a = app();
        return (a && a.currentChat != null) ? String(a.currentChat) : '';
    }

    /* ── Хранилище правок ─────────────────────────────────────── */
    // DOCS[key] = { chat, name, message_id, url, hash, lines,
    //               edits: {n: новый текст}, before: {n: исходный текст},
    //               comments: [{id, from, to, quote, src, text}] }
    let DOCS = {};
    let NOTE = {};   // общий комментарий по чату
    try {
        const raw = JSON.parse(sessionStorage.getItem(STORE_KEY) || '{}');
        DOCS = raw.docs || {}; NOTE = raw.note || {};
    } catch (_) {}
    function save() {
        try { sessionStorage.setItem(STORE_KEY, JSON.stringify({ docs: DOCS, note: NOTE })); } catch (_) {}
    }
    function docCount(d) {
        return Object.keys(d.edits || {}).length + (d.comments || []).filter((c) => c.text.trim()).length;
    }
    function chatDocs(chat) {
        return Object.values(DOCS).filter((d) => d.chat === chat && docCount(d) > 0);
    }

    /* ── Состояние редактора ──────────────────────────────────── */
    const S = { on: false, key: '', doc: null, orig: [], file: null };
    let btn = null, bar = null, selBtn = null;

    function editable(f) {
        if (!f || f.isLocal || !f.loaded || f.truncated) return false;
        return !!parseUrl(f.url || f.dlUrl);
    }

    function ensureHeaderBtn() {
        if (btn && btn.isConnected) return btn;
        const head = document.querySelector('#text-preview-panel .text-preview-header');
        if (!head) return null;
        btn = document.createElement('button');
        btn.type = 'button';
        btn.id = 'text-preview-edit';
        btn.className = 'glass-mode-btn tp-edit-btn';
        btn.hidden = true;
        btn.setAttribute('aria-pressed', 'false');
        btn.innerHTML = `<span class="tp-edit-btn__ic">${IC.pen}</span><span class="tp-edit-btn__lbl">Править</span><b class="tp-edit-btn__n" hidden></b>`;
        head.insertBefore(btn, $('text-preview-dl') || $('text-preview-close'));
        btn.addEventListener('click', (e) => { e.preventDefault(); toggle(); });
        return btn;
    }

    function syncBtn() {
        const b = ensureHeaderBtn();
        if (!b) return;
        const f = window.FileTiles?.currentFile?.();
        b.hidden = !editable(f);
        b.classList.toggle('is-active', S.on);
        b.setAttribute('aria-pressed', S.on ? 'true' : 'false');
        const t = S.on ? 'Выйти из режима правки' : 'Править: изменить строки и оставить комментарии для агента';
        b.title = t; b.setAttribute('aria-label', t);
        b.querySelector('.tp-edit-btn__lbl').textContent = S.on ? 'Готово' : 'Править';
        const ic = b.querySelector('.tp-edit-btn__ic');
        if (ic.dataset.on !== String(S.on)) { ic.innerHTML = S.on ? IC.check : IC.pen; ic.dataset.on = String(S.on); }
        const n = chatDocs(chatId()).reduce((s, d) => s + docCount(d), 0);
        const badge = b.querySelector('.tp-edit-btn__n');
        badge.hidden = !n || S.on;
        badge.textContent = String(n);
    }

    function keyFor(f) {
        const m = parseUrl(f.url || f.dlUrl) || {};
        return [m.chat || chatId(), m.message_id || '', f.name].join('::');
    }

    function toggle(force) {
        const next = typeof force === 'boolean' ? force : !S.on;
        if (next === S.on) return;
        if (next) enter(); else leave();
    }

    function enter() {
        const f = window.FileTiles?.currentFile?.();
        if (!editable(f)) return;
        const lines = f.text.split('\n');
        if (lines.length > MAX_LINES) {
            toast(`Файл длиннее ${MAX_LINES} строк, режим правки недоступен. Опишите правки в сообщении.`);
            return;
        }
        const m = parseUrl(f.url || f.dlUrl) || {};
        const key = keyFor(f);
        const hash = hashText(f.text);
        let d = DOCS[key];
        if (!d || d.hash !== hash) {
            d = DOCS[key] = {
                chat: m.chat || chatId(), name: f.name, message_id: m.message_id || null,
                url: f.url || f.dlUrl, hash, lines: lines.length, edits: {}, before: {}, comments: [],
            };
        }
        d.before = d.before || {};
        S.on = true; S.key = key; S.doc = d; S.orig = lines; S.file = f;
        $('text-preview-panel').classList.add('text-preview-panel--editing');
        const modes = $('text-preview-modes');
        if (modes) { modes.dataset.deHidden = modes.hidden ? '0' : '1'; modes.hidden = true; }
        renderEditor();
        ensureBar();
        syncBar();
    }

    function leave(opts = {}) {
        if (!S.on) return;
        flushFocused();
        S.on = false;
        const panel = $('text-preview-panel');
        if (panel) panel.classList.remove('text-preview-panel--editing');
        hideSel();
        if (bar) bar.hidden = true;
        const modes = $('text-preview-modes');
        if (modes && modes.dataset.deHidden === '1') modes.hidden = false;
        if (modes) delete modes.dataset.deHidden;
        const render = $('text-preview-render');
        if (render) render.classList.remove('text-preview-render--edit', 'is-mono');
        S.doc = null; S.orig = []; S.file = null; S.key = '';
        save();
        if (!opts.silent) window.FileTiles?.renderFilePreview?.();
        syncBtn();
    }

    /* ── Построчный редактор ──────────────────────────────────── */
    function supportsPlain() {
        const d = document.createElement('div');
        try { d.contentEditable = 'plaintext-only'; } catch (_) { return false; }
        return d.contentEditable === 'plaintext-only';
    }
    const PLAIN = supportsPlain();

    function renderEditor() {
        const body = $('text-preview-body'), render = $('text-preview-render');
        if (!render) return;
        if (body) { body.hidden = true; body.textContent = ''; }
        const mono = MONO_TYPES.has(S.file.type);
        render.className = 'text-preview-body text-preview-render text-preview-render--edit' + (mono ? ' is-mono' : '');
        render.hidden = false;
        render.innerHTML = '';
        const hint = document.createElement('div');
        hint.className = 'de-hint';
        hint.textContent = 'Правьте текст прямо в строках. Номер строки — комментарий к\u00a0ней, выделение текста — комментарий к\u00a0фрагменту.';
        render.appendChild(hint);
        const doc = document.createElement('div');
        doc.className = 'de-doc';
        doc.style.setProperty('--de-gw', `calc(${String(S.orig.length).length}ch + 22px)`);
        const frag = document.createDocumentFragment();
        S.orig.forEach((line, i) => frag.appendChild(rowEl(i + 1, line)));
        doc.appendChild(frag);
        render.appendChild(doc);
        (S.doc.comments || []).forEach((c) => placeComment(c, false));
        clearSelMarks();
        render.scrollTop = 0;
        const note = $('text-preview-note');
        if (note) { note.hidden = true; note.textContent = ''; }
    }

    function rowEl(n, orig) {
        const row = document.createElement('div');
        row.className = 'de-row';
        row.dataset.line = String(n);
        const num = document.createElement('button');
        num.type = 'button';
        num.className = 'de-num';
        num.textContent = String(n);
        num.title = `Комментарий к строке ${n} (Shift — диапазон)`;
        num.setAttribute('aria-label', `Комментарий к строке ${n}`);
        const txt = document.createElement('div');
        txt.className = 'de-txt';
        txt.tabIndex = 0;
        txt.spellcheck = false;
        txt.dataset.orig = orig;
        const has = Object.prototype.hasOwnProperty.call(S.doc.edits, n);
        txt.textContent = has ? S.doc.edits[n] : orig;
        row.append(num, txt);
        markRow(row, n);
        return row;
    }

    function markRow(row, n) {
        const has = Object.prototype.hasOwnProperty.call(S.doc.edits, n);
        const del = has && S.doc.edits[n] === '' && S.orig[n - 1] !== '';
        row.classList.toggle('is-changed', has && !del);
        row.classList.toggle('is-deleted', del);
        row.classList.toggle('has-cmt', (S.doc.comments || []).some((c) => c.text.trim() && n >= c.from && n <= c.to));
        // Подряд идущие изменённые строки — одна правка (как в счётчике и в JSON):
        // кнопка отката только у первой строки блока и откатывает весь блок.
        const head = has && !Object.prototype.hasOwnProperty.call(S.doc.edits, n - 1);
        row.classList.toggle('is-run', has && Object.prototype.hasOwnProperty.call(S.doc.edits, n + 1));
        let undo = row.querySelector('.de-undo');
        if (head && !undo) {
            undo = document.createElement('button');
            undo.type = 'button';
            undo.className = 'de-undo';
            undo.title = 'Вернуть исходную строку';
            undo.setAttribute('aria-label', `Вернуть исходную строку ${n}`);
            undo.innerHTML = IC.undo;
            row.appendChild(undo);
        } else if (!head && undo) undo.remove();
    }
    function markNear(n) {
        const doc = docEl();
        if (!doc) return;
        let a = n, b = n;
        while (a > 1 && Object.prototype.hasOwnProperty.call(S.doc.edits, a - 1)) a--;
        while (Object.prototype.hasOwnProperty.call(S.doc.edits, b + 1)) b++;
        for (let k = Math.max(1, a - 1); k <= b + 1; k++) {
            const r = doc.querySelector(`.de-row[data-line="${k}"]`);
            if (r) markRow(r, k);
        }
    }

    // Строка становится редактируемой по клику/фокусу и обратно при уходе фокуса.
    // Если все строки сразу contenteditable, браузер не даёт выделить текст через несколько строк.
    function activate(txt, x, y) {
        if (txt.isContentEditable) return;
        txt.contentEditable = PLAIN ? 'plaintext-only' : 'true';
        txt.focus({ preventScroll: true });
        let r = null;
        if (x != null) {
            if (document.caretRangeFromPoint) r = document.caretRangeFromPoint(x, y);
            else if (document.caretPositionFromPoint) {
                const cp = document.caretPositionFromPoint(x, y);
                if (cp) { r = document.createRange(); r.setStart(cp.offsetNode, cp.offset); }
            }
        }
        if (!r || !txt.contains(r.startContainer)) { r = document.createRange(); r.selectNodeContents(txt); r.collapse(false); }
        else r.collapse(true);
        const sel = window.getSelection();
        sel.removeAllRanges(); sel.addRange(r);
    }
    function deactivate(txt) { txt.removeAttribute('contenteditable'); }

    function readRow(txt) {
        const v = PLAIN ? txt.textContent : (txt.innerText ?? txt.textContent);
        return String(v).replace(/\r\n?/g, '\n').replace(/\n$/, '');
    }
    function commitRow(txt) {
        const row = txt.closest('.de-row');
        if (!row || !S.doc) return;
        const n = Number(row.dataset.line);
        const v = readRow(txt);
        if (v === S.orig[n - 1]) { delete S.doc.edits[n]; delete S.doc.before[n]; }
        else { S.doc.edits[n] = v; S.doc.before[n] = S.orig[n - 1]; }
        markNear(n);
        save(); syncBar();
    }
    function flushFocused() {
        const a = document.activeElement;
        if (!a || !a.classList) return;
        if (a.classList.contains('de-txt')) commitRow(a);
        else if (a.classList.contains('de-cmt__ta')) commitComment(a);
    }

    /* ── Комментарии ──────────────────────────────────────────── */
    const rangeLabel = (c) => (c.from === c.to ? `Строка ${c.from}` : `Строки ${c.from}–${c.to}`);
    function docEl() { return document.querySelector('#text-preview-render .de-doc'); }

    function placeComment(c, focus) {
        const doc = docEl();
        if (!doc) return null;
        let el = doc.querySelector(`.de-cmt[data-id="${c.id}"]`);
        if (!el) {
            el = document.createElement('div');
            el.className = 'de-cmt';
            el.dataset.id = c.id;
            el.innerHTML =
                `<div class="de-cmt__hd">${IC.cmt}<span class="de-cmt__lbl"></span>` +
                `<button type="button" class="de-cmt__x" title="Удалить комментарий" aria-label="Удалить комментарий">${IC.x}</button></div>` +
                '<div class="de-cmt__q" hidden></div>' +
                '<textarea class="de-cmt__ta" rows="1" placeholder="Что нужно изменить?"></textarea>';
            const anchor = doc.querySelector(`.de-row[data-line="${c.to}"]`);
            let after = anchor;
            while (after && after.nextElementSibling && after.nextElementSibling.classList.contains('de-cmt')) after = after.nextElementSibling;
            if (after) after.after(el); else doc.appendChild(el);
        }
        el.querySelector('.de-cmt__lbl').textContent = rangeLabel(c) + (c.quote ? ' · фрагмент' : '');
        const q = el.querySelector('.de-cmt__q');
        q.hidden = !c.quote;
        q.textContent = c.quote ? `«${c.quote.length > 240 ? c.quote.slice(0, 240) + '…' : c.quote}»` : '';
        const ta = el.querySelector('.de-cmt__ta');
        if (document.activeElement !== ta) ta.value = c.text || '';
        autoGrow(ta);
        if (focus) {
            for (let n = c.from; n <= c.to; n++) {
                const r = doc.querySelector(`.de-row[data-line="${n}"]`);
                if (r) r.classList.add('is-sel');
            }
            requestAnimationFrame(() => {
                ta.focus({ preventScroll: true });
                el.scrollIntoView({ block: 'nearest' });
            });
        }
        return el;
    }
    function clearSelMarks() {
        document.querySelectorAll('#text-preview-render .de-row.is-sel').forEach((r) => r.classList.remove('is-sel'));
    }
    function addComment(from, to, quote) {
        if (!S.doc) return;
        const a = Math.min(from, to), b = Math.max(from, to);
        clearSelMarks();
        const ex = !quote && S.doc.comments.find((c) => c.from === a && c.to === b && !c.quote);
        if (ex) { placeComment(ex, true); return; }
        const c = {
            id: 'c' + Date.now().toString(36) + Math.random().toString(36).slice(2, 5),
            from: a, to: b, quote: quote || '', text: '',
            src: S.orig.slice(a - 1, b).join('\n').slice(0, 4000),
        };
        S.doc.comments.push(c);
        placeComment(c, true);
    }
    function commitComment(ta) {
        const el = ta.closest('.de-cmt');
        if (!el || !S.doc) return;
        const c = S.doc.comments.find((x) => x.id === el.dataset.id);
        if (!c) return;
        c.text = ta.value;
        refreshRowMarks(c);
        save(); syncBar();
    }
    function removeComment(el) {
        if (!S.doc || !el) return;
        const id = el.dataset.id;
        const c = S.doc.comments.find((x) => x.id === id);
        S.doc.comments = S.doc.comments.filter((x) => x.id !== id);
        el.remove();
        if (c) refreshRowMarks(c);
        clearSelMarks();
        save(); syncBar();
    }
    function refreshRowMarks(c) {
        const doc = docEl();
        if (!doc) return;
        for (let n = c.from; n <= c.to; n++) {
            const r = doc.querySelector(`.de-row[data-line="${n}"]`);
            if (r) markRow(r, n);
        }
    }
    function autoGrow(ta) {
        ta.style.height = 'auto';
        ta.style.height = Math.min(ta.scrollHeight, 180) + 'px';
    }

    /* ── Комментарий к выделенному фрагменту ──────────────────── */
    function selInfo() {
        const sel = window.getSelection();
        const render = $('text-preview-render');
        if (!sel || sel.isCollapsed || !render || !sel.rangeCount) return null;
        const r = sel.getRangeAt(0);
        if (!render.contains(r.commonAncestorContainer)) return null;
        const rowOf = (node) => {
            const el = node.nodeType === 1 ? node : node.parentElement;
            return el && el.closest ? el.closest('.de-row') : null;
        };
        const a = rowOf(r.startContainer), b = rowOf(r.endContainer);
        if (!a || !b) return null;
        const from = Number(a.dataset.line), to = Number(b.dataset.line);
        const doc = docEl();
        const parts = [];
        for (let n = from; n <= to; n++) {
            const tx = doc && doc.querySelector(`.de-row[data-line="${n}"] .de-txt`);
            if (!tx) continue;
            const rr = document.createRange();
            rr.selectNodeContents(tx);
            if (n === from && tx.contains(r.startContainer)) rr.setStart(r.startContainer, r.startOffset);
            if (n === to && tx.contains(r.endContainer)) rr.setEnd(r.endContainer, r.endOffset);
            parts.push(rr.toString());
        }
        const quote = parts.join('\n').replace(/\n{2,}/g, '\n').trim();
        if (!quote) return null;
        const rects = Array.from(r.getClientRects()).filter((q) => q.width > 1 && q.height > 1);
        return { from, to, quote: quote.slice(0, 2000), rect: r.getBoundingClientRect(), rects };
    }
    function ensureSelBtn() {
        if (selBtn && selBtn.isConnected) return selBtn;
        const wrap = document.querySelector('#text-preview-panel .text-preview-wrap');
        if (!wrap) return null;
        selBtn = document.createElement('button');
        selBtn.type = 'button';
        selBtn.className = 'de-selbtn';
        selBtn.hidden = true;
        selBtn.innerHTML = `${IC.cmt}<span>Комментарий</span>`;
        selBtn.addEventListener('pointerdown', (e) => e.preventDefault());
        selBtn.addEventListener('mousedown', (e) => e.preventDefault());
        selBtn.addEventListener('click', (e) => {
            e.preventDefault();
            const info = selBtn._info;
            hideSel();
            if (!info) return;
            flushFocused();
            try { window.getSelection().removeAllRanges(); } catch (_) {}
            addComment(info.from, info.to, info.quote);
        });
        wrap.appendChild(selBtn);
        return selBtn;
    }
    function hideSel() { if (selBtn) { selBtn.hidden = true; selBtn._info = null; } }
    let selT = 0;
    function onSelChange() {
        if (!S.on) return;
        clearTimeout(selT);
        selT = setTimeout(() => {
            const info = selInfo();
            const b = ensureSelBtn();
            if (!b) return;
            if (!info) { hideSel(); return; }
            const wrap = b.parentElement.getBoundingClientRect();
            b._info = info;
            b.hidden = false;
            const bw = b.offsetWidth || 120, bh = b.offsetHeight || 30;
            // Кнопка под концом выделения, чтобы не закрывать соседнюю строку сверху;
            // если снизу места нет — над началом выделения.
            // Не закрываем текст: сначала свободное место справа от конца выделения
            // (на той же строке), затем над началом выделения, затем под концом.
            const rs = info.rects;
            const last = rs[rs.length - 1] || info.rect, first = rs[0] || info.rect;
            const botLim = wrap.height - 6 - ((bar && !bar.hidden) ? bar.offsetHeight : 0);
            const topLim = 6 + (($('text-preview-panel') || document).querySelector('.text-preview-head')?.getBoundingClientRect().bottom - wrap.top || 0);
            // Конец текста той визуальной строки, где закончилось выделение,
            // чтобы кнопка не легла на хвост строки после выделения.
            let lineEnd = last.right;
            const tx = docEl() && docEl().querySelector(`.de-row[data-line="${info.to}"] .de-txt`);
            if (tx) {
                const rr = document.createRange();
                rr.selectNodeContents(tx);
                for (const q of rr.getClientRects()) {
                    if (q.width > 0 && Math.abs(q.top - last.top) < 3) lineEnd = Math.max(lineEnd, q.right);
                }
            }
            let x = lineEnd - wrap.left + 12;
            let y = last.top - wrap.top + last.height / 2 - bh / 2;
            if (x + bw > wrap.width - 8) {
                x = first.left - wrap.left;
                y = first.top - wrap.top - bh - 6;
                // Над выделением — правее текста строки выше, если там хватает места.
                let prevEnd = -Infinity;
                document.querySelectorAll('#text-preview-render .de-txt').forEach((t) => {
                    const rr = document.createRange();
                    rr.selectNodeContents(t);
                    for (const q of rr.getClientRects()) {
                        if (q.width > 0 && q.bottom > y + wrap.top && q.top < y + wrap.top + bh) prevEnd = Math.max(prevEnd, q.right);
                    }
                });
                if (prevEnd > -Infinity && prevEnd - wrap.left + 12 + bw <= wrap.width - 8) x = Math.max(x, prevEnd - wrap.left + 12);
                if (y < topLim) { x = last.right - wrap.left - bw; y = last.bottom - wrap.top + 6; }
            }
            y = Math.min(y, botLim - bh);
            x = Math.max(8, Math.min(x, wrap.width - bw - 8));
            y = Math.max(6, Math.min(y, wrap.height - bh - 6));
            b.style.left = `${Math.round(x)}px`;
            b.style.top = `${Math.round(y)}px`;
        }, 120);
    }

    /* ── Нижняя панель: счётчик, общий комментарий, отправка ─── */
    function ensureBar() {
        if (bar && bar.isConnected) { bar.hidden = false; return bar; }
        const wrap = document.querySelector('#text-preview-panel .text-preview-wrap');
        if (!wrap) return null;
        bar = document.createElement('div');
        bar.className = 'de-bar';
        bar.innerHTML =
            '<div class="de-bar__row">' +
            '<span class="de-bar__sum"></span>' +
            `<button type="button" class="de-bar__reset" title="Сбросить правки этого файла">${IC.undo}<span>Сбросить</span></button>` +
            '</div>' +
            '<div class="de-bar__compose">' +
            '<textarea class="de-bar__note" rows="1" placeholder="Общий комментарий к правкам (необязательно)"></textarea>' +
            `<button type="button" class="de-bar__send" title="Отправить правки агенту (Ctrl+Enter)" aria-label="Отправить правки агенту">${IC.send}</button>` +
            '</div>';
        wrap.appendChild(bar);
        const note = bar.querySelector('.de-bar__note');
        note.addEventListener('input', () => { NOTE[chatId()] = note.value; autoGrow(note); save(); syncBar(); });
        bar.querySelector('.de-bar__reset').addEventListener('click', resetDoc);
        bar.querySelector('.de-bar__send').addEventListener('click', send);
        return bar;
    }
    function syncBar() {
        if (!bar || !S.on) { syncBtn(); return; }
        bar.hidden = false;
        const chat = chatId();
        const docs = chatDocs(chat);
        const edits = docs.reduce((s, d) => s + docEdits(d).length, 0);
        const cmts = docs.reduce((s, d) => s + d.comments.filter((c) => c.text.trim()).length, 0);
        const parts = [];
        if (edits) parts.push(`${edits} ${plural(edits, 'правка', 'правки', 'правок')}`);
        if (cmts) parts.push(`${cmts} ${plural(cmts, 'комментарий', 'комментария', 'комментариев')}`);
        if (docs.length > 1) parts.push(`${docs.length} ${plural(docs.length, 'файл', 'файла', 'файлов')}`);
        bar.querySelector('.de-bar__sum').textContent = parts.length ? parts.join(' · ') : 'Правок пока нет';
        bar.querySelector('.de-bar__reset').disabled = !(S.doc && docCount(S.doc));
        const note = bar.querySelector('.de-bar__note');
        if (document.activeElement !== note) { note.value = NOTE[chat] || ''; autoGrow(note); }
        bar.querySelector('.de-bar__send').disabled = !docs.length;
        syncBtn();
    }
    function resetDoc() {
        if (!S.doc || !docCount(S.doc)) return;
        if (!window.confirm(`Сбросить все правки в «${S.doc.name}»?`)) return;
        S.doc.edits = {}; S.doc.before = {}; S.doc.comments = [];
        save();
        renderEditor();
        syncBar();
    }

    /* ── Сборка запроса ───────────────────────────────────────── */
    // Подряд идущие изменённые строки склеиваются в один блок from..to.
    function docEdits(d) {
        const ns = Object.keys(d.edits || {}).map(Number).sort((a, b) => a - b);
        const bef = d.before || {};
        const out = [];
        let run = null;
        const flush = () => {
            if (!run) return;
            const before = run.map((n) => bef[n] ?? '');
            const after = [];
            run.forEach((n) => {
                const v = d.edits[n];
                if (!(v === '' && (bef[n] ?? '') !== '')) after.push(v);
            });
            const e = { op: after.length ? 'replace' : 'delete', from: run[0], to: run[run.length - 1], before: before.join('\n') };
            if (after.length) e.after = after.join('\n');
            out.push(e);
            run = null;
        };
        ns.forEach((n) => {
            if (run && n === run[run.length - 1] + 1) run.push(n);
            else { flush(); run = [n]; }
        });
        flush();
        return out;
    }
    function clip(s, n = 300) {
        s = String(s);
        return s.length > n ? s.slice(0, n) + '…' : s;
    }
    function quoteBlock(label, text) {
        const lines = clip(text).split('\n');
        const pad = ' '.repeat(label.length + 5);
        return [`   ${label}: «${lines[0]}`].concat(lines.slice(1).map((l) => pad + l)).join('\n') + '»';
    }
    function buildRequest(chat) {
        const docs = chatDocs(chat);
        const note = (NOTE[chat] || '').trim();
        const payloadDocs = [];
        const L = [];
        L.push(docs.length > 1 ? 'Правки к документам из чата.' : 'Правки к документу из чата.');
        L.push('Номера строк — по версии файла, которая приложена к указанному сообщению.');
        docs.forEach((d) => {
            const edits = docEdits(d);
            const comments = d.comments.filter((c) => c.text.trim()).sort((a, b) => a.from - b.from)
                .map((c) => {
                    const o = { from: c.from, to: c.to, comment: c.text.trim() };
                    if (c.quote) o.quote = c.quote;
                    else if (c.src != null) o.text = c.src;
                    return o;
                });
            payloadDocs.push({ file: { name: d.name, message_id: d.message_id, url: d.url, lines: d.lines }, edits, comments });
            L.push('', `Файл: ${d.name}` + (d.message_id ? ` (сообщение ${d.message_id})` : ''));
            const items = [
                ...edits.map((e) => ({ at: e.from, e })),
                ...comments.map((c) => ({ at: c.from, c })),
            ].sort((a, b) => a.at - b.at || (a.e ? -1 : 1));
            items.forEach((it, i) => {
                const x = it.e || it.c;
                const rng = x.from === x.to ? `Строка ${x.from}` : `Строки ${x.from}–${x.to}`;
                if (it.e && it.e.op === 'delete') {
                    L.push(`${i + 1}. ${rng} — удалить`);
                    L.push(quoteBlock('текст', it.e.before));
                } else if (it.e) {
                    L.push(`${i + 1}. ${rng} — заменить`);
                    L.push(quoteBlock('было', it.e.before));
                    L.push(quoteBlock('стало', it.e.after));
                } else {
                    L.push(`${i + 1}. ${rng} — ${it.c.comment}`);
                    if (it.c.quote) L.push(quoteBlock('фрагмент', it.c.quote));
                    else if (it.c.text != null) L.push(quoteBlock('текст', it.c.text));
                }
            });
        });
        if (note) L.push('', `Общий комментарий: ${note}`);
        L.push('', 'Замены и удаления перенеси как есть, комментарии выполни. Остальной текст не меняй.');
        L.push('Пришли обновлённые файлы целиком, с теми же именами.');
        L.push('', 'Полная разметка — в JSON (document_edit_request).');
        const payload = {
            kind: 'document_edit_request', version: 1,
            context: { source: gatewayName(), chat_id: chat || null },
            line_base: 1,
            line_note: 'from/to — номера строк исходного файла, с 1, включительно; before — исходный текст строк, after — текст, который должен стоять на их месте',
            documents: payloadDocs,
            prompt: note,
            output: { same_name: true, full_file: true },
        };
        return { text: L.join('\n'), payload, docs };
    }

    let sending = false;
    async function send() {
        if (sending) return;
        flushFocused();
        const a = app();
        const chat = chatId();
        if (!a || typeof a.sendMessage !== 'function') { toast('Чат недоступен'); return; }
        if (a._runState && a._runState !== 'idle') { toast('Агент ещё отвечает. Дождитесь ответа и отправьте правки.'); return; }
        if (!chat) { toast('Откройте чат, чтобы отправить правки'); return; }
        const req = buildRequest(chat);
        if (!req.docs.length) return;
        const body = req.text + '\n\n```json\n' + JSON.stringify(req.payload, null, 2) + '\n```';
        const b = bar && bar.querySelector('.de-bar__send');
        if (b) b.disabled = true;
        sending = true;
        let ok = true;
        try { await a.sendMessage(body); } catch (e) { ok = false; console.error('[doc-edit] send', e); }
        sending = false;
        if (!ok) { toast('Не удалось отправить правки'); syncBar(); return; }
        Object.keys(DOCS).forEach((k) => { if (req.docs.includes(DOCS[k])) delete DOCS[k]; });
        delete NOTE[chat];
        save();
        leave({ silent: true });
        const close = $('text-preview-close');
        if (close) close.click();
    }

    /* ── События ──────────────────────────────────────────────── */
    document.addEventListener('filepreview:state', (e) => {
        const ph = e.detail && e.detail.phase;
        if ((ph === 'open' || ph === 'exit') && S.on) leave({ silent: true });
        syncBtn();
    });
    document.addEventListener('input', (e) => {
        const t = e.target;
        if (!S.on || !t || !t.classList) return;
        if (t.classList.contains('de-txt')) commitRow(t);
        else if (t.classList.contains('de-cmt__ta')) { autoGrow(t); commitComment(t); }
    });
    document.addEventListener('focusout', (e) => {
        const t = e.target;
        if (!S.on || !t || !t.classList) return;
        if (t.classList.contains('de-txt')) { commitRow(t); deactivate(t); }
        else if (t.classList.contains('de-cmt__ta')) {
            commitComment(t);
            const el = t.closest('.de-cmt');
            setTimeout(() => {
                if (el && el.isConnected && document.activeElement !== t && !t.value.trim()) removeComment(el);
                clearSelMarks();
            }, 150);
        }
    });
    // Фокус с клавиатуры (Tab) включает правку строки; фокус от мыши/тапа обрабатывает click
    // с координатами, чтобы курсор встал туда, куда кликнули.
    let ptrDown = false;
    document.addEventListener('pointerdown', () => { ptrDown = true; }, true);
    document.addEventListener('pointerup', () => { ptrDown = false; }, true);
    document.addEventListener('pointercancel', () => { ptrDown = false; }, true);
    document.addEventListener('focusin', (e) => {
        const t = e.target;
        if (!S.on || ptrDown || !t || !t.classList || !t.classList.contains('de-txt') || t.isContentEditable) return;
        activate(t);
    });
    let lastNum = 0;
    document.addEventListener('click', (e) => {
        if (!S.on || !e.target || !e.target.closest) return;
        const t = e.target;
        const txt = t.closest('#text-preview-render .de-txt');
        if (txt && !txt.isContentEditable) {
            const sel = window.getSelection();
            if (!sel || sel.isCollapsed) activate(txt, e.clientX, e.clientY);
            return;
        }
        const num = t.closest('#text-preview-render .de-num');
        if (num) {
            e.preventDefault();
            flushFocused();
            const n = Number(num.closest('.de-row').dataset.line);
            addComment(e.shiftKey && lastNum ? lastNum : n, n, '');
            lastNum = n;
            return;
        }
        const undo = t.closest('#text-preview-render .de-undo');
        if (undo) {
            e.preventDefault();
            const row = undo.closest('.de-row');
            const n = Number(row.dataset.line);
            const doc = docEl();
            for (let k = n; Object.prototype.hasOwnProperty.call(S.doc.edits, k); k++) {
                delete S.doc.edits[k];
                delete S.doc.before[k];
                const tx = doc && doc.querySelector(`.de-row[data-line="${k}"] .de-txt`);
                if (tx) tx.textContent = S.orig[k - 1];
            }
            markNear(n);
            save(); syncBar();
            return;
        }
        const x = t.closest('#text-preview-render .de-cmt__x');
        if (x) { e.preventDefault(); removeComment(x.closest('.de-cmt')); }
    });
    document.addEventListener('paste', (e) => {
        if (PLAIN) return;
        const t = e.target && e.target.closest ? e.target.closest('#text-preview-render .de-txt') : null;
        if (!t) return;
        e.preventDefault();
        const text = (e.clipboardData && e.clipboardData.getData('text/plain')) || '';
        document.execCommand('insertText', false, text);
    });
    document.addEventListener('keydown', (e) => {
        if (!S.on) return;
        const t = e.target;
        const inEd = t && t.classList && (t.classList.contains('de-txt') || t.classList.contains('de-cmt__ta'));
        if (e.key === 'Escape' && inEd) { e.stopPropagation(); e.preventDefault(); t.blur(); return; }
        if ((e.ctrlKey || e.metaKey) && e.key === 'Enter' && t && t.closest && t.closest('#text-preview-panel')) {
            e.preventDefault(); e.stopPropagation(); send();
        }
    }, true);
    document.addEventListener('selectionchange', onSelChange);
    document.addEventListener('scroll', (e) => {
        if (S.on && e.target && e.target.id === 'text-preview-render') hideSel();
    }, true);

    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', ensureHeaderBtn, { once: true });
    else ensureHeaderBtn();

    window.DocEdit = { toggle, buildRequest: () => buildRequest(chatId()), get state() { return S; } };
})();
