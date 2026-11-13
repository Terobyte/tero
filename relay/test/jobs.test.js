import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { handleFetch, sha256Hex } from '../src/worker.js';
import { hasContact } from '../src/jobs.js';
import { textFor } from '../src/push.js';
import { openFakeDb } from './fake_d1.js';

// RFC 8291 Appendix A — валидная пара ключей подписки для push.js encryptPayload.
const SUB_P256DH = 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4';
const SUB_AUTH = 'BTBZMqHH6r4Tts7J_aSIgg';

const WRITE_KEY = 'write-key-1';
const DEVICE = 'device-token-1';
const READ = 'read-token-1';
const REVOKED = 'revoked-token-1';
const NOW = 1_800_000_000_000;
const DAY = 86400000;

async function setupEnv() {
  const DB = openFakeDb();
  for (const [token, kind, revoked] of [[DEVICE, 'device', 0], [READ, 'read', 0], [REVOKED, 'device', 1]]) {
    await DB.prepare(
      'INSERT INTO devices (token_hash, kind, label, device_id, revoked, created_at) VALUES (?, ?, ?, ?, ?, ?)',
    ).bind(await sha256Hex(token), kind, kind, `${token}-id`, revoked, new Date().toISOString()).run();
  }
  return { DB, RELAY_WRITE_KEY: WRITE_KEY };
}

async function call(env, method, path, { token = '', body, now = NOW } = {}) {
  const headers = { 'content-type': 'application/json' };
  if (token) headers.authorization = `Bearer ${token}`;
  const res = await handleFetch(new Request(`https://relay.test${path}`, {
    method, headers, body: body === undefined ? undefined : JSON.stringify(body),
  }), env, now);
  return { status: res.status, body: await res.json() };
}

const card = (id, kind, version = 'v1', extra = {}) => ({ id, kind, version, hot: false, rank: 1, title: id, ...extra });
const putCards = (env, cards, drop = []) => call(env, 'POST', '/jobs/cards', { token: WRITE_KEY, body: { cards, drop } });
const decide = (env, card_id, version, action, data, token = DEVICE) => call(env, 'POST', '/jobs/decide', {
  token, body: data === undefined ? { card_id, version, action } : { card_id, version, action, data },
});
const decisions = async (env) => (await call(env, 'GET', '/jobs/decisions', { token: WRITE_KEY })).body.decisions;
const settings = (env, body) => call(env, 'POST', '/jobs/settings', { token: DEVICE, body });

async function setupPushEnv() {
  const env = await setupEnv();
  const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  env.VAPID_PRIVATE_KEY = JSON.stringify(await crypto.subtle.exportKey('jwk', kp.privateKey));
  env.VAPID_SUBJECT = 'mailto:test@localhost';
  env.__fetch = async () => ({ status: 201 });
  await env.DB.prepare(
    'INSERT INTO push_subs (endpoint, p256dh, auth, dead, device_id) VALUES (?, ?, ?, 0, ?)',
  ).bind('https://push.example/sub/1', SUB_P256DH, SUB_AUTH, `${DEVICE}-id`).run();
  return env;
}

const pushNotify = (env, body, now = NOW) => call(env, 'POST', '/jobs/notify', { token: WRITE_KEY, body, now });

test('cards need the write key; phone reads them with its device token', async () => {
  const env = await setupEnv();
  assert.equal((await call(env, 'POST', '/jobs/cards', { body: { cards: [] } })).status, 401);
  assert.equal((await call(env, 'POST', '/jobs/cards', { token: DEVICE, body: { cards: [] } })).status, 401);
  assert.equal((await putCards(env, [card('job:a', 'job', 'v1', { hot: true })])).status, 200);
  const res = await call(env, 'GET', '/jobs/cards', { token: DEVICE });
  assert.equal(res.status, 200);
  assert.deepEqual(res.body.cards.map((c) => c.id), ['job:a']);
  assert.equal(res.body.now, NOW);
  assert.equal(res.body.laptop_seen, NOW);
  assert.equal(res.body.mode, 'push');
  assert.equal(res.body.push_cap, 4);
  assert.equal((await call(env, 'GET', '/jobs/cards', { token: REVOKED })).status, 401);
  assert.equal((await call(env, 'GET', '/jobs/cards')).status, 401);
});

