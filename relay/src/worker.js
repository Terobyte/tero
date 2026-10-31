// relay/src/worker.js — ретранслятор tero: состояние, вопросы/ответы, read-токен.
// Только встроенное API Worker'а (crypto.subtle, fetch, Response).

import { checkStale } from './cron.js';
import { filterState, redactSecrets, stripCodeAndDiffs } from './filter.js';
import { COOLDOWN_MS, PUSH_MAX } from './push.js';

const NOTIFY_PER_WINDOW = 6;
import {
  clientIp,
  consumeBadAttempt,
  releaseAttempt,
  createPairCode,
  exchangePairCode,
  resetDevices,
  revokeDevice,
} from './pair.js';
import { ensurePushTables, isPushEndpoint, notify as sendPush, pushOk } from './push.js';

// F17: run_id/machine_id сохраняются как ключи D1 без фильтра (нужны для
// точного совпадения при GET /state), но наружу (GET /runs) уходят только
// после той же очистки, что остальной payload.
function sanitizeId(value) {
  return redactSecrets(stripCodeAndDiffs(String(value ?? '')));
}

export async function sha256Hex(text) {
  const digest = await crypto.subtle.digest('SHA-256', new TextEncoder().encode(text));
  return Array.from(new Uint8Array(digest), (byte) => byte.toString(16).padStart(2, '0')).join('');
}

const TERMINAL = new Set(['done', 'stopped']);

function jsonResponse(data, status = 200) {
  return new Response(JSON.stringify(data), {
    status,
    headers: { 'content-type': 'application/json; charset=utf-8' },
  });
}

function bearerToken(request) {
  const header = request.headers.get('authorization') || '';
  const match = /^Bearer ([^ ]+)$/.exec(header.trim());
  return match ? match[1] : '';
}

function sameSecret(left, right) {
  if (!left || left.length !== right.length) return false;
  let diff = 0;
  for (let i = 0; i < left.length; i++) diff |= left.charCodeAt(i) ^ right.charCodeAt(i);
  return diff === 0;
}

function hasWriteKey(request, env) {
  const key = env.RELAY_WRITE_KEY || '';
  return key !== '' && sameSecret(bearerToken(request), key);
}

async function deviceRow(request, env) {
  const token = bearerToken(request);
  if (!token) return null;
  const hash = await sha256Hex(token);
  return env.DB.prepare(
    'SELECT kind, revoked, device_id FROM devices WHERE token_hash = ?',
  ).bind(hash).first();
}

async function deviceRole(request, env) {
  const row = await deviceRow(request, env);
  if (!row) return null;
  if (row.revoked) return 'revoked';
  return row.kind;
}

function revokedResponse(role) {
  if (role === 'revoked') return jsonResponse({ error: 'unpaired' }, 401);
  return null;
}

function nowIso() {
  return new Date().toISOString();
}

async function readJson(request) {
  try {
    const body = await request.json();
    return body && typeof body === 'object' ? body : null;
  } catch {
    return null;
  }
}

async function loadState(env, runId, machineId) {
  if (typeof runId !== 'string' || !runId || typeof machineId !== 'string' || !machineId) {
    return null;
  }
  const row = await env.DB.prepare(
    'SELECT payload FROM states WHERE run_id = ? AND machine_id = ?',
  ).bind(runId, machineId).first();
  if (!row) return null;
  try {
    return JSON.parse(row.payload);
  } catch {
    return null;
  }
}

const COMMAND_TTL_S = 600;
const RUN_ID_RE = /^[A-Za-z0-9][A-Za-z0-9._-]{0,80}$/;
const nowS = () => Math.floor(Date.now() / 1000);
const flag = (v) => (v === null || v === undefined ? null : v === 1);

