// Тесты багхантa 2026-11-12 (bugs.md): серверная часть. Должны быть красными, пока баги не починены.
import test from 'node:test';
import assert from 'node:assert/strict';
import { handleFetch } from '../src/worker.js';
import { openFakeDb } from './fake_d1.js';

const WRITE_KEY = 'write-key-bugs';

function req(method, path, { token = '', body = undefined } = {}) {
  const headers = { 'content-type': 'application/json', 'cf-connecting-ip': '1.2.3.4' };
  if (token) headers.authorization = `Bearer ${token}`;
  return new Request(`https://relay.test${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function pairDevice(env, previousToken = undefined) {
  const code = (await (await handleFetch(req('POST', '/pair/code', { token: WRITE_KEY, body: {} }), env)).json()).code;
  const body = previousToken ? { code, previous_token: previousToken } : { code };
  const res = await handleFetch(req('POST', '/pair/exchange', { body }), env);
  assert.equal(res.status, 200);
  return res.json();
}

test('B02: failed-прогон старше 7 дней не висит в /runs?all=1 (терминальный, как done/stopped)', async () => {
  const env = { DB: openFakeDb(), RELAY_WRITE_KEY: WRITE_KEY };
  const dev = await pairDevice(env);
  const post = await handleFetch(req('POST', '/state', {
    token: WRITE_KEY, body: { run_id: 'rf', machine_id: 'm1', status: 'failed' } }), env);
  assert.equal(post.status, 200);
  await env.DB.prepare('UPDATE states SET updated_at = ? WHERE run_id = ?')
    .bind(new Date(Date.now() - 8 * 86400000).toISOString(), 'rf').run();
  const all = await (await handleFetch(req('GET', '/runs?all=1', { token: dev.device_token }), env)).json();
  assert.deepEqual(all.runs.map((r) => r.run_id), [],
    'failed старше 7 дней — терминальный: ожидали пустой список, а он живёт в активных вечно');
  const plain = await (await handleFetch(req('GET', '/runs', { token: dev.device_token }), env)).json();
  assert.deepEqual(plain.runs.map((r) => r.run_id), [], 'failed не активный прогон');
});

test('B09: повторная привязка с previous_token отзывает старое устройство и переносит подписку новому, не 409', async () => {
  const env = { DB: openFakeDb(), RELAY_WRITE_KEY: WRITE_KEY };
  const sub = { endpoint: 'https://web.push.apple.com/abc123', p256dh: 'p'.repeat(87), auth: 'a'.repeat(22) };
  const first = await pairDevice(env);
  const r1 = await handleFetch(req('POST', '/push/subscribe', { token: first.device_token, body: sub }), env);
  assert.equal(r1.status, 200);
  // тот же телефон привязан новым кодом и предъявил старый токен
  const second = await pairDevice(env, first.device_token);
  assert.notEqual(second.device_id, first.device_id);
  const r2 = await handleFetch(req('POST', '/push/subscribe', { token: second.device_token, body: sub }), env);
  assert.equal(r2.status, 200, `ожидали 200 (подписка переходит новой привязке), получили ${r2.status} ${await r2.text()}`);
  // старый токен отозван
  const old = await handleFetch(req('GET', '/runs', { token: first.device_token }), env);
  assert.equal(old.status, 401, 'старый device token должен быть отозван');
  const fresh = await handleFetch(req('GET', '/runs', { token: second.device_token }), env);
  assert.equal(fresh.status, 200);
  // строка подписки принадлежит новому устройству (пуши идут ему)
  const row = await env.DB.prepare('SELECT device_id FROM push_subs WHERE endpoint = ?').bind(sub.endpoint).first();
  assert.equal(row.device_id, second.device_id);
});
