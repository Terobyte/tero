import test from 'node:test';
import assert from 'node:assert/strict';
import { handleFetch, sha256Hex } from '../src/worker.js';
import { openFakeDb } from './fake_d1.js';

const WRITE_KEY = 'write-key-1';
const DEVICE = 'device-token-1';
const NOW = 1_800_000_000_000;

async function setupEnv() {
  const DB = openFakeDb();
  await DB.prepare(
    'INSERT INTO devices (token_hash, kind, label, device_id, revoked, created_at) VALUES (?, ?, ?, ?, ?, ?)',
  ).bind(await sha256Hex(DEVICE), 'device', 'device', `${DEVICE}-id`, 0, new Date().toISOString()).run();
  return { DB, RELAY_WRITE_KEY: WRITE_KEY };
}

async function raw(env, method, path, token, bodyText) {
  const res = await handleFetch(new Request(`https://relay.test${path}`, {
    method, headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` }, body: bodyText,
  }), env, NOW);
  return { status: res.status, body: await res.json() };
}

async function seedAsk(env) {
  const body = JSON.stringify({ cards: [{ id: 'ask:q', kind: 'ask', version: 'v1', hot: false, rank: 1, title: 'q' }], drop: [] });
  assert.equal((await raw(env, 'POST', '/jobs/cards', WRITE_KEY, body)).status, 200);
}

const decideRaw = (env, dataJson) => raw(env, 'POST', '/jobs/decide', DEVICE,
  `{"card_id":"ask:q","version":"v1","action":"answers","data":${dataJson}}`);

test('B08: answer labeled __proto__ is kept as an own key', async () => {
  const env = await setupEnv();
  await seedAsk(env);
  const res = await decideRaw(env, '{"__proto__":"x"}');
  assert.equal(res.status, 200);
  const got = (await raw(env, 'GET', '/jobs/decisions', WRITE_KEY)).body.decisions;
  assert.equal(got.length, 1);
  assert.ok(Object.hasOwn(got[0].data, '__proto__'), 'data lost own key __proto__');
  assert.equal(got[0].data['__proto__'], 'x');
});

test('B09: 2001-char answer is rejected with 400, not silently clipped', async () => {
  const env = await setupEnv();
  await seedAsk(env);
  const res = await decideRaw(env, JSON.stringify({ q: 'a'.repeat(2001) }));
  assert.equal(res.status, 400, `got ${res.status} ${JSON.stringify(res.body)}`);
});

test('B09 sanity: 2000-char answer is accepted intact', async () => {
  const env = await setupEnv();
  await seedAsk(env);
  const res = await decideRaw(env, JSON.stringify({ q: 'a'.repeat(2000) }));
  assert.equal(res.status, 200);
  const got = (await raw(env, 'GET', '/jobs/decisions', WRITE_KEY)).body.decisions;
  assert.equal(got[0].data.q.length, 2000);
});
