/* Единый лоадер медиа «Кольцо» для всех интерфейсов (личный чат, Sigma, общий чат).
   Разметка — inline SVG, анимация и цвета — .fa-ring в chat.css (дуга от --accent).
   Обёртка .fa-media-loader центрирует кольцо на подложке.

   Прогресс: если известен размер файла (chat_files.file_size, Content-Length),
   кольцо из «крутилки» становится заполняемым (.fa-ring--det, --ring-fill 0..1),
   под ним подпись «0,8 / 2,0 МБ».
   - картинки: fetch + чтение потока (loadImage) → blob: URL в <img>;
   - видео: доля buffered от duration (bindVideo), байты — оценка от размера. */
(function () {
  const IMG = '<rect class="fa-ring__glyph" x="-7" y="-5.5" width="14" height="11" rx="2"/>'
    + '<path class="fa-ring__cut" d="M-5.5 4l3.5-4 2.5 2.5 2-2 3 3.5z"/>';
  const PLAY = '<path class="fa-ring__glyph" d="M-4 -5.5v11l9-5.5z"/>';

  function html(isVideo = false) {
    return '<svg class="fa-ring" viewBox="0 0 48 48" aria-hidden="true">'
      + '<circle class="fa-ring__track" cx="24" cy="24" r="20"/>'
      + '<circle class="fa-ring__arc" cx="24" cy="24" r="20"/>'
      + '<g class="fa-ring__icon" transform="translate(24 24)"><g class="fa-ring__pulse">'
      + (isVideo ? PLAY : IMG) + '</g></g></svg>';
  }

  /* размер из БД приходит то числом, то строкой ("2072640") */
  function toBytes(v) {
    const n = Number(v);
    return Number.isFinite(n) && n > 0 ? n : 0;
  }

  function fmt(bytes) {
    const b = Math.max(0, bytes || 0);
    if (b < 1024 * 1024) return Math.max(1, Math.round(b / 1024)) + ' КБ';
    return (b / 1024 / 1024).toFixed(1).replace('.', ',') + ' МБ';
  }
  function fmtPair(loaded, total) {
    if (total < 1024 * 1024) return fmt(loaded) + ' / ' + fmt(total);
    const mb = (x) => (x / 1024 / 1024).toFixed(1).replace('.', ',');
    return mb(loaded) + ' / ' + mb(total) + ' МБ';
  }

  function labelOf(root, create) {
    let el = root.querySelector(':scope > .fa-ring-size');
    if (!el && create) {
      el = document.createElement('span');
      el.className = 'fa-ring-size';
      root.appendChild(el);
    }
    return el;
  }

  /* frac: 0..1 или null (неизвестно → крутилка); text: подпись или '' */
  function paint(root, frac, text) {
    if (!root) return;
    const ring = root.querySelector('.fa-ring');
    if (ring) {
      if (frac == null) {
        ring.classList.remove('fa-ring--det');
        ring.style.removeProperty('--ring-fill');
      } else {
        ring.classList.add('fa-ring--det');
        ring.style.setProperty('--ring-fill', Math.max(0.02, Math.min(1, frac)).toFixed(4));
      }
    }
    const lbl = labelOf(root, !!text);
    if (lbl) lbl.textContent = text || '';
  }

  /* loaded/total в байтах. Пока байтов нет — крутилка + полный размер. */
  function setProgress(root, loaded, total) {
    total = toBytes(total);
    if (!total) { paint(root, null, ''); return; }
    if (!loaded) { paint(root, null, fmt(total)); return; }
    paint(root, loaded / total, fmtPair(Math.min(loaded, total), total));
  }

  function reset(root) { paint(root, null, ''); }

  /* rAF-троттлинг: на быстром канале чанки идут сотнями */
  function throttled(fn) {
    let pending = null, raf = 0;
    const run = () => { raf = 0; const a = pending; pending = null; if (a) fn.apply(null, a); };
    const call = function () {
      pending = arguments;
      if (!raf) raf = (window.requestAnimationFrame || ((f) => setTimeout(f, 16)))(run);
    };
    call.flush = () => { if (raf && window.cancelAnimationFrame) window.cancelAnimationFrame(raf); run(); };
    return call;
  }

  /* Кеш blob: URL по исходному url (LRU, бюджет по байтам и числу записей).
     Перерисовка ленты (innerHTML, смена чата) берёт картинку из кеша без
     повторной загрузки; вытесненные записи отзываются. */
  const cache = new Map();
  const MAX_BYTES = 120 * 1024 * 1024;
  const MAX_ENTRIES = 300;
  let cacheBytes = 0;
  function evict() {
    for (const [k, e] of cache) {
      if (cacheBytes <= MAX_BYTES && cache.size <= MAX_ENTRIES) break;
      cache.delete(k);
      cacheBytes -= e.size;
      try { URL.revokeObjectURL(e.objUrl); } catch (_) {}
    }
  }
  function track(srcUrl, objUrl, size, key) {
    const prev = cache.get(srcUrl);
    if (prev) {
      cache.delete(srcUrl);
      cacheBytes -= prev.size;
      if (prev.objUrl !== objUrl) { try { URL.revokeObjectURL(prev.objUrl); } catch (_) {} }
    }
    const s = toBytes(size);
    cache.set(srcUrl, { objUrl, size: s, key: key || null });
    cacheBytes += s;
    evict();
  }
  function hitEntry(srcUrl) {
    const e = cache.get(srcUrl);
    if (!e) return null;
    cache.delete(srcUrl); cache.set(srcUrl, e);   // свежесть для LRU
    return e;
  }
  function cached(srcUrl) { const e = hitEntry(srcUrl); return e ? e.objUrl : null; }
  function keyOf(srcUrl) { const e = cache.get(srcUrl); return e ? e.key : null; }

  const canStream = typeof window.ReadableStream !== 'undefined'
    && typeof window.Response !== 'undefined'
    && 'body' in window.Response.prototype;

  /* fetch с прогрессом. onProgress(loaded, total). Возвращает {blob, imageKey}.
     total: известный размер, иначе Content-Length (если ответ не сжат). */
  async function fetchProgress(url, opts) {
    const o = opts || {};
    const resp = await fetch(url, { credentials: 'same-origin', signal: o.signal });
    if (!resp.ok) throw new Error('HTTP ' + resp.status);
    const imageKey = resp.headers.get('X-Image-Key');
    const enc = (resp.headers.get('Content-Encoding') || '').toLowerCase();
    const cl = enc && enc !== 'identity' ? 0 : toBytes(resp.headers.get('Content-Length'));
    const total = toBytes(o.total) || cl;
    const report = o.onProgress ? throttled(o.onProgress) : null;
    if (report) report(0, total);
    if (!canStream || !resp.body || !resp.body.getReader) {
      const blob = await resp.blob();
      if (report) { report(blob.size, total || blob.size); report.flush(); }
      return { blob, imageKey };
    }
    const reader = resp.body.getReader();
    const chunks = [];
    let loaded = 0;
    for (;;) {
      const { done, value } = await reader.read();
      if (done) break;
      chunks.push(value);
      loaded += value.byteLength;
      if (report) report(loaded, total);
    }
    if (report) { report(loaded, total || loaded); report.flush(); }
    const type = resp.headers.get('Content-Type') || '';
    return { blob: new Blob(chunks, type ? { type } : undefined), imageKey };
  }

  /* Картинка с прогрессом на кольце.
     img — <img> (src ещё не задан), root — .fa-media-loader с кольцом,
     opts: { total, lazy (по умолчанию по img.loading==='lazy'), onKey }.
     Любая ошибка fetch → img.src = url: дальше работают штатные load/error
     и фоллбэки вызывающего кода. */
  function loadImage(img, url, root, opts) {
    const o = opts || {};
    if (!img) return;
    if (!url || url === '#' || /^(blob|data):/.test(url)) { img.src = url; return; }
    const hit = hitEntry(url);
    if (hit) {
      if (o.onKey && hit.key) { try { o.onKey(hit.key); } catch (_) {} }
      img.src = hit.objUrl;
      return;
    }
    const total = toBytes(o.total);
    setProgress(root, 0, total);
    const start = () => {
      if (typeof window.fetch !== 'function') { img.src = url; return; }
      fetchProgress(url, {
        total,
        onProgress: (l, t) => setProgress(root, l, t),
      }).then(({ blob, imageKey }) => {
        const objUrl = URL.createObjectURL(blob);
        track(url, objUrl, blob.size, imageKey);
        if (o.onKey && imageKey) { try { o.onKey(imageKey); } catch (_) {} }
        img.src = objUrl;
      }).catch(() => {
        reset(root);
        img.src = url;
      });
    };
    const lazy = o.lazy != null ? o.lazy : img.loading === 'lazy';
    if (lazy && 'IntersectionObserver' in window) {
      const io = new IntersectionObserver((entries) => {
        if (entries.some((e) => e.isIntersecting)) { io.disconnect(); start(); }
      }, { rootMargin: '600px 0px' });
      // наблюдаем лоадер: у <img> до загрузки может быть 0×0
      io.observe(root || img.parentElement || img);
    } else {
      start();
    }
  }

  /* Видео: прогресс по buffered/duration. Байты в подписи — оценка от total. */
  function bindVideo(video, root, totalBytes) {
    if (!video || !root) return;
    const total = toBytes(totalBytes);
    setProgress(root, 0, total);
    const upd = throttled(() => {
      const d = video.duration;
      const b = video.buffered;
      if (!b || !b.length || !Number.isFinite(d) || d <= 0) return;
      const frac = Math.min(1, b.end(b.length - 1) / d);
      if (total) setProgress(root, Math.round(frac * total), total);
      else paint(root, frac, '');
    });
    video.addEventListener('progress', upd);
    video.addEventListener('loadedmetadata', upd);
    video.addEventListener('error', () => reset(root), { once: true });
  }

  window.MediaRing = {
    html, setProgress, reset, fmt, toBytes,
    fetchProgress, loadImage, bindVideo, cached, keyOf, track,
  };
})();