// Отметки сторожа (колонки states) + пульс машины; без единой отметки — null.
async function loadMarks(env, runId, machineId) {
  const row = await env.DB.prepare(
    'SELECT s.owner_alive, s.host_alive, s.owner_checked_at, s.resume_w, m.last_seen '
    + 'FROM states s LEFT JOIN machines m ON m.machine_id = s.machine_id '
    + 'WHERE s.run_id = ? AND s.machine_id = ?',
  ).bind(runId, machineId).first();
  return marksOf(row);
}

function marksOf(row) {
  if (!row) return null;
  let resumeW = null;
  try {
    resumeW = row.resume_w ? JSON.parse(row.resume_w) : null;
  } catch {
    resumeW = null;
  }
  return {
    owner_alive: flag(row.owner_alive),
    host_alive: flag(row.host_alive),
    owner_checked_at: row.owner_checked_at ?? null,
    resume_w: resumeW,
    machine_last_seen: row.last_seen ?? null,
  };
}

async function postState(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const body = await readJson(request);
  if (!body) return jsonResponse({ error: 'bad json' }, 400);
  const { run_id: runId, machine_id: machineId } = body;
  if (typeof runId !== 'string' || !runId || typeof machineId !== 'string' || !machineId) {
    return jsonResponse({ error: 'run_id and machine_id required' }, 400);
  }
  // Секретоподобный id не хранится ключом D1: иначе он уходит через GET /runs
  // или маскируется там и перестаёт совпадать с GET /state.
  if (sanitizeId(runId) !== runId || sanitizeId(machineId) !== machineId) {
    return jsonResponse({ error: 'bad run_id or machine_id' }, 400);
  }
  const filtered = filterState({ ...body, run_id: runId, machine_id: machineId });
  const updatedAt = nowIso();
  filtered.updated_at = updatedAt;
  // state_at — время записи state.json у tero: запоздавший старый push
  // (повис, пока уходил финал done) не затирает более новое состояние.
  if (typeof filtered.state_at !== 'string') delete filtered.state_at;
  // F1: запоздавший старый state (state_at раньше) upsert не меняет — пуш
  // тогда тоже не шлём, иначе просроченный вопрос придёт поверх done.
  const upsert = await env.DB.prepare(
    'INSERT INTO states (run_id, machine_id, payload, updated_at, status) VALUES (?, ?, ?, ?, ?) '
    + 'ON CONFLICT (run_id, machine_id) DO UPDATE SET '
    + 'payload = excluded.payload, updated_at = excluded.updated_at, status = excluded.status '
    + "WHERE json_extract(excluded.payload, '$.state_at') IS NULL "
    + "OR json_extract(states.payload, '$.state_at') IS NULL "
    + "OR json_extract(excluded.payload, '$.state_at') >= json_extract(states.payload, '$.state_at')",
  ).bind(runId, machineId, JSON.stringify(filtered), updatedAt, String(filtered.status ?? '')).run();
  const changed = !upsert || !upsert.meta || Number(upsert.meta.changes) !== 0;
  // События «новый вопрос / done / stopped» — пуш сразу: cron добьёт только
  // просроченный пульс, а вопрос/финал хозяин должен увидеть без 5-минутной задержки.
  const isTerminal = TERMINAL.has(filtered.status);
  const hasQuestion = filtered.question && typeof filtered.question === 'object' && filtered.question.id;
  if (changed && (hasQuestion || isTerminal)) {
    try {
      await sendPush(env, runId, machineId, filtered);
    } catch {
      // Пуш не роняет запись состояния: cron повторит через push_log-кулдаун.
    }
  }
  await ensurePushTables(env.DB);
  const ok = await pushOk(env.DB, env);
  return jsonResponse({ ok: true, push_ok: ok });
}

async function getState(request, env, url) {
  const role = await deviceRole(request, env);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  const state = await loadState(env, url.searchParams.get('run_id'), url.searchParams.get('machine_id'));
  if (!state) return jsonResponse({ error: 'not found' }, 404);
  const marks = await loadMarks(env, url.searchParams.get('run_id'), url.searchParams.get('machine_id'));
  return jsonResponse({ state, marks });
}

