// relay/test/push.test.js — фаза 08-7: web push (VAPID JWT ES256 + aes128gcm RFC 8291).
import test from 'node:test';
import assert from 'node:assert/strict';
import { encryptPayload, createVapidJwt, notify, textFor, pushOk } from '../src/push.js';
import { openFakeDb as openRawDb } from './fake_d1.js';

// B17: подписки принадлежат неотозванному устройству dev1.
function openFakeDb() {
  const DB = openRawDb();
  DB._raw.prepare(
    "INSERT INTO devices (token_hash, kind, label, device_id, revoked, created_at) VALUES ('h', 'device', 'd', 'dev1', 0, '')",
  ).run();
  return DB;
}

// RFC 8291 §5 + Appendix A: фиксированный вектор.
const RFC8291_A = {
  plaintext: 'When I grow up, I want to be a watermelon',
  receiverPublic: 'BCVxsr7N_eNgVRqvHtD0zTZsEc6-VV-JvLexhqUzORcxaOzi6-AYWXvTBHm4bjyPjs7Vd8pZGH6SRpkNtoIAiw4',
  authSecret: 'BTBZMqHH6r4Tts7J_aSIgg',
  senderPrivate: 'yfWPiYE-n46HLnH0KqZOF1fJJU3MYrct3AELtAQ-oRw',
  senderPublic: 'BP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27mlmlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A8',
  salt: 'DGv6ra1nlYgDCS1FRnbzlw',
  expectedBody: 'DGv6ra1nlYgDCS1FRnbzlwAAEABBBP4z9KsN6nGRTbVYI_c7VJSPQTBtkgcy27ml'
    + 'mlMoZIIgDll6e3vCYLocInmYWAmS6TlzAC8wEqKK6PBru3jl7A_yl95bQpu6cVPT'
    + 'pK4Mqgkf1CXztLVBSt2Ks3oZwbuwXPXLWyouBWLVWGNWQexSgSxsj_Qulcy4a-fN',
};

test('шифрование с ключами и salt из RFC 8291 Appendix A — байт в байт тело из RFC', async () => {
  const body = await encryptPayload(RFC8291_A.plaintext, {
    receiverPublic: RFC8291_A.receiverPublic,
    authSecret: RFC8291_A.authSecret,
    senderPrivate: RFC8291_A.senderPrivate,
    senderPublic: RFC8291_A.senderPublic,
    salt: RFC8291_A.salt,
  });
  assert.equal(Buffer.from(body).toString('base64url'), RFC8291_A.expectedBody);
});

test('JWT — 64-байтная подпись r‖s, проверяется публичным ключом через subtle.verify', async () => {
  const { jwt, publicKey } = await createVapidJwt('https://push.example', 'subject@test');
  const [headerB64, payloadB64, sigB64] = jwt.split('.');
  const sig = Buffer.from(sigB64, 'base64url');
  assert.equal(sig.length, 64);
  const data = new TextEncoder().encode(`${headerB64}.${payloadB64}`);
  const key = await crypto.subtle.importKey(
    'jwk',
    { ...publicKey, key_ops: ['verify'], ext: true },
    { name: 'ECDSA', namedCurve: 'P-256' },
    false,
    ['verify'],
  );
  assert.ok(await crypto.subtle.verify({ name: 'ECDSA', hash: 'SHA-256' }, key, sig, data));
});

test('410 → подписка мёртвая, запись в push_log; вторая та же причина за 30 мин → пуша нет', async () => {
  const DB = openFakeDb();
  let calls = 0;
  const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const env = {
    DB,
    VAPID_PRIVATE_KEY: JSON.stringify(await crypto.subtle.exportKey('jwk', kp.privateKey)),
    VAPID_SUBJECT: 'mailto:test@localhost',
    __fetch: async () => { calls += 1; return { status: 410 }; },
  };
  await DB.prepare(
    "INSERT INTO push_subs (endpoint, p256dh, auth, dead, device_id) VALUES (?, ?, ?, 0, 'dev1')",
  ).bind('https://push.example/sub/1', RFC8291_A.receiverPublic, RFC8291_A.authSecret).run();
  const payload = { status: 'waiting_human', question: { id: 'q1', text: 'идём?' } };
  await notify(env, 'r1', 'm1', payload, 1_700_000_000_000);
  assert.equal(calls, 1);
  const sub = await DB.prepare(
    'SELECT dead FROM push_subs WHERE endpoint = ?',
  ).bind('https://push.example/sub/1').first();
  assert.equal(sub.dead, 1);
  const log = await DB.prepare('SELECT code FROM push_log').all();
  assert.ok(log.results.some((row) => row.code === 410));
  await notify(env, 'r1', 'm1', payload, 1_700_000_000_000 + 60_000);
  assert.equal(calls, 1);
});

