// relay/src/jobs.js — /jobs/*: карточки с ноута, решения с телефона, настройки.
// Телефон присылает только фиксированный набор действий (JOB_ACTIONS); любое другое — 400.

import { deviceRole, hasWriteKey, jsonResponse } from './worker.js';
import { notify as sendPush } from './push.js';

export const JOB_ACTIONS = {
  job: ['yes', 'no', 'engine'],
  ask: ['answers', 'stop'],
  approve: ['send', 'decline'],
  captcha: ['captcha_done', 'engine', 'stop'],
  stopped: ['engine', 'no'],
  status: ['no'],
};

const READ_KINDS = ['job', 'status'];
const ENGINES = ['chrome', 'browser-act'];
const FIRST_PATH = ['qa', 'software'];
const MODES = ['push', 'quiet', 'autopilot'];
const MAX_CARDS = 500;
const MAX_ANSWERS = 40;
const MAX_VALUE = 2000;
const DECISION_TTL_MS = 7 * 86400000;
const PUSHED_TTL_MS = 30 * 86400000;

// Общие шаблоны контактов: релей не хранит почту, телефон и адрес, даже если страницу обошли.
// Страница (задача 3) берёт те же источники.
const PHONE_PATTERN = /\+?\d(?:[\s().-]*\d){9,14}/;
export const CONTACT_PATTERNS = [
  /[^\s@]+@[^\s@]+\.[^\s@]+/,
  PHONE_PATTERN,
  /\d+ \w+ (?:St|Street|Ave|Avenue|Rd|Road|Blvd|Way|Dr|Drive)\b/i,
];

// phone: false — для id и ссылок: длинные числа там не телефон, почта и адрес всё равно ловятся.
export function hasContact(text, { phone = true } = {}) {
  // NFKC: полноширинные цифры и «＠» не должны обходить шаблоны.
  const flat = String(text).normalize('NFKC');
  return CONTACT_PATTERNS.some((re) => (phone || re !== PHONE_PATTERN) && re.test(flat));
}

// Ключи, у которых не проверяется только телефон: id, хэши и метки времени несут длинные числа.
const PHONE_EXEMPT_KEYS = new Set(['id', 'filled_hash', 'finished_at']);
// Ссылки: длинный id вакансии в пути — не телефон, но в запросе и якоре телефон ловится.
const URL_KEYS = new Set(['url', 'form_url', 'assist_url']);
// Служебные поля карточки верхнего уровня: версия — хэш (sha1/sha256) или номер паузы.
const MACHINE_KEYS = new Set(['id', 'kind', 'version', 'hot', 'rank']);

const isPlain = (v) => ['string', 'number', 'bigint'].includes(typeof v);

