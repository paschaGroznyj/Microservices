/**
 * tools-panel.js — панель выбора инструментов + размер контекста
 *
 * API:
 *   ToolsPanel.setUser(username)     → async, вызвать после checkAuth()
 *   ToolsPanel.getSelectedCategories() → string[]  (id категорий)
 *   ToolsPanel.getContextSize()        → 'small'|'medium'|'large'
 *   ToolsPanel.inject(container)       → void
 *
 * На бэк летят id категорий (не имена тулов) — бэк сам резолвит тулы.
 */

/* ── SVG-иконки категорий ────────────────────────────────────────────── */
const ICONS = {
  pm_research: `<svg viewBox="0 0 24 24" fill="none" width="17" height="17">
    <circle cx="11" cy="11" r="8" stroke="currentColor" stroke-width="2"/>
    <path d="M21 21l-4.35-4.35" stroke="currentColor" stroke-width="2"
          stroke-linecap="round"/>
    <path d="M11 8v3l2 2" stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`,

  internet: `<svg viewBox="0 0 24 24" fill="none" width="17" height="17">
    <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/>
    <path d="M2 12h20M12 2a15.3 15.3 0 0 1 4 10 15.3 15.3 0 0 1-4 10
             A15.3 15.3 0 0 1 8 12 15.3 15.3 0 0 1 12 2z"
          stroke="currentColor" stroke-width="2"/>
  </svg>`,

  content: `<svg viewBox="0 0 24 24" fill="none" width="17" height="17">
    <path d="M14 2H6a2 2 0 0 0-2 2v16a2 2 0 0 0 2 2h12a2 2 0 0 0 2-2V8z"
          stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/>
    <polyline points="14 2 14 8 20 8"
              stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"/>
    <line x1="16" y1="13" x2="8" y2="13"
          stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
    <line x1="16" y1="17" x2="8" y2="17"
          stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
  </svg>`,

  sessions: `<svg viewBox="0 0 24 24" fill="none" width="17" height="17">
    <path d="M21 15a2 2 0 0 1-2 2H7l-4 4V5a2 2 0 0 1 2-2h14a2 2 0 0 1 2 2z"
          stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`,

  media_gen: `<svg viewBox="0 0 24 24" fill="none" width="17" height="17">
    <rect x="2" y="4" width="20" height="16" rx="2"
          stroke="currentColor" stroke-width="2"/>
    <path d="M10 9l5 3-5 3V9z"
          stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`,

  admin: `<svg viewBox="0 0 24 24" fill="none" width="17" height="17">
    <rect x="3" y="11" width="18" height="11" rx="2" ry="2"
          stroke="currentColor" stroke-width="2"/>
    <path d="M7 11V7a5 5 0 0 1 10 0v4"
          stroke="currentColor" stroke-width="2"
          stroke-linecap="round" stroke-linejoin="round"/>
  </svg>`
};

/* ── Категории — только метаданные, без списка тулов ─────────────────── */
const TOOL_CATEGORIES = [
  // {
  //   id:          "pm_research",
  //   label:       "Process Mining",
  //   description: "Поиск статей, ревью, дайджесты, PDF",
  //   adminOnly:   false
  // },
  {
    id:          "internet",
    label:       "Интернет",
    description: "Поиск в сети и парсинг страниц",
    adminOnly:   false
  },
  {
    id:          "content",
    label:       "Контент и файлы",
    description: "Генерация PPTX, DOCX, Excel, PNG",
    adminOnly:   false
  },
  {
    id:          "sessions",
    label:       "Бортовой журнал агента",
    description: "Бортовой журнал агента: долгосрочная память и точки сессий",
    adminOnly:   false
  },
  {
    id:          "media_gen",
    label:       "Видео и аудио",
    description: "Генерация видео и синтез речи (RouterAPI, платно)",
    adminOnly:   true
  },
  {
    id:          "admin",
    label:       "Администрирование",
    description: "БД, кодовая база, библиотека скриптов",
    adminOnly:   true
  }
];

const CONTEXT_SIZES = [
  { id: "small",  label: "S", title: "Маленький",     hint: "~5 последних сообщений"  },
  { id: "medium", label: "M", title: "Большой",       hint: "~15 последних сообщений" },
  { id: "large",  label: "L", title: "Очень большой", hint: "~40 последних сообщений" }
];