async function getRuns(request, env) {
  const role = await deviceRole(request, env);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  // ?all=1 — ещё и терминальные за 7 дней (список «остановлены»); фильтр по колонке status.
  const all = new URL(request.url).searchParams.get('all') === '1';
  const cutoff = new Date(Date.now() - 7 * 24 * 60 * 60 * 1000).toISOString();
  const rows = await (all
    ? env.DB.prepare(
      'SELECT s.run_id, s.machine_id, s.payload, s.updated_at, s.status, s.owner_alive, s.host_alive, '
      + 's.owner_checked_at, s.resume_w, m.last_seen FROM states s LEFT JOIN machines m ON m.machine_id = s.machine_id '
      + "WHERE s.status NOT IN ('done', 'stopped') OR s.updated_at >= ? ORDER BY s.updated_at DESC",
    ).bind(cutoff)
    : env.DB.prepare(
      'SELECT s.run_id, s.machine_id, s.payload, s.updated_at, s.status, s.owner_alive, s.host_alive, '
      + 's.owner_checked_at, s.resume_w, m.last_seen FROM states s LEFT JOIN machines m ON m.machine_id = s.machine_id '
      + "WHERE s.status NOT IN ('done', 'stopped') ORDER BY s.updated_at DESC",
    )).all();
  const runs = [];
  for (const row of rows.results || []) {
    let payload = {};
    try {
      payload = JSON.parse(row.payload);
    } catch {
      payload = {};
    }
    const entry = {
      run_id: sanitizeId(row.run_id),
      machine_id: sanitizeId(row.machine_id),
      status: payload.status ?? null,
      phase: payload.phase ?? null,
      round: payload.round ?? null,
      updated_at: row.updated_at,
    };
    if (all) {
      entry.view = { resume: (payload.view && payload.view.resume) ?? null };
      entry.marks = marksOf(row);
    }
    runs.push(entry);
  }
  return jsonResponse({ runs });
}

async function postAnswer(request, env) {
  const role = await deviceRole(request, env);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  if (role !== 'device') return jsonResponse({ error: 'read-only token' }, 403);
  const body = await readJson(request);
  if (!body) return jsonResponse({ error: 'bad json' }, 400);
  const { question_id: rawQuestionId, action: rawAction, text = '', kind = 'answer' } = body;
  if (!['answer', 'message', 'wake'].includes(kind)) return jsonResponse({ error: 'bad kind' }, 400);
  if (typeof text !== 'string') return jsonResponse({ error: 'text must be a string' }, 400);
  if (text.length > 2000) return jsonResponse({ error: 'text too long' }, 413);
  if (kind !== 'answer') return postOwnerNote(env, body, kind, text);
  const questionId = rawQuestionId;
  const action = rawAction;
  if (typeof questionId !== 'string' || !questionId || typeof action !== 'string' || !action) {
    return jsonResponse({ error: 'question_id and action required' }, 400);
  }
  // run_id+machine_id обязательны: без сопоставленного состояния и открытого
  // вопроса действие проверить нечем, текст-флаг — только после сопоставления.
  const state = await loadState(env, body.run_id, body.machine_id);
  const question = state && state.question && typeof state.question === 'object'
    ? state.question
    : null;
  if (!state || !question || question.id !== questionId) {
    return jsonResponse({ error: 'no open question' }, 422);
  }
  if (state.phone_text_answers === false && text !== '') {
    return jsonResponse({ error: 'text answers disabled' }, 422);
  }
  if (!Array.isArray(question.actions) || !question.actions.includes(action)) {
    return jsonResponse({ error: 'action not allowed' }, 422);
  }
  // F11: run_id+machine_id сохраняются с ответом — id вопроса может
  // совпасть в разных прогонах, без привязки чужой прогон применит skip/stop.
  const uuid = crypto.randomUUID();
  const receivedAt = nowIso();
  await env.DB.prepare(
    'INSERT INTO answers (uuid, run_id, machine_id, question_id, action, text, who, received_at) '
    + 'VALUES (?, ?, ?, ?, ?, ?, ?, ?)',
  ).bind(uuid, body.run_id, body.machine_id, questionId, action, text, 'human', receivedAt).run();
  return jsonResponse({ ok: true, uuid });
}

