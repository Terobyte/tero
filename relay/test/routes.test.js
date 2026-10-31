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
      'INSERT INTO devices (token_hash, kind, label, device_id, revoked, created_at) VALUES (?, ?, ?, ?, 0, ?)',
    ).bind(await sha256Hex(token), kind, kind, `${kind}-id`, now).run();
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

test('GET /runs?all=1 — терминальные за 7 дней со status, view.resume, updated_at', async () => {
  const env = await setupEnv();
  await postState(env);
  const post = (body) => handleFetch(
    request('POST', '/state', { token: WRITE_KEY, body: { machine_id: 'm1', ...body } }), env);
  await post({ run_id: 'r2', status: 'stopped', view: { resume: { mode: 'ok', why: '' } } });
  await post({ run_id: 'r3', status: 'done' });
  // старше 7 дней — в списке нет
  await env.DB.prepare('UPDATE states SET updated_at = ? WHERE run_id = ?')
    .bind(new Date(Date.now() - 8 * 86400000).toISOString(), 'r3').run();
  const plain = await (await handleFetch(request('GET', '/runs', { token: DEVICE_TOKEN }), env)).json();
  assert.deepEqual(plain.runs.map((r) => r.run_id), ['r1']);
  assert.equal(Object.hasOwn(plain.runs[0], 'view'), false);
  const all = await (await handleFetch(request('GET', '/runs?all=1', { token: DEVICE_TOKEN }), env)).json();
  const byId = Object.fromEntries(all.runs.map((r) => [r.run_id, r]));
  assert.deepEqual(Object.keys(byId).sort(), ['r1', 'r2']);
  assert.equal(byId.r2.status, 'stopped');
  assert.deepEqual(byId.r2.view.resume, { mode: 'ok', why: '' });
  assert.ok(byId.r2.updated_at);
  assert.equal((await handleFetch(request('GET', '/runs?all=1'), env)).status, 401);
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

test('запоздавший старый push (state_at раньше) не затирает done', async () => {
  const env = await setupEnv();
  const post = (status, stateAt) => handleFetch(request('POST', '/state', {
    token: WRITE_KEY,
    body: { run_id: 'r1', machine_id: 'm1', status, state_at: stateAt },
  }), env);
  assert.equal((await post('done', '2026-10-27T01:00:02+00:00')).status, 200);
  assert.equal((await post('running', '2026-10-27T01:00:01+00:00')).status, 200);
  const { state } = await (await handleFetch(
    request('GET', '/state?run_id=r1&machine_id=m1', { token: DEVICE_TOKEN }), env,
  )).json();
  assert.equal(state.status, 'done');
  assert.equal((await post('running', '2026-10-27T01:00:03+00:00')).status, 200);
  const again = await (await handleFetch(
    request('GET', '/state?run_id=r1&machine_id=m1', { token: DEVICE_TOKEN }), env,
  )).json();
  assert.equal(again.state.status, 'running');
});

test('F1: запоздавший вопрос после done пуш не шлёт', async () => {
  const env = await setupEnv();
  await env.DB.prepare(
    "INSERT INTO push_subs (endpoint, p256dh, auth, dead, device_id) VALUES (?, ?, ?, 0, 'device-id')",
  ).bind(
    'https://push.example/sub/1',
    'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
    'BTBZMqHH6r4Tts7J_aSIgg',
  ).run();
  const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  env.VAPID_PRIVATE_KEY = JSON.stringify(await crypto.subtle.exportKey('jwk', kp.privateKey));
  env.VAPID_SUBJECT = 'mailto:test@localhost';
  let pushes = 0;
  env.__fetch = async () => { pushes += 1; return { status: 201 }; };
  const post = (body) => handleFetch(request('POST', '/state', { token: WRITE_KEY, body }), env);
  assert.equal((await post({
    run_id: 'r9', machine_id: 'm9', status: 'done', state_at: '2026-10-27T01:00:02+00:00',
  })).status, 200);
  assert.equal(pushes, 1);
  assert.equal((await post({
    run_id: 'r9', machine_id: 'm9', status: 'waiting_human',
    question: { id: 'q-old', text: 'старый вопрос?', actions: ['more'] },
    state_at: '2026-10-27T01:00:01+00:00',
  })).status, 200);
  assert.equal(pushes, 1);
  const { state } = await (await handleFetch(
    request('GET', '/state?run_id=r9&machine_id=m9', { token: DEVICE_TOKEN }), env,
  )).json();
  assert.equal(state.status, 'done');
});

test('POST /notify: ключ записи, текст уходит пушем, секреты и код вырезаны', async () => {
  const env = await setupEnv();
  const sent = [];
  env.__fetch = async (url, init) => { sent.push({ url, init }); return { status: 201 }; };
  const { generateKeyPairSync } = await import('node:crypto');
  const { privateKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' });
  env.VAPID_PRIVATE_KEY = JSON.stringify(privateKey.export({ format: 'jwk' }));
  await env.DB.prepare(
    "INSERT INTO push_subs (endpoint, p256dh, auth, dead, device_id) VALUES (?, ?, ?, 0, 'device-id')",
  ).bind('https://web.push.apple.com/x', 'BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM', 'BTBZMqHH6r4Tts7J_aSIgg', ).run();
  assert.equal((await handleFetch(request('POST', '/notify', { body: { text: 'x' } }), env)).status, 401);
  assert.equal((await handleFetch(request('POST', '/notify', { token: DEVICE_TOKEN, body: { text: 'x' } }), env)).status, 401);
  assert.equal((await handleFetch(request('POST', '/notify', { token: WRITE_KEY, body: { text: '   ' } }), env)).status, 400);
  const res = await handleFetch(
    request('POST', '/notify', { token: WRITE_KEY, body: { text: 'агента нет, решу сам через 15 мин', run_id: 'r1' } }),
    env,
  );
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ok, true);
  assert.equal(body.sent, true);
  assert.equal(sent.length, 1);
  const log = await env.DB.prepare('SELECT reason, code FROM push_log').all();
  assert.equal(log.results.length, 1);
  assert.match(log.results[0].reason, /^notify:/);
  // та же фраза за 30 мин — кулдаун; другая фраза — отдельный пуш
  const again = await (await handleFetch(
    request('POST', '/notify', { token: WRITE_KEY, body: { text: 'агента нет, решу сам через 15 мин', run_id: 'r1' } }),
    env,
  )).json();
  assert.equal(again.skipped, 'cooldown');
  const other = await (await handleFetch(
    request('POST', '/notify', { token: WRITE_KEY, body: { text: 'другое', run_id: 'r1' } }),
    env,
  )).json();
  assert.equal(other.sent, true);
  assert.equal(sent.length, 2);
});

test('POST /notify: не больше шести разных уведомлений за 30 мин', async () => {
  const env = await setupEnv();
  env.__fetch = async () => ({ status: 201 });
  const { generateKeyPairSync } = await import('node:crypto');
  const { privateKey } = generateKeyPairSync('ec', { namedCurve: 'P-256' });
  env.VAPID_PRIVATE_KEY = JSON.stringify(privateKey.export({ format: 'jwk' }));
  await env.DB.prepare(
    "INSERT INTO push_subs (endpoint, p256dh, auth, dead, device_id) VALUES (?, ?, ?, 0, 'device-id')",
  ).bind('https://web.push.apple.com/x', 'BNcRdreALRFXTkOOUHK1EtK2wtaz5Ry4YfYCA_0QTpQtUbVlUls0VJXg7A8u-Ts1XbjhazAkj7I99e8QcYP7DkM', 'BTBZMqHH6r4Tts7J_aSIgg').run();
  const send = async (text) => (await handleFetch(
    request('POST', '/notify', { token: WRITE_KEY, body: { text, run_id: 'r1' } }), env,
  )).json();
  for (let i = 0; i < 6; i++) assert.equal((await send(`текст ${i}`)).sent, true);
  const seventh = await send('ещё один');
  assert.equal(seventh.sent, false);
  assert.equal(seventh.skipped, 'rate');
});

test('POST /notify без подписок — не ошибка', async () => {
  const env = await setupEnv();
  const res = await handleFetch(request('POST', '/notify', { token: WRITE_KEY, body: { text: 'привет' } }), env);
  assert.equal(res.status, 200);
  const body = await res.json();
  assert.equal(body.ok, true);
  assert.equal(body.sent, false);
  assert.equal(body.skipped, 'no-subscription');
});

test('agent проходит в состояние по вложенному белому списку, без pid', async () => {
  const env = await setupEnv();
  await postState(env, {
    agent: { host: 'claude', alive: true, on_duty: true, pid: 4242, pid_started_at: 1.5, lease_until: 2, extra: 'x' },
  });
  const res = await handleFetch(request('GET', '/state?run_id=r1&machine_id=m1', { token: READ_TOKEN }), env);
  const { state } = await res.json();
  assert.deepEqual(state.agent, { host: 'claude', alive: true, on_duty: true, pid_started_at: 1.5, lease_until: 2 });
});

test('kind: message без question_id → 200, виден в GET /answers с kind', async () => {
  const env = await setupEnv();
  await postState(env);
  const res = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', kind: 'message', text: 'привет' },
    }),
    env,
  );
  assert.equal(res.status, 200);
  const listed = await (
    await handleFetch(request('GET', '/answers?run_id=r1&machine_id=m1', { token: WRITE_KEY }), env)
  ).json();
  assert.equal(listed.answers.length, 1);
  assert.equal(listed.answers[0].kind, 'message');
  assert.equal(listed.answers[0].text, 'привет');
  const byQuestion = await (
    await handleFetch(
      request('GET', '/answers?run_id=r1&machine_id=m1&question_id=q1', { token: WRITE_KEY }),
      env,
    )
  ).json();
  assert.equal(byQuestion.answers.length, 0);
});