test('cards come ordered by hot, then rank', async () => {
  const env = await setupEnv();
  await putCards(env, [
    card('job:c', 'job', 'v1', { rank: 1 }),
    card('job:b', 'job', 'v1', { rank: 2, hot: true }),
    card('job:a', 'job', 'v1', { rank: 3, hot: true }),
  ]);
  const res = await call(env, 'GET', '/jobs/cards', { token: DEVICE });
  assert.deepEqual(res.body.cards.map((c) => c.id), ['job:b', 'job:a', 'job:c']);
  await putCards(env, [], ['job:b']);
  const after = await call(env, 'GET', '/jobs/cards', { token: DEVICE });
  assert.deepEqual(after.body.cards.map((c) => c.id), ['job:a', 'job:c']);
});

test('read token cannot decide', async () => {
  const env = await setupEnv();
  await putCards(env, [card('job:a', 'job'), card('status:s', 'status'), card('approve:x', 'approve')]);
  const res = await call(env, 'GET', '/jobs/cards', { token: READ });
  assert.deepEqual(res.body.cards.map((c) => c.id).sort(), ['job:a', 'status:s']);
  const denied = await decide(env, 'job:a', 'v1', 'yes', undefined, READ);
  assert.equal(denied.status, 403);
  assert.equal(denied.body.error, 'read-only token');
  const settings = await call(env, 'POST', '/jobs/settings', { token: READ, body: { mode: 'quiet' } });
  assert.equal(settings.status, 403);
  assert.deepEqual(await decisions(env), []);
});

test('ack deletes', async () => {
  const env = await setupEnv();
  await putCards(env, [card('job:a', 'job'), card('job:b', 'job')]);
  await decide(env, 'job:a', 'v1', 'yes');
  await decide(env, 'job:b', 'v1', 'engine', { engine: 'chrome' });
  const list = await decisions(env);
  assert.equal(list.length, 2);
  const ack = await call(env, 'POST', '/jobs/decisions/ack', { token: WRITE_KEY, body: { ids: list.map((d) => d.id) } });
  assert.equal(ack.status, 200);
  assert.equal(env.DB.prepare('SELECT COUNT(*) AS n FROM jobs_decisions').first().n, 0);
  assert.equal((await call(env, 'POST', '/jobs/decisions/ack', { token: DEVICE, body: { ids: [] } })).status, 401);
});

test('cron sweeps old job data', async () => {
  const env = await setupEnv();
  const ins = (sql, ...args) => env.DB.prepare(sql).bind(...args).run();
  await ins("INSERT INTO jobs_decisions (id, card_id, version, kind, action, at) VALUES ('old', 'a', 'v', 'job', 'yes', ?)", NOW - 8 * DAY);
  await ins("INSERT INTO jobs_decisions (id, card_id, version, kind, action, at) VALUES ('new', 'b', 'v', 'job', 'yes', ?)", NOW - 6 * DAY);
  await ins("INSERT INTO jobs_pushed (key, kind, day, at) VALUES ('old', 'job', 'x', ?)", NOW - 31 * DAY);
  await ins("INSERT INTO jobs_pushed (key, kind, day, at) VALUES ('new', 'job', 'x', ?)", NOW - 29 * DAY);
  await ins("INSERT INTO push_log (run_id, reason, code, at) VALUES ('', 'jobs:old', 201, ?)", NOW - 31 * DAY);
  await ins("INSERT INTO push_log (run_id, reason, code, at) VALUES ('', 'jobs:new', 201, ?)", NOW - 29 * DAY);
  await ins("INSERT INTO push_log (run_id, reason, code, at) VALUES ('r', 'notify:x', 201, ?)", NOW - 31 * DAY);
  const worker = (await import('../src/worker.js')).default;
  await worker.scheduled({ scheduledTime: NOW }, env, undefined);
  const ids = (sql) => env.DB.prepare(sql).all().results.map((r) => Object.values(r)[0]);
  assert.deepEqual(ids('SELECT id FROM jobs_decisions'), ['new']);
  assert.deepEqual(ids('SELECT key FROM jobs_pushed'), ['new']);
  assert.deepEqual(ids('SELECT reason FROM push_log ORDER BY reason'), ['jobs:new', 'notify:x']);
});

