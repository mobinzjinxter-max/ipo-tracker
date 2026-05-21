// IPO Tracker Service Worker — handles Web Push & offline cache

const CACHE = 'ipo-tracker-v1';
const APP_URL = '/ipo-tracker/';

// ── Install / Activate ────────────────────────────────────────────────────────
self.addEventListener('install',  () => self.skipWaiting());
self.addEventListener('activate', e  => e.waitUntil(clients.claim()));

// ── Push received ─────────────────────────────────────────────────────────────
self.addEventListener('push', function (event) {
  let payload = { title: '📊 IPO Tracker', body: 'New alert!', type: 'info' };
  try { payload = event.data.json(); } catch (_) {}

  const icon  = APP_URL + 'icon.png';
  const badge = APP_URL + 'icon.png';

  const options = {
    body:             payload.body,
    icon:             icon,
    badge:            badge,
    vibrate:          [300, 100, 300, 100, 600],   // distinctive pattern
    requireInteraction: true,                        // stays on screen until tapped
    tag:              payload.type,                  // collapses same-type alerts
    renotify:         true,
    data: {
      type:  payload.type,
      sound: true,
    },
  };

  event.waitUntil(
    self.registration.showNotification(payload.title, options)
  );
});

// ── Notification tapped ───────────────────────────────────────────────────────
self.addEventListener('notificationclick', function (event) {
  event.notification.close();

  event.waitUntil(
    clients.matchAll({ type: 'window', includeUncontrolled: true }).then(list => {
      // If app already open, focus it and tell it to play sound
      for (const client of list) {
        if (client.url.includes('ipo-tracker')) {
          client.postMessage({ type: 'PLAY_SOUND' });
          return client.focus();
        }
      }
      // Otherwise open the app — sound plays on load via sessionStorage flag
      return clients.openWindow(APP_URL + '?notify=1');
    })
  );
});