test('kind: wake без question_id → 200', async () => {
  const env = await setupEnv();
  await postState(env);
  const res = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', kind: 'wake' },
    }),
    env,
  );
  assert.equal(res.status, 200);
});

test('kind: message без токена PWA → 401, с read-токеном → 403', async () => {
  const env = await setupEnv();
  await postState(env);
  const body = { run_id: 'r1', machine_id: 'm1', kind: 'message', text: 'x' };
  assert.equal((await handleFetch(request('POST', '/answer', { body }), env)).status, 401);
  assert.equal((await handleFetch(request('POST', '/answer', { token: READ_TOKEN, body }), env)).status, 403);
});

test('kind: message длиннее 2000 символов → отказ', async () => {
  const env = await setupEnv();
  await postState(env);
  const res = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', kind: 'message', text: 'я'.repeat(2001) },
    }),
    env,
  );
  assert.equal(res.status, 413);
});

test('phone_text_answers: false отклоняет message и в воркере', async () => {
  const env = await setupEnv();
  await postState(env, { phone_text_answers: false });
  const res = await handleFetch(
    request('POST', '/answer', {
      token: DEVICE_TOKEN,
      body: { run_id: 'r1', machine_id: 'm1', kind: 'message', text: 'привет' },
    }),
    env,
  );
  assert.equal(res.status, 422);
});
