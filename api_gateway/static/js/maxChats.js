(function () {
  'use strict';

  function getActualModels() {
    if (window.ModelPicker && typeof window.ModelPicker.getModels === 'function') {
      return window.ModelPicker.getModels().map(function (m) {
        return {
          id: m.id,
          label: m.label,
          accent: m.accent || '#10a37f',
          svg: m.svgSm || m.svg || ''
        };
      });
    }
    return [
      { id: 'coder',     label: 'GPT',    accent: '#10a37f', svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="2"><path d="M12 3.2 L18.6 7 V15 L12 18.8 L5.4 15 V7 Z"/><path d="M12 11 L18.6 7 M12 11 V18.8 M12 11 L5.4 7"/></svg>' },
      { id: 'designer',  label: 'Claude', accent: '#d97757', svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><line x1="12" y1="4.5" x2="12" y2="19.5"/><line x1="5.8" y1="8" x2="18.2" y2="16"/><line x1="18.2" y1="8" x2="5.8" y2="16"/></svg>' },
      { id: 'universal', label: 'GLM',    accent: '#8b5cf6', svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="2.4" stroke-linecap="round" stroke-linejoin="round"><path d="M7 6 H17 L8 14 H17"/><path d="M12 14 V20"/></svg>' },
      { id: 'gemini',    label: 'Gemini', accent: '#4285f4', svg: '<svg viewBox="0 0 24 24" fill="none" width="16" height="16" stroke="currentColor" stroke-width="2"><path d="M12 2.5C12 7.8 16.2 12 21.5 12 C16.2 12 12 16.2 12 21.5 C12 16.2 7.8 12 2.5 12 C7.8 12 12 7.8 12 2.5Z"/></svg>' }
    ];
  }

  var MAX_TOOLS = [
    { id: 'internet', label: 'Интернет' },
    { id: 'content',  label: 'Контент и файлы' },
    { id: 'sessions', label: 'Бортовой журнал' }
  ];

  var _mount = null;
  var _modalOverlay = null;
  var _modalOpen = false;
  var _chats = [];
  var _editing = null;
  var _form = null;

  function resetForm() {
    _editing = null;
    _form = {
      chat_id: '',
      agent_name: '',
      agent_model: 'coder',
      selected_tools: ['internet', 'content', 'sessions'],
      system_prompt: ''
    };
  }

  function fillFormFrom(chat) {
    var s = chat.settings || {};
    _editing = chat.chat_id;
    _form = {
      chat_id: chat.chat_id,
      agent_name: s.agent_name || '',
      agent_model: s.agent_model || 'coder',
      selected_tools: Array.isArray(s.selected_tools) ? s.selected_tools.slice() : ['internet', 'content', 'sessions'],
      system_prompt: s.system_prompt || ''
    };
  }

  function esc(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;')
      .replace(/</g, '&lt;')
      .replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;')
      .replace(/'/g, '&#39;');
  }

  async function api(method, url, body) {
    var opts = { method: method, credentials: 'include', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    var r = await fetch(url, opts);
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

  function chatsListHtml() {
    if (!_chats.length) return '<div class="tgc-empty">Нет встроенных чатов</div>';
    return _chats.map(function (c) {
      var s = c.settings || {};
      var mdl = getActualModels().find(function (x) { return x.id === s.agent_model; });
      return '<div class="tgc-row' + (_modalOpen && _editing === c.chat_id ? ' tgc-row--active' : '') + '">' +
        '<div class="tgc-row-main">' +
          '<span class="tgc-cid">' + esc(c.chat_id) + '</span>' +
          (s.agent_name ? '<span class="tgc-aname">' + esc(s.agent_name) + '</span>' : '') +
          (mdl ? '<span class="tgc-model" style="color:' + mdl.accent + '">' + mdl.svg + ' ' + mdl.label + '</span>' : '') +
        '</div>' +
        '<div class="tgc-row-actions">' +
          '<button class="tgc-btn" data-edit="' + esc(c.chat_id) + '" title="Редактировать"><svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M12 20h9"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg></button>' +
          '<button class="tgc-btn tgc-del" data-del="' + esc(c.chat_id) + '" title="Удалить"><svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" d="M10 11v6M14 11v6"/></svg></button>' +
        '</div>' +
      '</div>';
    }).join('');
  }

  function modelsHtml() {
    return getActualModels().map(function (m) {
      var on = _form.agent_model === m.id;
      return '<button class="tgc-opt' + (on ? ' tgc-opt--on' : '') + '" data-model="' + m.id + '" type="button" style="--tgc-accent:' + m.accent + '">' +
        '<span class="tgc-opt-ic">' + m.svg + '</span>' +
        '<span class="tgc-opt-label">' + esc(m.label) + '</span>' +
      '</button>';
    }).join('');
  }

  function toolsHtml() {
    return MAX_TOOLS.map(function (t) {
      var on = _form.selected_tools.indexOf(t.id) !== -1;
      return '<button class="tgc-topt' + (on ? ' tgc-topt--on' : '') + '" data-tool="' + t.id + '" type="button">' +
        '<span class="tgc-topt-ic">' + toolIcon(t.id) + '</span>' +
        '<span>' + esc(t.label) + '</span>' +
      '</button>';
    }).join('');
  }

  function toolIcon(iconId) {
    if (window.__TP_ICONS && window.__TP_ICONS[iconId]) return window.__TP_ICONS[iconId];
    return '<svg viewBox="0 0 24 24" width="16" height="16" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/></svg>';
  }

  function modelDisplay() {
    var m = getActualModels().find(function (x) { return x.id === _form.agent_model; });
    return m ? '<span class="tgc-dd-ic" style="color:' + m.accent + '">' + m.svg + '</span>' + m.label : '\u2014';
  }

  function toolsDisplay() {
    if (!_form.selected_tools.length) return 'Не выбраны';
    return _form.selected_tools.map(function (id) {
      var t = MAX_TOOLS.find(function (x) { return x.id === id; });
      return t ? t.label : id;
    }).join(', ');
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

  function ensureModal() {
    if (_modalOverlay && document.body.contains(_modalOverlay)) return _modalOverlay;
    var el = document.getElementById('maxc-modal-overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'maxc-modal-overlay';
      el.className = 'tgc-modal-overlay';
      el.setAttribute('role', 'dialog');
      el.setAttribute('aria-modal', 'true');
      el.setAttribute('aria-label', 'MAX-чат');
      document.body.appendChild(el);
    }
    el.addEventListener('mousedown', function (e) {
      if (e.target === el) closeModal();
    });
    _modalOverlay = el;
    return el;
  }

  function openModal(chat) {
    ensureModal();
    if (chat) {
      fillFormFrom(chat);
    } else {
      resetForm();
    }
    _modalOpen = true;
    _modalOverlay.classList.remove('closing');
    _modalOverlay.classList.add('active');
    render();
    setTimeout(function () {
      var cidInput = _modalOverlay && _modalOverlay.querySelector('#maxc-chatid:not([disabled])');
      if (cidInput) cidInput.focus();
    }, 60);
  }

  function closeModal() {
    if (!_modalOverlay || !_modalOpen) return;
    _modalOpen = false;
    _modalOverlay.classList.remove('active');
    _modalOverlay.classList.add('closing');
    resetForm();
    renderSidebar();
    setTimeout(function () {
      if (_modalOverlay) _modalOverlay.classList.remove('closing');
    }, 220);
  }

  function updatePanelCount() {
    var cnt = document.getElementById('maxc-panel-count');
    if (cnt) cnt.textContent = _chats.length ? String(_chats.length) : '';
  }

  function renderSidebar() {
    if (!_mount) return;
    _mount.innerHTML =
      '<div class="tgc-wrap">' +
        '<div class="tgc-list">' + chatsListHtml() + '</div>' +
        '<button class="tgc-open-modal-btn" id="maxc-open-create" type="button">' +
          '<svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M10 4v12M4 10h12"/></svg>' +
          '<span>Встроить чат</span>' +
        '</button>' +
      '</div>';

    var createBtn = _mount.querySelector('#maxc-open-create');
    if (createBtn) {
      createBtn.addEventListener('click', function () {
        openModal(null);
      });
    }

    _mount.querySelectorAll('[data-edit]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var cid = e.currentTarget.dataset.edit;
        var chat = _chats.find(function (c) { return c.chat_id === cid; });
        if (chat) openModal(chat);
      });
    });

    _mount.querySelectorAll('[data-del]').forEach(function (b) {
      b.addEventListener('click', async function (e) {
        var cid = e.currentTarget.dataset.del;
        if (!confirm('Удалить MAX-чат ' + cid + '?')) return;
        try {
          await api('DELETE', '/api/max/chats/' + encodeURIComponent(cid));
          if (_editing === cid) closeModal();
          await loadChats();
          render();
        } catch (err) {
          alert('Ошибка: ' + err.message);
        }
      });
    });

    updatePanelCount();
  }

  function renderModal() {
    if (!_modalOpen) return;
    ensureModal();
    if (!_form) resetForm();

    var maxIconSvg = '<svg class="tgc-modal-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7"><circle cx="12" cy="12" r="9"/><path d="M8 14V9l4 3 4-3v5" stroke-linecap="round" stroke-linejoin="round"/></svg>';

    _modalOverlay.innerHTML =
      '<div class="tgc-modal" id="maxc-modal">' +
        '<div class="tgc-modal-head">' +
          maxIconSvg +
          '<div class="tgc-modal-title">' + (_editing ? 'Редактирование MAX-чата' : 'Встроить MAX-чат') + '</div>' +
          '<button class="tgc-modal-close" id="maxc-modal-close" type="button" aria-label="Закрыть">✕</button>' +
        '</div>' +
        '<div class="tgc-modal-body">' +
          '<div class="tgc-form">' +
            '<div class="tgc-field">' +
              '<span class="tgc-field-label">ID чата MAX</span>' +
              '<input type="text" class="tgc-input" id="maxc-chatid" placeholder="Напр. 123456789" value="' + esc(_form.chat_id) + '"' + (_editing ? ' disabled' : '') + '/>' +
            '</div>' +
            '<div class="tgc-field">' +
              '<span class="tgc-field-label">Имя агента</span>' +
              '<input type="text" class="tgc-input" id="maxc-name" placeholder="Напр. AuGent" value="' + esc(_form.agent_name) + '"/>' +
            '</div>' +
            ddToggle('Модель', modelDisplay(), modelsHtml(), 'maxc-dd-model') +
            ddToggle('Инструменты', toolsDisplay(), toolsHtml(), 'maxc-dd-tools') +
            '<div class="tgc-field">' +
              '<span class="tgc-field-label">Базовый промпт</span>' +
              '<textarea class="tgc-textarea" id="maxc-prompt" placeholder="Системный промпт для агента в этом чате...">' + esc(_form.system_prompt) + '</textarea>' +
            '</div>' +
            '<div class="tgc-actions">' +
              '<button class="tgc-save" id="maxc-save" type="button">' + (_editing ? 'Сохранить' : 'Встроить чат') + '</button>' +
              '<button class="tgc-cancel" id="maxc-cancel" type="button">Отмена</button>' +
            '</div>' +
            '<div class="tgc-msg" id="maxc-msg"></div>' +
          '</div>' +
        '</div>' +
      '</div>';

    bindModalEvents();
  }

  function render() {
    renderSidebar();
    if (_modalOpen) renderModal();
  }

  function showMsg(text, isErr) {
    var el = _modalOverlay && _modalOverlay.querySelector('#maxc-msg');
    if (!el) return;
    el.textContent = text;
    el.className = 'tgc-msg' + (isErr ? ' tgc-msg--err' : ' tgc-msg--ok');
  }

  function bindModalEvents() {
    if (!_modalOverlay) return;

    var closeBtn = _modalOverlay.querySelector('#maxc-modal-close');
    if (closeBtn) closeBtn.addEventListener('click', closeModal);

    _modalOverlay.querySelectorAll('.tgc-dd-toggle').forEach(function (btn) {
      btn.addEventListener('click', function () {
        var dd = btn.closest('.tgc-dd');
        dd.classList.toggle('tgc-dd--open');
        _modalOverlay.querySelectorAll('.tgc-dd').forEach(function (other) {
          if (other !== dd) other.classList.remove('tgc-dd--open');
        });
      });
    });

    _modalOverlay.querySelectorAll('[data-model]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        _form.agent_model = e.currentTarget.dataset.model;
        renderModal();
      });
    });

    _modalOverlay.querySelectorAll('[data-tool]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var id = e.currentTarget.dataset.tool;
        var i = _form.selected_tools.indexOf(id);
        if (i === -1) _form.selected_tools.push(id);
        else _form.selected_tools.splice(i, 1);
        renderModal();
      });
    });

    var cidEl = _modalOverlay.querySelector('#maxc-chatid');
    if (cidEl) cidEl.addEventListener('input', function (e) { _form.chat_id = e.target.value.trim(); });

    var nameEl = _modalOverlay.querySelector('#maxc-name');
    if (nameEl) nameEl.addEventListener('input', function (e) { _form.agent_name = e.target.value; });

    var promptEl = _modalOverlay.querySelector('#maxc-prompt');
    if (promptEl) promptEl.addEventListener('input', function (e) { _form.system_prompt = e.target.value; });

    var cancelEl = _modalOverlay.querySelector('#maxc-cancel');
    if (cancelEl) cancelEl.addEventListener('click', closeModal);

    var saveEl = _modalOverlay.querySelector('#maxc-save');
    if (saveEl) saveEl.addEventListener('click', onSave);
  }

  async function onSave() {
    try {
      if (_editing) {
        await api('POST', '/api/max/settings/' + encodeURIComponent(_editing), {
          system_prompt: _form.system_prompt,
          agent_name: _form.agent_name,
          agent_model: _form.agent_model,
          selected_tools: _form.selected_tools
        });
        await loadChats();
        renderSidebar();
        showMsg('Настройки сохранены', false);
        setTimeout(function () { closeModal(); }, 450);
      } else {
        var cid = (_form.chat_id || '').trim();
        if (!cid) { showMsg('Укажите ID чата', true); return; }
        await api('POST', '/api/max/chats', {
          chat_id: cid,
          system_prompt: _form.system_prompt,
          agent_name: _form.agent_name,
          agent_model: _form.agent_model,
          selected_tools: _form.selected_tools
        });
        await loadChats();
        renderSidebar();
        showMsg('Чат встроен', false);
        setTimeout(function () { closeModal(); }, 450);
      }
    } catch (err) {
      showMsg('Ошибка: ' + err.message, true);
    }
  }

  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && _modalOpen) {
      e.stopPropagation();
      closeModal();
    }
  });

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
