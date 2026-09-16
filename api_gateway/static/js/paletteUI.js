// paletteUI.js — UI цветовой палитры в профиле.
// Строит выпадашку внутри #palette-mount (в profile-body). Управление —
// 2D-пикер: кастомная белая точка (knob) на прямоугольной карте с dot-grid.
//   ось X → Hue (0..360)
//   ось Y → Saturation (верх — насыщеннее, низ — блёклее)
// L держим из эталонного бежа, чтобы гамма оставалась «в характере» бренда.
// Реакция ТОЛЬКО при захвате точки (pointerdown, пока зажато).
// Никаких hover-анимаций — на простое наведение мышки карта не реагирует.
(function () {
  const P = window.UserPalette;
  if (!P) return;

  const mount = document.getElementById('palette-mount');
  if (!mount) return;

  // L эталона (беж #8b4513) — держим при движении по H/S.
  const baseHsl = P._util.hexToHsl(P.DEFAULT_ACCENT); // {h,s,l}

  // Диапазон насыщенности по вертикали (верх → низ).
  const S_MAX = 85;
  const S_MIN = 12;

  mount.innerHTML = [
    '<div class="palette-section" id="palette-section">',
    '  <button class="palette-toggle" id="palette-toggle" type="button" aria-expanded="false">',
    '    <span class="palette-swatch" id="palette-swatch"></span>',
    '    <span>Цвет интерфейса</span>',
    '    <svg class="palette-chevron" viewBox="0 0 20 20" fill="none">',
    '      <path d="M5 8l5 5 5-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>',
    '    </svg>',
    '  </button>',
    '  <div class="palette-pop">',
    '    <div class="palette-card">',
    '      <div class="palette-field" id="palette-field">',
    '        <div class="palette-dots" id="palette-dots"></div>',
    '        <div class="palette-blob" id="palette-blob"></div>',
    '        <div class="palette-knob" id="palette-knob"></div>',
    '      </div>',
    '      <div class="palette-card-inner">',
    '        <div class="palette-preview-row">',
    '          <div class="palette-preview-chip" id="palette-chip"></div>',
    '          <div class="palette-preview-meta">',
    '            <span class="palette-preview-title">Акцент интерфейса</span>',
    '            <span class="palette-preview-hex" id="palette-hex">#8b4513</span>',
    '          </div>',
    '        </div>',
    '        <div class="palette-actions">',
    '          <button class="palette-btn palette-btn-save" id="palette-save" type="button">Сохранить</button>',
    '          <button class="palette-btn palette-btn-reset" id="palette-reset" type="button">Сбросить</button>',
    '        </div>',
    '      </div>',
    '    </div>',
    '  </div>',
    '</div>',
  ].join('');

  const section = document.getElementById('palette-section');
  const toggle  = document.getElementById('palette-toggle');
  const field   = document.getElementById('palette-field');
  const dots    = document.getElementById('palette-dots');
  const blob    = document.getElementById('palette-blob');
  const knob    = document.getElementById('palette-knob');
  const swatch  = document.getElementById('palette-swatch');
  const chip    = document.getElementById('palette-chip');
  const hexEl   = document.getElementById('palette-hex');
  const saveBtn = document.getElementById('palette-save');
  const resetBtn= document.getElementById('palette-reset');

  function clamp01(v) { return Math.min(1, Math.max(0, v)); }

  // Нормированные координаты точки на карте (0..1). Центр = старт.
  let nx = 0.5, ny = 0.5;

  // X → Hue, Y → Saturation (верх ny=0 → S_MAX, низ ny=1 → S_MIN).
  function hueFromX(x) { return x * 360; }
  function satFromY(y) { return S_MAX - y * (S_MAX - S_MIN); }
  function xFromHue(h) { return clamp01(h / 360); }
  function yFromSat(s) { return clamp01((S_MAX - s) / (S_MAX - S_MIN)); }

  function hexFromXY(x, y) {
    return P._util.hslToHex(hueFromX(x), satFromY(y), baseHsl.l);
  }

  function placeKnob() {
    knob.style.left = (nx * 100) + '%';
    knob.style.top  = (ny * 100) + '%';
    blob.style.left = (nx * 100) + '%';
    blob.style.top  = (ny * 100) + '%';
    dots.style.setProperty('--mx', (nx * 100) + '%');
    dots.style.setProperty('--my', (ny * 100) + '%');
  }

  function syncVisual(hex) {
    swatch.style.background = hex;
    chip.style.background = hex;
    hexEl.textContent = hex.toUpperCase();
    knob.style.background = hex;
  }

  function pullFromPalette() {
    const cur = P.getAccent() || P.DEFAULT_ACCENT;
    const hsl = P._util.hexToHsl(cur) || baseHsl;
    nx = xFromHue(hsl.h);
    ny = yFromSat(hsl.s);
    placeKnob();
    syncVisual(cur);
  }
  pullFromPalette();

  toggle.addEventListener('click', () => {
    const open = section.classList.toggle('open');
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    if (open) requestAnimationFrame(placeKnob);
  });

  // ── 2D-драг точки ────────────────────────────────────────────────
  let dragging = false;

  function pointFromEvent(e) {
    const r = field.getBoundingClientRect();
    const cx = (e.touches ? e.touches[0].clientX : e.clientX);
    const cy = (e.touches ? e.touches[0].clientY : e.clientY);
    nx = clamp01((cx - r.left) / r.width);
    ny = clamp01((cy - r.top) / r.height);
  }

  function applyFromXY() {
    const hex = hexFromXY(nx, ny);
    P.setAccent(hex, { preview: true });
    placeKnob();
    syncVisual(hex);
  }

  function onDown(e) {
    dragging = true;
    field.classList.add('dragging');
    pointFromEvent(e);
    applyFromXY();
    e.preventDefault();
  }

  function onMove(e) {
    if (!dragging) return;
    pointFromEvent(e);
    applyFromXY();
    if (e.cancelable) e.preventDefault();
  }

  function onUp() {
    if (!dragging) return;
    dragging = false;
    field.classList.remove('dragging');
  }

  field.addEventListener('mousedown', onDown);
  field.addEventListener('touchstart', onDown, { passive: false });
  window.addEventListener('mousemove', onMove);
  window.addEventListener('touchmove', onMove, { passive: false });
  window.addEventListener('mouseup', onUp);
  window.addEventListener('touchend', onUp);
  window.addEventListener('touchcancel', onUp);

  saveBtn.addEventListener('click', async () => {
    saveBtn.disabled = true;
    const ok = await P.save();
    saveBtn.textContent = ok ? 'Сохранено' : 'Ошибка';
    setTimeout(() => { saveBtn.textContent = 'Сохранить'; saveBtn.disabled = false; }, 1400);
  });

  resetBtn.addEventListener('click', async () => {
    await P.reset();
    pullFromPalette();
  });

  document.addEventListener('palette:changed', pullFromPalette);
})();
