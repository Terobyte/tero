// relay/public/sw.js — service worker пульта: пуш → уведомление, тап → корень.
self.addEventListener('push', (event) => {
  const text = event.data ? event.data.text() : 'теро: нужен ответ';
  event.waitUntil(self.registration.showNotification('теро', { body: text.slice(0, 200) }));
});

self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil(clients.openWindow('/'));
});
