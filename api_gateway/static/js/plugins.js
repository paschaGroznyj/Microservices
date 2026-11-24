/* Раздел «Плагины»: JS-скрипты для консоли браузера из /api/plugins/snippets
   (Redis plugins:snippets). Код вставляется через textContent — не исполняется
   страницей, пользователь копирует его в DevTools сам. */
(function () {
  'use strict';
  let loaded = false, loading = null;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (_) {
      const ta = el('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch (_) {}
      ta.remove(); return ok;
    }
  }

  function card(s) {
    const c = el('div', 'pl-card'); c.dataset.id = s.id;
    const head = el('div', 'pl-card__head');
    const meta = el('div', 'pl-card__meta');
    meta.append(el('h3', 'pl-card__title', s.title), el('p', 'pl-card__desc', s.description));
    const btns = el('div', 'pl-card__btns');
    const show = el('button', 'pl-btn', 'Показать код'); show.type = 'button';
    const copy = el('button', 'pl-btn', 'Копировать'); copy.type = 'button';
    btns.append(show, copy); head.append(meta, btns);
    const pre = el('pre', 'pl-code', s.code); pre.hidden = true;
    show.addEventListener('click', () => { pre.hidden = !pre.hidden; show.textContent = pre.hidden ? 'Показать код' : 'Скрыть код'; });
    copy.addEventListener('click', async () => {
      const ok = await copyText(s.code);
      copy.textContent = ok ? 'Скопировано' : 'Не удалось'; copy.classList.toggle('pl-btn--ok', ok);
      setTimeout(() => { copy.textContent = 'Копировать'; copy.classList.remove('pl-btn--ok'); }, 1600);
    });
    c.append(head, pre); return c;
  }

  async function load() {
    const list = document.getElementById('plugins-snippets');
    if (!list) return;
    list.replaceChildren(el('div', 'pl-state', 'Загрузка…'));
    try {
      const r = await fetch('/api/plugins/snippets', { credentials: 'same-origin', cache: 'no-store' });
      if (!r.ok) throw new Error('HTTP ' + r.status);
      const items = (await r.json()).snippets || [];
      list.replaceChildren(...(items.length ? items.map(card) : [el('div', 'pl-state', 'Скриптов пока нет')]));
      loaded = true;
    } catch (e) {
      list.replaceChildren(el('div', 'pl-state', 'Не удалось загрузить скрипты (' + e.message + ')'));
    }
  }

  window.PluginsPanel = {
    open() { if (!loaded && !loading) loading = load().finally(() => { loading = null; }); },
    reload() { loaded = false; return this.open(); },
  };
})();