test('cron sweep works before jobs_pushed exists', async () => {
  const env = await setupEnv();
  const worker = (await import('../src/worker.js')).default;
  await worker.scheduled({ scheduledTime: NOW }, env, undefined);
});

test('decide accepts only the actions of the card kind', async () => {
  const env = await setupEnv();
  await putCards(env, [card('approve:x', 'approve')]);
  assert.equal((await decide(env, 'approve:x', 'v1', 'yes')).status, 400);
  assert.equal((await decide(env, 'approve:x', 'v1', 'send')).status, 200);
  assert.equal((await decide(env, 'nope', 'v1', 'send')).status, 404);
  await putCards(env, [card('job:a', 'job'), card('status:s', 'status'), card('ask:q', 'ask')]);
  assert.equal((await decide(env, 'status:s', 'v1', 'yes')).status, 400);
  assert.equal((await decide(env, 'ask:q', 'v1', 'bash')).status, 400);
});

test('stale version is refused', async () => {
  const env = await setupEnv();
  await putCards(env, [card('approve:x', 'approve', 'h1')]);
  assert.equal((await decide(env, 'approve:x', 'h1', 'send')).status, 200);
  await putCards(env, [card('approve:x', 'approve', 'h2')]);
  const stale = await decide(env, 'approve:x', 'h1', 'send');
  assert.equal(stale.status, 409);
  assert.equal(stale.body.error, 'stale');
  assert.deepEqual(await decisions(env), []);
  assert.equal((await decide(env, 'approve:x', 'h2', 'decline')).status, 200);
  assert.equal((await decisions(env)).length, 1);
});

test('same version rewrite keeps the decision', async () => {
  const env = await setupEnv();
  await putCards(env, [card('approve:x', 'approve', 'h1')]);
  await decide(env, 'approve:x', 'h1', 'send');
  await putCards(env, [card('approve:x', 'approve', 'h1', { title: 'other' })]);
  assert.equal((await decisions(env)).length, 1);
});

test('double send is one decision', async () => {
  const env = await setupEnv();
  await putCards(env, [card('approve:x', 'approve')]);
  assert.equal((await decide(env, 'approve:x', 'v1', 'send')).status, 200);
  assert.equal((await decide(env, 'approve:x', 'v1', 'send')).status, 200);
  assert.equal((await decisions(env)).length, 1);
});

test('yes then no is refused', async () => {
  const env = await setupEnv();
  await putCards(env, [card('job:x', 'job')]);
  assert.equal((await decide(env, 'job:x', 'v1', 'yes')).status, 200);
  const second = await decide(env, 'job:x', 'v1', 'no');
  assert.equal(second.status, 409);
  assert.deepEqual(second.body, { error: 'decided', action: 'yes' });
  const list = await decisions(env);
  assert.deepEqual(list.map((d) => d.action), ['yes']);
});

test('engine replaces the previous choice until yes or no', async () => {
  const env = await setupEnv();
  await putCards(env, [card('job:x', 'job')]);
  assert.equal((await decide(env, 'job:x', 'v1', 'engine', { engine: 'chrome' })).status, 200);
  assert.equal((await decide(env, 'job:x', 'v1', 'engine', { engine: 'browser-act' })).status, 200);
  let list = await decisions(env);
  assert.equal(list.length, 1);
  assert.deepEqual(list[0].data, { engine: 'browser-act' });
  assert.equal((await decide(env, 'job:x', 'v1', 'yes')).status, 200);
  assert.equal((await decisions(env)).length, 2);
  const late = await decide(env, 'job:x', 'v1', 'engine', { engine: 'chrome' });
  assert.equal(late.status, 409);
  assert.equal(late.body.error, 'decided');
  list = await decisions(env);
  assert.deepEqual(list.find((d) => d.action === 'engine').data, { engine: 'browser-act' });
});

test('engine data is checked', async () => {
  const env = await setupEnv();
  await putCards(env, [card('job:x', 'job'), card('ask:q', 'ask'), card('approve:x', 'approve')]);
  assert.equal((await decide(env, 'job:x', 'v1', 'engine', { engine: 'bash' })).status, 400);
  assert.equal((await decide(env, 'job:x', 'v1', 'engine', { engine: 'chrome', extra: 1 })).status, 400);
  assert.equal((await decide(env, 'job:x', 'v1', 'engine')).status, 400);
  assert.equal((await decide(env, 'ask:q', 'v1', 'answers', { q: { a: 1 } })).status, 400);
  assert.equal((await decide(env, 'approve:x', 'v1', 'send', { cmd: 'x' })).status, 400);
  assert.deepEqual(await decisions(env), []);
});

