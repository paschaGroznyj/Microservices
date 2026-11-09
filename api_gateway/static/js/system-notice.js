// system-notice.js — модалка системного уведомления (перезапуск сервисов).
// Источник: поле notice в ответе /api/presence/count (пишет restart.sh в Redis sys:notice).
// Ничего не блокирует: только модалка поверх активного экрана.
(function () {
  const SEEN_KEY = 'catchpm:sys_notice_closed';
  const DONE_AUTOHIDE_MS = 8000;
  let el = null;           // корень модалки
  let shownId = null;      // id уведомления в модалке
  let shownPhase = null;
  let hideTimer = null;

  function esc(s) {
    const d = document.createElement('div');
    d.textContent = s == null ? '' : String(s);
    return d.innerHTML;
  }

  function renderMd(md) {
    const src = String(md || '');
    try {
      if (window.marked) {
        const html = (window.marked.parse || window.marked)(src, { breaks: true, gfm: true });
        return window.DOMPurify ? window.DOMPurify.sanitize(html) : esc(src).replace(/\n/g, '<br>');
      }
    } catch (_) {}
    return esc(src).replace(/\n/g, '<br>');
  }

  function closedId() {
    try { return localStorage.getItem(SEEN_KEY); } catch (_) { return null; }
  }
  function rememberClosed(id) {
    try { localStorage.setItem(SEEN_KEY, id); } catch (_) {}
  }

  function close(byUser) {
    if (hideTimer) { clearTimeout(hideTimer); hideTimer = null; }
    if (!el) return;
    if (byUser && shownId) rememberClosed(shownId);
    el.classList.remove('is-open');
    const node = el;
    el = null;
    setTimeout(() => node.remove(), 180);
    document.removeEventListener('keydown', onKey, true);
  }

  function onKey(e) {
    if (e.key === 'Escape' && el) { e.stopPropagation(); close(true); }
  }

  function build() {
    const root = document.createElement('div');
    root.className = 'sys-notice';
    root.setAttribute('role', 'dialog');
    root.setAttribute('aria-modal', 'false');
    root.setAttribute('aria-labelledby', 'sys-notice-title');
    root.innerHTML =
      '<div class="sys-notice__backdrop"></div>' +
      '<div class="sys-notice__card">' +
        '<div class="sys-notice__head">' +
          '<span class="sys-notice__dot" aria-hidden="true"></span>' +
          '<span class="sys-notice__title" id="sys-notice-title"></span>' +
          '<button type="button" class="sys-notice__x" aria-label="Закрыть">×</button>' +
        '</div>' +
        '<div class="sys-notice__chips"></div>' +
        '<div class="sys-notice__body md-content"></div>' +
        '<div class="sys-notice__foot">' +
          '<span class="sys-notice__status"></span>' +
          '<button type="button" class="sys-notice__ok">Понятно</button>' +
        '</div>' +
      '</div>';
    root.querySelector('.sys-notice__backdrop').addEventListener('click', () => close(true));
    root.querySelector('.sys-notice__x').addEventListener('click', () => close(true));
    root.querySelector('.sys-notice__ok').addEventListener('click', () => close(true));
    document.body.appendChild(root);
    document.addEventListener('keydown', onKey, true);
    requestAnimationFrame(() => root.classList.add('is-open'));
    return root;
  }

  function fill(n) {
    const done = n.phase === 'done';
    el.classList.toggle('is-done', done);
    el.querySelector('.sys-notice__title').textContent =
      done ? 'Обновление завершено' : 'Технические работы';
    const chips = el.querySelector('.sys-notice__chips');
    const svcs = Array.isArray(n.services) ? n.services : [];
    chips.innerHTML = svcs.map((s) => `<span class="sys-notice__chip">${esc(s)}</span>`).join('');
    chips.hidden = !svcs.length;
    const body = el.querySelector('.sys-notice__body');
    if (body.dataset.src !== n.text) {
      body.innerHTML = renderMd(n.text);
      body.dataset.src = n.text || '';
      body.querySelectorAll('a[href]').forEach((a) => { a.target = '_blank'; a.rel = 'noopener'; });
    }
    // В фазе «готово» текст работ («займёт около минуты») уже неактуален.
    body.hidden = done || !String(n.text || '').trim();
    el.querySelector('.sys-notice__status').textContent =
      done ? 'Сервисы снова доступны' : 'Идёт перезапуск сервисов';
  }

  // notice: объект из /api/presence/count или null.
  function update(n) {
    if (!n || !n.id) {
      // Ключ истёк или удалён: модалку в фазе работ превращаем в «готово» и прячем.
      if (el && shownPhase === 'maintenance') {
        fill({ id: shownId, phase: 'done', text: '', services: [] });
        shownPhase = 'done';
        hideTimer = setTimeout(() => close(false), DONE_AUTOHIDE_MS);
      }
      return;
    }
    if (n.phase === 'done') {
      // «Готово» показываем только тем, кто видел работы (модалка открыта).
      if (el && shownId === n.id && shownPhase !== 'done') {
        fill(n);
        shownPhase = 'done';
        hideTimer = setTimeout(() => close(false), DONE_AUTOHIDE_MS);
      }
      return;
    }
    if (closedId() === n.id) return;
    if (el && shownId === n.id) { fill(n); return; }
    if (el) close(false);
    shownId = n.id;
    shownPhase = n.phase;
    el = build();
    fill(n);
  }

  window.SystemNotice = { update, close: () => close(false) };
})();
