// paletteUI.js — UI цветовой палитры в профиле.
// Строит выпадашку внутри #palette-mount (в profile-body), связывает
// ползунок тона с window.UserPalette, рисует инерционный blob по dot-grid.
// Красим пока ТОЛЬКО акцент: ползунок = Hue, S/L берём из текущего
// эталонного бежа, чтобы гамма оставалась «в характере» бренда.
(function () {
  const P = window.UserPalette;
  if (!P) return;

  const mount = document.getElementById('palette-mount');
  if (!mount) return;

  // S/L эталона (беж #8b4513) — держим при кручении Hue.
  const baseHsl = P._util.hexToHsl(P.DEFAULT_ACCENT); // {h,s,l}

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
    '    <div class="palette-card" id="palette-card">',
    '      <div class="palette-dots" id="palette-dots"></div>',
    '      <div class="palette-blob" id="palette-blob"></div>',
    '      <div class="palette-card-inner">',
    '        <div class="palette-preview-row">',
    '          <div class="palette-preview-chip" id="palette-chip"></div>',
    '          <div class="palette-preview-meta">',
    '            <span class="palette-preview-title">Акцент интерфейса</span>',
    '            <span class="palette-preview-hex" id="palette-hex">#8b4513</span>',
    '          </div>',
    '        </div>',
    '        <div class="palette-hue-wrap">',
    '          <input type="range" class="palette-hue" id="palette-hue" min="0" max="360" step="1" value="25">',
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
  const card    = document.getElementById('palette-card');
  const dots    = document.getElementById('palette-dots');
  const blob    = document.getElementById('palette-blob');
  const swatch  = document.getElementById('palette-swatch');
  const chip    = document.getElementById('palette-chip');
  const hexEl   = document.getElementById('palette-hex');
  const hue     = document.getElementById('palette-hue');
  const saveBtn = document.getElementById('palette-save');
  const resetBtn= document.getElementById('palette-reset');

  // hex из текущего hue (S/L эталона)
  function hexFromHue(h) {
    return P._util.hslToHex(h, baseHsl.s, baseHsl.l);
  }

  function syncVisual(hex) {
    swatch.style.background = hex;
    chip.style.background = hex;
    hexEl.textContent = hex.toUpperCase();
  }

  // Проставить ползунок под текущий акцент палитры
  function pullFromPalette() {
    const cur = P.getAccent() || P.DEFAULT_ACCENT;
    const hsl = P._util.hexToHsl(cur) || baseHsl;
    hue.value = Math.round(hsl.h);
    syncVisual(cur);
  }
  pullFromPalette();

  // Разворот/сворачивание
  toggle.addEventListener('click', () => {
    const open = section.classList.toggle('open');
    toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
  });

  // Движение ползунка → мгновенный preview (без записи в БД)
  hue.addEventListener('input', () => {
    const hex = hexFromHue(Number(hue.value));
    P.setAccent(hex, { preview: true });
    syncVisual(hex);
  });

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

  // Синк, если сервер прислал другой акцент (другое устройство)
  document.addEventListener('palette:changed', pullFromPalette);

  // ── инерционный blob по dot-grid ─────────────────────────────────
  let mx = 0.5, my = 0.5;   // цель (0..1)
  let bx = 0.5, by = 0.5;   // текущее (инерция)
  let raf = null, inside = false;

  function onMove(e) {
    const r = card.getBoundingClientRect();
    const px = (e.touches ? e.touches[0].clientX : e.clientX) - r.left;
    const py = (e.touches ? e.touches[0].clientY : e.clientY) - r.top;
    mx = Math.min(1, Math.max(0, px / r.width));
    my = Math.min(1, Math.max(0, py / r.height));
    inside = true;
    if (!raf) raf = requestAnimationFrame(tick);
  }

  function tick() {
    // экспоненциальное сглаживание = ощущение инерции
    bx += (mx - bx) * 0.18;
    by += (my - by) * 0.18;
    const r = card.getBoundingClientRect();
    blob.style.left = (bx * r.width) + 'px';
    blob.style.top  = (by * r.height) + 'px';
    dots.style.setProperty('--mx', (bx * 100) + '%');
    dots.style.setProperty('--my', (by * 100) + '%');
    if (Math.abs(mx - bx) > 0.001 || Math.abs(my - by) > 0.001) {
      raf = requestAnimationFrame(tick);
    } else {
      raf = null;
    }
  }

  card.addEventListener('mousemove', onMove);
  card.addEventListener('touchmove', onMove, { passive: true });
  card.addEventListener('mouseleave', () => { inside = false; });
})();