test('answers are capped, truncated and refused with contacts', async () => {
  const env = await setupEnv();
  await putCards(env, [card('ask:q', 'ask')]);
  const many = Object.fromEntries(Array.from({ length: 41 }, (_, i) => [`k${i}`, 'v']));
  assert.equal((await decide(env, 'ask:q', 'v1', 'answers', many)).status, 400);
  for (const value of ['me@example.com', 'call +1 (617) 555-0123', '617.555.0123', 'live at 12 Main Street']) {
    const res = await decide(env, 'ask:q', 'v1', 'answers', { why: value });
    assert.equal(res.status, 400, value);
    assert.deepEqual(res.body, { error: 'contact' });
  }
  assert.equal((await decide(env, 'ask:q', 'v1', 'answers', { why: 'x'.repeat(3000) })).status, 200);
  assert.equal((await decisions(env))[0].data.why.length, 2000);
  assert.equal(hasContact('5 years of Python'), false);
});

test('settings are validated', async () => {
  const env = await setupEnv();
  const post = (body) => call(env, 'POST', '/jobs/settings', { token: DEVICE, body });
  assert.equal((await post({ push_cap: 8 })).status, 400);
  assert.equal((await post({ push_cap: 0 })).status, 400);
  assert.equal((await post({ push_cap: '5' })).status, 400);
  assert.equal((await post({ mode: 'rm -rf' })).status, 400);
  assert.equal((await post({ mode: 'quiet', push_cap: 5 })).status, 200);
  const res = await call(env, 'GET', '/jobs/cards', { token: DEVICE });
  assert.equal(res.body.mode, 'quiet');
  assert.equal(res.body.push_cap, 5);
  const laptop = await call(env, 'GET', '/jobs/settings', { token: WRITE_KEY });
  assert.deepEqual(laptop.body, { mode: 'quiet', push_cap: 5 });
});

test('laptop_seen moves on every laptop call', async () => {
  const env = await setupEnv();
  const seen = async () => (await call(env, 'GET', '/jobs/cards', { token: DEVICE })).body.laptop_seen;
  assert.equal(await seen(), 0);
  await call(env, 'GET', '/jobs/decisions', { token: WRITE_KEY, now: NOW + 1 });
  assert.equal(await seen(), NOW + 1);
  await call(env, 'GET', '/jobs/settings', { token: WRITE_KEY, now: NOW + 2 });
  assert.equal(await seen(), NOW + 2);
  await call(env, 'POST', '/jobs/cards', { token: WRITE_KEY, now: NOW + 3, body: { cards: [], drop: [] } });
  assert.equal(await seen(), NOW + 3);
  assert.equal((await call(env, 'GET', '/jobs/decisions', { token: DEVICE })).status, 401);
});

test('unknown jobs path is 404', async () => {
  const env = await setupEnv();
  assert.equal((await call(env, 'GET', '/jobs/nope', { token: DEVICE })).status, 404);
});

test('migration applies on old schema', () => {
  const full = readFileSync(new URL('../schema.sql', import.meta.url), 'utf8');
  const old = full.slice(0, full.indexOf('-- План EMPLOYED 4'));
  assert.ok(old.length > 0 && !old.includes('jobs_cards'));
  const db = new DatabaseSync(':memory:');
  db.exec(old);
  const migration = readFileSync(new URL('../migrate-jobs.sql', import.meta.url), 'utf8');
  db.exec(migration);
  db.exec(migration);
  const names = db.prepare("SELECT name FROM sqlite_master WHERE name LIKE 'jobs_%' OR name LIKE 'idx_jobs_%'").all().map((r) => r.name);
  for (const n of ['jobs_meta', 'jobs_cards', 'jobs_decisions', 'idx_jobs_decisions_once', 'idx_jobs_decisions_engine', 'jobs_pushed', 'idx_jobs_pushed_kind_day']) {
    assert.ok(names.includes(n), n);
  }
  assert.equal(db.prepare("SELECT value FROM jobs_meta WHERE key = 'push_cap'").get().value, '4');
  db.prepare('INSERT INTO jobs_pushed (key, kind, day, at) VALUES (?, ?, ?, ?)').run('job:a', 'job', '2026-01-01', 1);
  assert.equal(db.prepare('SELECT kind FROM jobs_pushed WHERE key = ?').get('job:a').kind, 'job');
});

