/* ============================================================
   ОБЩИЙ ЧАТ (sigma-интерфейс, SSE-доставка)
   У sigma нет WebSocket — события приходят по SSE
   GET /api/group/{room_uid}/stream (status_store.push_group_event).
   Поле ввода БЕЗ кнопок (без скрепки) — требование sigma.
============================================================ */
(function () {
  const ROOM_UID = 'global';
  const COLLAPSE_MAX = 240; // px — порог сворачивания длинных сообщений

  const GroupChat = {
    app: null,
    es: null,
    opened: false,
    userId: null,
    username: null,
    messages: [],
    pinned: [],
    reconnectTimer: null,
    editingId: null,
    pinnedCursor: 0,

    _notifyTimer: null,
    _edgeNotifyTimer: null,
    _hasUnreadNotify: false,
    _sidebarObserverBound: false,
    _audioCtx: null,
    _lastSoundAt: 0,

    _isGroupTabActive() {
      const navEl = document.querySelector('.nav-item[data-tab="group"]');
      if (this.app && this.app.currentTab) {
        return this.app.currentTab === 'group';
      }
      return !!(navEl && navEl.classList.contains('active'));
    },

    _isMobileSidebarHidden() {
      if (window.innerWidth > 768) return false;
      const sidebar = document.querySelector('.sidebar');
      return !(sidebar && sidebar.classList.contains('open'));
    },

    _ensureNavElements() {
      const navEl = document.querySelector('.nav-item[data-tab="group"]');
      if (navEl) {
        if (!navEl.querySelector('.gc-nav-bg')) {
          const bg = document.createElement('span');
          bg.className = 'gc-nav-bg';
          bg.setAttribute('aria-hidden', 'true');
          bg.innerHTML = '<canvas class="gc-nav-graph"></canvas>';
          navEl.insertBefore(bg, navEl.firstChild);
        }
        if (!navEl.querySelector('.gc-nav-label')) {
          Array.from(navEl.childNodes).forEach((n) => {
            if (n.nodeType === Node.TEXT_NODE && n.textContent.trim()) {
              const lbl = document.createElement('span');
              lbl.className = 'gc-nav-label';
              lbl.textContent = n.textContent.trim();
              n.replaceWith(lbl);
            }
          });
        }
        let dot = navEl.querySelector('.gc-nav-dot');
        if (!dot) {
          dot = document.createElement('span');
          dot.className = 'presence-dot gc-nav-dot';
          dot.hidden = true;
          dot.setAttribute('aria-hidden', 'true');
          navEl.appendChild(dot);
        }
      }

      if (!document.getElementById('gc-mobile-edge-notify') && document.body) {
        const edge = document.createElement('div');
        edge.id = 'gc-mobile-edge-notify';
        edge.className = 'gc-mobile-edge-notify';
        edge.setAttribute('aria-hidden', 'true');
        document.body.appendChild(edge);
      }

      this._bindSidebarObserver();
      return navEl;
    },

    _bindSidebarObserver() {
      if (this._sidebarObserverBound) return;
      const sidebar = document.querySelector('.sidebar');
      if (!sidebar) return;
      this._sidebarObserverBound = true;

      const onSidebarStateChange = () => {
        const isOpen = sidebar.classList.contains('open');
        if (isOpen) {
          // Сайдбар открыли на мобилке: убираем 5px полосу слева и показываем анимацию только на «Общем чате»
          this._stopMobileEdgeNotify();
          if (this._hasUnreadNotify && !this._isGroupTabActive()) {
            this._startNavItemShimmer(5000);
          }
        }
      };

      const mo = new MutationObserver(onSidebarStateChange);
      mo.observe(sidebar, { attributes: true, attributeFilter: ['class'] });
      window.addEventListener('resize', () => {
        if (window.innerWidth > 768) {
          this._stopMobileEdgeNotify();
        }
      });
    },

    _initAudioUnlock() {
      if (this._audioUnlockBound) return;
      this._audioUnlockBound = true;
      const unlock = () => {
        try {
          const Ctx = window.AudioContext || window.webkitAudioContext;
          if (!Ctx) return;
          if (!this._audioCtx) this._audioCtx = new Ctx();
          if (this._audioCtx.state === 'suspended') {
            this._audioCtx.resume().catch(() => {});
          }
        } catch (e) {}
      };
      document.addEventListener('pointerdown', unlock, { passive: true });
      document.addEventListener('keydown', unlock, { passive: true });
    },

    _playNotifySound() {
      const nowMs = Date.now();
      if (nowMs - this._lastSoundAt < 1200) return;
      this._lastSoundAt = nowMs;
      try {
        const Ctx = window.AudioContext || window.webkitAudioContext;
        if (!Ctx) return;
        if (!this._audioCtx) this._audioCtx = new Ctx();
        const ctx = this._audioCtx;
        if (ctx.state === 'suspended') {
          ctx.resume().catch(() => {});
        }
        const t0 = ctx.currentTime + 0.01;
        const filter = ctx.createBiquadFilter();
        filter.type = 'lowpass';
        filter.frequency.setValueAtTime(2400, t0);
        filter.connect(ctx.destination);

        const playTone = (freq, startOffset, duration, peakGain) => {
          const osc = ctx.createOscillator();
          const sub = ctx.createOscillator();
          const gain = ctx.createGain();
          osc.type = 'sine';
          sub.type = 'triangle';
          osc.frequency.setValueAtTime(freq, t0 + startOffset);
          sub.frequency.setValueAtTime(freq * 0.5, t0 + startOffset);

          gain.gain.setValueAtTime(0.0001, t0 + startOffset);
          gain.gain.exponentialRampToValueAtTime(peakGain, t0 + startOffset + 0.018);
          gain.gain.exponentialRampToValueAtTime(0.0001, t0 + startOffset + duration);

          osc.connect(gain);
          sub.connect(gain);
          gain.connect(filter);

          osc.start(t0 + startOffset);
          sub.start(t0 + startOffset);
          osc.stop(t0 + startOffset + duration + 0.02);
          sub.stop(t0 + startOffset + duration + 0.02);
        };

        playTone(587.33, 0.0, 0.24, 0.065);
        playTone(880.0, 0.09, 0.34, 0.08);
      } catch (e) {}
    },

    _stopNavGraph() {
      window.ChatAnimations?.stopNavGraph?.();
    },

    _startNavGraph(navEl) {
      window.ChatAnimations?.startNavGraph?.(navEl);
    },

    _startNavItemShimmer(durationMs = 5000) {
      const navEl = this._ensureNavElements();
      if (!navEl) return;
      navEl.classList.add('gc-nav-notify');
      requestAnimationFrame(() => navEl.classList.add('bg-ready'));
      this._startNavGraph(navEl);

      clearTimeout(this._notifyTimer);
      this._notifyTimer = setTimeout(() => {
        navEl.classList.remove('bg-ready');
        setTimeout(() => {
          if (!navEl.classList.contains('bg-ready')) {
            navEl.classList.remove('gc-nav-notify');
            this._stopNavGraph();
          }
        }, 1000);
      }, durationMs);
    },

    _startMobileEdgeNotify(durationMs = 5000) {
      this._ensureNavElements();
      const edgeEl = document.getElementById('gc-mobile-edge-notify');
      if (!edgeEl) return;
      edgeEl.classList.add('gc-edge-active');
      requestAnimationFrame(() => edgeEl.classList.add('bg-ready'));
      window.ChatAnimations?.startMobileEdgeGraph?.(edgeEl);

      clearTimeout(this._edgeNotifyTimer);
      this._edgeNotifyTimer = setTimeout(() => {
        this._stopMobileEdgeNotify();
      }, durationMs);
    },

    _stopMobileEdgeNotify() {
      clearTimeout(this._edgeNotifyTimer);
      this._edgeNotifyTimer = null;
      const edgeEl = document.getElementById('gc-mobile-edge-notify');
      if (!edgeEl) {
        window.ChatAnimations?.stopMobileEdgeGraph?.();
        return;
      }
      edgeEl.classList.remove('bg-ready');
      setTimeout(() => {
        if (!edgeEl.classList.contains('bg-ready')) {
          edgeEl.classList.remove('gc-edge-active');
          window.ChatAnimations?.stopMobileEdgeGraph?.();
        }
      }, 1400);
    },

    _triggerNavNotification() {
      const navEl = this._ensureNavElements();
      if (!navEl) return;
      this._hasUnreadNotify = true;
      const dot = navEl.querySelector('.gc-nav-dot');
      if (dot) dot.hidden = false;

      this._playNotifySound();

      if (this._isMobileSidebarHidden()) {
        // На мобилке при скрытом сайдбаре подсвечиваем левую грань экрана (5px),
        // а при открытии сайдбара покажем анимацию на плашке «Общий чат»
        this._startMobileEdgeNotify(5000);
      } else {
        this._stopMobileEdgeNotify();
        this._startNavItemShimmer(5000);
      }
    },

    _clearNavNotification() {
      this._hasUnreadNotify = false;
      clearTimeout(this._notifyTimer);
      this._notifyTimer = null;
      this._stopNavGraph();
      this._stopMobileEdgeNotify();
      const navEl = document.querySelector('.nav-item[data-tab="group"]');
      if (!navEl) return;
      navEl.classList.remove('bg-ready', 'gc-nav-notify');
      const dot = navEl.querySelector('.gc-nav-dot');
      if (dot) dot.hidden = true;
    },

    init(app) {
      this.app = app;
      this.userId = app?.currentUser?.user_id ?? null;
      this.username = app?.currentUser?.username ?? null;
      this._ensureNavElements();
      this._initAudioUnlock();
      this._bindVisibilityLifecycle();
      this._connectSse();
    },

    open(app) {
      this.app = app;
      this.userId = app?.currentUser?.user_id ?? null;
      this.username = app?.currentUser?.username ?? null;
      this._ensureNavElements();
      this._initAudioUnlock();
      this._bindVisibilityLifecycle();
      this._clearNavNotification();
      if (!this.opened) {
        this._bindInput();
        this._bindPinnedBar();
        this.opened = true;
      }
      this._loadPermissions();
      this._loadHistory();
      this._loadPinned();
      this._connectSse();
    },

    _bindVisibilityLifecycle() {
      if (this._visLifecycleBound) return;
      this._visLifecycleBound = true;
      document.addEventListener('visibilitychange', () => {
        if (document.visibilityState === 'hidden') {
          clearTimeout(this._hiddenSuspendTimer);
          this._hiddenSuspendTimer = setTimeout(() => {
            if (document.visibilityState !== 'hidden') return;
            clearTimeout(this.reconnectTimer);
            this.reconnectTimer = null;
            if (this.es) {
              try { this.es.close(); } catch (_) {}
              this.es = null;
            }
          }, 45000);
        } else if (document.visibilityState === 'visible') {
          clearTimeout(this._hiddenSuspendTimer);
          this._hiddenSuspendTimer = null;
          if (!window.__authSessionExpired) {
            this._probeAndReconnect(200);
          }
        }
      });
    },

    /* ---------- сеть ---------- */
    async _loadHistory() {
      if (window.__authSessionExpired) return;
      try {
        const res = await fetch(`/api/group/${ROOM_UID}/messages?limit=80`, { credentials: 'include', redirect: 'manual' });
        if (window.__isAuthRedirectOrUnauthorized?.(res)) { window.__onAuthSessionExpired?.(); return; }
        if (!res.ok) return;
        const data = await res.json();
        this.messages = data.messages || [];
        this._renderAll();
      } catch (e) { console.warn('[group] history error', e); }
    },

    async _loadPinned() {
      if (window.__authSessionExpired) return;
      try {
        const res = await fetch(`/api/group/${ROOM_UID}/pinned`, { credentials: 'include', redirect: 'manual' });
        if (window.__isAuthRedirectOrUnauthorized?.(res)) { window.__onAuthSessionExpired?.(); return; }
        if (!res.ok) return;
        const data = await res.json();
        this.pinned = (data.pinned || []).slice().sort((a, b) => (a.id || 0) - (b.id || 0));
        this.pinnedCursor = Math.max(0, this.pinned.length - 1);
        this._pinnedTouchedAfterJump = true;
        this._hasEverJumpedPin = false;
        this._renderPinned();
      } catch (e) { console.warn('[group] pinned error', e); }
    },

    async _loadPermissions() {
      if (window.__authSessionExpired) return;
      // Права админа нет в currentUser (/profile/me) — тянем из отдельного эндпоинта.
      try {
        const res = await fetch('/api/auth/permissions', { credentials: 'include', redirect: 'manual' });
        if (window.__isAuthRedirectOrUnauthorized?.(res)) { window.__onAuthSessionExpired?.(); return; }
        if (!res.ok) { this.isAdmin = false; return; }
        const data = await res.json();
        this.isAdmin = !!data.is_admin;
      } catch (e) { this.isAdmin = false; }
      // перерисовать меню/плашку с учётом прав, когда ответ пришёл
      this._renderAll();
      this._renderPinned();
    },

    _scheduleReconnect(customDelayMs) {
      clearTimeout(this.reconnectTimer);
      this.reconnectTimer = null;
      if (window.__authSessionExpired || document.visibilityState === 'hidden') return;
      const attempt = Math.min(this._reconnectAttempts || 0, 5);
      const delay = typeof customDelayMs === 'number'
        ? customDelayMs
        : Math.min(60000, 3000 * Math.pow(2, attempt));
      this._reconnectAttempts = (this._reconnectAttempts || 0) + 1;
      this.reconnectTimer = setTimeout(() => this._probeAndReconnect(), delay);
    },

    async _probeAndReconnect() {
      if (window.__authSessionExpired || document.visibilityState === 'hidden') return;
      if (this.es && this.es.readyState !== 2) return;
      try {
        const r = await fetch('/api/presence/count', {
          credentials: 'same-origin',
          cache: 'no-store',
          redirect: 'manual',
        });
        if (window.__isAuthRedirectOrUnauthorized?.(r) || r.type === 'opaqueredirect' || r.status === 401 || r.status === 302) {
          window.__onAuthSessionExpired?.();
          return;
        }
        if (!r.ok) {
          this._scheduleReconnect();
          return;
        }
      } catch (_) {
        this._scheduleReconnect();
        return;
      }
      this._connectSse();
    },

    _connectSse() {
      if (window.__authSessionExpired || document.visibilityState === 'hidden') return;
      if (this.es && this.es.readyState !== 2) return;
      if (this.es) { try { this.es.close(); } catch (e) {} this.es = null; }
      try {
        this.es = new EventSource(`/api/group/${ROOM_UID}/stream`, { withCredentials: true });
      } catch (e) { return; }
      this.es.onopen = () => {
        this._reconnectAttempts = 0;
      };
      this.es.addEventListener('group', (ev) => {
        this._reconnectAttempts = 0;
        let data; try { data = JSON.parse(ev.data); } catch { return; }
        this._onEvent(data);
      });
      this.es.onerror = () => {
        try { this.es.close(); } catch (e) {}
        this.es = null;
        this._scheduleReconnect();
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
      const byId = this.messages.findIndex(m => m.id === msg.id);
      if (byId >= 0) {
        this.messages[byId] = msg;
        if (this.opened) this._renderAll();
        return;
      }
      const byClient = msg.client_msg_id
        ? this.messages.findIndex(m => m._optimistic && m.client_msg_id === msg.client_msg_id)
        : -1;
      if (byClient >= 0) {
        this.messages[byClient] = msg;
      } else {
        this.messages.push(msg);
        if (!this._isGroupTabActive()) {
          this._triggerNavNotification(msg);
        }
      }
      if (this.opened) this._renderAll();
    },

    // Удаление сообщения (отыгрывается у всех на приём события delete_message).
    _deleteMessage(messageId) {
      this.messages = this.messages.filter(m => m.id !== messageId);
      this._renderAll();
      this._loadPinned();
    },

    async _send(text) {
      text = (text || '').trim();
      if (!text) return;
      const clientId = 'c' + Date.now() + Math.random().toString(36).slice(2, 8);
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

    _dateInfo(iso) {
      if (!iso) return null;
      try {
        const d = new Date(iso);
        if (isNaN(d.getTime())) return null;
        const y = d.getFullYear();
        const m = d.getMonth();
        const day = d.getDate();
        const key = `${y}-${String(m + 1).padStart(2, '0')}-${String(day).padStart(2, '0')}`;
        const months = [
          'января', 'февраля', 'марта', 'апреля', 'мая', 'июня',
          'июля', 'августа', 'сентября', 'октября', 'ноября', 'декабря'
        ];
        const nowYear = new Date().getFullYear();
        const label = (y === nowYear)
          ? `${day} ${months[m]}`
          : `${day} ${months[m]} ${y}`;
        return { key, label };
      } catch {
        return null;
      }
    },

    _ensureFloatingDatePill() {
      const inner = document.querySelector('#view-group .gc-inner');
      if (!inner) return null;
      let el = inner.querySelector('#gc-floating-date');
      if (!el) {
        el = document.createElement('div');
        el.id = 'gc-floating-date';
        el.className = 'gc-date-float';
        el.setAttribute('aria-hidden', 'true');
        el.innerHTML = '<span class="gc-date-pill" id="gc-floating-date-text"></span>';
        inner.appendChild(el);
      }
      return el;
    },

    _clearMergedDateSeps(box) {
      box = box || document.getElementById('gc-messages');
      if (!box) return;
      box.querySelectorAll('.gc-date-sep.is-merged').forEach(s => s.classList.remove('is-merged'));
    },

    _syncFloatingDate(box, isScrollEvent) {
      box = box || document.getElementById('gc-messages');
      const floatEl = this._ensureFloatingDatePill();
      if (!box || !floatEl) return;
      const pillEl = floatEl.querySelector('.gc-date-pill');
      const seps = Array.from(box.querySelectorAll('.gc-date-sep'));
      if (!seps.length || !pillEl) {
        floatEl.classList.remove('is-visible');
        this._clearMergedDateSeps(box);
        return;
      }

      const boxRect = box.getBoundingClientRect();
      const scrollbarGap = Math.max(0, box.offsetWidth - box.clientWidth);
      floatEl.style.right = scrollbarGap + 'px';
      const pinBar = document.getElementById('gc-pinned');
      const topOffset = (pinBar && !pinBar.hidden)
        ? Math.max(64, Math.round((pinBar.getBoundingClientRect().bottom - boxRect.top) + 8))
        : 64;
      floatEl.style.top = topOffset + 'px';

      const pillH = pillEl.offsetHeight || 26;
      let activeIdx = -1;
      for (let i = 0; i < seps.length; i++) {
        const sepTop = seps[i].getBoundingClientRect().top - boxRect.top;
        if (sepTop <= topOffset + 4) {
          activeIdx = i;
        } else {
          break;
        }
      }

      this._clearMergedDateSeps(box);

      if (activeIdx < 0) {
        floatEl.classList.remove('is-visible');
        floatEl.style.transform = 'translate3d(0, 0, 0)';
        clearTimeout(this._dateFloatHideTimer);
        return;
      }

      const activeSep = seps[activeIdx];
      const activeSepTop = activeSep.getBoundingClientRect().top - boxRect.top;
      const nextSep = (activeIdx + 1 < seps.length) ? seps[activeIdx + 1] : null;
      const label = activeSep.getAttribute('data-date-label') || '';

      if (pillEl.textContent !== label) {
        pillEl.textContent = label;
      }

      // Если мы остановились/дошли прямо до самой статичной плашки этого дня в потоке —
      // показываем саму статичную плашку на границе, плавающая не нужна
      if (activeSepTop >= topOffset - 4) {
        floatEl.classList.remove('is-visible');
        floatEl.style.transform = 'translate3d(0, 0, 0)';
        clearTimeout(this._dateFloatHideTimer);
        return;
      }

      // Мягкое выталкивание следующей плашкой снизу при подходе к границе
      let pushY = 0;
      if (nextSep) {
        const nextTop = nextSep.getBoundingClientRect().top - boxRect.top;
        const overlap = (topOffset + pillH + 4) - nextTop;
        if (overlap > 0 && overlap < pillH + 12) {
          pushY = -Math.round(overlap);
        }
      }
      floatEl.style.transform = `translate3d(0, ${pushY}px, 0)`;

      if (isScrollEvent) {
        floatEl.classList.add('is-visible');
        if (Math.abs(activeSepTop - topOffset) < pillH) {
          activeSep.classList.add('is-merged');
        }
        clearTimeout(this._dateFloatHideTimer);
        this._dateFloatHideTimer = setTimeout(() => {
          floatEl.classList.remove('is-visible');
          this._clearMergedDateSeps(box);
        }, 1050);
      } else if (floatEl.classList.contains('is-visible')) {
        if (Math.abs(activeSepTop - topOffset) < pillH) {
          activeSep.classList.add('is-merged');
        }
      }
    },

    _renderAll() {
      const box = document.getElementById('gc-messages');
      if (!box) return;
      // Полный ререндер через innerHTML сбрасывает scrollTop в 0 -> если
      // пользователь пролистал вверх (читает старое) и прилетело событие
      // (new_message/edit/pin/loadPinned), его кидало в начало ленты.
      // Запоминаем позицию и держим её, если пользователь НЕ у низа.
      const prevTop = box.scrollTop;
      const nearBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 120;
      let lastDateKey = '';
      const chunks = [];
      for (const m of this.messages) {
        const di = this._dateInfo(m.created_at);
        if (di && di.key !== lastDateKey) {
          lastDateKey = di.key;
          chunks.push(
            `<div class="gc-date-sep" data-date-key="${di.key}" data-date-label="${this._esc(di.label)}"><span class="gc-date-pill">${this._esc(di.label)}</span></div>`
          );
        }
        chunks.push(this._msgHtml(m, di));
      }
      box.innerHTML = chunks.join('');
      this._bindMsgActions(box);
      this._bindCollages(box);
      this._initCollapsibles(box);
      this._ignoreNextDateScroll = true;
      if (nearBottom) box.scrollTop = box.scrollHeight;
      else box.scrollTop = prevTop;
      requestAnimationFrame(() => { this._ignoreNextDateScroll = false; });
      this._syncScrollArrow(box);
      this._syncFloatingDate(box, false);
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
      const mediaParts = this._mediaParts(m);
      const textBlock = (m.message_text && m.message_text.trim())
        ? `<div class="gc-bubble-text message-text message-text--collapsible">${this._esc(m.message_text)}</div>${collapseBtn}`
        : '';
      const mediaTop = mediaParts.collage || '';
      const audiosHtml = mediaParts.audios || '';
      const hasImages = !!mediaTop;
      const hasAudios = !!audiosHtml;
      const hasCaption = !!(textBlock || mediaParts.docs);
      const audioOnly = hasAudios && !hasImages && !hasCaption;
      const bubbleMod = audioOnly
        ? ' gc-msg--audio-only'
        : (hasImages
            ? (hasCaption ? ' gc-msg--has-images gc-msg--with-caption' : ' gc-msg--has-images gc-msg--media-only')
            : '');
      const metaHtml = `<div class="gc-meta">${pin}${edited}<span class="gc-time">${this._fmtTime(m.created_at)}</span></div>`;
      const innerHtml = audioOnly
        ? `${audiosHtml}${metaHtml}`
        : (hasImages
            ? (hasCaption
                ? `${mediaTop}<div class="gc-msg__caption">${mediaParts.docs}${textBlock}${metaHtml}</div>`
                : `${mediaTop}${metaHtml}`)
            : `${mediaParts.docs}${textBlock}${metaHtml}`);
      const topAudios = (!audioOnly && hasAudios) ? audiosHtml : '';
      return `<div class="gc-row ${mine ? 'gc-row--mine' : ''}" data-id="${m.id}" data-mine="${mine ? 1 : 0}" data-pinned="${m.is_pinned ? 1 : 0}" data-can-del="${canDel ? 1 : 0}" data-can-edit="${canEdit ? 1 : 0}" data-can-pin="${canPin ? 1 : 0}">
        ${author}
        ${topAudios}
        <div class="${cls}${bubbleMod}">
          ${innerHtml}
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
    _isVideo(f) {
      if (!f) return false;
      if (f.is_video) return true;
      const mime = String(f.mime_type || '').toLowerCase();
      if (mime.startsWith('video/')) return true;
      const ext = String(f.file_name || '').split('.').pop().toLowerCase();
      return ['mp4', 'webm', 'mov', 'm4v', 'ogv', 'avi', 'mkv'].includes(ext);
    },

    _videoMime(f) {
      const mime = String(f?.mime_type || '').toLowerCase();
      if (mime.startsWith('video/')) return mime;
      const ext = String(f?.file_name || '').split('.').pop().toLowerCase();
      if (ext === 'webm') return 'video/webm';
      if (ext === 'mov') return 'video/quicktime';
      if (ext === 'ogv' || ext === 'ogg') return 'video/ogg';
      return 'video/mp4';
    },

    _isAudio(f) {
      if (!f) return false;
      if (window.ChatAnimations && typeof window.ChatAnimations.isPlayableAudioFile === 'function') {
        return window.ChatAnimations.isPlayableAudioFile(f, f.mime_type);
      }
      return /^audio\//i.test(String(f.mime_type || '')) || /\.(mp3|mpga|wav|wave|ogg|oga|opus|m4a|aac|flac|weba|caf|aif|aiff)$/i.test(String(f.file_name || ''));
    },

    _mediaParts(m) {
      const files = Array.isArray(m.files) ? m.files : [];
      if (!files.length) return { collage: '', audios: '', docs: '' };
      
      const visuals = files.filter(f => f && (f.is_image || (!f.is_image && this._isVideo(f))));
      const audios = files.filter(f => f && !f.is_image && !this._isVideo(f) && this._isAudio(f));
      const docs = files.filter(f => f && !f.is_image && !this._isVideo(f) && !this._isAudio(f));
      
      let collage = '';
      let audiosHtml = '';
      let docsHtml = '';
      
      if (visuals.length) {
        const n = visuals.length;
        const shown = visuals.slice(0, 4);
        const layout = n === 1 ? 'one' : n === 2 ? 'two' : n === 3 ? 'three' : 'four';
        
        const tiles = shown.map((f, i) => {
          const url = this._esc(this._fileUrl(f));
          const isVid = !f.is_image && this._isVideo(f);
          const name = this._esc(f.file_name || (isVid ? 'video.mp4' : 'image'));
          const more = (n > 4 && i === 3) ? `<span class="gc-collage__more">+${n - 4}</span>` : '';
          
          if (isVid) {
            const vmime = this._esc(this._videoMime(f));
            if (n === 1) {
              // Одиночное видео рендерится сразу с контролами в размер
              const dlUrl = url + (url.indexOf('/api/group/file/') === 0 ? '?download=1' : '');
              return `<div class="gc-collage__cell gc-collage__cell--video-single" data-img-idx="0">
                <span class="gc-collage__bg" aria-hidden="true"></span>
                <video class="gc-video" controls preload="metadata" playsinline webkit-playsinline src="${url}">
                  <source src="${url}" type="${vmime}">
                </video>
                <a class="image-lightbox__dl fa-card-dl-btn" href="${dlUrl}" download="${name}" title="Скачать" onclick="event.stopPropagation()">
                  <svg viewBox="0 0 14 14" fill="none"><path d="M7 2v7M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
                </a>
              </div>`;
            } else {
              // Видео в сетке 2x2/3x3 - просто превью (первый кадр) + кнопка Play поверх
              const dlUrl = url + (url.indexOf('/api/group/file/') === 0 ? '?download=1' : '');
              return `<div class="gc-collage__cell gc-collage__cell--video-thumb" role="button" tabindex="0" data-lb="1" data-img-idx="${i}" title="${name}">
                <video class="gc-collage__thumb-video" src="${url}" preload="auto" autoplay playsinline webkit-playsinline muted></video>
                <span class="gc-collage__play"><svg viewBox="0 0 24 24" width="28" height="28" fill="currentColor"><path d="M8 5v14l11-7z"/></svg></span>
                ${more}
              </div>`;
            }
          } else {
            const dlUrl = url + (url.indexOf('/api/group/file/') === 0 ? '?download=1' : '');
            const dlBtn = (n === 1) ? `<a class="image-lightbox__dl fa-card-dl-btn" href="${dlUrl}" download="${name}" title="Скачать" onclick="event.stopPropagation()">
              <svg viewBox="0 0 14 14" fill="none"><path d="M7 2v7M4 6l3 3 3-3M2 11h10" stroke="currentColor" stroke-width="1.5" stroke-linecap="round" stroke-linejoin="round"/></svg>
            </a>` : '';
            return `<div class="gc-collage__cell" role="button" tabindex="0" data-lb="1" data-img-idx="${i}">
              <span class="gc-collage__bg" style="background-image:url('${url}')" aria-hidden="true"></span>
              <img src="${url}" alt="${name}" loading="lazy">${more}
              ${dlBtn}
            </div>`;
          }
        }).join('');
        
        // Массивы для лайтбокса
        const items = visuals.map(f => {
          return {
            url: this._fileUrl(f),
            name: f.file_name || (f.is_image ? 'image' : 'video'),
            isVideo: (!f.is_image && this._isVideo(f)),
            mime: this._videoMime(f)
          };
        });
        
        const itemsJson = this._esc(JSON.stringify(items));
        collage = `<div class="gc-collage gc-collage--${layout}" data-items="${itemsJson}">${tiles}</div>`;
      }
      
      if (audios.length && window.ChatAnimations && typeof window.ChatAnimations.renderAudioChipHtml === 'function') {
        audiosHtml = '<div class="gc-audios">' + audios.map(f => window.ChatAnimations.renderAudioChipHtml({
          name: f.file_name || 'audio',
          url: this._fileUrl(f),
          dlUrl: this._fileUrl(f, 1),
        })).join('') + '</div>';
      }

      if (docs.length) {
        docsHtml = '<div class="gc-files">' + docs.map(f => `
          <a class="gc-file" href="${this._fileUrl(f, 1)}" target="_blank" rel="noopener" title="${this._esc(f.file_name || '')}">
            <span class="gc-file__ico">
              <svg viewBox="0 0 24 24" width="20" height="20" fill="none"><path d="M14 3v5h5M6 3h9l5 5v11a1 1 0 0 1-1 1H6a1 1 0 0 1-1-1V4a1 1 0 0 1 1-1Z" stroke="currentColor" stroke-width="1.6" stroke-linecap="round"/></svg>
            </span>
            <span class="gc-file__meta">
              <span class="gc-file__name">${this._esc(f.file_name || 'файл')}</span>
              <span class="gc-file__size">${this._fmtSize(f.size_bytes)}</span>
            </span>
          </a>`).join('') + '</div>';
      }
      return { collage, audios: audiosHtml, docs: docsHtml };
    },

    _mediaHtml(m) {
      const p = this._mediaParts(m);
      return (p.audios || '') + p.collage + p.docs;
    },

    /* лайтбокс со стрелками (стиль image-lightbox основного чата) */
    _openLightbox(items, startIdx) {
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
          <div class="image-lightbox__media-container"></div>
          <button class="gc-lb__nav gc-lb__nav--prev" type="button" aria-label="Назад" ${items.length < 2 ? 'hidden' : ''}>
            <svg viewBox="0 0 24 24" width="26" height="26" fill="none"><path d="M15 5l-7 7 7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
          <button class="gc-lb__nav gc-lb__nav--next" type="button" aria-label="Вперёд" ${items.length < 2 ? 'hidden' : ''}>
            <svg viewBox="0 0 24 24" width="26" height="26" fill="none"><path d="M9 5l7 7-7 7" stroke="currentColor" stroke-width="2" stroke-linecap="round" stroke-linejoin="round"/></svg>
          </button>
        </div>`;
      document.body.appendChild(overlay);
      const container = overlay.querySelector('.image-lightbox__media-container');
      const nameEl = overlay.querySelector('.image-lightbox__name');
      const cntEl = overlay.querySelector('.gc-lb__counter');
      const dlEl = overlay.querySelector('.image-lightbox__dl');
      

      let scale = 1, panX = 0, panY = 0;
      let currentMedia = null;
      
      const applyTransform = () => {
        if (!currentMedia || currentMedia.tagName === 'VIDEO') return;
        currentMedia.style.transform = `translate3d(${panX}px, ${panY}px, 0) scale(${scale})`;
      };
      const resetZoom = () => { scale = 1; panX = 0; panY = 0; applyTransform(); };

      const show = () => {
        const it = items[idx];
        nameEl.textContent = it.name || '';
        
        const hasMultiple = items.length > 1;
        cntEl.textContent = hasMultiple ? `${idx + 1}/${items.length}` : '';
        if (prevBtn) { prevBtn.hidden = !hasMultiple; prevBtn.style.display = hasMultiple ? '' : 'none'; }
        if (nextBtn) { nextBtn.hidden = !hasMultiple; nextBtn.style.display = hasMultiple ? '' : 'none'; }
        
        dlEl.href = it.url + (it.url.indexOf('/api/group/file/') === 0 ? '?download=1' : '');
        dlEl.setAttribute('download', it.name || 'file');
        
        container.innerHTML = '';
        resetZoom();
        
        if (it.isVideo) {
          const vid = document.createElement('video');
          vid.className = 'image-lightbox__video';
          vid.controls = true;
          vid.autoplay = true;
          vid.playsInline = true;
          vid.src = it.url;
          container.appendChild(vid);
          currentMedia = vid;
        } else {
          const img = document.createElement('img');
          img.className = 'image-lightbox__img';
          img.src = it.url;
          container.appendChild(img);
          currentMedia = img;
          
          let dragging = false, lastX, lastY;
          let touchStartDist = 0, touchStartScale = 1;
          let lastTap = 0;
          
          const clampPan = () => {
              if (scale <= 1) { panX = 0; panY = 0; return; }
              const rect = img.getBoundingClientRect();
              const boxRect = container.getBoundingClientRect();
              const maxPx = Math.max(0, (rect.width - boxRect.width) / 2);
              const maxPy = Math.max(0, (rect.height - boxRect.height) / 2);
              panX = Math.max(-maxPx, Math.min(maxPx, panX));
              panY = Math.max(-maxPy, Math.min(maxPy, panY));
          };
          
          img.addEventListener('wheel', (e) => {
            e.preventDefault();
            const step = e.deltaY < 0 ? 1.15 : (1 / 1.15);
            scale = Math.min(6, Math.max(1, scale * step));
            if (scale === 1) { panX = 0; panY = 0; } else clampPan();
            applyTransform();
          }, { passive: false });
          
          img.addEventListener('dblclick', (e) => {
                    e.stopPropagation();
                    if (scale > 1.05) resetZoom();
                    else { scale = 2.25; applyTransform(); }
                });
                img.addEventListener('mousedown', (e) => {
            if (scale <= 1) return;
            e.preventDefault(); dragging = true; lastX = e.clientX; lastY = e.clientY;
            img.style.cursor = 'grabbing';
            img.style.transition = 'none';
          });
          window.addEventListener('mousemove', (e) => {
            if (!dragging) return;
            panX += e.clientX - lastX; panY += e.clientY - lastY;
            lastX = e.clientX; lastY = e.clientY;
            clampPan(); applyTransform();
          });
          window.addEventListener('mouseup', () => {
            if (dragging) { dragging = false; img.style.cursor = scale > 1 ? 'grab' : 'zoom-in'; img.style.transition = 'transform 0.08s ease-out'; }
          });
          
          img.addEventListener('touchstart', (e) => {
              if (e.touches.length === 1) {
                  lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;
                  img.style.transition = 'none';
              } else if (e.touches.length === 2) {
                  const dx = e.touches[0].clientX - e.touches[1].clientX;
                  const dy = e.touches[0].clientY - e.touches[1].clientY;
                  touchStartDist = Math.sqrt(dx*dx + dy*dy);
                  touchStartScale = scale;
                  img.style.transition = 'none';
              }
          }, { passive: false });

          img.addEventListener('touchmove', (e) => {
              if (e.touches.length === 1 && scale > 1.01) {
                  e.preventDefault();
                  panX += e.touches[0].clientX - lastX; panY += e.touches[0].clientY - lastY;
                  lastX = e.touches[0].clientX; lastY = e.touches[0].clientY;
                  clampPan(); applyTransform();
              } else if (e.touches.length === 2) {
                  e.preventDefault();
                  const dx = e.touches[0].clientX - e.touches[1].clientX;
                  const dy = e.touches[0].clientY - e.touches[1].clientY;
                  const dist = Math.sqrt(dx*dx + dy*dy);
                  scale = Math.min(6, Math.max(1, touchStartScale * (dist / touchStartDist)));
                  if (scale <= 1) { panX = 0; panY = 0; }
                  applyTransform();
              }
          }, { passive: false });

          img.addEventListener('touchend', (e) => {
              img.style.transition = 'transform 0.08s ease-out';
              if (e.touches.length === 0) {
                  const now = Date.now();
                  if (now - lastTap < 300) {
                      if (scale > 1.05) resetZoom(); else { scale = 2.5; applyTransform(); }
                      lastTap = 0;
                  } else lastTap = now;
              }
          });
          
          let swipeStartX = 0, swipeStartY = 0;
          overlay.addEventListener('touchstart', (e) => {
              if (e.touches.length === 1 && scale <= 1.01) { swipeStartX = e.touches[0].clientX; swipeStartY = e.touches[0].clientY; }
          }, { passive: true });
          overlay.addEventListener('touchend', (e) => {
              if (e.touches.length === 0 && e.changedTouches.length === 1 && scale <= 1.01 && hasMultiple && swipeStartX > 0) {
                  const dx = e.changedTouches[0].clientX - swipeStartX;
                  const dy = e.changedTouches[0].clientY - swipeStartY;
                  if (Math.abs(dx) > 55 && Math.abs(dx) > Math.abs(dy) * 1.5) nav(dx > 0 ? -1 : 1);
                  swipeStartX = 0;
              }
          });
        }
      };
      
      const prevBtn = overlay.querySelector('.gc-lb__nav--prev');
      const nextBtn = overlay.querySelector('.gc-lb__nav--next');
      const nav = (dir) => {
        idx = (idx + dir + items.length) % items.length;
        show();
      };
      if (prevBtn) prevBtn.onclick = (e) => { e.stopPropagation(); nav(-1); };
      if (nextBtn) nextBtn.onclick = (e) => { e.stopPropagation(); nav(1); };



      const close = () => {
        document.removeEventListener('keydown', onKey);
        overlay.remove();
      };
      overlay.querySelector('.image-lightbox__close').onclick = close;
      overlay.addEventListener('click', (e) => {
        if (e.target === overlay || e.target.classList.contains('image-lightbox__box') || e.target.classList.contains('image-lightbox__media-container')) {
          close();
        }
      });
      const onKey = (e) => {
        if (e.key === 'Escape') close();
        else if (e.key === 'ArrowLeft' && scale <= 1) nav(-1);
        else if (e.key === 'ArrowRight' && scale <= 1) nav(1);
      };
      document.addEventListener('keydown', onKey);
      show();
      requestAnimationFrame(() => overlay.classList.add('image-lightbox--shown'));
    },

    /* клики по плиткам коллажа -> лайтбокс со стрелками
       + анимация загрузки: ставим is-loaded на ячейку, когда img готов
       (шиммер гаснет, картинка проявляется через opacity-transition) */
    _bindCollages(root) {
      const scope = root || document;
      if (window.ChatAnimations && typeof window.ChatAnimations.bindAudioChips === 'function') {
        window.ChatAnimations.bindAudioChips(scope);
      }
      scope.querySelectorAll('.gc-collage').forEach((col) => {
        const itemsAttr = col.getAttribute('data-items');
        if (!itemsAttr) return;
        const items = JSON.parse(itemsAttr);
        const isOne = col.classList.contains('gc-collage--one');
        
        col.querySelectorAll('[data-lb]').forEach((cell) => {
          cell.onclick = (e) => {
            if (e.target.closest('a')) return;
            const idx = parseInt(cell.getAttribute('data-img-idx') || '0', 10) || 0;
            this._openLightbox(items, idx);
          };
          
          const img = cell.querySelector('img');
          const vidThumb = cell.querySelector('.gc-collage__thumb-video');
          
          if (img) {
            const done = () => {
              cell.classList.add('is-loaded');
              if (isOne && img.naturalWidth > 0 && img.naturalHeight > 0) {
                const MAX_W = 420; const MAX_H = 420; const MIN_W = 300; const MIN_H = 110;
                const r = img.naturalWidth / img.naturalHeight;
                let w = MAX_W, h = Math.round(MAX_W / r);
                if (r < MAX_W / MAX_H) { h = MAX_H; w = Math.round(MAX_H * r); }
                const boxW = Math.min(MAX_W, Math.max(MIN_W, w));
                const boxH = Math.min(MAX_H, Math.max(MIN_H, h));
                const msgEl = cell.closest('.gc-msg--has-images');
                if (msgEl) msgEl.style.setProperty('--gc-media-w', boxW + 'px');
                cell.style.setProperty('--gc-cell-ar', `${boxW} / ${boxH}`);
                if (boxW > w + 2 || boxH > h + 2) cell.classList.add('gc-collage__cell--glass');
              }
            };
            if (img.complete && img.naturalWidth > 0) done();
            else { img.addEventListener('load', done, { once: true }); img.addEventListener('error', done, { once: true }); }
          } else if (vidThumb) {
            vidThumb.muted = true;
            vidThumb.defaultMuted = true;
            vidThumb.autoplay = true;
            vidThumb.playsInline = true;
            const restartPreview = () => {
              try { vidThumb.currentTime = 0; } catch (_) {}
              const pr = vidThumb.play();
              if (pr && typeof pr.catch === 'function') pr.catch(() => {});
            };
            vidThumb.addEventListener('timeupdate', () => {
              if (vidThumb.currentTime >= 5) restartPreview();
            });
            vidThumb.addEventListener('ended', restartPreview);
            const doneVid = () => {
              cell.classList.add('is-loaded');
              if (vidThumb.paused) {
                const pr = vidThumb.play();
                if (pr && typeof pr.catch === 'function') pr.catch(() => {});
              }
            };
            if (vidThumb.readyState >= 1) doneVid();
            else vidThumb.addEventListener('loadedmetadata', doneVid, { once: true });
          }
        });
        
        // Для одиночного видео прямо в чате
        col.querySelectorAll('.gc-collage__cell--video-single .gc-video').forEach(vid => {
          const wrap = vid.closest('.gc-collage__cell--video-single');
          const doneVid = () => {
            wrap.classList.add('is-loaded');
            const vw = vid.videoWidth || 0, vh = vid.videoHeight || 0;
            if (vw > 0 && vh > 0) {
              const MAX_W = 420; const MAX_H = 420; const MIN_W = 300; const MIN_H = 180;
              const r = vw / vh;
              let w = MAX_W, h = Math.round(MAX_W / r);
              if (r < MAX_W / MAX_H) { h = MAX_H; w = Math.round(MAX_H * r); }
              const boxW = Math.min(MAX_W, Math.max(MIN_W, w));
              const boxH = Math.min(MAX_H, Math.max(MIN_H, h));
              const msgEl = wrap.closest('.gc-msg--has-images');
              if (msgEl) msgEl.style.setProperty('--gc-media-w', boxW + 'px');
              wrap.style.setProperty('--gc-cell-ar', `${boxW} / ${boxH}`);
              if (boxW > w + 2 || boxH > h + 2) wrap.classList.add('gc-collage__cell--glass');
            }
          };
          if (vid.readyState >= 1 && vid.videoWidth > 0) doneVid();
          else vid.addEventListener('loadedmetadata', doneVid, { once: true });
        });
      });
    },

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
      if (this.pinnedCursor < 0 || this.pinnedCursor >= this.pinned.length) this.pinnedCursor = Math.max(0, this.pinned.length - 1);
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

    _ensureScrollArrowBtn() {
      const inputArea = document.querySelector('#view-group .gc-input-area');
      if (!inputArea) return null;
      let btn = inputArea.querySelector('#gc-scroll-to-bottom-btn');
      if (!btn) {
        btn = document.createElement('button');
        btn.id = 'gc-scroll-to-bottom-btn';
        btn.type = 'button';
        btn.className = 'chat-send-btn active scroll-to-bottom';
        btn.setAttribute('aria-label', 'Прокрутить вниз');
        btn.innerHTML = `
          <svg viewBox="0 0 24 24" fill="none">
            <path d="M12 5v14M18 13l-6 6-6-6"
                  stroke="currentColor" stroke-width="2"
                  stroke-linecap="round" stroke-linejoin="round"/>
          </svg>`;
        btn.addEventListener('click', () => {
          const box = document.getElementById('gc-messages');
          if (box) {
            box.scrollTo({ top: box.scrollHeight, behavior: 'smooth' });
          }
          btn.classList.remove('visible');
        });
        inputArea.insertBefore(btn, inputArea.firstChild);
      }
      return btn;
    },

    _syncScrollArrow(box) {
      box = box || document.getElementById('gc-messages');
      const btn = this._ensureScrollArrowBtn();
      if (!btn) return;
      if (!box) {
        btn.classList.remove('visible');
        return;
      }
      const distFromBottom = box.scrollHeight - box.scrollTop - box.clientHeight;
      if (distFromBottom > 500) {
        btn.classList.add('visible');
      } else {
        btn.classList.remove('visible');
      }
    },

    _computePinTargetTop(box, row) {
      const boxRect = box.getBoundingClientRect();
      const rowRect = row.getBoundingClientRect();
      const pinBar = document.getElementById('gc-pinned');
      const topInset = (pinBar && !pinBar.hidden)
        ? Math.max(84, (pinBar.getBoundingClientRect().bottom - boxRect.top) + 12)
        : 24;
      const availH = Math.max(100, box.clientHeight - topInset);
      const rowTopInScroll = (rowRect.top - boxRect.top) + box.scrollTop;
      const desired = rowTopInScroll - topInset - Math.max(0, (availH - rowRect.height) / 2);
      const maxScroll = Math.max(0, box.scrollHeight - box.clientHeight);
      return Math.max(0, Math.min(maxScroll, Math.round(desired)));
    },

    _syncPinnedByScroll(box) {
      box = box || document.getElementById('gc-messages');
      if (!box || !this.pinned || this.pinned.length <= 1) return;
      const distFromBottom = box.scrollHeight - box.scrollTop - box.clientHeight;
      if (distFromBottom < 36) {
        const lastIdx = this.pinned.length - 1;
        if (this.pinnedCursor !== lastIdx) {
          this.pinnedCursor = lastIdx;
          this._renderPinned();
        }
        return;
      }
      const boxRect = box.getBoundingClientRect();
      const pinBar = document.getElementById('gc-pinned');
      const topInset = (pinBar && !pinBar.hidden)
        ? Math.max(84, (pinBar.getBoundingClientRect().bottom - boxRect.top) + 12)
        : 24;
      const availH = Math.max(100, box.clientHeight - topInset);
      const probeY = box.scrollTop + topInset + availH * 0.56;

      let matchedIdx = 0;
      for (let i = 0; i < this.pinned.length; i++) {
        const p = this.pinned[i];
        const row = box.querySelector(`.gc-row[data-id="${p.id}"]`);
        if (!row) continue;
        const rowTop = (row.getBoundingClientRect().top - boxRect.top) + box.scrollTop;
        if (rowTop <= probeY) {
          matchedIdx = i;
        }
      }
      if (matchedIdx !== this.pinnedCursor) {
        this.pinnedCursor = matchedIdx;
        this._renderPinned();
      }
    },

    _cancelPinScrollAnim() {
      if (this._pinScrollRaf) {
        cancelAnimationFrame(this._pinScrollRaf);
        this._pinScrollRaf = null;
      }
      clearTimeout(this._programmaticPinScrollTimer);
      this._programmaticPinScroll = false;
    },

    _bindPinnedBar() {
      this._pinnedTouchedAfterJump = true;
      this._hasEverJumpedPin = false;
      this._lastPinJumpTarget = null;
      const box = document.getElementById('gc-messages');
      if (box && !box._gcPinScrollBound) {
        box._gcPinScrollBound = true;
        const onUserInteract = () => {
          if (this._programmaticPinScroll) {
            this._cancelPinScrollAnim();
          }
          this._userScrollActive = true;
        };
        const onScroll = () => {
          this._syncScrollArrow(box);
          this._syncFloatingDate(box, !this._ignoreNextDateScroll);
          if (this._programmaticPinScroll) return;
          if (this._lastPinJumpTarget != null && Math.abs(box.scrollTop - this._lastPinJumpTarget) < 14) {
            return;
          }
          this._pinnedTouchedAfterJump = true;
          this._lastPinJumpTarget = null;
          this._syncPinnedByScroll(box);
        };
        box.addEventListener('wheel', onUserInteract, { passive: true });
        box.addEventListener('touchmove', onUserInteract, { passive: true });
        box.addEventListener('pointerdown', onUserInteract, { passive: true });
        box.addEventListener('scroll', onScroll, { passive: true });
      }
      const bar = document.getElementById('gc-pinned');
      if (!bar || bar._gcPinClickBound) return;
      bar._gcPinClickBound = true;
      bar.addEventListener('click', (e) => {
        const unpin = e.target.closest('.gc-pinned-unpin');
        if (unpin) {
          e.stopPropagation();
          const m = this.pinned[this.pinnedCursor];
          if (m) this._pin(m.id, false);
          return;
        }
        const barEl = e.target.closest('.gc-pinned-bar');
        if (!barEl || !this.pinned.length) return;
        const msgBox = document.getElementById('gc-messages');
        const curMsg = this.pinned[this.pinnedCursor];
        const curRow = (msgBox && curMsg) ? msgBox.querySelector(`.gc-row[data-id="${curMsg.id}"]`) : null;
        const alreadyCentered = (this._hasEverJumpedPin && msgBox && curRow)
          ? Math.abs(msgBox.scrollTop - this._computePinTargetTop(msgBox, curRow)) <= 20
          : false;

        if (this._hasEverJumpedPin && (!this._pinnedTouchedAfterJump || alreadyCentered) && this.pinned.length > 1) {
          this.pinnedCursor = (this.pinnedCursor - 1 + this.pinned.length) % this.pinned.length;
        }
        this._hasEverJumpedPin = true;
        this._pinnedTouchedAfterJump = false;
        this._userScrollActive = false;
        const m = this.pinned[this.pinnedCursor];
        this._renderPinned();
        if (m) this._scrollToMessage(m.id);
      });
    },

    _flashBubble(row) {
      if (!row) return;
      const bubble = row.querySelector('.gc-msg');
      if (!bubble) return;
      if (bubble._gcFlashTimer) clearTimeout(bubble._gcFlashTimer);
      bubble.classList.remove('gc-msg--flash');
      void bubble.offsetWidth;
      bubble.classList.add('gc-msg--flash');
      bubble._gcFlashTimer = setTimeout(() => {
        bubble.classList.remove('gc-msg--flash');
      }, 1850);
    },

    _scrollToMessage(id) {
      const box = document.getElementById('gc-messages');
      if (!box) return;
      const row = box.querySelector(`.gc-row[data-id="${id}"]`);
      if (!row) return;

      this._cancelPinScrollAnim();
      const startTop = box.scrollTop;
      const initialTarget = this._computePinTargetTop(box, row);
      const dist = Math.abs(startTop - initialTarget);

      this._programmaticPinScroll = true;
      this._lastPinJumpTarget = initialTarget;

      if (dist <= 4) {
        box.scrollTop = initialTarget;
        this._lastPinJumpTarget = box.scrollTop;
        this._flashBubble(row);
        this._syncScrollArrow(box);
        this._programmaticPinScrollTimer = setTimeout(() => {
          this._programmaticPinScroll = false;
        }, 60);
        return;
      }

      const duration = Math.min(420, Math.max(180, Math.round(dist * 0.22)));
      const t0 = performance.now();

      const step = (now) => {
        const p = Math.min(1, (now - t0) / duration);
        const ease = 1 - Math.pow(1 - p, 3);
        const currentTarget = this._computePinTargetTop(box, row);
        box.scrollTop = Math.round(startTop + (currentTarget - startTop) * ease);
        this._syncScrollArrow(box);

        if (p < 1) {
          this._pinScrollRaf = requestAnimationFrame(step);
        } else {
          this._pinScrollRaf = null;
          const finalTarget = this._computePinTargetTop(box, row);
          box.scrollTop = finalTarget;
          this._lastPinJumpTarget = box.scrollTop;
          this._flashBubble(row);
          this._syncScrollArrow(box);
          this._programmaticPinScrollTimer = setTimeout(() => {
            this._programmaticPinScroll = false;
          }, 80);
        }
      };

      this._pinScrollRaf = requestAnimationFrame(step);
    },

    _bindMsgActions(box) {
      box.querySelectorAll('.gc-row').forEach(row => {
        row.querySelectorAll('.gc-msg, .gc-audios').forEach(el => {
          el.oncontextmenu = (e) => {
            e.preventDefault();
            this._showCtxMenu(e, row);
          };
        });
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

    _syncInputBtn() {
      const input = document.getElementById('gc-input');
      const sendBtn = document.getElementById('gc-send-btn');
      if (!sendBtn) return;
      const hasText = !!(input && input.innerText.trim().length);
      sendBtn.disabled = !hasText;
      sendBtn.classList.toggle('active', hasText);
    },

    _bindInput() {
      const input = document.getElementById('gc-input');
      const sendBtn = document.getElementById('gc-send-btn');
      const syncBtn = () => this._syncInputBtn();
      const doSend = async () => {
        if (!input) return;
        const text = input.innerText.trim();
        if (this.editingId != null) {
          if (!text) return;
          const id = this.editingId;
          this._cancelEdit();
          await this._submitEdit(id, text);
          return;
        }
        if (!text) return;
        input.innerHTML = '';
        syncBtn();
        await this._send(text);
      };
      sendBtn?.addEventListener('click', doSend);
      input?.addEventListener('input', syncBtn);
      input?.addEventListener('keyup', syncBtn);
      // sigma не загружает файлы, но не пускаем HTML/картинки в contenteditable
      input?.addEventListener('paste', (e) => {
        const cd = e.clipboardData || window.clipboardData;
        if (!cd) { setTimeout(syncBtn, 0); return; }
        const text = cd.getData('text/plain') || cd.getData('text') || '';
        e.preventDefault();
        if (text) document.execCommand('insertText', false, text);
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
