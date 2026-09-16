// palette.js — персональная цветовая палитра пользователя.
//
// Модель (по договорённости): красим ПОКА только базовый акцент --accent.
// Из одного тона генерим согласованные производные (--accent-hover,
// --accent-muted, --text-on-accent). Хранится ОДНА палитра; в тёмной теме
// тон пересчитывается программно (инверсия светлоты в HSL), чтобы
// бренд-оттенок оставался узнаваемым и юзер настраивал один раз.
//
// Слои применения:
//   1) localStorage('user_palette')  — мгновенно, до рендера, без FOUC.
//   2) GET /api/preferences          — фоновый синк между устройствами.
//   3) PUT /api/preferences (Redis+БД) — на "Сохранить".
//
// Публичный API window.UserPalette: applyFromStorage(), syncFromServer(),
// setAccent(hex, {preview}), save(), reset(), onThemeChange().
(function () {
  const root = document.documentElement;
  const LS_KEY = 'user_palette';

  // Дефолтный беж — совпадает с :root в theme.css (--accent light).
  const DEFAULT_ACCENT = '#8b4513';

  // ── цветовые утилиты ─────────────────────────────────────────────
  function clamp(v, a, b) { return Math.min(b, Math.max(a, v)); }

  function hexToRgb(hex) {
    let h = String(hex || '').trim().replace('#', '');
    if (h.length === 3) h = h.split('').map(c => c + c).join('');
    const n = parseInt(h, 16);
    if (isNaN(n) || h.length !== 6) return null;
    return { r: (n >> 16) & 255, g: (n >> 8) & 255, b: n & 255 };
  }

  function rgbToHex(r, g, b) {
    const to = (x) => clamp(Math.round(x), 0, 255).toString(16).padStart(2, '0');
    return '#' + to(r) + to(g) + to(b);
  }

  function rgbToHsl(r, g, b) {
    r /= 255; g /= 255; b /= 255;
    const max = Math.max(r, g, b), min = Math.min(r, g, b);
    let h, s, l = (max + min) / 2;
    if (max === min) { h = s = 0; }
    else {
      const d = max - min;
      s = l > 0.5 ? d / (2 - max - min) : d / (max + min);
      switch (max) {
        case r: h = (g - b) / d + (g < b ? 6 : 0); break;
        case g: h = (b - r) / d + 2; break;
        default: h = (r - g) / d + 4;
      }
      h /= 6;
    }
    return { h: h * 360, s: s * 100, l: l * 100 };
  }

  function hslToRgb(h, s, l) {
    h /= 360; s /= 100; l /= 100;
    let r, g, b;
    if (s === 0) { r = g = b = l; }
    else {
      const hue2rgb = (p, q, t) => {
        if (t < 0) t += 1;
        if (t > 1) t -= 1;
        if (t < 1 / 6) return p + (q - p) * 6 * t;
        if (t < 1 / 2) return q;
        if (t < 2 / 3) return p + (q - p) * (2 / 3 - t) * 6;
        return p;
      };
      const q = l < 0.5 ? l * (1 + s) : l + s - l * s;
      const p = 2 * l - q;
      r = hue2rgb(p, q, h + 1 / 3);
      g = hue2rgb(p, q, h);
      b = hue2rgb(p, q, h - 1 / 3);
    }
    return { r: r * 255, g: g * 255, b: b * 255 };
  }

  function hexToHsl(hex) {
    const rgb = hexToRgb(hex);
    if (!rgb) return null;
    return rgbToHsl(rgb.r, rgb.g, rgb.b);
  }

  function hslToHex(h, s, l) {
    const { r, g, b } = hslToRgb(h, s, l);
    return rgbToHex(r, g, b);
  }

  // Контрастный текст на акценте: по относительной яркости.
  function textOn(hex) {
    const rgb = hexToRgb(hex);
    if (!rgb) return '#ffffff';
    const lum = (0.299 * rgb.r + 0.587 * rgb.g + 0.114 * rgb.b) / 255;
    return lum > 0.6 ? '#0d0d0d' : '#ffffff';
  }

  // ── генерация набора токенов из одного акцента ───────────────────
  // theme: 'light' | 'dark'. В dark инвертируем светлоту (H,S держим),
  // чтобы тот же тон читался на тёмном фоне.
  function buildTokens(accentHex, theme) {
    const hsl = hexToHsl(accentHex) || hexToHsl(DEFAULT_ACCENT);
    let { h, s, l } = hsl;

    if (theme === 'dark') {
      // Инверсия светлоты + мягкая коррекция: очень тёмные тона
      // поднимаем, очень светлые опускаем, чтобы не уходить в крайности.
      l = 100 - l;
      l = clamp(l, 45, 78);
      s = clamp(s, 25, 90);
    } else {
      l = clamp(l, 22, 55);
    }

    const base = hslToHex(h, s, l);
    const hover = hslToHex(h, s, clamp(theme === 'dark' ? l - 8 : l - 6, 5, 92));
    const muted = hslToHex(h, clamp(s - 12, 0, 100),
                           clamp(theme === 'dark' ? l - 4 : l + 18, 5, 92));

    return {
      '--accent': base,
      '--accent-hover': hover,
      '--accent-muted': muted,
      '--text-on-accent': textOn(base),
    };
  }

  // ── применение к DOM ─────────────────────────────────────────────
  function currentTheme() {
    return root.getAttribute('data-theme') || 'light';
  }

  // Текущий "исходный" акцент, который выбрал юзер (light-эталон).
  let accentHex = DEFAULT_ACCENT;

  function applyTokens(accent) {
    const theme = currentTheme();
    const tokens = buildTokens(accent, theme);
    for (const k in tokens) root.style.setProperty(k, tokens[k]);
  }

  function clearTokens() {
    ['--accent', '--accent-hover', '--accent-muted', '--text-on-accent']
      .forEach(k => root.style.removeProperty(k));
  }

  // ── хранилище ────────────────────────────────────────────────────
  function loadLocal() {
    try {
      const raw = localStorage.getItem(LS_KEY);
      if (!raw) return null;
      const p = JSON.parse(raw);
      return p && p['--accent'] ? p['--accent'] : null;
    } catch (e) { return null; }
  }

  function saveLocal(accent) {
    try { localStorage.setItem(LS_KEY, JSON.stringify({ '--accent': accent })); }
    catch (e) { /* приватный режим и т.п. */ }
  }

  function clearLocal() {
    try { localStorage.removeItem(LS_KEY); } catch (e) {}
  }

  // ── публичный API ────────────────────────────────────────────────
  // Синхронно на старте — до первого рендера (см. inline-вызов в head).
  function applyFromStorage() {
    const a = loadLocal();
    if (a) { accentHex = a; applyTokens(a); }
  }

  // Фоновый синк с сервером (после загрузки). Обновляет и localStorage.
  async function syncFromServer() {
    try {
      const r = await fetch('/api/preferences', { credentials: 'same-origin' });
      if (!r.ok) return;
      const data = await r.json();
      const pal = (data && data.palette) || {};
      const a = pal['--accent'];
      if (a && a !== accentHex) {
        accentHex = a;
        saveLocal(a);
        applyTokens(a);
        document.dispatchEvent(new CustomEvent('palette:changed', { detail: { accent: a } }));
      } else if (!a) {
        // сервер пуст — считаем дефолтом
        document.dispatchEvent(new CustomEvent('palette:changed', { detail: { accent: DEFAULT_ACCENT } }));
      }
    } catch (e) { /* presence-стиль: не критично */ }
  }

  // preview=true — только визуально (движение ползунка), без записи.
  function setAccent(hex, opts) {
    const rgb = hexToRgb(hex);
    if (!rgb) return;
    accentHex = hex;
    applyTokens(hex);
    if (!opts || !opts.preview) saveLocal(hex);
  }

  // Сохранить на сервер (Redis+БД) + localStorage.
  async function save() {
    saveLocal(accentHex);
    try {
      const r = await fetch('/api/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ palette: { '--accent': accentHex } }),
      });
      return r.ok;
    } catch (e) { return false; }
  }

  // Сброс к дефолтному бежу (локально + на сервере).
  async function reset() {
    accentHex = DEFAULT_ACCENT;
    clearLocal();
    clearTokens();
    document.dispatchEvent(new CustomEvent('palette:changed', { detail: { accent: DEFAULT_ACCENT } }));
    try {
      await fetch('/api/preferences', {
        method: 'PUT',
        headers: { 'Content-Type': 'application/json' },
        credentials: 'same-origin',
        body: JSON.stringify({ palette: {} }),
      });
    } catch (e) {}
  }

  // При переключении light/dark пересчитать токены под новую тему.
  function onThemeChange() {
    if (accentHex && accentHex !== DEFAULT_ACCENT) applyTokens(accentHex);
    else clearTokens();
  }

  function getAccent() { return accentHex; }

  window.UserPalette = {
    applyFromStorage,
    syncFromServer,
    setAccent,
    getAccent,
    save,
    reset,
    onThemeChange,
    DEFAULT_ACCENT,
    _util: { hexToHsl, hslToHex, hexToRgb, rgbToHex },
  };

  // Применяем из localStorage немедленно (скрипт в head, до body-рендера).
  applyFromStorage();
})();
