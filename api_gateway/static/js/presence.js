// presence.js — онлайн-счётчик (авто-обновление) + оверлей по клику + детектор нового билда и протухшей SSO-сессии.
(function () {
  const COUNT_INTERVAL = 15000; // мс
  const AUTH_RELOAD_COOLDOWN_MS = 90000;
  const AUTH_RELOAD_KEY = 'catchpm:last_auth_reload_ts';

  const btn     = document.getElementById('presence-btn');
  const countEl = document.getElementById('presence-count');
  const overlay = document.getElementById('presence-overlay');
  const nowEl   = document.getElementById('presence-overlay-now');
  const subEl   = document.getElementById('presence-overlay-sub');
  const liveEl  = document.getElementById('presence-overlay-live');
  const topEl   = document.getElementById('presence-top');
  const wrap    = document.getElementById('presence-wrap');

  let initialBuildId = document.querySelector('meta[name="app-build-id"]')?.getAttribute('content') || null;
  let dismissedBuildId = null;
  let updateToastEl = null;
  let authToastEl = null;
  let presenceTimer = null;

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

  function isAuthRedirectOrUnauthorized(res) {
    if (!res) return false;
    if (res.type === 'opaqueredirect') return true;
    if (res.status === 401 || res.status === 302) return true;
    if (res.redirected && res.url && res.url.includes('/application/o/authorize')) return true;
    return false;
  }

  function showAuthExpiredToast() {
    if (authToastEl) return;
    const toast = document.createElement('div');
    toast.className = 'build-update-toast auth-expired-toast';
    toast.setAttribute('role', 'status');
    toast.innerHTML =
      '<span class="build-update-dot" aria-hidden="true"></span>' +
      '<span class="build-update-text">Сессия истекла после простоя</span>' +
      '<button type="button" class="build-update-btn">Обновить</button>' +
      '<button type="button" class="build-update-close" aria-label="Скрыть">×</button>';

    toast.querySelector('.build-update-btn').addEventListener('click', () => {
      window.location.reload();
    });
    toast.querySelector('.build-update-close').addEventListener('click', () => {
      toast.remove();
      authToastEl = null;
    });

    document.body.appendChild(toast);
    authToastEl = toast;
  }

  function stopAllBackgroundTraffic() {
    if (presenceTimer) {
      clearInterval(presenceTimer);
      presenceTimer = null;
    }
    try {
      if (window.GroupChat) {
        clearTimeout(window.GroupChat.reconnectTimer);
        window.GroupChat.reconnectTimer = null;
        if (window.GroupChat.es) {
          try { window.GroupChat.es.close(); } catch (_) {}
          window.GroupChat.es = null;
        }
        if (window.GroupChat.ws) {
          try { window.GroupChat.ws.onclose = null; window.GroupChat.ws.close(); } catch (_) {}
          window.GroupChat.ws = null;
        }
      }
    } catch (_) {}

    try {
      const app = window.__catchpmApp;
      if (app) {
        if (app._statusPollTimer) {
          clearInterval(app._statusPollTimer);
          app._statusPollTimer = null;
        }
        if (app._sources) {
          Object.keys(app._sources).forEach((k) => {
            try { app._sources[k].close(); } catch (_) {}
            delete app._sources[k];
          });
        }
        if (app._sockets) {
          Object.keys(app._sockets).forEach((k) => {
            try {
              app._sockets[k].onclose = null;
              app._sockets[k].close();
            } catch (_) {}
            delete app._sockets[k];
          });
        }
      }
    } catch (_) {}
  }

  function tryAutoReloadOnceWhenVisible(forceReload = false) {
    if (document.visibilityState !== 'visible' && !forceReload) return;
    try {
      const curChat = window.__catchpmApp && window.__catchpmApp.currentChat;
      if (curChat) {
        sessionStorage.setItem('catchpm:restore_chat_id', String(curChat));
      }
      const lastTs = Number(sessionStorage.getItem(AUTH_RELOAD_KEY) || 0);
      const now = Date.now();
      if (forceReload || (now - lastTs > AUTH_RELOAD_COOLDOWN_MS)) {
        sessionStorage.setItem(AUTH_RELOAD_KEY, String(now));
        window.location.reload();
        return;
      }
    } catch (_) {}
    showAuthExpiredToast();
  }

  function handleAuthSessionExpired(opts) {
    const force = !!(opts && opts.forceReload);
    if (window.__authSessionExpired && !force) {
      if (document.visibilityState === 'visible') tryAutoReloadOnceWhenVisible(false);
      return;
    }
    window.__authSessionExpired = true;
    stopAllBackgroundTraffic();
    if (force || document.visibilityState === 'visible') {
      tryAutoReloadOnceWhenVisible(force);
    } else {
      showAuthExpiredToast();
    }
  }

  window.__isAuthRedirectOrUnauthorized = isAuthRedirectOrUnauthorized;
  window.__onAuthSessionExpired = handleAuthSessionExpired;

  function showBuildUpdateToast(newBuildId) {
    if (!newBuildId || newBuildId === dismissedBuildId) return;
    if (updateToastEl) {
      updateToastEl.dataset.buildId = newBuildId;
      return;
    }
    const toast = document.createElement('div');
    toast.className = 'build-update-toast';
    toast.dataset.buildId = newBuildId;
    toast.setAttribute('role', 'status');
    toast.innerHTML =
      '<span class="build-update-dot" aria-hidden="true"></span>' +
      '<span class="build-update-text">Доступна новая версия интерфейса</span>' +
      '<button type="button" class="build-update-btn">Обновить</button>' +
      '<button type="button" class="build-update-close" aria-label="Скрыть">×</button>';

    toast.querySelector('.build-update-btn').addEventListener('click', () => {
      window.location.reload();
    });
    toast.querySelector('.build-update-close').addEventListener('click', () => {
      dismissedBuildId = toast.dataset.buildId || newBuildId;
      toast.remove();
      updateToastEl = null;
    });

    document.body.appendChild(toast);
    updateToastEl = toast;
  }

  function checkBuildId(serverBuildId) {
    if (!serverBuildId) return;
    if (!initialBuildId) {
      initialBuildId = serverBuildId;
      return;
    }
    if (serverBuildId !== initialBuildId) {
      showBuildUpdateToast(serverBuildId);
    }
  }

  async function refreshCount() {
    if (window.__authSessionExpired) return;
    if (document.visibilityState === 'hidden') return;
    try {
      const r = await fetch('/api/presence/count?ping=1', {
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'manual'
      });
      if (isAuthRedirectOrUnauthorized(r)) {
        handleAuthSessionExpired();
        return;
      }
      if (!r.ok) return;
      const hdrBuild = r.headers.get('x-build-id');
      const d = await r.json();
      checkBuildId(d.build_id || hdrBuild);
      if (btn && countEl) {
        const n = Number(d.online || 0);
        countEl.textContent = n;
        btn.classList.toggle('is-empty', n === 0);
      }
      if (liveEl && d.active_requests !== undefined) {
        const ar = Math.max(0, Number(d.active_requests || 0));
        liveEl.textContent = ar;
        liveEl.classList.toggle('is-busy', ar > 0);
      }
    } catch (e) { /* presence — не критичный путь */ }
  }

  async function loadOverlay() {
    if (window.__authSessionExpired) {
      renderOverlayError();
      return;
    }
    try {
      const r = await fetch('/api/presence/overlay', {
        credentials: 'same-origin',
        cache: 'no-store',
        redirect: 'manual'
      });
      if (isAuthRedirectOrUnauthorized(r)) {
        handleAuthSessionExpired();
        renderOverlayError();
        return;
      }
      if (!r.ok) { renderOverlayError(); return; }
      const d = await r.json();
      checkBuildId(d.build_id || r.headers.get('x-build-id'));
      const by = d.by_gateway || {};
      if (nowEl) nowEl.textContent = Number(d.online || 0);
      if (subEl) subEl.textContent =
        `main ${by.main || 0} · sigma ${by.sigma || 0} · пик за сутки ${d.peak_today || 0}`;
      if (liveEl) {
        const ar = Math.max(0, Number(d.active_requests || 0));
        liveEl.textContent = ar;
        liveEl.classList.toggle('is-busy', ar > 0);
      }
      const top = Array.isArray(d.top_users) ? d.top_users : [];
      if (topEl) {
        topEl.innerHTML = top.length
          ? top.map((u, i) =>
              `<li><span class="rank">${i + 1}</span>` +
              `<span class="uname">${escapeHtml(u.username)}</span>` +
              `<span class="msgs">${Number(u.messages || 0)}</span></li>`
            ).join('')
          : '<li class="empty">За сутки сообщений нет</li>';
      }
    } catch (e) { renderOverlayError(); }
  }

  function renderOverlayError() {
    if (topEl) topEl.innerHTML = '<li class="empty">Не удалось загрузить статистику</li>';
  }

  function openOverlay() {
    if (!overlay) return;
    overlay.hidden = false;
    loadOverlay();
  }
  function closeOverlay() { if (overlay) overlay.hidden = true; }
  function toggleOverlay() {
    if (!overlay) return;
    overlay.hidden ? openOverlay() : closeOverlay();
  }

  if (btn) {
    btn.addEventListener('click', (e) => { e.stopPropagation(); toggleOverlay(); });
  }
  document.addEventListener('click', (e) => {
    if (overlay && !overlay.hidden && wrap && !wrap.contains(e.target)) closeOverlay();
  });
  document.addEventListener('keydown', (e) => { if (e.key === 'Escape') closeOverlay(); });
  let lastWakeCheckAt = 0;
  function onWakeOrVisible() {
    if (document.visibilityState === 'hidden') return;
    if (window.__authSessionExpired) {
      tryAutoReloadOnceWhenVisible();
      return;
    }
    const now = Date.now();
    if (now - lastWakeCheckAt < 3000) return;
    lastWakeCheckAt = now;
    refreshCount();
  }

  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      onWakeOrVisible();
    } else if (document.visibilityState === 'hidden') {
      if (!window.__authSessionExpired) {
        navigator.sendBeacon('/api/presence/leave');
      }
    }
  });
  window.addEventListener('pageshow', () => onWakeOrVisible());
  window.addEventListener('focus', () => onWakeOrVisible());

  refreshCount();
  presenceTimer = setInterval(refreshCount, COUNT_INTERVAL);
})();
