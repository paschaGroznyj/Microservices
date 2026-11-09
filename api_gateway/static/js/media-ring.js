/* Единый лоадер медиа «Кольцо» для всех интерфейсов (личный чат, Sigma, общий чат).
   Разметка — inline SVG, анимация и цвета — .fa-ring в chat.css (дуга от --accent).
   Обёртка .fa-media-loader центрирует кольцо на подложке.

   Прогресс: если известен размер файла (chat_files.file_size, Content-Length),
   кольцо из «крутилки» становится заполняемым (.fa-ring--det, --ring-fill 0..1),
   под ним подпись «0,8 / 2,0 МБ».
   - картинки: fetch + чтение потока (loadImage) → blob: URL в <img>;
   - видео: доля buffered от duration (bindVideo), байты — оценка от размера.
   Фон: если есть chat_files.thumbhash — размытая заглушка (placeholder), иначе surface2. */
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
     opts: { total, lazy (по умолчанию по img.loading==='lazy'), onKey, hash (ThumbHash base64) }.
     Любая ошибка fetch → img.src = url: дальше работают штатные load/error
     и фоллбэки вызывающего кода. */
  function loadImage(img, url, root, opts) {
    const o = opts || {};
    if (!img) return;
    if (o.hash) placeholder(root, o.hash);
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
  function bindVideo(video, root, totalBytes, hash) {
    if (!video || !root) return;
    if (hash) placeholder(root, hash);
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


  /* ThumbHash → размытая заглушка под кольцом (chat_files.thumbhash, base64).
     Декодер — порт thumbHashToRGBA из github.com/evanw/thumbhash (MIT, Evan Wallace).
     Картинка 32 px рисуется в canvas → data: URL, CSS растягивает и размывает. */
  function thumbHashToRGBA(hash) {
    const { PI, min, max, cos, round } = Math;
    const header24 = hash[0] | (hash[1] << 8) | (hash[2] << 16);
    const header16 = hash[3] | (hash[4] << 8);
    const l_dc = (header24 & 63) / 63;
    const p_dc = ((header24 >> 6) & 63) / 31.5 - 1;
    const q_dc = ((header24 >> 12) & 63) / 31.5 - 1;
    const l_scale = ((header24 >> 18) & 31) / 31;
    const hasAlpha = header24 >> 23;
    const p_scale = ((header16 >> 3) & 63) / 63;
    const q_scale = ((header16 >> 9) & 63) / 63;
    const isLandscape = header16 >> 15;
    const lx = max(3, isLandscape ? (hasAlpha ? 5 : 7) : header16 & 7);
    const ly = max(3, isLandscape ? header16 & 7 : (hasAlpha ? 5 : 7));
    const a_dc = hasAlpha ? (hash[5] & 15) / 15 : 1;
    const a_scale = (hash[5] >> 4) / 15;
    const ac_start = hasAlpha ? 6 : 5;
    let ac_index = 0;
    const decodeChannel = (nx, ny, scale) => {
      const ac = [];
      for (let cy = 0; cy < ny; cy++)
        for (let cx = cy ? 0 : 1; cx * ny < nx * (ny - cy); cx++)
          ac.push((((hash[ac_start + (ac_index >> 1)] >> ((ac_index++ & 1) << 2)) & 15) / 7.5 - 1) * scale);
      return ac;
    };
    const l_ac = decodeChannel(lx, ly, l_scale);
    const p_ac = decodeChannel(3, 3, p_scale * 1.25);
    const q_ac = decodeChannel(3, 3, q_scale * 1.25);
    const a_ac = hasAlpha && decodeChannel(5, 5, a_scale);
    const ratio = thumbHashRatio(hash);
    const w = round(ratio > 1 ? 32 : 32 * ratio);
    const h = round(ratio > 1 ? 32 / ratio : 32);
    const rgba = new Uint8ClampedArray(w * h * 4), fx = [], fy = [];
    for (let y = 0, i = 0; y < h; y++) {
      for (let x = 0; x < w; x++, i += 4) {
        let l = l_dc, p = p_dc, q = q_dc, a = a_dc;
        for (let cx = 0, n = max(lx, hasAlpha ? 5 : 3); cx < n; cx++) fx[cx] = cos(PI / w * (x + 0.5) * cx);
        for (let cy = 0, n = max(ly, hasAlpha ? 5 : 3); cy < n; cy++) fy[cy] = cos(PI / h * (y + 0.5) * cy);
        for (let cy = 0, j = 0; cy < ly; cy++)
          for (let cx = cy ? 0 : 1, fy2 = fy[cy] * 2; cx * ly < lx * (ly - cy); cx++, j++)
            l += l_ac[j] * fx[cx] * fy2;
        for (let cy = 0, j = 0; cy < 3; cy++)
          for (let cx = cy ? 0 : 1, fy2 = fy[cy] * 2; cx < 3 - cy; cx++, j++) {
            const f = fx[cx] * fy2;
            p += p_ac[j] * f;
            q += q_ac[j] * f;
          }
        if (hasAlpha)
          for (let cy = 0, j = 0; cy < 5; cy++)
            for (let cx = cy ? 0 : 1, fy2 = fy[cy] * 2; cx < 5 - cy; cx++, j++)
              a += a_ac[j] * fx[cx] * fy2;
        const b = l - 2 / 3 * p;
        const r = (3 * l - b + q) / 2;
        const g = r - q;
        // | 0: отсечение дробной части, как Uint8Array в эталоне (Clamped округлял бы)
        rgba[i] = max(0, 255 * min(1, r)) | 0;
        rgba[i + 1] = max(0, 255 * min(1, g)) | 0;
        rgba[i + 2] = max(0, 255 * min(1, b)) | 0;
        rgba[i + 3] = max(0, 255 * min(1, a)) | 0;
      }
    }
    return { w, h, rgba };
  }
  function thumbHashRatio(hash) {
    const header = hash[3];
    const hasAlpha = hash[2] & 0x80;
    const isLandscape = hash[4] & 0x80;
    const lx = isLandscape ? (hasAlpha ? 5 : 7) : header & 7;
    const ly = isLandscape ? header & 7 : (hasAlpha ? 5 : 7);
    return lx / ly;
  }

  const thumbCache = new Map();   // base64 → data: URL (крошечные, без вытеснения до 500)
  function thumbUrl(b64) {
    if (!b64 || typeof b64 !== 'string') return null;
    if (thumbCache.has(b64)) return thumbCache.get(b64);
    let url = null;
    try {
      const bin = atob(b64);
      if (bin.length < 5 || bin.length > 64) throw new Error('bad thumbhash');
      const bytes = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) bytes[i] = bin.charCodeAt(i);
      const { w, h, rgba } = thumbHashToRGBA(bytes);
      const c = document.createElement('canvas');
      c.width = w; c.height = h;
      c.getContext('2d').putImageData(new ImageData(rgba, w, h), 0, 0);
      url = c.toDataURL('image/png');
    } catch (_) { url = null; }
    if (thumbCache.size > 500) thumbCache.clear();
    thumbCache.set(b64, url);
    return url;
  }

  /* Размытый фон лоадера вместо surface2. Нет хеша / битый хеш → фон как раньше. */
  function placeholder(root, b64) {
    if (!root) return false;
    const url = thumbUrl(b64);
    if (!url) return false;
    root.style.setProperty('--th-bg', 'url("' + url + '")');
    root.classList.add('fa-media-loader--th');
    return true;
  }

  window.MediaRing = {
    html, setProgress, reset, fmt, toBytes,
    fetchProgress, loadImage, bindVideo, cached, keyOf, track,
    placeholder, thumbUrl, thumbHashToRGBA,
  };
})();