// Телефон в запросе ссылки: отдельный токен (не кусок uuid или хэша), 10+ цифр,
// с ведущим «+» или разбитый на группы не длиннее 4 цифр. Голое длинное число — id.
const PHONE_RUN = /[+(]?\d[\d\s().\-+/]*\d/g;
const ALNUM = /[\p{L}\p{N}]/u;
function hasShapedPhone(text) {
  for (const m of text.matchAll(PHONE_RUN)) {
    const run = m[0];
    const groups = run.match(/\d+/g);
    if (groups.join('').length < 10) continue;
    const before = text[m.index - 1] || '';
    const after = text[m.index + run.length] || '';
    if (ALNUM.test(before) || ALNUM.test(after)) continue;
    if (run.startsWith('+') || (groups.length > 1 && groups.every((g) => g.length <= 4))) return true;
  }
  return false;
}

export function urlHasContact(value) {
  const raw = String(value).normalize('NFKC');
  let decoded = raw;
  try {
    decoded = decodeURIComponent(raw.replace(/\+/g, '%20')).normalize('NFKC');
  } catch {
    decoded = raw;
  }
  if (hasContact(raw, { phone: false }) || hasContact(decoded, { phone: false })) return true;
  const cut = decoded.search(/[?#]/);
  return cut >= 0 && (hasShapedPhone(decoded.slice(cut)) || hasPhoneParam(decoded.slice(cut + 1)));
}

// Параметр, названный телефоном (`phone=6175550123`): голое число под таким именем — не id.
const PHONE_PARAMS = new Set(['phone', 'tel', 'telephone', 'mobile', 'cell', 'sms', 'whatsapp']);
function hasPhoneParam(query) {
  return query.split(/[&;#]/).some((part) => {
    const eq = part.indexOf('=');
    const name = (eq < 0 ? part : part.slice(0, eq)).toLowerCase().replace(/[^a-z]/g, '');
    const value = eq < 0 ? '' : part.slice(eq + 1);
    return (PHONE_PARAMS.has(name) || name.endsWith('phone')) && value.replace(/\D/g, '').length >= 7;
  });
}

// Проверка простого значения под ключом: ссылки по своим правилам, служебные числа без телефона.
function plainHasContact(key, v) {
  if (URL_KEYS.has(key)) return urlHasContact(v);
  if (PHONE_EXEMPT_KEYS.has(key)) return hasContact(String(v), { phone: false });
  return hasContact(String(v));
}

// Исключения — только у простого значения прямо под своим ключом; массивы и объекты
// под этими ключами проверяются целиком. Флага в рекурсии нет:
// `cards.some(cardHasContact)` иначе передал бы индекс как флаг.
function cardHasContact(card) {
  const walk = (value) => {
    if (isPlain(value)) return hasContact(String(value));
    if (Array.isArray(value)) return value.some((v) => walk(v));
    if (isPlainObject(value)) {
      // Имена полей (метки в his/drafts) проверяются целиком.
      return Object.entries(value).some(([key, v]) => hasContact(key) || (
        isPlain(v) ? plainHasContact(key, v) : walk(v)));
    }
    return false;
  };
  // Верхний уровень: служебные поля без проверки телефона (почта и адрес ловятся),
  // всё остальное — payload и любые прочие поля — как текст.
  return Object.entries(card).some(([key, v]) => (
    MACHINE_KEYS.has(key) && isPlain(v) ? hasContact(String(v), { phone: false }) : walk({ [key]: v })));
}

const isPlainObject = (v) => v !== null && typeof v === 'object' && !Array.isArray(v);
const bad = (error, status = 400) => jsonResponse({ error }, status);

async function readBody(request) {
  try {
    const body = await request.json();
    return isPlainObject(body) ? body : null;
  } catch {
    return null;
  }
}

async function setMeta(env, key, value) {
  await env.DB.prepare(
    'INSERT INTO jobs_meta (key, value) VALUES (?, ?) '
    + 'ON CONFLICT (key) DO UPDATE SET value = excluded.value',
  ).bind(key, String(value)).run();
}

async function getSettings(env) {
  const rows = await env.DB.prepare(
    "SELECT key, value FROM jobs_meta WHERE key IN ('mode', 'push_cap', 'laptop_seen')",
  ).all();
  const meta = Object.fromEntries((rows.results || []).map((r) => [r.key, r.value]));
  return {
    mode: meta.mode ?? 'push',
    push_cap: Number(meta.push_cap ?? 4),
    laptop_seen: Number(meta.laptop_seen ?? 0),
  };
}

// Токен телефона: null = пустой или отозванный (ответ 401), иначе роль.
async function phoneRole(request, env) {
  const role = await deviceRole(request, env);
  return role === 'device' || role === 'read' ? role : null;
}

function validCard(card) {
  return isPlainObject(card)
    && typeof card.id === 'string' && card.id !== '' && card.id.length <= 200
    && Object.hasOwn(JOB_ACTIONS, card.kind)
    && typeof card.version === 'string' && card.version !== '' && card.version.length <= 200
    && (card.hot === undefined || typeof card.hot === 'boolean' || card.hot === 0 || card.hot === 1)
    && (card.rank === undefined || Number.isFinite(card.rank));
}

async function putCards(request, env, now) {
  if (!hasWriteKey(request, env)) return bad('unauthorized', 401);
  const body = await readBody(request);
  if (!body) return bad('bad json');
  const cards = body.cards ?? [];
  const drop = body.drop ?? [];
  if (!Array.isArray(cards) || cards.length > MAX_CARDS || !cards.every(validCard)) {
    return bad('bad cards');
  }
  if (!Array.isArray(drop) || drop.length > MAX_CARDS || !drop.every((id) => typeof id === 'string')) {
    return bad('bad drop');
  }
  // Защита вторым рубежом: релей не хранит контакты, даже если ноут их не вычистил.
  if (cards.some(cardHasContact)) return bad('contact');
  const db = env.DB;
  const batch = [];
  for (const card of cards) {
    // Решения старой версии гибнут в той же транзакции, что и упсёрт карточки.
    batch.push(db.prepare('DELETE FROM jobs_decisions WHERE card_id = ? AND version != ?')
      .bind(card.id, card.version));
    batch.push(db.prepare(
      'INSERT INTO jobs_cards (id, kind, version, hot, rank, payload, updated_at) '
      + 'VALUES (?, ?, ?, ?, ?, ?, ?) ON CONFLICT (id) DO UPDATE SET kind = excluded.kind, '
      + 'version = excluded.version, hot = excluded.hot, rank = excluded.rank, '
      + 'payload = excluded.payload, updated_at = excluded.updated_at',
    ).bind(card.id, card.kind, card.version, card.hot ? 1 : 0, card.rank ?? 0, JSON.stringify(card), now));
  }
  for (const id of drop) batch.push(db.prepare('DELETE FROM jobs_cards WHERE id = ?').bind(id));
  batch.push(db.prepare(
    "INSERT INTO jobs_meta (key, value) VALUES ('laptop_seen', ?) "
    + 'ON CONFLICT (key) DO UPDATE SET value = excluded.value',
  ).bind(String(now)));
  await db.batch(batch);
  return jsonResponse({ ok: true });
}

async function listCards(request, env, now) {
  const role = await phoneRole(request, env);
  if (!role) return bad('unauthorized', 401);
  const rows = await env.DB.prepare(
    'SELECT kind, payload FROM jobs_cards ORDER BY hot DESC, rank, id',
  ).all();
  const cards = [];
  for (const row of rows.results || []) {
    if (role === 'read' && !READ_KINDS.includes(row.kind)) continue;
    try {
      const card = JSON.parse(row.payload);
      // Готовые ответы горячей — его личные ответы из базы: токену read они не положены.
      if (role === 'read' && isPlainObject(card.payload)) delete card.payload.prepared;
      cards.push(card);
    } catch {
      // битая строка не должна ронять список
    }
  }
  const settings = await getSettings(env);
  return jsonResponse({ cards, ...settings, now });
}

// Проверка data по действию; возвращает {data} или {error}.
function checkData(action, data) {
  if (data === undefined) data = {};
  if (!isPlainObject(data)) return { error: 'bad data' };
  const keys = Object.keys(data);
  if (action === 'engine') {
    if (keys.length !== 1 || keys[0] !== 'engine' || !ENGINES.includes(data.engine)) {
      return { error: 'bad data' };
    }
    return { data: { engine: data.engine } };
  }
  if (action === 'answers') {
    if (keys.length > MAX_ANSWERS) return { error: 'bad data' };
    // Без прототипа: ярлык формы «__proto__» — обычный ключ, а не сеттер.
    const out = Object.create(null);
    for (const key of keys) {
      const value = data[key];
      if (typeof value !== 'string') return { error: 'bad data' };
      if (value.length > MAX_VALUE) return { error: 'too long' };   // не режем молча: ноут получил бы обрывок
      if (hasContact(key) || hasContact(value)) return { error: 'contact' };
      out[key] = value;
    }
    return { data: out };
  }
  return keys.length === 0 ? { data: {} } : { error: 'bad data' };
}

async function decide(request, env, now) {
  const role = await phoneRole(request, env);
  if (!role) return bad('unauthorized', 401);
  if (role !== 'device') return bad('read-only token', 403);
  const body = await readBody(request);
  if (!body) return bad('bad json');
  const { card_id: cardId, version, action } = body;
  if (typeof cardId !== 'string' || !cardId || typeof version !== 'string' || !version
    || typeof action !== 'string' || !action) {
    return bad('card_id, version and action required');
  }
  const db = env.DB;
  const card = await db.prepare('SELECT kind, version FROM jobs_cards WHERE id = ?').bind(cardId).first();
  if (!card) return bad('unknown card', 404);
  if (card.version !== version) return bad('stale', 409);
  if (!JOB_ACTIONS[card.kind].includes(action)) return bad('action not allowed');
  const checked = checkData(action, body.data);
  if (checked.error) return bad(checked.error);
  const data = JSON.stringify(checked.data);
  const isEngine = card.kind === 'job' && action === 'engine';

  // Версия и «уже решено» проверяются самим INSERT: между чтением выше и записью
  // карточку могли переписать, а двойной тап — вставить то же решение дважды.
  const insert = 'INSERT INTO jobs_decisions (id, card_id, version, kind, action, data, at, acked) '
    + 'SELECT ?, ?, ?, ?, ?, ?, ?, 0 WHERE EXISTS '
    + '(SELECT 1 FROM jobs_cards WHERE id = ? AND version = ?)';
  const args = [crypto.randomUUID(), cardId, version, card.kind, action, data, now, cardId, version];
  let res;
  if (isEngine) {
    res = await db.prepare(
      `${insert} AND NOT EXISTS (SELECT 1 FROM jobs_decisions WHERE card_id = ? AND version = ? `
      + "AND action IN ('yes', 'no')) "
      + "ON CONFLICT (card_id) WHERE kind = 'job' AND action = 'engine' DO UPDATE SET "
      + 'id = excluded.id, data = excluded.data, at = excluded.at, version = excluded.version',
    ).bind(...args, cardId, version).run();
  } else {
    res = await db.prepare(`${insert} ON CONFLICT DO NOTHING`).bind(...args).run();
  }
  if (res.meta.changes === 1) return jsonResponse({ ok: true });

  const fresh = await db.prepare('SELECT version FROM jobs_cards WHERE id = ?').bind(cardId).first();
  if (!fresh) return bad('unknown card', 404);
  if (fresh.version !== version) return bad('stale', 409);
  const first = await db.prepare(
    "SELECT action FROM jobs_decisions WHERE card_id = ? AND version = ? "
    + "AND NOT (kind = 'job' AND action = 'engine')",
  ).bind(cardId, version).first();
  if (first && first.action === action) return jsonResponse({ ok: true });
  if (first) return jsonResponse({ error: 'decided', action: first.action }, 409);
  return bad('stale', 409);
}

async function postSettings(request, env) {
  const role = await phoneRole(request, env);
  if (!role) return bad('unauthorized', 401);
  if (role !== 'device') return bad('read-only token', 403);
  const body = await readBody(request);
  if (!body) return bad('bad json');
  const { mode, push_cap: cap } = body;
  if (mode !== undefined && !MODES.includes(mode)) return bad('bad mode');
  if (cap !== undefined && !(Number.isInteger(cap) && cap >= 1 && cap <= 7)) return bad('bad push_cap');
  if (mode !== undefined) await setMeta(env, 'mode', mode);
  if (cap !== undefined) await setMeta(env, 'push_cap', cap);
  return jsonResponse({ ok: true });
}

async function laptopSettings(request, env, now) {
  if (!hasWriteKey(request, env)) return bad('unauthorized', 401);
  await setMeta(env, 'laptop_seen', now);
  const { mode, push_cap } = await getSettings(env);
  return jsonResponse({ mode, push_cap });
}

async function laptopDecisions(request, env, now) {
  if (!hasWriteKey(request, env)) return bad('unauthorized', 401);
  await setMeta(env, 'laptop_seen', now);
  const rows = await env.DB.prepare(
    'SELECT id, card_id, version, kind, action, data, at FROM jobs_decisions WHERE acked = 0 '
    + 'ORDER BY at, rowid',
  ).all();
  const decisions = (rows.results || []).map((row) => {
    let data = {};
    try {
      data = JSON.parse(row.data);
    } catch {
      data = {};
    }
    return { ...row, data };
  });
  return jsonResponse({ decisions });
}

async function ackDecisions(request, env) {
  if (!hasWriteKey(request, env)) return bad('unauthorized', 401);
  const body = await readBody(request);
  if (!body || !Array.isArray(body.ids) || body.ids.length > MAX_CARDS
    || !body.ids.every((id) => typeof id === 'string')) {
    return bad('ids required');
  }
  if (body.ids.length === 0) return jsonResponse({ ok: true });
  await env.DB.batch(body.ids.map((id) => env.DB.prepare('DELETE FROM jobs_decisions WHERE id = ?').bind(id)));
  return jsonResponse({ ok: true });
}

function bostonDay(nowMs) {
  return new Intl.DateTimeFormat('en-CA', { timeZone: 'America/New_York' }).format(new Date(nowMs));
}

// Пуш вакансии (`job`) считается в суточный push_cap и молчит в режиме quiet.
// Пуш паузы капчи идёт всегда, вне потолка — без него задание висит.
async function postJobsNotify(request, env, now) {
  if (!hasWriteKey(request, env)) return bad('unauthorized', 401);
  const body = await readBody(request);
  const cardId = body && typeof body.card_id === 'string' ? body.card_id : '';
  const text = body && typeof body.text === 'string' ? body.text : '';
  if (!cardId || !text) return bad('card_id and text required');
  const db = env.DB;
  const cardRow = await db.prepare('SELECT kind, version, hot, payload FROM jobs_cards WHERE id = ?').bind(cardId).first();
  if (!cardRow) return jsonResponse({ sent: false, why: 'no-card' });
  const isCaptcha = cardRow.kind === 'captcha';
  if (!isCaptcha) {
    if (cardRow.kind !== 'job') return jsonResponse({ sent: false, why: 'no-card' });
    let path;
    try {
      // В строке лежит карточка целиком (asdict(Card) ноута): путь — в её payload.
      path = JSON.parse(cardRow.payload).payload?.path;
    } catch {
      path = undefined;
    }
    // Пуш только горячей вакансии первого пути — остальные остаются в пачке без пуша.
    if (!cardRow.hot || !FIRST_PATH.includes(path)) return bad('ineligible');
    const { mode } = await getSettings(env);
    if (mode === 'quiet') return jsonResponse({ sent: false, why: 'quiet' });
  }
  const key = isCaptcha ? `${cardId}#${cardRow.version}` : cardId;
  let reserved;
  if (isCaptcha) {
    reserved = await db.prepare(
      'INSERT INTO jobs_pushed (key, kind, day, at) VALUES (?, ?, ?, ?) ON CONFLICT (key) DO NOTHING',
    ).bind(key, 'captcha', bostonDay(now), now).run();
  } else {
    const { push_cap: cap } = await getSettings(env);
    reserved = await db.prepare(
      'INSERT INTO jobs_pushed (key, kind, day, at) '
      + "SELECT ?, 'job', ?, ? WHERE (SELECT COUNT(*) FROM jobs_pushed WHERE kind = 'job' AND day = ?) < ? "
      + 'ON CONFLICT (key) DO NOTHING',
    ).bind(key, bostonDay(now), now, bostonDay(now), cap).run();
  }
  if (reserved.meta.changes === 0) {
    const existing = await db.prepare('SELECT 1 FROM jobs_pushed WHERE key = ?').bind(key).first();
    return jsonResponse({ sent: false, why: existing ? 'dup' : 'cap' });
  }
  let result;
  try {
    result = await sendPush(env, 'jobs', '', { job: { card_id: key, text } }, now);
  } catch {
    result = { error: true };
  }
  if (result && result.sent) return jsonResponse({ sent: true, why: 'ok' });
  await db.prepare('DELETE FROM jobs_pushed WHERE key = ?').bind(key).run();
  if (result && result.skipped === 'no-subscription') return jsonResponse({ sent: false, why: 'no-sub' });
  return jsonResponse({ sent: false, why: 'error' });
}

export async function handleJobs(request, env, url, now = Date.now()) {
  const method = request.method.toUpperCase();
  const { pathname } = url;
  if (method === 'POST' && pathname === '/jobs/cards') return putCards(request, env, now);
  if (method === 'GET' && pathname === '/jobs/cards') return listCards(request, env, now);
  if (method === 'POST' && pathname === '/jobs/decide') return decide(request, env, now);
  if (method === 'POST' && pathname === '/jobs/notify') return postJobsNotify(request, env, now);
  if (method === 'POST' && pathname === '/jobs/settings') return postSettings(request, env);
  if (method === 'GET' && pathname === '/jobs/settings') return laptopSettings(request, env, now);
  if (method === 'GET' && pathname === '/jobs/decisions') return laptopDecisions(request, env, now);
  if (method === 'POST' && pathname === '/jobs/decisions/ack') return ackDecisions(request, env);
  return null;
}

// Cron: неподтверждённые решения старше 7 дней; jobs_pushed (задача 2) и push_log
// с reason 'jobs:%' старше 30 дней. jobs_pushed появится позже — пока таблицы может не быть.
export async function sweepJobs(env, nowMs = Date.now()) {
  await env.DB.prepare('DELETE FROM jobs_decisions WHERE at < ?').bind(nowMs - DECISION_TTL_MS).run();
  const cutoff = nowMs - PUSHED_TTL_MS;
  await env.DB.prepare("DELETE FROM push_log WHERE reason LIKE 'jobs:%' AND at < ?").bind(cutoff).run();
  try {
    await env.DB.prepare('DELETE FROM jobs_pushed WHERE at < ?').bind(cutoff).run();
  } catch (err) {
    if (!/no such table/i.test(String(err && err.message))) throw err;
  }
}
