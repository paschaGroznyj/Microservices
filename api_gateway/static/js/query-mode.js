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

  // Иконка режима «Модель»: SVG «нейросеть» (currentColor, тема меняется сама).
  const MODEL_SVG = '<svg class="qm-svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="M13.4 21C11.1 21 10.9 21 10.7 20.9C10.5 20.8 10.3 20.8 8.9 20.8C7.1 20.8 7 20.7 6.5 20.4C5.7 20 5.4 19.3 5.3 18.3C5.3 18 5.3 17.7 5.3 17.7C5.2 17.7 5 17.6 4.5 17.6C3.3 17.6 2.8 17.5 2 16.7C1.2 15.9 1 15.4 1 14C1 12.8 1.1 12.3 1.7 11.7C2 11.3 2 11.3 1.9 11.1C1.4 10.6 1.3 10.1 1.3 9C1.4 8.1 1.4 7.8 1.7 7.4C2.3 6.6 3.1 6.1 4.3 6C4.9 6 4.9 6 4.9 5.6C5.2 4.6 5.9 3.6 6.5 3.3C7 3 7.3 3 8.3 3C9.5 3 9.9 3.1 10.6 3.7L10.8 3.9 11.1 3.7C11.7 3.1 12.1 3 13.3 3C14.4 3 14.6 3 15.1 3.2C15.8 3.6 16.6 4.8 16.7 5.7C16.8 6 16.8 6 17.4 6.1C18.5 6.2 19.2 6.5 19.8 7.2C20.2 7.8 20.3 8 20.3 9.4L20.3 10.6 20.7 10.7C21.7 11 22.5 11.8 22.9 12.8C23 13.1 23 13.1 23 15.5C23 18.2 23 18.5 22.8 19C22.5 19.9 21.6 20.7 20.5 20.9C20.2 21 17.9 21 13.4 21ZM20.1 19.3C20.4 19.2 20.8 19.1 21 18.8C21.2 18.4 21.2 18.5 21.2 15.7L21.2 13.2 21.1 13.1C21 12.8 20.8 12.6 20.5 12.5L20.3 12.4 17.7 12.4C15.2 12.4 15.1 12.4 14.9 12.3C14.2 11.8 14.4 10.9 15.2 10.7C15.3 10.6 15.7 10.6 16.9 10.6C18.3 10.6 18.5 10.6 18.5 10.5C18.6 10.4 18.6 8.8 18.5 8.5C18.4 8 17.9 7.8 17.2 7.8L16.7 7.8 16.6 7.9C16.3 8.3 15.7 8.3 15.3 7.9C15 7.7 15 7.5 15 6.7C15 5.6 14.8 5.1 14.2 4.8C13.9 4.7 12.9 4.7 12.6 4.8C12.3 4.9 12 5.2 11.8 5.6C11.7 5.9 11.7 8.5 11.8 8.8C11.8 8.8 12.2 9.2 12.6 9.6C13.5 10.6 13.6 10.7 13.6 11.4C13.6 12.1 13.6 12.1 11.9 13.8C10.9 14.7 10.4 15.2 10.4 15.3C10.3 15.6 10.4 18.2 10.5 18.5C10.6 19 11 19.2 11.6 19.3C12 19.3 19.8 19.3 20.1 19.3ZM8.7 18.9C8.7 18.9 8.7 18.7 8.7 18.6C8.6 18.4 8.6 17.6 8.6 16.5C8.6 14.8 8.6 14.7 8.7 14.5C8.8 14.3 9 14.1 10.4 12.7C11.6 11.6 11.7 11.4 11.7 11.3C11.7 11.3 11.3 10.9 10.9 10.4C9.8 9.3 9.9 9.6 9.9 7.5C9.8 5.6 9.8 5.6 9.5 5.2C9.2 4.8 9 4.7 8.3 4.7C7.5 4.7 7.4 4.8 7.1 5.1C6.8 5.4 6.8 5.6 6.7 6.7C6.7 7.5 6.7 7.7 6.6 7.8C6.3 8.6 5.3 8.6 5 7.8L4.9 7.7 4.4 7.7C3.4 7.8 3.1 8.2 3.1 9.2C3.1 9.8 3.1 10 3.4 10.2C3.6 10.5 3.8 10.5 4.7 10.5C5.9 10.5 6.3 10.6 6.6 11C6.9 11.3 6.9 11.8 6.5 12.2C6.2 12.4 6.2 12.4 4.6 12.4C3.6 12.4 3.5 12.4 3.2 12.7C2.9 12.9 2.8 13.1 2.8 14C2.8 14.9 2.9 15.1 3.3 15.5C3.7 15.9 4.1 15.9 4.9 15.9L5.3 15.8 5.3 15.6C5.7 14.8 6.6 14.7 7 15.5C7.1 15.7 7.1 15.7 7.1 17C7.1 18.6 7.1 18.7 7.5 18.9C7.8 19.1 8.7 19 8.7 18.9ZM13.4 16.6C12.8 16.5 12.5 15.9 12.7 15.4C13 14.9 13.1 14.8 16.7 14.9L19.3 14.9 19.5 15C19.8 15.1 19.8 15.2 20 15.4C20.2 15.8 19.9 16.4 19.5 16.6C19.3 16.6 13.7 16.6 13.4 16.6Z"/></svg>';
  // Иконка агента — та же, что у «Агента» в sb-nav (голова). В Sigma там картинка — берём её.
  const AGENT_SVG = '<svg class="qm-svg" viewBox="0 0 24 24" aria-hidden="true"><path fill="currentColor" fill-rule="evenodd" d="M11.3 22.7C10.7 22.5 10.4 22.2 10.4 21.7C10.4 21.6 10.3 21.4 10.2 21.2L10 21 8.6 21C5.6 21 4.7 20.3 4.7 17.7C4.7 16.4 4.7 16.3 3.9 16.3C2.7 16.3 1.9 15.9 1.7 14.9C1.5 14.3 1.6 14 3 11.6L4.2 9.7 4.3 8.9C4.5 7.6 4.7 6.9 5.2 5.9C8.6 -0.9 19.3 -0.1 21.9 7.2C23 10.5 22.3 13.8 19.8 16.4C18.6 17.6 18.6 17.5 20.1 19.5C20.6 20 21 20.6 21 20.8C21.2 21.6 20.5 22.3 19.8 22.1C19 21.9 17 19.2 16.8 18.2C16.5 16.9 16.8 16.2 18.2 14.8C19.2 13.7 19.7 13 19.9 12C20.2 10.8 20.2 10.8 17.8 10.8L16 10.8 15.8 11C15.7 11.1 15.5 11.4 15.3 11.6C14.3 13.1 11.8 13.2 10.4 11.8C8.6 10 9.6 6.8 12.2 6.4C13.5 6.1 15 6.8 15.6 8C15.7 8.2 15.9 8.4 16 8.6L16.2 8.8 17.8 8.8C19.4 8.8 19.4 8.8 19.6 8.6C20.3 7.8 18.3 5.1 16.2 4.1C15 3.4 12.5 3.2 11.1 3.7C8.7 4.3 6.8 6.4 6.5 8.8C6.4 9.8 6.3 10.1 4.9 12.3C3.7 14.2 3.8 14.3 5.6 14.4L6.8 14.4 6.8 16.4L6.8 18.5 7 18.7L7.2 18.9 9.1 18.9C11.3 18.9 11.6 19 12 19.7C12.4 20.3 12.6 21.7 12.3 22.2C12.2 22.6 11.6 22.9 11.3 22.7ZM13.5 10.6C14.5 9.8 14 8.4 12.8 8.4C12.3 8.4 12.3 8.4 11.9 8.7C11.6 9.1 11.6 9.1 11.6 9.6C11.6 10.6 12.7 11.2 13.5 10.6Z"/></svg>';
  function _agentIcon(cls) {
    const nav = document.querySelector('.sb-nav .nav-item[data-tab="agent"] .nav-icon-img');
    if (!nav) return AGENT_SVG;
    const light = nav.getAttribute('data-icon-light');
    const dark = nav.getAttribute('data-icon-dark') || light;
    const theme = document.documentElement.getAttribute('data-theme') === 'dark' ? 'dark' : 'light';
    return `<img class="${cls} js-theme-icon" src="${theme === 'dark' ? dark : light}" data-icon-light="${light}" data-icon-dark="${dark}" alt="" aria-hidden="true">`;
  }
  function _icon(k, cls) { return k === 'agent' ? _agentIcon(cls) : k === 'model' ? MODEL_SVG : ICONS[k]; }

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
