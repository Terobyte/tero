// relay/public/sw.js — service worker пульта: пуш → уведомление, тап → пульт.
// Формат пуша — текст, как был: старые и новые sw понимают одно и то же.
self.addEventListener('push', (event) => {
  const text = event.data ? event.data.text() : 'теро: нужен ответ';
  event.waitUntil(self.registration.showNotification('теро', { body: text.slice(0, 200) }));
});

// Тап: уже открытый пульт поднимается (он сам перечитает данные на visibilitychange
// и выберет ждущий прогон), иначе — новое окно в корне.
self.addEventListener('notificationclick', (event) => {
  event.notification.close();
  event.waitUntil((async () => {
    const wins = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    const mine = wins.find((c) => new URL(c.url).origin === self.location.origin && 'focus' in c);
    if (mine) return mine.focus();
    return clients.openWindow('/');
  })());
});