/* ═══════════════════════════════════════════════════════════════════════ */

class ToolsPanel {
  constructor() {
    this._isAdmin     = false;
    this._contextSize = "medium";
    this._loopEnabled = false;   // самоцикл агента (только админ)
    this._open        = false;
    this._panelEl     = null;

    /** @type {Set<string>} — id выбранных категорий */
    this._selected = new Set(
      // TOOL_CATEGORIES.filter(c => !c.adminOnly).map(c => c.id)
    );
  }

  /* ── публичное API ──────────────────────────────────────────────────── */

  /**
   * Вызывается из app.js после checkAuth().
   * Спрашивает бэк /api/auth/permissions — тот решает, админ или нет.
   */
  async setUser(username) {
    try {
      const res = await fetch("/api/auth/permissions", { credentials: "include" });
      if (res.ok) {
        const { is_admin } = await res.json();
        this._isAdmin = !!is_admin; // "1" -> ! -> false -> ! -> true
      }
    } catch (e) {
      console.warn("[ToolsPanel] permissions fetch failed:", e);
      this._isAdmin = false;
    }
    // Если не админ — снимаем admin-категорию
    if (!this._isAdmin) {
      this._selected.delete("admin");
    }
  }

  /** Возвращает id выбранных категорий — бэк сам знает какие тулы за ними */
  getSelectedCategories() {
    return [...this._selected];
  }

  getContextSize() { return this._contextSize; }

  /** Самоцикл включён? Только админ может включить. */
  getLoopEnabled() { return this._isAdmin && this._loopEnabled; }

  /**
   * Вставляет кнопку 🔧 в chat-input-footer и start-input-footer.
   * Безопасно вызывать повторно — не задвоит.
   */
  inject(container) {
    // ── активный чат ──────────────────────────
    const chatFooter = container.querySelector(".chat-input-footer");
    if (chatFooter && !chatFooter.querySelector(".tools-trigger-btn")) {
      const btn       = this._makeBtn();
      const attachBtn = chatFooter.querySelector(".attach-btn");
      if (attachBtn?.nextSibling) {
        chatFooter.insertBefore(btn, attachBtn.nextSibling);
      } else {
        chatFooter.appendChild(btn);
      }
      // Синхронизируем badge сразу при вставке
      this._updateTriggerBadge(btn);
    }

    // ── стартовый экран ───────────────────────
    const startFooter = container.querySelector(".start-input-footer");
    if (startFooter && !startFooter.querySelector(".tools-trigger-btn")) {
      const btn     = this._makeBtn();
      const sendBtn = startFooter.querySelector(".start-send-btn");
      sendBtn ? startFooter.insertBefore(btn, sendBtn) : startFooter.appendChild(btn);
      // Синхронизируем badge сразу при вставке
      this._updateTriggerBadge(btn);
    }
  }

  /* ── internal ───────────────────────────────────────────────────────── */

  _makeBtn() {
    const btn = document.createElement("button");
    btn.type      = "button";
    btn.className = "tools-trigger-btn attach-btn";
    btn.title     = "Настройка инструментов";
    btn.setAttribute("aria-label", "Выбор инструментов");
    btn.innerHTML = `
      <svg viewBox="0 0 24 24" fill="none" width="17" height="17">
        <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77
                 a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91
                 a6 6 0 0 1 7.94-7.94l-3.76 3.76z"
              stroke="currentColor" stroke-width="2"
              stroke-linecap="round" stroke-linejoin="round"/>
      </svg>`;
    btn.addEventListener("click", e => { e.stopPropagation(); this._toggle(btn); });
    return btn;
  }

  _toggle(triggerBtn) {
    this._open ? this._close() : this._openPanel(triggerBtn);
  }

