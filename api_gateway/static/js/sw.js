/* sw.js — service worker Web Push (отдаётся гейтвеем с корня: GET /sw.js, scope "/").
 * push:              показать «AuGent ответил: <чат>» (payload от chats-consumer, app/push_sender.py)
 * notificationclick: открыть чат — фокус на уже открытой вкладке (postMessage push.js),
 *                    иначе новое окно /?push_chat=<id>
 * Решение «слать или нет» принимает сервер (ключ viewing:{uid}:{chat}); Chrome требует
 * показывать уведомление на каждый push, поэтому здесь фильтрации нет.
 */
'use strict';

self.addEventListener('install', () => self.skipWaiting());
self.addEventListener('activate', (e) => e.waitUntil(self.clients.claim()));

self.addEventListener('push', (event) => {
  let d = {};
  try { d = event.data ? event.data.json() : {}; } catch (_) { d = { body: event.data ? event.data.text() : '' }; }
  const title = d.title || 'AuGent ответил';
  const opts = {
    body: d.body || 'Ответ готов',
    tag: d.tag || (d.chat_id ? 'chat-' + d.chat_id : 'augent'),
    renotify: true,
    icon: '/static/img/icon.png',
    badge: '/static/img/icon_white.png',
    data: { chat_id: d.chat_id || null, url: d.url || '/' },
  };
  event.waitUntil(self.registration.showNotification(title, opts));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const data = event.notification.data || {};
  const chatId = data.chat_id ? String(data.chat_id) : '';
  const url = new URL(data.url || '/', self.location.origin).href;
  event.waitUntil((async () => {
    const list = await self.clients.matchAll({ type: 'window', includeUncontrolled: true });
    const same = list.filter((c) => new URL(c.url).origin === self.location.origin);
    const target = same.find((c) => c.focused) || same.find((c) => c.visibilityState === 'visible') || same[0];
    if (target) {
      try { await target.focus(); } catch (_) {}
      target.postMessage({ type: 'push:open-chat', chat_id: chatId });
      return;
    }
    await self.clients.openWindow(url);
  })());
});