// Сообщение хозяину и wake: без вопроса, только токен PWA (проверен выше) и
// незавершённый прогон; при marks.owner_alive === false — отказ «сначала resume».
async function postOwnerNote(env, body, kind, text) {
  if (typeof body.run_id !== 'string' || !body.run_id
    || typeof body.machine_id !== 'string' || !body.machine_id) {
    return jsonResponse({ error: 'run_id and machine_id required' }, 400);
  }
  const state = await loadState(env, body.run_id, body.machine_id);
  if (!state) return jsonResponse({ error: 'unknown run' }, 422);
  if (state.status === 'stopped') return jsonResponse({ error: 'прогон не идёт, сначала resume' }, 422);
  if (['done'].includes(state.status)) return jsonResponse({ error: 'run finished' }, 422);
  const marks = await loadMarks(env, body.run_id, body.machine_id);
  if (marks && marks.owner_alive === false) return jsonResponse({ error: 'прогон не идёт, сначала resume' }, 422);
  if (kind === 'message') {
    if (!text) return jsonResponse({ error: 'text required' }, 400);
    if (state.phone_text_answers === false) return jsonResponse({ error: 'text answers disabled' }, 422);
  }
  const uuid = crypto.randomUUID();
  await env.DB.prepare(
    'INSERT INTO answers (uuid, run_id, machine_id, question_id, action, text, who, received_at, kind) '
    + 'VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)',
  ).bind(uuid, body.run_id, body.machine_id, '', kind, kind === 'message' ? text : '', 'human', nowIso(), kind).run();
  return jsonResponse({ ok: true, uuid });
}

// ---- команды с телефона (фаза 4 плана 09): только `resume <run_id>`
async function expireCommands(env, now) {
  await env.DB.prepare("UPDATE commands SET status = 'lost' WHERE status = 'taken' AND taken_at < ?")
    .bind(now - COMMAND_TTL_S).run();
  await env.DB.prepare("UPDATE commands SET status = 'expired' WHERE status = 'pending' AND created_at < ?")
    .bind(now - COMMAND_TTL_S).run();
}

async function postCommand(request, env) {
  const role = await deviceRole(request, env);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  if (role !== 'device') return jsonResponse({ error: 'read-only token' }, 403);
  const body = await readJson(request);
  if (!body) return jsonResponse({ error: 'bad json' }, 400);
  const { run_id: runId, machine_id: machineId, text = '' } = body;
  if (body.command !== undefined && body.command !== 'resume') return jsonResponse({ error: 'bad command' }, 400);
  if (typeof runId !== 'string' || !RUN_ID_RE.test(runId) || typeof machineId !== 'string' || !machineId) {
    return jsonResponse({ error: 'bad run_id' }, 400);
  }
  if (typeof text !== 'string') return jsonResponse({ error: 'text must be a string' }, 400);
  if (text.length > 2000) return jsonResponse({ error: 'text too long' }, 413);
  if (!(await loadState(env, runId, machineId))) return jsonResponse({ error: 'unknown run' }, 422);
  const now = nowS();
  await expireCommands(env, now);
  const busy = await env.DB.prepare(
    "SELECT id FROM commands WHERE run_id = ? AND machine_id = ? AND status IN ('pending', 'taken')",
  ).bind(runId, machineId).first();
  if (busy) return jsonResponse({ error: 'already pending' }, 409);
  const id = crypto.randomUUID();
  try {
    await env.DB.prepare(
      'INSERT INTO commands (id, run_id, machine_id, text, status, created_at) VALUES (?, ?, ?, ?, ?, ?)',
    ).bind(id, runId, machineId, text, 'pending', now).run();
  } catch {
    // Гонка двух POST: частичный индекс idx_commands_active пропустил только одного.
    const clash = await env.DB.prepare(
      "SELECT id FROM commands WHERE run_id = ? AND machine_id = ? AND status IN ('pending', 'taken')",
    ).bind(runId, machineId).first();
    if (clash) return jsonResponse({ error: 'already pending' }, 409);
    throw new Error('command insert failed');
  }
  return jsonResponse({ ok: true, id });
}