  _openPanel(triggerBtn) {
    this._close();
    window.ModelPicker?._close?.();
    const panel = document.createElement("div");
    panel.className = "tools-panel";
    // Маркер: панель открыта на стартовом экране (поле ввода по центру).
    // Нужен, чтобы на мобилке дать отступ сверху и скролл секциям.
    if (document.querySelector(".chat-main.start-screen-mode")) {
      panel.classList.add("tp-on-start");
    }
    panel.setAttribute("role", "dialog");
    panel.setAttribute("aria-label", "Настройка инструментов");
    this._panelEl = panel;

    // ── Размер контекста ──────────────────────
    const ctxSection = document.createElement("div");
    ctxSection.className = "tp-section";
    ctxSection.innerHTML = `
      <div class="tp-section-title">
        <svg viewBox="0 0 24 24" fill="none" width="13" height="13">
          <circle cx="12" cy="12" r="10" stroke="currentColor" stroke-width="2"/>
          <line x1="12" y1="8" x2="12" y2="12"
                stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <line x1="12" y1="16" x2="12.01" y2="16"
                stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        Размер контекста
      </div>
      <div class="tp-context-row">
        ${CONTEXT_SIZES.map(s => `
          <button type="button"
                  class="tp-context-btn ${this._contextSize === s.id ? "active" : ""}"
                  data-size="${s.id}" title="${s.hint}">
            ${s.label}
            <span class="tp-context-label">${s.title}</span>
          </button>`).join("")}
      </div>`;

    ctxSection.querySelectorAll(".tp-context-btn").forEach(btn => {
      btn.addEventListener("click", () => {
        this._contextSize = btn.dataset.size;
        panel.querySelectorAll(".tp-context-btn").forEach(b =>
          b.classList.toggle("active", b.dataset.size === this._contextSize));
        this._updateTriggerBadge(triggerBtn);
      });
    });

    // ── Разделитель ───────────────────────────
    const divider = document.createElement("div");
    divider.className = "tp-divider";

    // ── Категории ─────────────────────────────
    const toolsSection = document.createElement("div");
    toolsSection.className = "tp-section";
    toolsSection.innerHTML = `
      <div class="tp-section-title">
        <svg viewBox="0 0 24 24" fill="none" width="13" height="13">
          <path d="M14.7 6.3a1 1 0 0 0 0 1.4l1.6 1.6a1 1 0 0 0 1.4 0l3.77-3.77
                   a6 6 0 0 1-7.94 7.94l-6.91 6.91a2.12 2.12 0 0 1-3-3l6.91-6.91
                   a6 6 0 0 1 7.94-7.94l-3.76 3.76z"
                stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
        </svg>
        Инструменты
      </div>`;

    TOOL_CATEGORIES.forEach(cat => {
      // adminOnly — не рендерим совсем если не админ
      if (cat.adminOnly && !this._isAdmin) return;

      const checked = this._selected.has(cat.id);
      const row     = document.createElement("label");
      row.className = `tp-row${cat.adminOnly ? " tp-row--admin" : ""}`;
      row.innerHTML = `
        <span class="tp-row-icon">${ICONS[cat.id] ?? ""}</span>
        <span class="tp-row-info">
          <span class="tp-row-label">${cat.label}</span>
          <span class="tp-row-desc">${cat.description}</span>
        </span>
        <span class="tp-checkbox ${checked ? "checked" : ""}">
          ${checked ? this._checkSVG() : ""}
        </span>
        <input type="checkbox" class="tp-hidden-cb"
               ${checked ? "checked" : ""}
               data-cat="${cat.id}">`;

      const cb  = row.querySelector(".tp-hidden-cb");
      const box = row.querySelector(".tp-checkbox");

      row.addEventListener("click", e => {
        e.preventDefault();
        const nowChecked = !cb.checked;
        cb.checked = nowChecked;
        if (nowChecked) {
          this._selected.add(cat.id);
          box.classList.add("checked");
          box.innerHTML = this._checkSVG();
        } else {
          this._selected.delete(cat.id);
          box.classList.remove("checked");
          box.innerHTML = "";
        }
        this._updateTriggerBadge(triggerBtn);
      });

      toolsSection.appendChild(row);
    });

    // ── Самоцикл агента (только админ) ─────────────
    if (this._isAdmin) {
      const loopRow = document.createElement("label");
      loopRow.className = "tp-row tp-row--admin";
      loopRow.innerHTML = `
        <span class="tp-row-icon"><svg viewBox="0 0 24 24" fill="none" width="17" height="17">
          <path d="M21 12a9 9 0 1 1-2.64-6.36" stroke="currentColor" stroke-width="2" stroke-linecap="round"/>
          <path d="M21 3v5h-5" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/>
        </svg></span>
        <span class="tp-row-info">
          <span class="tp-row-label">Самоцикл</span>
          <span class="tp-row-desc">Агент работает по установке из сообщения в цикле</span>
        </span>
        <span class="tp-checkbox ${this._loopEnabled ? "checked" : ""}">
          ${this._loopEnabled ? this._checkSVG() : ""}
        </span>
        <input type="checkbox" class="tp-hidden-cb" ${this._loopEnabled ? "checked" : ""} data-cat="__loop__">`;

      const loopCb  = loopRow.querySelector(".tp-hidden-cb");
      const loopBox = loopRow.querySelector(".tp-checkbox");
      loopRow.addEventListener("click", e => {
        e.preventDefault();
        e.stopPropagation();
        const nowChecked = !this._loopEnabled;
        this._loopEnabled = nowChecked;
        loopCb.checked = nowChecked;
        loopBox.classList.toggle("checked", nowChecked);
        loopBox.innerHTML = nowChecked ? this._checkSVG() : "";
        this._updateTriggerBadge(triggerBtn);
      });
      toolsSection.appendChild(loopRow);
    }

    // Весь контент — в скролл-контейнер, чтобы при нехватке высоты
    // появлялась прокрутка, а не панель уходила за экран.
    const scrollBody = document.createElement("div");
    scrollBody.className = "tp-scroll-body";
    scrollBody.appendChild(ctxSection);
    scrollBody.appendChild(divider);
    scrollBody.appendChild(toolsSection);
    panel.appendChild(scrollBody);

    document.body.appendChild(panel);
    this._positionPanel(panel, triggerBtn);
    this._open = true;
    triggerBtn.classList.add("active");
    this._updateTriggerBadge(triggerBtn);

    this._outsideHandler = e => {
      if (!panel.contains(e.target) && e.target !== triggerBtn) this._close();
    };
    setTimeout(() => document.addEventListener("click", this._outsideHandler), 0);

    this._escHandler = e => { if (e.key === "Escape") this._close(); };
    document.addEventListener("keydown", this._escHandler);
  }