const storedCards = async (env, token = DEVICE) => (await call(env, 'GET', '/jobs/cards', { token })).body;

test('card with email in a text field is refused and nothing is stored', async () => {
  const env = await setupEnv();
  const res = await putCards(env, [card('job:a', 'job', 'v1', { why: 'write to me@example.com' })]);
  assert.equal(res.status, 400);
  assert.deepEqual(res.body, { error: 'contact' });
  const got = await storedCards(env);
  assert.deepEqual(got.cards, []);
  assert.equal(got.laptop_seen, 0);
});

test('status card with an address in a nested array is refused', async () => {
  const env = await setupEnv();
  const res = await putCards(env, [card('status:s', 'status', 'v1', { lines: [{ note: ['ok', 'lives at 12 Main St'] }] })]);
  assert.equal(res.status, 400);
  assert.deepEqual(res.body, { error: 'contact' });
  assert.deepEqual((await storedCards(env)).cards, []);
});

test('urls and ids with long digit runs are accepted', async () => {
  const env = await setupEnv();
  const res = await putCards(env, [card('job:12345678901234', 'job', 'v1', {
    url: 'https://boards.example.com/jobs/4567891234',
    form_url: 'https://boards.example.com/apply/4567891234567',
    filled_hash: '1234567890123456',
    title: 'Backend dev',
    salary: 150000,
  })]);
  assert.equal(res.status, 200);
  assert.equal((await storedCards(env)).cards.length, 1);
});

test('dirty second card refuses the whole batch', async () => {
  const env = await setupEnv();
  const res = await putCards(env, [
    card('job:a', 'job'),
    card('job:b', 'job', 'v1', { text: 'call 617 555 0123' }),
  ]);
  assert.equal(res.status, 400);
  assert.equal(JSON.stringify(res.body).includes('555'), false);
  assert.deepEqual((await storedCards(env)).cards, []);
});

test('ack with no ids does not touch the batch', async () => {
  const env = await setupEnv();
  env.DB.batch = () => { throw new Error('batch called'); };
  const res = await call(env, 'POST', '/jobs/decisions/ack', { token: WRITE_KEY, body: { ids: [] } });
  assert.equal(res.status, 200);
  assert.deepEqual(res.body, { ok: true });
});

test('exempt keys still refuse an email or an address', async () => {
  const env = await setupEnv();
  const a = await putCards(env, [card('person@example.com', 'job', 'v1', { title: 'Dev' })]);
  assert.equal(a.status, 400);
  assert.deepEqual(a.body, { error: 'contact' });
  const b = await putCards(env, [card('job:a', 'job', 'v1', { form_url: 'https://x.com/apply?ref=12 Main St' })]);
  assert.equal(b.status, 400);
  const c = await putCards(env, [card('job:a', 'job', 'v1', { filled_hash: 'me@example.com', url: 'a@b.co' })]);
  assert.equal(c.status, 400);
  assert.deepEqual((await storedCards(env)).cards, []);
});

test('a phone in the only card is refused too', async () => {
  const env = await setupEnv();
  const res = await putCards(env, [card('job:a', 'job', 'v1', { title: 'call 617-555-0123' })]);
  assert.equal(res.status, 400);
  assert.deepEqual(res.body, { error: 'contact' });
  assert.deepEqual((await storedCards(env)).cards, []);
});

test('contacts in field names are refused', async () => {
  const env = await setupEnv();
  for (const key of ['me@example.com', 'call 617-555-0123']) {
    const res = await putCards(env, [card('job:a', 'job', 'v1', { his: { [key]: 'ok' } })]);
    assert.equal(res.status, 400, key);
    assert.deepEqual(res.body, { error: 'contact' });
  }
  assert.deepEqual((await storedCards(env)).cards, []);
});

test('contacts in answer labels are refused', async () => {
  const env = await setupEnv();
  await putCards(env, [card('ask:q', 'ask')]);
  for (const key of ['me@example.com', 'call 617-555-0123']) {
    const res = await decide(env, 'ask:q', 'v1', 'answers', { [key]: 'ok' });
    assert.equal(res.status, 400, key);
    assert.deepEqual(res.body, { error: 'contact' });
  }
  assert.deepEqual(await decisions(env), []);
});

