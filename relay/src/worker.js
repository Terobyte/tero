// relay/src/worker.js — ретранслятор tero: состояние, вопросы/ответы, read-токен.
// Только встроенное API Worker'а (crypto.subtle, fetch, Response).

import { filterState, redactSecrets, stripCodeAndDiffs } from './filter.js';
import {
  MAX_BAD_PER_IP,
  badAttempts,
  clientIp,
  consumeBadAttempt,
  createPairCode,
  exchangePairCode,
  resetDevices,
  revokeDevice,
} from './pair.js';

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

async function deviceRole(request, env) {
  const token = bearerToken(request);
  if (!token) return null;
  const hash = await sha256Hex(token);
  const row = await env.DB.prepare(
    'SELECT kind, revoked FROM devices WHERE token_hash = ?',
  ).bind(hash).first();
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
  await env.DB.prepare(
    'INSERT INTO states (run_id, machine_id, payload, updated_at) VALUES (?, ?, ?, ?) '
    + 'ON CONFLICT (run_id, machine_id) DO UPDATE SET '
    + 'payload = excluded.payload, updated_at = excluded.updated_at',
  ).bind(runId, machineId, JSON.stringify(filtered), updatedAt).run();
  return jsonResponse({ ok: true });
}

async function getState(request, env, url) {
  const role = await deviceRole(request, env);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  const state = await loadState(env, url.searchParams.get('run_id'), url.searchParams.get('machine_id'));
  if (!state) return jsonResponse({ error: 'not found' }, 404);
  return jsonResponse({ state });
}

async function getRuns(request, env) {
  const role = await deviceRole(request, env);
  const denied = revokedResponse(role) || (role ? null : jsonResponse({ error: 'unauthorized' }, 401));
  if (denied) return denied;
  const rows = await env.DB.prepare(
    'SELECT run_id, machine_id, payload, updated_at FROM states ORDER BY updated_at DESC',
  ).all();
  const runs = [];
  for (const row of rows.results || []) {
    let payload = {};
    try {
      payload = JSON.parse(row.payload);
    } catch {
      payload = {};
    }
    if (TERMINAL.has(payload.status)) continue;
    runs.push({
      run_id: sanitizeId(row.run_id),
      machine_id: sanitizeId(row.machine_id),
      status: payload.status ?? null,
      phase: payload.phase ?? null,
      round: payload.round ?? null,
      updated_at: row.updated_at,
    });
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
  const { question_id: questionId, action, text = '' } = body;
  if (typeof questionId !== 'string' || !questionId || typeof action !== 'string' || !action) {
    return jsonResponse({ error: 'question_id and action required' }, 400);
  }
  if (typeof text !== 'string') return jsonResponse({ error: 'text must be a string' }, 400);
  if (text.length > 2000) return jsonResponse({ error: 'text too long' }, 413);
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

const ANSWER_COLS = 'uuid, run_id, machine_id, question_id, action, text, who, received_at';

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
  if (await badAttempts(env.DB, ip) >= MAX_BAD_PER_IP) {
    return jsonResponse({ error: 'too many attempts' }, 429);
  }
  const label = body && typeof body.label === 'string' ? body.label.slice(0, 64) : '';
  let result;
  try {
    result = await exchangePairCode(env.DB, code, label);
  } catch {
    return jsonResponse({ error: 'exchange failed' }, 500);
  }
  if (result.status !== 200) {
    if (result.status === 404) {
      const admitted = await consumeBadAttempt(env.DB, ip);
      if (!admitted) return jsonResponse({ error: 'too many attempts' }, 429);
    }
    return jsonResponse({ error: result.error }, result.status);
  }
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
  if (method === 'POST' && pathname === '/pair/code') return postPairCode(request, env);
  if (method === 'POST' && pathname === '/pair/exchange') return postPairExchange(request, env);
  if (method === 'POST' && pathname === '/pair/revoke') return postPairRevoke(request, env);
  if (method === 'POST' && pathname === '/pair/reset') return postPairReset(request, env);
  return jsonResponse({ error: 'not found' }, 404);
}

export default {
  fetch: (request, env) => handleFetch(request, env),
};
