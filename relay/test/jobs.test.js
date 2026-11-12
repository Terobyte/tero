import test from 'node:test';
import assert from 'node:assert/strict';
import { DatabaseSync } from 'node:sqlite';
import { readFileSync } from 'node:fs';
import { handleFetch, sha256Hex } from '../src/worker.js';
import { hasContact } from '../src/jobs.js';
import { openFakeDb } from './fake_d1.js';

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
  env.DB.prepare('CREATE TABLE jobs_pushed (card_id TEXT, at INTEGER)').run();
  const ins = (sql, ...args) => env.DB.prepare(sql).bind(...args).run();
  await ins("INSERT INTO jobs_decisions (id, card_id, version, kind, action, at) VALUES ('old', 'a', 'v', 'job', 'yes', ?)", NOW - 8 * DAY);
  await ins("INSERT INTO jobs_decisions (id, card_id, version, kind, action, at) VALUES ('new', 'b', 'v', 'job', 'yes', ?)", NOW - 6 * DAY);
  await ins("INSERT INTO jobs_pushed (card_id, at) VALUES ('old', ?)", NOW - 31 * DAY);
  await ins("INSERT INTO jobs_pushed (card_id, at) VALUES ('new', ?)", NOW - 29 * DAY);
  await ins("INSERT INTO push_log (run_id, reason, code, at) VALUES ('', 'jobs:old', 201, ?)", NOW - 31 * DAY);
  await ins("INSERT INTO push_log (run_id, reason, code, at) VALUES ('', 'jobs:new', 201, ?)", NOW - 29 * DAY);
  await ins("INSERT INTO push_log (run_id, reason, code, at) VALUES ('r', 'notify:x', 201, ?)", NOW - 31 * DAY);
  const worker = (await import('../src/worker.js')).default;
  await worker.scheduled({ scheduledTime: NOW }, env, undefined);
  const ids = (sql) => env.DB.prepare(sql).all().results.map((r) => Object.values(r)[0]);
  assert.deepEqual(ids('SELECT id FROM jobs_decisions'), ['new']);
  assert.deepEqual(ids('SELECT card_id FROM jobs_pushed'), ['new']);
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
  for (const n of ['jobs_meta', 'jobs_cards', 'jobs_decisions', 'idx_jobs_decisions_once', 'idx_jobs_decisions_engine']) {
    assert.ok(names.includes(n), n);
  }
  assert.equal(db.prepare("SELECT value FROM jobs_meta WHERE key = 'push_cap'").get().value, '4');
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
