// maxChats.js — раздел «MAX-чаты» в профиле (#max-chats-mount).
// Любой юзер видит и редактирует свои добавленные MAX-чаты.
// Зеркало tgChats.js, но без триггеров/членов (в max_db только chats+settings).
//
// API (через api_gateway -> chats_service /max/*):
//   GET    /api/max/chats               → { chats: [...] }
//   POST   /api/max/chats               → встроить чат + настройки
//   DELETE /api/max/chats/{chat_id}     → деактивировать
//   GET    /api/max/settings/{chat_id}  → { settings }
//   POST   /api/max/settings/{chat_id}  → обновить настройки

(function () {
  'use strict';

  const MAX_MODELS = [
    { id: 'coder', label: 'GPT', accent: '#10a37f',
      svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 3l7 4v10l-7 4-7-4V7z"/><path d="M12 3v8l7 4M12 11l-7 4M12 11v10"/></svg>' },
    { id: 'designer', label: 'Claude', accent: '#d97757',
      svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2v6M12 16v6M2 12h6M16 12h6M5 5l4 4M15 15l4 4M19 5l-4 4M9 15l-4 4"/></svg>' },
    { id: 'universal', label: 'GLM', accent: '#8b5cf6',
      svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round"><path d="M6 5h12L7 19h11"/></svg>' },
    { id: 'gemini', label: 'Gemini', accent: '#4285f4',
      svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"><path d="M12 2c0 5.5 4.5 10 10 10-5.5 0-10 4.5-10 10 0-5.5-4.5-10-10-10 5.5 0 10-4.5 10-10z"/></svg>' },
  ];

  const MAX_TOOLS = [
    { id: 'internet', label: 'Интернет' },
    { id: 'content',  label: 'Контент и файлы' },
    { id: 'sessions', label: 'Бортовой журнал' },
  ];

  const DEFAULTS = {
    system_prompt: '',
    agent_name: '',
    agent_model: 'universal',
    selected_tools: ['internet', 'content'],
  };

  var _mount = null;
  var _chats = [];
  var _editing = null;   // chat_id редактируемого чата или null
  var _form = null;

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;');
  }

  function resetForm() {
    _editing = null;
    _form = {
      chat_id: '',
      system_prompt: DEFAULTS.system_prompt,
      agent_name: DEFAULTS.agent_name,
      agent_model: DEFAULTS.agent_model,
      selected_tools: DEFAULTS.selected_tools.slice(),
    };
  }

  function fillFormFrom(chat) {
    _editing = chat.chat_id;
    var s = chat.settings || {};
    _form = {
      chat_id: chat.chat_id,
      system_prompt: s.system_prompt || '',
      agent_name: s.agent_name || '',
      agent_model: s.agent_model || DEFAULTS.agent_model,
      selected_tools: Array.isArray(s.selected_tools) ? s.selected_tools.slice() : DEFAULTS.selected_tools.slice(),
    };
  }

  async function api(method, url, body) {
    var opt = { method: method, headers: {} };
    if (body !== undefined) { opt.headers['Content-Type'] = 'application/json'; opt.body = JSON.stringify(body); }
    var r = await fetch(url, opt);
    if (!r.ok) {
      var t = await r.text().catch(function () { return ''; });
      throw new Error(r.status + ' ' + t.slice(0, 200));
    }
    return r.json().catch(function () { return {}; });
  }

  async function loadChats() {
    var data = await api('GET', '/api/max/chats');
    _chats = (data && data.chats) || [];
  }

  function modelLabel(id) {
    var m = MAX_MODELS.find(function (x) { return x.id === id; });
    return m ? m.label : id;
  }

  function chatsListHtml() {
    if (!_chats.length) return '<div class="tgc-empty">Пока нет встроенных MAX-чатов</div>';
    return _chats.map(function (c) {
      var s = c.settings || {};
      return '<div class="tgc-chat-item' + (_editing === c.chat_id ? ' tgc-chat-item--active' : '') + '">' +
        '<div class="tgc-chat-head">' +
          '<span class="tgc-chat-id">' + esc(c.chat_id) + '</span>' +
          '<span class="tgc-chat-actions">' +
            '<button class="tgc-ic" type="button" data-edit="' + esc(c.chat_id) + '" title="Настроить">' +
              '<svg viewBox="0 0 20 20" width="14" height="14" fill="none"><path d="M4 13.5V16h2.5l7-7-2.5-2.5-7 7zM14.8 6.2l-1-1 1.4-1.4a.8.8 0 0 1 1.1 0a.8.8 0 0 1 0 1.1L14.8 6.2z" stroke="currentColor" stroke-width="1.3" stroke-linejoin="round"/></svg>' +
            '</button>' +
            '<button class="tgc-ic tgc-ic--del" type="button" data-del="' + esc(c.chat_id) + '" title="Удалить">' +
              '<svg viewBox="0 0 20 20" width="14" height="14" fill="none"><path d="M5 6h10M8 6V4h4v2M6 6l1 10h6l1-10" stroke="currentColor" stroke-width="1.3" stroke-linecap="round" stroke-linejoin="round"/></svg>' +
            '</button>' +
          '</span>' +
        '</div>' +
        '<div class="tgc-chat-meta">' + esc(modelLabel(s.agent_model)) +
          (s.agent_name ? ' · ' + esc(s.agent_name) : '') + '</div>' +
      '</div>';
    }).join('');
  }

  function modelsHtml() {
    return MAX_MODELS.map(function (m) {
      var on = _form.agent_model === m.id;
      return '<button type="button" class="tgc-model' + (on ? ' active' : '') + '" data-model="' + m.id + '"' +
        (on ? ' style="border-color:' + m.accent + ';color:' + m.accent + '"' : '') + '>' +
        '<span class="tgc-model-ic">' + m.svg + '</span>' + esc(m.label) + '</button>';
    }).join('');
  }

  function toolsHtml() {
    return MAX_TOOLS.map(function (t) {
      var on = _form.selected_tools.indexOf(t.id) !== -1;
      return '<button type="button" class="tgc-tool' + (on ? ' active' : '') + '" data-tool="' + t.id + '">' +
        esc(t.label) + '</button>';
    }).join('');
  }

  function render() {
    if (!_mount) return;
    if (!_form) resetForm();

    _mount.innerHTML =
      '<div class="tgc-wrap">' +
        '<div class="tgc-list">' + chatsListHtml() + '</div>' +
        '<div class="tgc-form">' +
          '<div class="tgc-form-title">' + (_editing ? 'Настройка чата ' + esc(_editing) : 'Встроить MAX-чат') + '</div>' +
          (_editing ? '' :
            '<div class="tgc-field">' +
              '<span class="tgc-field-label">ID чата MAX</span>' +
              '<input type="text" class="tgc-input" id="maxc-chatid" placeholder="Напр. 123456789" value="' + esc(_form.chat_id) + '"/>' +
            '</div>') +
          '<div class="tgc-field">' +
            '<span class="tgc-field-label">Имя агента</span>' +
            '<input type="text" class="tgc-input" id="maxc-name" placeholder="Напр. AuGent" value="' + esc(_form.agent_name) + '"/>' +
          '</div>' +
          '<div class="tgc-field">' +
            '<span class="tgc-field-label">Модель</span>' +
            '<div class="tgc-models">' + modelsHtml() + '</div>' +
          '</div>' +
          '<div class="tgc-field">' +
            '<span class="tgc-field-label">Инструменты</span>' +
            '<div class="tgc-tools">' + toolsHtml() + '</div>' +
          '</div>' +
          '<div class="tgc-field">' +
            '<span class="tgc-field-label">Базовый промпт</span>' +
            '<textarea class="tgc-textarea" id="maxc-prompt" placeholder="Системный промпт для агента в этом чате...">' + esc(_form.system_prompt) + '</textarea>' +
          '</div>' +
          '<div class="tgc-actions">' +
            '<button class="tgc-save" id="maxc-save" type="button">' + (_editing ? 'Сохранить' : 'Встроить чат') + '</button>' +
            (_editing ? '<button class="tgc-cancel" id="maxc-cancel" type="button">Отмена</button>' : '') +
          '</div>' +
          '<div class="tgc-msg" id="maxc-msg"></div>' +
        '</div>' +
      '</div>';

    bindEvents();
  }

  function showMsg(text, isErr) {
    var el = _mount.querySelector('#maxc-msg');
    if (!el) return;
    el.textContent = text;
    el.className = 'tgc-msg' + (isErr ? ' tgc-msg--err' : ' tgc-msg--ok');
  }

  function bindEvents() {
    _mount.querySelectorAll('[data-model]').forEach(function (b) {
      b.addEventListener('click', function (e) { _form.agent_model = e.currentTarget.dataset.model; render(); });
    });
    _mount.querySelectorAll('[data-tool]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var id = e.currentTarget.dataset.tool;
        var i = _form.selected_tools.indexOf(id);
        if (i >= 0) _form.selected_tools.splice(i, 1); else _form.selected_tools.push(id);
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
        if (!confirm('Удалить MAX-чат ' + cid + '?')) return;
        try {
          await api('DELETE', '/api/max/chats/' + encodeURIComponent(cid));
          if (_editing === cid) resetForm();
          
          await loadChats(); render();
        } catch (err) { showMsg('Ошибка: ' + err.message, true); }
      });
    });

    var cidEl = _mount.querySelector('#maxc-chatid');
    if (cidEl) cidEl.addEventListener('input', function (e) { _form.chat_id = e.target.value.trim(); });

    var nameEl = _mount.querySelector('#maxc-name');
    if (nameEl) nameEl.addEventListener('input', function (e) { _form.agent_name = e.target.value; });

    var promptEl = _mount.querySelector('#maxc-prompt');
    if (promptEl) promptEl.addEventListener('input', function (e) { _form.system_prompt = e.target.value; });

    var cancelEl = _mount.querySelector('#maxc-cancel');
    if (cancelEl) cancelEl.addEventListener('click', function () { resetForm(); render(); });

    var saveEl = _mount.querySelector('#maxc-save');
    if (saveEl) saveEl.addEventListener('click', onSave);
  }

  async function onSave() {
    try {
      if (_editing) {
        await api('POST', '/api/max/settings/' + encodeURIComponent(_editing), {
          system_prompt: _form.system_prompt,
          agent_name: _form.agent_name,
          agent_model: _form.agent_model,
          selected_tools: _form.selected_tools,
        });
        showMsg('Сохранено', false);
      } else {
        var cid = (_form.chat_id || '').trim();
        if (!cid) { showMsg('Укажите ID чата', true); return; }
        await api('POST', '/api/max/chats', {
          chat_id: cid,
          system_prompt: _form.system_prompt,
          agent_name: _form.agent_name,
          agent_model: _form.agent_model,
          selected_tools: _form.selected_tools,
        });
        showMsg('Чат встроен', false);
        resetForm();
      }
      await loadChats();
      render();
    } catch (err) {
      showMsg('Ошибка: ' + err.message, true);
    }
  }

  function wirePanel() {
    var panel = document.getElementById('maxc-panel');
    var toggle = document.getElementById('maxc-panel-toggle');
    if (!panel || !toggle || toggle.dataset.wired) return;
    toggle.dataset.wired = '1';
    toggle.addEventListener('click', function () {
      var open = panel.classList.toggle('open');
      toggle.setAttribute('aria-expanded', open ? 'true' : 'false');
    });
  }

  async function init() {
    _mount = document.getElementById('max-chats-mount');
    if (!_mount) return;
    wirePanel();
    resetForm();
    try {
      await loadChats();
    } catch (err) {
      _mount.innerHTML = '<div class="tgc-empty">Не удалось загрузить MAX-чаты: ' + esc(err.message) + '</div>';
      return;
    }
    render();
  }

  if (document.readyState === 'loading') {
    document.addEventListener('DOMContentLoaded', init);
  } else {
    init();
  }

  window.__maxChatsReload = async function () {
    try { await loadChats(); render(); } catch (e) {}
  };
})();
