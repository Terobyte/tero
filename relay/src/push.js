// relay/src/push.js — web push (фаза 08-7): VAPID JWT ES256 + aes128gcm RFC 8291.
// Только встроенное API Worker'а/Node (crypto.subtle, fetch). Секретов в коде
// нет: VAPID-ключи — из env (VAPID_PRIVATE_KEY — JWK JSON, VAPID_SUBJECT).

const te = new TextEncoder();

export const PUSH_MAX = 200;
export const COOLDOWN_MS = 30 * 60 * 1000;

function b64uEncode(input) {
  const bytes = typeof input === 'string' ? te.encode(input) : input;
  let bin = '';
  for (const b of bytes) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

function b64uDecode(s) {
  const norm = String(s).replace(/-/g, '+').replace(/_/g, '/');
  const bin = atob(norm);
  const out = new Uint8Array(bin.length);
  for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
  return out;
}

function concat(...parts) {
  const total = parts.reduce((n, p) => n + p.length, 0);
  const out = new Uint8Array(total);
  let off = 0;
  for (const p of parts) {
    out.set(p, off);
    off += p.length;
  }
  return out;
}

function required(value, name) {
  if (value === undefined || value === null || value === '') throw new Error(`${name} required`);
  return value;
}

async function hmacSha256(keyBytes, data) {
  const key = await crypto.subtle.importKey('raw', keyBytes, { name: 'HMAC', hash: 'SHA-256' }, false, ['sign']);
  return new Uint8Array(await crypto.subtle.sign('HMAC', key, data));
}

// Шифрование aes128gcm по RFC 8291 §3. Эфемерный ключ и salt — параметры
// (по умолчанию случайные), чтобы тест подставил вектор Appendix A.
export async function encryptPayload(plaintext, opts = {}) {
  const toBytes = (v) => (typeof v === 'string' ? b64uDecode(v) : new Uint8Array(v));
  const uaPublic = toBytes(required(opts.receiverPublic, 'receiverPublic'));
  if (uaPublic.length !== 65) throw new Error('receiverPublic must be 65 bytes');
  const authSecret = toBytes(required(opts.authSecret, 'authSecret'));
  const salt = opts.salt !== undefined ? toBytes(opts.salt) : crypto.getRandomValues(new Uint8Array(16));
  if (salt.length !== 16) throw new Error('salt must be 16 bytes');
  let asPublic;
  let asPrivateKey;
  if (opts.senderPrivate !== undefined) {
    if (opts.senderPublic === undefined) throw new Error('senderPublic required with senderPrivate');
    asPublic = toBytes(opts.senderPublic);
    if (asPublic.length !== 65) throw new Error('senderPublic must be 65 bytes');
    const d = typeof opts.senderPrivate === 'string' ? opts.senderPrivate : b64uEncode(toBytes(opts.senderPrivate));
    asPrivateKey = await crypto.subtle.importKey(
      'jwk',
      {
        kty: 'EC', crv: 'P-256', ext: true,
        x: b64uEncode(asPublic.slice(1, 33)),
        y: b64uEncode(asPublic.slice(33, 65)),
        d,
      },
      { name: 'ECDH', namedCurve: 'P-256' },
      false,
      ['deriveBits'],
    );
  } else if (opts.senderPublic !== undefined) {
    throw new Error('senderPrivate required');
  } else {
    const kp = await crypto.subtle.generateKey({ name: 'ECDH', namedCurve: 'P-256' }, true, ['deriveBits']);
    asPrivateKey = kp.privateKey;
    asPublic = new Uint8Array(await crypto.subtle.exportKey('raw', kp.publicKey));
  }
  const uaKey = await crypto.subtle.importKey('raw', uaPublic, { name: 'ECDH', namedCurve: 'P-256' }, false, []);
  const ecdh = new Uint8Array(await crypto.subtle.deriveBits({ name: 'ECDH', public: uaKey }, asPrivateKey, 256));
  // §3.3: PRK_key = HMAC(auth_secret, ecdh_secret); IKM = HMAC(PRK_key, key_info || 0x01).
  const prkKey = await hmacSha256(authSecret, ecdh);
  const keyInfo = concat(te.encode('WebPush: info'), new Uint8Array([0]), uaPublic, asPublic);
  const ikm = await hmacSha256(prkKey, concat(keyInfo, new Uint8Array([1])));
  // §3.4: PRK = HMAC(salt, IKM); CEK/NONCE — HKDF-Expand с одним блоком.
  const prk = await hmacSha256(salt, ikm);
  const cek = (await hmacSha256(prk, concat(te.encode('Content-Encoding: aes128gcm'), new Uint8Array([0, 1])))).slice(0, 16);
  const nonce = (await hmacSha256(prk, concat(te.encode('Content-Encoding: nonce'), new Uint8Array([0, 1])))).slice(0, 12);
  // Делимитер 0x02, без паддинга; AES-GCM дописывает 16-байтный тег.
  const pt = concat(te.encode(String(plaintext)), new Uint8Array([2]));
  const aesKey = await crypto.subtle.importKey('raw', cek, { name: 'AES-GCM' }, false, ['encrypt']);
  const ct = new Uint8Array(await crypto.subtle.encrypt({ name: 'AES-GCM', iv: nonce }, aesKey, pt));
  // §4: salt(16) + rs=4096 BE(4) + idlen=65(1) + as_public(65).
  return concat(salt, new Uint8Array([0, 0, 0x10, 0, asPublic.length]), asPublic, ct);
}

// VAPID JWT ES256: подпись — сырые r‖s (64 байта), aud = origin endpoint, exp ≤ 24 ч.
export async function createVapidJwt(audience, subject, opts = {}) {
  const nowMs = opts.nowMs ?? Date.now();
  const expSec = Math.min(opts.expiresInSec ?? 12 * 3600, 24 * 3600);
  let privKey;
  let pubJwk;
  if (opts.privateKey) {
    const jwk = opts.privateKey;
    privKey = await crypto.subtle.importKey(
      'jwk', { ...jwk, ext: true }, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign'],
    );
    pubJwk = { kty: 'EC', crv: 'P-256', x: jwk.x, y: jwk.y, ext: true };
  } else {
    const kp = await crypto.subtle.generateKey({ name: 'ECDSA', namedCurve: 'P-256' }, true, ['sign', 'verify']);
    privKey = kp.privateKey;
    pubJwk = await crypto.subtle.exportKey('jwk', kp.publicKey);
  }
  let aud = String(audience);
  try {
    aud = new URL(aud).origin;
  } catch {
    // не URL — как есть
  }
  const header = { typ: 'JWT', alg: 'ES256' };
  const payload = { aud, exp: Math.floor(nowMs / 1000) + expSec, sub: String(subject || '') };
  const unsigned = `${b64uEncode(JSON.stringify(header))}.${b64uEncode(JSON.stringify(payload))}`;
  const sig = new Uint8Array(await crypto.subtle.sign({ name: 'ECDSA', hash: 'SHA-256' }, privKey, te.encode(unsigned)));
  return { jwt: `${unsigned}.${b64uEncode(sig)}`, publicKey: pubJwk };
}

function reasonFor(payload) {
  const stale = payload && payload.stale === true ? ':stale' : '';
  const q = payload && payload.question;
  if (q && typeof q === 'object' && typeof q.id === 'string' && q.id) return `question:${q.id}${stale}`;
  return String((payload && payload.status) || 'stale') + stale;
}

const SUFFIX = ' — подробно — в пульте';

export function textFor(payload) {
  // F2: stale-пуш cron'а — это потеря связи, а не старый вопрос/статус.
  if (payload && payload.stale === true) {
    return `tero: нет связи с машиной (сон, сеть или краш)${SUFFIX}`.slice(0, PUSH_MAX);
  }
  const q = payload && payload.question;
  const detail = q && typeof q === 'object' && typeof q.text === 'string' && q.text ? q.text : null;
  const status = (payload && payload.status) || '';
  let core = detail || (status === 'done' ? 'прогон закончен' : status === 'stopped' ? 'прогон остановлен' : 'нужен ответ');
  // F6: фраза-суффикс обязана уцелеть — режем тело так, чтобы суффикс влез в 200.
  // Многоточие при обрезке вопроса — тоже в лимит (резерв 1 символ).
  const room = PUSH_MAX - [...SUFFIX].length - 'tero: '.length - (detail ? 1 : 0);
  const clipped = [...core].slice(0, Math.max(room, 0)).join('');
  core = detail && clipped.length < [...core].length ? `${clipped}…` : clipped;
  return `tero: ${core}${SUFFIX}`;
}

export async function ensurePushTables(db) {
  await db.prepare(
    'CREATE TABLE IF NOT EXISTS push_subs (endpoint TEXT PRIMARY KEY, p256dh TEXT NOT NULL, '
    + "auth TEXT NOT NULL, dead INTEGER NOT NULL DEFAULT 0, created_at TEXT NOT NULL DEFAULT '')",
  ).run();
  await db.prepare(
    'CREATE TABLE IF NOT EXISTS push_log (id INTEGER PRIMARY KEY AUTOINCREMENT, '
    + "run_id TEXT NOT NULL DEFAULT '', reason TEXT NOT NULL DEFAULT '', code INTEGER NOT NULL, at INTEGER NOT NULL)",
  ).run();
  await db.prepare(
    'CREATE INDEX IF NOT EXISTS idx_push_log_run_reason ON push_log (run_id, reason, at)',
  ).run();
  // F3: слот кулдауна — одна строка на (run_id, reason); чистится при записи
  // push_log той же причины, чтобы таблица не росла.
  await db.prepare(
    'CREATE TABLE IF NOT EXISTS push_claims (run_id TEXT NOT NULL, reason TEXT NOT NULL, '
    + 'at INTEGER NOT NULL, PRIMARY KEY (run_id, reason))',
  ).run();
}

// true, пока есть хоть одна живая подписка (или подписок вообще нет —
// канал не сломан, а не настроен); false — все подписки мертвы (404/410)
// или VAPID для отправки не настроен/битый (F5, F9: ключ обязан проходить
// importKey для ES256-подписи, иначе push-сервис подписку отвергнет).
// aSync-вариант: Web Crypto асинхронен. Старый синхронный vapidConfig оставлен
// ниже — notify ловит importKey-ошибку и роняет вызов до отправки.
export async function vapidConfigAsync(env) {
  const { privateKey, subject } = vapidConfig(env);
  try {
    await crypto.subtle.importKey(
      'jwk', { ...privateKey, ext: true }, { name: 'ECDSA', namedCurve: 'P-256' }, false, ['sign'],
    );
  } catch {
    throw new Error('vapid private key invalid');
  }
  return { privateKey, subject };
}

export function vapidConfig(env) {
  const raw = env && env.VAPID_PRIVATE_KEY;
  if (!raw) throw new Error('vapid private key missing');
  let jwk;
  try {
    jwk = JSON.parse(raw);
  } catch {
    throw new Error('vapid private key invalid');
  }
  if (!jwk || typeof jwk !== 'object' || !jwk.x || !jwk.y || !jwk.d) {
    throw new Error('vapid private key invalid');
  }
  return { privateKey: jwk, subject: (env && env.VAPID_SUBJECT) || 'mailto:tero@localhost' };
}

export async function pushOk(db, env = null) {
  if (env) {
    try {
      await vapidConfigAsync(env);
    } catch {
      // Конфиг битый, но проверять подписки нечем — смотрим только таблицу:
      // при живых подписках false всё равно правильнее true-настроенного.
      try {
        const c = await db.prepare('SELECT COUNT(*) AS n FROM push_subs').first();
        if (!c || Number(c.n) === 0) return true;
      } catch {
        return true;
      }
      return false;
    }
  }
  try {
    const t = await db.prepare('SELECT COUNT(*) AS n FROM push_subs').first();
    if (!t || Number(t.n) === 0) return true;
    const live = await db.prepare('SELECT COUNT(*) AS n FROM push_subs WHERE COALESCE(dead,0)=0').first();
    return Number(live && live.n) > 0;
  } catch {
    return true;
  }
}

// Отправка пуша всем живым подпискам: текст ≤ 200 символов, без находок;
// одна причина не чаще раза в 30 мин (по run_id+причине в push_log);
// код ответа — в push_log; 404/410 → подписка мертва. Сеть роняет вызов
// исключением (cron тогда снимет отметку и попробует снова), а не тишиной.
export async function notify(env, runId, machineId, payload, nowMs = Date.now()) {
  void machineId;
  const db = env.DB;
  await ensurePushTables(db);
  const reason = reasonFor(payload);
  // F8: кулдаун считает только доставленные ответы (2xx–4xx): запись code=0 —
  // сетевой обрыв без ответа сервиса — не доставка, повтору не мешает.
  // F10: перед захватом вычищаем claims старше 30 мин — зависший слот
  // (Worker упал после INSERT) не блокирует пуш навсегда.
  let lastAt = 0;
  try {
    const row = await db.prepare(
      'SELECT at FROM push_log WHERE run_id = ? AND reason = ? AND code != 0 ORDER BY at DESC LIMIT 1',
    ).bind(String(runId), reason).first();
    if (row && Number.isFinite(Number(row.at))) lastAt = Number(row.at);
  } catch {
    lastAt = 0;
  }
  if (nowMs - lastAt < COOLDOWN_MS) return { skipped: 'cooldown' };
  let subs = [];
  try {
    const res = await db.prepare(
      'SELECT endpoint, p256dh, auth FROM push_subs WHERE COALESCE(dead,0)=0',
    ).all();
    subs = (res && res.results) || [];
  } catch {
    subs = [];
  }
  if (!subs.length) return { skipped: 'no-subscription' };
  // F5: стабильный VAPID обязателен — случайный ключ сгенерировал бы подпись,
  // которую push-сервис отвергнет, а канал выглядел бы живым.
  const { privateKey: vapidPriv, subject } = await vapidConfigAsync(env);
  const doFetch = env.__fetch || ((url, init) => fetch(url, init));
  const text = textFor(payload);
  // F3: атомарный кулдаун — резерв слота одной строкой: под конкурентными
  // вызовами в D1 влезет только один (уникальный индекс run_id+reason).
  // F10: зависшие слоты старше окна — мусор, чистим перед захватом.
  try {
    await db.prepare(
      'DELETE FROM push_claims WHERE run_id = ? AND reason = ? AND at <= ?',
    ).bind(String(runId), reason, nowMs - COOLDOWN_MS).run();
  } catch {
    // очистка не роняет пуш
  }
  try {
    await db.prepare(
      'INSERT INTO push_claims (run_id, reason, at) VALUES (?, ?, ?)',
    ).bind(String(runId), reason, nowMs).run();
  } catch {
    return { skipped: 'cooldown' };
  }
  const errors = [];
  let lastCode = 0;
  let sentAny = false;
  for (const sub of subs) {
    try {
      const body = await encryptPayload(text, { receiverPublic: sub.p256dh, authSecret: sub.auth });
      const built = await createVapidJwt(sub.endpoint, subject, { privateKey: vapidPriv });
      const vk = b64uEncode(concat(new Uint8Array([4]), b64uDecode(built.publicKey.x), b64uDecode(built.publicKey.y)));
      let res;
      try {
        res = await doFetch(String(sub.endpoint), {
          method: 'POST',
          headers: {
            'content-type': 'application/octet-stream',
            'content-encoding': 'aes128gcm',
            ttl: '86400',
            authorization: `Vapid t=${built.jwt}, k=${vk}`,
          },
          body,
        });
      } catch {
        res = null;
      }
      const code = Number(res && res.status) || 0;
      if (!code) {
        // F4: обрыв одной подписки — не приговор остальным: помечаем кодом 0
        // в лог и идём дальше. Исключением роняли бы весь цикл и Worker.
        try {
          await db.prepare(
            'INSERT INTO push_log (run_id, reason, code, at) VALUES (?, ?, ?, ?)',
          ).bind(String(runId), reason, 0, nowMs).run();
        } catch {
          // лог диагностики не роняет пуш
        }
        errors.push(new Error(`push fetch failed: ${sub.endpoint}`));
        continue;
      }
      lastCode = lastCode || code;
      sentAny = sentAny || (code >= 200 && code < 300);
      try {
        await db.prepare(
          'INSERT INTO push_log (run_id, reason, code, at) VALUES (?, ?, ?, ?)',
        ).bind(String(runId), reason, code, nowMs).run();
      } catch {
        // лог диагностики не роняет пуш
      }
      // Слот кулдауна отжил своё — push_log теперь сам держит 30-минутное окно.
      try {
        await db.prepare(
          'DELETE FROM push_claims WHERE run_id = ? AND reason = ?',
        ).bind(String(runId), reason).run();
      } catch {
        // очистка не роняет пуш
      }
      if (code === 404 || code === 410) {
        try {
          await db.prepare('UPDATE push_subs SET dead=1 WHERE endpoint=?').bind(sub.endpoint).run();
        } catch {
          // отметка не роняет пуш
        }
      }
    } catch (err) {
      // F4: битая подписка (ключ/шифрование) — тоже per-sub, не весь пуш.
      errors.push(err instanceof Error ? err : new Error(String(err)));
    }
  }
  if (!sentAny && errors.length && !lastCode) {
    // Все подписки упали по сети — снимаем резерв, чтобы cron повторил.
    try {
      await db.prepare(
        'DELETE FROM push_claims WHERE run_id = ? AND reason = ?',
      ).bind(String(runId), reason).run();
    } catch {
      // очистка не роняет пуш
    }
    throw errors[0];
  }
  return { sent: sentAny || lastCode !== 0, code: lastCode };
}
