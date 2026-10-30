// presence.js — онлайн-счётчик (авто-обновление) + оверлей по клику + детектор нового билда.
(function () {
  const COUNT_INTERVAL = 15000; // мс

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

  function escapeHtml(str) {
    return String(str == null ? '' : str)
      .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
      .replace(/"/g, '&quot;').replace(/'/g, '&#39;');
  }

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
    try {
      // 1. Отправляем пинг присутствия (обновляет TTL в Redis)
      fetch('/api/presence/ping', { method: 'POST', credentials: 'same-origin', cache: 'no-store' }).catch(()=>{});

      // 2. Запрашиваем новые данные
      const r = await fetch('/api/presence/count', {
        credentials: 'same-origin',
        cache: 'no-store'
      });
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
    try {
      const r = await fetch('/api/presence/overlay', {
        credentials: 'same-origin',
        cache: 'no-store'
      });
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
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') refreshCount();
    else if (document.visibilityState === 'hidden') {
      navigator.sendBeacon('/api/presence/leave');
    }
  });

  refreshCount();
  setInterval(refreshCount, COUNT_INTERVAL);
})();
