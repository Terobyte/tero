// relay/src/pair.js — одноразовые коды привязки телефона (план 08-5).
// Код живёт 15 мин и сгорает при обмене; после 5 неверных кодов с одного IP
// за 15 мин — 429. Токены хранятся хешем SHA-256, read-токен виджета отдельный.
// Время — только через Date.now(), чтобы тесты подменяли часы.

export const PAIR_ALPHABET = 'ABCDEFGHJKMNPQRSTUVWXYZ23456789';
export const PAIR_CODE_LEN = 8;
export const PAIR_TTL_MS = 15 * 60 * 1000;
export const MAX_BAD_PER_IP = 5;

export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

export function generatePairCode(randomValues) {
  const take = randomValues || ((buf) => crypto.getRandomValues(buf));
  const buf = new Uint8Array(PAIR_CODE_LEN);
  take(buf);
  let out = '';
  for (let i = 0; i < buf.length; i++) out += PAIR_ALPHABET[buf[i] % PAIR_ALPHABET.length];
  return out;
}

export function randomToken() {
  const buf = crypto.getRandomValues(new Uint8Array(32));
  let bin = '';
  for (const b of buf) bin += String.fromCharCode(b);
  return btoa(bin).replace(/\+/g, '-').replace(/\//g, '_').replace(/=+$/, '');
}

export function clientIp(request) {
  const cf = request.headers.get('cf-connecting-ip');
  if (cf) return cf.split(',')[0].trim();
  const fwd = request.headers.get('x-forwarded-for');
  if (fwd) return fwd.split(',')[0].trim();
  return 'unknown';
}

export async function createPairCode(db, label = '') {
  const code = generatePairCode();
  const now = Date.now();
  await db.prepare(
    'INSERT INTO pair_codes (code, label, created_at, used) VALUES (?, ?, ?, 0)',
  ).bind(code, label, now).run();
  return { code, expires_at: now + PAIR_TTL_MS };
}

export async function exchangePairCode(db, code, label = '') {
  const now = Date.now();
  const claimId = randomToken();
  const deviceId = randomToken();
  const deviceToken = randomToken();
  const readToken = randomToken();
  const createdAt = new Date(now).toISOString();
  const deviceHash = await sha256Hex(deviceToken);
  const readHash = await sha256Hex(readToken);
  // Захват кода и обе вставки — один batch (в D1 это одна транзакция).
  // Вставки берут строку кода только под своим claim: если код уже забран
  // или истёк, UPDATE меняет 0 строк и вставлять нечего; если падает
  // вставка, откатывается и захват — компенсирующих запросов нет.
  const insertSql = 'INSERT INTO devices (token_hash, kind, label, device_id, revoked, created_at) '
    + "SELECT ?, ?, COALESCE(NULLIF(?, ''), label), ?, 0, ? FROM pair_codes WHERE code = ? AND claim = ?";
  const [claim] = await db.batch([
    db.prepare(
      'UPDATE pair_codes SET used = 1, claim = ? WHERE code = ? AND used = 0 AND created_at > ?',
    ).bind(claimId, code, now - PAIR_TTL_MS),
    db.prepare(insertSql).bind(deviceHash, 'device', label, deviceId, createdAt, code, claimId),
    db.prepare(insertSql).bind(readHash, 'read', label, deviceId, createdAt, code, claimId),
  ]);
  const claimed = claim && claim.meta ? Number(claim.meta.changes || 0) : 0;
  if (claimed !== 1) {
    const row = await db.prepare('SELECT code FROM pair_codes WHERE code = ?').bind(code).first();
    return row ? { status: 410, error: 'expired' } : { status: 404, error: 'bad code' };
  }
  return { status: 200, device_token: deviceToken, read_token: readToken, device_id: deviceId };
}

export async function badAttempts(db, ip) {
  const row = await db.prepare(
    'SELECT COUNT(*) AS n FROM pair_attempts WHERE ip = ? AND at > ?',
  ).bind(ip, Date.now() - PAIR_TTL_MS).first();
  return row ? Number(row.n) : 0;
}

// Резерв попытки ДО проверки кода (B15): параллельная пачка не проверит больше
// MAX_BAD_PER_IP кодов. Возвращает id резерва (0 — лимит исчерпан).
export async function consumeBadAttempt(db, ip) {
  const now = Date.now();
  await db.prepare('DELETE FROM pair_attempts WHERE at <= ?').bind(now - PAIR_TTL_MS).run();
  // Атомарно: один стейтмент вставляет только пока счётчик ниже лимита,
  // иначе changes = 0 и обмен отвечает 429. Последовательные check-then-act
  // здесь гоняют даже в одном потоке (await interleaving), поэтому условие
  // обязано жить внутри INSERT, а не в JS.
  const res = await db.prepare(
    'INSERT INTO pair_attempts (ip, at) SELECT ?, ? WHERE '
    + '(SELECT COUNT(*) FROM pair_attempts WHERE ip = ? AND at > ?) < ?',
  ).bind(ip, now, ip, now - PAIR_TTL_MS, MAX_BAD_PER_IP).run();
  const n = res && res.meta ? Number(res.meta.changes || 0) : 0;
  if (n !== 1) return 0;
  return Number(res.meta.last_row_id || 0) || -1;
}

// Удачный обмен возвращает зарезервированную попытку: лимит считает только неверные коды.
export async function releaseAttempt(db, attemptId) {
  if (!(attemptId > 0)) return;
  await db.prepare('DELETE FROM pair_attempts WHERE rowid = ?').bind(attemptId).run();
}

export async function revokeDevice(db, deviceId) {
  const res = await db.prepare(
    'UPDATE devices SET revoked = 1 WHERE device_id = ? AND device_id != \'\' AND revoked = 0',
  ).bind(deviceId).run();
  const n = res && res.meta ? Number(res.meta.changes || 0) : 0;
  // B17: подписки отозванного устройства удаляются — пуши ему больше не идут.
  if (deviceId) await db.prepare('DELETE FROM push_subs WHERE device_id = ?').bind(deviceId).run();
  return { revoked: n };
}

export async function resetDevices(db) {
  const res = await db.prepare('UPDATE devices SET revoked = 1 WHERE revoked = 0').run();
  const n = res && res.meta ? Number(res.meta.changes || 0) : 0;
  await db.prepare('DELETE FROM push_subs').run();
  return { revoked: n };
}