async function postCommandsPoll(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const body = await readJson(request);
  const machineId = body && body.machine_id;
  if (typeof machineId !== 'string' || !machineId || sanitizeId(machineId) !== machineId) {
    return jsonResponse({ error: 'machine_id required' }, 400);
  }
  const now = nowS();
  await env.DB.prepare(
    'INSERT INTO machines (machine_id, last_seen) VALUES (?, ?) '
    + 'ON CONFLICT (machine_id) DO UPDATE SET last_seen = excluded.last_seen',
  ).bind(machineId, now).run();
  for (const mark of Array.isArray(body.marks) ? body.marks.slice(0, 200) : []) {
    if (!mark || typeof mark.run_id !== 'string' || !RUN_ID_RE.test(mark.run_id)) continue;
    const bit = (v) => (typeof v === 'boolean' ? (v ? 1 : 0) : null);
    const checked = Number.isFinite(Number(mark.checked_at)) ? Math.floor(Number(mark.checked_at)) : now;
    await env.DB.prepare(
      'UPDATE states SET owner_alive = ?, host_alive = ?, owner_checked_at = ?, resume_w = ? '
      + 'WHERE run_id = ? AND machine_id = ?',
    ).bind(bit(mark.owner_alive), bit(mark.host_alive), checked,
      JSON.stringify(mark.resume && typeof mark.resume === 'object' ? mark.resume : null),
      mark.run_id, machineId).run();
  }
  await expireCommands(env, now);
  const rows = await env.DB.prepare(
    "SELECT id, run_id FROM commands WHERE machine_id = ? AND status = 'pending' ORDER BY created_at",
  ).bind(machineId).all();
  return jsonResponse({ ok: true, pending: rows.results || [] });
}

async function postCommandsTake(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const body = await readJson(request);
  if (!body || typeof body.id !== 'string' || typeof body.machine_id !== 'string') {
    return jsonResponse({ error: 'id and machine_id required' }, 400);
  }
  const now = nowS();
  await expireCommands(env, now);
  const done = await env.DB.prepare(
    "UPDATE commands SET status = 'taken', taken_at = ? WHERE id = ? AND machine_id = ? AND status = 'pending'",
  ).bind(now, body.id, body.machine_id).run();
  if (!done.meta || Number(done.meta.changes) !== 1) return jsonResponse({ error: 'not pending' }, 409);
  const row = await env.DB.prepare('SELECT id, run_id, text FROM commands WHERE id = ?').bind(body.id).first();
  return jsonResponse({ ok: true, ...row });
}

async function postCommandsResult(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const body = await readJson(request);
  if (!body || typeof body.id !== 'string' || typeof body.machine_id !== 'string') {
    return jsonResponse({ error: 'id and machine_id required' }, 400);
  }
  const code = Number.isFinite(Number(body.code)) ? Math.floor(Number(body.code)) : 1;
  const text = typeof body.text === 'string' ? body.text.slice(0, 500) : '';
  await expireCommands(env, nowS());
  const done = await env.DB.prepare(
    "UPDATE commands SET status = ?, code = ?, result_text = ? WHERE id = ? AND machine_id = ? AND status = 'taken'",
  ).bind(code === 0 ? 'done' : 'failed', code, text, body.id, body.machine_id).run();
  if (!done.meta || Number(done.meta.changes) !== 1) return jsonResponse({ error: 'not taken' }, 409);
  return jsonResponse({ ok: true });
}

