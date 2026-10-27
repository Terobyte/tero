import test from 'node:test';
import assert from 'node:assert/strict';
import { handleFetch } from '../src/worker.js';
import { openFakeDb } from './fake_d1.js';
import { PAIR_ALPHABET, PAIR_TTL_MS, exchangePairCode, generatePairCode } from '../src/pair.js';

const WRITE_KEY = 'write-key-pair';

function req(method, path, { token = '', body = undefined, ip = '1.2.3.4' } = {}) {
  const headers = { 'content-type': 'application/json', 'cf-connecting-ip': ip };
  if (token) headers.authorization = `Bearer ${token}`;
  return new Request(`https://relay.test${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function envWithWriteKey() {
  const DB = openFakeDb();
  return { DB, RELAY_WRITE_KEY: WRITE_KEY };
}

async function makeCode(env, label = '') {
  const res = await handleFetch(
    req('POST', '/pair/code', { token: WRITE_KEY, body: { label } }),
    env,
  );
  assert.equal(res.status, 200);
  return (await res.json()).code;
}

async function exchange(env, code, ip = '1.2.3.4', label) {
  const body = label === undefined ? { code } : { code, label };
  return handleFetch(req('POST', '/pair/exchange', { body, ip }), env);
}

async function deviceCount(env) {
  const row = await env.DB.prepare('SELECT COUNT(*) AS n FROM devices').first();
  return Number(row.n);
}

test('алфавит без похожих букв, длина 8, TTL 15 мин', () => {
  for (const bad of ['I', 'L', 'O', '0', '1']) {
    assert.ok(!PAIR_ALPHABET.includes(bad), `alphabet must not contain ${bad}`);
  }
  for (let i = 0; i < 20; i++) {
    const code = generatePairCode();
    assert.equal(code.length, 8);
    for (const ch of code) assert.ok(PAIR_ALPHABET.includes(ch));
  }
  assert.equal(PAIR_TTL_MS, 15 * 60 * 1000);
});

test('обмен: токены устройства и чтения; второй обмен того же кода → 410', async () => {
  const env = await envWithWriteKey();
  const code = await makeCode(env, 'iphone');
  const first = await exchange(env, code);
  assert.equal(first.status, 200);
  const body = await first.json();
  assert.ok(body.device_token);
  assert.ok(body.read_token);
  assert.ok(body.device_id);
  assert.notEqual(body.device_token, body.read_token);
  const second = await exchange(env, code);
  assert.equal(second.status, 410);
});

test('код живёт 15 мин: после TTL обмен → 410 (подменные часы)', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 1_000_000 });
  try {
    const env = await envWithWriteKey();
    const code = await makeCode(env);
    t.mock.timers.tick(14 * 60 * 1000);
    const fresh = await makeCode(env);
    assert.equal((await exchange(env, fresh)).status, 200);
    t.mock.timers.tick(2 * 60 * 1000); // первому коду уже 16 мин
    assert.equal((await exchange(env, code)).status, 410);
  } finally {
    t.mock.timers.reset();
  }
});

test('граница TTL: ровно 15 мин → 410', async (t) => {
  t.mock.timers.enable({ apis: ['Date'], now: 2_000_000 });
  try {
    const env = await envWithWriteKey();
    const code = await makeCode(env);
    t.mock.timers.tick(PAIR_TTL_MS); // ровно 15 мин
    assert.equal((await exchange(env, code)).status, 410);
  } finally {
    t.mock.timers.reset();
  }
});

test('параллельный обмен одного кода: один 200, второй 410, одна пара токенов', async () => {
  const env = await envWithWriteKey();
  const code = await makeCode(env, 'iphone');
  const [a, b] = await Promise.all([exchange(env, code), exchange(env, code)]);
  const statuses = [a.status, b.status].sort();
  assert.deepEqual(statuses, [200, 410]);
  assert.equal(await deviceCount(env), 2);
});

test('6-й неверный код с одного IP → 429', async () => {
  const env = await envWithWriteKey();
  let status = 0;
  for (let i = 0; i < 6; i++) {
    status = (await exchange(env, 'ZZZZZZZZ', '9.9.9.9')).status;
  }
  assert.equal(status, 429);
});

test('параллельные неверные коды: не больше 5 ответов 404, лишний → 429', async () => {
  const env = await envWithWriteKey();
  const results = await Promise.all(
    Array.from({ length: 6 }, () => exchange(env, 'ZZZZZZZZ', '7.7.7.7')),
  );
  const statuses = results.map((r) => r.status);
  const bad = statuses.filter((s) => s === 404).length;
  const limited = statuses.filter((s) => s === 429).length;
  assert.ok(bad <= 5, `expected at most five 404, got ${bad}`);
  assert.ok(limited >= 1, `expected at least one 429, got ${statuses}`);
});

test('--revoke одного устройства не трогает другое; отозванный → 401 unpaired', async () => {
  const env = await envWithWriteKey();
  const first = await (await exchange(env, await makeCode(env, 'iphone'))).json();
  const second = await (await exchange(env, await makeCode(env, 'ipad'))).json();
  const runs = (token) => handleFetch(req('GET', '/runs', { token }), env);
  assert.equal((await runs(first.device_token)).status, 200);
  const revoked = await handleFetch(
    req('POST', '/pair/revoke', { token: WRITE_KEY, body: { device_id: first.device_id } }),
    env,
  );
  assert.equal(revoked.status, 200);
  assert.equal((await revoked.json()).revoked, 2); // device- и read-токены iphone
  const gone = await runs(first.device_token);
  assert.equal(gone.status, 401);
  assert.deepEqual(await gone.json(), { error: 'unpaired' });
  // read-токен той же пары тоже отозван
  const goneRead = await runs(first.read_token);
  assert.equal(goneRead.status, 401);
  assert.equal((await runs(second.device_token)).status, 200);
});

test('revoke точечный при дублирующемся label', async () => {
  const env = await envWithWriteKey();
  const first = await (await exchange(env, await makeCode(env, 'dup'))).json();
  const second = await (await exchange(env, await makeCode(env, 'dup'))).json();
  const runs = (token) => handleFetch(req('GET', '/runs', { token }), env);
  const revoked = await handleFetch(
    req('POST', '/pair/revoke', { token: WRITE_KEY, body: { device_id: first.device_id } }),
    env,
  );
  assert.equal(revoked.status, 200);
  assert.equal((await revoked.json()).revoked, 2);
  assert.equal((await runs(first.device_token)).status, 401);
  assert.equal((await runs(second.device_token)).status, 200);
  assert.equal((await runs(second.read_token)).status, 200);
});

test('отказ второго INSERT: частичных записей нет, код допускает повтор', async () => {
  const env = await envWithWriteKey();
  const code = await makeCode(env, 'iphone');
  const realPrepare = env.DB.prepare.bind(env.DB);
  let deviceInserts = 0;
  env.DB.prepare = (sql) => {
    if (typeof sql === 'string' && sql.startsWith('INSERT INTO devices')) {
      deviceInserts += 1;
      if (deviceInserts === 2) {
        return { bind: () => ({ run: () => { throw new Error('boom-read-insert'); } }) };
      }
    }
    return realPrepare(sql);
  };
  await assert.rejects(() => exchangePairCode(env.DB, code));
  env.DB.prepare = realPrepare;
  assert.equal(await deviceCount(env), 0);
  const retry = await exchange(env, code);
  assert.equal(retry.status, 200);
});

test('отказ второго INSERT и компенсирующего DELETE: активных частичных записей нет, код допускает повтор', async () => {
  const env = await envWithWriteKey();
  const code = await makeCode(env, 'iphone');
  const realPrepare = env.DB.prepare.bind(env.DB);
  let deviceInserts = 0;
  env.DB.prepare = (sql) => {
    if (typeof sql === 'string' && sql.startsWith('INSERT INTO devices')) {
      deviceInserts += 1;
      if (deviceInserts === 2) {
        return { bind: () => ({ run: () => { throw new Error('boom-read-insert'); } }) };
      }
    }
    if (typeof sql === 'string' && sql.startsWith('DELETE FROM devices')) {
      return { bind: () => ({ run: () => { throw new Error('boom-cleanup'); } }) };
    }
    return realPrepare(sql);
  };
  await assert.rejects(() => exchangePairCode(env.DB, code));
  env.DB.prepare = realPrepare;
  const rows = (await env.DB.prepare(
    'SELECT kind, revoked FROM devices',
  ).all()).results;
  assert.deepEqual(rows, [], `expected no device rows, got ${JSON.stringify(rows)}`);
  const row = await env.DB.prepare('SELECT used FROM pair_codes WHERE code = ?').bind(code).first();
  assert.equal(row.used, 0, 'code must allow a retry after rollback');
  const retry = await exchange(env, code);
  assert.equal(retry.status, 200);
});

test('revoke точечный при пустом label', async () => {
  const env = await envWithWriteKey();
  const first = await (await exchange(env, await makeCode(env))).json();
  const second = await (await exchange(env, await makeCode(env))).json();
  const runs = (token) => handleFetch(req('GET', '/runs', { token }), env);
  const revoked = await handleFetch(
    req('POST', '/pair/revoke', { token: WRITE_KEY, body: { device_id: second.device_id } }),
    env,
  );
  assert.equal(revoked.status, 200);
  assert.equal((await runs(second.device_token)).status, 401);
  assert.equal((await runs(first.device_token)).status, 200);
});
