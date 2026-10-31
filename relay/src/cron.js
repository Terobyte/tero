// relay/src/cron.js — cron раз в 5 мин: пульс старше 15 мин → один пуш
// через настоящий web push (relay/src/push.js).

import { notify as sendPush } from './push.js';

export const STALE_AFTER_MS = 15 * 60 * 1000;

export async function checkStale(env, nowMs = Date.now(), notifyFn = defaultNotify) {
  // Терминальность — только по колонке status (SQL-фильтр выше); payload
  // читается для остальных полей уведомления, не для фильтрации.
  const rows = await env.DB.prepare(
    "SELECT run_id, machine_id, payload, updated_at FROM states WHERE status NOT IN ('done', 'stopped')",
  ).all();
  const fired = [];
  for (const row of rows.results || []) {
    let payload = {};
    try {
      payload = JSON.parse(row.payload);
    } catch {
      continue;
    }
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
      // F2: stale-метка в payload — текст пуша скажет про потерю связи.
      await notifyFn(env, row.run_id, row.machine_id, { ...payload, stale: true });
    } catch {
      // Пуш не ушёл — снимаем отметку, следующий cron попробует снова.
      await env.DB.prepare(
        'UPDATE states SET payload = ? WHERE run_id = ? AND machine_id = ? AND payload = ?',
      ).bind(row.payload, row.run_id, row.machine_id, claimed).run();
      continue;
    }
    fired.push({ run_id: row.run_id, machine_id: row.machine_id });
  }
  await purgeAnswers(env, nowMs);
  return fired;
}

export const ANSWER_TTL_MS = 7 * 24 * 60 * 60 * 1000;

// Ответы и сообщения терминальных прогонов старше 7 дней удаляются.
export async function purgeAnswers(env, nowMs = Date.now()) {
  const cutoff = new Date(nowMs - ANSWER_TTL_MS).toISOString();
  await env.DB.prepare(
    'DELETE FROM answers WHERE received_at < ? AND EXISTS (SELECT 1 FROM states s '
    + "WHERE s.run_id = answers.run_id AND s.machine_id = answers.machine_id "
    + "AND s.status IN ('done', 'stopped'))",
  ).bind(cutoff).run();
}

export async function defaultNotify(env, runId, machineId, payload) {
  // Web push по умолчанию: cron без подменённого notify шлёт настоящий пуш.
  await sendPush(env, runId, machineId, payload, Date.now());
  if (env && typeof env.__notified === 'function') {
    await env.__notified(runId, machineId, payload);
  }
}
