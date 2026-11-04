/**
 * FileTiles — квадратные плитки вложений (130×130).
 *
 * Превью рендерится внутри квадрата, подпись лежит поверх нижнего края,
 * рамок нет. Один рендер для полосы ввода (FileAttach) и для истории
 * сообщений пользователя (app.js _renderMessageFiles).
 *
 *   tileHtml(file, opts)       — HTML одной плитки
 *   groupHtml(files, opts)     — блок .fa-message-files с плитками
 *   enhanceScroller(host, o)   — горизонтальная прокрутка + стрелки, вернёт track
 *   trackOf(host)              — контейнер, в который кладутся плитки
 *   stopAudioFor(el)           — остановить звук, если он играет внутри el
 *
 * file = { name, size?, type?, url?, dlUrl? }
 * Клики делегированы: картинка/видео → лайтбокс с перелистыванием по
 * соседним плиткам, аудио → проигрывание в самой плитке, документ → скачать.
 */
(() => {
    'use strict';
    if (window.FileTiles) return;

    const EXT_LABEL = {
        pdf: 'PDF', docx: 'DOC', doc: 'DOC', xlsx: 'XLS', xls: 'XLS', csv: 'CSV',
        pptx: 'PPT', ppt: 'PPT', txt: 'TXT', md: 'MD',
        png: 'PNG', jpg: 'JPG', jpeg: 'JPG', gif: 'GIF', webp: 'WEBP', svg: 'SVG',
        mp4: 'MP4', mov: 'MOV', avi: 'AVI', webm: 'WEBM',
        mp3: 'MP3', wav: 'WAV', ogg: 'OGG', m4a: 'M4A', flac: 'FLAC', opus: 'OPUS', aac: 'AAC',
        zip: 'ZIP', rar: 'RAR', '7z': '7Z',
        json: 'JSON', xml: 'XML', html: 'HTML', py: 'PY', js: 'JS', ts: 'TS', css: 'CSS',
    };
    const EXT_COLOR = {
        pdf: 'red', doc: 'blue', docx: 'blue', xls: 'green', xlsx: 'green', csv: 'green',
        ppt: 'orange', pptx: 'orange', zip: 'gray', rar: 'gray', '7z': 'gray',
        json: 'yellow', xml: 'yellow', py: 'teal', js: 'yellow', ts: 'blue', css: 'purple',
    };
    const IMAGE_EXTS = new Set(['png', 'jpg', 'jpeg', 'gif', 'webp', 'svg', 'bmp', 'avif', 'ico']);
    const VIDEO_EXTS = new Set(['mp4', 'webm', 'mov', 'm4v', 'ogv', 'avi', 'mkv']);
    const AUDIO_EXTS = new Set(['mp3', 'mpga', 'wav', 'wave', 'ogg', 'oga', 'opus', 'm4a', 'aac', 'flac', 'weba', 'aif', 'aiff']);
    const OPEN_IN_TAB_EXTS = new Set(['pdf', 'txt']);

    const IC = {
        play: '<svg class="fa-tile__ic fa-tile__ic--play" viewBox="0 0 24 24" aria-hidden="true"><path d="M8 5.2c0-1.1 1.2-1.8 2.2-1.2l9.6 6c.9.6.9 1.9 0 2.4l-9.6 6c-1 .6-2.2-.1-2.2-1.2V5.2Z" fill="currentColor"/></svg>',
        pause: '<svg class="fa-tile__ic fa-tile__ic--pause" viewBox="0 0 24 24" aria-hidden="true"><rect x="6.5" y="5" width="4" height="14" rx="1.6" fill="currentColor"/><rect x="13.5" y="5" width="4" height="14" rx="1.6" fill="currentColor"/></svg>',
        close: '<svg viewBox="0 0 14 14" fill="none" aria-hidden="true"><path d="M3 3l8 8M11 3L3 11" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>',
        left: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M14.5 6l-6 6 6 6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
        right: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M9.5 6l6 6-6 6" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    };

    const esc = (s) => String(s ?? '').replace(/[&<>"']/g, (c) => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));
    const extOf = (name) => {
        const n = String(name || '');
        const i = n.lastIndexOf('.');
        return i > 0 ? n.slice(i + 1).toLowerCase() : '';
    };

    function kindOf(name, mime) {
        const ext = extOf(name);
        const m = String(mime || '').toLowerCase();
        if (m.startsWith('image/') || IMAGE_EXTS.has(ext)) return 'image';
        if (m.startsWith('video/') || VIDEO_EXTS.has(ext)) return 'video';
        if (m.startsWith('audio/') || AUDIO_EXTS.has(ext)) return 'audio';
        return 'doc';
    }
    const labelOf = (name) => {
        const e = extOf(name);
        return EXT_LABEL[e] || (e ? e.toUpperCase().slice(0, 4) : 'FILE');
    };
    function colorOf(name, kind) {
        if (kind === 'image' || kind === 'video') return 'purple';
        if (kind === 'audio') return 'teal';
        return EXT_COLOR[extOf(name)] || 'gray';
    }
    function fmtSize(bytes) {
        const b = Number(bytes);
        if (!isFinite(b) || b <= 0) return '';
        if (b < 1024) return `${b} Б`;
        if (b < 1024 * 1024) return `${Math.round(b / 1024)} КБ`;
        return `${(b / 1024 / 1024).toFixed(b < 10 * 1024 * 1024 ? 1 : 0)} МБ`;
    }
    function tileHtml(file, opts = {}) {
        const f = file || {};
        const name = String(f.name || 'файл');
        const url = String(f.url || '');
        let kind = kindOf(name, f.type);
        if (kind !== 'doc' && !url) kind = 'doc';
        const color = colorOf(name, kind);
        const label = labelOf(name);
        const meta = [label, fmtSize(f.size)].filter(Boolean).join(' · ');

        let media;
        if (kind === 'image') {
            media = `<img class="fa-tile__img" src="${esc(url)}" alt="" loading="lazy" decoding="async" draggable="false">`;
        } else if (kind === 'video') {
            media = `<video class="fa-tile__video" src="${esc(url)}#t=0.1" preload="metadata" muted playsinline disablepictureinpicture tabindex="-1"></video><span class="fa-tile__badge">${IC.play}</span>`;
        } else if (kind === 'audio') {
            media = `<span class="fa-tile__audio"><button class="fa-tile__play fa-audio-play" type="button" tabindex="-1" aria-label="Воспроизвести">${IC.play}${IC.pause}</button>`
                + `<canvas class="fa-tile__viz fa-audio-viz" role="slider" tabindex="0" aria-label="Перемотка: ${esc(name)}" aria-valuemin="0" aria-valuemax="0" aria-valuenow="0" aria-valuetext="0:00 из 0:00"></canvas></span>`;
        } else {
            media = `<span class="fa-tile__label">${esc(label)}</span>`;
        }

        const cls = ['fa-chip', `fa-chip--${color}`, 'fa-tile', `fa-tile--${kind}`];
        if (opts.readonly) cls.push('fa-chip--readonly');
        if (opts.enter) cls.push('fa-tile--enter');
        const idxAttr = opts.idx != null ? ` data-idx="${Number(opts.idx)}"` : '';
        const attrs = [
            `class="${cls.join(' ')}"`, 'role="button"', 'tabindex="0"',
            `title="${esc(name)}"`, `data-kind="${kind}"`, `data-name="${esc(name)}"`,
            `data-url="${esc(url)}"`, `data-meta="${esc(meta)}"`,
        ];
        if (f.type) attrs.push(`data-mime="${esc(f.type)}"`);
        if (f.dlUrl) attrs.push(`data-dl-url="${esc(f.dlUrl)}"`);
        if (kind === 'audio') attrs.push(`data-audio-url="${esc(url)}"`, `data-audio-name="${esc(name)}"`);
        const metaHtml = kind === 'audio'
            ? `<span class="fa-tile__meta"><span class="fa-tile__meta-txt">${esc(meta)}</span><span class="fa-tile__time"><b class="cur">0:00</b><span class="fa-audio-dur"> / 0:00</span></span></span>`
            : `<span class="fa-tile__meta">${esc(meta)}</span>`;
        const removeBtn = opts.removable
            ? `<button class="fa-chip__remove" type="button" aria-label="Убрать ${esc(name)}"${idxAttr}>${IC.close}</button>`
            : '';
        return `<div ${attrs.join(' ')}${idxAttr}><span class="fa-tile__media">${media}</span>`
            + `<span class="fa-tile__cap"><span class="fa-chip__name">${esc(name)}</span>${metaHtml}</span>`
            + `${removeBtn}</div>`;
    }

    function groupHtml(files, opts = {}) {
        const list = Array.isArray(files) ? files : [];
        if (!list.length) return '';
        const tiles = list.map((f) => tileHtml(f, { readonly: true, ...opts })).join('');
        return `<div class="fa-message-files fa-tiles"><div class="fa-tiles__track">${tiles}</div></div>`;
    }

    /* ── Горизонтальная прокрутка ─────────────────────────────── */
    const trackOf = (host) => (host && host.__faTiles && host.__faTiles.track) || host;

    function enhanceScroller(host, opts = {}) {
        if (!host) return null;
        if (host.__faTiles) { host.__faTiles.update(); return host.__faTiles.track; }
        host.classList.add('fa-tiles');
        let track = host.querySelector(':scope > .fa-tiles__track');
        if (!track) {
            track = document.createElement('div');
            track.className = 'fa-tiles__track';
            Array.from(host.children).forEach((ch) => track.appendChild(ch));
            host.appendChild(track);
        }
        const mk = (dir) => {
            const b = document.createElement('button');
            b.type = 'button';
            b.tabIndex = -1;
            b.className = `fa-tiles__nav fa-tiles__nav--${dir}`;
            b.setAttribute('aria-label', dir === 'prev' ? 'Прокрутить влево' : 'Прокрутить вправо');
            b.innerHTML = dir === 'prev' ? IC.left : IC.right;
            b.addEventListener('click', (e) => {
                e.preventDefault();
                e.stopPropagation();
                const step = Math.max(140, Math.round(track.clientWidth * 0.8));
                track.scrollBy({ left: dir === 'prev' ? -step : step, behavior: 'smooth' });
            });
            host.appendChild(b);
        };
        mk('prev');
        mk('next');

        const update = () => {
            const max = track.scrollWidth - track.clientWidth;
            const over = max > 2;
            host.classList.toggle('is-overflow', over);
            host.classList.toggle('has-prev', over && track.scrollLeft > 2);
            host.classList.toggle('has-next', over && track.scrollLeft < max - 2);
        };
        track.addEventListener('scroll', update, { passive: true });
        if (opts.wheel) {
            // В полосе ввода вертикальное колесо листает плитки по горизонтали.
            track.addEventListener('wheel', (e) => {
                if (Math.abs(e.deltaY) <= Math.abs(e.deltaX)) return;
                if (track.scrollWidth - track.clientWidth <= 2) return;
                e.preventDefault();
                track.scrollLeft += e.deltaY;
            }, { passive: false });
        }
        try { new ResizeObserver(update).observe(track); } catch (_) {}
        try { new MutationObserver(() => requestAnimationFrame(update)).observe(track, { childList: true }); } catch (_) {}
        host.__faTiles = { track, update };
        requestAnimationFrame(update);
        return track;
    }

    /* ── Аудио внутри плитки: тот же матричный визуализатор, что у fa-audio-viz ── */
    function bindAudioTile(t) {
        if (!t) return null;
        if (t.dataset.audioBound === '1') return t._audioState || null;
        const anim = window.ChatAnimations;
        if (!anim || typeof anim.bindAudioChip !== 'function') return null;
        anim.bindAudioChip(t);
        const s = t._audioState;
        if (s && s.audio) {
            s.audio.addEventListener('play', () => t.classList.add('fa-tile--active'));
            s.audio.addEventListener('error', () => {
                if (!s.audio.getAttribute('src') || !t.isConnected) return;
                try { s.setPlaying(false); } catch (_) {}
                t.classList.remove('fa-tile--active');
                t.classList.add('fa-tile--broken');
                const txt = t.querySelector('.fa-tile__meta-txt');
                if (txt) txt.textContent = 'Файл недоступен';
            });
        }
        return s || null;
    }
    function bindAudioTiles(root) {
        const scope = root && root.querySelectorAll ? root : document;
        if (scope.matches && scope.matches('.fa-tile--audio')) bindAudioTile(scope);
        scope.querySelectorAll('.fa-tile--audio:not([data-audio-bound="1"])').forEach(bindAudioTile);
    }
    function stopAudioFor(el) {
        if (!el) return;
        const tiles = el.matches && el.matches('.fa-tile--audio')
            ? [el]
            : Array.from(el.querySelectorAll ? el.querySelectorAll('.fa-tile--audio') : []);
        tiles.forEach((t) => {
            const s = t._audioState;
            if (s && s.playing) { try { s.setPlaying(false); } catch (_) {} }
        });
    }
    function toggleTileAudio(t) {
        const s = bindAudioTile(t);
        if (s && typeof s.setPlaying === 'function') s.setPlaying(!s.playing);
    }
    if (typeof MutationObserver === 'function') {
        const mo = new MutationObserver((list) => {
            for (const m of list) {
                for (const n of m.addedNodes) if (n.nodeType === 1) bindAudioTiles(n);
            }
        });
        const start = () => { bindAudioTiles(document); mo.observe(document.body, { childList: true, subtree: true }); };
        if (document.body) start(); else document.addEventListener('DOMContentLoaded', start, { once: true });
    }

    /* ── Лайтбокс для картинок/видео ──────────────────────────── */
    function openLightbox(t) {
        const scope = t.closest('.fa-tiles__track') || t.parentElement || document;
        const tiles = Array.from(scope.querySelectorAll('.fa-tile[data-kind="image"], .fa-tile[data-kind="video"]'));
        const items = tiles.map((x) => ({
            isVideo: x.dataset.kind === 'video',
            url: x.dataset.url,
            dlUrl: x.dataset.dlUrl || x.dataset.url,
            name: x.dataset.name || '',
            imageKey: null,
        }));
        const idx = Math.max(0, tiles.indexOf(t));
        const app = window.__catchpmApp;
        if (app && typeof app._openMediaLightbox === 'function') {
            app._openMediaLightbox(items, idx);
        } else if (items[idx]) {
            window.open(items[idx].url, '_blank', 'noopener');
        }
    }

    function downloadDoc(t) {
        const url = t.dataset.dlUrl || t.dataset.url;
        if (!url) return;
        const name = t.dataset.name || '';
        if (url.startsWith('blob:') && OPEN_IN_TAB_EXTS.has(extOf(name))) {
            window.open(url, '_blank', 'noopener');
            return;
        }
        const a = document.createElement('a');
        a.href = url;
        a.download = name;
        a.rel = 'noopener';
        document.body.appendChild(a);
        a.click();
        a.remove();
    }

    /* ── Превью текстовых файлов в правой панели (#text-preview-panel) ── */
    const PV_MD = new Set(['md', 'markdown', 'mdx']);
    const PV_JSON = new Set(['json', 'jsonl', 'ndjson', 'geojson', 'ipynb']);
    const PV_CSV = new Set(['csv', 'tsv']);
    const PV_HTML = new Set(['html', 'htm']);
    const PV_CODE = {
        py: 'python', js: 'javascript', mjs: 'javascript', cjs: 'javascript', jsx: 'javascript',
        ts: 'typescript', tsx: 'typescript', css: 'css', scss: 'scss', less: 'less',
        sql: 'sql', sh: 'bash', bash: 'bash', zsh: 'bash', yml: 'yaml', yaml: 'yaml',
        toml: 'ini', ini: 'ini', cfg: 'ini', conf: 'ini', env: 'bash', xml: 'xml',
        go: 'go', rs: 'rust', java: 'java', kt: 'kotlin', c: 'c', h: 'c', cpp: 'cpp',
        hpp: 'cpp', cs: 'csharp', php: 'php', rb: 'ruby', swift: 'swift', lua: 'lua',
        r: 'r', diff: 'diff', patch: 'diff', dockerfile: 'dockerfile', makefile: 'makefile',
        vue: 'xml', svelte: 'xml', graphql: 'graphql', proto: 'protobuf',
    };
    const PV_TEXT = new Set(['txt', 'log', 'text', 'rst', 'tex', 'srt', 'vtt', 'gitignore', 'editorconfig']);
    const PV_MAX_BYTES = 2 * 1024 * 1024;
    const PV_HL_MAX = 200 * 1024;
    const PV_CSV_ROWS = 500;

    // Тип превью файла: md | json | csv | html | code | text | null (рендерить нечего).
    function previewTypeOf(name, mime) {
        const n = String(name || '').toLowerCase();
        const base = n.split('/').pop();
        let ext = extOf(base);
        if (!ext && (base === 'dockerfile' || base === 'makefile')) ext = base;
        if (!ext && base.startsWith('.')) ext = base.slice(1);
        const m = String(mime || '').toLowerCase();
        if (PV_MD.has(ext)) return 'md';
        if (PV_JSON.has(ext)) return 'json';
        if (PV_CSV.has(ext)) return 'csv';
        if (PV_HTML.has(ext)) return 'html';
        if (Object.prototype.hasOwnProperty.call(PV_CODE, ext)) return 'code';
        if (PV_TEXT.has(ext)) return 'text';
        if (m === 'text/markdown') return 'md';
        if (m === 'application/json') return 'json';
        if (m === 'text/csv') return 'csv';
        if (m.startsWith('text/')) return 'text';
        return null;
    }
    const PV_HAS_RENDER = new Set(['md', 'json', 'csv', 'html']);

    function hl(text, lang) {
        const h = window.hljs;
        if (h && lang && text.length <= PV_HL_MAX && h.getLanguage && h.getLanguage(lang)) {
            try { return h.highlight(text, { language: lang, ignoreIllegals: true }).value; } catch (_) {}
        }
        return esc(text);
    }

    function pvEls() {
        return {
            panel: document.getElementById('text-preview-panel'),
            body: document.getElementById('text-preview-body'),
            render: document.getElementById('text-preview-render'),
            title: document.getElementById('text-preview-title'),
            modes: document.getElementById('text-preview-modes'),
            note: document.getElementById('text-preview-note'),
        };
    }
    // Кнопка «Скачать» всегда зафиксирована в шапке text-preview-header.
    function placeDlBtn() {
        const a = document.getElementById('text-preview-dl');
        const head = document.querySelector('#text-preview-panel .text-preview-header');
        if (!a || !head) return;
        const close = document.getElementById('text-preview-close');
        if (a.parentElement !== head) head.insertBefore(a, close || null);
    }
    function setNote(text, isErr) {
        const { note } = pvEls();
        if (!note) return;
        note.hidden = !text;
        note.textContent = text || '';
        note.classList.toggle('is-error', !!isErr);
    }

    const FP = { token: 0, tile: null, type: null, text: '', loaded: false, truncated: false, totalSize: 0, name: '', modePref: {}, textChip: null, textMode: 'raw' };

    function exitFilePreview(opts = {}) {
        const { panel, body, render, modes } = pvEls();
        if (FP.tile) FP.tile.classList.remove('is-preview-open');
        const wasFile = !!(panel && panel.classList.contains('text-preview-panel--file'));
        const wasTextChip = !!FP.textChip;
        FP.token++;
        FP.tile = null; FP.type = null; FP.text = ''; FP.loaded = false;
        FP.textChip = null; FP.textMode = 'raw';
        if (!wasFile && !wasTextChip) return;
        const tok = FP.token;
        const reset = () => {
            if (tok !== FP.token) return; // за это время открыли что-то новое
            panel.classList.remove('text-preview-panel--file');
            if (body) {
                body.setAttribute('contenteditable', 'true');
                body.setAttribute('aria-label', 'Редактируемый текст');
                body.hidden = false;
                body.classList.remove('is-code');
                if (opts.deferred && wasFile && !wasTextChip) body.textContent = '';
            }
            if (render) { render.hidden = true; render.innerHTML = ''; render.className = 'text-preview-body text-preview-render'; }
            placeDlBtn(false);
            if (modes) modes.hidden = true;
            setNote('');
        };
        if (opts.deferred) setTimeout(reset, 300); else reset();
    }

    function sniffTextContent(raw) {
        const t = String(raw || '').trim();
        if (!t) return 'md';
        if ((t.startsWith('{') && t.endsWith('}')) || (t.startsWith('[') && t.endsWith(']'))) {
            try { JSON.parse(t); return 'json'; } catch (_) {}
        }
        if (/^<!doctype\s+html/i.test(t) || /^<html[\s>]/i.test(t)) return 'html';
        return 'md';
    }

    function renderTextChipPreview() {
        const { panel, body, render, title, modes } = pvEls();
        if (!panel || !body || !render) return;
        const mode = FP.textMode === 'preview' ? 'preview' : 'raw';
        if (modes) {
            modes.hidden = false;
            modes.querySelectorAll('[data-body-mode]').forEach((b) => {
                const on = b.dataset.bodyMode === mode;
                b.classList.toggle('is-active', on);
                b.setAttribute('aria-pressed', on ? 'true' : 'false');
            });
        }
        if (mode === 'raw') {
            panel.classList.remove('text-preview-panel--file');
            render.hidden = true;
            render.innerHTML = '';
            render.className = 'text-preview-body text-preview-render';
            body.hidden = false;
            body.setAttribute('contenteditable', 'true');
            placeDlBtn(false);
            setNote('');
            return;
        }
        const raw = String(body.innerText ?? body.textContent ?? '').replace(/\r\n/g, '\n');
        const tType = sniffTextContent(raw);
        const out = buildRendered(tType, raw, (title && title.textContent) || 'text.md');
        panel.classList.add('text-preview-panel--file');
        placeDlBtn(true);
        body.hidden = true;
        render.className = `text-preview-body text-preview-render text-preview-render--${out.cls}`;
        render.hidden = false;
        if (out.node) { render.innerHTML = ''; render.appendChild(out.node); } else render.innerHTML = out.html;
        if (out.after) out.after(render);
        setNote(out.note || '', !!out.err);
        render.scrollTop = 0;
    }

    function enterTextChipPreview(chipEl) {
        exitFilePreview();
        FP.textChip = chipEl || true;
        FP.textMode = 'raw';
        renderTextChipPreview();
    }

    function pvSource(t) {
        const file = t && t._faFile;
        if (file instanceof Blob) return { blob: file };
        const url = (t && (t.dataset.url || t.dataset.dlUrl)) || '';
        return { url };
    }

    async function openFilePreview(t) {
        const type = previewTypeOf(t.dataset.name, t.dataset.mime);
        if (!type) return false;
        const { panel, body, render, title, modes } = pvEls();
        if (!panel || !body || !render) return false;
        const app = window.__catchpmApp;
        // Закрыть чип вставленного текста, если он был открыт в панели.
        if (app && app._activePreviewChipEl) {
            app._activePreviewChipEl.classList.remove('is-preview-open');
            app._activePreviewChipEl = null;
            app._activePreviewChipId = null;
        }
        if (FP.tile && FP.tile !== t) FP.tile.classList.remove('is-preview-open');
        const tok = ++FP.token;
        FP.tile = t; FP.type = type; FP.text = ''; FP.loaded = false;
        FP.textChip = null; FP.textMode = 'raw';
        FP.truncated = false; FP.totalSize = 0; FP.name = t.dataset.name || 'файл';
        t.classList.add('is-preview-open');

        panel.classList.add('text-preview-panel--file');
        placeDlBtn(true);
        body.setAttribute('contenteditable', 'false');
        body.setAttribute('aria-label', 'Содержимое файла');
        if (title) title.textContent = FP.name;
        if (modes) modes.hidden = !PV_HAS_RENDER.has(type);
        syncModeBtns();
        render.hidden = true; render.innerHTML = '';
        body.hidden = false; body.classList.remove('is-code');
        body.textContent = 'Загружаю…';
        setNote('');
        panel.classList.add('open');
        panel.setAttribute('aria-hidden', 'false');

        let text = '';
        try {
            const src = pvSource(t);
            let blob = src.blob || null;
            if (!blob) {
                if (!src.url) throw new Error('нет ссылки на файл');
                const headers = src.url.startsWith('blob:') ? {} : { Range: `bytes=0-${PV_MAX_BYTES - 1}` };
                const r = await fetch(src.url, { headers, credentials: 'same-origin' });
                if (!r.ok) throw new Error(`HTTP ${r.status}`);
                const cr = r.headers.get('Content-Range');
                const m = cr && cr.match(/\/(\d+)\s*$/);
                blob = await r.blob();
                FP.totalSize = m ? Number(m[1]) : blob.size;
            } else {
                FP.totalSize = blob.size;
            }
            if (blob.size > PV_MAX_BYTES) blob = blob.slice(0, PV_MAX_BYTES);
            FP.truncated = FP.totalSize > PV_MAX_BYTES;
            text = await blob.text();
        } catch (err) {
            if (tok !== FP.token) return true;
            body.textContent = '';
            setNote(`Не удалось загрузить файл: ${err && err.message ? err.message : err}`, true);
            return true;
        }
        if (tok !== FP.token) return true;
        if (text.slice(0, 4096).includes('\u0000')) {
            body.textContent = '';
            if (modes) modes.hidden = true;
            setNote('Похоже на бинарный файл, текстовое превью недоступно.', true);
            return true;
        }
        FP.text = text.replace(/\r\n/g, '\n');
        FP.loaded = true;
        renderFilePreview();
        body.scrollTop = 0; render.scrollTop = 0;
        return true;
    }

    function curMode() {
        if (!PV_HAS_RENDER.has(FP.type)) return 'preview';
        return FP.modePref[FP.type] || 'preview';
    }
    function syncModeBtns() {
        const { modes } = pvEls();
        if (!modes) return;
        const mode = curMode();
        modes.querySelectorAll('[data-body-mode]').forEach((b) => {
            const on = b.dataset.bodyMode === mode;
            b.classList.toggle('is-active', on);
            b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
    }

    function renderFilePreview() {
        const { body, render } = pvEls();
        if (!FP.loaded || !body || !render) return;
        const mode = curMode();
        syncModeBtns();
        const sizeNote = FP.truncated
            ? `Показаны первые ${fmtSize(PV_MAX_BYTES)} из ${fmtSize(FP.totalSize)}. Полный файл можно скачать.`
            : '';
        if (mode === 'raw' && PV_HAS_RENDER.has(FP.type)) {
            render.hidden = true; render.innerHTML = '';
            body.hidden = false;
            body.classList.remove('is-code');
            body.textContent = FP.text;
            setNote(sizeNote);
            return;
        }
        const out = buildRendered(FP.type, FP.text, FP.name);
        body.hidden = true; body.textContent = '';
        render.className = `text-preview-body text-preview-render text-preview-render--${out.cls}`;
        render.hidden = false;
        if (out.node) { render.innerHTML = ''; render.appendChild(out.node); } else render.innerHTML = out.html;
        if (out.after) out.after(render);
        setNote([out.note, sizeNote].filter(Boolean).join(' '), !!out.err);
    }

    function sniffDelim(text, ext) {
        if (ext === 'tsv') return '\t';
        const first = text.split('\n', 1)[0] || '';
        let best = ',', bestN = 0;
        for (const d of [',', ';', '\t', '|']) {
            const n = first.split(d).length - 1;
            if (n > bestN) { best = d; bestN = n; }
        }
        return best;
    }
    function parseCsv(text, delim, maxRows) {
        const rows = [];
        let row = [], cell = '', q = false, i = 0;
        const n = text.length;
        while (i < n) {
            const c = text[i];
            if (q) {
                if (c === '"') {
                    if (text[i + 1] === '"') { cell += '"'; i += 2; continue; }
                    q = false; i++; continue;
                }
                cell += c; i++; continue;
            }
            if (c === '"' && cell === '') { q = true; i++; continue; }
            if (c === delim) { row.push(cell); cell = ''; i++; continue; }
            if (c === '\n') {
                row.push(cell); rows.push(row); row = []; cell = ''; i++;
                if (rows.length > maxRows) return { rows: rows.slice(0, maxRows), more: true };
                continue;
            }
            cell += c; i++;
        }
        if (cell !== '' || row.length) { row.push(cell); rows.push(row); }
        const more = rows.length > maxRows;
        return { rows: more ? rows.slice(0, maxRows) : rows, more };
    }

    function buildRendered(type, text, name) {
        const ext = extOf(name);
        if (type === 'md') {
            let html;
            try {
                html = window.marked ? window.marked.parse(text) : `<pre>${esc(text)}</pre>`;
                if (window.DOMPurify) html = window.DOMPurify.sanitize(html);
                else if (window.marked) html = `<pre>${esc(text)}</pre>`;
            } catch (_) { html = `<pre>${esc(text)}</pre>`; }
            return {
                cls: 'md', html,
                after: (root) => {
                    root.querySelectorAll('a[href]').forEach((a) => { a.target = '_blank'; a.rel = 'noopener noreferrer'; });
                    if (window.hljs && text.length <= PV_HL_MAX) {
                        root.querySelectorAll('pre code').forEach((c) => { try { window.hljs.highlightElement(c); } catch (_) {} });
                    }
                },
            };
        }
        if (type === 'json') {
            try {
                let pretty;
                if (ext === 'jsonl' || ext === 'ndjson') {
                    pretty = text.split('\n').filter((l) => l.trim())
                        .map((l) => JSON.stringify(JSON.parse(l), null, 2)).join('\n\n');
                } else {
                    pretty = JSON.stringify(JSON.parse(text), null, 2);
                }
                return { cls: 'code', html: `<pre class="tp-code"><code class="hljs language-json">${hl(pretty, 'json')}</code></pre>` };
            } catch (err) {
                return {
                    cls: 'code', err: true,
                    html: `<pre class="tp-code"><code class="hljs">${esc(text)}</code></pre>`,
                    note: `JSON не разобран: ${err && err.message ? err.message : err}`,
                };
            }
        }
        if (type === 'csv') {
            const { rows, more } = parseCsv(text, sniffDelim(text, ext), PV_CSV_ROWS + 1);
            if (!rows.length) return { cls: 'md', html: '<p>Файл пуст.</p>' };
            const [head, ...data] = rows;
            const cols = rows.reduce((m, r) => Math.max(m, r.length), 0);
            const NUM = /^\s*[-+]?[\d\s]*[.,]?\d+\s*%?\s*$/;
            const isNum = Array.from({ length: cols }, (_, i) => {
                const vals = data.map((r) => r[i]).filter((v) => v != null && String(v).trim() !== '');
                return vals.length > 0 && vals.every((v) => NUM.test(v));
            });
            const nc = (i) => (isNum[i] ? ' class="tp-num"' : '');
            const th = Array.from({ length: cols }, (_, i) => `<th${nc(i)}>${esc(head[i] ?? '')}</th>`).join('');
            const tb = data.map((r) => `<tr>${Array.from({ length: cols }, (_, i) => `<td${nc(i)}>${esc(r[i] ?? '')}</td>`).join('')}</tr>`).join('');
            const note = more ? `Показаны первые ${PV_CSV_ROWS} строк.` : `Строк: ${data.length}, столбцов: ${cols}.`;
            return { cls: 'table', html: `<table class="tp-table"><thead><tr>${th}</tr></thead><tbody>${tb}</tbody></table>`, note };
        }
        if (type === 'html') {
            const fr = document.createElement('iframe');
            fr.className = 'tp-frame';
            fr.setAttribute('sandbox', '');
            fr.setAttribute('referrerpolicy', 'no-referrer');
            fr.title = name;
            const csp = '<meta http-equiv="Content-Security-Policy" content="default-src \'none\'; img-src data: blob:; style-src \'unsafe-inline\'; font-src data:">';
            fr.srcdoc = csp + text;
            return { cls: 'frame', node: fr, note: 'Скрипты и внешние ресурсы отключены.' };
        }
        // code | text
        if (type === 'code') {
            let base = String(name || '').toLowerCase().split('/').pop();
            let e = extOf(base) || base;
            const lang = PV_CODE[e] || '';
            return { cls: 'code', html: `<pre class="tp-code"><code class="hljs${lang ? ' language-' + lang : ''}">${hl(text, lang)}</code></pre>` };
        }
        return { cls: 'text', html: `<pre class="tp-plain">${esc(text)}</pre>` };
    }

    // Переключатель «Превью / Исходник» в шапке панели.
    document.addEventListener('click', (e) => {
        const btn = e.target && e.target.closest ? e.target.closest('#text-preview-modes [data-body-mode]') : null;
        if (!btn) return;
        if (FP.type) {
            e.preventDefault();
            const next = btn.dataset.bodyMode === 'raw' ? 'raw' : 'preview';
            if (curMode() === next) return;
            FP.modePref[FP.type] = next;
            renderFilePreview();
            return;
        }
        if (FP.textChip) {
            e.preventDefault();
            const next = btn.dataset.bodyMode === 'raw' ? 'raw' : 'preview';
            if (FP.textMode === next) return;
            FP.textMode = next;
            renderTextChipPreview();
        }
    });

    // Плитка без рендера в поле ввода: вместо скачивания коротко показываем «Нет превью».
    function flashNoPreview(t) {
        const meta = t.querySelector('.fa-tile__meta');
        if (!meta || t.classList.contains('fa-tile--nopreview')) return;
        const prev = meta.textContent;
        t.classList.add('fa-tile--nopreview');
        meta.textContent = 'Нет превью';
        setTimeout(() => {
            t.classList.remove('fa-tile--nopreview');
            if (meta.isConnected) meta.textContent = t.dataset.meta || prev;
        }, 1400);
    }

    function activate(t, e) {
        if (t.classList.contains('fa-tile--broken')) return;
        const kind = t.dataset.kind;
        if (kind === 'image' || kind === 'video') return openLightbox(t);
        if (kind === 'audio') return toggleTileAudio(t);
        if (previewTypeOf(t.dataset.name, t.dataset.mime)) { openFilePreview(t); return; }
        // Только что приложенный файл повторно не скачиваем.
        if (t.closest('.fa-preview-bar')) {
            const url = t.dataset.dlUrl || t.dataset.url || '';
            if (extOf(t.dataset.name) === 'pdf' && url) { window.open(url, '_blank', 'noopener'); return; }
            return flashNoPreview(t);
        }
        return downloadDoc(t);
    }

    // Плитку убрали из DOM (крестик, отправка) — закрываем её превью.
    new MutationObserver(() => {
        if (FP.tile && !FP.tile.isConnected) {
            const app = window.__catchpmApp;
            if (app && typeof app.closeTextPreview === 'function') app.closeTextPreview();
            else exitFilePreview({ deferred: true });
        }
    }).observe(document.documentElement, { childList: true, subtree: true });

    const isFileTile = (t) => !!t && !t.classList.contains('fa-chip--text') && !t.classList.contains('input-chip');

    document.addEventListener('click', (e) => {
        const t = e.target && e.target.closest ? e.target.closest('.fa-tile') : null;
        if (!isFileTile(t)) return;
        if (e.target.closest('.fa-chip__remove, .fa-tiles__nav, .fa-audio-viz, .fa-audio-play')) return;
        e.preventDefault();
        activate(t, e);
    });
    document.addEventListener('keydown', (e) => {
        if (e.key !== 'Enter' && e.key !== ' ') return;
        const t = e.target;
        if (!(t instanceof HTMLElement) || !t.classList.contains('fa-tile') || !isFileTile(t)) return;
        e.preventDefault();
        activate(t, null);
    });
    document.addEventListener('animationend', (e) => {
        const t = e.target;
        if (t && t.classList && t.classList.contains('fa-tile--enter')) t.classList.remove('fa-tile--enter');
    });
    // Превью не загрузилось (файл удалён/не сохранён) — плитка становится «документом».
    document.addEventListener('error', (e) => {
        const el = e.target;
        if (!el || !el.classList || !(el.classList.contains('fa-tile__img') || el.classList.contains('fa-tile__video'))) return;
        const t = el.closest('.fa-tile');
        if (!t || t.classList.contains('fa-tile--broken')) return;
        const media = t.querySelector('.fa-tile__media');
        if (media) media.innerHTML = `<span class="fa-tile__label">${esc(labelOf(t.dataset.name))}</span>`;
        t.classList.remove('fa-tile--image', 'fa-tile--video');
        t.classList.add('fa-tile--doc', 'fa-tile--broken');
        t.dataset.kind = 'doc';
        t.title = `${t.dataset.name || ''} — файл недоступен`;
        t.dataset.meta = 'Файл недоступен';
        const meta = t.querySelector('.fa-tile__meta');
        if (meta) meta.textContent = 'Файл недоступен';
    }, true);

    /* ── Скачать вставленный текст из панели предпросмотра (#text-preview-dl) ── */
    let _textDlUrl = '';
    function textFileName(text) {
        const first = (String(text).split('\n').find((l) => l.trim()) || '').trim();
        let base = first.replace(/[\\/:*?"<>|\u0000-\u001f]+/g, ' ').replace(/\s+/g, ' ').trim().slice(0, 48).trim();
        if (!base) {
            const d = new Date();
            const p2 = (n) => String(n).padStart(2, '0');
            base = `text-${d.getFullYear()}${p2(d.getMonth() + 1)}${p2(d.getDate())}-${p2(d.getHours())}${p2(d.getMinutes())}`;
        }
        return base.replace(/[.\s]+$/, '') + '.txt';
    }
    document.addEventListener('click', (e) => {
        const a = e.target && e.target.closest ? e.target.closest('#text-preview-dl') : null;
        if (!a) return;
        e.stopPropagation();
        if (FP.tile) {
            const t = FP.tile;
            const file = t._faFile;
            let url = t.dataset.dlUrl || t.dataset.url || '';
            if (!url && file instanceof Blob) {
                if (_textDlUrl) { try { URL.revokeObjectURL(_textDlUrl); } catch (_) {} }
                url = _textDlUrl = URL.createObjectURL(file);
            }
            if (!url) { e.preventDefault(); return; }
            a.href = url;
            a.download = t.dataset.name || 'file';
            return;
        }
        const body = document.getElementById('text-preview-body');
        const text = body ? (body.innerText || body.textContent || '') : '';
        if (!text.trim()) { e.preventDefault(); return; }
        if (_textDlUrl) { try { URL.revokeObjectURL(_textDlUrl); } catch (_) {} }
        _textDlUrl = URL.createObjectURL(new Blob([text], { type: 'text/plain;charset=utf-8' }));
        a.href = _textDlUrl;
        a.download = textFileName(text);
    }, true);

    window.FileTiles = {
        tileHtml, groupHtml, enhanceScroller, trackOf, stopAudioFor, bindAudioTiles,
        kindOf, labelOf, fmtSize,
        previewTypeOf, openFilePreview, exitFilePreview, enterTextChipPreview,
    };
})();
