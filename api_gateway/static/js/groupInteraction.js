/* ════════════════════════════════════════════════════════════
   Реакции и контекстное меню общего чата (как в Telegram).

   ПКМ по сообщению: панель быстрых реакций + пункты с SVG-иконками.
   Шеврон раскрывает квадрат 8×8 со всеми реакциями (без поиска).
   Выбранная реакция попадает в .gc-meta плашкой.

   Одна реакция на пользователя: выбор другой заменяет, повтор снимает.
   Клики копятся 1 с и уходят пачкой в POST /api/group/reactions,
   дальше Redis-шина гейтвея (reactions_bus.py) и событие reactions.
   Набор эмодзи совпадает с chats_service/app/core/reactions.py.

   Галочки (как в Telegram), только у своих сообщений, правее времени:
   часики — ещё не в БД, ✓ — записано, ✓✓ — прочитал хоть кто-то
   (id <= readUpto = MAX отметки других из ответа истории и событий read).
   Прочтение: IntersectionObserver по строкам ленты, вкладка активна,
   новый максимум уходит в POST /api/group/{room}/read не чаще раза в 1 с,
   дальше Redis-шина reads_bus.py. ПКМ по своему: «Прочитали N» и список.
   ════════════════════════════════════════════════════════════ */
(function () {
  'use strict';

  const REACTIONS = [
    '❤️', '🔥', '😁', '👎', '👍', '💯', '😢', '😨',
    '👌', '😱', '🤯', '🤩', '🤡', '🤔', '🥺', '🤝',
    '🥰', '👏', '🤬', '🎉', '🤮', '💩', '🙏', '🕊️',
    '🥱', '🥴', '😍', '🐳', '❤️‍🔥', '🌚', '🌭', '🤣',
    '⚡', '🍌', '🏆', '💔', '🤨', '😐', '🍓', '🍾',
    '💋', '🖕', '😈', '😴', '😭', '🤓', '👻', '👨‍💻',
    '👀', '🎃', '🙈', '😇', '✍️', '🤗', '🫡', '🎅',
    '🎄', '☃️', '💅', '🤪', '🗿', '🆒', '💘', '🦄',
  ];
  const QUICK = REACTIONS.slice(0, 7);
  const ALLOWED = new Set(REACTIONS);
  const DEBOUNCE_MS = 1000;
  const MAX_WAIT_MS = 3000;
  // своя реакция, ещё не подтверждённая сервером, сильнее события
  const OVERRIDE_TTL_MS = 10000;

  const ICONS = {
    edit: '<path d="M4 20h4L18.5 9.5a2.83 2.83 0 0 0-4-4L4 16v4Z"/><path d="m13.5 6.5 4 4"/>',
    pin: '<path d="M9 4h6l-1 6 3 3v2H7v-2l3-3-1-6Z"/><path d="M12 15v5"/>',
    unpin: '<path d="M9 4h6l-1 6 3 3v2H7v-2l3-3-1-6Z"/><path d="M12 15v5"/><path d="M4 4l16 16"/>',
    del: '<path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/><path d="M9 7V4h6v3"/>',
    chevron: '<path d="m6 9 6 6 6-6"/>',
    seen: '<path d="M2 12.5 6.5 17 16 7.5"/><path d="m11.5 15.5 1.5 1.5L22.5 7.5"/>',
    back: '<path d="m15 6-6 6 6 6"/>',
  };
  const svg = (name, size = 18) =>
    `<svg class="gc-ico" viewBox="0 0 24 24" width="${size}" height="${size}" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true">${ICONS[name]}</svg>`;

  // галочки: 16×11, цвет от .gc-meta (currentColor)
  const TICK_ONE = '<svg viewBox="0 0 16 11" width="16" height="11" aria-hidden="true"><path d="M1.5 5.8 4.9 9.2 11.6 2.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const TICK_TWO = '<svg viewBox="0 0 16 11" width="16" height="11" aria-hidden="true"><path d="M1 5.8 4.4 9.2 11.1 2.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/><path d="M7.4 8.2 8.4 9.2 15.1 2.2" fill="none" stroke="currentColor" stroke-width="1.6" stroke-linecap="round" stroke-linejoin="round"/></svg>';
  const TICK_WAIT = '<svg viewBox="0 0 12 12" width="12" height="12" aria-hidden="true"><circle cx="6" cy="6" r="4.9" fill="none" stroke="currentColor" stroke-width="1.3"/><path d="M6 3.4V6l1.8 1.2" fill="none" stroke="currentColor" stroke-width="1.3" stroke-linecap="round"/></svg>';
  const READ_DEBOUNCE_MS = 1000;
  const esc = (t) => String(t ?? '').replace(/[&<>"']/g, c => ({ '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;' }[c]));

  const GI = {
    REACTIONS, QUICK,
    // прочтение
    room: 'global',
    readUpto: 0,        // MAX отметки других: мои id <= readUpto -> ✓✓
    myRead: 0,          // своя отметка (что я уже прочитал)
    _readSent: 0,       // последнее, что ушло на сервер
    _readTimer: null,
    _readIo: null,
    _visible: new Set(),
    _queue: new Map(),      // message_id -> emoji ('' = снять)
    _override: new Map(),   // message_id -> {emoji, ts}
    _timer: null,
    _firstAt: 0,

    _gc() { return window.GroupChat; },
    _uid() { return this._gc()?.userId; },
    canReact(m) {
      return !!m && !m._optimistic && Number.isFinite(Number(m.id)) && Number(m.id) > 0;
    },

    myEmoji(m) {
      const uid = String(this._uid());
      const r = (m?.reactions || []).find(x => (x.user_ids || []).some(u => String(u) === uid));
      return r ? r.emoji : '';
    },

    /* ── рендер плашек ── */
    chipsHtml(m) {
      const list = (m?.reactions || []).filter(r => r && r.count > 0 && ALLOWED.has(r.emoji));
      if (!list.length) return '';
      const mine = this.myEmoji(m);
      return `<span class="gc-reacts">${list.map(r =>
        `<button type="button" class="gc-react${r.emoji === mine ? ' gc-react--mine' : ''}" data-emoji="${r.emoji}">` +
        `<span class="gc-react__e">${r.emoji}</span>${r.count > 1 ? `<span class="gc-react__n">${r.count}</span>` : ''}</button>`
      ).join('')}</span>`;
    },

    patchRow(messageId) {
      const gc = this._gc();
      const m = gc?.messages?.find(x => x.id === messageId);
      const row = document.querySelector(`#gc-messages .gc-row[data-id="${messageId}"]`);
      if (!m || !row) return;
      // у медиа без подписи реакции в своей полосе под медиа
      const host = row.querySelector('.gc-msg__reacts') || row.querySelector('.gc-meta');
      if (!host) return;
      host.querySelector('.gc-reacts')?.remove();
      const html = this.chipsHtml(m);
      if (html) host.insertAdjacentHTML('afterbegin', html);
      this.bindChips(row);
    },

    bindChips(root) {
      root.querySelectorAll('.gc-react').forEach(btn => {
        btn.onclick = (e) => {
          e.stopPropagation();
          const row = btn.closest('.gc-row');
          const id = Number(row?.dataset.id);
          if (id) this.toggle(id, btn.dataset.emoji);
        };
      });
    },

    /* ── локальное применение ── */
    _applyLocal(m, emoji) {
      const uid = this._uid();
      const us = String(uid);
      const list = (m.reactions || []).map(r => ({
        ...r, user_ids: (r.user_ids || []).filter(u => String(u) !== us),
      }));
      if (emoji) {
        let r = list.find(x => x.emoji === emoji);
        if (!r) { r = { emoji, count: 0, user_ids: [] }; list.push(r); }
        r.user_ids.push(uid);
      }
      list.forEach(r => { r.count = r.user_ids.length; });
      m.reactions = list.filter(r => r.count > 0);
    },

    toggle(messageId, emoji) {
      if (!ALLOWED.has(emoji)) return;
      const m = this._gc()?.messages?.find(x => x.id === messageId);
      if (!this.canReact(m)) return;
      const next = this.myEmoji(m) === emoji ? '' : emoji;
      this._applyLocal(m, next);
      this._override.set(messageId, { emoji: next, ts: Date.now() });
      this._queue.set(messageId, next);
      this.patchRow(messageId);
      this._schedule();
    },

    /* ── событие reactions от сервера ── */
    onEvent(data) {
      const gc = this._gc();
      if (!gc) return;
      for (const it of data.items || []) {
        const id = Number(it.message_id);
        const m = gc.messages?.find(x => x.id === id);
        if (!m) continue;
        m.reactions = Array.isArray(it.reactions) ? it.reactions : [];
        const ov = this._override.get(id);
        if (ov) {
          const fresh = Date.now() - ov.ts < OVERRIDE_TTL_MS;
          if (this.myEmoji(m) === ov.emoji || !fresh) this._override.delete(id);
          else this._applyLocal(m, ov.emoji);   // сервер ещё не видел последний клик
        }
        this.patchRow(id);
      }
    },

    /* ── галочки ── */
    ticksHtml(m) {
      if (!m || String(m.user_id) !== String(this._uid())) return '';
      if (m._optimistic) return `<span class="gc-ticks gc-ticks--wait" title="Отправляется">${TICK_WAIT}</span>`;
      const id = Number(m.id);
      if (!Number.isFinite(id) || id <= 0) return '';
      return id <= this.readUpto
        ? `<span class="gc-ticks gc-ticks--read" title="Прочитано">${TICK_TWO}</span>`
        : `<span class="gc-ticks" title="Отправлено">${TICK_ONE}</span>`;
    },

    /* точечно: только свои строки, без _renderAll */
    patchTicks() {
      const gc = this._gc();
      document.querySelectorAll('#gc-messages .gc-row[data-mine="1"]').forEach(row => {
        const m = gc?.messages?.find(x => String(x.id) === row.dataset.id);
        const old = row.querySelector('.gc-ticks');
        if (!m || !old) return;
        const html = this.ticksHtml(m);
        if (old.outerHTML !== html) old.outerHTML = html;
      });
    },

    /* ответ истории: {room_uid, reads: {read_upto, my_last_read_id}} */
    onHistory(data) {
      if (data?.room_uid) this.room = data.room_uid;
      const r = data?.reads;
      if (!r) return;
      this.readUpto = Math.max(this.readUpto, Number(r.read_upto) || 0);
      // Нет своей записи (has_mark === false): my_last_read_id — это просто
      // последний id комнаты, на сервере его нет. Не считаем его прочитанным и
      // отправленным, иначе первый вход ничего не запишет и у автора не будет ✓✓.
      if (r.has_mark !== false) {
        const my = Number(r.my_last_read_id) || 0;
        this.myRead = Math.max(this.myRead, my);
        this._readSent = Math.max(this._readSent, my);
      }
      this._checkRead();   // строки могли попасть в кадр раньше ответа истории
    },

    /* new_message от другого: автор прочитал всё до своего сообщения */
    onNewMessage(msg) {
      const id = Number(msg?.id);
      if (!Number.isFinite(id) || !msg || String(msg.user_id) === String(this._uid())) return;
      if (id > this.readUpto) { this.readUpto = id; this.patchTicks(); }
    },

    /* событие read: items [{user_id, last_read_id}] */
    onRead(data) {
      const me = String(this._uid());
      let changed = false;
      for (const it of data.items || []) {
        const v = Number(it.last_read_id) || 0;
        if (String(it.user_id) === me) {
          // своя другая вкладка
          this.myRead = Math.max(this.myRead, v);
          this._readSent = Math.max(this._readSent, v);
        } else if (v > this.readUpto) {
          this.readUpto = v; changed = true;
        }
      }
      if (changed) this.patchTicks();
    },

    _canRead() {
      return document.visibilityState === 'visible' && document.hasFocus()
        && (this._gc()?._isGroupTabActive?.() ?? true);
    },

    /* после каждого _renderAll: innerHTML пересоздаёт строки */
    observe(box) {
      if (!box || typeof IntersectionObserver === 'undefined') return;
      if (!this._readIo || this._readIo._root !== box) {
        this._readIo?.disconnect();
        this._readIo = new IntersectionObserver((entries) => {
          for (const en of entries) {
            const id = Number(en.target.dataset.id);
            if (!Number.isFinite(id)) continue;
            // высокое медиа целиком не влезет: хватит половины строки или половины ленты
            const seen = en.isIntersecting && (en.intersectionRatio >= 0.5
              || en.intersectionRect.height >= (en.rootBounds?.height || 1) * 0.5);
            if (seen) this._visible.add(id); else this._visible.delete(id);
          }
          this._checkRead();
        }, { root: box, threshold: [0, 0.5, 1] });
        this._readIo._root = box;
      }
      this._readIo.disconnect();
      this._visible.clear();
      box.querySelectorAll('.gc-row[data-id]').forEach(row => {
        if (Number(row.dataset.id) > 0) this._readIo.observe(row);
      });
    },

    _checkRead() {
      if (!this._canRead() || !this._visible.size) return;
      const max = Math.max(...this._visible);
      if (max <= this.myRead) return;
      this.myRead = max;
      if (!this._readTimer) this._readTimer = setTimeout(() => this._sendRead(), READ_DEBOUNCE_MS);
    },

    async _sendRead() {
      this._readTimer = null;
      const v = this.myRead;
      if (v <= this._readSent) return;
      try {
        const r = await fetch(`/api/group/${encodeURIComponent(this.room)}/read`, {
          method: 'POST', credentials: 'include', keepalive: true,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ last_read_id: v }),
        });
        if (r.status >= 500) throw new Error('HTTP ' + r.status);
        this._readSent = Math.max(this._readSent, v);
      } catch (e) {
        if (!this._readTimer) this._readTimer = setTimeout(() => this._sendRead(), 3000);
      }
    },

    _readBeacon() {
      if (this.myRead <= this._readSent || !navigator.sendBeacon) return;
      clearTimeout(this._readTimer); this._readTimer = null;
      const ok = navigator.sendBeacon(`/api/group/${encodeURIComponent(this.room)}/read`,
        new Blob([JSON.stringify({ last_read_id: this.myRead })], { type: 'application/json' }));
      if (ok) this._readSent = this.myRead;
    },

    /* ── «кто прочитал» в меню ── */
    _readersRowHtml() {
      return `<button class="gc-ctx-item gc-ctx-readers" data-act="readers" disabled>${svg('seen')}<span>Загрузка…</span></button><div class="gc-ctx-sep"></div>`;
    },

    _readersListHtml(list) {
      const items = list.map(r => {
        const name = r.username || ('Пользователь ' + r.user_id);
        return `<div class="gc-readers__item"><span class="gc-readers__ava">${esc(name.trim().charAt(0).toUpperCase() || '?')}</span><span class="gc-readers__name">${esc(name)}</span></div>`;
      }).join('');
      return `<div class="gc-ctx-menu gc-readers"><button class="gc-ctx-item gc-readers__back" data-act="back">${svg('back')}<span>Назад</span></button><div class="gc-ctx-sep"></div><div class="gc-readers__list">${items}</div></div>`;
    },

    async _loadReaders(pop, id, x0, y0, rebuild) {
      const btn = pop.querySelector('.gc-ctx-readers');
      const label = btn?.querySelector('span');
      // кэш на время жизни меню: «Назад» не дёргает GET повторно
      let data = pop._readers || null;
      if (!data) {
        try {
          const r = await fetch(`/api/group/message/${id}/readers`, { credentials: 'include' });
          if (r.ok) data = await r.json();
        } catch (_) { /* сеть */ }
        if (data) pop._readers = data;
      }
      if (!btn || !btn.isConnected) return;
      if (!data) { label.textContent = 'Не удалось загрузить'; return; }
      const list = data.readers || [];
      if (!list.length) { label.textContent = 'Пока никто не прочитал'; return; }
      label.textContent = list.length === 1
        ? (list[0].username || 'Прочитал 1')
        : `Прочитали ${list.length}`;
      btn.disabled = false;
      btn.onclick = (ev) => {
        ev.stopPropagation();
        pop.classList.add('gc-ctx-pop--readers');
        pop.innerHTML = this._readersListHtml(list);
        pop.querySelector('.gc-readers__back').onclick = (e2) => { e2.stopPropagation(); rebuild(); };
        this._place(pop, x0, y0);
      };
    },

    /* ── отправка пачкой ── */
    _schedule() {
      const now = Date.now();
      if (!this._timer) this._firstAt = now;
      clearTimeout(this._timer);
      const wait = Math.min(DEBOUNCE_MS, Math.max(0, this._firstAt + MAX_WAIT_MS - now));
      this._timer = setTimeout(() => this.flush(), wait);
    },

    _take() {
      const items = [...this._queue].map(([message_id, emoji]) => ({ message_id, emoji }));
      this._queue.clear();
      clearTimeout(this._timer);
      this._timer = null;
      return items;
    },

    async flush() {
      const items = this._take();
      if (!items.length) return;
      try {
        const r = await fetch('/api/group/reactions', {
          method: 'POST', credentials: 'include', keepalive: true,
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ items }),
        });
        if (r.status >= 500) throw new Error('HTTP ' + r.status);
      } catch (e) {
        // сеть или 5xx: вернуть в очередь, не затирая более свежие клики
        for (const it of items) if (!this._queue.has(it.message_id)) this._queue.set(it.message_id, it.emoji);
        setTimeout(() => this._schedule(), 2000);
      }
    },

    flushBeacon() {
      const items = this._take();
      if (!items.length || !navigator.sendBeacon) return;
      navigator.sendBeacon('/api/group/reactions',
        new Blob([JSON.stringify({ items })], { type: 'application/json' }));
    },

    /* ── контекстное меню ── */
    _itemsHtml(opts) {
      const out = [];
      if (opts.canEdit) out.push(`<button class="gc-ctx-item" data-act="edit">${svg('edit')}<span>Изменить</span></button>`);
      if (opts.canPin) out.push(`<button class="gc-ctx-item" data-act="pin">${svg(opts.pinned ? 'unpin' : 'pin')}<span>${opts.pinned ? 'Открепить' : 'Закрепить в шапке'}</span></button>`);
      if (opts.canDel) out.push(`<button class="gc-ctx-item gc-ctx-item--danger" data-act="del">${svg('del')}<span>Удалить</span></button>`);
      return out.join('');
    },

    _barHtml(mine) {
      const btns = QUICK.map(e =>
        `<button type="button" class="gc-rbar__btn${e === mine ? ' is-mine' : ''}" data-emoji="${e}">${e}</button>`).join('');
      return `<div class="gc-rbar">${btns}<button type="button" class="gc-rbar__more" aria-label="Все реакции">${svg('chevron', 18)}</button></div>`;
    },

    _gridHtml(mine) {
      return `<div class="gc-rgrid">${REACTIONS.map(e =>
        `<button type="button" class="gc-rgrid__btn${e === mine ? ' is-mine' : ''}" data-emoji="${e}">${e}</button>`).join('')}</div>`;
    },

    _place(pop, x, y) {
      const w = pop.offsetWidth, h = pop.offsetHeight;
      x = Math.max(8, Math.min(x, window.innerWidth - w - 8));
      y = Math.max(8, Math.min(y, window.innerHeight - h - 8));
      pop.style.left = x + 'px';
      pop.style.top = y + 'px';
    },

    /* opts: {id, message, canEdit, canPin, canDel, pinned, onAction(act), onClose()} */
    openMenu(e, opts) {
      const react = this.canReact(opts.message);
      // «кто прочитал» — только автору, у записанного в БД сообщения
      const readers = react && String(opts.message?.user_id) === String(this._uid());
      const itemsHtml = this._itemsHtml(opts);
      if (!react && !itemsHtml) return null;
      const mine = react ? this.myEmoji(opts.message) : '';
      const pop = document.createElement('div');
      pop.id = 'gc-ctx-menu';
      pop.className = 'gc-ctx-pop';
      document.body.appendChild(pop);
      const x0 = e.clientX, y0 = e.clientY;
      const pick = (btn) => { this.toggle(opts.id, btn.dataset.emoji); opts.onClose(); };

      const build = () => {
        pop.className = 'gc-ctx-pop';
        const menuHtml = (readers ? this._readersRowHtml() : '') + itemsHtml;
        pop.innerHTML = (react ? this._barHtml(mine) : '')
          + (menuHtml ? `<div class="gc-ctx-menu">${menuHtml}</div>` : '');
        this._place(pop, x0, y0);
        pop.querySelectorAll('.gc-ctx-item:not(.gc-ctx-readers)').forEach(btn => {
          btn.onclick = () => { opts.onAction(btn.dataset.act); opts.onClose(); };
        });
        pop.querySelectorAll('.gc-rbar__btn').forEach(btn => { btn.onclick = () => pick(btn); });
        const more = pop.querySelector('.gc-rbar__more');
        if (more) more.onclick = (ev) => {
          ev.stopPropagation();
          // как в Telegram: квадрат со всеми реакциями вместо панели и пунктов
          pop.classList.add('gc-ctx-pop--grid');
          pop.innerHTML = this._gridHtml(mine);
          pop.querySelectorAll('.gc-rgrid__btn').forEach(btn => { btn.onclick = () => pick(btn); });
          this._place(pop, x0, y0);
        };
        if (readers) this._loadReaders(pop, opts.id, x0, y0, build);
      };
      build();
      return pop;
    },
  };

  window.addEventListener('pagehide', () => { GI.flushBeacon(); GI._readBeacon(); });
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'hidden') { GI.flushBeacon(); GI._readBeacon(); }
    else GI._checkRead();
  });
  // вернулись в окно: видимые сообщения теперь считаются прочитанными
  window.addEventListener('focus', () => GI._checkRead());

  window.GroupInteraction = GI;
})();
