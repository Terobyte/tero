// relay/src/cron.js — cron раз в 5 мин: пульс старше 15 мин → один пуш.
// Отправка пуша — заглушка notify() (фазе 7 подставить настоящий push.js).

export const STALE_AFTER_MS = 15 * 60 * 1000;
const TERMINAL = new Set(['done', 'stopped']);

export async function checkStale(env, nowMs = Date.now(), notify = defaultNotify) {
  const rows = await env.DB.prepare(
    'SELECT run_id, machine_id, payload, updated_at FROM states',
  ).all();
  const fired = [];
  for (const row of rows.results || []) {
    let payload = {};
    try {
      payload = JSON.parse(row.payload);
    } catch {
      continue;
    }
    if (TERMINAL.has(payload.status)) continue;
    if (payload.stale_notified) continue;
    const updatedAt = Date.parse(row.updated_at || '');
    if (!Number.isFinite(updatedAt)) continue;
    if (nowMs - updatedAt < STALE_AFTER_MS) continue;
    // Сначала атомарный захват (CAS по старому payload), потом пуш: из двух
    // параллельных cron пуш отправит только тот, чей UPDATE изменил строку.
    // Свежий пульс tero меняет payload — захват не проходит, пульс не затирается.
    const claimed = JSON.stringify({ ...payload, stale_notified: true });
    const claim = await env.DB.prepare(
      'UPDATE states SET payload = ? WHERE run_id = ? AND machine_id = ? AND payload = ? AND updated_at = ?',
    ).bind(claimed, row.run_id, row.machine_id, row.payload, row.updated_at).run();
    if (!claim || !claim.meta || claim.meta.changes !== 1) continue;
    try {
      await notify(env, row.run_id, row.machine_id, payload);
    } catch {
      // Пуш не ушёл — снимаем отметку, следующий cron попробует снова.
      await env.DB.prepare(
        'UPDATE states SET payload = ? WHERE run_id = ? AND machine_id = ? AND payload = ?',
      ).bind(row.payload, row.run_id, row.machine_id, claimed).run();
      continue;
    }
    fired.push({ run_id: row.run_id, machine_id: row.machine_id });
  }
  return fired;
}

export async function defaultNotify(env, runId, machineId, payload) {
  // Фаза 7: настоящий web push. Здесь — отметка и возврат пульса.
  if (env && typeof env.__notified === 'function') {
    await env.__notified(runId, machineId, payload);
  }
}