test('a phone sent as a number is refused, except under exempt keys', async () => {
  const env = await setupEnv();
  const bare = await putCards(env, [card('job:a', 'job', 'v1', { note: 6175550123 })]);
  assert.equal(bare.status, 400);
  assert.deepEqual(bare.body, { error: 'contact' });
  assert.deepEqual((await storedCards(env)).cards, []);
  // id должен быть строкой (validCard), поэтому числовое исключение проверяем на url
  const exempt = await putCards(env, [card('job:a', 'job', 'v1', { url: 6175550123, rank: 2.5 })]);
  assert.equal(exempt.status, 200);
});

test('phone exemption covers only plain values under exempt keys', async () => {
  const env = await setupEnv();
  for (const extra of [
    { url: { note: 6175550123 } },
    { form_url: ['call 617-555-0123'] },
    { filled_hash: { deep: { x: ['6175550123'] } } },
  ]) {
    const res = await putCards(env, [card('job:a', 'job', 'v1', extra)]);
    assert.equal(res.status, 400, JSON.stringify(extra));
    assert.deepEqual(res.body, { error: 'contact' });
  }
  assert.deepEqual((await storedCards(env)).cards, []);
  assert.equal((await putCards(env, [card('job:a', 'job', 'v1', { url: 'https://x/jobs/6175550123' })])).status, 200);
});

test('contact check covers every JSON type, depth and look-alike digits', async () => {
  const env = await setupEnv();
  const dirty = [
    { a: [[{ b: ['x', { c: 'me@example.com' }] }]] },
    { a: { b: { 'me@example.com': 1 } } },
    { a: ['x', 6175550123] },
    { note: '６１７－５５５－０１２３' },
    { note: 'me＠example.com' },
  ];
  for (const extra of dirty) {
    assert.equal((await putCards(env, [card('job:a', 'job', 'v1', extra)])).status, 400, JSON.stringify(extra));
  }
  const clean = { a: null, b: true, c: false, d: [1, 2.5, null], e: { f: 'ok' } };
  assert.equal((await putCards(env, [card('job:a', 'job', 'v1', clean)])).status, 200);
});

test('answers: nested values and look-alike digits are refused', async () => {
  const env = await setupEnv();
  await putCards(env, [card('ask:q', 'ask')]);
  for (const data of [
    { why: { nested: 'me@example.com' } },
    { why: ['6175550123'] },
    { why: 6175550123 },
    { why: '６１７５５５０１２３' },
  ]) {
    assert.ok([400].includes((await decide(env, 'ask:q', 'v1', 'answers', data)).status), JSON.stringify(data));
  }
  assert.deepEqual(await decisions(env), []);
});

// Путь лежит в payload, как у карточки ноута.
const hotJob = (id, extra = {}) => card(id, 'job', 'v1', { hot: true, payload: { path: 'qa' }, ...extra });

test('job push respects mode, cap and dup', async () => {
  const env = await setupPushEnv();
  await settings(env, { mode: 'quiet' });
  await putCards(env, [hotJob('job:a')]);
  assert.deepEqual((await pushNotify(env, { card_id: 'job:a', text: 'QA at X' })).body, { sent: false, why: 'quiet' });

  await settings(env, { mode: 'push', push_cap: 2 });
  await putCards(env, [hotJob('job:b'), hotJob('job:c')]);
  assert.equal((await pushNotify(env, { card_id: 'job:a', text: 'QA at X' })).body.sent, true);
  assert.equal((await pushNotify(env, { card_id: 'job:b', text: 't' })).body.sent, true);
  assert.deepEqual((await pushNotify(env, { card_id: 'job:c', text: 't' })).body, { sent: false, why: 'cap' });
  assert.deepEqual((await pushNotify(env, { card_id: 'job:a', text: 't' })).body, { sent: false, why: 'dup' });
  assert.deepEqual((await pushNotify(env, { card_id: 'job:nope', text: 't' })).body, { sent: false, why: 'no-card' });
  const logged = env.DB.prepare("SELECT reason FROM push_log WHERE run_id = 'jobs' AND code = 201 ORDER BY id").all().results.map((r) => r.reason);
  assert.deepEqual(logged, ['jobs:job:a', 'jobs:job:b']);
});