  _positionPanel(panel, triggerBtn) {
    const rect    = triggerBtn.getBoundingClientRect();
    const MARGIN  = 8;
    const vh      = window.innerHeight;

    // Измеряем размеры панели без ограничений по высоте
    panel.style.cssText = "visibility:hidden;position:fixed;top:0;left:0;max-height:none;";
    const naturalH = panel.scrollHeight;   // полная высота без обрезки
    const pw       = panel.offsetWidth;
    panel.style.cssText = "";

    const spaceAbove = rect.top - MARGIN;
    const spaceBelow = vh - rect.bottom - MARGIN;

    // Предпочитаем открыться СВЕРХУ, fallback — снизу
    let openAbove = spaceAbove >= spaceBelow;
    // Если сверху совсем мало (< 120px) и снизу больше — снизу
    if (openAbove && spaceAbove < 120 && spaceBelow > spaceAbove) openAbove = false;

    let maxH, top;
    if (openAbove) {
      maxH = Math.min(naturalH, spaceAbove);
      top  = rect.top - maxH - MARGIN + window.scrollY;
    } else {
      maxH = Math.min(naturalH, spaceBelow);
      top  = rect.bottom + MARGIN + window.scrollY;
    }

    // На стартовом экране мобилки поле ввода по центру → панель, открытая
    // вверх, может упереться в верх экрана. Гарантируем минимум 10px сверху
    // и позволяем секциям скроллиться (maxH ограничен доступным местом).
    if (panel.classList.contains("tp-on-start") && window.innerWidth <= 768) {
      const TOP_MIN = 10;
      if (openAbove) {
        const avail = rect.top - MARGIN - TOP_MIN;
        maxH = Math.min(naturalH, Math.max(avail, 0));
        top  = rect.top - maxH - MARGIN + window.scrollY;
        if (top - window.scrollY < TOP_MIN) top = TOP_MIN + window.scrollY;
      }
    }

    // Горизонталь — прижимаем к правому краю кнопки, не выходим за viewport
    let left = rect.right - pw + window.scrollX;
    if (left < MARGIN) left = MARGIN;
    if (left + pw > window.innerWidth - MARGIN) left = window.innerWidth - pw - MARGIN;

    panel.style.top     = `${top}px`;
    panel.style.left    = `${left}px`;
    panel.style.maxHeight = `${maxH}px`;
  }

