// tgChats.js
// Раздел «Telegram-чаты» в профиле (#tg-chats-mount). ТОЛЬКО для админов.
// Встраивание/редактирование per-chat настроек TG-ботов без прямых SQL.
// Переиспользует список моделей и категорий тулов из tools-panel.js
// (MP_MODELS / TOOL_CATEGORIES / ICONS) — те же id и иконки.
//
// API (через api_gateway, гейт админа на бэке):
//   GET    /api/tg/chats               → { chats: [{chat_id, chat_type, is_active, settings:{...}}] }
//   POST   /api/tg/chats               → встроить чат + настройки
//   DELETE /api/tg/chats/{chat_id}     → деактивировать
//   GET    /api/tg/settings/{chat_id}  → { settings }
//   POST   /api/tg/settings/{chat_id}  → обновить настройки

(function () {
  'use strict';

  // ── Метаданные (дубль минимального набора, чтобы не зависеть от порядка загрузки модулей) ──
  const TG_MODELS = [
    { id: 'coder',     label: 'Кодер',     accent: '#6366f1' },
    { id: 'designer',  label: 'Дизайнер',  accent: '#ec4899' },
    { id: 'universal', label: 'Универсал', accent: '#10b981' },
  ];

  const TG_TOOLS = [
    { id: 'internet', label: 'Интернет',              icon: 'internet' },
    { id: 'content',  label: 'Контент и файлы',       icon: 'content'  },
    { id: 'sessions', label: 'Бортовой журнал агента', icon: 'sessions' },
  ];

  const TG_TRIGGERS = [
    { id: 'mention',      label: 'По упоминанию' },
    { id: 'orchestrator', label: 'Оркестратор'  },
  ];

  // SVG иконки тулов — берём из глобального ICONS, если модуль tools-panel уже загружен,
  // иначе минимальный фолбэк.
  function toolIcon(iconId) {
    if (window.__TP_ICONS && window.__TP_ICONS[iconId]) return window.__TP_ICONS[iconId];
    return `<svg viewBox="0 0 24 24" width="17" height="17" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/></svg>`;
  }

  const DEFAULTS = {
    system_prompt: '',
    agent_model: 'designer',
    selected_tools: ['internet', 'content'],
    trigger_mode: 'orchestrator',
    orchestrator_context_n: 6,
    proactive_enabled: false,
  };

  let _mount = null;
  let _chats = [];          // список встроенных чатов
  let _editing = null;      // chat_id редактируемого или null (режим создания)
  let _form = { ...DEFAULTS, chat_id: '' };

  async function api(method, path, body) {
    const opts = { method, credentials: 'include', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    const res = await fetch(path, opts);
    let data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      const msg = (data && (data.detail || data.error)) || `HTTP ${res.status}`;
      throw new Error(msg);
    }
    return data;
  }

  async function isAdmin() {
    try {
      const res = await fetch('/api/auth/permissions', { credentials: 'include' });
      if (!res.ok) return false;
      const { is_admin } = await res.json();
      return !!is_admin;
    } catch (_) { return false; }
  }

  async function loadChats() {
    try {
      const data = await api('GET', '/api/tg/chats');
      _chats = (data && data.chats) || [];
    } catch (e) {
      _chats = [];
      console.warn('[tgChats] load failed:', e.message);
    }
  }

  function chatTypeBadge(t) {
    if (t === 'group') return '<span class="tgc-badge tgc-badge--group">группа</span>';
    return '<span class="tgc-badge tgc-badge--private">личка</span>';
  }

  function resetForm() {
    _editing = null;
    _form = { ...DEFAULTS, chat_id: '' };
  }

  function fillFormFrom(chat) {
    const s = chat.settings || {};
    _editing = chat.chat_id;
    _form = {
      chat_id: chat.chat_id,
      system_prompt: s.system_prompt || '',
      agent_model: s.agent_model || DEFAULTS.agent_model,
      selected_tools: Array.isArray(s.selected_tools) ? s.selected_tools.slice() : DEFAULTS.selected_tools.slice(),
      trigger_mode: s.trigger_mode || DEFAULTS.trigger_mode,
      orchestrator_context_n: s.orchestrator_context_n ?? DEFAULTS.orchestrator_context_n,
      proactive_enabled: !!s.proactive_enabled,
    };
  }

  function render() {
    if (!_mount) return;

    const listHtml = _chats.length
      ? _chats.map(c => `
        <div class="tgc-row ${_editing === c.chat_id ? 'tgc-row--active' : ''}" data-cid="${c.chat_id}">
          <div class="tgc-row-main">
            <span class="tgc-cid">${c.chat_id}</span>
            ${chatTypeBadge(c.chat_type)}
            <span class="tgc-model">${(c.settings && c.settings.agent_model) || '—'}</span>
          </div>
          <div class="tgc-row-actions">
            <button class="tgc-btn tgc-edit"  data-cid="${c.chat_id}" title="Редактировать">✎</button>
            <button class="tgc-btn tgc-del"   data-cid="${c.chat_id}" title="Удалить из allow-list">✕</button>
          </div>
        </div>`).join('')
      : '<div class="tgc-empty">Пока нет встроенных чатов</div>';

    const modelsHtml = TG_MODELS.map(m => `
      <button type="button" class="tgc-chip ${_form.agent_model === m.id ? 'tgc-chip--on' : ''}"
              data-model="${m.id}" style="--acc:${m.accent}">${m.label}</button>`).join('');

    const toolsHtml = TG_TOOLS.map(t => {
      const on = _form.selected_tools.includes(t.id);
      return `<button type="button" class="tgc-tool ${on ? 'tgc-tool--on' : ''}" data-tool="${t.id}">
                <span class="tgc-tool-ic">${toolIcon(t.icon)}</span>${t.label}
              </button>`;
    }).join('');

    const trigHtml = TG_TRIGGERS.map(tr => `
      <button type="button" class="tgc-chip ${_form.trigger_mode === tr.id ? 'tgc-chip--on' : ''}"
              data-trigger="${tr.id}">${tr.label}</button>`).join('');

    _mount.innerHTML = `
      <div class="tgc-wrap">
        <div class="tgc-list">${listHtml}</div>

        <div class="tgc-form">
          <div class="tgc-form-title">${_editing ? 'Редактирование чата' : 'Встроить новый чат'}</div>

          <label class="tgc-label">Chat ID</label>
          <input class="tgc-input" id="tgc-chatid" type="text" placeholder="-1004313379449 или 123456789"
                 value="${_form.chat_id}" ${_editing ? 'disabled' : ''}>
          <div class="tgc-hint">Минус в начале → группа, без минуса → личка. Тип определится автоматически.</div>

          <label class="tgc-label">Базовая модель</label>
          <div class="tgc-chips" id="tgc-models">${modelsHtml}</div>

          <label class="tgc-label">Инструменты</label>
          <div class="tgc-tools" id="tgc-tools">${toolsHtml}</div>

          <label class="tgc-label">Режим триггера</label>
          <div class="tgc-chips" id="tgc-triggers">${trigHtml}</div>

          <label class="tgc-label">Глубина контекста оркестратора</label>
          <input class="tgc-input tgc-input--sm" id="tgc-ctxn" type="number" min="1" max="40"
                 value="${_form.orchestrator_context_n}">

          <label class="tgc-check">
            <input type="checkbox" id="tgc-proactive" ${_form.proactive_enabled ? 'checked' : ''}>
            Проактивные триггеры (рассылки, поки)
          </label>

          <label class="tgc-label">Базовый промпт</label>
          <textarea class="tgc-textarea" id="tgc-prompt" rows="4"
                    placeholder="Системный промпт для агента в этом чате...">${_form.system_prompt}</textarea>

          <div class="tgc-actions">
            <button class="tgc-save" id="tgc-save">${_editing ? 'Сохранить' : 'Встроить чат'}</button>
            ${_editing ? '<button class="tgc-cancel" id="tgc-cancel">Отмена</button>' : ''}
          </div>
          <div class="tgc-msg" id="tgc-msg"></div>
        </div>
      </div>`;

    bindEvents();
  }

  function bindEvents() {
    // список: edit / delete
    _mount.querySelectorAll('.tgc-edit').forEach(b => b.addEventListener('click', e => {
      const cid = e.currentTarget.dataset.cid;
      const chat = _chats.find(c => String(c.chat_id) === String(cid));
      if (chat) { fillFormFrom(chat); render(); }
    }));
    _mount.querySelectorAll('.tgc-del').forEach(b => b.addEventListener('click', async e => {
      const cid = e.currentTarget.dataset.cid;
      if (!confirm(`Удалить чат ${cid} из allow-list бота?`)) return;
      try {
        await api('DELETE', `/api/tg/chats/${encodeURIComponent(cid)}`);
        if (String(_editing) === String(cid)) resetForm();
        await loadChats();
        render();
      } catch (err) { showMsg('Ошибка удаления: ' + err.message, true); }
    }));

    // model chips
    _mount.querySelectorAll('[data-model]').forEach(b => b.addEventListener('click', e => {
      _form.agent_model = e.currentTarget.dataset.model; render();
    }));
    // tool toggles
    _mount.querySelectorAll('[data-tool]').forEach(b => b.addEventListener('click', e => {
      const id = e.currentTarget.dataset.tool;
      const i = _form.selected_tools.indexOf(id);
      if (i >= 0) _form.selected_tools.splice(i, 1); else _form.selected_tools.push(id);
      render();
    }));
    // trigger chips
    _mount.querySelectorAll('[data-trigger]').forEach(b => b.addEventListener('click', e => {
      _form.trigger_mode = e.currentTarget.dataset.trigger; render();
    }));

    // inputs (сохраняем в _form без ререндера, чтобы не терять фокус)
    const cidEl = _mount.querySelector('#tgc-chatid');
    if (cidEl) cidEl.addEventListener('input', e => { _form.chat_id = e.target.value.trim(); });
    const ctxEl = _mount.querySelector('#tgc-ctxn');
    if (ctxEl) ctxEl.addEventListener('input', e => { _form.orchestrator_context_n = parseInt(e.target.value, 10) || 1; });
    const proEl = _mount.querySelector('#tgc-proactive');
    if (proEl) proEl.addEventListener('change', e => { _form.proactive_enabled = e.target.checked; });
    const prEl = _mount.querySelector('#tgc-prompt');
    if (prEl) prEl.addEventListener('input', e => { _form.system_prompt = e.target.value; });

    // save
    const saveBtn = _mount.querySelector('#tgc-save');
    if (saveBtn) saveBtn.addEventListener('click', onSave);
    const cancelBtn = _mount.querySelector('#tgc-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', () => { resetForm(); render(); });
  }

  function showMsg(text, isErr) {
    const el = _mount.querySelector('#tgc-msg');
    if (!el) return;
    el.textContent = text;
    el.className = 'tgc-msg ' + (isErr ? 'tgc-msg--err' : 'tgc-msg--ok');
    if (!isErr) setTimeout(() => { if (el) el.textContent = ''; }, 2500);
  }

  async function onSave() {
    const payload = {
      system_prompt: _form.system_prompt,
      agent_model: _form.agent_model,
      selected_tools: _form.selected_tools,
      trigger_mode: _form.trigger_mode,
      orchestrator_context_n: _form.orchestrator_context_n,
      proactive_enabled: _form.proactive_enabled,
    };
    try {
      if (_editing) {
        await api('POST', `/api/tg/settings/${encodeURIComponent(_editing)}`, payload);
        showMsg('Настройки сохранены');
      } else {
        const cid = (_form.chat_id || '').trim();
        if (!cid) { showMsg('Укажи Chat ID', true); return; }
        await api('POST', '/api/tg/chats', { chat_id: cid, ...payload });
        showMsg('Чат встроен');
        resetForm();
      }
      await loadChats();
      render();
    } catch (err) {
      showMsg('Ошибка: ' + err.message, true);
    }
  }

  async function init() {
    _mount = document.getElementById('tg-chats-mount');
    const section = document.getElementById('tg-chats-section');
    if (!_mount || !section) return;

    const admin = await isAdmin();
    if (!admin) { section.style.display = 'none'; return; }
    section.style.display = '';

    resetForm();
    await loadChats();
    render();
  }

  // Публичный хук: перезагрузить при открытии профиля (на случай изменений).
  window.TgChats = { init, reload: async () => { await loadChats(); render(); } };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
