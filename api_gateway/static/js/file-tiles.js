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

    function activate(t, e) {
        if (t.classList.contains('fa-tile--broken')) return;
        const kind = t.dataset.kind;
        if (kind === 'image' || kind === 'video') return openLightbox(t);
        if (kind === 'audio') return toggleTileAudio(t);
        return downloadDoc(t);
    }

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
    };
})();