  _close() {
    if (this._panelEl) {
      this._panelEl.classList.add("tp-hiding");
      const el = this._panelEl;
      setTimeout(() => el.remove(), 180);
      this._panelEl = null;
    }
    this._open = false;
    document.querySelectorAll(".tools-trigger-btn").forEach(b => b.classList.remove("active"));
    if (this._outsideHandler) {
      document.removeEventListener("click", this._outsideHandler);
      this._outsideHandler = null;
    }
    if (this._escHandler) {
      document.removeEventListener("keydown", this._escHandler);
      this._escHandler = null;
    }
  }

  _updateTriggerBadge(btn) {
    const all = TOOL_CATEGORIES.filter(c => !c.adminOnly || this._isAdmin).length;
    const sel = this._selected.size;

    // Обновляем ВСЕ кнопки-триггеры в DOM (их может быть 2: стартовый + активный чат)
    const targets = btn
      ? [btn, ...document.querySelectorAll(".tools-trigger-btn")].filter(
          (b, i, arr) => arr.indexOf(b) === i   // dedupe
        )
      : document.querySelectorAll(".tools-trigger-btn");

    targets.forEach(target => {
      let badge = target.querySelector(".tp-badge");
      if (sel === 0) {
        if (!badge) { badge = document.createElement("span"); target.appendChild(badge); }
        badge.className   = "tp-badge tp-badge--warn";
        badge.textContent = "0";
      } else if (sel < all) {
        if (!badge) { badge = document.createElement("span"); target.appendChild(badge); }
        badge.className   = "tp-badge";
        badge.textContent = sel;
      } else {
        badge?.remove();
      }
    });
  }

  _checkSVG() {
    return `<svg viewBox="0 0 12 12" fill="none" width="10" height="10">
      <path d="M2 6l3 3 5-5" stroke="currentColor" stroke-width="2"
            stroke-linecap="round" stroke-linejoin="round"/>
    </svg>`;
  }
}

/* ── Синглтон ──────────────────────────────────────────────────────────── */
window.ToolsPanel = new ToolsPanel();
// Экспорт иконок тулов для переиспользования в tgChats.js (те же SVG, что в панели).
window.__TP_ICONS = ICONS;
export default window.ToolsPanel;
/* ══════════════════════════════════════════════════════════════════════════
   MODEL PICKER — выбор агента (GPT / Claude / GLM / Gemini)
   API:
     ModelPicker.getModel()        → 'coder' | 'designer' | 'universal' | 'gemini'
     ModelPicker.inject(container) → void
   Выбор хранится в localStorage.
══════════════════════════════════════════════════════════════════════════ */

