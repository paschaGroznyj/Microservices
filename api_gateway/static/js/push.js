/* push.js — Web Push об ответах агента (только main, см. chat.html).
 *
 *  - переключатель «Уведомления об ответах» в профиле (#notify-mount);
 *    вкл: разрешение браузера -> service worker /sw.js -> pushManager.subscribe ->
 *         POST /api/push/subscribe, затем notify_enabled=true в /api/user/settings
 *         (гейтвей дебаунсит 1 с и кладёт в Redis-очередь -> chats_service);
 *  - heartbeat «вкладка на экране»: POST /api/push/viewing {chat_id, visible} раз в 15 с,
 *    сразу при смене чата и видимости. Пока ключ viewing:{uid}:{chat} жив (35 с),
 *    chats-consumer push по этому чату не шлёт;
 *  - клик по уведомлению: sw.js фокусирует вкладку и шлёт push:open-chat,
 *    либо открывает /?push_chat=<id> (здесь превращается в catchpm:restore_chat_id).
 */
(function () {
  'use strict';

  // Новое окно из уведомления: app.js (module, выполняется позже) откроет этот чат.
  try {
    const u = new URL(location.href);
    const pc = u.searchParams.get('push_chat');
    if (pc) {
      sessionStorage.setItem('catchpm:restore_chat_id', pc);
      u.searchParams.delete('push_chat');
      history.replaceState(history.state, '', u.pathname + u.search + u.hash);
    }
  } catch (_) { /* noop */ }

  const SUPPORTED = !!(window.isSecureContext && 'serviceWorker' in navigator &&
    'PushManager' in window && 'Notification' in window);
  const IS_IOS = /iPad|iPhone|iPod/.test(navigator.userAgent) ||
    (navigator.platform === 'MacIntel' && navigator.maxTouchPoints > 1);
  const HB_MS = 15000;

  const st = { enabled: false, busy: false, want: false, lastChat: null, lastVis: null, lastSent: 0 };
  let els = null;

  const ICON = '<svg class="pn-ic" viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.8" stroke-linecap="round" stroke-linejoin="round" aria-hidden="true"><path d="M6 8a6 6 0 0 1 12 0c0 7 3 9 3 9H3s3-2 3-9"/><path d="M10.3 21a1.94 1.94 0 0 0 3.4 0"/></svg>';

  function b64uToBytes(s) {
    const pad = '='.repeat((4 - (s.length % 4)) % 4);
    const raw = atob((s + pad).replace(/-/g, '+').replace(/_/g, '/'));
    const out = new Uint8Array(raw.length);
    for (let i = 0; i < raw.length; i++) out[i] = raw.charCodeAt(i);
    return out;
  }

  function sameKey(buf, bytes) {
    if (!buf) return false;
    const a = new Uint8Array(buf);
    if (a.length !== bytes.length) return false;
    for (let i = 0; i < a.length; i++) if (a[i] !== bytes[i]) return false;
    return true;
  }

  function post(path, body) {
    return fetch(path, {
      method: 'POST', credentials: 'include', keepalive: true,
      headers: { 'Content-Type': 'application/json' }, body: JSON.stringify(body),
    });
  }

  function hint(text, warn) {
    if (!els) return;
    els.hint.textContent = text || '';
    els.hint.classList.toggle('is-warn', !!warn);
  }

  function paint() {
    if (!els) return;
    const perm = SUPPORTED ? Notification.permission : 'unsupported';
    const blocked = !SUPPORTED || perm === 'denied';
    // пока идёт подписка, тумблер держит выбор пользователя, а не откатывается назад
    els.sw.checked = st.busy ? st.want : (st.enabled && perm === 'granted');
    els.sw.disabled = blocked || st.busy;
    els.row.classList.toggle('is-disabled', blocked);
    els.row.classList.toggle('is-busy', st.busy);
    if (!SUPPORTED) {
      hint(IS_IOS ? 'На iPhone и iPad уведомления работают, если добавить сайт на экран «Домой».'
                  : 'Этот браузер не поддерживает push-уведомления.', true);
    } else if (perm === 'denied') {
      hint('Уведомления запрещены в настройках браузера.', true);
    }
  }

  async function ensureSubscription() {
    const reg = await navigator.serviceWorker.register('/sw.js', { scope: '/' });
    await navigator.serviceWorker.ready;
    const r = await fetch('/api/push/vapid', { credentials: 'include' });
    if (!r.ok) throw new Error('сервер push недоступен (' + r.status + ')');
    const key = b64uToBytes((await r.json()).public_key || '');
    let sub = await reg.pushManager.getSubscription();
    if (sub && !sameKey(sub.options && sub.options.applicationServerKey, key)) {
      try { await sub.unsubscribe(); } catch (_) { /* noop */ }
      sub = null;
    }
    if (!sub) sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: key });
    const j = sub.toJSON();
    const res = await post('/api/push/subscribe', { endpoint: j.endpoint, keys: j.keys });
    if (!res.ok) throw new Error('подписка не сохранена (' + res.status + ')');
    return sub;
  }

  function saveSetting(on) {
    return post('/api/user/settings', { notify_enabled: !!on }).catch((e) => {
      console.warn('[push] save setting failed:', e);
    });
  }

  async function onToggle() {
    if (st.busy) { els.sw.checked = st.want; return; }  // подписка ещё идёт (FCM бывает медленным)
    const want = els.sw.checked;
    st.want = want; st.busy = true; paint(); hint('');
    try {
      if (want) {
        let perm = Notification.permission;
        if (perm !== 'granted') perm = await Notification.requestPermission();
        if (perm !== 'granted') {
          st.enabled = false;
          if (perm !== 'denied') hint('Разрешение не выдано: уведомления не включены.', true);
          return;
        }
        await ensureSubscription();
        st.enabled = true;
        await saveSetting(true);
        st.lastChat = null;
        beat(true);
      } else {
        st.enabled = false;
        await saveSetting(false);
      }
    } catch (e) {
      console.warn('[push] toggle failed:', e);
      st.enabled = false;
      hint('Не удалось включить: ' + (e && e.message ? e.message : e), true);
    } finally {
      st.busy = false; paint();
    }
  }

  // ── «вкладка на экране» ──
  function currentChat() {
    const a = window.__catchpmApp;
    return a && a.currentChat != null ? String(a.currentChat) : '';
  }

  function beat(force) {
    if (!st.enabled) return;
    const chat = currentChat();
    const vis = document.visibilityState === 'visible';
    const changed = chat !== st.lastChat || vis !== st.lastVis;
    if (!force && !changed && (!vis || Date.now() - st.lastSent < HB_MS)) return;
    const prev = st.lastChat && st.lastChat !== chat ? st.lastChat : '';
    st.lastChat = chat; st.lastVis = vis; st.lastSent = Date.now();
    if (!chat && !prev) return;
    const body = { chat_id: chat, visible: vis, prev_chat_id: prev };
    if (!vis && navigator.sendBeacon) {
      navigator.sendBeacon('/api/push/viewing', new Blob([JSON.stringify(body)], { type: 'application/json' }));
      return;
    }
    post('/api/push/viewing', body).catch(() => {});
  }

  function mount() {
    const host = document.getElementById('notify-mount');
    if (!host || els) return;
    host.innerHTML =
      '<label class="pn-row" for="pn-switch">' + ICON +
        '<span class="pn-txt"><span class="pn-title">Уведомления об ответах</span>' +
        '<span class="pn-sub">Когда вкладка с чатом не на экране</span></span>' +
        '<input type="checkbox" class="pn-switch" id="pn-switch" role="switch">' +
      '</label><div class="pn-hint" id="pn-hint" aria-live="polite"></div>';
    els = { row: host.querySelector('.pn-row'), sw: host.querySelector('#pn-switch'), hint: host.querySelector('#pn-hint') };
    els.sw.addEventListener('change', onToggle);
    paint();
  }

  async function init() {
    mount();
    if (!SUPPORTED) return;
    navigator.serviceWorker.addEventListener('message', (e) => {
      const d = e.data || {};
      if (d.type === 'push:open-chat' && d.chat_id) {
        const a = window.__catchpmApp;
        if (a && typeof a.openChat === 'function' && String(a.currentChat) !== String(d.chat_id)) a.openChat(d.chat_id);
      }
    });
    try {
      const r = await fetch('/api/user/settings', { credentials: 'include' });
      if (r.ok) st.enabled = !!(await r.json()).notify_enabled;
    } catch (_) { /* noop */ }
    paint();
    if (st.enabled && Notification.permission === 'granted') {
      // обновить подписку (endpoint мог смениться) — молча
      ensureSubscription().catch((e) => console.warn('[push] resubscribe failed:', e));
    }
    setInterval(() => beat(false), 1000);
    document.addEventListener('visibilitychange', () => beat(true));
    window.addEventListener('pagehide', () => {
      if (!st.enabled || !st.lastChat) return;
      st.lastVis = null;
      const body = { chat_id: st.lastChat, visible: false };
      if (navigator.sendBeacon) navigator.sendBeacon('/api/push/viewing', new Blob([JSON.stringify(body)], { type: 'application/json' }));
    });
    beat(true);
  }

  window.WebPush = { state: st, beat, ensureSubscription };
  if (document.readyState === 'loading') document.addEventListener('DOMContentLoaded', init);
  else init();
})();
