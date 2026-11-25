/* Раздел «Плагины»: подключение устройств по кнопке и JS-скрипты из
   /api/plugins/snippets. Код скриптов вставляется через textContent и
   страницей не исполняется. Токен устройства живёт только в памяти и
   sessionStorage этой вкладки (те же ключи, что у консольных скриптов). */
(function () {
  'use strict';
  let loaded = false, loading = null;

  const SS_ID = 'augent.device_id', SS_TOKEN = 'augent.device_token';
  const shared = () => window.__auGentDeviceBridge || (window.__auGentDeviceBridge = {});
  const dev = { list: [], loaded: false, busy: false, showOffline: false, pings: {}, error: '' };
  let modal = null, pollTimer = null;

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
    return r.json();
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
    const p = (navigator.userAgentData && navigator.userAgentData.platform) || navigator.platform || '';
    return /win/i.test(p) ? 'windows' : /mac/i.test(p) ? 'macos' : 'linux';
  }
  const PLATFORM_LABEL = { windows: 'Windows', macos: 'macOS', linux: 'Linux' };

  /* ── Devices card ──────────────────────────────────────────── */
  function root() { return document.getElementById('plugins-devices'); }

  async function loadDevices(quiet) {
    if (dev.busy) return;
    dev.busy = true;
    if (!quiet) render();
    try {
      const data = await api('/api/devices');
      dev.list = (data.devices || []).filter(d => d.status !== 'revoked');
      dev.error = ''; dev.loaded = true;
    } catch (e) {
      dev.error = e.message; dev.loaded = true;
    } finally {
      dev.busy = false; render(); syncModalStatus();
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
    const pb = el('button', 'pl-btn', busy ? 'Пинг…' : 'Пинг'); pb.type = 'button';
    pb.disabled = !online || busy;
    pb.title = online ? 'Проверить связь с устройством' : 'Устройство офлайн';
    pb.addEventListener('click', () => ping(d));
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
      el('p', 'pl-card__desc', 'Подключите ПК к агенту: зарегистрируйте устройство и вставьте токен в клиент Device Bridge.'));
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
    else if (dev.error) body.append(el('div', 'pl-state pl-state--err', 'Не удалось загрузить устройства. ' + dev.error));
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
    box.replaceChildren(head, body);
  }

  /* ── Registration modal ────────────────────────────────────── */
  function closeModal() {
    if (!modal) return;
    clearInterval(pollTimer); pollTimer = null;
    document.removeEventListener('keydown', onKey, true);
    modal.remove(); modal = null;
    const add = document.getElementById('plugins-connect');
    if (add) add.focus();
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
    clearInterval(pollTimer); pollTimer = null;
    const s = shell('Подключить устройство');
    const platform = detectPlatform();
    const form = el('form', 'pl-form'); form.noValidate = true; form.id = 'pl-reg-form';
    const l1 = el('label', 'pl-field');
    const nameIn = el('input', 'pl-input'); nameIn.id = 'pl-dev-name'; nameIn.maxLength = 128; nameIn.required = true;
    nameIn.value = (PLATFORM_LABEL[platform] || 'My') + ' desktop'; nameIn.autocomplete = 'off';
    l1.append(el('span', 'pl-field__label', 'Имя устройства'), nameIn);
    const l2 = el('label', 'pl-field');
    const sel = el('select', 'pl-input'); sel.id = 'pl-dev-platform';
    Object.keys(PLATFORM_LABEL).forEach(k => { const o = el('option', null, PLATFORM_LABEL[k]); o.value = k; o.selected = k === platform; sel.append(o); });
    l2.append(el('span', 'pl-field__label', 'Платформа'), sel);
    const err = el('div', 'pl-form__err'); err.hidden = true; err.setAttribute('role', 'alert');
    form.append(l1, l2, el('p', 'modal-hint', 'После регистрации появится токен. Его нужно вставить в поле Device token клиента Device Bridge на этом ПК.'), err);
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
    clearInterval(pollTimer);
    const started = Date.now();
    pollTimer = setInterval(() => {
      const st = document.getElementById('pl-reg-status');
      if (!st || st.dataset.state === 'on' || Date.now() - started > 600000) { clearInterval(pollTimer); pollTimer = null; return; }
      loadDevices(true);
    }, 4000);
    copy.focus();
  }

  function syncModalStatus() {
    const st = document.getElementById('pl-reg-status');
    if (!st || !modal) return;
    const d = dev.list.find(x => x.device_id === modal.dataset.deviceId);
    const on = !!d && d.status === 'online';
    st.dataset.state = on ? 'on' : 'wait';
    st.replaceChildren(on ? svg('M5 12l5 5L20 7', 16) : el('span', 'pl-dot pl-dot--wait'),
      document.createTextNode(on ? 'Устройство подключено' : 'Ожидаем подключения клиента…'));
    const steps = document.getElementById('pl-reg-steps');
    if (steps) steps.hidden = on;
    const done = document.getElementById('pl-reg-done');
    if (done) done.className = on ? 'btn pl-btn-accent' : 'btn btn-secondary';
  }

  /* ── Snippets ──────────────────────────────────────────────── */
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
    open() {
      if (root()) { if (!dev.loaded) render(); loadDevices(dev.loaded); }
      if (!loaded && !loading) loading = load().finally(() => { loading = null; });
    },
    reload() { loaded = false; dev.loaded = false; return this.open(); },
  };
})();