const MP_MODELS = [
  {
    id:     'coder',
    label:  'GPT',
    // OpenAI — контур куба-узла
    svg: `<svg viewBox="0 0 24 24" fill="none" width="20" height="20"
               stroke="currentColor" stroke-width="1.5"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 3.2 L18.6 7 V15 L12 18.8 L5.4 15 V7 Z"/>
            <path d="M12 3.2 V11 L18.6 15"/>
            <path d="M5.4 7 L12 11 L12 18.8"/>
          </svg>`,
    svgSm: `<svg viewBox="0 0 24 24" fill="none" width="15" height="15"
               stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 3.2 L18.6 7 V15 L12 18.8 L5.4 15 V7 Z"/>
              <path d="M12 11 L18.6 7 M12 11 V18.8 M12 11 L5.4 7"/>
            </svg>`,
    hint:   'Модель от OpenAI. Сильна в коде, инструментах и длинном контексте. Показывает tool-calling.',
    accent: '#10a37f',
  },
  {
    id:     'designer',
    label:  'Claude',
    // Anthropic — искра-вспышка
    svg: `<svg viewBox="0 0 24 24" fill="none" width="20" height="20"
               stroke="currentColor" stroke-width="2.2"
               stroke-linecap="round" stroke-linejoin="round">
            <line x1="12" y1="4.5" x2="12" y2="19.5"/>
            <line x1="5.8" y1="8" x2="18.2" y2="16"/>
            <line x1="18.2" y1="8" x2="5.8" y2="16"/>
          </svg>`,
    svgSm: `<svg viewBox="0 0 24 24" fill="none" width="15" height="15"
               stroke="currentColor" stroke-width="2.4"
               stroke-linecap="round" stroke-linejoin="round">
              <line x1="12" y1="4.5" x2="12" y2="19.5"/>
              <line x1="5.8" y1="8" x2="18.2" y2="16"/>
              <line x1="18.2" y1="8" x2="5.8" y2="16"/>
            </svg>`,
    hint:   'Модель от Anthropic. Сильна в коде, генерирует презентации и отчёты. Показывает reasoning (размышления).',
    accent: '#d97757',
  },
  {
    id:     'universal',
    label:  'GLM',
    // Zhipu AI — стилизованная Z
    svg: `<svg viewBox="0 0 24 24" fill="none" width="20" height="20"
               stroke="currentColor" stroke-width="2.2"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M7 6 H17 L8 14 H17"/>
            <path d="M12 14 V20"/>
          </svg>`,
    svgSm: `<svg viewBox="0 0 24 24" fill="none" width="15" height="15"
               stroke="currentColor" stroke-width="2.4"
               stroke-linecap="round" stroke-linejoin="round">
              <path d="M7 6 H17 L8 14 H17"/>
              <path d="M12 14 V20"/>
            </svg>`,
    hint:   'Модель от Zhipu AI. Универсальная, показывает reasoning (размышления).',
    accent: '#8b5cf6',
  },
  {
    id:     'gemini',
    label:  'Gemini',
    // Gemini: четырёхконечная звезда с вогнутыми гранями
    svg: `<svg viewBox="0 0 24 24" fill="none" width="20" height="20"
               stroke="currentColor" stroke-width="1.75"
               stroke-linecap="round" stroke-linejoin="round">
            <path d="M12 2.5C12 7.8 16.2 12 21.5 12
                     C16.2 12 12 16.2 12 21.5
                     C12 16.2 7.8 12 2.5 12
                     C7.8 12 12 7.8 12 2.5Z"/>
          </svg>`,
    svgSm: `<svg viewBox="0 0 24 24" fill="none" width="15" height="15"
               stroke="currentColor" stroke-width="2"
               stroke-linecap="round" stroke-linejoin="round">
              <path d="M12 2.5C12 7.8 16.2 12 21.5 12
                       C16.2 12 12 16.2 12 21.5
                       C12 16.2 7.8 12 2.5 12
                       C7.8 12 12 7.8 12 2.5Z"/>
            </svg>`,
    hint:   'Модель от Google. Показывает tool-calling.',
    accent: '#4285f4',
  },
];

const MP_STORAGE_KEY = 'mp_selected_model';

// Доступность провайдеров под моделями. Тянется с /api/providers/status
// (проксирует agent_service). Бейдж в опции показывает, что модель может быть
// недоступна из-за ПРОВАЙДЕРА, а не из-за агента. Кешируем на клиенте, чтобы
// повторные открытия панели не дёргали сеть — бэкенд и так кеширует на 30с.
const MP_PROVIDERS_URL      = '/api/providers/status';
const MP_PROVIDERS_TTL_MS   = 25000;

class ModelPickerClass {
  constructor() {
    this._selected      = localStorage.getItem(MP_STORAGE_KEY) ?? 'coder';
    this._panel         = null;
    this._storedTrigger = null;
    // Кеш доступности провайдеров: {providers: {id: {up, name, reason}}, ts}
    this._providers     = null;
    this._providersTs   = 0;
    this._providersReq  = null;   // in-flight промис, чтобы не слать дубли
  }

  /* ── Публичный API ──────────────────────────────────────────────────── */

  getModel() { return this._selected; }

  /* Тянет доступность провайдеров с бэка (с TTL-кешем и дедупом запросов).
     Возвращает объект providers ({id: {up, name, reason?}}) или null. */
  async _fetchProviders(force = false) {
    const now = Date.now();
    if (!force && this._providers && (now - this._providersTs) < MP_PROVIDERS_TTL_MS) {
      return this._providers;
    }
    if (this._providersReq) return this._providersReq;

    this._providersReq = (async () => {
      try {
        const resp = await fetch(MP_PROVIDERS_URL, { credentials: 'same-origin' });
        if (!resp.ok) throw new Error('status ' + resp.status);
        const data = await resp.json();
        this._providers   = data?.providers ?? null;
        this._providersTs = Date.now();
        return this._providers;
      } catch (_) {
        // сеть/агент недоступны — оставляем нейтральное состояние (null)
        this._providers   = null;
        this._providersTs = Date.now();
        return null;
      } finally {
        this._providersReq = null;
      }
    })();
    return this._providersReq;
  }

