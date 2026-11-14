import test from 'node:test';
import assert from 'node:assert/strict';
import { handleFetch, sha256Hex } from '../src/worker.js';
import { safeMarks } from '../public/jobs.js';
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

async function call(env, method, path, token, body) {
  const res = await handleFetch(new Request(`https://relay.test${path}`, {
    method, headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
    body: body === undefined ? undefined : JSON.stringify(body),
  }), env, NOW);
  return { status: res.status, body: await res.json() };
}

test('b33_safeMarks_keeps_laptop_resume_tags_and_entities', () => {
  const input = '<h3>Skills</h3><p><b>Dev</b> a &amp; b <mark>new</mark></p><ul><li>x</li></ul>';
  const out = safeMarks(input);
  assert.equal(out, input, `expected resume_html unchanged, got: ${out}`);
});

test('guard_b33_safeMarks_still_neutralises_script_and_attributes', () => {
  const out = safeMarks('<script>alert(1)</script><img src=x onerror=alert(1)><p onclick="x">t</p>');
  assert.ok(!/<script/i.test(out), out);
  assert.ok(!/<img/i.test(out), out);
  assert.ok(!/<p[^>]*onclick/i.test(out), out);
});

test('b34_engine_choice_change_after_ack_stays_listed', async () => {
  const env = await setupEnv();
  const cards = { cards: [{ id: 'job:e', kind: 'job', version: 'v1', hot: false, rank: 1, title: 'e' }], drop: [] };
  assert.equal((await call(env, 'POST', '/jobs/cards', WRITE_KEY, cards)).status, 200);
  const dec = (engine) => call(env, 'POST', '/jobs/decide', DEVICE,
    { card_id: 'job:e', version: 'v1', action: 'engine', data: { engine } });
  assert.equal((await dec('chrome')).status, 200);
  const first = (await call(env, 'GET', '/jobs/decisions', WRITE_KEY)).body.decisions;
  assert.equal(first.length, 1);
  assert.equal(first[0].data.engine, 'chrome');
  const X = first[0].id;
  assert.equal((await dec('browser-act')).status, 200);
  assert.equal((await call(env, 'POST', '/jobs/decisions/ack', WRITE_KEY, { ids: [X] })).status, 200);
  const after = (await call(env, 'GET', '/jobs/decisions', WRITE_KEY)).body.decisions;
  assert.deepEqual(after.map((d) => d.data), [{ engine: 'browser-act' }],
    `engine B decision lost after acking id ${X}: ${JSON.stringify(after)}`);
});
