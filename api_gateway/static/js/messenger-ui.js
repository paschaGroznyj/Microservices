// messenger-ui.js — общее для tgChats.js и maxChats.js:
//  • раздел «Инструменты» (выпадающий список, категории и иконки из tools-panel / tp-section);
//  • лимиты на встраивание чатов по уровню (GET /api/messengers/limits, проверяет и сервер).
(function () {
  'use strict';

  // Запасной список, если tools-panel.js не успел загрузиться (те же id/подписи).
  var FALLBACK = [
    { id: 'internet', label: 'Интернет', description: 'Поиск в сети и парсинг страниц', adminOnly: false },
    { id: 'content', label: 'Контент и файлы', description: 'Генерация PPTX, DOCX, Excel, PNG, расшифровка аудио и видео', adminOnly: false },
    { id: 'sessions', label: 'Бортовой журнал агента', description: 'Бортовой журнал агента: долгосрочная память и точки сессий', adminOnly: false }
  ];
  var CHECK = '<svg viewBox="0 0 12 12" width="11" height="11" fill="none"><path d="M2.5 6.2l2.3 2.3 4.7-5" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  var DOT = '<svg viewBox="0 0 24 24" width="16" height="16" fill="none"><circle cx="12" cy="12" r="9" stroke="currentColor" stroke-width="2"/></svg>';
  var TYPE_RU = { private: 'личный', group: 'группа' };

  var _p = null, _info = null;

  function load(force) {
    if (!_p || force) {
      _p = fetch('/api/messengers/limits', { credentials: 'include' })
        .then(function (r) { return r.ok ? r.json() : null; })
        .catch(function () { return null; })
        .then(function (d) { _info = d; return d; });
    }
    return _p;
  }

  function isAdmin() { return !!(_info && _info.is_admin); }
  function esc(s) {
    return String(s == null ? '' : s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;').replace(/"/g, '&quot;');
  }

  // ── Инструменты ──────────────────────────────────────────────────────────
  function categories() {
    var all = (window.__TP_CATEGORIES && window.__TP_CATEGORIES.length) ? window.__TP_CATEGORIES : FALLBACK;
    return all.filter(function (c) { return !c.adminOnly || isAdmin(); });
  }
  function icon(id) { return (window.__TP_ICONS && window.__TP_ICONS[id]) || DOT; }
  function picked(sel) {
    return categories().filter(function (c) { return (sel || []).indexOf(c.id) >= 0; });
  }

  // Значение в свёрнутом поле: «Интернет, Контент и файлы» + «2 из 3».
  function toolsValueHtml(sel) {
    var cats = categories(), on = picked(sel);
    var names = on.length ? on.map(function (c) { return esc(c.label); }).join(', ') : 'Не выбраны';
    return '<span class="tgc-tv' + (on.length ? '' : ' tgc-tv--none') + '">' + names + '</span>' +
      '<span class="tgc-tn">' + on.length + ' из ' + cats.length + '</span>';
  }

  // Содержимое списка: иконка, название, описание, чекбокс; внизу «Выбрать всё».
  function toolsContentHtml(sel) {
    var cats = categories(), on = picked(sel);
    var rows = cats.map(function (c) {
      var isOn = (sel || []).indexOf(c.id) >= 0;
      return '<button class="tgc-topt tgc-topt--d' + (isOn ? ' tgc-topt--on' : '') + (c.adminOnly ? ' tgc-topt--admin' : '') +
        '" data-tool="' + esc(c.id) + '" type="button" role="menuitemcheckbox" aria-checked="' + isOn + '">' +
        '<span class="tgc-topt-ic">' + icon(c.id) + '</span>' +
        '<span class="tgc-topt-tx"><b>' + esc(c.label) + (c.adminOnly ? '<em>админ</em>' : '') + '</b>' +
        '<small>' + esc(c.description || '') + '</small></span>' +
        '<span class="tgc-topt-ck" aria-hidden="true">' + CHECK + '</span>' +
      '</button>';
    }).join('');
    return rows + '<div class="tgc-topt-ft"><button type="button" data-tools-all>' +
      (on.length === cats.length ? 'Снять всё' : 'Выбрать всё') + '</button></div>';
  }

  // Новый массив selected_tools после «Выбрать всё / Снять всё».
  // Скрытые от пользователя категории (adminOnly) не трогаем.
  function toggleAll(sel) {
    var cats = categories(), ids = cats.map(function (c) { return c.id; });
    var rest = (sel || []).filter(function (id) { return ids.indexOf(id) < 0; });
    return picked(sel).length === cats.length ? rest : rest.concat(ids);
  }

  // ── Лимиты ───────────────────────────────────────────────────────────────
  function chatType(c) {
    if (c && c.chat_type) return c.chat_type;
    var n = parseInt(String((c && c.chat_id) || c || '').trim(), 10);
    return isNaN(n) || n < 0 ? 'group' : 'private';
  }
  function limits() { return (_info && !_info.is_admin && _info.limits) || null; }

  function usage(chats) {
    var u = { private: 0, group: 0 };
    (chats || []).forEach(function (c) { var t = chatType(c); if (t in u) u[t]++; });
    return u;
  }

  // chat_id → текст подсказки для чатов сверх лимита (лишними считаем самые новые).
  function overLimit(chats) {
    var out = {}, lim = limits();
    if (!lim) return out;
    var byType = { private: [], group: [] };
    (chats || []).forEach(function (c) { var t = chatType(c); if (byType[t]) byType[t].push(c); });
    Object.keys(byType).forEach(function (t) {
      var arr = byType[t].slice().sort(function (a, b) {
        return String(a.created_at || '').localeCompare(String(b.created_at || ''));
      });
      arr.slice(lim[t]).forEach(function (c) {
        out[c.chat_id] = 'Сверх лимита уровня L' + _info.level + ' (' + TYPE_RU[t] + ': ' + lim[t] +
          '). Чат работает, но новый такого типа не добавить, пока их больше лимита.';
      });
    });
    return out;
  }

  function warnHtml(text) {
    return '<span class="tgc-warn" title="' + esc(text) + '" aria-label="' + esc(text) + '">!</span>';
  }

  // Строка над кнопкой «Встроить»: «Уровень L1 · личные 1/1 · группы 0/1».
  function usageHtml(chats) {
    var lim = limits();
    if (!lim) return '';
    var u = usage(chats);
    function part(t, name) {
      var cls = u[t] > lim[t] ? 'tgc-lim-over' : (u[t] === lim[t] ? 'tgc-lim-full' : '');
      return '<span class="' + cls + '">' + name + ' ' + u[t] + '/' + lim[t] + '</span>';
    }
    return '<div class="tgc-lim">Уровень L' + _info.level + ' · ' + part('private', 'личные') + ' · ' + part('group', 'группы') + '</div>';
  }

  // null — можно добавить; иначе текст ошибки (финальную проверку делает сервер).
  function canAdd(chats, chatId) {
    var lim = limits();
    if (!lim) return null;
    var cid = String(chatId || '').trim();
    if ((chats || []).some(function (c) { return String(c.chat_id) === cid; })) return null;
    var t = chatType(cid);
    if (usage(chats)[t] < lim[t]) return null;
    return 'Лимит уровня L' + _info.level + ': ' + (t === 'group' ? 'групп' : 'личных чатов') + ' — ' + lim[t] +
      '. Удалите один из чатов или повысьте уровень.';
  }


  // ── Общий вид списка и окна настроек (TG и MAX одинаково) ───────────────
  var IC = {
    group: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="9" cy="8" r="3.2"/><path d="M3.5 19c.6-3 2.8-4.6 5.5-4.6S13.9 16 14.5 19"/><circle cx="17" cy="9" r="2.4"/><path d="M15.8 14.6c2.3.2 4 1.7 4.6 4.4"/></svg>',
    user: '<svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2" stroke-linecap="round"><circle cx="12" cy="8" r="3.6"/><path d="M5 20c.8-3.6 3.6-5.6 7-5.6s6.2 2 7 5.6"/></svg>',
    more: '<svg viewBox="0 0 24 24" width="16" height="16" fill="currentColor"><circle cx="5" cy="12" r="1.7"/><circle cx="12" cy="12" r="1.7"/><circle cx="19" cy="12" r="1.7"/></svg>',
    close: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none"><path d="M4 4l8 8M12 4l-8 8" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>',
    back: '<svg viewBox="0 0 16 16" width="16" height="16" fill="none"><path d="M10 3.5 5.5 8l4.5 4.5" stroke="currentColor" stroke-width="1.7" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    copy: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="5.5" y="5.5" width="8" height="8" rx="1.8"/><path d="M10.5 3.2A1.7 1.7 0 0 0 9 2.5H4.2A1.7 1.7 0 0 0 2.5 4.2V9a1.7 1.7 0 0 0 .7 1.4"/></svg>',
    ok: '<svg viewBox="0 0 16 16" fill="none"><path d="M3.5 8.4 6.5 11.3 12.5 4.8" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    chev: '<svg viewBox="0 0 16 16" fill="none"><path d="m6 3.5 4.5 4.5L6 12.5" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>',
    cal: '<svg viewBox="0 0 16 16" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="2.5" y="3.5" width="11" height="10" rx="2"/><path d="M2.5 6.5h11M5.5 2v3M10.5 2v3" stroke-linecap="round"/></svg>'
  };
  var TRIG = [
    ['mention', 'По упоминанию', 'Агент отвечает, когда его отметили через @'],
    ['orchestrator', 'Оркестратор', 'Агент сам решает, когда вступить в разговор']
  ];
  function typeRu(t) { return t === 'group' ? 'группа' : 'личка'; }
  // Инициалы и тон — как у .chat-av в сайдбаре (app.js initialOf / toneOf).
  function initialOf(t) {
    var w = String(t || '').match(/[\p{L}\p{N}]+/gu) || [];
    return w.slice(0, 2).map(function (x) { return Array.from(x)[0]; }).join('').toUpperCase();
  }
  function toneOf(id) { var h = 0; for (var ch of String(id)) h = (h * 31 + ch.codePointAt(0)) >>> 0; return h % 8; }
  function avHtml(cid, type, name, cls) {
    var ini = initialOf(name);
    return '<span class="chat-av tgx-av' + (cls ? ' ' + cls : '') + '" data-tone="' + toneOf(cid) + '" aria-hidden="true">' +
      (ini ? esc(ini) : (type === 'group' ? IC.group : IC.user)) + '</span>';
  }
  function chatName(c) { var s = (c && c.settings) || {}; return s.agent_name || ''; }
  function mdlSvg(m) { return String(m.svg || '').replace(/\swidth="\d+"\s+height="\d+"/, ''); }
  function isSheet() { return window.innerWidth <= 600; }
  function cnt(t) { var n = (t || '').length; return n >= 1000 ? (n / 1000).toFixed(1).replace('.', ',') + 'K зн.' : n + ' зн.'; }

  // Список: вкладки «Все · Группы · Лички» + строки (аватар, имя, ID · тип, модель, ⋯).
  function listHtml(chats, o) {
    o = o || {};
    chats = chats || [];
    if (!chats.length) return '<div class="tgc-empty">Нет встроенных чатов</div>';
    var tab = o.tab || 'all', over = overLimit(chats), n = { all: chats.length, group: 0, private: 0 };
    chats.forEach(function (c) { n[chatType(c)]++; });
    var tabs = '<div class="tgx-tabs" role="tablist">' + [['all', 'Все'], ['group', 'Группы'], ['private', 'Лички']].map(function (t) {
      var on = tab === t[0];
      return '<button type="button" role="tab" class="tgx-tab' + (on ? ' is-on' : '') + '" data-tab="' + t[0] + '" aria-selected="' + on + '">' + t[1] + '<b>' + n[t[0]] + '</b></button>';
    }).join('') + '</div>';
    var rows = chats.filter(function (c) { return tab === 'all' || chatType(c) === tab; }).map(function (c) {
      var s = c.settings || {}, t = chatType(c), w = over[c.chat_id], nm = chatName(c);
      var m = (o.models || []).find(function (x) { return x.id === s.agent_model; });
      return '<div class="tgx-row' + (o.active === c.chat_id ? ' is-on' : '') + '" role="button" tabindex="0" data-open="' + esc(c.chat_id) + '">' +
        '<span class="tgx-avw">' + avHtml(c.chat_id, t, nm) + (w ? '<i class="tgx-avw-w" title="' + esc(w) + '">!</i>' : '') + '</span>' +
        '<span class="tgx-tx"><span class="tgx-nm' + (nm ? '' : ' is-empty') + '">' + (nm ? esc(nm) : 'Без имени') + '</span>' +
        '<span class="tgx-l2"><span class="tgx-id">' + esc(c.chat_id) + '</span><span class="tgx-ty">· ' + typeRu(t) + '</span></span></span>' +
        (m ? '<span class="tgx-mdl" style="--m:' + m.accent + '">' + mdlSvg(m) + esc(m.label) + '</span>' : '') +
        '<button type="button" class="tgx-more" data-more="' + esc(c.chat_id) + '" title="Действия" aria-label="Действия">' + IC.more + '</button></div>';
    }).join('');
    return tabs + '<div class="tgx-rows">' + (rows || '<div class="tgc-empty">Нет чатов этого типа</div>') + '</div>';
  }

  // Предупреждение и лимиты — справа в section-label раздела.
  function sectionMetaHtml(chats) {
    var lim = limits();
    if (!lim) return '';
    var u = usage(chats), nOver = Object.keys(overLimit(chats)).length, lv = _info.level;
    function part(t, name) {
      var cls = u[t] > lim[t] ? 'is-over' : (u[t] === lim[t] ? 'is-full' : '');
      return '<span class="' + cls + '">' + name + ' ' + u[t] + '/' + lim[t] + (u[t] > lim[t] ? '<i class="tgx-w" aria-hidden="true">!</i>' : '') + '</span>';
    }
    var tip = nOver
      ? 'Сверх лимита уровня L' + lv + ': ' + nOver + '. Эти чаты работают, но новый чат такого типа не добавить, пока их больше лимита.'
      : 'Лимит уровня L' + lv + ': личных чатов ' + lim.private + ', групп ' + lim.group + '.';
    return '<span class="tgx-slm' + (nOver ? ' is-warn' : '') + '" title="' + esc(tip) + '">' +
      '<span class="tgx-lv">L' + lv + '</span>' + part('private', 'личные') + '<span class="tgx-dot">·</span>' + part('group', 'группы') + '</span>';
  }
  function renderSectionMeta(el, chats) {
    if (typeof el === 'string') el = document.getElementById(el);
    if (!el) return;
    var meta = el.querySelector('.tgx-slm-wrap');
    if (!meta) { meta = document.createElement('span'); meta.className = 'tgx-slm-wrap'; el.appendChild(meta); el.classList.add('tgx-sl'); }
    meta.innerHTML = sectionMetaHtml(chats);
  }

  // Контекстное меню строки (как в панели вложений).
  function closeMenu() { var m = document.getElementById('tgx-menu'); if (m) m.remove(); }
  function menu(x, y, items) {
    closeMenu();
    var m = document.createElement('div');
    m.className = 'att-menu tgx-menu'; m.id = 'tgx-menu'; m.setAttribute('role', 'menu');
    m.innerHTML = items.map(function (it, i) {
      return (it.danger ? '<hr class="tgx-menu-sep"/>' : '') + '<button type="button" role="menuitem" data-i="' + i + '"' + (it.danger ? ' class="tgx-danger"' : '') + '>' + esc(it.t) + '</button>';
    }).join('');
    document.body.appendChild(m);
    var r = m.getBoundingClientRect();
    m.style.left = Math.max(8, Math.min(x, window.innerWidth - r.width - 8)) + 'px';
    m.style.top = Math.max(8, Math.min(y, window.innerHeight - r.height - 8)) + 'px';
    m.addEventListener('click', function (e) {
      var b = e.target.closest('[data-i]'); if (!b) return;
      var f = items[+b.dataset.i].f; closeMenu(); if (f) f();
    });
    var first = m.querySelector('button'); if (first) first.focus({ preventScroll: true });
  }
  document.addEventListener('mousedown', function (e) { if (!e.target.closest('#tgx-menu,[data-more],[data-tr-more]')) closeMenu(); }, true);
  document.addEventListener('keydown', function (e) {
    if (e.key === 'Escape' && document.getElementById('tgx-menu')) { e.stopPropagation(); closeMenu(); }
  }, true);
  window.addEventListener('resize', closeMenu);

  function copy(text) {
    try { if (navigator.clipboard && window.isSecureContext) return navigator.clipboard.writeText(text); } catch (_) {}
    var ta = document.createElement('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
    document.body.appendChild(ta); ta.select();
    try { document.execCommand('copy'); } catch (_) {}
    ta.remove();
  }

  // h: { tab(t), open(cid), del(cid) } — делегирование, вешается на mount один раз.
  function bindList(root, h) {
    if (!root || root.dataset.tgxBound) return;
    root.dataset.tgxBound = '1';
    function rowMenu(cid, x, y) {
      menu(x, y, [
        { t: 'Редактировать', f: function () { h.open(cid); } },
        { t: 'Копировать ID', f: function () { copy(cid); } },
        { t: 'Удалить', danger: true, f: function () { h.del(cid); } }
      ]);
    }
    root.addEventListener('click', function (e) {
      var t;
      if ((t = e.target.closest('[data-tab]'))) { h.tab(t.dataset.tab); return; }
      if ((t = e.target.closest('[data-more]'))) {
        e.stopPropagation();
        var r = t.getBoundingClientRect();
        rowMenu(t.dataset.more, r.right - 200, r.bottom + 4);
        return;
      }
      if ((t = e.target.closest('[data-open]'))) h.open(t.dataset.open);
    });
    root.addEventListener('contextmenu', function (e) {
      var t = e.target.closest('[data-open]'); if (!t) return;
      e.preventDefault(); rowMenu(t.dataset.open, e.clientX, e.clientY);
    });
    root.addEventListener('keydown', function (e) {
      var t = e.target.closest && e.target.closest('[data-open]');
      if (t && e.target === t && (e.key === 'Enter' || e.key === ' ')) { e.preventDefault(); h.open(t.dataset.open); }
    });
  }

  // Окно настроек: модалка на ПК, шторка на мобиле. Поля сохраняют прежние id/data-*.
  // o: { modalId, editing, form, models, toolsContent, schedule, trCount, sub, isSheet }
  function modalHtml(o) {
    var f = o.form, ed = o.editing, t = chatType(ed || f.chat_id);
    var head = '<div class="tgx-hd"><div class="tgx-ttl">' + (ed ? 'Настройки чата' : 'Встроить чат') + '</div>' +
      '<button type="button" class="tgx-x" id="tgc-modal-close" aria-label="Закрыть">' + IC.close + '</button></div>';
    var who = ed
      ? '<div class="tgx-who">' + avHtml(ed, t, f.agent_name, 'tgx-av--lg') +
          '<div class="tgx-who-tx"><div class="tgx-who-nm" data-who-nm>' + (f.agent_name ? esc(f.agent_name) : '<span class="is-empty">Без имени</span>') + '</div>' +
          '<div class="tgx-who-l2"><button type="button" class="tgx-idc" data-copy="' + esc(ed) + '" title="Скопировать ID"><span>' + esc(ed) + '</span>' + IC.copy + '</button>' +
          '<span class="tgx-ty">' + typeRu(t) + '</span></div></div></div>'
      : '<label class="tgx-fl"><span class="tgx-lb">Chat ID <i data-cid-type>' + (f.chat_id ? typeRu(t) : '') + '</i></span>' +
          '<input class="tgc-input" id="tgc-chatid" type="text" inputmode="numeric" autocomplete="off" placeholder="-1004313379449 или 123456789" value="' + esc(f.chat_id) + '"/>' +
          '<span class="tgx-hint">С минусом — группа, без минуса — личный чат</span></label>';
    var seg = (o.models || []).map(function (m) {
      return '<button type="button" class="tgx-sb' + (m.id === f.agent_model ? ' is-on' : '') + '" data-model="' + esc(m.id) + '" style="--m:' + m.accent + '" role="radio" aria-checked="' + (m.id === f.agent_model) + '">' + mdlSvg(m) + '<span>' + esc(m.label) + '</span></button>';
    }).join('');
    var tr = TRIG.find(function (x) { return x[0] === f.trigger_mode; }) || TRIG[0];
    var trSeg = TRIG.map(function (x) {
      return '<button type="button" class="tgx-sb tgx-sb--acc' + (x[0] === f.trigger_mode ? ' is-on' : '') + '" data-trigger="' + x[0] + '" role="radio" aria-checked="' + (x[0] === f.trigger_mode) + '"><span>' + x[1] + '</span></button>';
    }).join('');
    var trN = o.trCount || 0;
    var schedRow = ed
      ? '<button type="button" class="tgx-ln tgx-ln--btn" data-sub><div class="tgx-ln-tx"><span>' + IC.cal + 'Расписание</span><span class="tgx-hint">Индивидуальные триггеры этого чата</span></div>' +
          '<span class="tgx-ln-r">' + (trN ? trN + ' ' + plural(trN, 'триггер', 'триггера', 'триггеров') : 'нет') + IC.chev + '</span></button>'
      : '<div class="tgx-ln is-off"><div class="tgx-ln-tx"><span>' + IC.cal + 'Расписание</span><span class="tgx-hint">Доступно после сохранения чата</span></div></div>';
    var body =
      who +
      (ed ? '<label class="tgx-fl"><span class="tgx-lb">Имя агента</span>' : '<label class="tgx-fl"><span class="tgx-lb">Имя агента</span>') +
        '<input class="tgc-input" id="tgc-agentname" type="text" placeholder="AuGent по умолчанию" value="' + esc(f.agent_name || '') + '"/></label>' +
      '<div class="tgx-fl"><span class="tgx-lb">Модель</span><div class="tgx-seg" role="radiogroup" aria-label="Модель">' + seg + '</div></div>' +
      '<div class="tgx-fl"><span class="tgx-lb">Инструменты</span>' +
        '<div class="tgc-dd tgx-dd" id="tgc-dd-tools"><button class="tgc-dd-toggle tgx-ddt" type="button" aria-haspopup="menu">' +
          '<span class="tgc-dd-value">' + toolsValueHtml(f.selected_tools) + '</span><span class="tgx-ddt-ch">' + IC.chev + '</span></button>' +
          '<div class="tgc-dd-pop" role="menu">' + o.toolsContent + '</div></div></div>' +
      '<div class="tgx-fl"><span class="tgx-lb">Когда отвечает</span><div class="tgx-seg" role="radiogroup" aria-label="Когда отвечает">' + trSeg + '</div><span class="tgx-hint">' + tr[2] + '</span></div>' +
      '<div class="tgx-grp">' +
        '<div class="tgx-ln"><div class="tgx-ln-tx"><span>Глубина контекста</span><span class="tgx-hint">Сколько последних сообщений видит агент</span></div>' +
          '<div class="tgx-step"><button type="button" data-step="-1" aria-label="Меньше">−</button><b data-ctx>' + esc(f.orchestrator_context_n) + '</b><button type="button" data-step="1" aria-label="Больше">+</button></div></div>' +
        '<label class="tgx-ln"><div class="tgx-ln-tx"><span>Проактивные триггеры</span><span class="tgx-hint">Агент пишет в чат сам, без упоминания</span></div>' +
          '<input type="checkbox" class="tgx-sw" id="tgc-proactive"' + (f.proactive_enabled ? ' checked' : '') + '/></label>' +
        schedRow +
      '</div>' +
      '<label class="tgx-fl"><span class="tgx-lb">Базовый промпт <i data-cnt>' + cnt(f.system_prompt) + '</i></span>' +
        '<textarea class="tgc-textarea tgx-ta" id="tgc-prompt" rows="3" placeholder="Системный промпт для агента в этом чате…">' + esc(f.system_prompt) + '</textarea></label>';
    var foot = '<div class="tgx-ft">' + (ed ? '<button type="button" class="tgx-del" data-del>Удалить</button>' : '') + '<span class="tgx-sp"></span>' +
      '<button type="button" class="tgx-btn" id="tgc-cancel">Отмена</button>' +
      '<button type="button" class="tgx-btn tgx-btn--pri" id="tgc-save">' + (ed ? 'Сохранить' : 'Встроить') + '</button></div>';
    var sub = ed
      ? '<div class="tgx-sub' + (o.sub ? ' is-in' : '') + '" aria-hidden="' + !o.sub + '">' + (o.isSheet ? '<div class="tgx-grab"></div>' : '') +
          '<div class="tgx-hd"><button type="button" class="tgx-x" data-subx aria-label="Назад">' + IC.back + '</button><div class="tgx-ttl">Расписание</div></div>' +
          '<div class="tgx-bd tgx-bd--sub">' + (o.schedule || '') + '</div>' +
          '<div class="tgx-ft"><span class="tgx-hint">Триггеры сохраняются сразу</span><span class="tgx-sp"></span><button type="button" class="tgx-btn tgx-btn--pri" data-subx>Готово</button></div></div>'
      : '';
    return '<div class="tgx-box' + (o.isSheet ? ' tgx-box--sheet' : '') + '" id="' + o.modalId + '">' + (o.isSheet ? '<div class="tgx-grab"></div>' : '') +
      head + '<div class="tgx-bd">' + body + '</div><div class="tgc-msg" id="tgc-msg"></div>' + foot + sub + '</div>';
  }
  function plural(n, a, b, c) { var m = n % 10, h = n % 100; return m === 1 && h !== 11 ? a : (m >= 2 && m <= 4 && (h < 12 || h > 14) ? b : c); }

  function fitPrompt(ov) {
    var t = ov && ov.querySelector('.tgx-ta');
    if (t) { t.style.height = 'auto'; t.style.height = Math.min(t.scrollHeight + 2, 240) + 'px'; }
  }
  // Дополнительные контролы окна. h: { step(d) -> новое значение, del(), sub(bool) }
  function bindModal(ov, h) {
    fitPrompt(ov);
    ov.querySelectorAll('[data-step]').forEach(function (b) {
      b.addEventListener('click', function () { var v = h.step(+b.dataset.step); var el = ov.querySelector('[data-ctx]'); if (el) el.textContent = v; });
    });
    var cp = ov.querySelector('[data-copy]');
    if (cp) cp.addEventListener('click', function () {
      copy(cp.dataset.copy); cp.classList.add('is-ok');
      var svg = cp.querySelector('svg'); if (svg) svg.outerHTML = IC.ok;
    });
    var pr = ov.querySelector('#tgc-prompt');
    if (pr) pr.addEventListener('input', function () { var c = ov.querySelector('[data-cnt]'); if (c) c.textContent = cnt(pr.value); fitPrompt(ov); });
    var cid = ov.querySelector('#tgc-chatid');
    if (cid) cid.addEventListener('input', function () { var el = ov.querySelector('[data-cid-type]'); if (el) el.textContent = cid.value.trim() ? typeRu(chatType(cid.value)) : ''; });
    var an = ov.querySelector('#tgc-agentname'), nm = ov.querySelector('[data-who-nm]');
    if (an && nm) an.addEventListener('input', function () { nm.innerHTML = an.value ? esc(an.value) : '<span class="is-empty">Без имени</span>'; });
    var del = ov.querySelector('[data-del]'); if (del) del.addEventListener('click', h.del);
    ov.querySelectorAll('[data-sub]').forEach(function (b) { b.addEventListener('click', function () { h.sub(true); }); });
    ov.querySelectorAll('[data-subx]').forEach(function (b) { b.addEventListener('click', function () { h.sub(false); }); });
    // клик мимо выпадающего списка закрывает его
    ov.addEventListener('mousedown', function (e) {
      var open = ov.querySelector('.tgc-dd--open');
      if (open && !open.contains(e.target)) open.classList.remove('tgc-dd--open');
    });
  }
  // Esc по слоям: выпадающий список → экран «Расписание» → окно. true — событие обработано слоем.
  function escLayer(ov, sub, closeSub) {
    var dd = ov && ov.querySelector('.tgc-dd--open');
    if (dd) { dd.classList.remove('tgc-dd--open'); return true; }
    if (sub) { closeSub(); return true; }
    return false;
  }

  window.MessengerUI = {
    load: load, isAdmin: isAdmin, info: function () { return _info; },
    categories: categories, toolsValueHtml: toolsValueHtml, toolsContentHtml: toolsContentHtml, toggleAll: toggleAll,
    overLimit: overLimit, warnHtml: warnHtml, usageHtml: usageHtml, canAdd: canAdd,
    listHtml: listHtml, renderSectionMeta: renderSectionMeta, bindList: bindList, modalHtml: modalHtml,
    bindModal: bindModal, escLayer: escLayer, isSheet: isSheet, copy: copy, chatType: chatType, menu: menu, closeMenu: closeMenu, moreIcon: function () { return IC.more; }
  };
})();
