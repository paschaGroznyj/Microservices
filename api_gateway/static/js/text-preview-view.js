/**
 * TPView — оформление правой панели превью (#text-preview-panel).
 *
 *   head({ name, type, label, color, meta }) — бейдж типа, имя со средним эллипсисом, мета
 *   setMeta(text)                      — обновить мету в шапке
 *   code(text, lang, opts)             — просмотр кода в стиле ws-diff / Monaco:
 *                                        номера строк, направляющие отступов, текущая
 *                                        строка, миникарта (в развёрнутом виде), статус-бар
 *   pdf(render, data)                  — PDF через pdf.js: зум, «по ширине» / «страница»,
 *                                        листание, миниатюры страниц в развёрнутом виде
 *   closePdf()                         — освободить документ pdf.js
 *   setWide(on) / isWide()             — развернуть панель на всю ширину
 *
 * pdf.js (~1,7 МБ вместе с worker) подгружается через import() только при первом
 * открытии PDF. Ширина панели тянется за левый край и запоминается в localStorage.
 */
(() => {
    'use strict';
    if (window.TPView) return;

    const PDFJS_URL = '/static/js/vendor/pdfjs/pdf.min.js';
    const PDFJS_WORKER = '/static/js/vendor/pdfjs/pdf.worker.min.js';
    const W_KEY = 'tp.panelW';
    const W_MIN = 360;
    const CV_MAX_LINES = 20000;   // больше — отдаём старому <pre>, таблица слишком тяжёлая
    const CV_MINI_LINES = 4000;   // миникарта только для файлов поменьше
    const CV_HL_MAX = 200 * 1024;

    const $ = (id) => document.getElementById(id);
    const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
    const narrowScreen = () => window.matchMedia('(max-width: 900px)').matches;
    const plural = (n, a, b, c) => {
        const m10 = n % 10, m100 = n % 100;
        if (m10 === 1 && m100 !== 11) return a;
        if (m10 >= 2 && m10 <= 4 && (m100 < 12 || m100 > 14)) return b;
        return c;
    };

    const svg = (d, sw = 1.6) => `<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><path d="${d}" stroke="currentColor" stroke-width="${sw}" stroke-linecap="round" stroke-linejoin="round"/></svg>`;
    const IC = {
        full: svg('M4 8V4h4M16 8V4h-4M4 12v4h4M16 12v4h-4'),
        narrow: svg('M8 4v4H4M12 4v4h4M8 16v-4H4M12 16v-4h4'),
        minus: svg('M5 10h10', 1.8),
        plus: svg('M5 10h10M10 5v10', 1.8),
        fitw: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="4" y="5" width="12" height="10" rx="1.2" stroke="currentColor" stroke-width="1.5"/><path d="M1.8 10h2M16.2 10h2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
        fitp: '<svg viewBox="0 0 20 20" fill="none" aria-hidden="true"><rect x="6" y="5" width="8" height="10" rx="1.2" stroke="currentColor" stroke-width="1.5"/><path d="M10 1.8v2M10 16.2v2" stroke="currentColor" stroke-width="1.5" stroke-linecap="round"/></svg>',
        prev: svg('M12 5l-5 5 5 5', 1.7),
        next: svg('M8 5l5 5-5 5', 1.7),
        wrap: svg('M3 5h14M3 10h11a3 3 0 010 6h-3m0 0l2-2m-2 2l2 2M3 15h4', 1.5),
    };
    const ib = (cls, title, icon) => `<button type="button" class="tp-ib ${cls}" title="${title}" aria-label="${title}">${icon}</button>`;

    /* ───────────── Шапка ───────────── */
    function head(info) {
        const ty = $('text-preview-ty'), title = $('text-preview-title'), panel = $('text-preview-panel');
        if (!panel) return;
        if (!info) {
            if (ty) { ty.hidden = true; ty.textContent = ''; }
            if (title) title.removeAttribute('title');
            panel.removeAttribute('data-tp-type');
            setMeta('');
            syncRailBtn();
            return;
        }
        const name = String(info.name || '');
        panel.dataset.tpType = info.type || '';
        if (ty) {
            ty.textContent = info.label || '';
            ty.dataset.c = info.color || 'gray';
            ty.hidden = !info.label;
        }
        if (title) {
            // Средний эллипсис: режется основа имени, расширение видно всегда.
            const m = name.match(/^(.+?)(\.[^./\s]{1,10})$/);
            title.innerHTML = m
                ? `<span class="tp-nb">${esc(m[1])}</span><span class="tp-ne">${esc(m[2])}</span>`
                : `<span class="tp-nb">${esc(name)}</span>`;
            title.title = name;
        }
        setMeta(info.meta || '');
        syncRailBtn();
    }
    function setMeta(text) {
        const meta = $('text-preview-meta');
        if (!meta) return;
        meta.textContent = text || '';
        meta.hidden = !text;
    }

    /* ───────────── Разворот и ширина ───────────── */
    const isWide = () => !!$('text-preview-panel')?.classList.contains('is-wide');
    function syncWideBtn() {
        const b = $('text-preview-wide');
        if (!b) return;
        const on = isWide();
        const t = on ? 'Свернуть к чату' : 'Развернуть на всю ширину';
        b.innerHTML = on ? IC.narrow : IC.full;
        b.title = t; b.setAttribute('aria-label', t);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
        b.classList.toggle('is-on', on);
    }
    function syncRailBtn() {
        const b = $('text-preview-rail'), panel = $('text-preview-panel');
        if (!b || !panel) return;
        b.hidden = !(panel.dataset.tpType === 'pdf' && isWide());
        const on = !panel.classList.contains('no-rail');
        b.classList.toggle('is-on', on);
        b.setAttribute('aria-pressed', on ? 'true' : 'false');
    }
    function setWide(on) {
        const panel = $('text-preview-panel');
        if (!panel) return;
        const next = !!on && !narrowScreen();
        if (next === isWide()) return;
        panel.classList.toggle('is-wide', next);
        sidebarForWide(next);
        syncWideBtn();
        syncRailBtn();
        if (PDF && PDF.setMode && (PDF.mode === 'fw' || PDF.mode === 'fp')) PDF.setMode(next ? 'fp' : 'fw');
        document.dispatchEvent(new CustomEvent('tpview:layout', { detail: { wide: next } }));
    }

    // Разворот на всю ширину сворачивает левый сайдбар, а возврат раскрывает его,
    // если свернули мы. Состояние в localStorage не пишем: это временный режим.
    let sbAuto = false;
    function sidebarForWide(on) {
        const layout = document.querySelector('.chat-layout');
        if (!layout) return;
        const collapsed = layout.classList.contains('sidebar-collapsed');
        if (on === collapsed) { if (!on) sbAuto = false; return; }
        if (!on && !sbAuto) return;
        sbAuto = on;
        layout.classList.toggle('sidebar-collapsed', on);
        const exp = $('sidebar-expand-btn'), col = $('sidebar-collapse-btn');
        if (exp) exp.hidden = !on;
        if (col) col.setAttribute('aria-expanded', String(!on));
    }

    function savedWidth() {
        try { const v = Number(localStorage.getItem(W_KEY)); return v >= W_MIN ? v : 0; } catch (_) { return 0; }
    }
    function applyWidth(panel, w) {
        if (w) { panel.style.width = w + 'px'; panel.style.maxWidth = 'none'; }
        else { panel.style.width = ''; panel.style.maxWidth = ''; }
    }
    function bindGrip() {
        const grip = $('text-preview-grip'), panel = $('text-preview-panel');
        if (!grip || !panel || grip.dataset.bound) return;
        grip.dataset.bound = '1';
        applyWidth(panel, savedWidth());
        grip.addEventListener('pointerdown', (e) => {
            if (e.button !== 0 || isWide() || narrowScreen()) return;
            e.preventDefault();
            const host = panel.parentElement;
            const startX = e.clientX, startW = panel.getBoundingClientRect().width;
            const maxW = Math.max(W_MIN, (host ? host.clientWidth : window.innerWidth) - 240);
            panel.classList.add('is-resizing');
            grip.classList.add('is-hot');
            grip.setPointerCapture(e.pointerId);
            const move = (ev) => applyWidth(panel, Math.round(Math.min(maxW, Math.max(W_MIN, startW + (startX - ev.clientX)))));
            const up = () => {
                grip.removeEventListener('pointermove', move);
                grip.removeEventListener('pointerup', up);
                grip.removeEventListener('pointercancel', up);
                panel.classList.remove('is-resizing');
                grip.classList.remove('is-hot');
                try { localStorage.setItem(W_KEY, String(Math.round(panel.getBoundingClientRect().width))); } catch (_) {}
            };
            grip.addEventListener('pointermove', move);
            grip.addEventListener('pointerup', up);
            grip.addEventListener('pointercancel', up);
        });
        // Двойной клик по ручке — вернуть ширину по умолчанию.
        grip.addEventListener('dblclick', () => {
            applyWidth(panel, 0);
            try { localStorage.removeItem(W_KEY); } catch (_) {}
        });
    }

    function bindPanel() {
        const panel = $('text-preview-panel');
        if (!panel || panel.dataset.tpvBound) return;
        panel.dataset.tpvBound = '1';
        bindGrip();
        syncWideBtn();
        panel.addEventListener('click', (e) => {
            const b = e.target.closest && e.target.closest('button');
            if (!b) return;
            if (b.id === 'text-preview-wide') { e.preventDefault(); setWide(!isWide()); }
            else if (b.id === 'text-preview-rail') {
                e.preventDefault();
                panel.classList.toggle('no-rail');
                syncRailBtn();
            }
        });
        // Панель закрыли: после выезда возвращаем обычную ширину.
        let tm = 0;
        new MutationObserver(() => {
            clearTimeout(tm);
            if (panel.classList.contains('open')) return;
            tm = setTimeout(() => { if (!panel.classList.contains('open')) setWide(false); }, 300);
        }).observe(panel, { attributes: true, attributeFilter: ['class'] });
        document.addEventListener('keydown', (e) => {
            if (!panel.classList.contains('open')) return;
            if (e.altKey && !e.ctrlKey && !e.metaKey && e.code === 'KeyZ') {
                const btn = panel.querySelector('.cv-wrap');
                if (btn) { e.preventDefault(); btn.click(); }
            }
        });
        const mq = window.matchMedia('(max-width: 900px)');
        if (mq.addEventListener) mq.addEventListener('change', (m) => { if (m.matches) setWide(false); });
    }

    /* ───────────── Код: ws-diff + Monaco ───────────── */
    const LANG_NAME = {
        python: 'Python', javascript: 'JavaScript', typescript: 'TypeScript', json: 'JSON', xml: 'HTML',
        css: 'CSS', scss: 'SCSS', less: 'Less', sql: 'SQL', bash: 'Shell', yaml: 'YAML', ini: 'INI',
        go: 'Go', rust: 'Rust', java: 'Java', kotlin: 'Kotlin', c: 'C', cpp: 'C++', csharp: 'C#',
        php: 'PHP', ruby: 'Ruby', swift: 'Swift', lua: 'Lua', r: 'R', diff: 'Diff', dockerfile: 'Dockerfile',
        makefile: 'Makefile', markdown: 'Markdown', graphql: 'GraphQL', protobuf: 'Protobuf',
    };

    function highlight(text, lang) {
        const h = window.hljs;
        if (h && lang && text.length <= CV_HL_MAX && h.getLanguage && h.getLanguage(lang)) {
            try { return h.highlight(text, { language: lang, ignoreIllegals: true }).value; } catch (_) {}
        }
        return esc(text);
    }
    // HTML hljs режем по строкам, перенося незакрытые <span> на следующую строку.
    function splitLines(html) {
        const out = [], stack = [];
        for (const line of html.split('\n')) {
            const pre = stack.join('');
            const re = /<span[^>]*>|<\/span>/g;
            let m;
            while ((m = re.exec(line))) { if (m[0][1] === '/') stack.pop(); else stack.push(m[0]); }
            out.push(pre + line + '</span>'.repeat(stack.length));
        }
        return out;
    }
    function indentOf(line, ts) {
        let n = 0;
        for (const ch of line) { if (ch === ' ') n++; else if (ch === '\t') n += ts; else break; }
        return n;
    }
    // Шаг отступа: табы → 4, иначе самый частый прирост между соседними строками (2 или 4).
    function detectIndent(raw) {
        if (raw.some((l) => l.startsWith('\t'))) return { size: 4, tabs: true };
        const votes = { 2: 0, 4: 0 };
        let prev = 0;
        for (const l of raw) {
            if (!l.trim()) continue;
            const n = indentOf(l, 4), d = n - prev;
            if (d === 2 || d === 4) votes[d]++;
            prev = n;
        }
        return { size: votes[2] > votes[4] ? 2 : 4, tabs: false };
    }
    const fmtBytes = (b) => (window.FileTiles?.fmtSize ? window.FileTiles.fmtSize(b) : b < 1024 ? `${b} Б` : b < 1024 * 1024
        ? `${(b / 1024).toFixed(1).replace('.', ',')} КБ` : `${(b / 1048576).toFixed(1).replace('.', ',')} МБ`);

    function code(text, lang, opts = {}) {
        const src = String(text ?? '').replace(/\r\n/g, '\n');
        const raw = src.split('\n');
        if (raw.length > 1 && raw[raw.length - 1] === '') raw.pop();
        if (raw.length > CV_MAX_LINES) return null;
        const body = raw.join('\n');
        const lines = splitLines(highlight(body, lang));
        const ind = detectIndent(raw);
        const TS = ind.size;
        const levels = raw.map((l) => (l.trim() ? indentOf(l, TS) : -1));
        for (let i = levels.length - 1, nx = 0; i >= 0; i--) { if (levels[i] < 0) levels[i] = nx; else nx = levels[i]; }
        const rows = lines.map((h, i) => {
            const lv = Math.floor(levels[i] / TS) * TS;
            const guide = lv >= TS ? `<i class="cv-ig" style="width:${lv}ch"></i>` : '';
            return `<tr><td class="ln" aria-hidden="true">${i + 1}</td><td class="cd">${guide}${h || ' '}</td></tr>`;
        }).join('');
        const label = LANG_NAME[lang] || opts.label || 'Текст';
        const size = opts.size != null ? opts.size : new Blob([body]).size;
        const mini = raw.length <= CV_MINI_LINES
            ? `<div class="cv-mini" aria-hidden="true"><pre>${lines.join('\n')}</pre><span class="cv-vp"></span></div>`
            : '';
        const root = document.createElement('div');
        root.className = 'cv-root';
        root.style.setProperty('--ts', TS + 'ch');
        root.innerHTML =
            `<div class="cv${opts.wrap ? ' wrap' : ''}"><div class="cv-sc" tabindex="0"><table class="cv-t"><tbody>${rows}</tbody></table></div>${mini}</div>` +
            `<div class="cv-st"><span class="cv-st-i" aria-hidden="true"><span class="mm-it">${esc(label)}</span><span class="mm-dot"></span>` +
            `<span class="mm-it"><b>${raw.length}</b>&nbsp;${plural(raw.length, 'строка', 'строки', 'строк')}${opts.formatted ? ' (форматировано)' : ''} · ${fmtBytes(size)}</span>` +
            `<span class="mm-dot cv-x"></span><span class="mm-it cv-pos cv-x">Стр 1, стлб 1</span>` +
            `<span class="mm-dot cv-x"></span><span class="mm-it cv-x">${ind.tabs ? 'Табуляция' : 'Пробелы: ' + TS}</span>` +
            `<span class="mm-dot cv-x"></span><span class="mm-it cv-x">UTF-8 · LF</span></span><span class="cv-sp"></span>` +
            ib('cv-wrap' + (opts.wrap ? ' is-on' : ''), 'Перенос строк · Alt+Z', IC.wrap) + '</div>';
        return { cls: 'cv', node: root, lines: raw.length, after: () => bindCode(root) };
    }

    function bindCode(root) {
        const cv = root.querySelector('.cv'), sc = root.querySelector('.cv-sc');
        const mini = root.querySelector('.cv-mini'), pos = root.querySelector('.cv-pos');
        if (!cv || !sc) return;
        let raf = 0;
        const sync = () => {
            raf = 0;
            if (!sc.isConnected) { ro.disconnect(); return; }
            cv.classList.toggle('is-ovr', !cv.classList.contains('wrap') && sc.scrollLeft + sc.clientWidth < sc.scrollWidth - 2);
            if (mini && mini.offsetWidth) {
                const pre = mini.firstElementChild, vp = mini.lastElementChild;
                const ph = pre.getBoundingClientRect().height, k = ph / Math.max(1, sc.scrollHeight);
                // Длинный файл не влезает в миникарту целиком: сдвигаем её вслед за прокруткой.
                const over = Math.max(0, ph - mini.clientHeight);
                const p = sc.scrollTop / Math.max(1, sc.scrollHeight - sc.clientHeight);
                const shift = over * Math.min(1, Math.max(0, p));
                pre.style.translate = `0 ${-shift}px`;
                vp.style.top = (sc.scrollTop * k - shift) + 'px';
                vp.style.height = Math.max(14, sc.clientHeight * k) + 'px';
            }
        };
        const queue = () => { if (!raf) raf = requestAnimationFrame(sync); };
        const ro = new ResizeObserver(queue);
        ro.observe(sc);
        sc.addEventListener('scroll', queue, { passive: true });
        sc.addEventListener('click', (e) => {
            const tr = e.target.closest('tr');
            if (!tr) return;
            sc.querySelectorAll('tr.is-cur').forEach((x) => x.classList.remove('is-cur'));
            tr.classList.add('is-cur');
            let col = 1;
            const sel = window.getSelection && window.getSelection();
            const cd = tr.cells[1];
            if (sel && sel.rangeCount && cd.contains(sel.anchorNode)) {
                try {
                    const r = document.createRange();
                    r.setStart(cd, 0); r.setEnd(sel.anchorNode, sel.anchorOffset);
                    col = r.toString().length + 1;
                } catch (_) {}
            }
            if (pos) pos.textContent = `Стр ${tr.rowIndex + 1}, стлб ${col}`;
        });
        if (mini) {
            mini.addEventListener('click', (e) => {
                const pre = mini.firstElementChild;
                const k = sc.scrollHeight / Math.max(1, pre.getBoundingClientRect().height);
                const y = e.clientY - pre.getBoundingClientRect().top;
                sc.scrollTop = y * k - sc.clientHeight / 2;
            });
        }
        root.querySelector('.cv-wrap')?.addEventListener('click', (e) => {
            const on = cv.classList.toggle('wrap');
            e.currentTarget.classList.toggle('is-on', on);
            queue();
        });
        queue();
    }

    /* ───────────── PDF ───────────── */
    let pdfjsP = null;
    function loadPdfjs() {
        if (!pdfjsP) {
            pdfjsP = import(PDFJS_URL).then((m) => {
                m.GlobalWorkerOptions.workerSrc = PDFJS_WORKER;
                return m;
            }).catch((err) => { pdfjsP = null; throw err; });
        }
        return pdfjsP;
    }

    let PDF = null;
    const PAD = 18, PILL = 64, ZMIN = 0.1, ZMAX = 5, THUMB_W = 92;
    // Шаги +/− как в браузерах; от «по ширине» (например, 87%) идём к ближайшему шагу.
    const ZSTEPS = [0.25, 0.33, 0.5, 0.67, 0.75, 0.8, 0.9, 1, 1.1, 1.25, 1.5, 1.75, 2, 2.5, 3, 4, 5];
    const zStep = (z, dir) => (dir > 0
        ? (ZSTEPS.find((s) => s > z + 0.005) ?? ZMAX)
        : ([...ZSTEPS].reverse().find((s) => s < z - 0.005) ?? ZMIN));

    function closePdf() {
        const st = PDF;
        PDF = null;
        if (!st) return;
        st.dead = true;
        clearTimeout(st.timer);
        try { st.io && st.io.disconnect(); } catch (_) {}
        try { st.rio && st.rio.disconnect(); } catch (_) {}
        try { st.ro && st.ro.disconnect(); } catch (_) {}
        try { st.task && st.task.destroy(); } catch (_) {}
        const panel = $('text-preview-panel');
        if (panel) panel.classList.remove('no-rail');
    }

    async function pdf(render, data) {
        closePdf();
        const st = {
            dead: false, doc: null, task: null, sizes: [], zoom: 1, mode: isWide() ? 'fp' : 'fw',
            cur: 1, visible: new Set(), tasks: new Map(), drawn: new Map(), timer: 0,
        };
        PDF = st;
        render.innerHTML =
            '<div class="tpv-pdf">' +
            '<div class="tpv-rail" aria-label="Страницы"></div>' +
            '<div class="tpv-stage"><div class="tpv-sc" tabindex="0"><div class="tpv-pages"></div></div>' +
            '<div class="tpv-zm" role="toolbar" aria-label="Масштаб">' +
            ib('z-out', 'Уменьшить', IC.minus) + '<b class="z-val">100%</b>' + ib('z-in', 'Увеличить', IC.plus) +
            '<span class="mm-dot"></span>' + ib('z-fw', 'По ширине', IC.fitw) + ib('z-fp', 'Страница целиком', IC.fitp) +
            '<span class="mm-dot tpv-pgx"></span>' + ib('p-prev tpv-pgx', 'Предыдущая страница', IC.prev) +
            '<span class="tpv-pgn"><b class="p-cur">1</b>&nbsp;/&nbsp;<span class="p-all">…</span></span>' +
            ib('p-next tpv-pgx', 'Следующая страница', IC.next) + '</div></div></div>';
        const root = render.firstElementChild;
        const sc = root.querySelector('.tpv-sc'), pagesEl = root.querySelector('.tpv-pages');
        const rail = root.querySelector('.tpv-rail'), zval = root.querySelector('.z-val');

        const lib = await loadPdfjs();
        if (st.dead) return null;
        st.task = lib.getDocument({ data, isEvalSupported: false, verbosity: 0 });
        const doc = await st.task.promise;
        if (st.dead) { try { doc.destroy(); } catch (_) {} return null; }
        st.doc = doc;
        for (let n = 1; n <= doc.numPages; n++) {
            const p = await doc.getPage(n);
            if (st.dead) return null;
            const vp = p.getViewport({ scale: 1 });
            st.sizes.push([vp.width, vp.height]);
        }
        root.querySelector('.p-all').textContent = String(doc.numPages);
        root.classList.toggle('is-single', doc.numPages === 1);

        const frag = document.createDocumentFragment(), rfrag = document.createDocumentFragment();
        st.sizes.forEach(([w, h], i) => {
            const pg = document.createElement('div');
            pg.className = 'tpv-pg';
            pg.dataset.n = String(i + 1);
            frag.appendChild(pg);
            const th = document.createElement('button');
            th.type = 'button';
            th.className = 'tpv-th' + (i === 0 ? ' is-on' : '');
            th.dataset.n = String(i + 1);
            th.setAttribute('aria-label', `Страница ${i + 1}`);
            th.innerHTML = `<span class="tpv-thc" style="width:${THUMB_W}px;height:${Math.round(THUMB_W * h / w)}px"></span><span>${i + 1}</span>`;
            rfrag.appendChild(th);
        });
        pagesEl.appendChild(frag);
        rail.appendChild(rfrag);

        const zoomFor = (mode) => {
            const [w, h] = st.sizes[st.cur - 1] || st.sizes[0];
            const availW = Math.max(120, sc.clientWidth - 2 * PAD);
            if (mode === 'fw') return availW / Math.max(...st.sizes.map((s) => s[0]));
            return Math.min(availW / w, Math.max(120, sc.clientHeight - PAD - PILL) / h);
        };
        const clampZ = (z) => Math.min(ZMAX, Math.max(ZMIN, z));

        async function draw(n) {
            const z = st.zoom;
            if (st.dead || st.drawn.get(n) === z) return;
            const pg = pagesEl.children[n - 1];
            if (!pg) return;
            const prev = st.tasks.get(n);
            if (prev) { try { prev.cancel(); } catch (_) {} }
            const page = await st.doc.getPage(n);
            if (st.dead || st.zoom !== z) return;
            const dpr = Math.min(3, window.devicePixelRatio || 1);
            const vp = page.getViewport({ scale: z * dpr });
            const cv = document.createElement('canvas');
            cv.width = Math.floor(vp.width); cv.height = Math.floor(vp.height);
            const task = page.render({ canvasContext: cv.getContext('2d'), viewport: vp });
            st.tasks.set(n, task);
            try { await task.promise; } catch (_) { return; }
            if (st.dead || st.zoom !== z) return;
            st.tasks.delete(n);
            st.drawn.set(n, z);
            pg.replaceChildren(cv);
        }
        const drawVisible = () => {
            clearTimeout(st.timer);
            st.timer = setTimeout(() => { st.visible.forEach((n) => draw(n)); }, 120);
        };

        function layout(keep = true) {
            if (st.dead) return;
            if (st.mode !== 'free') st.zoom = clampZ(zoomFor(st.mode));
            const anchor = keep && sc.scrollHeight > sc.clientHeight
                ? (sc.scrollTop + sc.clientHeight / 2) / sc.scrollHeight : null;
            st.sizes.forEach(([w, h], i) => {
                const pg = pagesEl.children[i];
                pg.style.width = Math.round(w * st.zoom) + 'px';
                pg.style.height = Math.round(h * st.zoom) + 'px';
            });
            if (anchor != null) sc.scrollTop = anchor * sc.scrollHeight - sc.clientHeight / 2;
            zval.textContent = Math.round(st.zoom * 100) + '%';
            root.querySelectorAll('.z-fw,.z-fp').forEach((b) => b.classList.toggle('is-on', b.classList.contains('z-' + st.mode)));
            drawVisible();
        }
        st.setMode = (mode) => { st.mode = mode; layout(); };

        function setCur(n) {
            if (n === st.cur) return;
            st.cur = n;
            root.querySelector('.p-cur').textContent = String(n);
            rail.querySelectorAll('.tpv-th.is-on').forEach((t) => t.classList.remove('is-on'));
            const th = rail.children[n - 1];
            if (th) {
                th.classList.add('is-on');
                if (rail.offsetParent) {
                    const top = th.offsetTop, bot = top + th.offsetHeight;
                    if (top < rail.scrollTop || bot > rail.scrollTop + rail.clientHeight) {
                        rail.scrollTo({ top: top - rail.clientHeight / 2 + th.offsetHeight / 2, behavior: 'smooth' });
                    }
                }
            }
        }
        function goPage(n) {
            n = Math.max(1, Math.min(st.sizes.length, n));
            const pg = pagesEl.children[n - 1];
            if (!pg) return;
            sc.scrollTo({ top: pg.offsetTop - PAD, behavior: 'smooth' });
            setCur(n);
        }
        st.goPage = goPage;
        let sraf = 0;
        sc.addEventListener('scroll', () => {
            if (sraf) return;
            sraf = requestAnimationFrame(() => {
                sraf = 0;
                const y = sc.scrollTop + sc.clientHeight / 3;
                let n = 1;
                for (const pg of pagesEl.children) { if (pg.offsetTop <= y) n = Number(pg.dataset.n); else break; }
                setCur(n);
            });
        }, { passive: true });

        // Ctrl + колесо — масштаб, как в Acrobat.
        sc.addEventListener('wheel', (e) => {
            if (!e.ctrlKey && !e.metaKey) return;
            e.preventDefault();
            st.mode = 'free';
            st.zoom = clampZ(st.zoom * Math.exp(-e.deltaY * 0.0022));
            layout();
        }, { passive: false });

        root.addEventListener('click', (e) => {
            const b = e.target.closest('button');
            if (!b) return;
            const c = b.classList;
            if (c.contains('z-in')) { st.mode = 'free'; st.zoom = clampZ(zStep(st.zoom, 1)); layout(); }
            else if (c.contains('z-out')) { st.mode = 'free'; st.zoom = clampZ(zStep(st.zoom, -1)); layout(); }
            else if (c.contains('z-fw')) st.setMode('fw');
            else if (c.contains('z-fp')) { st.setMode('fp'); goPage(st.cur); }
            else if (c.contains('p-prev')) goPage(st.cur - 1);
            else if (c.contains('p-next')) goPage(st.cur + 1);
            else if (c.contains('tpv-th')) goPage(Number(b.dataset.n));
        });

        // Рисуем только страницы рядом с видимой областью.
        st.io = new IntersectionObserver((entries) => {
            entries.forEach((en) => {
                const n = Number(en.target.dataset.n);
                if (en.isIntersecting) { st.visible.add(n); draw(n); } else st.visible.delete(n);
            });
        }, { root: sc, rootMargin: '600px 0px' });
        Array.from(pagesEl.children).forEach((pg) => st.io.observe(pg));

        // Миниатюры рендерятся, только когда колонка видна (развёрнутый вид).
        st.rio = new IntersectionObserver((entries) => {
            entries.forEach(async (en) => {
                if (!en.isIntersecting) return;
                const box = en.target.querySelector('.tpv-thc');
                if (!box || box.dataset.done) return;
                box.dataset.done = '1';
                st.rio.unobserve(en.target);
                const n = Number(en.target.dataset.n);
                const page = await st.doc.getPage(n);
                if (st.dead) return;
                const dpr = Math.min(2, window.devicePixelRatio || 1);
                const vp = page.getViewport({ scale: (THUMB_W / st.sizes[n - 1][0]) * dpr });
                const cv = document.createElement('canvas');
                cv.width = Math.floor(vp.width); cv.height = Math.floor(vp.height);
                try { await page.render({ canvasContext: cv.getContext('2d'), viewport: vp }).promise; } catch (_) { return; }
                if (!st.dead) box.appendChild(cv);
            });
        }, { root: rail, rootMargin: '300px 0px' });
        Array.from(rail.children).forEach((th) => st.rio.observe(th));

        let lraf = 0;
        st.ro = new ResizeObserver(() => {
            if (lraf) return;
            lraf = requestAnimationFrame(() => { lraf = 0; if (st.mode !== 'free') layout(); });
        });
        st.ro.observe(sc);
        layout(false);
        syncRailBtn();
        return { pages: doc.numPages };
    }

    function init() { bindPanel(); }
    if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init, { once: true });
    else init();

    window.TPView = { head, setMeta, code, pdf, closePdf, loadPdfjs, setWide, isWide, plural, fmtBytes };
})();