// Итог последней команды сторожа для телефона (фаза 4 плана 09): читают
// и PWA, и read-токен; протухшие take помечаем lost перед чтением.
async function getLastCommand(request, env, url) {
  const role = await deviceRole(request, env);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  const runId = url.searchParams.get('run_id');
  const machineId = url.searchParams.get('machine_id');
  if (!runId || !machineId) return jsonResponse({ error: 'run_id and machine_id required' }, 400);
  await expireCommands(env, nowS());
  const row = await env.DB.prepare(
    'SELECT id, run_id, status, code, result_text, created_at, taken_at FROM commands '
    + 'WHERE run_id = ? AND machine_id = ? ORDER BY created_at DESC, rowid DESC LIMIT 1',
  ).bind(runId, machineId).first();
  return jsonResponse({ command: row ? { ...row } : null });
}

const ANSWER_COLS = 'uuid, run_id, machine_id, question_id, action, text, who, received_at, kind';

async function getAnswers(request, env, url) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const runId = url.searchParams.get('run_id');
  const machineId = url.searchParams.get('machine_id');
  const questionId = url.searchParams.get('question_id');
  if (runId || machineId) {
    if (!runId || !machineId) return jsonResponse({ error: 'run_id and machine_id required' }, 400);
    const rows = questionId
      ? await env.DB.prepare(
        `SELECT ${ANSWER_COLS} FROM answers WHERE run_id = ? AND machine_id = ? AND question_id = ? ORDER BY received_at`,
      ).bind(runId, machineId, questionId).all()
      : await env.DB.prepare(
        `SELECT ${ANSWER_COLS} FROM answers WHERE run_id = ? AND machine_id = ? ORDER BY received_at`,
      ).bind(runId, machineId).all();
    return jsonResponse({ answers: rows.results || [] });
  }
  const rows = questionId
    ? await env.DB.prepare(
      `SELECT ${ANSWER_COLS} FROM answers WHERE question_id = ? ORDER BY received_at`,
    ).bind(questionId).all()
    : await env.DB.prepare(
      `SELECT ${ANSWER_COLS} FROM answers ORDER BY received_at`,
    ).all();
  return jsonResponse({ answers: rows.results || [] });
}

async function ackAnswer(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const body = await readJson(request);
  if (!body || typeof body.uuid !== 'string' || !body.uuid) {
    return jsonResponse({ error: 'uuid required' }, 400);
  }
  const res = await env.DB.prepare('DELETE FROM answers WHERE uuid = ?').bind(body.uuid).run();
  const deleted = res && res.meta ? Number(res.meta.changes || 0) : 0;
  return jsonResponse({ ok: true, deleted });
}

// Текст от tero (`tero notify`, «агента нет, решу сам») — пуш на телефон с ключом
// записи. Кулдаун — тот же push_log, по тексту; без подписок — не ошибка.
async function postNotify(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const body = await readJson(request);
  const raw = body && typeof body.text === 'string' ? body.text : '';
  if (!raw.trim()) return jsonResponse({ error: 'text required' }, 400);
  const text = redactSecrets(stripCodeAndDiffs(raw)).slice(0, PUSH_MAX);
  const runId = typeof body.run_id === 'string' && body.run_id ? sanitizeId(body.run_id) : 'notify';
  await ensurePushTables(env.DB);
  // Кулдаун по тексту отсекает повтор; общий потолок отсекает поток разных текстов
  // (ключ записи есть у каждого прогона на машине): не больше NOTIFY_PER_WINDOW за 30 мин.
  const since = Date.now() - COOLDOWN_MS;
  const recent = await env.DB.prepare(
    "SELECT COUNT(*) AS n FROM push_log WHERE reason LIKE 'notify:%' AND code >= 200 AND code < 300 AND at > ?",
  ).bind(since).first();
  if (Number(recent && recent.n) >= NOTIFY_PER_WINDOW) {
    return jsonResponse({ ok: true, sent: false, skipped: 'rate' });
  }
  let result;
  try {
    result = await sendPush(env, runId, '', { notify: text });
  } catch {
    return jsonResponse({ ok: false, error: 'push failed' }, 502);
  }
  return jsonResponse({ ok: true, sent: !!(result && result.sent), skipped: (result && result.skipped) || null });
}

