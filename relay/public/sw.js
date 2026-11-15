// relay/public/sw.js — service worker пульта: пуш → уведомление, тап → пульт.
// Формат пуша — текст, как был: старые и новые sw понимают одно и то же.
self.addEventListener('push', (event) => {
  const text = event.data ? event.data.text() : 'теро: нужен ответ';
  event.waitUntil(self.registration.showNotification('теро', { body: text.slice(0, 200) }));
});

// Тап: уже открытый пульт поднимается (он сам перечитает данные на visibilitychange
// и выберет ждущий прогон), иначе — новое окно в корне. Пуш вакансии («работа: ...»)
// ведёт на страницу карточек /jobs.html, а не на пульт: открытый пульт на / не считается.
self.addEventListener('notificationclick', (event) => {
  const body = (event.notification && event.notification.body) || '';
  const isJob = body.startsWith('работа:');
  const path = isJob ? '/jobs.html' : '/';
  event.notification.close();
  event.waitUntil((async () => {
    const wins = await clients.matchAll({ type: 'window', includeUncontrolled: true });
    const mine = wins.find((c) => {
      const u = new URL(c.url);
      if (u.origin !== self.location.origin || !('focus' in c)) return false;
      // Пуш вакансии ведёт только на уже открытую /jobs.html; прочие уведомления
      // поднимают любое своё окно, как раньше (пульт мог быть открыт на /?run_id=...).
      return isJob ? u.pathname === path : true;
    });
    if (mine) return mine.focus();
    if (isJob) return clients.openWindow('/jobs.html');
    return clients.openWindow('/');
  })());
});
