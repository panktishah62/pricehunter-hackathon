/* Zwig service worker — PUSH ONLY.
 * Intentionally has NO fetch handler / no precaching, so it can never serve a
 * stale SPA or break the live site. It only handles Web Push display + clicks.
 */

self.addEventListener('install', () => {
  self.skipWaiting();
});

self.addEventListener('activate', (event) => {
  event.waitUntil(self.clients.claim());
});

self.addEventListener('push', (event) => {
  let payload = {};
  try {
    payload = event.data ? event.data.json() : {};
  } catch (e) {
    payload = { title: 'Zwig', body: event.data ? event.data.text() : '' };
  }

  const title = payload.title || 'Zwig';
  const options = {
    body: payload.body || '',
    icon: '/favicon-192.png',
    badge: '/favicon-192.png',
    tag: payload.tag || undefined,
    renotify: Boolean(payload.tag),
    data: { url: payload.url || '/#/app', ...(payload.data || {}) },
  };
  event.waitUntil(self.registration.showNotification(title, options));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  const targetUrl =
    (event.notification.data && event.notification.data.url) || '/#/app';
  event.waitUntil(
    self.clients
      .matchAll({ type: 'window', includeUncontrolled: true })
      .then((clientList) => {
        const targetAbs = new URL(targetUrl, self.location.origin).href;
        // Prefer a window already on the target — just focus it, don't redirect.
        for (const client of clientList) {
          if (client.url === targetAbs && 'focus' in client) {
            return client.focus();
          }
        }
        // Otherwise navigate an existing app window to the target.
        for (const client of clientList) {
          if ('focus' in client && 'navigate' in client) {
            return client
              .navigate(targetAbs)
              .then((c) => (c || client).focus())
              .catch(() => client.focus());
          }
        }
        // No window open — open a fresh one.
        if (self.clients.openWindow) {
          return self.clients.openWindow(targetAbs);
        }
        return undefined;
      }),
  );
});