async function postPairCode(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const body = await readJson(request);
  const label = typeof (body && body.label) === 'string' ? body.label.slice(0, 64) : '';
  const { code, expires_at } = await createPairCode(env.DB, label);
  return jsonResponse({ code, expires_at });
}

async function postPairExchange(request, env) {
  const body = await readJson(request);
  const code = body && typeof body.code === 'string' ? body.code.trim() : '';
  if (!code) return jsonResponse({ error: 'code required' }, 400);
  const ip = clientIp(request);
  // B15: сначала атомарный резерв попытки, потом проверка кода — не наоборот.
  const attempt = await consumeBadAttempt(env.DB, ip);
  if (!attempt) return jsonResponse({ error: 'too many attempts' }, 429);
  const label = body && typeof body.label === 'string' ? body.label.slice(0, 64) : '';
  let result;
  try {
    result = await exchangePairCode(env.DB, code, label);
  } catch {
    await releaseAttempt(env.DB, attempt);
    return jsonResponse({ error: 'exchange failed' }, 500);
  }
  if (result.status !== 200) {
    return jsonResponse({ error: result.error }, result.status);
  }
  await releaseAttempt(env.DB, attempt);
  return jsonResponse({
    device_token: result.device_token,
    read_token: result.read_token,
    device_id: result.device_id,
  });
}

async function postPairRevoke(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const body = await readJson(request);
  const deviceId = body && typeof body.device_id === 'string' ? body.device_id : '';
  if (!deviceId) return jsonResponse({ error: 'device_id required' }, 400);
  const { revoked } = await revokeDevice(env.DB, deviceId);
  return jsonResponse({ ok: true, revoked });
}

async function postPairReset(request, env) {
  if (!hasWriteKey(request, env)) return jsonResponse({ error: 'unauthorized' }, 401);
  const { revoked } = await resetDevices(env.DB);
  return jsonResponse({ ok: true, revoked });
}

async function postPushSubscribe(request, env) {
  const row = await deviceRow(request, env);
  const role = !row ? null : (row.revoked ? 'revoked' : row.kind);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  if (role !== 'device') return jsonResponse({ error: 'read-only token' }, 403);
  if (!row.device_id) return jsonResponse({ error: 'device without id' }, 403);
  const body = await readJson(request);
  const endpoint = body && typeof body.endpoint === 'string' ? body.endpoint : '';
  const p256dh = body && typeof body.p256dh === 'string' ? body.p256dh : '';
  const auth = body && typeof body.auth === 'string' ? body.auth : '';
  if (!isPushEndpoint(endpoint) || endpoint.length > 500 || !p256dh || !auth) {
    return jsonResponse({ error: 'push endpoint, p256dh and auth required' }, 400);
  }
  // B17: подписка принадлежит устройству; чужую живую не перезаписать,
  // подписку отозванного устройства новое забирает себе.
  const res = await env.DB.prepare(
    'INSERT INTO push_subs (endpoint, p256dh, auth, dead, device_id) VALUES (?, ?, ?, 0, ?) '
    + 'ON CONFLICT (endpoint) DO UPDATE SET p256dh = excluded.p256dh, '
    + 'auth = excluded.auth, dead = 0, device_id = excluded.device_id '
    + 'WHERE push_subs.device_id = excluded.device_id OR NOT EXISTS ('
    + 'SELECT 1 FROM devices d WHERE d.device_id = push_subs.device_id AND d.revoked = 0)',
  ).bind(endpoint, p256dh.slice(0, 200), auth.slice(0, 100), row.device_id).run();
  if (!res || !res.meta || res.meta.changes !== 1) {
    return jsonResponse({ error: 'subscription owned by another device' }, 409);
  }
  return jsonResponse({ ok: true });
}

