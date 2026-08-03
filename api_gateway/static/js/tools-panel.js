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
  {
    id:          "pm_research",
    label:       "Process Mining",
    description: "Поиск статей, ревью, дайджесты, PDF",
    adminOnly:   false
  },
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
    label:       "Сессии и память",
    description: "Управление историей диалогов",
    adminOnly:   false
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
    }

    // ── стартовый экран ───────────────────────
    const startFooter = container.querySelector(".start-input-footer");
    if (startFooter && !startFooter.querySelector(".tools-trigger-btn")) {
      const btn     = this._makeBtn();
      const sendBtn = startFooter.querySelector(".start-send-btn");
      sendBtn ? startFooter.insertBefore(btn, sendBtn) : startFooter.appendChild(btn);
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

    const panel = document.createElement("div");
    panel.className = "tools-panel";
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

    panel.appendChild(ctxSection);
    panel.appendChild(divider);
    panel.appendChild(toolsSection);

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
    const rect   = triggerBtn.getBoundingClientRect();
    const MARGIN = 8;

    panel.style.cssText = "visibility:hidden;display:block;";
    const ph = panel.offsetHeight;
    const pw = panel.offsetWidth;
    panel.style.cssText = "";

    let top  = rect.top - ph - MARGIN + window.scrollY;
    let left = rect.left + window.scrollX;

    if (left + pw > window.innerWidth - MARGIN) left = window.innerWidth - pw - MARGIN;
    if (top < MARGIN) top = rect.bottom + MARGIN + window.scrollY;

    panel.style.top  = `${top}px`;
    panel.style.left = `${left}px`;
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
    let badge  = btn.querySelector(".tp-badge");
    const all  = TOOL_CATEGORIES.filter(c => !c.adminOnly || this._isAdmin).length;
    const sel  = this._selected.size;

    if (sel === 0) {
      if (!badge) { badge = document.createElement("span"); btn.appendChild(badge); }
      badge.className   = "tp-badge tp-badge--warn";
      badge.textContent = "0";
    } else if (sel < all) {
      if (!badge) { badge = document.createElement("span"); btn.appendChild(badge); }
      badge.className   = "tp-badge";
      badge.textContent = sel;
    } else {
      badge?.remove();
    }
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
export default window.ToolsPanel;
