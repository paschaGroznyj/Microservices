/* Подключение устройств, проверка канала и удаление офлайн-записей.
   Токен хранится только в памяти и sessionStorage текущей вкладки. */
(function () {
  'use strict';

  const SS_ID = 'augent.device_id', SS_TOKEN = 'augent.device_token';
  const shared = () => window.__auGentDeviceBridge || (window.__auGentDeviceBridge = {});
  const dev = { list: [], loaded: false, busy: false, showOffline: false, pings: {}, deleting: {}, error: '' };
  let modal = null, pollTimer = null, request = null, failures = 0;
  const REFRESH_MS = 3000;

  function el(tag, cls, text) {
    const n = document.createElement(tag);
    if (cls) n.className = cls;
    if (text != null) n.textContent = text;
    return n;
  }

  function svg(path, size) {
    const ns = 'http://www.w3.org/2000/svg';
    const s = document.createElementNS(ns, 'svg');
    s.setAttribute('viewBox', '0 0 24 24'); s.setAttribute('width', size || 16); s.setAttribute('height', size || 16);
    s.setAttribute('fill', 'none'); s.setAttribute('aria-hidden', 'true');
    const p = document.createElementNS(ns, 'path');
    p.setAttribute('d', path); p.setAttribute('stroke', 'currentColor'); p.setAttribute('stroke-width', '2');
    p.setAttribute('stroke-linecap', 'round'); p.setAttribute('stroke-linejoin', 'round');
    s.appendChild(p); return s;
  }
  const ICON_PLUS = 'M12 5v14M5 12h14';
  const ICON_REFRESH = 'M21 12a9 9 0 1 1-2.64-6.36M21 4v5h-5';
  const ICON_CLOSE = 'M18 6L6 18M6 6l12 12';
  const ICON_COPY = 'M9 9h10v10H9zM5 15V5h10';

  async function copyText(text) {
    try { await navigator.clipboard.writeText(text); return true; }
    catch (_) {
      const ta = el('textarea'); ta.value = text; ta.style.position = 'fixed'; ta.style.opacity = '0';
      document.body.appendChild(ta); ta.select();
      let ok = false; try { ok = document.execCommand('copy'); } catch (_) {}
      ta.remove(); return ok;
    }
  }

  /* ── Device Bridge API ─────────────────────────────────────── */
  const HTTP_REASON = {
    401: 'Войдите в AuGent заново.',
    403: 'Запрос отклонён сервером (проверка Origin/HTTPS).',
    404: 'Устройство или маршрут не найдены.',
    409: 'Устройство офлайн или функция выключена в клиенте.',
    422: 'Сервер отклонил данные.',
    429: 'Слишком много запросов, повторите позже.',
    502: 'Gateway недоступен.',
    503: 'Сервис недоступен.',
    504: 'Устройство не ответило вовремя.',
  };

  async function api(path, opts) {
    const o = Object.assign({ credentials: 'same-origin', cache: 'no-store' }, opts || {});
    o.headers = Object.assign({ 'X-Device-Bridge': '1' }, o.body ? { 'Content-Type': 'application/json' } : {}, o.headers || {});
    let r;
    try { r = await fetch(path, o); }
    catch (_) { throw new Error('Сеть недоступна.'); }
    // Response bodies are not shown: the status code is enough and safe.
    if (!r.ok) throw new Error('HTTP ' + r.status + '. ' + (HTTP_REASON[r.status] || 'Ошибка сервера.'));
    const data = await r.json();
    return data;
  }

  function savedCreds() {
    const s = shared();
    try {
      s.deviceId = s.deviceId || sessionStorage.getItem(SS_ID);
      s.token = s.token || sessionStorage.getItem(SS_TOKEN);
    } catch (_) {}
    return s.deviceId && s.token ? { deviceId: s.deviceId, token: s.token } : null;
  }

  function storeCreds(deviceId, token) {
    const s = shared(); s.deviceId = deviceId; s.token = token;
    try { sessionStorage.setItem(SS_ID, deviceId); sessionStorage.setItem(SS_TOKEN, token); } catch (_) {}
  }

  function detectPlatform() {
    const nav = navigator;
    const uad = (nav.userAgentData && nav.userAgentData.platform) || '';
    const ua = nav.userAgent || '';
    const p = nav.platform || '';
    // Android отдаёт navigator.platform = "Linux armv8l", поэтому проверяем его раньше Linux.
    if (/android/i.test(uad) || /android/i.test(ua)) return 'android';
    const src = uad || p;
    // iPadOS 13+ притворяется Mac; у настоящих Mac maxTouchPoints = 0.
    if (/^ios$/i.test(uad) || /iphone|ipad|ipod/i.test(ua) || (/mac/i.test(src) && nav.maxTouchPoints > 1)) return 'ios';
    return /win/i.test(src) ? 'windows' : /mac/i.test(src) ? 'macos' : 'linux';
  }
  const PLATFORM_LABEL = { windows: 'Windows', macos: 'macOS', linux: 'Linux', android: 'Android', ios: 'iOS' };
  const MOBILE_PLATFORMS = { android: 1, ios: 1 };

  /* ── Devices card ──────────────────────────────────────────── */
  function root() { return document.getElementById('plugins-devices'); }

  function active() {
    return !document.hidden && (!!modal || !document.getElementById('view-plugins')?.classList.contains('hidden'));
  }

  function schedule() {
    clearTimeout(pollTimer); pollTimer = null;
    if (active()) pollTimer = setTimeout(() => loadDevices(true), Math.min(30000, REFRESH_MS * 2 ** Math.min(failures, 3)));
  }

  function forgetCreds(deviceId) {
    const creds = savedCreds();
    if (!creds || creds.deviceId !== deviceId) return;
    const state = shared(); delete state.deviceId; delete state.token;
    try { sessionStorage.removeItem(SS_ID); sessionStorage.removeItem(SS_TOKEN); } catch (_) {}
    if (modal?.dataset.deviceId === deviceId) closeModal();
  }

  function loadDevices(quiet) {
    if (request) return request;
    dev.busy = true;
    if (!quiet) render();
    request = (async () => {
      try {
        const data = await api('/api/devices');
        const all = data.devices || [];
        const creds = savedCreds();
        if (creds && !all.some(d => d.device_id === creds.deviceId && d.status !== 'revoked')) forgetCreds(creds.deviceId);
        dev.list = all.filter(d => d.status !== 'revoked');
        dev.error = ''; dev.loaded = true; failures = 0;
      } catch (e) {
        dev.error = e.message; dev.loaded = true; failures++;
      } finally {
        dev.busy = false; request = null; render(); syncModalStatus(); schedule();
      }
    })();
    return request;
  }

  async function removeOffline(d) {
    if (d.status === 'online' || dev.deleting[d.device_id]) return;
    if (!window.confirm('Удалить офлайн-устройство «' + d.name + '»? Его токен будет отозван. Для подключения понадобится новая регистрация.')) return;
    dev.deleting[d.device_id] = true; render();
    try {
      await api('/api/devices/' + encodeURIComponent(d.device_id) + '?offline_only=true', { method: 'DELETE' });
      forgetCreds(d.device_id);
      dev.list = dev.list.filter(x => x.device_id !== d.device_id);
      delete dev.pings[d.device_id];
      // Wait for a pre-existing refresh, then fetch after DELETE so a stale
      // pre-delete response cannot resurrect the removed row.
      if (request) await request;
      await loadDevices(true);
    } catch (e) {
      dev.error = e.message;
      // A connect can win the race with DELETE; re-read but retain the error.
      const message = e.message;
      if (request) await request;
      await loadDevices(true); dev.error = message;
    } finally {
      delete dev.deleting[d.device_id]; render(); schedule();
    }
  }

  async function ping(d) {
    dev.pings[d.device_id] = { state: 'busy', text: 'Ожидаем ответ…' };
    render();
    const t0 = performance.now();
    try {
      const r = await api('/api/devices/' + encodeURIComponent(d.device_id) + '/commands', {
        method: 'POST', body: JSON.stringify({ operation: 'ping', timeout: 10 }),
      });
      const ms = Math.round(performance.now() - t0);
      const ok = r && r.status === 'ok';
      dev.pings[d.device_id] = ok
        ? { state: 'ok', text: 'Пинг: ' + (typeof r.output === 'string' && r.output && r.output.length <= 40 ? r.output : 'ok') + ' · ' + ms + ' мс' }
        : { state: 'err', text: 'Пинг: устройство ответило ошибкой.' };
    } catch (e) {
      dev.pings[d.device_id] = { state: 'err', text: 'Пинг: ' + e.message };
      if (/HTTP 409/.test(e.message)) loadDevices(true);
    }
    render();
  }

  function deviceRow(d, current) {
    const online = d.status === 'online';
    const row = el('div', 'pl-dev' + (online ? '' : ' pl-dev--off')); row.dataset.id = d.device_id;
    const dot = el('span', 'pl-dot' + (online ? ' pl-dot--on' : '')); dot.title = online ? 'Онлайн' : 'Офлайн';
    const info = el('div', 'pl-dev__info');
    const name = el('div', 'pl-dev__name');
    const nm = el('span', 'pl-dev__title', d.name || 'Устройство'); nm.title = d.name || '';
    name.append(nm);
    if (current && current === d.device_id) name.append(el('span', 'pl-tag', 'эта вкладка'));
    const meta = el('div', 'pl-dev__meta',
      [PLATFORM_LABEL[d.platform] || d.platform, online ? 'онлайн' : 'офлайн', d.device_id.slice(0, 8)].filter(Boolean).join(' · '));
    meta.title = d.device_id;
    info.append(name, meta);
    if (online && Array.isArray(d.capabilities) && d.capabilities.length) {
      const caps = el('div', 'pl-caps');
      d.capabilities.forEach(c => caps.append(el('span', 'pl-cap', c)));
      info.append(caps);
    }
    const res = dev.pings[d.device_id];
    if (res) info.append(el('div', 'pl-dev__res pl-dev__res--' + res.state, res.text));
    const act = el('div', 'pl-dev__act');
    const busy = !!(res && res.state === 'busy');
    const pb = el('button', 'pl-btn' + (online ? '' : ' pl-btn--danger'), online ? (busy ? 'Пинг…' : 'Пинг') : (dev.deleting[d.device_id] ? 'Удаление…' : 'Удалить')); pb.type = 'button';
    pb.disabled = online ? busy : !!dev.deleting[d.device_id];
    pb.title = online ? 'Проверить связь с устройством' : 'Удалить устройство и отозвать токен';
    if (!online) pb.dataset.deleteDevice = d.device_id;
    pb.addEventListener('click', () => online ? ping(d) : removeOffline(d));
    act.append(pb);
    row.append(dot, info, act);
    return row;
  }

  function render() {
    const box = root();
    if (!box) return;
    const head = el('div', 'pl-devs__head');
    const meta = el('div', 'pl-card__meta');
    meta.append(el('h3', 'pl-card__title', 'Устройства'),
      el('p', 'pl-card__desc', 'Статус обновляется каждые 3 секунды. Пинг проверяет канал с клиентом.'));
    const btns = el('div', 'pl-card__btns');
    const refresh = el('button', 'pl-icon-btn'); refresh.type = 'button';
    refresh.title = 'Обновить список'; refresh.setAttribute('aria-label', 'Обновить список');
    refresh.append(svg(ICON_REFRESH)); refresh.disabled = dev.busy;
    if (dev.busy) refresh.classList.add('is-spin');
    refresh.addEventListener('click', () => loadDevices());
    const add = el('button', 'pl-btn pl-btn--primary'); add.type = 'button'; add.id = 'plugins-connect';
    add.append(svg(ICON_PLUS, 14), document.createTextNode('Подключить устройство'));
    add.addEventListener('click', openModal);
    btns.append(refresh, add); head.append(meta, btns);

    const body = el('div', 'pl-devs__body');
    const current = savedCreds() ? shared().deviceId : null;
    if (!dev.loaded) body.append(el('div', 'pl-state', 'Загрузка устройств…'));
    else if (!dev.list.length) body.append(el('div', 'pl-state', 'Устройств пока нет. Нажмите «Подключить устройство».'));
    else {
      const on = dev.list.filter(d => d.status === 'online');
      const off = dev.list.filter(d => d.status !== 'online').sort((a, b) => (b.created_at || 0) - (a.created_at || 0));
      on.forEach(d => body.append(deviceRow(d, current)));
      if (!on.length) body.append(el('div', 'pl-state', 'Нет устройств онлайн. Запустите клиент Device Bridge на ПК.'));
      if (off.length) {
        const tg = el('button', 'pl-dev__toggle'); tg.type = 'button';
        tg.append(svg('M9 6l6 6-6 6', 14), document.createTextNode((dev.showOffline ? 'Скрыть офлайн' : 'Показать офлайн') + ' · ' + off.length)); tg.setAttribute('aria-expanded', String(dev.showOffline));
        tg.addEventListener('click', () => { dev.showOffline = !dev.showOffline; render(); });
        body.append(tg);
        if (dev.showOffline) off.forEach(d => body.append(deviceRow(d, current)));
      }
    }
    if (dev.error) body.prepend(el('div', 'pl-state pl-state--err', dev.error));
    box.replaceChildren(head, body);
  }

  /* ── Registration modal ────────────────────────────────────── */
  function closeModal() {
    if (!modal) return;
    clearTimeout(pollTimer); pollTimer = null;
    document.removeEventListener('keydown', onKey, true);
    modal.remove(); modal = null;
    const add = document.getElementById('plugins-connect');
    if (add) add.focus();
    schedule();
  }

  function onKey(e) { if (e.key === 'Escape' && modal && !modal.dataset.busy) { e.stopPropagation(); closeModal(); } }

  function shell(title) {
    const ov = el('div', 'modal-overlay pl-modal'); ov.id = 'plugins-register-modal';
    const card = el('div', 'modal-card pl-modal__card');
    card.setAttribute('role', 'dialog'); card.setAttribute('aria-modal', 'true'); card.setAttribute('aria-label', title);
    const h = el('div', 'modal-header');
    const close = el('button', 'modal-close'); close.type = 'button'; close.setAttribute('aria-label', 'Закрыть');
    close.append(svg(ICON_CLOSE, 18)); close.addEventListener('click', () => { if (!ov.dataset.busy) closeModal(); });
    h.append(el('h3', null, title), close);
    const body = el('div', 'modal-body'), foot = el('div', 'modal-footer');
    card.append(h, body, foot); ov.append(card);
    ov.addEventListener('mousedown', e => { if (e.target === ov && !ov.dataset.busy) closeModal(); });
    return { ov, body, foot };
  }

  function openModal() {
    if (modal) return;
    const creds = savedCreds();
    if (creds) showToken(creds, true); else showForm();
    document.addEventListener('keydown', onKey, true);
  }

  function mount(s) {
    if (modal) modal.replaceWith(s.ov); else document.body.append(s.ov);
    modal = s.ov;
  }

  function showForm() {
    clearTimeout(pollTimer); pollTimer = null;
    const s = shell('Подключить устройство');
    const platform = detectPlatform();
    const form = el('form', 'pl-form'); form.noValidate = true; form.id = 'pl-reg-form';
    const l1 = el('label', 'pl-field');
    const nameIn = el('input', 'pl-input'); nameIn.id = 'pl-dev-name'; nameIn.maxLength = 128; nameIn.required = true;
    nameIn.value = (PLATFORM_LABEL[platform] || 'My') + (MOBILE_PLATFORMS[platform] ? ' device' : ' desktop'); nameIn.autocomplete = 'off';
    l1.append(el('span', 'pl-field__label', 'Имя устройства'), nameIn);
    const l2 = el('label', 'pl-field');
    const sel = el('select', 'pl-input'); sel.id = 'pl-dev-platform';
    Object.keys(PLATFORM_LABEL).forEach(k => { const o = el('option', null, PLATFORM_LABEL[k]); o.value = k; o.selected = k === platform; sel.append(o); });
    l2.append(el('span', 'pl-field__label', 'Платформа'), sel);
    const err = el('div', 'pl-form__err'); err.hidden = true; err.setAttribute('role', 'alert');
    form.append(l1, l2, el('p', 'modal-hint', 'После регистрации появится токен. Его нужно вставить в поле Device token клиента Device Bridge на ' + (MOBILE_PLATFORMS[platform] ? 'этом устройстве.' : 'этом ПК.')), err);
    s.body.append(form);
    const cancel = el('button', 'btn btn-secondary', 'Отмена'); cancel.type = 'button'; cancel.addEventListener('click', closeModal);
    const ok = el('button', 'btn pl-btn-accent', 'Зарегистрировать'); ok.type = 'submit'; ok.setAttribute('form', 'pl-reg-form');
    s.foot.append(cancel, ok);
    form.addEventListener('submit', async e => {
      e.preventDefault();
      const name = nameIn.value.trim();
      if (!name || name.length > 128) { err.textContent = 'Имя должно содержать от 1 до 128 символов.'; err.hidden = false; nameIn.focus(); return; }
      if (s.ov.dataset.busy) return;
      s.ov.dataset.busy = '1'; ok.disabled = cancel.disabled = nameIn.disabled = sel.disabled = true;
      ok.textContent = 'Регистрация…'; err.hidden = true;
      try {
        const data = await api('/api/devices', { method: 'POST', body: JSON.stringify({ name, platform: sel.value }) });
        if (typeof data.device_id !== 'string' || !data.device_id || typeof data.token !== 'string' || !data.token)
          throw new Error('Неожиданный ответ сервера. Обновите список: устройство могло создаться.');
        storeCreds(data.device_id, data.token);
        delete s.ov.dataset.busy;
        showToken({ deviceId: data.device_id, token: data.token, name }, false);
        loadDevices(true);
      } catch (ex) {
        delete s.ov.dataset.busy;
        ok.disabled = cancel.disabled = nameIn.disabled = sel.disabled = false; ok.textContent = 'Зарегистрировать';
        err.textContent = ex.message; err.hidden = false;
      }
    });
    mount(s);
    nameIn.focus(); nameIn.select();
  }

  function showToken(creds, reopened) {
    const s = shell(reopened ? 'Токен устройства' : 'Устройство зарегистрировано');
    s.ov.dataset.deviceId = creds.deviceId;
    const known = dev.list.find(d => d.device_id === creds.deviceId);
    const intro = el('p', null, reopened
      ? 'В этой вкладке уже зарегистрировано устройство. Новое устройство не создаётся.'
      : 'Скопируйте токен и вставьте его в поле Device token клиента Device Bridge.');
    const idRow = el('div', 'pl-kv');
    const idv = el('span', 'pl-kv__v', ((creds.name || (known && known.name) || '') + ' · ' + creds.deviceId.slice(0, 8)).replace(/^ · /, ''));
    idv.title = creds.deviceId;
    idRow.append(el('span', 'pl-kv__k', 'Устройство'), idv);
    const tokWrap = el('div', 'pl-token');
    const tokLabel = el('label', 'pl-field__label', 'Токен'); tokLabel.htmlFor = 'pl-dev-token';
    // Masked state shows a fixed-length placeholder, never a clipped secret.
    const MASK = '\u2022'.repeat(24);
    const tok = el('input', 'pl-input pl-token__in'); tok.type = 'text'; tok.readOnly = true; tok.value = MASK; tok.dataset.masked = '1';
    const setMasked = m => { tok.value = m ? MASK : creds.token; tok.dataset.masked = m ? '1' : '0'; eye.textContent = m ? 'Показать' : 'Скрыть'; };
    tok.id = 'pl-dev-token'; tok.setAttribute('aria-label', 'Токен устройства'); tok.spellcheck = false; tok.autocomplete = 'off';
    tok.addEventListener('focus', () => { if (tok.dataset.masked === '0') tok.select(); });
    const eye = el('button', 'pl-btn', 'Показать'); eye.type = 'button';
    eye.addEventListener('click', () => setMasked(tok.dataset.masked === '0'));
    const copy = el('button', 'pl-btn pl-btn--primary'); copy.type = 'button'; copy.id = 'pl-copy-token';
    const copyLabel = el('span', null, 'Скопировать токен'); copy.append(svg(ICON_COPY, 14), copyLabel);
    const setIcon = p => copy.firstChild.replaceWith(svg(p, 14));
    copy.addEventListener('click', async () => {
      const ok = await copyText(creds.token);
      if (!ok) { setMasked(false); tok.focus(); tok.select(); }
      copyLabel.textContent = ok ? 'Скопировано' : 'Выделено, нажмите Ctrl+C';
      copy.classList.toggle('pl-btn--ok', ok); if (ok) setIcon('M5 12l5 5L20 7');
      setTimeout(() => { if (copy.isConnected) { copyLabel.textContent = 'Скопировать токен'; copy.classList.remove('pl-btn--ok'); setIcon(ICON_COPY); } }, 1800);
    });
    const tokBtns = el('div', 'pl-token__btns'); tokBtns.append(eye, copy);
    tokWrap.append(tokLabel, tok, tokBtns);
    const status = el('div', 'pl-wait'); status.id = 'pl-reg-status';
    const steps = el('ol', 'pl-steps'); steps.id = 'pl-reg-steps';
    ['Запустите клиент Device Bridge на ПК.', 'Вставьте токен в поле Device token и подключитесь.', 'Статус ниже сменится на «Подключено».']
      .forEach(t => steps.append(el('li', null, t)));
    s.body.append(intro, idRow, tokWrap, steps, status,
      el('p', 'modal-hint pl-modal__hint', 'Токен действует 30 дней и хранится только в этой вкладке. Не\u00a0отправляйте его в чат.'));
    if (reopened) {
      const again = el('button', 'btn btn-secondary', 'Новое устройство'); again.type = 'button';
      again.addEventListener('click', showForm);
      s.foot.append(again);
    }
    const done = el('button', 'btn btn-secondary', 'Готово'); done.type = 'button'; done.id = 'pl-reg-done'; done.addEventListener('click', closeModal);
    s.foot.append(done);
    mount(s);
    syncModalStatus();
    schedule();
    copy.focus();
  }

  function syncModalStatus() {
    const st = document.getElementById('pl-reg-status');
    if (!st || !modal) return;
    const d = dev.list.find(x => x.device_id === modal.dataset.deviceId);
    const on = !dev.error && !!d && d.status === 'online';
    st.dataset.state = dev.error ? 'error' : on ? 'on' : 'wait';
    st.replaceChildren(on ? svg('M5 12l5 5L20 7', 16) : el('span', 'pl-dot pl-dot--wait'),
      document.createTextNode(dev.error ? 'Не удалось проверить подключение. Повторяем…' : on ? 'Устройство подключено' : 'Ожидаем подключения клиента…'));
    const steps = document.getElementById('pl-reg-steps');
    if (steps) steps.hidden = on;
    const done = document.getElementById('pl-reg-done');
    if (done) done.className = on ? 'btn pl-btn-accent' : 'btn btn-secondary';
  }

  document.addEventListener('visibilitychange', () => {
    if (active()) loadDevices(true); else { clearTimeout(pollTimer); pollTimer = null; }
  });
  window.addEventListener('online', () => { if (active()) loadDevices(true); });
  window.addEventListener('focus', () => { if (active()) loadDevices(true); });
  const view = document.getElementById('view-plugins');
  if (view) new MutationObserver(() => {
    if (active()) schedule(); else { clearTimeout(pollTimer); pollTimer = null; }
  }).observe(view, { attributes: true, attributeFilter: ['class'] });

  window.PluginsPanel = {
    open() { if (!dev.loaded) render(); return loadDevices(dev.loaded); },
    reload() { return loadDevices(false); },
  };
})();
