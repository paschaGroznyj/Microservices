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

  var TG_TOOLS = [
    { id: 'internet', label: 'Интернет' },
    { id: 'content',  label: 'Контент и файлы' },
    { id: 'sessions', label: 'Бортовой журнал' }
  ];

  var TG_TRIGGERS = [
    { id: 'mention',      label: 'По упоминанию' },
    { id: 'orchestrator', label: 'Оркестратор'  }
  ];

  var TG_DOW = [
    { v: 1, s: 'Пн' }, { v: 2, s: 'Вт' }, { v: 3, s: 'Ср' },
    { v: 4, s: 'Чт' }, { v: 5, s: 'Пт' }, { v: 6, s: 'Сб' }, { v: 7, s: 'Вс' }
  ];
  var MONTHS_RU = ['Январь','Февраль','Март','Апрель','Май','Июнь','Июль','Август','Сентябрь','Октябрь','Ноябрь','Декабрь'];
  var DOW_HDR = ['Пн','Вт','Ср','Чт','Пт','Сб','Вс'];
  var CHECK_SVG = '<svg viewBox="0 0 14 14" fill="none" width="11" height="11"><path d="M2.5 7.2L5.6 10.3L11.5 3.8" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>';

  var _triggers = [];
  var _trForm = null;
  var _calOpen = false;
  var _calView = null;

  function trDefaults() {
    return {
      id: null, kind: 'recurring', title: '', time_msk: '09:00',
      days_of_week: [], trigger_date: '', prompt: '', addressed: false, is_active: true
    };
  }

  function toolIcon(iconId) {
    if (window.__TP_ICONS && window.__TP_ICONS[iconId]) return window.__TP_ICONS[iconId];
    return '<svg viewBox="0 0 24 24" width="16" height="16" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/></svg>';
  }

  var DEFAULTS = {
    system_prompt: '',
    agent_name: '',
    agent_model: 'coder',
    selected_tools: ['internet', 'content', 'sessions'],
    trigger_mode: 'mention',
    orchestrator_context_n: 5,
    proactive_enabled: false
  };

  var _mount = null;
  var _modalOverlay = null;
  var _modalOpen = false;
  var _chats = [];
  var _editing = null;
  var _form = Object.assign({}, DEFAULTS, { chat_id: '' });

  async function api(method, path, body) {
    var opts = { method: method, credentials: 'include', headers: {} };
    if (body !== undefined) {
      opts.headers['Content-Type'] = 'application/json';
      opts.body = JSON.stringify(body);
    }
    var res = await fetch(path, opts);
    var data = null;
    try { data = await res.json(); } catch (_) {}
    if (!res.ok) {
      var msg = (data && (data.detail || data.error)) || ('HTTP ' + res.status);
      throw new Error(msg);
    }
    return data;
  }

  async function loadChats() {
    try {
      var data = await api('GET', '/api/tg/chats');
      _chats = (data && data.chats) || [];
    } catch (e) {
      _chats = [];
      console.warn('[tgChats] load failed:', e.message);
    }
  }

  function resetForm() {
    _editing = null;
    _form = Object.assign({}, DEFAULTS, { chat_id: '', selected_tools: DEFAULTS.selected_tools.slice() });
    _triggers = [];
    _trForm = null;
    _calOpen = false;
  }

  function fillFormFrom(chat) {
    var s = chat.settings || {};
    _editing = chat.chat_id;
    _form = {
      chat_id: chat.chat_id,
      system_prompt: s.system_prompt || '',
      agent_name: s.agent_name || '',
      agent_model: s.agent_model || DEFAULTS.agent_model,
      selected_tools: Array.isArray(s.selected_tools) ? s.selected_tools.slice() : DEFAULTS.selected_tools.slice(),
      trigger_mode: s.trigger_mode || DEFAULTS.trigger_mode,
      orchestrator_context_n: s.orchestrator_context_n != null ? s.orchestrator_context_n : DEFAULTS.orchestrator_context_n,
      proactive_enabled: s.proactive_enabled != null ? s.proactive_enabled : DEFAULTS.proactive_enabled
    };
    _triggers = [];
    _trForm = null;
    _calOpen = false;
    loadTriggers(chat.chat_id);
  }

  async function loadTriggers(chatId) {
    try {
      var data = await api('GET', '/api/tg/triggers/' + encodeURIComponent(chatId));
      _triggers = (data && data.triggers) || [];
    } catch (e) {
      _triggers = [];
    }
    if (_modalOpen) renderModal();
  }

  function modelDisplay() {
    var m = getActualModels().find(function (x) { return x.id === _form.agent_model; });
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

  function esc(s) {
    return String(s == null ? '' : s)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function fmtDateRu(iso) {
    if (!iso) return '';
    var p = iso.split('-');
    if (p.length !== 3) return iso;
    return p[2] + '.' + p[1] + '.' + p[0];
  }

  function dowLabel(arr) {
    if (!arr || arr.length === 0) return 'все дни';
    if (arr.length === 7) return 'все дни';
    return TG_DOW.filter(function (d) { return arr.indexOf(d.v) !== -1; })
                 .map(function (d) { return d.s; }).join(', ');
  }

  function triggersListHtml() {
    if (!_triggers || _triggers.length === 0) {
      return '<div class="tgc-tr-empty">Триггеров пока нет</div>';
    }
    return _triggers.map(function (t) {
      var meta;
      if (t.kind === 'once') {
        meta = 'Разово · ' + fmtDateRu(t.trigger_date) + ' · ' + (t.time_msk || '') + ' МСК';
      } else {
        meta = 'Регулярно · ' + (t.time_msk || '') + ' МСК · ' + dowLabel(t.days_of_week);
      }
      return '<div class="tgc-tr-item' + (t.is_active ? '' : ' tgc-tr-item--off') + '">' +
        '<div class="tgc-tr-item-head">' +
          '<span class="tgc-tr-title">' + esc(t.title || (t.kind === 'once' ? 'Разовый триггер' : 'Регулярный триггер')) + '</span>' +
          '<span class="tgc-tr-actions">' +
            '<button class="tgc-tr-ic" type="button" data-tr-edit="' + t.id + '" title="Редактировать">' +
              '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M12 20h9"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg>' +
            '</button>' +
            '<button class="tgc-tr-ic tgc-tr-ic--del" type="button" data-tr-del="' + t.id + '" title="Удалить">' +
              '<svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" d="M10 11v6M14 11v6"/></svg>' +
            '</button>' +
          '</span>' +
        '</div>' +
        '<div class="tgc-tr-meta">' + meta + (t.addressed ? ' · адресно' : ' · всем') + '</div>' +
        (t.prompt ? '<div class="tgc-tr-prompt">' + esc(t.prompt) + '</div>' : '') +
      '</div>';
    }).join('');
  }

  function calendarHtml() {
    var view = _calView || (function () {
      var d = _trForm && _trForm.trigger_date ? new Date(_trForm.trigger_date + 'T00:00:00') : new Date();
      return { year: d.getFullYear(), month: d.getMonth() };
    })();
    _calView = view;
    var first = new Date(view.year, view.month, 1);
    var startShift = (first.getDay() + 6) % 7;
    var daysInMonth = new Date(view.year, view.month + 1, 0).getDate();
    var sel = _trForm && _trForm.trigger_date ? _trForm.trigger_date : '';

    var cells = '';
    var i;
    for (i = 0; i < startShift; i++) cells += '<span class="tgc-cal-cell tgc-cal-cell--empty"></span>';
    for (i = 1; i <= daysInMonth; i++) {
      var mm = String(view.month + 1).padStart(2, '0');
      var dd = String(i).padStart(2, '0');
      var iso = view.year + '-' + mm + '-' + dd;
      var isSel = iso === sel;
      cells += '<button type="button" class="tgc-cal-cell' + (isSel ? ' tgc-cal-cell--sel' : '') + '" data-cal-day="' + iso + '">' + i + '</button>';
    }
    var hdr = DOW_HDR.map(function (d) { return '<span class="tgc-cal-hdr">' + d + '</span>'; }).join('');

    return '<div class="tgc-cal">' +
      '<div class="tgc-cal-nav">' +
        '<button type="button" class="tgc-cal-arrow" data-cal-prev>&#8249;</button>' +
        '<span class="tgc-cal-title">' + MONTHS_RU[view.month] + ' ' + view.year + '</span>' +
        '<button type="button" class="tgc-cal-arrow" data-cal-next>&#8250;</button>' +
      '</div>' +
      '<div class="tgc-cal-grid">' + hdr + cells + '</div>' +
    '</div>';
  }

  function triggerFormHtml() {
    if (!_trForm) {
      return '<button class="tgc-tr-add" type="button" id="tgc-tr-add">+ Добавить триггер</button>';
    }
    var f = _trForm;
    var kindTabs =
      '<div class="tgc-tr-tabs">' +
        '<button type="button" class="tgc-tr-tab' + (f.kind === 'recurring' ? ' active' : '') + '" data-tr-kind="recurring">Регулярный</button>' +
        '<button type="button" class="tgc-tr-tab' + (f.kind === 'once' ? ' active' : '') + '" data-tr-kind="once">Разовый (дата)</button>' +
      '</div>';

    var kindBody;
    if (f.kind === 'recurring') {
      var dows = TG_DOW.map(function (d) {
        var on = f.days_of_week.indexOf(d.v) !== -1;
        return '<button type="button" class="tgc-dow' + (on ? ' active' : '') + '" data-tr-dow="' + d.v + '">' + d.s + '</button>';
      }).join('');
      kindBody =
        '<div class="tgc-tr-row">' +
          '<label class="tgc-tr-sub">Время (МСК)</label>' +
          '<input type="time" class="tgc-input tgc-input--sm" id="tgc-tr-time" value="' + esc(f.time_msk) + '"/>' +
        '</div>' +
        '<div class="tgc-tr-row">' +
          '<label class="tgc-tr-sub">Дни недели <span class="tgc-tr-hint">(пусто = все)</span></label>' +
          '<div class="tgc-dow-row">' + dows + '</div>' +
        '</div>';
    } else {
      kindBody =
        '<div class="tgc-tr-row">' +
          '<label class="tgc-tr-sub">Дата</label>' +
          '<div class="tgc-cal-field">' +
            '<button type="button" class="tgc-cal-input" id="tgc-cal-open">' +
              (f.trigger_date ? fmtDateRu(f.trigger_date) : 'Выбрать дату') +
              '<svg viewBox="0 0 20 20" width="15" height="15" fill="none"><rect x="3" y="4" width="14" height="13" rx="2" stroke="currentColor" stroke-width="1.3"/><path d="M3 8h14M7 3v3M13 3v3" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>' +
            '</button>' +
            (_calOpen ? calendarHtml() : '') +
          '</div>' +
        '</div>' +
        '<div class="tgc-tr-row">' +
          '<label class="tgc-tr-sub">Время (МСК)</label>' +
          '<input type="time" class="tgc-input tgc-input--sm" id="tgc-tr-time" value="' + esc(f.time_msk) + '"/>' +
        '</div>';
    }

    return '<div class="tgc-tr-editor">' +
      '<div class="tgc-tr-editor-title">' + (f.id ? 'Редактирование триггера' : 'Новый триггер') + '</div>' +
      kindTabs +
      '<div class="tgc-tr-row">' +
        '<label class="tgc-tr-sub">Название</label>' +
        '<input type="text" class="tgc-input" id="tgc-tr-title" placeholder="Напр. Утренний дайджест" value="' + esc(f.title) + '"/>' +
      '</div>' +
      kindBody +
      '<div class="tgc-tr-row">' +
        '<label class="tgc-tr-sub">Промпт триггера</label>' +
        '<textarea class="tgc-textarea" id="tgc-tr-prompt" placeholder="Что агент должен сделать при срабатывании...">' + esc(f.prompt) + '</textarea>' +
      '</div>' +
      '<label class="tgc-check tgc-check--sm">' +
        '<input type="checkbox" class="tgc-check-cb" id="tgc-tr-addressed"' + (f.addressed ? ' checked' : '') + '/>' +
        '<span class="tgc-check-box" aria-hidden="true">' + CHECK_SVG + '</span>' +
        '<span class="tgc-check-label">Адресно случайному участнику (иначе — всем)</span>' +
      '</label>' +
      '<div class="tgc-tr-editor-actions">' +
        '<button class="tgc-save tgc-save--sm" type="button" id="tgc-tr-save">' + (f.id ? 'Сохранить' : 'Добавить') + '</button>' +
        '<button class="tgc-cancel tgc-cancel--sm" type="button" id="tgc-tr-cancel">Отмена</button>' +
      '</div>' +
    '</div>';
  }

  function triggersSectionHtml() {
    if (!_editing) {
      return '<div class="tgc-tr-section tgc-tr-section--hint">Сохраните чат, чтобы настраивать индивидуальные триггеры.</div>';
    }
    return '<div class="tgc-tr-section">' +
      '<div class="tgc-tr-section-title">Индивидуальные триггеры</div>' +
      '<div class="tgc-tr-list">' + triggersListHtml() + '</div>' +
      triggerFormHtml() +
    '</div>';
  }

  function ensureModal() {
    if (_modalOverlay && document.body.contains(_modalOverlay)) return _modalOverlay;
    var el = document.getElementById('tgc-modal-overlay');
    if (!el) {
      el = document.createElement('div');
      el.id = 'tgc-modal-overlay';
      el.className = 'tgc-modal-overlay';
      el.setAttribute('role', 'dialog');
      el.setAttribute('aria-modal', 'true');
      el.setAttribute('aria-label', 'Telegram-чат');
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
    _modalOverlay.innerHTML = '';
    _modalOverlay.classList.remove('closing');
    _modalOverlay.classList.add('active');
    render();
    setTimeout(function () {
      var cidInput = _modalOverlay && _modalOverlay.querySelector('#tgc-chatid:not([disabled])');
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

  function renderSidebar() {
    if (!_mount) return;

    var listHtml = _chats.length === 0
      ? '<div class="tgc-empty">Нет встроенных чатов</div>'
      : _chats.map(function (c) {
          var s = c.settings || {};
          var mdl = getActualModels().find(function (m) { return m.id === s.agent_model; });
          var isEditing = _modalOpen && _editing === c.chat_id;
          return '<div class="tgc-row' + (isEditing ? ' tgc-row--active' : '') + '">' +
            '<div class="tgc-row-main">' +
              '<span class="tgc-cid">' + esc(c.chat_id) + '</span>' +
              '<span class="tgc-badge tgc-badge--' + (c.chat_type || 'private') + '">' + (c.chat_type === 'group' ? 'группа' : 'личка') + '</span>' +
              (s.agent_name ? '<span class="tgc-aname">' + esc(s.agent_name) + '</span>' : '') +
              (mdl ? '<span class="tgc-model" style="color:' + mdl.accent + '">' + mdl.svg + ' ' + mdl.label + '</span>' : '') +
            '</div>' +
            '<div class="tgc-row-actions">' +
              '<button class="tgc-btn" data-edit="' + esc(c.chat_id) + '" title="Редактировать"><svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M12 20h9"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z"/></svg></button>' +
              '<button class="tgc-btn tgc-del" data-del="' + esc(c.chat_id) + '" title="Удалить"><svg viewBox="0 0 24 24" width="14" height="14" aria-hidden="true"><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M3 6h18"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M8 6V4a1 1 0 0 1 1-1h6a1 1 0 0 1 1 1v2"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" stroke-linejoin="round" d="M19 6l-1 14a2 2 0 0 1-2 2H8a2 2 0 0 1-2-2L5 6"/><path fill="none" stroke="currentColor" stroke-width="1.9" stroke-linecap="round" d="M10 11v6M14 11v6"/></svg></button>' +
            '</div>' +
          '</div>';
        }).join('');

    _mount.innerHTML =
      '<div class="tgc-wrap">' +
        '<div class="tgc-list">' + listHtml + '</div>' +
        '<button class="tgc-open-modal-btn" id="tgc-open-create" type="button">' +
          '<svg viewBox="0 0 20 20" width="15" height="15" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round"><path d="M10 4v12M4 10h12"/></svg>' +
          '<span>Встроить чат</span>' +
        '</button>' +
      '</div>';

    var createBtn = _mount.querySelector('#tgc-open-create');
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
        if (!confirm('Удалить чат ' + cid + '?')) return;
        try {
          await api('DELETE', '/api/tg/chats/' + encodeURIComponent(cid));
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
    var prevBody = _modalOverlay.querySelector('.tgc-modal-body');
    var savedScrollTop = prevBody ? prevBody.scrollTop : 0;
    var prevTrList = _modalOverlay.querySelector('.tgc-tr-list');
    var savedTrListScroll = prevTrList ? prevTrList.scrollTop : 0;
    var openDdIds = Array.prototype.map.call(
      _modalOverlay.querySelectorAll('.tgc-dd.tgc-dd--open'),
      function (el) { return el.id; }
    ).filter(Boolean);

    var modelContent = getActualModels().map(function (m) {
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

    var tgIconSvg = '<svg class="tgc-modal-icon" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.7" stroke-linejoin="round"><path d="M21 4L3 11l6.5 2.5L18 7l-6 8.5v4.5l3.2-3.2L19 19l2-15z"/></svg>';

    _modalOverlay.innerHTML =
      '<div class="tgc-modal" id="tgc-modal">' +
        '<div class="tgc-modal-head">' +
          tgIconSvg +
          '<div class="tgc-modal-title">' + (_editing ? 'Редактирование чата' : 'Встроить новый чат') + '</div>' +
          '<button class="tgc-modal-close" id="tgc-modal-close" type="button" aria-label="Закрыть">✕</button>' +
        '</div>' +
        '<div class="tgc-modal-body">' +
          '<div class="tgc-form">' +
            '<div class="tgc-field">' +
              '<span class="tgc-field-label">Chat ID</span>' +
              '<input class="tgc-input" id="tgc-chatid" type="text" placeholder="-1004313379449 или 123456789" value="' + esc(_form.chat_id) + '"' + (_editing ? ' disabled' : '') + '/>' +
              '<span class="tgc-field-hint">Минус \u2192 группа, без минуса \u2192 личка</span>' +
            '</div>' +
            '<div class="tgc-field">' +
              '<span class="tgc-field-label">Имя агента</span>' +
              '<input class="tgc-input" id="tgc-agentname" type="text" placeholder="Напр. AuGent (пусто \u2192 дефолт)" value="' + esc(_form.agent_name || '') + '"/>' +
            '</div>' +
            ddToggle('Модель', modelDisplay(), modelContent, 'tgc-dd-model') +
            ddToggle('Инструменты', toolsDisplay(), toolsContent, 'tgc-dd-tools') +
            ddToggle('Триггер', triggerDisplay(), triggerContent, 'tgc-dd-trigger') +
            '<div class="tgc-field">' +
              '<span class="tgc-field-label">Глубина контекста</span>' +
              '<input class="tgc-input tgc-input--sm" id="tgc-ctxn" type="number" min="1" max="20" value="' + esc(_form.orchestrator_context_n) + '"/>' +
            '</div>' +
            '<label class="tgc-check">' +
              '<input type="checkbox" class="tgc-check-cb" id="tgc-proactive"' + (_form.proactive_enabled ? ' checked' : '') + '/>' +
              '<span class="tgc-check-box" aria-hidden="true">' + CHECK_SVG + '</span>' +
              '<span class="tgc-check-label">Проактивные триггеры</span>' +
            '</label>' +
            '<div class="tgc-field">' +
              '<span class="tgc-field-label">Базовый промпт</span>' +
              '<textarea class="tgc-textarea" id="tgc-prompt" placeholder="Системный промпт для агента в этом чате...">' + esc(_form.system_prompt) + '</textarea>' +
            '</div>' +
            triggersSectionHtml() +
            '<div class="tgc-actions">' +
              '<button class="tgc-save" id="tgc-save" type="button">' + (_editing ? 'Сохранить' : 'Встроить чат') + '</button>' +
              '<button class="tgc-cancel" id="tgc-cancel" type="button">Отмена</button>' +
            '</div>' +
            '<div class="tgc-msg" id="tgc-msg"></div>' +
          '</div>' +
        '</div>' +
      '</div>';

    bindModalEvents();
  }

  function render() {
    renderSidebar();
    if (_modalOpen) renderModal();
  }

  function bindModalEvents() {
    if (!_modalOverlay) return;

    openDdIds.forEach(function (id) {
      var dd = _modalOverlay.querySelector('#' + id);
      if (dd) dd.classList.add('tgc-dd--open');
    });
    var newBody = _modalOverlay.querySelector('.tgc-modal-body');
    if (newBody && savedScrollTop > 0) newBody.scrollTop = savedScrollTop;
    var newTrList = _modalOverlay.querySelector('.tgc-tr-list');
    if (newTrList && savedTrListScroll > 0) newTrList.scrollTop = savedTrListScroll;

    var closeBtn = _modalOverlay.querySelector('#tgc-modal-close');
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
        var dd = e.currentTarget.closest('.tgc-dd');
        if (dd) dd.classList.remove('tgc-dd--open');
        _form.agent_model = e.currentTarget.dataset.model;
        renderModal();
      });
    });

    _modalOverlay.querySelectorAll('[data-tool]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var id = e.currentTarget.dataset.tool;
        var i = _form.selected_tools.indexOf(id);
        if (i >= 0) _form.selected_tools.splice(i, 1); else _form.selected_tools.push(id);
        renderModal();
      });
    });

    _modalOverlay.querySelectorAll('[data-trigger]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var dd = e.currentTarget.closest('.tgc-dd');
        if (dd) dd.classList.remove('tgc-dd--open');
        _form.trigger_mode = e.currentTarget.dataset.trigger;
        renderModal();
      });
    });

    var cidEl = _modalOverlay.querySelector('#tgc-chatid');
    if (cidEl) cidEl.addEventListener('input', function (e) { _form.chat_id = e.target.value.trim(); });
    var ctxEl = _modalOverlay.querySelector('#tgc-ctxn');
    if (ctxEl) ctxEl.addEventListener('input', function (e) { _form.orchestrator_context_n = parseInt(e.target.value, 10) || 1; });
    var proEl = _modalOverlay.querySelector('#tgc-proactive');
    if (proEl) proEl.addEventListener('change', function (e) { _form.proactive_enabled = e.target.checked; });
    var prEl = _modalOverlay.querySelector('#tgc-prompt');
    if (prEl) prEl.addEventListener('input', function (e) { _form.system_prompt = e.target.value; });
    var anEl = _modalOverlay.querySelector('#tgc-agentname');
    if (anEl) anEl.addEventListener('input', function (e) { _form.agent_name = e.target.value; });

    var saveBtn = _modalOverlay.querySelector('#tgc-save');
    if (saveBtn) saveBtn.addEventListener('click', onSave);
    var cancelBtn = _modalOverlay.querySelector('#tgc-cancel');
    if (cancelBtn) cancelBtn.addEventListener('click', closeModal);

    bindTriggerEvents();
  }

  function bindTriggerEvents() {
    if (!_modalOverlay) return;

    var addBtn = _modalOverlay.querySelector('#tgc-tr-add');
    if (addBtn) addBtn.addEventListener('click', function () {
      _trForm = trDefaults(); _calOpen = false; _calView = null; renderModal();
    });

    _modalOverlay.querySelectorAll('[data-tr-edit]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var id = parseInt(e.currentTarget.dataset.trEdit, 10);
        var t = _triggers.find(function (x) { return x.id === id; });
        if (!t) return;
        _trForm = {
          id: t.id, kind: t.kind || 'recurring', title: t.title || '',
          time_msk: t.time_msk || '09:00',
          days_of_week: (t.days_of_week || []).slice(),
          trigger_date: t.trigger_date || '',
          prompt: t.prompt || '', addressed: !!t.addressed,
          is_active: t.is_active != null ? t.is_active : true
        };
        _calOpen = false; _calView = null; renderModal();
      });
    });

    _modalOverlay.querySelectorAll('[data-tr-del]').forEach(function (b) {
      b.addEventListener('click', async function (e) {
        var id = e.currentTarget.dataset.trDel;
        if (!confirm('Удалить триггер?')) return;
        try {
          await api('DELETE', '/api/tg/triggers/' + encodeURIComponent(_editing) + '/' + encodeURIComponent(id));
          await loadTriggers(_editing);
        } catch (err) { showMsg('Ошибка: ' + err.message, true); }
      });
    });

    if (!_trForm) return;

    _modalOverlay.querySelectorAll('[data-tr-kind]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        _trForm.kind = e.currentTarget.dataset.trKind;
        _calOpen = false; renderModal();
      });
    });

    var tEl = _modalOverlay.querySelector('#tgc-tr-title');
    if (tEl) tEl.addEventListener('input', function (e) { _trForm.title = e.target.value; });
    var tmEl = _modalOverlay.querySelector('#tgc-tr-time');
    if (tmEl) tmEl.addEventListener('input', function (e) { _trForm.time_msk = e.target.value; });
    var pEl = _modalOverlay.querySelector('#tgc-tr-prompt');
    if (pEl) pEl.addEventListener('input', function (e) { _trForm.prompt = e.target.value; });
    var adEl = _modalOverlay.querySelector('#tgc-tr-addressed');
    if (adEl) adEl.addEventListener('change', function (e) { _trForm.addressed = e.target.checked; });

    _modalOverlay.querySelectorAll('[data-tr-dow]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        var v = parseInt(e.currentTarget.dataset.trDow, 10);
        var i = _trForm.days_of_week.indexOf(v);
        if (i === -1) _trForm.days_of_week.push(v); else _trForm.days_of_week.splice(i, 1);
        _trForm.days_of_week.sort(function (a, b) { return a - b; });
        renderModal();
      });
    });

    var calOpenBtn = _modalOverlay.querySelector('#tgc-cal-open');
    if (calOpenBtn) calOpenBtn.addEventListener('click', function () { _calOpen = !_calOpen; renderModal(); });
    var prevBtn = _modalOverlay.querySelector('[data-cal-prev]');
    if (prevBtn) prevBtn.addEventListener('click', function () {
      _calView.month--; if (_calView.month < 0) { _calView.month = 11; _calView.year--; } renderModal();
    });
    var nextBtn = _modalOverlay.querySelector('[data-cal-next]');
    if (nextBtn) nextBtn.addEventListener('click', function () {
      _calView.month++; if (_calView.month > 11) { _calView.month = 0; _calView.year++; } renderModal();
    });
    _modalOverlay.querySelectorAll('[data-cal-day]').forEach(function (b) {
      b.addEventListener('click', function (e) {
        _trForm.trigger_date = e.currentTarget.dataset.calDay;
        _calOpen = false; renderModal();
      });
    });

    var trSave = _modalOverlay.querySelector('#tgc-tr-save');
    if (trSave) trSave.addEventListener('click', onSaveTrigger);
    var trCancel = _modalOverlay.querySelector('#tgc-tr-cancel');
    if (trCancel) trCancel.addEventListener('click', function () { _trForm = null; _calOpen = false; renderModal(); });
  }

  async function onSaveTrigger() {
    var f = _trForm;
    if (f.kind === 'once' && !f.trigger_date) { showMsg('Укажите дату', true); return; }
    if (!f.time_msk) { showMsg('Укажите время', true); return; }
    var payload = {
      kind: f.kind,
      title: f.title,
      time_msk: f.time_msk,
      days_of_week: f.kind === 'recurring' ? f.days_of_week : [],
      trigger_date: f.kind === 'once' ? f.trigger_date : null,
      prompt: f.prompt,
      addressed: f.addressed,
      is_active: f.is_active
    };
    try {
      if (f.id) {
        await api('PATCH', '/api/tg/triggers/' + encodeURIComponent(_editing) + '/' + encodeURIComponent(f.id), payload);
      } else {
        await api('POST', '/api/tg/triggers/' + encodeURIComponent(_editing), payload);
      }
      _trForm = null; _calOpen = false;
      await loadTriggers(_editing);
      showMsg('Триггер сохранён', false);
    } catch (err) {
      showMsg('Ошибка: ' + err.message, true);
    }
  }

  function showMsg(text, isErr) {
    var el = _modalOverlay && _modalOverlay.querySelector('#tgc-msg');
    if (!el) return;
    el.textContent = text;
    el.className = 'tgc-msg ' + (isErr ? 'tgc-msg--err' : 'tgc-msg--ok');
    if (!isErr) setTimeout(function () { if (el) el.textContent = ''; }, 2500);
  }

  async function onSave() {
    var payload = {
      system_prompt: _form.system_prompt,
      agent_name: _form.agent_name,
      agent_model: _form.agent_model,
      selected_tools: _form.selected_tools,
      trigger_mode: _form.trigger_mode,
      orchestrator_context_n: _form.orchestrator_context_n,
      proactive_enabled: _form.proactive_enabled
    };
    try {
      if (_editing) {
        await api('POST', '/api/tg/settings/' + encodeURIComponent(_editing), payload);
        await loadChats();
        renderSidebar();
        showMsg('Настройки сохранены');
        setTimeout(function () { closeModal(); }, 450);
      } else {
        var cid = (_form.chat_id || '').trim();
        if (!cid) { showMsg('Укажи Chat ID', true); return; }
        await api('POST', '/api/tg/chats', Object.assign({ chat_id: cid }, payload));
        await loadChats();
        renderSidebar();
        showMsg('Чат встроен');
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