async function getPushVapid(request, env) {
  const role = await deviceRole(request, env);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  const key = env.VAPID_PUBLIC_KEY || '';
  return jsonResponse({ vapid_public_key: key });
}

async function getManifest(url) {
  const code = url.searchParams.get('code') || '';
  const manifest = {
    name: 'теро-пульт',
    short_name: 'теро',
    display: 'standalone',
    start_url: `/?code=${code}`,
    scope: '/',
    icons: [{ src: '/icon.png', sizes: '512x512', type: 'image/png' }],
  };
  return jsonResponse(manifest, 200);
}

export async function handleFetch(request, env) {
  const url = new URL(request.url);
  const { pathname } = url;
  const method = request.method.toUpperCase();

  if (method === 'POST' && pathname === '/state') return postState(request, env);
  if (method === 'GET' && pathname === '/state') return getState(request, env, url);
  if (method === 'GET' && pathname === '/runs') return getRuns(request, env);
  if (method === 'POST' && pathname === '/answer') return postAnswer(request, env);
  if (method === 'GET' && pathname === '/answers') return getAnswers(request, env, url);
  if (method === 'POST' && pathname === '/answers/ack') return ackAnswer(request, env);
  if (method === 'POST' && pathname === '/commands') return postCommand(request, env);
  if (method === 'GET' && pathname === '/commands/last') return getLastCommand(request, env, url);
  if (method === 'POST' && pathname === '/commands/poll') return postCommandsPoll(request, env);
  if (method === 'POST' && pathname === '/commands/take') return postCommandsTake(request, env);
  if (method === 'POST' && pathname === '/commands/result') return postCommandsResult(request, env);
  if (method === 'POST' && pathname === '/notify') return postNotify(request, env);
  if (method === 'POST' && pathname === '/pair/code') return postPairCode(request, env);
  if (method === 'POST' && pathname === '/pair/exchange') return postPairExchange(request, env);
  if (method === 'POST' && pathname === '/pair/revoke') return postPairRevoke(request, env);
  if (method === 'POST' && pathname === '/pair/reset') return postPairReset(request, env);
  if (method === 'POST' && pathname === '/push/subscribe') return postPushSubscribe(request, env);
  if (method === 'GET' && pathname === '/push/vapid') return getPushVapid(request, env);
  if (method === 'GET' && pathname === '/manifest.webmanifest') return getManifest(url);
  if (method === 'GET' && pathname.startsWith('/pair/')) {
    const code = pathname.slice('/pair/'.length).split('/')[0] || '';
    return Response.redirect(new URL(`/?code=${encodeURIComponent(code)}`, url), 302);
  }
  // Статику relay/public (index.html, source.js, pult.js, sw.js) отдаёт [assets] wrangler —
  // см. wrangler.toml; Worker держит только API и манифест с кодом в start_url.
  return jsonResponse({ error: 'not found' }, 404);
}

export default {
  fetch: (request, env) => handleFetch(request, env),
  async scheduled(event, env, ctx) {
    const nowMs = event && typeof event.scheduledTime === 'number'
      ? event.scheduledTime
      : Date.now();
    if (ctx && typeof ctx.waitUntil === 'function') {
      ctx.waitUntil(checkStale(env, nowMs));
    } else {
      await checkStale(env, nowMs);
    }
  },
};
