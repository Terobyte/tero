import test from 'node:test';
import assert from 'node:assert/strict';
import { handleFetch, sha256Hex } from '../src/worker.js';
import { openFakeDb } from './fake_d1.js';

const WRITE_KEY = 'write-key-1';
const DEVICE_TOKEN = 'device-token-1';
const READ_TOKEN = 'read-token-1';

async function setupEnv() {
  const DB = openFakeDb();
  const now = new Date().toISOString();
  for (const [token, kind] of [[DEVICE_TOKEN, 'device'], [READ_TOKEN, 'read']]) {
    await DB.prepare(
      'INSERT INTO devices (token_hash, kind, label, revoked, created_at) VALUES (?, ?, ?, 0, ?)',
    ).bind(await sha256Hex(token), kind, kind, now).run();
  }
  return { DB, RELAY_WRITE_KEY: WRITE_KEY };
}

function request(method, path, { token = '', body = undefined } = {}) {
  const headers = { 'content-type': 'application/json' };
  if (token) headers.authorization = `Bearer ${token}`;
  return new Request(`https://relay.test${path}`, {
    method,
    headers,
    body: body === undefined ? undefined : JSON.stringify(body),
  });
}

async function postState(env, extra = {}) {
  const res = await handleFetch(
    request('POST', '/state', {
      token: WRITE_KEY,
      body: {
        run_id: 'r1', machine_id: 'm1', status: 'waiting_human',
        question: { id: 'q1', text: 'идём дальше?', actions: ['more', 'skip'] },
        phone_text_answers: true,
        ...extra,
      },
    }),
    env,
  );
  assert.equal(res.status, 200);
}

test('без токена → 401', async () => {
  const env = await setupEnv();
  assert.equal((await handleFetch(request('GET', '/state?run_id=r1&machine_id=m1'), env)).status, 401);
  assert.equal(
    (await handleFetch(request('POST', '/state', { body: {} }), env)).status,
    401,
  );
  assert.equal(
    (await handleFetch(request('POST', '/answer', { body: {} }), env)).status,
    401,
  );
  assert.equal((await handleFetch(request('GET', '/runs'), env)).status, 401);
  assert.equal((await handleFetch(request('GET', '/answers'), env)).status, 401);
  assert.equal(
    (await handleFetch(request('POST', '/answers/ack', { body: { uuid: 'x' } }), env)).status,
    401,
  );
  assert.equal(
    (await handleFetch(request('POST', '/answers/ack', { body: { uuid: 'x' } }), env)).status,
    401,
  );
});

test('без run_id+machine_id ответ не принимается, обход text-флага закрыт', async () => {
  const env = await setupEnv();
  const res = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { question_id: 'ghost', action: 'anything', text: '' },
    }),
    env,
  );
  assert.equal(res.status, 422);
  const textOnly = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { question_id: 'ghost', action: 'anything', text: 'подсказка' },
    }),
    env,
  );
  assert.equal(textOnly.status, 422);
});

test('действие вне actions вопроса отвергается', async () => {
  const env = await setupEnv();
  await postState(env);
  const res = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', question_id: 'q1', action: 'hack', text: '' },
    }),
    env,
  );
  assert.equal(res.status, 422);
});

test('read-токен читает, но не может POST /answer', async () => {
  const env = await setupEnv();
  await postState(env);
  const get = await handleFetch(
    request('GET', '/state?run_id=r1&machine_id=m1', { token: READ_TOKEN }),
    env,
  );
  assert.equal(get.status, 200);
  const denied = await handleFetch(
    request('POST', '/answer', {
      token: READ_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', question_id: 'q1', action: 'more', text: '' },
    }),
    env,
  );
  assert.equal(denied.status, 403);
});

test('ответ длиннее 2000 → 413; uuid и время получения у каждого ответа; who human', async () => {
  const env = await setupEnv();
  await postState(env);
  const tooLong = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', question_id: 'q1', action: 'more', text: 'x'.repeat(2001) },
    }),
    env,
  );
  assert.equal(tooLong.status, 413);
  const exact = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', question_id: 'q1', action: 'skip', text: 'x'.repeat(2000) },
    }),
    env,
  );
  assert.equal(exact.status, 200);
  const listed = await (
    await handleFetch(request('GET', '/answers?question_id=q1', { token: WRITE_KEY }), env)
  ).json();
  assert.equal(listed.answers.length, 1);
  assert.ok(listed.answers[0].uuid);
  assert.ok(listed.answers[0].received_at);
  assert.equal(listed.answers[0].who, 'human');
});