  /* Проставляет состояние бейджа в уже отрисованной опции панели. */
  _applyProviderBadge(optEl, prov) {
    const badge = optEl.querySelector('.mp-option-status');
    if (!badge) return;
    const dot   = badge.querySelector('.mp-status-dot');
    const label = badge.querySelector('.mp-status-label');

    if (!prov) {
      // нет данных (агент недоступен / ещё грузится) — нейтрально
      badge.dataset.state = 'unknown';
      if (label) label.textContent = 'Проверка…';
      badge.title = 'Проверка доступности провайдера';
      return;
    }
    if (prov.up) {
      badge.dataset.state = 'up';
      if (label) label.textContent = 'Доступен';
      badge.title = prov.name ? `Провайдер ${prov.name}: доступен` : 'Провайдер доступен';
    } else {
      badge.dataset.state = 'down';
      if (label) label.textContent = 'Недоступен';
      badge.title = (prov.name ? `Провайдер ${prov.name}: недоступен` : 'Провайдер недоступен')
                  + (prov.reason ? ` (${prov.reason})` : '');
    }
  }

  /* Обновляет бейджи всех опций в открытой панели по кешу/фетчу. */
  async _refreshPanelBadges() {
    if (!this._panel) return;
    const opts = this._panel.querySelectorAll('.mp-option');
    // сперва — то, что уже есть в кеше (мгновенно), потом освежаем сетью
    const paint = (providers) => {
      opts.forEach(opt => this._applyProviderBadge(opt, providers?.[opt.dataset.model] ?? null));
    };
    paint(this._providers);
    const providers = await this._fetchProviders();
    if (this._panel) paint(providers);
  }

  inject(container) {
    this._injectInto(container.querySelector('.chat-input-footer'),  '#attach-btn');
    this._injectInto(container.querySelector('.start-input-footer'), '#start-attach-btn');
  }

  /* ── Внутренние ─────────────────────────────────────────────────────── */

  _injectInto(footer, attachSel) {
    if (!footer) return;
    if (footer.querySelector('.mp-trigger-btn')) return;

    const btn       = this._makeBtn();
    const attachBtn = footer.querySelector(attachSel) ?? footer.querySelector('.attach-btn');

    if (attachBtn?.nextSibling) {
      footer.insertBefore(btn, attachBtn.nextSibling);
    } else if (attachBtn) {
      attachBtn.after(btn);
    } else {
      footer.prepend(btn);
    }

    this._syncBtn(btn);
  }

  _makeBtn() {
    const btn       = document.createElement('button');
    btn.type        = 'button';
    btn.className   = 'mp-trigger-btn attach-btn';
    btn.title       = 'Выбор модели агента';
    btn.addEventListener('click', (e) => { e.stopPropagation(); this._toggle(btn); });
    return btn;
  }

  _syncBtn(btn) {
    const m = MP_MODELS.find(x => x.id === this._selected) ?? MP_MODELS[0];
    // Только иконка — круглая кнопка как attach-btn, без текста
    btn.innerHTML = m.svgSm;
    btn.dataset.modelId = m.id;
    btn.title = `Модель: ${m.label}`;
    btn.style.setProperty('--mp-accent', m.accent);
  }

  _syncAllBtns() {
    document.querySelectorAll('.mp-trigger-btn').forEach(b => this._syncBtn(b));
  }

  /* ── Дропдаун ───────────────────────────────────────────────────────── */

  _toggle(triggerBtn) {
    if (this._panel) { this._close(); return; }
    this._open(triggerBtn);
  }