test('job push text is exact and capped at 120 chars', () => {
  assert.equal(textFor({ job: { card_id: 'job:a', text: 'QA at X' } }), 'работа: QA at X');
  const long = 'a'.repeat(200);
  assert.equal(textFor({ job: { card_id: 'job:a', text: long } }).length, 120);
  assert.equal(textFor({ job: { card_id: 'job:a', text: long } }), `работа: ${'a'.repeat(112)}`);
});

test('cap resets at Boston midnight', async () => {
  const env = await setupPushEnv();
  await settings(env, { push_cap: 2 });
  await putCards(env, [hotJob('job:a'), hotJob('job:b'), hotJob('job:c')]);
  // 2026-01-15 — зима, Бостон = UTC-5, без DST.
  const t1 = Date.UTC(2026, 0, 16, 4, 50, 0); // 23:50 Бостон, 15 января
  const t2 = Date.UTC(2026, 0, 16, 4, 55, 0); // 23:55 Бостон, тот же день
  const t3 = Date.UTC(2026, 0, 16, 5, 10, 0); // 00:10 Бостон, 16 января — новые сутки
  assert.equal((await pushNotify(env, { card_id: 'job:a', text: 't' }, t1)).body.sent, true);
  assert.equal((await pushNotify(env, { card_id: 'job:b', text: 't' }, t2)).body.sent, true);
  assert.equal((await pushNotify(env, { card_id: 'job:c', text: 't' }, t3)).body.sent, true);
});

test('captcha push ignores cap and quiet', async () => {
  const env = await setupPushEnv();
  await settings(env, { mode: 'quiet', push_cap: 1 });
  await putCards(env, [card('captcha:x', 'captcha', '1')]);
  assert.equal((await pushNotify(env, { card_id: 'captcha:x', text: 'капча' })).body.sent, true);
  assert.deepEqual((await pushNotify(env, { card_id: 'captcha:x', text: 'капча' })).body, { sent: false, why: 'dup' });
  await putCards(env, [card('captcha:x', 'captcha', '2')]);
  assert.equal((await pushNotify(env, { card_id: 'captcha:x', text: 'капча' })).body.sent, true);
  const logged = env.DB.prepare("SELECT reason FROM push_log WHERE run_id = 'jobs' AND code = 201 ORDER BY id").all().results.map((r) => r.reason);
  assert.deepEqual(logged, ['jobs:captcha:x#1', 'jobs:captcha:x#2']);
});

test('failed delivery gives the slot back', async () => {
  const env = await setupPushEnv();
  env.__fetch = async () => ({ status: 500 });
  await putCards(env, [hotJob('job:a')]);
  assert.deepEqual((await pushNotify(env, { card_id: 'job:a', text: 't' })).body, { sent: false, why: 'error' });
  const row = await env.DB.prepare('SELECT * FROM jobs_pushed WHERE key = ?').bind('job:a').first();
  assert.equal(row, null);
  env.__fetch = async () => ({ status: 201 });
  assert.equal((await pushNotify(env, { card_id: 'job:a', text: 't' })).body.sent, true);
});

test('only hot first-path jobs are pushed', async () => {
  const env = await setupPushEnv();
  await putCards(env, [
    card('job:cold', 'job', 'v1', { hot: false, payload: { path: 'qa' } }),
    card('job:other-path', 'job', 'v1', { hot: true, payload: { path: 'retail' } }),
    card('job:top-path', 'job', 'v1', { hot: true, path: 'qa' }),
  ]);
  assert.equal((await pushNotify(env, { card_id: 'job:cold', text: 't' })).status, 400, 'cold job stays in batch');
  assert.equal((await pushNotify(env, { card_id: 'job:other-path', text: 't' })).status, 400, 'out-of-path job stays in batch');
  assert.equal((await pushNotify(env, { card_id: 'job:top-path', text: 't' })).status, 400, 'path outside payload is not the contract');
  assert.deepEqual(env.DB.prepare('SELECT key FROM jobs_pushed').all().results, [], 'no slot reserved');
});

test('old pult notify unchanged', () => {
  assert.equal(textFor({ status: 'done' }), 'tero: прогон закончен — подробно — в пульте');
  assert.equal(textFor({ notify: 'hi' }), 'tero: hi');
});