test('текст при phone_text_answers: false → 422 здесь, не только на странице', async () => {
  const env = await setupEnv();
  await postState(env, { phone_text_answers: false });
  const textAns = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', question_id: 'q1', action: 'more', text: 'подсказка' },
    }),
    env,
  );
  assert.equal(textAns.status, 422);
  const button = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', question_id: 'q1', action: 'more', text: '' },
    }),
    env,
  );
  assert.equal(button.status, 200);
});

test('лишнее поле состояния отброшено; токен в находке вырезан', async () => {
  const env = await setupEnv();
  const token = 'sk-abcdefghij1234567890klmnopqrstuvwxyzABCD';
  await postState(env, {
    evil: 'drop me',
    findings: [{ title: 'red', path: 'src/a.py', test: 'test_a', text: `see ${token} here` }],
  });
  const get = await handleFetch(
    request('GET', '/state?run_id=r1&machine_id=m1', { token: DEVICE_TOKEN }),
    env,
  );
  const { state } = await get.json();
  assert.ok(!('evil' in state));
  assert.ok(!state.findings[0].text.includes(token));
});

test('GET /answers дважды отдаёт тот же ответ, после ack — нет', async () => {
  const env = await setupEnv();
  await postState(env);
  const posted = await (
    await handleFetch(
      request('POST', '/answer', {
        token: DEVICE_TOKEN,
        body: { run_id: 'r1', machine_id: 'm1', question_id: 'q1', action: 'more', text: '' },
      }),
      env,
    )
  ).json();
  const first = await (
    await handleFetch(request('GET', '/answers', { token: WRITE_KEY }), env)
  ).json();
  const second = await (
    await handleFetch(request('GET', '/answers', { token: WRITE_KEY }), env)
  ).json();
  assert.deepEqual(second, first);
  assert.equal(first.answers.length, 1);
  const ack = await handleFetch(
    request('POST', '/answers/ack', { token: WRITE_KEY, body: { uuid: posted.uuid } }),
    env,
  );
  assert.equal(ack.status, 200);
  const third = await (
    await handleFetch(request('GET', '/answers', { token: WRITE_KEY }), env)
  ).json();
  assert.equal(third.answers.length, 0);
});

test('F11: ответ привязан к run_id+machine_id, чужой прогон его не забирает', async () => {
  const env = await setupEnv();
  const stateFor = (run_id, machine_id) => request('POST', '/state', {
    token: WRITE_KEY,
    body: {
      run_id,
      machine_id,
      status: 'waiting_human',
      question: { id: '1-1', text: 'идём?', actions: ['skip'] },
      phone_text_answers: true,
    },
  });
  for (const [r, m] of [['r1', 'm1'], ['r2', 'm2']]) {
    assert.equal((await handleFetch(stateFor(r, m), env)).status, 200);
  }
  const posted = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', question_id: '1-1', action: 'skip', text: '' },
    }),
    env,
  );
  assert.equal(posted.status, 200);
  const all = await (
    await handleFetch(request('GET', '/answers', { token: WRITE_KEY }), env)
  ).json();
  assert.equal(all.answers.length, 1);
  assert.equal(all.answers[0].run_id, 'r1');
  assert.equal(all.answers[0].machine_id, 'm1');
  const other = await (
    await handleFetch(
      request('GET', '/answers?run_id=r2&machine_id=m2', { token: WRITE_KEY }),
      env,
    )
  ).json();
  assert.equal(other.answers.length, 0);
});

test('GET /runs — только активные прогоны', async () => {
  const env = await setupEnv();
  await postState(env);
  await handleFetch(
    request('POST', '/state', {
      token: WRITE_KEY,
      body: { run_id: 'r2', machine_id: 'm1', status: 'done' },
    }),
    env,
  );
  const { runs } = await (
    await handleFetch(request('GET', '/runs', { token: DEVICE_TOKEN }), env)
  ).json();
  assert.equal(runs.length, 1);
  assert.equal(runs[0].run_id, 'r1');
});

test('секретоподобный run_id/machine_id не хранится ключом D1 → 400', async () => {
  const env = await setupEnv();
  const res = await handleFetch(
    request('POST', '/state', { token: WRITE_KEY, body: { run_id: 'sk-run-secret', machine_id: 'm1', status: 'running' } }),
    env,
  );
  assert.equal(res.status, 400);
  const runs = await handleFetch(request('GET', '/runs', { token: DEVICE_TOKEN }), env);
  assert.deepEqual((await runs.json()).runs, []);
});
