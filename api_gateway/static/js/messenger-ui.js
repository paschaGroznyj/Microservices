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

  window.MessengerUI = {
    load: load, isAdmin: isAdmin, info: function () { return _info; },
    categories: categories, toolsValueHtml: toolsValueHtml, toolsContentHtml: toolsContentHtml, toggleAll: toggleAll,
    overLimit: overLimit, warnHtml: warnHtml, usageHtml: usageHtml, canAdd: canAdd
  };
})();
