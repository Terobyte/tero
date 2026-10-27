import test from 'node:test';
import assert from 'node:assert/strict';
import { openFakeDb } from './fake_d1.js';

test('fake_d1 поднимает schema.sql без ошибок', async () => {
  const db = openFakeDb();
  const tables = await db
    .prepare("SELECT name FROM sqlite_master WHERE type = 'table' ORDER BY name")
    .all();
  const names = tables.results.map((r) => r.name);
  for (const want of ['states', 'devices', 'answers']) {
    assert.ok(names.includes(want), `нет таблицы ${want}: ${names}`);
  }
});

test('states/devices/answers читаются и пишутся', async () => {
  const db = openFakeDb();
  const now = '2026-10-26T00:00:00.000Z';
  await db
    .prepare('INSERT INTO states (run_id, machine_id, payload, updated_at) VALUES (?, ?, ?, ?)')
    .bind('r1', 'm1', '{"status":"running"}', now)
    .run();
  const row = await db
    .prepare('SELECT payload FROM states WHERE run_id = ? AND machine_id = ?')
    .bind('r1', 'm1')
    .first();
  assert.equal(row.payload, '{"status":"running"}');

  await db
    .prepare(
      'INSERT INTO devices (token_hash, kind, label, revoked, created_at) VALUES (?, ?, ?, 0, ?)',
    )
    .bind('abc123', 'device', 'iphone', now)
    .run();
  const dev = await db
    .prepare('SELECT kind FROM devices WHERE token_hash = ? AND revoked = 0')
    .bind('abc123')
    .first();
  assert.equal(dev.kind, 'device');

  await db
    .prepare(
      'INSERT INTO answers (uuid, question_id, action, text, who, received_at) VALUES (?, ?, ?, ?, ?, ?)',
    )
    .bind('u1', 'q1', 'more', '', 'human', now)
    .run();
  const ans = await db.prepare('SELECT * FROM answers WHERE uuid = ?').bind('u1').first();
  assert.equal(ans.who, 'human');

  const batched = await db.batch([
    db.prepare('SELECT COUNT(*) AS n FROM states'),
    db.prepare('SELECT COUNT(*) AS n FROM answers'),
  ]);
  assert.equal(batched.length, 2);
});

test('batch: WITH … SELECT отдаёт строки, а не meta записи', async () => {
  const db = openFakeDb();
  const [res] = await db.batch([db.prepare('WITH x AS (SELECT 7 AS n) SELECT n FROM x')]);
  assert.deepEqual(res.results.map((r) => ({ ...r })), [{ n: 7 }]);
});
