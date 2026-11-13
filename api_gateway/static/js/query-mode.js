/**
 * QueryMode — пилюля «Агент / Модель» у кнопки отправки.
 *
 *   agent — полный агент с инструментами (по умолчанию);
 *   model — MauDel: только языковая модель, без инструментов и вложений.
 *
 * Публичное API (window.QueryMode):
 *   get()            → 'agent' | 'model'   (уходит в send_message как type_query)
 *   isModel()        → boolean
 *   set(mode)        → переключить режим
 *   inject(container)→ вставить пилюлю в .chat-input-footer / .start-input-footer
 *
 * В режиме «Модель» блокируются .tools-trigger-btn и #attach-btn / #start-attach-btn,
 * уже прикреплённые файлы снимаются (FileAttach.clear), drop/paste режет FileAttach.
 */
(() => {
  const LS_KEY = 'augent:type_query';
  const ICONS = {
    agent: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><rect x="4" y="7" width="16" height="12" rx="3" stroke="currentColor" stroke-width="1.8"/><path d="M12 7V4M9 12.5h.01M15 12.5h.01M9.5 16h5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/><circle cx="12" cy="3.5" r="1" fill="currentColor"/></svg>',
    model: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 18.5V17a3 3 0 0 1-1-2.2V8a3 3 0 0 1 3-3h10a3 3 0 0 1 3 3v6.8a3 3 0 0 1-3 3H9.5L5 20.5v-2Z" stroke="currentColor" stroke-width="1.8" stroke-linejoin="round"/><path d="M8.5 10h7M8.5 13.3h4.5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"/></svg>',
    check: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M5 12.5l4.2 4.2L19 7" stroke="currentColor" stroke-width="2.2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    chev: '<svg viewBox="0 0 24 24" fill="none" aria-hidden="true"><path d="M7 10l5 5 5-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>',
  };
  const MODES = {
    agent: { label: 'Агент',  desc: 'Инструменты, файлы, поиск, код' },
    model: { label: 'Модель', desc: 'Без инструментов, быстрее и дешевле' },
  };
  const LOCK_SEL = '.tools-trigger-btn, #attach-btn, #start-attach-btn';

  let mode = 'agent';
  try { if (localStorage.getItem(LS_KEY) === 'model') mode = 'model'; } catch (_) {}
  let menu = null;
  let menuOwner = null;
  const observed = new WeakSet();

  const get = () => mode;
  const isModel = () => mode === 'model';

  function _footers(root = document) {
    return root.querySelectorAll('.chat-input-footer:not(.ed-ai-footer), .start-input-footer');
  }

  // Иконка агента — та же картинка, что у пункта «Агент» в sb-nav
  // (в Sigma — сигма-иконка). Тема переключается через .js-theme-icon (theme.js).
  const AGENT_IMG = {
    light: '/static/img/icon_black.png?v=98b91002ac',
    dark: '/static/img/icon_white.png?v=d6edccf0b9',
  };
  function _agentIcon(cls) {
    const nav = document.querySelector('.sb-nav .nav-item[data-tab="agent"] .nav-icon-img');
    const light = (nav && nav.getAttribute('data-icon-light')) || AGENT_IMG.light;
    const dark = (nav && nav.getAttribute('data-icon-dark')) || AGENT_IMG.dark;
    const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    return `<img class="${cls} js-theme-icon" src="${theme === 'dark' ? dark : light}" data-icon-light="${light}" data-icon-dark="${dark}" alt="" aria-hidden="true">`;
  }
  function _icon(k, cls) { return k === 'agent' ? _agentIcon(cls) : ICONS[k]; }

  function _syncPill(pill) {
    const m = MODES[mode];
    pill.dataset.mode = mode;
    pill.classList.toggle('qm-pill--model', mode === 'model');
    pill.setAttribute('aria-label', `Режим: ${m.label}. Сменить режим`);
    pill.title = mode === 'model' ? 'MauDel — модель без инструментов' : 'Агент с инструментами';
    pill.innerHTML = `<span class="qm-pill__ic">${_icon(mode, 'qm-img')}</span><span class="qm-pill__lbl">${m.label}</span><span class="qm-pill__chev">${ICONS.chev}</span>`;
  }

  function _syncLocks(footer) {
    const lock = mode === 'model';
    footer.querySelectorAll(LOCK_SEL).forEach((b) => {
      if (b.classList.contains('mp-trigger-btn')) return;   // пикер модели остаётся рабочим
      if (lock) {
        if (!b.hasAttribute('data-qm-title')) b.setAttribute('data-qm-title', b.title || '');
        b.disabled = true;
        b.classList.add('qm-locked');
        b.setAttribute('aria-disabled', 'true');
        b.title = 'Недоступно в режиме «Модель»';
      } else if (b.classList.contains('qm-locked')) {
        b.disabled = false;
        b.classList.remove('qm-locked');
        b.removeAttribute('aria-disabled');
        b.title = b.getAttribute('data-qm-title') || '';
        b.removeAttribute('data-qm-title');
      }
    });
  }

  function _syncAll() {
    document.querySelectorAll('.qm-pill').forEach(_syncPill);
    _footers().forEach(_syncLocks);
    const box = document.querySelector('#chat-input-box, #start-input-box');
    box?.classList.toggle('qm-model', mode === 'model');
    document.querySelectorAll('.input-footer-note, .start-footer-note').forEach((n) => {
      if (!n.hasAttribute('data-qm-note')) n.setAttribute('data-qm-note', n.textContent);
      n.textContent = mode === 'model'
        ? 'Модель отвечает без инструментов — для поиска, файлов и кода переключитесь на «Агент»'
        : n.getAttribute('data-qm-note');
    });
  }

  function set(next) {
    next = next === 'model' ? 'model' : 'agent';
    if (next === mode) { _closeMenu(); return; }
    mode = next;
    try { localStorage.setItem(LS_KEY, mode); } catch (_) {}
    if (mode === 'model' && (window.FileAttach?.getFiles?.() ?? []).length) {
      window.FileAttach.clear();
      window.FileAttach.showToast?.('Вложения сняты: в режиме «Модель» файлы не отправляются.');
    }
    window.ToolsPanel?._close?.();
    _syncAll();
    _closeMenu();
    document.dispatchEvent(new CustomEvent('querymode:change', { detail: { mode } }));
  }

  function _closeMenu() {
    if (!menu) return;
    menu.remove();
    menu = null;
    menuOwner?.setAttribute('aria-expanded', 'false');
    menuOwner = null;
    document.removeEventListener('pointerdown', _outside, true);
    document.removeEventListener('keydown', _esc, true);
  }
  function _outside(e) {
    if (menu && !menu.contains(e.target) && !menuOwner?.contains(e.target)) _closeMenu();
  }
  function _esc(e) {
    if (e.key === 'Escape') { e.stopPropagation(); const o = menuOwner; _closeMenu(); o?.focus(); }
  }

  // Бегущая строка для описаний, которые не влезают в пункт меню:
  // сдвиг = переполнение, скорость ~30 px/с, пауза по краям (в keyframes).
  function _marquee(root) {
    root.querySelectorAll('.qm-marq').forEach((box) => {
      const inner = box.querySelector('.qm-marq__in');
      if (!inner) return;
      const over = Math.ceil(inner.scrollWidth - box.clientWidth);
      box.classList.toggle('is-run', over > 2);
      if (over > 2) {
        box.style.setProperty('--qm-shift', `-${over + 4}px`);
        box.style.setProperty('--qm-dur', `${Math.max(4, (over + 4) / 30 + 2.4).toFixed(1)}s`);
      }
    });
  }

  function _openMenu(pill) {
    if (menu) { const same = menuOwner === pill; _closeMenu(); if (same) return; }
    menu = document.createElement('div');
    menu.className = 'qm-menu';
    menu.setAttribute('role', 'menu');
    menu.innerHTML = Object.entries(MODES).map(([k, m]) => `
      <button type="button" role="menuitemradio" aria-checked="${k === mode}" class="qm-item${k === mode ? ' is-on' : ''}" data-mode="${k}">
        <span class="qm-item__ic">${_icon(k, 'qm-img')}</span>
        <span class="qm-item__txt"><b>${m.label}</b><small class="qm-marq"><span class="qm-marq__in">${m.desc}</span></small></span>
        <span class="qm-item__ok">${k === mode ? ICONS.check : ''}</span>
      </button>`).join('');
    menu.addEventListener('click', (e) => {
      const it = e.target.closest('.qm-item');
      if (it) set(it.dataset.mode);
    });
    document.body.appendChild(menu);
    menuOwner = pill;
    pill.setAttribute('aria-expanded', 'true');
    const r = pill.getBoundingClientRect();
    // Меню целиком над полем ввода и не выходит за его левый/правый край.
    const box = pill.closest('.chat-input-box, .start-input-box');
    const br = box ? box.getBoundingClientRect() : null;
    const minL = br ? br.left : 12;
    const maxR = br ? br.right : window.innerWidth - 12;
    // Узкий экран: меню во всю ширину поля ввода, края совпадают.
    if (br && window.innerWidth <= 768) menu.style.width = `${br.width}px`;
    else if (br && menu.offsetWidth > br.width) menu.style.width = `${br.width}px`;
    const mw = menu.offsetWidth, mh = menu.offsetHeight;
    const left = Math.max(minL, Math.min(r.right - mw, maxR - mw));
    const anchorTop = br ? br.top : r.top;
    const top = anchorTop - mh - 8 >= 8 ? anchorTop - mh - 8 : r.bottom + 8;
    menu.style.left = `${left}px`;
    menu.style.top = `${top}px`;
    menu.querySelector('.qm-item.is-on')?.focus({ preventScroll: true });
    _marquee(menu);
    document.addEventListener('pointerdown', _outside, true);
    document.addEventListener('keydown', _esc, true);
  }

  function _makePill() {
    const pill = document.createElement('button');
    pill.type = 'button';
    pill.className = 'qm-pill';
    pill.setAttribute('aria-haspopup', 'menu');
    pill.setAttribute('aria-expanded', 'false');
    pill.addEventListener('click', (e) => { e.stopPropagation(); _openMenu(pill); });
    _syncPill(pill);
    return pill;
  }

  function inject(container) {
    (container ? _footers(container) : _footers()).forEach((footer) => {
      if (!footer.querySelector('.qm-pill')) {
        const send = footer.querySelector('#send-btn, .chat-send-btn, .start-send-btn');
        const pill = _makePill();
        if (send) footer.insertBefore(pill, send); else footer.appendChild(pill);
      }
      // ToolsPanel / ModelPicker могут перерисовать кнопки позже — держим блокировку.
      if (!observed.has(footer)) {
        observed.add(footer);
        new MutationObserver(() => _syncLocks(footer)).observe(footer, { childList: true });
      }
    });
    _syncAll();
  }

  window.addEventListener('resize', _closeMenu);
  window.QueryMode = { get, isModel, set, inject };
})();
