// user-secrets.js — «Ключи API» в профиле (#secrets-mount).
// GET/PUT/DELETE /api/profile/secrets. Значение ключа после сохранения
// сервер не отдаёт: показываем только маску ••••last4 и даты.
(function () {
  'use strict';
  const mount = document.getElementById('secrets-mount');
  if (!mount) return;

  const PROVIDERS = {
    routerai: { label: 'RouterAPI', sub: 'Видео, речь и музыка за счёт вашего ключа',
                ph: 'sk-…', link: 'https://routerai.ru' },
  };
  const KEY_IC = '<svg class="us-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><circle cx="8" cy="15" r="4"/><path d="M10.8 12.2 20 3"/><path d="m16 7 3 3"/><path d="m14 9 2 2"/></svg>';
  const esc = (s) => String(s ?? '').replace(/[&<>"']/g, c => ({'&':'&amp;','<':'&lt;','>':'&gt;','"':'&quot;',"'":'&#39;'}[c]));
  const pad = (n) => String(n).padStart(2, '0');
  const fmt = (iso) => { const d = iso ? new Date(iso) : null; return d && !isNaN(d) ? `${pad(d.getDate())}.${pad(d.getMonth() + 1)}` : ''; };

  let items = {};       // provider -> {last4, updated_at, last_used_at}
  let editing = null;   // provider в режиме ввода
  let busy = false;
  const msg = {};       // provider -> {text, warn}

  async function load() {
    try {
      const r = await fetch('/api/profile/secrets', { credentials: 'include', cache: 'no-store' });
      if (!r.ok) throw new Error(r.status);
      const data = await r.json();
      items = {};
      (data.items || []).forEach(it => { if (it.name === 'default') items[it.provider] = it; });
    } catch (e) {
      msg.routerai = { text: 'Не удалось загрузить ключи', warn: true };
    }
    render();
  }

  function rowHtml(p) {
    const spec = PROVIDERS[p], it = items[p], ed = editing === p, m = msg[p];
    let sub = esc(spec.sub);
    if (it) {
      const parts = [`<span class="us-mask">••••${esc(it.last4 || '')}</span>`];
      if (it.updated_at) parts.push('обновлён ' + fmt(it.updated_at));
      parts.push(it.last_used_at ? 'использован ' + fmt(it.last_used_at) : 'ещё не использован');
      sub = parts.join(' · ');
    }
    const actions = ed ? '' : (it
      ? `<button type="button" class="us-btn" data-act="edit" data-p="${p}">Заменить</button>
         <button type="button" class="us-btn us-btn--icon" data-act="del" data-p="${p}" title="Удалить ключ" aria-label="Удалить ключ">
           <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M4 7h16"/><path d="M10 11v6M14 11v6"/><path d="M6 7l1 12a2 2 0 0 0 2 2h6a2 2 0 0 0 2-2l1-12"/><path d="M9 7V4h6v3"/></svg>
         </button>`
      : `<button type="button" class="us-btn us-btn--accent" data-act="edit" data-p="${p}">Добавить</button>`);
    const form = ed ? `
      <form class="us-form" data-p="${p}" autocomplete="off">
        <input class="us-input" type="password" name="secret" placeholder="${esc(spec.ph)}" autocomplete="new-password"
               spellcheck="false" autocapitalize="off" aria-label="Ключ ${esc(spec.label)}" maxlength="256" required>
        <button type="submit" class="us-btn us-btn--accent"${busy ? ' disabled' : ''}>Сохранить</button>
        <button type="button" class="us-btn" data-act="cancel">Отмена</button>
      </form>` : '';
    return `<div class="us-row${it ? ' is-set' : ''}${ed ? ' is-edit' : ''}" data-p="${p}">
        <div class="us-head">${KEY_IC}
          <span class="us-txt"><span class="us-title">${esc(spec.label)}</span><span class="us-sub${it ? '' : ' us-sub--wrap'}">${sub}</span></span>
          <span class="us-acts">${actions}</span>
        </div>${form}
        <div class="us-hint${m && m.warn ? ' is-warn' : ''}" aria-live="polite">${m ? esc(m.text) : ''}</div>
      </div>`;
  }

  function render() {
    mount.innerHTML = Object.keys(PROVIDERS).map(rowHtml).join('');
    if (editing) mount.querySelector(`.us-form[data-p="${editing}"] .us-input`)?.focus();
  }

  async function save(p, secret) {
    busy = true; msg[p] = { text: 'Сохраняю…' }; render();
    try {
      const r = await fetch('/api/profile/secrets', {
        method: 'PUT', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: p, secret }),
      });
      const data = await r.json().catch(() => ({}));
      if (!r.ok) throw new Error(data.detail || 'Не удалось сохранить ключ');
      items[p] = data; editing = null; msg[p] = { text: 'Ключ сохранён в зашифрованном виде' };
    } catch (e) {
      msg[p] = { text: String(e.message || e), warn: true };
    } finally {
      busy = false; render();
    }
  }

  async function del(p) {
    if (!confirm(`Удалить ключ ${PROVIDERS[p].label}? Генерация видео и аудио перестанет работать.`)) return;
    try {
      const r = await fetch('/api/profile/secrets', {
        method: 'DELETE', credentials: 'include',
        headers: { 'Content-Type': 'application/json' },
        body: JSON.stringify({ provider: p }),
      });
      if (!r.ok) throw new Error();
      delete items[p]; msg[p] = { text: 'Ключ удалён' };
    } catch (e) {
      msg[p] = { text: 'Не удалось удалить ключ', warn: true };
    }
    render();
  }

  mount.addEventListener('click', (e) => {
    const b = e.target.closest('[data-act]');
    if (!b) return;
    const p = b.dataset.p || editing;
    if (b.dataset.act === 'edit') { editing = p; msg[p] = null; render(); }
    else if (b.dataset.act === 'cancel') { editing = null; msg[p] = null; render(); }
    else if (b.dataset.act === 'del') del(p);
  });
  mount.addEventListener('submit', (e) => {
    const f = e.target.closest('.us-form');
    if (!f) return;
    e.preventDefault();
    const v = f.secret.value.trim();
    f.secret.value = '';
    if (v && !busy) save(f.dataset.p, v);
  });
  mount.addEventListener('keydown', (e) => {
    if (e.key === 'Escape' && editing) { e.stopPropagation(); editing = null; render(); }
  });

  // Профиль открывается кнопкой #profile-btn — подтягиваем свежие даты при каждом открытии.
  document.addEventListener('click', (e) => { if (e.target.closest('#profile-btn')) load(); });
  load();
})();
