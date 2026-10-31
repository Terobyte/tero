import test from 'node:test';
import assert from 'node:assert/strict';
import { checkStale } from '../src/cron.js';
import { openFakeDb } from './fake_d1.js';

function stateRow(runId, updatedAt, payload) {
  return {
    run_id: runId, machine_id: 'm1', payload: JSON.stringify(payload), updated_at: updatedAt,
  };
}

async function setupEnv(rows) {
  const DB = openFakeDb();
  for (const row of rows) {
    await DB.prepare(
      'INSERT INTO states (run_id, machine_id, payload, updated_at, status) VALUES (?, ?, ?, ?, ?)',
    ).bind(row.run_id, row.machine_id, row.payload, row.updated_at,
      String(JSON.parse(row.payload).status ?? '')).run();
  }
  return { DB };
}

const NOW = Date.now();
const iso = (msAgo) => new Date(NOW - msAgo).toISOString();

test('пульс 16 мин назад → один notify и отметка stale_notified', async () => {
  const env = await setupEnv([stateRow('r1', iso(16 * 60 * 1000), { status: 'waiting_human' })]);
  let calls = 0;
  const fired = await checkStale(env, NOW, async () => { calls += 1; });
  assert.equal(calls, 1);
  assert.equal(fired.length, 1);
  const row = await env.DB.prepare(
    'SELECT payload FROM states WHERE run_id = ? AND machine_id = ?',
  ).bind('r1', 'm1').first();
  assert.ok(JSON.parse(row.payload).stale_notified);
});

test('второй cron без нового пульса → тишина', async () => {
  const env = await setupEnv([stateRow('r1', iso(16 * 60 * 1000), {
    status: 'waiting_human', stale_notified: true,
  })]);
  let calls = 0;
  const fired = await checkStale(env, NOW, async () => { calls += 1; });
  assert.equal(calls, 0);
  assert.equal(fired.length, 0);
});

test('F15 красный: свежий push между чтением и UPDATE не затирается', async () => {
  const staleTs = iso(16 * 60 * 1000);
  const env = await setupEnv([stateRow('r1', staleTs, { status: 'waiting_human' })]);
  let calls = 0;
  const slowNotify = async () => {
    calls += 1;
    // Конкурентный свежий пульс tero, пока cron ждёт отправки пуша.
    await env.DB.prepare(
      'UPDATE states SET payload = ?, updated_at = ? WHERE run_id = ? AND machine_id = ?',
    ).bind(JSON.stringify({ status: 'running' }), new Date(NOW).toISOString(), 'r1', 'm1').run();
  };
  const fired = await checkStale(env, NOW, slowNotify);
  assert.equal(calls, 1);
  const row = await env.DB.prepare(
    'SELECT payload, updated_at FROM states WHERE run_id = ? AND machine_id = ?',
  ).bind('r1', 'm1').first();
  const payload = JSON.parse(row.payload);
  // Свежий push tero cron не затирает: статус running и его updated_at целы,
  // stale_notified ставится только когда версия строки не изменилась.
  assert.equal(payload.status, 'running');
  assert.equal(row.updated_at, new Date(NOW).toISOString());
  assert.equal(payload.stale_notified, undefined);
  assert.equal(fired.length, 1);
});

test('два параллельных cron по одной stale-строке → один notify', async () => {
  const env = await setupEnv([stateRow('r1', iso(16 * 60 * 1000), { status: 'waiting_human' })]);
  let calls = 0;
  const notify = async () => { calls += 1; await new Promise((r) => setTimeout(r, 10)); };
  const [a, b] = await Promise.all([checkStale(env, NOW, notify), checkStale(env, NOW, notify)]);
  assert.equal(calls, 1);
  assert.equal(a.length + b.length, 1);
});

test('notify упал → отметка снята, следующий cron пробует снова', async () => {
  const env = await setupEnv([stateRow('r1', iso(16 * 60 * 1000), { status: 'waiting_human' })]);
  const failed = await checkStale(env, NOW, async () => { throw new Error('net'); });
  assert.equal(failed.length, 0);
  let calls = 0;
  await checkStale(env, NOW, async () => { calls += 1; });
  assert.equal(calls, 1);
});

test('свежий пульс и done/stopped — без пуша', async () => {
  const env = await setupEnv([
    stateRow('fresh', iso(60 * 1000), { status: 'waiting_human' }),
    stateRow('old-done', iso(60 * 60 * 1000), { status: 'done' }),
    stateRow('old-stopped', iso(60 * 60 * 1000), { status: 'stopped' }),
  ]);
  let calls = 0;
  const fired = await checkStale(env, NOW, async () => { calls += 1; });
  assert.equal(calls, 0);
  assert.equal(fired.length, 0);
});

test('stale-уведомление помечено stale: true (текст — про потерю связи, а не вопрос)', async () => {
  const env = await setupEnv([stateRow('r1', iso(16 * 60 * 1000), {
    status: 'waiting_human', question: { id: 'q1', text: 'идём?' },
  })]);
  let got = null;
  await checkStale(env, NOW, async (e, r, m, payload) => { got = payload; });
  assert.equal(got && got.stale, true);
  assert.equal(got.question.id, 'q1');
});

test('purgeAnswers: терминальный прогон старше 7 дней чистится, живой — нет', async () => {
  const { openFakeDb } = await import('./fake_d1.js');
  const { purgeAnswers } = await import('../src/cron.js');
  const DB = openFakeDb();
  const now = Date.now();
  const old = new Date(now - 8 * 86400000).toISOString();
  for (const [rid, status] of [['done1', 'done'], ['live1', 'running']]) {
    await DB.prepare('INSERT INTO states (run_id, machine_id, payload, updated_at, status) VALUES (?, ?, ?, ?, ?)')
      .bind(rid, 'm', JSON.stringify({ status }), old, status).run();
    await DB.prepare(
      'INSERT INTO answers (uuid, run_id, machine_id, question_id, action, who, received_at, kind) VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
    ).bind(`u-${rid}`, rid, 'm', '', 'message', 'human', old, 'message').run();
  }
  await purgeAnswers({ DB }, now);
  const left = (await DB.prepare('SELECT uuid FROM answers').all()).results.map((r) => r.uuid);
  assert.deepEqual(left, ['u-live1']);
});

test('F12: терминальность — по колонке status, не по payload', async () => {
  const DB = openFakeDb();
  const ins = 'INSERT INTO states (run_id, machine_id, payload, updated_at, status) VALUES (?, ?, ?, ?, ?)';
  await DB.prepare(ins).bind('live', 'm1', JSON.stringify({ status: 'done' }),
    iso(16 * 60 * 1000), 'running').run();
  await DB.prepare(ins).bind('fin', 'm1', JSON.stringify({ status: 'running' }),
    iso(16 * 60 * 1000), 'stopped').run();
  const seen = [];
  await checkStale({ DB }, NOW, async (_env, runId) => { seen.push(runId); });
  assert.deepEqual(seen, ['live']);
});