async function vapidEnv(DB, extra = {}) {
  const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
  const priv = await crypto.subtle.exportKey('jwk', kp.privateKey);
  return { DB, VAPID_PRIVATE_KEY: JSON.stringify(priv), VAPID_SUBJECT: 'mailto:test@localhost', ...extra };
}

async function liveSub(DB, endpoint = 'https://push.example/sub/1') {
  await DB.prepare(
    "INSERT INTO push_subs (endpoint, p256dh, auth, dead, device_id) VALUES (?, ?, ?, 0, 'dev1')",
  ).bind(endpoint, RFC8291_A.receiverPublic, RFC8291_A.authSecret).run();
}

test('F6: пуш ≤200 символов, точная фраза «подробно — в пульте» не обрезается', () => {
  assert.equal(textFor({ status: 'done' }), 'tero: прогон закончен — подробно — в пульте');
  const long = textFor({ status: 'waiting_human', question: { id: 'q', text: 'ы'.repeat(500) } });
  assert.ok([...long].length <= 200, `длина ${[...long].length}`);
  assert.ok(long.includes('подробно — в пульте'));
});

test('F2: stale-пуш сообщает о потере связи, а не повторяет вопрос', () => {
  const t = textFor({ status: 'waiting_human', stale: true, question: { id: 'q1', text: 'идём?' } });
  assert.ok(t.includes('нет связи с машиной (сон, сеть или краш)'));
  assert.ok(!t.includes('идём?'));
  assert.ok([...t].length <= 200);
});

test('F5: без VAPID пуш не уходит самоподписом; pushOk false', async () => {
  const DB = openFakeDb();
  await liveSub(DB);
  let calls = 0;
  const env = { DB, __fetch: async () => { calls += 1; return { status: 201 }; } };
  await assert.rejects(notify(env, 'r1', 'm1', { status: 'done' }, 1_700_000_000_000), /vapid/i);
  assert.equal(calls, 0);
  assert.equal(await pushOk(DB, env), false);
  const bad = { DB, VAPID_PRIVATE_KEY: 'not-json', __fetch: env.__fetch };
  await assert.rejects(notify(bad, 'r1', 'm1', { status: 'done' }, 1_700_000_000_000), /vapid/i);
  assert.equal(await pushOk(DB, bad), false);
});

test('F5-ok: с настроенным VAPID пуш уходит, pushOk true', async () => {
  const DB = openFakeDb();
  await liveSub(DB);
  let calls = 0;
  const env = await vapidEnv(DB, { __fetch: async () => { calls += 1; return { status: 201 }; } });
  const res = await notify(env, 'r1', 'm1', { status: 'done' }, 1_700_000_000_000);
  assert.equal(res.sent, true);
  assert.equal(calls, 1);
  assert.equal(await pushOk(DB, env), true);
});

test('F3: параллельные notify по одной причине — один пуш', async () => {
  const DB = openFakeDb();
  await liveSub(DB);
  let calls = 0;
  const env = await vapidEnv(DB, {
    __fetch: async () => { calls += 1; await new Promise((r) => setTimeout(r, 20)); return { status: 201 }; },
  });
  const payload = { status: 'waiting_human', question: { id: 'q1', text: 'идём?' } };
  const t = 1_700_000_000_000;
  const [a, b] = await Promise.all([notify(env, 'r1', 'm1', payload, t), notify(env, 'r1', 'm1', payload, t)]);
  assert.equal(calls, 1);
  assert.equal([a, b].filter((r) => r && r.sent).length, 1);
  assert.equal([a, b].filter((r) => r && r.skipped === 'cooldown').length, 1);
});

