/* ============================================================
   ОБЩИЙ ЧАТ (main-интерфейс, WebSocket-доставка)
   Отдельный модуль, не трогает основной чат с агентом.
   События приходят по WS /ws/group/{room_uid} от gateway
   (gateway <- /internal/group_message <- group-chat-consumer <- Kafka).
============================================================ */
(function () {
  const ROOM_UID = 'global';

  const GroupChat = {
    app: null,
    ws: null,
    opened: false,
    userId: null,
    username: null,
    messages: [],
    pinned: [],
    reconnectTimer: null,

    open(app) {
      this.app = app;
      this.userId = app?.currentUser?.user_id ?? null;
      this.username = app?.currentUser?.username ?? null;
      if (!this.opened) {
        this._bindInput();
        this.opened = true;
      }
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
        this.pinned = data.pinned || [];
        this._renderPinned();
      } catch (e) { console.warn('[group] pinned error', e); }
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
      } else if (ev === 'delete_message') {
        this.messages = this.messages.filter(m => m.id !== data.message_id);
        this._renderAll();
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

    async _send(text) {
      text = (text || '').trim();
      if (!text) return;
      const clientId = 'c' + Date.now() + Math.random().toString(36).slice(2, 8);
      // оптимистичный рендер
      this.messages.push({
        id: 'tmp-' + clientId, _optimistic: true, client_msg_id: clientId,
        user_id: this.userId, username: this.username, message_text: text,
        is_pinned: false, edited_at: null, created_at: new Date().toISOString(),
      });
      this._renderAll();
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
      if (nearBottom) box.scrollTop = box.scrollHeight;
    },

    _msgHtml(m) {
      const mine = String(m.user_id) === String(this.userId);
      const cls = mine ? 'gc-msg gc-msg--mine' : 'gc-msg gc-msg--other';
      const canAdmin = this.app?.currentUser?.is_admin;
      const edited = m.edited_at ? '<span class="gc-edited">(изм.)</span>' : '';
      const pin = m.is_pinned ? '<span class="gc-pin-flag" title="Закреплено">★</span>' : '';
      const author = mine ? '' : `<div class="gc-author">${this._esc(m.username || 'Аноним')}</div>`;
      const canDel = mine;
      const canPin = canAdmin;
      return `<div class="gc-row ${mine ? 'gc-row--mine' : ''}" data-id="${m.id}" data-mine="${mine ? 1 : 0}" data-pinned="${m.is_pinned ? 1 : 0}" data-can-del="${canDel ? 1 : 0}" data-can-pin="${canPin ? 1 : 0}">
        ${author}
        <div class="${cls}">
          <div class="gc-bubble-text">${this._esc(m.message_text)}</div>
          <div class="gc-meta">${pin}${edited}<span class="gc-time">${this._fmtTime(m.created_at)}</span></div>
        </div>
      </div>`;
    },

    _renderPinned() {
      const box = document.getElementById('gc-pinned');
      if (!box) return;
      if (!this.pinned.length) { box.hidden = true; box.innerHTML = ''; return; }
      box.hidden = false;
      box.innerHTML = this.pinned.map(m => `
        <div class="gc-pinned-item">
          <span class="gc-pin-flag">★</span>
          <span class="gc-pinned-author">${this._esc(m.username || '')}:</span>
          <span class="gc-pinned-text">${this._esc(m.message_text)}</span>
        </div>`).join('');
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
      const canPin = row.dataset.canPin === '1';
      const pinned = row.dataset.pinned === '1';
      if (!canDel && !canPin) return;
      const menu = document.createElement('div');
      menu.className = 'gc-ctx-menu';
      menu.id = 'gc-ctx-menu';
      const items = [];
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
          if (btn.dataset.act === 'del') this._delete(id);
          else if (btn.dataset.act === 'pin') this._pin(id, !pinned);
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
      // Поле общего чата теперь на эталонной разметке chat-input-box (#gc-input-box),
      // чипы вложений рендерятся в fa-preview-bar внутри него — идентично основному чату.
      // Передаём КОНТЕЙНЕР (.gc-input-area), внутри которого init найдёт
      // .chat-input-box через querySelector (сам себя элемент не находит).
      const attachBtn = document.getElementById('gc-attach-btn');
      const wrapper = document.querySelector('.gc-input-area');
      if (attachBtn && wrapper) {
        try { window.FileAttach?.init(attachBtn, wrapper); } catch (e) { /* noop */ }
      }
    },

    _bindInput() {
      const input = document.getElementById('gc-input');
      const sendBtn = document.getElementById('gc-send-btn');
      const box = document.getElementById('gc-input-box');
      const syncBtn = () => {
        if (!sendBtn) return;
        const hasText = !!(input && input.innerText.trim().length);
        const hasFiles = this._hasPendingFiles();
        const canSend = hasText || hasFiles;
        sendBtn.disabled = !canSend;
        sendBtn.classList.toggle('active', canSend);
      };
      const doSend = async () => {
        if (!input) return;
        const text = input.innerText.trim();
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
      input?.addEventListener('paste', () => setTimeout(syncBtn, 0));
      input?.addEventListener('keydown', (e) => {
        if (e.key === 'Enter' && !e.shiftKey) { e.preventDefault(); doSend(); }
      });

      if (box && typeof MutationObserver !== 'undefined') {
        const obs = new MutationObserver(() => syncBtn());
        obs.observe(box, { childList: true, subtree: true, attributes: true });
      }

      syncBtn();
    },
  };

  window.GroupChat = GroupChat;
})();
