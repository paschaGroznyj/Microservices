// tgChats.js — раздел «Telegram-чаты» в профиле (#tg-chats-mount).
// Любой юзер видит и редактирует свои добавленные чаты.
// Переиспользает ICONS из tools-panel.js (window.__TP_ICONS).
//
// API (через api_gateway):
//   GET    /api/tg/chats               → { chats: [...] }
//   POST   /api/tg/chats               → встроить чат + настройки
//   DELETE /api/tg/chats/{chat_id}     → деактивировать
//   GET    /api/tg/settings/{chat_id}  → { settings }
//   POST   /api/tg/settings/{chat_id}  → обновить настройки

(function () {
  'use strict';

  const TG_MODELS = [
    { id: 'coder',     label: 'Кодер',     accent: '#6366f1',
      svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"><polyline points="7 8 3 12 7 16"/><polyline points="17 8 21 12 17 16"/><line x1="14" y1="4" x2="10" y2="20"/></svg>' },
    { id: 'designer',  label: 'Дизайнер',  accent: '#ec4899',
      svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2a10 10 0 0 1 0 20c-1.657 0-3-1.343-3-3 0-.828.336-1.578.879-2.121A3 3 0 0 0 8 14.5c0-1.657 1.343-3 3-3"/><circle cx="8" cy="9" r="1.2" fill="currentColor" stroke="none"/><circle cx="12" cy="6" r="1.2" fill="currentColor" stroke="none"/><circle cx="16" cy="9" r="1.2" fill="currentColor" stroke="none"/></svg>' },
    { id: 'universal', label: 'Универсал', accent: '#10b981',
      svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="1.75" stroke-linecap="round" stroke-linejoin="round"><line x1="4" y1="6" x2="20" y2="6"/><line x1="4" y1="12" x2="20" y2="12"/><line x1="4" y1="18" x2="20" y2="18"/><circle cx="9" cy="6" r="2" fill="var(--surface,#fff)" stroke="currentColor"/><circle cx="15" cy="12" r="2" fill="var(--surface,#fff)" stroke="currentColor"/><circle cx="9" cy="18" r="2" fill="var(--surface,#fff)" stroke="currentColor"/></svg>' },
  ];

  const TG_TOOLS = [
    { id: 'internet', label: 'Интернет' },
    { id: 'content',  label: 'Контент и файлы' },
    { id: 'sessions', label: 'Бортовой журнал' },
  ];

  const TG_TRIGGERS = [
    { id: 'mention',      label: 'По упоминанию' },
    { id: 'orchestrator', label: 'Оркестратор'  },
  ];

  function toolIcon(iconId) {
    if (window.__TP_ICONS && window.__TP_ICONS[iconId]) return window.__TP_ICONS[iconId];
    return '<svg viewBox="0 0 24 24" width="16" height="16" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/></svg>';
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
  let _chats = [];
  let _editing = null;
  let _form = Object.assign({}, DEFAULTS, { chat_id: '' });

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
      const msg = (data && (data.detail || data.error)) || ('HTTP ' + res.status);
      throw new Error(msg);
    }
    return data;
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

  function resetForm() {
    _editing = null;
    _form = Object.assign({}, DEFAULTS, { chat_id: '' });
  }

  function fillFormFrom(chat) {
    const s = chat.settings || {};
    _editing = chat.chat_id;
    _form = {
      chat_id: chat.chat_id,
      system_prompt: s.system_prompt || '',
      agent_model: s.agent_model || DEFAULTS.agent_model,
      selected_tools: s.selected_tools || DEFAULTS.selected_tools,
      trigger_mode: s.trigger_mode || DEFAULTS.trigger_mode,
      orchestrator_context_n: s.orchestrator_context_n != null ? s.orchestrator_context_n : DEFAULTS.orchestrator_context_n,
      proactive_enabled: s.proactive_enabled != null ? s.proactive_enabled : DEFAULTS.proactive_enabled,
    };
  }

  function modelDisplay() {
    var m = TG_MODELS.find(function (x) { return x.id === _form.agent_model; });
    return m ? '<span class="tgc-dd-ic" style="color:' + m.accent + '">' + m.svg + '</span>' + m.label : '\u2014';
  }

  function toolsDisplay() {
    if (!_form.selected_tools.length) return 'Не выбраны';
    return _form.selected_tools.map(function (id) {
      var t = TG_TOOLS.find(function (x) { return x.id === id; });
      return t ? t.label : id;
    }).join(', ');
  }

  function triggerDisplay() {
    var t = TG_TRIGGERS.find(function (x) { return x.id === _form.trigger_mode; });
    return t ? t.label : '\u2014';
  }

  function ddToggle(labelText, valueHtml, contentHtml, sectionId) {
    return '<div class="tgc-dd" id="' + sectionId + '">' +
      '<button class="tgc-dd-toggle" type="button">' +
        '<span class="tgc-dd-label">' + labelText + '</span>' +
        '<span class="tgc-dd-value">' + valueHtml + '</span>' +
        '<svg class="tgc-dd-chevron" viewBox="0 0 20 20" fill="none"><path d="M5 8l5 5 5-5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
      '</button>' +
      '<div class="tgc-dd-pop">' + contentHtml + '</div>' +
    '</div>';
  }

  function render() {
    if (!_mount) return;

    var listHtml = _chats.length === 0
      ? '<div class="tgc-empty">Нет встроенных чатов</div>'
      : _chats.map(function (c) {
          var s = c.settings || {};
          var mdl = TG_MODELS.find(function (m) { return m.id === s.agent_model; });
          var isEditing = _editing === c.chat_id;
          return '<div class="tgc-row' + (isEditing ? ' tgc-row--active' : '') + '">' +
            '<div class="tgc-row-main">' +
              '<span class="tgc-cid">' + c.chat_id + '</span>' +
              '<span class="tgc-badge tgc-badge--' + (c.chat_type || 'private') + '">' + (c.chat_type === 'group' ? 'группа' : 'личка') + '</span>' +
              (mdl ? '<span class="tgc-model" style="color:' + mdl.accent + '">' + mdl.svg + ' ' + mdl.label + '</span>' : '') +
            '</div>' +
            '<div class="tgc-row-actions">' +
              '<button class="tgc-btn" data-edit="' + c.chat_id + '" title="Редактировать"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M11 2l3 3-9 9H2v-3l9-9z" stroke-linecap="round" stroke-linejoin="round"/></svg></button>' +
              '<button class="tgc-btn tgc-del" data-del="' + c.chat_id + '" title="Удалить"><svg viewBox="0 0 16 16" width="14" height="14" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M3 4h10M6 4V2h4v2M5 4l1 10h4l1-10" stroke-linecap="round" stroke-linejoin="round"/></svg></button>' +
            '</div>' +
          '</div>';
        }).join('');

    var modelContent = TG_MODELS.map(function (m) {
      return '<button class="tgc-opt' + (m.id === _form.agent_model ? ' tgc-opt--on' : '') + '" data-model="' + m.id + '" type="button" style="--tgc-accent:' + m.accent + '">' +
        '<span class="tgc-opt-ic">' + m.svg + '</span>' +
        '<span class="tgc-opt-label">' + m.label + '</span>' +
      '</button>';
    }).join('');

    var toolsContent = TG_TOOLS.map(function (t) {
      var on = _form.selected_tools.indexOf(t.id) >= 0;
      return '<button class="tgc-topt' + (on ? ' tgc-topt--on' : '') + '" data-tool="' + t.id + '" type="button">' +
        '<span class="tgc-topt-ic">' + toolIcon(t.id) + '</span>' +
        '<span>' + t.label + '</span>' +
      '</button>';
    }).join('');

    var triggerContent = TG_TRIGGERS.map(function (t) {
      return '<button class="tgc-opt' + (t.id === _form.trigger_mode ? ' tgc-opt--on' : '') + '" data-trigger="' + t.id + '" type="button">' +
        '<span class="tgc-opt-label">' + t.label + '</span>' +
      '</button>';
    }).join('');

    _mount.innerHTML =
      '<div class="tgc-list">' + listHtml + '</div>' +
      '<div class="tgc-form">' +
        '<div class="tgc-form-title">' + (_editing ? 'Редактирование чата' : 'Встроить новый чат') + '</div>' +
        '<div class="tgc-field">' +
          '<span class="tgc-field-label">Chat ID</span>' +
          '<input class="tgc-input" id="tgc-chatid" type="text" placeholder="-1004313379449 или 123456789" value="' + _form.chat_id + '"' + (_editing ? ' disabled' : '') + '/>' +
          '<span class="tgc-field-hint">Минус \u2192 группа, без минуса \u2192 личка</span>' +
        '</div>' +
        ddToggle('Модель', modelDisplay(), modelContent, 'tgc-dd-model') +
        ddToggle('Инструменты', toolsDisplay(), toolsContent, 'tgc-dd-tools') +
        ddToggle('Триггер', triggerDisplay(), triggerContent, 'tgc-dd-trigger') +
        '<div class="tgc-field">' +
          '<span class="tgc-field-label">Глубина контекста</span>' +
          '<input class="tgc-input tgc-input--sm" id="tgc-ctxn" type="number" min="1" max="20" value="' + _form.orchestrator_context_n + '"/>' +
        '</div>' +
        '<label class="tgc-check">' +
          '<input type="checkbox" id="tgc-proactive"' + (_form.proactive_enabled ? ' checked' : '') + '/>' +
          '<span>Проактивные триггеры</span>' +
        '</label>' +
        '<div class="tgc-field">' +
          '<span class="tgc-field-label">Базовый промпт</span>' +
          '<textarea class="tgc-textarea" id="tgc-prompt" placeholder="Системный промпт для агента в этом чате...">' + _form.system_prompt + '</textarea>' +
        '</div>' +
        '<div class="tgc-actions">' +
          '<button class="tgc-save" id="tgc-save" type="button">' + (_editing ? 'Сохранить' : 'Встроить чат') + '</button>' +
          (_editing ? '<button class="tgc-cancel" id="tgc-cancel" type="button">Отмена</button>' : '') +
        '</div>' +
        '<div class="tgc-msg" id="tgc-msg"></div>' +
      '</div>';

    bindEvents();
    updatePanelCount();
  }

  function bindEvents() {
    _mount.querySelectorAll('.tgc-dd-toggle').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var dd = btn.closest('.tgc-dd');
        dd.classList.toggle('tgc-dd--open');
        _mount.querySelectorAll('.tgc-dd').forEach(function (other) {
          if (other !== dd) other.classList.remove('tgc-dd--open');
        });
      });
    });

    _mount.querySelectorAll('[data-model]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        _form.agent_model = e.currentTarget.dataset.model;
        render();
      });
    });

    _mount.querySelectorAll('[data-tool]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var id = e.currentTarget.dataset.tool;
        var i = _form.selected_tools.indexOf(id);
        if (i >= 0) _form.selected_tools.splice(i, 1); else _form.selected_tools.push(id);
        render();
      });
    });

    _mount.querySelectorAll('[data-trigger]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        _form.trigger_mode = e.currentTarget.dataset.trigger;
        render();
      });
    });

    _mount.querySelectorAll('[data-edit]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var cid = e.currentTarget.dataset.edit;
        var chat = _chats.find(function (c) { return c.chat_id === cid; });
        if (chat) { fillFormFrom(chat); render(); }
      });
    });

    _mount.querySelectorAll('[data-del]').forEach(function (b) {
      b.addEventListener('click', async function (e) {
        var cid = e.currentTarget.dataset.del;
        if (!confirm('Удалить чат ' + cid + '?')) return;
        try {
          await api('DELETE', '/api/tg/chats/' + encodeURIComponent(cid));
          if (_editing === cid) resetForm();
          await loadChats(); render();
        } catch (err) { showMsg('Ошибка: ' + err.message, true); }
      });
    });

    var cidEl = _mount.querySelector('#tgc-chatid');
    if (cidEl) cidEl.addEventListener('input', function (e) { _form.chat_id = e.target.value.trim(); });
    var ctxEl = _mount.querySelector('#tgc-ctxn');
    if (ctxEl) ctxEl.addEventListener('input', function (e) { _form.orchestrator_context_n = parseInt(e.target.value, 10) || 1; });
    var proEl = _mount.querySelector('#tgc-proactive');
    if (proEl) proEl.addEventListener('change', function (e) { _form.proactive_enabled = e.target.checked; });
    var prEl = _mount.querySelector('#tgc-prompt');
    if (prEl) prEl.addEventListener('input', function (e) { _form.system_prompt = e.target.value; });

    var saveBtn = _mount.querySelector('#tgc-save');
    if (saveBtn) saveBtn.addEventListener('click', onSave);
    var cancelBtn = _mount.querySelector('#tgc-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', function () { resetForm(); render(); });
  }

  function showMsg(text, isErr) {
    var el = _mount.querySelector('#tgc-msg');
    if (!el) return;
    el.textContent = text;
    el.className = 'tgc-msg ' + (isErr ? 'tgc-msg--err' : 'tgc-msg--ok');
    if (!isErr) setTimeout(function () { if (el) el.textContent = ''; }, 2500);
  }

  async function onSave() {
    var payload = {
      system_prompt: _form.system_prompt,
      agent_model: _form.agent_model,
      selected_tools: _form.selected_tools,
      trigger_mode: _form.trigger_mode,
      orchestrator_context_n: _form.orchestrator_context_n,
      proactive_enabled: _form.proactive_enabled,
    };
    try {
      if (_editing) {
        await api('POST', '/api/tg/settings/' + encodeURIComponent(_editing), payload);
        showMsg('Настройки сохранены');
      } else {
        var cid = (_form.chat_id || '').trim();
        if (!cid) { showMsg('Укажи Chat ID', true); return; }
        await api('POST', '/api/tg/chats', Object.assign({ chat_id: cid }, payload));
        showMsg('Чат встроен');
        resetForm();
      }
      await loadChats();
      render();
    } catch (err) {
      showMsg('Ошибка: ' + err.message, true);
    }
  }

  function updatePanelCount() {
    var cnt = document.getElementById('tgc-panel-count');
    if (cnt) cnt.textContent = _chats.length ? String(_chats.length) : '';
  }

  function wirePanel() {
    var panel = document.getElementById('tgc-panel');
    var toggle = document.getElementById('tgc-panel-toggle');
    if (!panel || !toggle || toggle.dataset.wired) return;
    toggle.dataset.wired = '1';
    toggle.addEventListener('click', function () {
      var open = panel.classList.toggle('open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  async function init() {
    _mount = document.getElementById('tg-chats-mount');
    var section = document.getElementById('tg-chats-section');
    if (!_mount || !section) return;
    section.style.display = '';
    wirePanel();
    resetForm();
    await loadChats();
    render();
    updatePanelCount();
  }

  window.TgChats = { init: init, reload: async function () { await loadChats(); render(); } };

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }
})();
