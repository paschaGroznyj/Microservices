/**
 * UploadProgress — анимация загрузки вложений после нажатия «Отправить».
 * Используется основным чатом (app.js -> FileAttach.upload) и общим чатом (groupChat.js).
 *
 *   UploadProgress.send(url, formData, {onProgress, onSent}) -> Promise<{ok,status,json(),clone()}>
 *       XHR вместо fetch: только у XHR есть upload.onprogress.
 *       Сетевой сбой -> reject (как у fetch).
 *   UploadProgress.track(getRoot) -> tracker
 *       getRoot(): узел сообщения (или null, если его нет в DOM).
 *       tracker.progress(0..1) | sent() | done() | fail(msg) | repaint()
 *   UploadProgress.paint(root, state)  — нарисовать state на узле (для ререндеров).
 */
const UploadProgress = (() => {
    const R = 15;
    const C = 2 * Math.PI * R;
    const HOSTS = '.fa-tile__media, .gc-collage__cell, .gc-file, .fa-chip--audio, .fa-chip:not(.fa-tile)';

    function send(url, formData, opts = {}) {
        return new Promise((resolve, reject) => {
            const xhr = new XMLHttpRequest();
            xhr.open('POST', url, true);
            xhr.withCredentials = true;
            xhr.upload.onprogress = (e) => {
                if (e.lengthComputable && e.total > 0) opts.onProgress?.(e.loaded / e.total);
            };
            // Байты ушли, сервер ещё раскладывает файлы (OBS/Redis).
            xhr.upload.onload = () => { opts.onProgress?.(1); opts.onSent?.(); };
            xhr.onload = () => {
                const text = xhr.responseText || '';
                const res = {
                    ok: xhr.status >= 200 && xhr.status < 300,
                    status: xhr.status,
                    text: async () => text,
                    json: async () => JSON.parse(text || 'null'),
                };
                res.clone = () => res;
                resolve(res);
            };
            xhr.onerror = () => reject(new TypeError('Network error'));
            xhr.onabort = () => reject(new DOMException('Aborted', 'AbortError'));
            xhr.send(formData);
        });
    }

    function _hostsOf(root) {
        if (!root) return [];
        const seen = new Set();
        const out = [];
        root.querySelectorAll(HOSTS).forEach((el) => {
            // fa-tile__media внутри плитки: сам .fa-chip.fa-tile в выборку не попадает,
            // а вложенные чипы внутри уже выбранного хоста пропускаем.
            for (const s of seen) if (s.contains(el)) return;
            seen.add(el);
            out.push(el);
        });
        return out;
    }

    function _isCompact(host) {
        return host.matches('.gc-file, .fa-chip--audio, .fa-chip:not(.fa-tile)');
    }

    const ICON_OK = '<svg class="up-ov__icon up-ov__icon--ok" viewBox="0 0 24 24" aria-hidden="true"><path d="M6 12.5l4 4 8-9" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"/></svg>';
    const ICON_ERR = '<svg class="up-ov__icon up-ov__icon--err" viewBox="0 0 24 24" aria-hidden="true"><path d="M12 7v6.5M12 17v.01" fill="none" stroke="currentColor" stroke-width="2.4" stroke-linecap="round"/></svg>';

    function _build(host, compact, st) {
        const ov = document.createElement('span');
        ov.className = 'up-ov' + (compact ? ' up-ov--compact' : '') + (st._shown ? ' up-ov--instant' : '');
        const off = (C * (1 - Math.min(1, st.pct || 0))).toFixed(2);
        ov.setAttribute('role', 'progressbar');
        ov.setAttribute('aria-label', 'Загрузка файла');
        ov.setAttribute('aria-valuemin', '0');
        ov.setAttribute('aria-valuemax', '100');
        if (compact) {
            ov.innerHTML = '<span class="up-ov__line"><span class="up-ov__fill"></span></span>' + ICON_ERR;
        } else {
            ov.innerHTML =
                '<span class="up-ov__ring">'
                + `<svg viewBox="0 0 36 36" aria-hidden="true"><circle class="up-ov__track" cx="18" cy="18" r="${R}"/>`
                + `<circle class="up-ov__bar" cx="18" cy="18" r="${R}" stroke-dasharray="${C.toFixed(2)}" stroke-dashoffset="${off}"/></svg>`
                + ICON_OK + ICON_ERR
                + '<span class="up-ov__pct">0%</span></span>'
                + '<span class="up-ov__label"></span>';
        }
        if (getComputedStyle(host).position === 'static') host.classList.add('up-host--rel');
        host.classList.add('up-host');
        host.appendChild(ov);
        return ov;
    }

    function _paintOne(host, st) {
        const compact = _isCompact(host);
        let ov = host.querySelector(':scope > .up-ov');
        if (!ov) ov = _build(host, compact, st);
        const pct = Math.max(0, Math.min(100, Math.round((st.pct || 0) * 100)));
        ov.dataset.phase = st.phase;
        ov.setAttribute('aria-valuenow', String(pct));
        if (st.phase === 'error') {
            ov.setAttribute('aria-label', st.error || 'Файл не загружен');
            ov.title = st.error || 'Файл не загружен';
        } else {
            ov.setAttribute('aria-label', st.phase === 'processing' ? 'Обработка файла' : `Загрузка файла: ${pct}%`);
            ov.removeAttribute('title');
        }
        if (compact) {
            ov.querySelector('.up-ov__fill').style.width = pct + '%';
        } else {
            ov.querySelector('.up-ov__bar').setAttribute('stroke-dashoffset', (C * (1 - pct / 100)).toFixed(2));
            ov.querySelector('.up-ov__pct').textContent = pct + '%';
            ov.querySelector('.up-ov__label').textContent =
                st.phase === 'processing' ? 'Обработка…' : st.phase === 'error' ? 'Не загружено' : '';
        }
    }

    function paint(root, st) {
        if (!root || !st) return;
        const hosts = _hostsOf(root);
        if (st.phase === 'gone') {
            hosts.forEach((h) => _clearHost(h));
            return;
        }
        root.setAttribute('aria-busy', st.phase === 'upload' || st.phase === 'processing' ? 'true' : 'false');
        hosts.forEach((h) => _paintOne(h, st));
        // ререндер ленты пересоздаёт оверлей: без повторного fade-in и мигания
        if (hosts.length) st._shown = true;
        if (st.phase === 'done') {
            hosts.forEach((h) => {
                const ov = h.querySelector(':scope > .up-ov');
                if (!ov || ov._upHide) return;
                ov._upHide = setTimeout(() => {
                    ov.classList.add('up-ov--hide');
                    setTimeout(() => _clearHost(h), 260);
                }, 650);
            });
        }
    }

    function _clearHost(h) {
        h.querySelector(':scope > .up-ov')?.remove();
        h.classList.remove('up-host', 'up-host--rel');
    }

    function track(getRoot) {
        const st = { phase: 'upload', pct: 0, error: '' };
        let raf = 0;
        const flush = () => { raf = 0; paint(getRoot(), st); };
        const schedule = () => { if (!raf) raf = requestAnimationFrame(flush); };
        const t = {
            state: st,
            progress(p) {
                if (st.phase !== 'upload') return;
                st.pct = Math.max(st.pct, Number(p) || 0);
                schedule();
            },
            sent() {
                if (st.phase !== 'upload') return;
                st.pct = 1; st.phase = 'processing'; schedule();
            },
            done() {
                if (st.phase === 'error') return;
                st.pct = 1; st.phase = 'done'; schedule();
                setTimeout(() => { st.phase = 'gone'; }, 1000);
            },
            fail(msg) {
                if (st.phase === 'done' || st.phase === 'gone') return;
                st.phase = 'error'; st.error = msg || 'Файл не загружен'; schedule();
            },
            repaint() { paint(getRoot(), st); },
            get finished() { return st.phase === 'done' || st.phase === 'gone' || st.phase === 'error'; },
        };
        schedule();
        return t;
    }

    return { send, track, paint };
})();

window.UploadProgress = UploadProgress;
