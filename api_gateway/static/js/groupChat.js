/* ============================================================
   ОБЩИЙ ЧАТ (main-интерфейс, WebSocket-доставка)
   Отдельный модуль, не трогает основной чат с агентом.
   События приходят по WS /ws/group/{room_uid} от gateway
   (gateway <- /internal/group_message <- group-chat-consumer <- Kafka).
============================================================ */
(function () {
  const ROOM_UID = 'global';
  const COLLAPSE_MAX = 240; // px — порог сворачивания длинных сообщений (как у user-message)

  const GroupChat = {
    app: null,
    ws: null,
    opened: false,
    userId: null,
    username: null,
    messages: [],
    pinned: [],
    reconnectTimer: null,
    editingId: null,      // id редактируемого сообщения (режим правки в поле ввода)
    pinnedCursor: 0,      // текущая позиция в карусели закреплённых
    isAdmin: false,       // права админа (из /api/auth/permissions)

    open(app) {
      this.app = app;
      this.userId = app?.currentUser?.user_id ?? null;
      this.username = app?.currentUser?.username ?? null;
      if (!this.opened) {
        this._bindInput();
        this._bindPinnedBar();
        this.opened = true;
      }
      this._loadPermissions();
      this._initFileAttach();
      this._loadHistory();
      this._loadPinned();
      this._connectWs();
    },

    /* ---------- сеть ---------- */
    async _loadHistory() {
      try {
        const res = await fetch(`/api/group/${ROOM_UID}/messages?limit=80`, { credentials: 'include' });
        if (!res.ok) return;
        const data = await res.json();
        this.messages = data.messages || [];
        this._renderAll();
      } catch (e) { console.warn('[group] history error', e); }
    },

    async _loadPinned() {
      try {
        const res = await fetch(`/api/group/${ROOM_UID}/pinned`, { credentials: 'include' });
        if (!res.ok) return;
        const data = await res.json();
        // по позиции сообщения в ленте (по возрастанию created_at/id) — как в Telegram
        this.pinned = (data.pinned || []).slice().sort((a, b) => (a.id || 0) - (b.id || 0));
        if (this.pinnedCursor >= this.pinned.length) this.pinnedCursor = 0;
        this._renderPinned();
      } catch (e) { console.warn('[group] pinned error', e); }
    },

    async _loadPermissions() {
      // Права админа нет в currentUser (/profile/me) — тянем из отдельного эндпоинта.
      try {
        const res = await fetch('/api/auth/permissions', { credentials: 'include' });
        if (!res.ok) { this.isAdmin = false; return; }
        const data = await res.json();
        this.isAdmin = !!data.is_admin;
      } catch (e) { this.isAdmin = false; }
      // перерисовать меню/плашку с учётом прав, когда ответ пришёл
      this._renderAll();
      this._renderPinned();
    },

    _connectWs() {
      if (this.ws && (this.ws.readyState === 0 || this.ws.readyState === 1)) return;
      const proto = location.protocol === 'https:' ? 'wss' : 'ws';
      try {
        this.ws = new WebSocket(`${proto}://${location.host}/ws/group/${ROOM_UID}`);
      } catch (e) { return; }
      this.ws.onmessage = (ev) => {
        let data; try { data = JSON.parse(ev.data); } catch { return; }
        this._onEvent(data);
      };
      this.ws.onclose = () => {
        clearTimeout(this.reconnectTimer);
        this.reconnectTimer = setTimeout(() => this._connectWs(), 3000);
      };
    },

    _onEvent(data) {
      const ev = data.event;
      if (ev === 'new_message' && data.message) {
        this._upsertMessage(data.message);
      } else if (ev === 'edit_message' && data.message) {
        this._upsertMessage(data.message);
        this._loadPinned();
      } else if (ev === 'delete_message') {
        this._deleteMessage(data.message_id);
      } else if (ev === 'pin_message' && data.message) {
        this._upsertMessage(data.message);
        this._loadPinned();
      }
    },

    _upsertMessage(msg) {
      // дедуп по id или client_msg_id (своё оптимистичное -> замена)
      const byId = this.messages.findIndex(m => m.id === msg.id);
      if (byId >= 0) { this.messages[byId] = msg; this._renderAll(); return; }
      const byClient = msg.client_msg_id
        ? this.messages.findIndex(m => m._optimistic && m.client_msg_id === msg.client_msg_id)
        : -1;
      if (byClient >= 0) { this.messages[byClient] = msg; }
      else { this.messages.push(msg); }
      this._renderAll();
    },

    // Удаление сообщения (отыгрывается у всех на приём события delete_message).
    _deleteMessage(messageId) {
      this.messages = this.messages.filter(m => m.id !== messageId);
      this._renderAll();
      this._loadPinned();
    },

    async _send(text) {
      text = (text || '').trim();
      let files = [];
      try { files = window.FileAttach?.getFiles?.() || []; } catch (e) { files = []; }
      if (!text && !files.length) return;
      const clientId = 'c' + Date.now() + Math.random().toString(36).slice(2, 8);
      // оптимистичный рендер (файлы-плейсхолдеры из локальных File -> object URL)
      const optimisticFiles = files.map((f, i) => ({
        id: 'tmpf-' + clientId + '-' + i,
        file_name: f.name, mime_type: f.type || 'application/octet-stream',
        size_bytes: f.size, is_image: (f.type || '').startsWith('image/'),
        _localUrl: (f.type || '').startsWith('image/') ? URL.createObjectURL(f) : null,
      }));
      this.messages.push({
        id: 'tmp-' + clientId, _optimistic: true, client_msg_id: clientId,
        user_id: this.userId, username: this.username, message_text: text,
        is_pinned: false, edited_at: null, created_at: new Date().toISOString(),
        files: optimisticFiles,
      });
      this._renderAll();
      // 1) заливаем файлы (если есть) в OBS через chats_service
      if (files.length) {
        try {
          const fd = new FormData();
          fd.append('client_msg_id', clientId);
          files.forEach(f => fd.append('files', f));
          await fetch(`/api/group/${ROOM_UID}/upload`, {
            method: 'POST', credentials: 'include', body: fd,
          });
        } catch (e) { console.warn('[group] upload error', e); }
      }
      // 2) отправляем сообщение (консюмер доклеит файлы по client_msg_id)
      try {
        await fetch(`/api/group/${ROOM_UID}/send`, {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message: text, client_msg_id: clientId }),
        });
      } catch (e) { console.warn('[group] send error', e); }
    },

    async _delete(id) {
      try {
        await fetch('/api/group/message/delete', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: id }),
        });
      } catch (e) { console.warn('[group] delete error', e); }
    },

    async _pin(id, isPinned) {
      try {
        await fetch('/api/group/message/pin', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: id, is_pinned: isPinned }),
        });
      } catch (e) { console.warn('[group] pin error', e); }
    },

    async _submitEdit(id, newText) {
      newText = (newText || '').trim();
      if (!newText) return;
      try {
        await fetch('/api/group/message/edit', {
          method: 'POST', credentials: 'include',
          headers: { 'Content-Type': 'application/json' },
          body: JSON.stringify({ message_id: id, new_text: newText }),
        });
      } catch (e) { console.warn('[group] edit error', e); }
    },

    /* ---------- edit-режим (в поле ввода) ---------- */
    _startEdit(id) {
      const msg = this.messages.find(m => m.id === id);
      if (!msg) return;
      this.editingId = id;
      const input = document.getElementById('gc-input');
      const area = document.querySelector('.gc-input-area');
      if (area) area.classList.add('gc-editing');
      this._renderEditBanner(msg);
      if (input) {
        input.innerText = msg.message_text || '';
        input.focus();
        // курсор в конец
        try {
          const r = document.createRange();
          r.selectNodeContents(input); r.collapse(false);
          const sel = window.getSelection(); sel.removeAllRanges(); sel.addRange(r);
        } catch (e) {}
      }
      this._syncInputBtn();
    },

    _cancelEdit() {
      this.editingId = null;
      const input = document.getElementById('gc-input');
      const area = document.querySelector('.gc-input-area');
      if (area) area.classList.remove('gc-editing');
      const banner = document.getElementById('gc-edit-banner');
      if (banner) banner.remove();
      if (input) input.innerHTML = '';
      this._syncInputBtn();
    },

    _renderEditBanner(msg) {
      const box = document.getElementById('gc-input-box');
      if (!box) return;
      let banner = document.getElementById('gc-edit-banner');
      if (!banner) {
        banner = document.createElement('div');
        banner.id = 'gc-edit-banner';
        banner.className = 'gc-edit-banner';
        box.insertBefore(banner, box.firstChild);
      }
      const preview = (msg.message_text || '').replace(/\s+/g, ' ').slice(0, 80);
      banner.innerHTML = `
        <svg class="gc-edit-banner__ic" viewBox="0 0 24 24" fill="none" width="16" height="16">
          <path d="M12 20h9M16.5 3.5a2.12 2.12 0 0 1 3 3L7 19l-4 1 1-4 12.5-12.5z" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/>
        </svg>
        <div class="gc-edit-banner__body">
          <div class="gc-edit-banner__title">Редактирование</div>
          <div class="gc-edit-banner__text">${this._esc(preview)}</div>
        </div>
        <button class="gc-edit-banner__close" type="button" aria-label="Отменить">
          <svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
        </button>`;
      banner.querySelector('.gc-edit-banner__close').onclick = () => this._cancelEdit();
    },

    /* ---------- рендер ---------- */
    _esc(s) {
      return (s || '').replace(/[&<>"']/g, c => ({
        '&': '&amp;', '<': '&lt;', '>': '&gt;', '"': '&quot;', "'": '&#39;'
      }[c]));
    },

    _fmtTime(iso) {
      if (!iso) return '';
      try {
        const d = new Date(iso);
        return d.toLocaleTimeString('ru-RU', { hour: '2-digit', minute: '2-digit' });
      } catch { return ''; }
    },

    _renderAll() {
      const box = document.getElementById('gc-messages');
      if (!box) return;
      const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
      box.innerHTML = this.messages.map(m => this._msgHtml(m)).join('');
      this._bindMsgActions(box);
      this._bindCollages(box);
      this._initCollapsibles(box);
      if (nearBottom) box.scrollTop = box.scrollHeight;
    },

    _msgHtml(m) {
      const mine = String(m.user_id) === String(this.userId);
      const cls = mine ? 'gc-msg gc-msg--mine' : 'gc-msg gc-msg--other';
      const canAdmin = this.isAdmin;
      const edited = m.edited_at ? '<span class="gc-edited">изменено</span>' : '';
      const pin = m.is_pinned ? '<span class="gc-pin-flag" title="Закреплено">★</span>' : '';
      const author = mine ? '' : `<div class="gc-author">${this._esc(m.username || 'Аноним')}</div>`;
      const canDel = mine || canAdmin;
      const canEdit = mine && !m._optimistic;
      const canPin = canAdmin;
      const collapseBtn = `<button class="msg-collapse-toggle" type="button" aria-expanded="false" hidden>
        <span class="msg-collapse-toggle__label">Показать больше</span>
        <svg class="msg-collapse-toggle__chevron" viewBox="0 0 12 12" width="12" height="12" fill="none" aria-hidden="true">
          <path d="M3 4.5 6 7.5 9 4.5" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/>
        </svg></button>`;
      const media = this._mediaHtml(m);
      const textBlock = (m.message_text && m.message_text.trim())
        ? `<div class="gc-bubble-text message-text message-text--collapsible">${this._esc(m.message_text)}</div>${collapseBtn}`
        : '';
      const bubbleMod = media && !textBlock ? ' gc-msg--media-only' : '';
      return `<div class="gc-row ${mine ? 'gc-row--mine' : ''}" data-id="${m.id}" data-mine="${mine ? 1 : 0}" data-pinned="${m.is_pinned ? 1 : 0}" data-can-del="${canDel ? 1 : 0}" data-can-edit="${canEdit ? 1 : 0}" data-can-pin="${canPin ? 1 : 0}">
        ${author}
        <div class="${cls}${bubbleMod}">
          ${media}
          ${textBlock}
          <div class="gc-meta">${pin}${edited}<span class="gc-time">${this._fmtTime(m.created_at)}</span></div>
        </div>
      </div>`;
    },

    /* url файла: локальный object-url (оптимистичный) либо через gateway */
    _fileUrl(f, download) {
      if (f._localUrl) return f._localUrl;
      const dl = download ? '?download=1' : '';
      return `/api/group/file/${f.id}${dl}`;
    },

    _fmtSize(bytes) {
      const b = Number(bytes) || 0;
      if (b < 1024) return b + ' Б';
      if (b < 1024 * 1024) return (b / 1024).toFixed(0) + ' КБ';
      return (b / 1024 / 1024).toFixed(1) + ' МБ';
    },

    /* коллаж картинок (как в ТГ) + плитки не-картинок */
    _mediaHtml(m) {
      const files = Array.isArray(m.files) ? m.files : [];
      if (!files.length) return '';
      const images = files.filter(f => f.is_image);
      const docs = files.filter(f => !f.is_image);
      let html = '';
      if (images.length) {
        const n = images.length;
        const shown = images.slice(0, 4);
        const layout = n === 1 ? 'one' : n === 2 ? 'two' : n === 3 ? 'three' : 'four';
        const tiles = shown.map((f, i) => {
          const more = (n > 4 && i === 3) ? `<span class="gc-collage__more">+${n - 4}</span>` : '';
          return `<button class="gc-collage__cell" type="button" data-lb="1" data-img-idx="${i}">
            <img src="${this._fileUrl(f)}" alt="${this._esc(f.file_name || '')}" loading="lazy">${more}
          </button>`;
        }).join('');
        // список всех url картинок сообщения — для листания в лайтбоксе
        const urls = images.map(f => this._fileUrl(f)).join('|');
        const names = images.map(f => (f.file_name || 'image')).join('|');
        html += `<div class="gc-collage gc-collage--${layout}" data-imgs="${this._esc(urls)}" data-names="${this._esc(names)}">${tiles}</div>`;
      }
      if (docs.length) {
        html += '<div class="gc-files">' + docs.map(f => `
          <a class="gc-file" href="${this._fileUrl(f, 1)}" target="_blank" rel="noopener" title="${this._esc(f.file_name || '')}">
            <span class="gc-file__ico">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none"><path d="M14 3v5h5M6 3h9l5 5v11a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.6" stroke-linejoin="round"/></svg>
            </span>
            <span class="gc-file__meta">
              <span class="gc-file__name">${this._esc(f.file_name || 'файл')}</span>
              <span class="gc-file__size">${this._fmtSize(f.size_bytes)}</span>
            </span>
          </a>`).join('') + '</div>';
      }
      return html;
    },

    /* лайтбокс со стрелками (стиль image-lightbox основного чата) */
    _openLightbox(urls, names, startIdx) {
      document.getElementById('image-lightbox')?.remove();
      let idx = startIdx || 0;
      const overlay = document.createElement('div');
      overlay.id = 'image-lightbox';
      overlay.className = 'image-lightbox gc-lightbox';
      overlay.innerHTML = `
        <div class="image-lightbox__box">
          <div class="image-lightbox__bar">
            <span class="image-lightbox__name"></span>
            <span class="gc-lb__counter"></span>
            <a class="image-lightbox__dl" href="" download title="Скачать">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none"><path d="M12 3v12m0 0 4-4m-4 4-4-4M5 21h14" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </a>
            <button class="image-lightbox__close" type="button" title="Закрыть" aria-label="Закрыть">
              <svg viewBox="0 0 24 24" width="18" height="18" fill="none"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
            </button>
          </div>
          <img class="image-lightbox__img" src="" alt="">
          <button class="gc-lb__nav gc-lb__nav--prev" type="button" aria-label="Назад" ${urls.length < 2 ? 'hidden' : ''}>
            <svg viewBox="0 0 24 24" width="26" height="26" fill="none"><path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
          <button class="gc-lb__nav gc-lb__nav--next" type="button" aria-label="Вперёд" ${urls.length < 2 ? 'hidden' : ''}>
            <svg viewBox="0 0 24 24" width="26" height="26" fill="none"><path d="M9 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>`;
      document.body.appendChild(overlay);
      const img = overlay.querySelector('.image-lightbox__img');
      const nameEl = overlay.querySelector('.image-lightbox__name');
      const cntEl = overlay.querySelector('.gc-lb__counter');
      const dlEl = overlay.querySelector('.image-lightbox__dl');
      const show = () => {
        img.src = urls[idx];
        nameEl.textContent = names[idx] || '';
        cntEl.textContent = urls.length > 1 ? `${idx + 1}/${urls.length}` : '';
        dlEl.href = urls[idx] + (urls[idx].indexOf('/api/group/file/') === 0 ? '?download=1' : '');
        dlEl.setAttribute('download', names[idx] || 'image');
      };
      const prevBtn = overlay.querySelector('.gc-lb__nav--prev');
      const nextBtn = overlay.querySelector('.gc-lb__nav--next');

      // ── зум колесом + панорамирование + двойной клик ──
      let scale = 1, panX = 0, panY = 0, dragging = false, lastX = 0, lastY = 0;
      const MIN = 1, MAX = 4;
      const applyTransform = () => {
        img.style.transform = `translate(${panX}px, ${panY}px) scale(${scale})`;
        img.style.cursor = scale > 1 ? 'grab' : 'zoom-in';
        // при зуме прячем стрелки (листание выключено, работает панорамирование)
        const showNav = scale <= 1 && urls.length > 1;
        if (prevBtn) prevBtn.style.display = showNav ? '' : 'none';
        if (nextBtn) nextBtn.style.display = showNav ? '' : 'none';
      };
      const resetZoom = () => { scale = 1; panX = 0; panY = 0; applyTransform(); };

      const nav = (d) => { idx = (idx + d + urls.length) % urls.length; resetZoom(); show(); };
      prevBtn.onclick = (e) => { e.stopPropagation(); nav(-1); };
      nextBtn.onclick = (e) => { e.stopPropagation(); nav(1); };

      img.addEventListener('wheel', (e) => {
        e.preventDefault();
        const delta = -e.deltaY;
        const step = delta > 0 ? 1.15 : 1 / 1.15;
        scale = Math.min(MAX, Math.max(MIN, scale * step));
        if (scale === 1) { panX = 0; panY = 0; }
        applyTransform();
      }, { passive: false });

      img.addEventListener('dblclick', (e) => {
        e.stopPropagation();
        if (scale > 1) resetZoom();
        else { scale = 2; applyTransform(); }
      });

      img.addEventListener('mousedown', (e) => {
        if (scale <= 1) return;
        e.preventDefault(); dragging = true; lastX = e.clientX; lastY = e.clientY;
        img.style.cursor = 'grabbing';
      });
      window.addEventListener('mousemove', (e) => {
        if (!dragging) return;
        panX += e.clientX - lastX; panY += e.clientY - lastY;
        lastX = e.clientX; lastY = e.clientY;
        applyTransform();
      });
      window.addEventListener('mouseup', () => {
        if (dragging) { dragging = false; img.style.cursor = scale > 1 ? 'grab' : 'zoom-in'; }
      });

      const close = () => {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
      };
      overlay.querySelector('.image-lightbox__close').onclick = close;
      overlay.addEventListener('click', (e) => { if (e.target === overlay) close(); });
      const onKey = (e) => {
        if (e.key === 'Escape') close();
        else if (e.key === 'ArrowLeft' && scale <= 1) nav(-1);
        else if (e.key === 'ArrowRight' && scale <= 1) nav(1);
      };
      document.addEventListener('keydown', onKey);
      show();
      applyTransform();
      requestAnimationFrame(() => overlay.classList.add('image-lightbox--shown'));
    },

    /* клики по плиткам коллажа -> лайтбокс со стрелками */
    _bindCollages(root) {
      const scope = root || document;
      scope.querySelectorAll('.gc-collage').forEach((col) => {
        const urls = (col.getAttribute('data-imgs') || '').split('|').filter(Boolean);
        const names = (col.getAttribute('data-names') || '').split('|');
        col.querySelectorAll('[data-lb]').forEach((cell) => {
          cell.onclick = () => {
            const idx = parseInt(cell.getAttribute('data-img-idx') || '0', 10) || 0;
            this._openLightbox(urls, names, idx);
          };
        });
      });
    },

    /* коллапс длинных сообщений — та же механика, что в основном чате */
    _initCollapsibles(root) {
      const scope = root || document;
      scope.querySelectorAll('.gc-msg .message-text--collapsible').forEach((el) => {
        el.style.maxHeight = COLLAPSE_MAX + 'px';
        const toggle = el.parentElement
          ? el.parentElement.querySelector('.msg-collapse-toggle')
          : null;
        const overflow = el.scrollHeight - el.clientHeight > 4;
        if (overflow) {
          if (toggle) {
            toggle.hidden = false;
            toggle.onclick = () => this._toggleCollapse(toggle);
          }
        } else {
          el.classList.remove('message-text--collapsible');
          el.style.maxHeight = '';
          if (toggle) toggle.remove();
        }
      });
    },

    _toggleCollapse(btn) {
      const wrap = btn.closest('.gc-msg') || btn.parentElement;
      const text = wrap ? wrap.querySelector('.message-text--collapsible') : null;
      if (!text) return;
      const expanded = text.classList.toggle('is-expanded');
      text.style.maxHeight = expanded ? 'none' : COLLAPSE_MAX + 'px';
      btn.setAttribute('aria-expanded', expanded ? 'true' : 'false');
      const label = btn.querySelector('.msg-collapse-toggle__label');
      if (label) label.textContent = expanded ? 'Свернуть' : 'Показать больше';
    },

    /* ---------- закреплённые (карусель как в Telegram) ---------- */
    _renderPinned() {
      const bar = document.getElementById('gc-pinned');
      if (!bar) return;
      const gcBox = document.getElementById('gc-messages');
      if (!this.pinned.length) {
        bar.hidden = true; bar.innerHTML = '';
        if (gcBox) gcBox.classList.remove('gc-has-pinned');
        return;
      }
      if (this.pinnedCursor >= this.pinned.length) this.pinnedCursor = 0;
      const m = this.pinned[this.pinnedCursor];
      const canAdmin = this.isAdmin;
      const idx = this.pinnedCursor + 1;
      const total = this.pinned.length;
      const counter = total > 1 ? `<span class="gc-pinned-counter">${idx}/${total}</span>` : '';
      const text = (m.message_text || '').replace(/\s+/g, ' ');
      const unpinBtn = canAdmin
        ? `<button class="gc-pinned-unpin" type="button" title="Открепить" aria-label="Открепить">
             <svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M6 6l12 12M18 6L6 18" stroke="currentColor" stroke-width="2" stroke-linecap="round"/></svg>
           </button>` : '';
      bar.hidden = false;
      if (gcBox) gcBox.classList.add('gc-has-pinned');
      bar.innerHTML = `
        <div class="gc-pinned-bar" data-mid="${m.id}">
          <span class="gc-pinned-ic">
            <svg viewBox="0 0 24 24" fill="none" width="16" height="16"><path d="M9 4v6l-2 3v2h10v-2l-2-3V4M12 17v3" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </span>
          ${counter}
          <div class="gc-pinned-body">
            <div class="gc-pinned-title">Закреплённое сообщение</div>
            <div class="gc-pinned-text">${this._esc(text)}</div>
          </div>
          ${unpinBtn}
        </div>`;
    },

    _bindPinnedBar() {
      const bar = document.getElementById('gc-pinned');
      if (!bar) return;
      bar.addEventListener('click', (e) => {
        const unpin = e.target.closest('.gc-pinned-unpin');
        if (unpin) {
          e.stopPropagation();
          const m = this.pinned[this.pinnedCursor];
          if (m) this._pin(m.id, false);
          return;
        }
        const barEl = e.target.closest('.gc-pinned-bar');
        if (!barEl) return;
        // клик по плашке: скроллим к текущему закрепу, затем сдвигаем карусель на следующий
        const m = this.pinned[this.pinnedCursor];
        if (m) this._scrollToMessage(m.id);
        if (this.pinned.length > 1) {
          this.pinnedCursor = (this.pinnedCursor + 1) % this.pinned.length;
          this._renderPinned();
        }
      });
    },

    _scrollToMessage(id) {
      const box = document.getElementById('gc-messages');
      if (!box) return;
      const row = box.querySelector(`.gc-row[data-id="${id}"]`);
      if (!row) return;
      row.scrollIntoView({ behavior: 'smooth', block: 'center' });
      const bubble = row.querySelector('.gc-msg');
      if (bubble) {
        bubble.classList.add('gc-msg--flash');
        setTimeout(() => bubble.classList.remove('gc-msg--flash'), 1400);
      }
    },

    _bindMsgActions(box) {
      box.querySelectorAll('.gc-row').forEach(row => {
        const msgEl = row.querySelector('.gc-msg');
        if (!msgEl) return;
        msgEl.oncontextmenu = (e) => {
          e.preventDefault();
          this._showCtxMenu(e, row);
        };
      });
    },

    _closeCtxMenu() {
      const ex = document.getElementById('gc-ctx-menu');
      if (ex) ex.remove();
      document.removeEventListener('click', this._ctxCloseHandler, true);
      document.removeEventListener('contextmenu', this._ctxCloseHandler, true);
    },

    _showCtxMenu(e, row) {
      this._closeCtxMenu();
      const id = Number(row.dataset.id);
      const canDel = row.dataset.canDel === '1';
      const canEdit = row.dataset.canEdit === '1';
      const canPin = row.dataset.canPin === '1';
      const pinned = row.dataset.pinned === '1';
      if (!canDel && !canPin && !canEdit) return;
      const menu = document.createElement('div');
      menu.className = 'gc-ctx-menu';
      menu.id = 'gc-ctx-menu';
      const items = [];
      if (canEdit) items.push(`<button class="gc-ctx-item" data-act="edit">Изменить</button>`);
      if (canPin) items.push(`<button class="gc-ctx-item" data-act="pin">${pinned ? 'Открепить' : 'Закрепить в шапке'}</button>`);
      if (canDel) items.push(`<button class="gc-ctx-item gc-ctx-item--danger" data-act="del">Удалить</button>`);
      menu.innerHTML = items.join('');
      document.body.appendChild(menu);
      const mw = menu.offsetWidth, mh = menu.offsetHeight;
      let x = e.clientX, y = e.clientY;
      if (x + mw > window.innerWidth - 8) x = window.innerWidth - mw - 8;
      if (y + mh > window.innerHeight - 8) y = window.innerHeight - mh - 8;
      menu.style.left = x + 'px';
      menu.style.top = y + 'px';
      menu.querySelectorAll('.gc-ctx-item').forEach(btn => {
        btn.onclick = () => {
          const act = btn.dataset.act;
          if (act === 'del') this._delete(id);
          else if (act === 'pin') this._pin(id, !pinned);
          else if (act === 'edit') this._startEdit(id);
          this._closeCtxMenu();
        };
      });
      this._ctxCloseHandler = (ev) => {
        if (!menu.contains(ev.target)) this._closeCtxMenu();
      };
      setTimeout(() => {
        document.addEventListener('click', this._ctxCloseHandler, true);
        document.addEventListener('contextmenu', this._ctxCloseHandler, true);
      }, 0);
    },

    _hasPendingFiles() {
      try {
        const arr = window.FileAttach?.getFiles?.() || [];
        return Array.isArray(arr) && arr.length > 0;
      } catch (e) { return false; }
    },

    _initFileAttach() {
      // Инициализируем модуль вложений для поля общего чата (main).
      // FileAttach ищет .chat-input-box как input-box и #gc-attach-btn как кнопку.
      const attachBtn = document.getElementById('gc-attach-btn');
      const wrapper = document.querySelector('.gc-input-area');
      if (attachBtn && wrapper) {
        try { window.FileAttach?.init(attachBtn, wrapper); } catch (e) { /* noop */ }
      }
    },

    _syncInputBtn() {
      const input = document.getElementById('gc-input');
      const sendBtn = document.getElementById('gc-send-btn');
      if (!sendBtn) return;
      const hasText = !!(input && input.innerText.trim().length);
      const hasFiles = this._hasPendingFiles();
      const canSend = hasText || hasFiles;
      sendBtn.disabled = !canSend;
      sendBtn.classList.toggle('active', canSend);
    },

    _bindInput() {
      const input = document.getElementById('gc-input');
      const sendBtn = document.getElementById('gc-send-btn');
      const syncBtn = () => this._syncInputBtn();
      const doSend = async () => {
        if (!input) return;
        const text = input.innerText.trim();
        // режим редактирования: сохраняем правку
        if (this.editingId != null) {
          if (!text) return;
          const id = this.editingId;
          this._cancelEdit();
          await this._submitEdit(id, text);
          return;
        }
        const hasFiles = this._hasPendingFiles();
        if (!text && !hasFiles) return;
        input.innerHTML = '';
        syncBtn();
        await this._send(text || '');
        try { window.FileAttach?.clear?.(); } catch (e) {}
        syncBtn();
      };
      sendBtn?.addEventListener('click', doSend);
      input?.addEventListener('input', syncBtn);
      input?.addEventListener('keyup', syncBtn);
      // paste: не пускаем HTML/картинки внутрь contenteditable.
      // Картинки/файлы -> FileAttach (чипы), текст -> plain insert.
      input?.addEventListener('paste', (e) => {
        const cd = e.clipboardData || window.clipboardData;
        if (!cd) { setTimeout(syncBtn, 0); return; }
        const hasFile = Array.from(cd.items || []).some(i => i.kind === 'file');
        const text = cd.getData('text/plain') || cd.getData('text') || '';
        // всегда блокируем дефолтную вставку (иначе браузер кладёт <img>/<div style>)
        e.preventDefault();
        // файлы/картинки подхватит FileAttach своим paste-хендлером (стек),
        // здесь в поле ничего не вставляем
        if (!hasFile && text) {
          document.execCommand('insertText', false, text);
        }
        setTimeout(syncBtn, 0);
      });
      input?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); }
        else if (e.key === 'Escape' && this.editingId != null) { e.preventDefault(); this._cancelEdit(); }
      });

      syncBtn();
    },
  };

  window.GroupChat = GroupChat;
})();