  _open(triggerBtn) {
    // Закрываем чужие оверлеи
    window.ToolsPanel?._close?.();
    document.querySelectorAll('.mp-panel').forEach(p => p.classList.add('mp-hiding'));
    document.querySelectorAll('.chat-item-dropdown').forEach(d => d.remove());

    const panel = document.createElement('div');
    panel.className = 'mp-panel';
    panel.innerHTML = `
      <div class="mp-panel-title">Выбор модели</div>
      ${MP_MODELS.map(m => `
        <button class="mp-option${m.id === this._selected ? ' mp-option--active' : ''}"
                data-model="${m.id}" style="--mp-accent:${m.accent}" type="button">
          <span class="mp-option-icon">${m.svg}</span>
          <span class="mp-option-info">
            <span class="mp-option-label">${m.label}</span>
            <span class="mp-option-hint">${m.hint}</span>
            <span class="mp-option-status" data-state="unknown">
              <span class="mp-status-dot"></span>
              <span class="mp-status-label">Проверка…</span>
            </span>
          </span>
          <span class="mp-option-check">
            <svg viewBox="0 0 16 16" fill="none" width="13" height="13">
              <polyline points="2 12 6 15 14 4"
                        stroke="currentColor" stroke-width="2.2"
                        stroke-linecap="round" stroke-linejoin="round"/>
            </svg>
          </span>
        </button>
      `).join('')}
    `;

    panel.querySelectorAll('.mp-option').forEach(opt => {
      opt.addEventListener('click', () => {
        this._select(opt.dataset.model);
        this._close();
      });
    });

    document.body.appendChild(panel);
    this._panel         = panel;
    this._storedTrigger = triggerBtn;
    triggerBtn.classList.add('active');

    // Подтягиваем доступность провайдеров и раскрашиваем бейджи
    this._refreshPanelBadges();

    requestAnimationFrame(() => this._position(panel, triggerBtn));

    this._outsideHandler = (e) => {
      if (!panel.contains(e.target) && e.target !== triggerBtn) this._close();
    };
    this._escHandler = (e) => { if (e.key === 'Escape') this._close(); };
    setTimeout(() => {
      document.addEventListener('click',   this._outsideHandler);
      document.addEventListener('keydown', this._escHandler);
    }, 0);
  }

  _close() {
    if (!this._panel) return;
    const dying = this._panel;
    // Сразу блокируем клики — панель не должна перекрывать UI во время анимации
    dying.style.pointerEvents = 'none';
    dying.classList.add('mp-panel--hiding');
    // Страховка: если animationend не выстрелит — убиваем через таймаут
    const forceRemove = setTimeout(() => dying.remove(), 300);
    dying.addEventListener('animationend', () => { clearTimeout(forceRemove); dying.remove(); }, { once: true });
    this._panel = null;
    this._storedTrigger?.classList.remove('active');
    this._storedTrigger = null;
    document.removeEventListener('click',   this._outsideHandler);
    document.removeEventListener('keydown', this._escHandler);
  }

  _select(modelId) {
    this._selected = modelId;
    localStorage.setItem(MP_STORAGE_KEY, modelId);
    this._syncAllBtns();
  }

  _position(panel, triggerBtn) {
    const MARGIN = 8;
    const vh     = window.innerHeight;
    const rect   = triggerBtn.getBoundingClientRect();

    panel.style.cssText = 'visibility:hidden;position:fixed;top:0;left:0;';
    const naturalH = panel.scrollHeight;
    const pw       = panel.offsetWidth;
    panel.style.cssText = '';

    const spaceAbove = rect.top - MARGIN;
    const spaceBelow = vh - rect.bottom - MARGIN;
    const openAbove  = spaceAbove >= spaceBelow || spaceAbove >= naturalH;

    let top, maxH;
    if (openAbove) {
      maxH = Math.min(naturalH, spaceAbove);
      top  = rect.top - maxH - MARGIN + window.scrollY;
    } else {
      maxH = Math.min(naturalH, spaceBelow);
      top  = rect.bottom + MARGIN + window.scrollY;
    }

    let left = rect.right - pw + window.scrollX;
    if (left < MARGIN)                          left = MARGIN;
    if (left + pw > window.innerWidth - MARGIN) left = window.innerWidth - pw - MARGIN;

    panel.style.top       = `${top}px`;
    panel.style.left      = `${left}px`;
    panel.style.maxHeight = `${maxH}px`;
  }
}

window.ModelPicker = new ModelPickerClass();