// --- Карточки ровно в той форме, в какой их кладёт EMPLOYED (asdict(Card)): данные в payload. ---

const employedJob = (id, payload = {}) => ({
  id, kind: 'job', version: '1', hot: true, rank: 0,
  payload: { title: 'QA', company: 'Acme', location: 'Boston', salary: '', why: 'fits', path: 'qa',
    url: 'https://jobs.lever.co/acme/x', engine: 'chrome', ...payload },
});

test('employed-shaped hot job is pushed: path is read from the payload', async () => {
  const env = await setupPushEnv();
  assert.equal((await putCards(env, [employedJob('job:acme-qa')])).status, 200);
  assert.deepEqual((await pushNotify(env, { card_id: 'job:acme-qa', text: 'QA · Acme' })).body, { sent: true, why: 'ok' });
  await putCards(env, [employedJob('job:retail', { path: 'retail' })]);
  assert.equal((await pushNotify(env, { card_id: 'job:retail', text: 't' })).status, 400, 'out-of-path job stays in batch');
});

test('card version and kind are machine fields: a hash with a long digit run passes', async () => {
  const env = await setupEnv();
  const approve = { id: 'approve:acme-qa', kind: 'approve', version: `3a${'1234567890'}bcdef${'0'.repeat(47)}`, hot: false, rank: 0,
    payload: { title: 'QA', company: 'Acme', resume_html: '<h3>Skills</h3>', filled: [], his: {}, drafts: {} } };
  const ask = { id: 'ask:acme-qa', kind: 'ask', version: 'ab0123456789012cdef0123456789abcdef01234', hot: false, rank: 0,
    payload: { title: 'QA', company: 'Acme', items: [] } };
  assert.equal((await putCards(env, [approve, ask])).status, 200);
  // почта и адрес в версии всё равно не проходят
  const dirty = await putCards(env, [{ ...ask, version: 'me@example.com' }]);
  assert.deepEqual(dirty.body, { error: 'contact' });
});

test('status finished_at is a timestamp, not a phone; other numbers are still checked', async () => {
  const env = await setupEnv();
  const status = { id: 'status:acme-qa', kind: 'status', version: '1', hot: false, rank: 0,
    payload: { title: 'QA', company: 'Acme', url: 'https://x.example/job', stage: 'отправлено', finished_at: 1763051234.56 } };
  assert.equal((await putCards(env, [status])).status, 200);
  const dirty = await putCards(env, [{ ...status, payload: { ...status.payload, note: 1763051234 } }]);
  assert.deepEqual(dirty.body, { error: 'contact' });
});

test('job links keep their ids, a contact hidden in the query is refused', async () => {
  const env = await setupEnv();
  for (const url of [
    'https://boards.greenhouse.io/acme/jobs/4512345007',
    'https://job-boards.greenhouse.io/acme/jobs/7012345',
    'https://jobs.lever.co/acme/12345678-1234-4abc-9def-123456789012',
    'https://www.acme.com/careers?gh_jid=4512345007',
    'https://jobs.example.com/x?id=a1b2c3d4-5678-9012-3456-7890abcdef12',
  ]) {
    assert.equal((await putCards(env, [employedJob('job:a', { url })])).status, 200, url);
  }
  for (const url of [
    'https://x.example/apply?phone=617-555-0123',
    'https://x.example/apply?phone=%2B1%20617%20555%200123',
    'https://x.example/apply#tel=(617) 555-0123',
    'https://x.example/apply?ref=me%40example.com',
  ]) {
    const res = await putCards(env, [employedJob('job:a', { url })]);
    assert.deepEqual(res.body, { error: 'contact' }, url);
  }
});

test('read token does not see the prepared answers on a hot card', async () => {
  const env = await setupEnv();
  await putCards(env, [employedJob('job:a', { prepared: { known: [{ label: 'Sponsorship?', value: 'No' }], ask: [] } })]);
  const read = (await storedCards(env, READ)).cards;
  assert.equal(read.length, 1);
  assert.equal(read[0].payload.title, 'QA');
  assert.equal('prepared' in read[0].payload, false);
  const device = (await storedCards(env, DEVICE)).cards;
  assert.deepEqual(device[0].payload.prepared.known, [{ label: 'Sponsorship?', value: 'No' }]);
});