test('F7: stale после вопроса — отдельная причина cooldown', async () => {
  const DB = openFakeDb();
  await liveSub(DB);
  let calls = 0;
  const env = await vapidEnv(DB, { __fetch: async () => { calls += 1; return { status: 201 }; } });
  const t = 1_700_000_000_000;
  await notify(env, 'r1', 'm1', { status: 'waiting_human', question: { id: 'q1', text: 'идём?' } }, t);
  assert.equal(calls, 1);
  const res = await notify(env, 'r1', 'm1', { status: 'waiting_human', stale: true, question: { id: 'q1', text: 'идём?' } }, t + 16 * 60 * 1000);
  assert.equal(calls, 2);
  assert.equal(res.sent, true);
});

test('F8: сетевой сбой не блокирует повтор через 5 мин', async () => {
  const DB = openFakeDb();
  await liveSub(DB);
  let fail = true;
  let calls = 0;
  const env = await vapidEnv(DB, {
    __fetch: async () => {
      calls += 1;
      if (fail) throw new Error('conn reset');
      return { status: 201 };
    },
  });
  const t = 1_700_000_000_000;
  await assert.rejects(notify(env, 'r1', 'm1', { status: 'done' }, t), /push fetch failed/);
  fail = false;
  const res = await notify(env, 'r1', 'm1', { status: 'done' }, t + 5 * 60 * 1000);
  assert.equal(res.sent, true);
  assert.equal(calls, 2);
});

test('F9: синтаксически целый, но невалидный VAPID JWK — pushOk false', async () => {
  const DB = openFakeDb();
  await liveSub(DB);
  const env = { DB, VAPID_PRIVATE_KEY: JSON.stringify({ kty: 'EC', crv: 'P-256', x: 'AA', y: 'AA', d: 'AA' }), VAPID_SUBJECT: 'mailto:test@localhost', __fetch: async () => ({ status: 201 }) };
  assert.equal(await pushOk(DB, env), false);
});

test('F10: просроченный claim не блокирует следующий пуш', async () => {
  const DB = openFakeDb();
  await liveSub(DB);
  let calls = 0;
  const env = await vapidEnv(DB, { __fetch: async () => { calls += 1; return { status: 201 }; } });
  const t = 1_700_000_000_000;
  await DB.prepare('INSERT INTO push_claims (run_id, reason, at) VALUES (?, ?, ?)').bind('r1', 'done', t - 31 * 60 * 1000).run();
  const res = await notify(env, 'r1', 'm1', { status: 'done' }, t);
  assert.equal(res.sent, true);
  assert.equal(calls, 1);
});

test('F4: падение первой подписки не отменяет пуш второй', async () => {
  const DB = openFakeDb();
  await liveSub(DB, 'https://push.example/sub/1');
  await liveSub(DB, 'https://push.example/sub/2');
  const tried = [];
  const env = await vapidEnv(DB, {
    __fetch: async (url) => {
      tried.push(String(url));
      if (String(url).endsWith('/sub/1')) throw new Error('conn reset');
      return { status: 201 };
    },
  });
  const res = await notify(env, 'r1', 'm1', { status: 'done' }, 1_700_000_000_000);
  assert.equal(res.sent, true);
  assert.deepEqual(tried.sort(), ['https://push.example/sub/1', 'https://push.example/sub/2']);
  const log = await DB.prepare('SELECT code FROM push_log').all();
  assert.ok(log.results.some((r) => r.code === 201));
  const dead = await DB.prepare('SELECT dead FROM push_subs ORDER BY endpoint').all();
  assert.deepEqual(dead.results.map((r) => r.dead), [0, 0]);
});

test('B17: подписка отозванного устройства пуш не получает', async () => {
  const DB = openFakeDb();
  let calls = 0;
  const env = await vapidEnv(DB, { __fetch: async () => { calls += 1; return { status: 201 }; } });
  await liveSub(DB);
  DB._raw.prepare("UPDATE devices SET revoked = 1 WHERE device_id = 'dev1'").run();
  await notify(env, 'r1', 'm1', { status: 'waiting_human', question: { id: 'q', text: 't' } }, 1_700_000_000_000);
  assert.equal(calls, 0);
});
