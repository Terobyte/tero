import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import { handleFetch, sha256Hex } from '../src/worker.js';
import {
  captureDraft, phoneLight, renderBelow, renderPair, renderQuestion, renderTop, restoreDraft,
  tokenFromLink, viewOf,
} from '../public/pult.js';
import { openFakeDb } from './fake_d1.js';

// Страница теперь = карточка view.html + pult.js; старые проверки страницы
// идут через то же сочетание: без токена / unpaired — экран привязки, иначе верх + низ.
function renderPage(state = {}, opts = {}) {
  if (!opts.token) return renderPair(false);
  if (opts.unpaired) return renderPair(true);
  return renderTop(state) + renderBelow(state, opts);
}
const SHELL = new URL('../public/pult.js', import.meta.url);

const WRITE_KEY = 'write-key-1';
const DEVICE_TOKEN = 'device-token-1';
const READ_TOKEN = 'read-token-1';

const fixture = () => ({
  run_id: 'r1',
  machine_id: 'm1',
  status: 'waiting_human',
  phase: '3/8',
  round: 2,
  role: 'player',
  model: 'm1',
  question: { id: '3-1', text: 'идём дальше?', actions: ['more', 'skip', 'retry', 'stop'] },
  models: ['m1', 'm2', 'judge'],
  decisions: [{ who: 'human', what: 'more', why: 'ок', at: '2026-10-27T01:00:00Z' }],
  debts: [{ phase: '1/8', reason: 'skip' }],
  history: [{ phase: '1/8', verdict: 'SHIP' }],
  events: [{ at: '2026-10-27T01:00:00Z', text: 'вопрос 3-1' }],
  phone_text_answers: true,
});

test('открытый вопрос раньше статуса', () => {
  const html = renderPage(fixture(), { token: 't' });
  const q = html.indexOf('идём дальше?');
  const s = html.indexOf('waiting_human');
  assert.ok(q >= 0 && s >= 0 && q < s, 'вопрос должен идти раньше статуса');
});

test('моделей judge в списке нет', () => {
  const html = renderPage(fixture(), { token: 't' });
  assert.ok(!html.includes('judge'), 'judge нельзя выбирать через model');
});

test('закрытый вопрос → «уже отвечено»', () => {
  const st = fixture();
  st.question = null;
  st.answered = { by: 'human', action: 'more' };
  const html = renderPage(st, { token: 't' });
  assert.ok(html.includes('уже отвечено'), 'закрытый вопрос показывает итог');
});

test('phone_text_answers: false → кнопки без текста', () => {
  const st = fixture();
  st.phone_text_answers = false;
  const html = renderPage(st, { token: 't' });
  assert.ok(!html.includes('<textarea'), 'текстового поля быть не должно');
  for (const action of ['more', 'skip', 'retry', 'stop']) {
    assert.ok(html.includes(`data-action="${action}"`), `нет кнопки ${action}`);
  }
});

test('stop — с подтверждением', () => {
  const html = renderPage(fixture(), { token: 't' });
  assert.ok(html.includes('confirm'), 'stop спрашивает подтверждение');
});

test('кнопка пушей есть; requestPermission только по ней', () => {
  const html = renderPage(fixture(), { token: 't' });
  assert.ok(html.includes('id="push-btn"'), 'нет кнопки включения пушей');
  const shell = readFileSync(SHELL, 'utf8');
  const hits = shell.split('requestPermission').length - 1;
  assert.equal(hits, 2, 'requestPermission: проверка API + вызов');
  assert.ok(shell.includes('pushBtn') || shell.includes('push-btn'), 'привязка к кнопке пушей');
  const at = shell.indexOf('Notification.requestPermission');
  assert.ok(at > 0, 'вызов — только из обработчика кнопки пушей');
  assert.ok(shell.lastIndexOf('enablePush', at) >= 0, 'вызов — только из обработчика кнопки пушей');
});

test('прогоны, долг и история — на странице; журнал теперь в карточке (view.log)', () => {
  const runs = [{ run_id: 'r1', status: 'waiting_human' }, { run_id: 'r2', status: 'running' }];
  const html = renderPage(fixture(), { token: 't', runs });
  assert.ok(html.includes('r2'), 'список прогонов с переключением');
  assert.ok(!html.includes('id="events"'), 'отдельного журнала под карточкой нет');
  assert.ok(html.includes('1/8'), 'долг и история фазы');
});

test('без токена — экран привязки, без данных', () => {
  const html = renderPage(fixture(), { token: '' });
  assert.ok(html.includes('привяз'), 'только экран привязки');
  assert.ok(!html.includes('идём дальше?'), 'вопрос без токена не виден');
  assert.ok(!html.includes('waiting_human'), 'статус без токена не виден');
});

test('unpaired → «привязка недействительна», без данных', () => {
  const html = renderPage(fixture(), { token: 't', unpaired: true });
  assert.ok(html.includes('привязка недействительна'), 'отозванный токен виден');
  assert.ok(!html.includes('идём дальше?'), 'данные при unpaired не видны');
});

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

test('подписка на пуши: device пишет, read нет, мусор → 400', async () => {
  const env = await setupEnv();
  const sub = { endpoint: 'https://web.push.apple.com/sub/9', p256dh: 'abc', auth: 'def' };
  const ok = await handleFetch(
    request('POST', '/push/subscribe', { token: DEVICE_TOKEN, body: sub }), env,
  );
  assert.equal(ok.status, 200);
  const stored = await env.DB.prepare(
    'SELECT endpoint FROM push_subs WHERE endpoint = ?',
  ).bind(sub.endpoint).first();
  assert.equal(stored.endpoint, sub.endpoint);
  const readDenied = await handleFetch(
    request('POST', '/push/subscribe', { token: READ_TOKEN, body: sub }), env,
  );
  assert.equal(readDenied.status, 403);
  const anon = await handleFetch(request('POST', '/push/subscribe', { body: sub }), env);
  assert.equal(anon.status, 401);
  const junk = await handleFetch(
    request('POST', '/push/subscribe', { token: DEVICE_TOKEN, body: { endpoint: 'http://x' } }),
    env,
  );
  assert.equal(junk.status, 400);
});

test('vapid-ключ отдаётся с токеном, без — 401', async () => {
  const env = await setupEnv();
  env.VAPID_PUBLIC_KEY = 'BPublic';
  const ok = await handleFetch(request('GET', '/push/vapid', { token: DEVICE_TOKEN }), env);
  assert.equal(ok.status, 200);
  assert.equal((await ok.json()).vapid_public_key, 'BPublic');
  const anon = await handleFetch(request('GET', '/push/vapid'), env);
  assert.equal(anon.status, 401);
});

test('манифест: standalone, код в start_url', async () => {
  const env = await setupEnv();
  const res = await handleFetch(request('GET', '/manifest.webmanifest?code=AB12CD34'), env);
  assert.equal(res.status, 200);
  const manifest = await res.json();
  assert.equal(manifest.display, 'standalone');
  assert.ok(String(manifest.start_url).includes('AB12CD34'), 'код привязки в start_url');
  assert.equal(manifest.scope, '/');
});

test('sw.js: пуш показывает уведомление, тап открывает корень', () => {
  const sw = readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8');
  assert.ok(sw.includes('showNotification'), 'пуш без уведомления — нарушение спеки');
  assert.ok(sw.includes("openWindow('/')") || sw.includes('openWindow("/")'), 'тап ведёт в корень');
  assert.ok(!sw.includes('import'), 'sw без импортов — кешируется целиком');
});

test('index.html — копия view.html с манифестом и source.js; source.js грузит pult.js', () => {
  const page = readFileSync(new URL('../public/index.html', import.meta.url), 'utf8');
  assert.ok(page.includes('/manifest.webmanifest'), 'PWA без манифеста не встанет');
  assert.ok(page.includes('src="source.js"'), 'подача карточки');
  assert.ok(page.includes('/*render:start*/'), 'карточка HUD внутри страницы');
  assert.ok(!page.includes('render.js'), 'старого render.js больше нет');
  const source = readFileSync(new URL('../public/source.js', import.meta.url), 'utf8');
  assert.ok(source.includes("'data-host', 'phone'") && source.includes('pult.js'));
  assert.ok(source.includes('window.teroSource'));
});

test('неизвестный путь — 404 json, а не статика', async () => {
  const env = await setupEnv();
  const missing = await handleFetch(request('GET', '/nope'), env);
  assert.equal(missing.status, 404);
  assert.equal((await missing.json()).error, 'not found');
});

test('F1: действие answer рендерится, чужие не выдумываются', () => {
  const html = renderPage({ question: { id: 'q', text: 't', actions: ['answer'] } }, { token: 't' });
  assert.ok(html.includes('data-action="answer"'), 'answer должно рендериться');
  for (const invented of ['more', 'skip', 'retry', 'stop']) {
    assert.ok(!html.includes(`data-action="${invented}"`), `не выдумывать ${invented}`);
  }
});

test('F2: действие model — с выбором не-judge моделей', () => {
  const html = renderPage(
    { question: { id: 'q', text: 't', actions: ['model'] }, models: ['m1', 'judge'] },
    { token: 't' },
  );
  assert.ok(html.includes('data-action="model"'), 'model должно рендериться');
  assert.ok(html.includes('m1'), 'доступная модель видна');
  assert.ok(!html.includes('judge'), 'judge нельзя выбирать через model');
});

test('F3: wire спрашивает подтверждение перед stop', () => {
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(shell.includes('confirm('), 'stop спрашивает подтверждение в обработчике');
});

test('F4: подписка без exportKey, ключ — из JWK', () => {
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(!shell.includes('exportKey'), 'getKey возвращает буфер, exportKey нет');
  assert.ok(shell.includes("getKey('p256dh')"), 'ключи читаются напрямую');
  assert.ok(shell.includes('applicationServerKey'), 'ключ передаётся в subscribe');
});

test('F5: кнопка пушей привязана независимо от вопроса', () => {
  const shell = readFileSync(SHELL, 'utf8');
  const wireAt = shell.indexOf('function wire(');
  const pushAt = shell.indexOf("getElementById('push-btn')");
  assert.ok(wireAt >= 0 && pushAt > wireAt, 'кнопка пушей привязана в wire');
  const noQ = renderPage({}, { token: 't' });
  assert.ok(noQ.includes('id="push-btn"'), 'кнопка видна и без вопроса');
});

test('F6: код из ссылки попадает в манифест и обмен', () => {
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(shell.includes("get('code')"), 'код читается из query');
  assert.ok(shell.includes('/manifest.webmanifest?code='), 'манифест хранит код в start_url');
  assert.ok(shell.includes('/pair/exchange'), 'код обменивается автоматически');
});

test('F7: filterState пропускает view.log, итог и историю', async () => {
  const { filterState } = await import('../src/filter.js');
  const out = filterState({
    run_id: 'r',
    machine_id: 'm',
    view: { log: [{ ts: '2026-10-27T01:00:00Z', role: 'judge', type: 'text', text: 'вопрос 3-1' }] },
    answered: { by: 'human', action: 'more' },
    history: [{ phase: '1/8', verdict: 'SHIP' }],
  });
  assert.ok(Array.isArray(out.view.log) && out.view.log.length === 1, 'журнал проходит фильтр');
  assert.ok(out.view.log[0].text.includes('вопрос 3-1'), 'текст события цел');
  assert.ok(out.answered && out.history, 'итог и история проходят фильтр');
});

test('F8: пустое состояние — не «уже отвечено»', () => {
  const html = renderPage({}, { token: 't' });
  assert.ok(!html.includes('уже отвечено'), 'без вопроса и ответов итога нет');
});

test('F13: без текстовых ответов нет кнопки answer', () => {
  const html = renderPage(
    { phone_text_answers: false, question: { id: 'q', text: 'need input', actions: ['answer', 'skip', 'stop'] } },
    { token: 't' },
  );
  assert.ok(!html.includes('data-action="answer"'), 'answer без текста шлёт пустой ответ');
  assert.ok(!html.includes('<textarea'), 'текстового поля быть не должно');
});

test('F9: выбор модели уходит ролью player', () => {
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(shell.includes('player ${model}') || shell.includes('player " + model'), 'model шлётся как player <спека>');
});

test('F10: vapid JWK превращается в 65 байт', () => {
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(shell.includes('.x') && shell.includes('.y'), 'JWK x/y разбираются в несжатый ключ');
});

test('F11: код из пути /pair/<code> тоже подбирается', () => {
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(shell.includes('pathname'), 'старые ссылки /pair/<code> ведут на пульт');
});

test('F16: без текстовых ответов model не рендерится', () => {
  const html = renderPage(
    { phone_text_answers: false, question: { id: 'q', text: 't', actions: ['model', 'skip', 'stop'] }, models: ['codex:gpt-5'] },
    { token: 't' },
  );
  assert.ok(!html.includes('data-action="model"'), 'model без текста шлёт текст — убрать');
  assert.ok(!html.includes('<textarea'), 'текстового поля быть не должно');
});

test('F17: опрос каждые 15с не стирает черновик ответа', () => {
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(shell.includes('captureDraft'), 'черновик должен сниматься перед перерисовкой');
  assert.ok(shell.includes('restoreDraft'), 'черновик должен восстанавливаться после перерисовки');
  const renderAt = shell.indexOf('top.innerHTML = renderTop(state)');
  const captureAt = shell.lastIndexOf('captureDraft(', renderAt);
  assert.ok(captureAt >= 0 && captureAt < renderAt, 'черновик снимается перед заменой innerHTML');
  const wireAt = shell.indexOf('wire(state)');
  const restoreAt = shell.indexOf('restoreDraft(', wireAt);
  assert.ok(restoreAt > wireAt, 'черновик восстанавливается после wire, а не до');
});

test('F14: код обменивается только в PWA, Safari код бережёт', () => {
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(shell.includes('display-mode') || shell.includes('standalone'), 'PWA-режим определяется');
  const loadAt = shell.indexOf('async function load(');
  const pairAt = shell.indexOf('/pair/exchange');
  assert.ok(loadAt >= 0 && pairAt > loadAt, 'обмен — из загрузки');
});

function fakeSection(doc, qid, text, model) {
  // Минимальный DOM: #question с .qid, textarea и select — как после renderPage.
  const make = (name, value) => ({
    value,
    getAttribute: (k) => (k === 'name' ? name : null),
    focus() { doc.activeElement = this; },
  });
  return {
    qidEl: { textContent: qid },
    textEl: make('text', text),
    modelEl: make('model', model),
    querySelector(sel) {
      if (sel === '.qid') return this.qidEl;
      if (sel.startsWith('textarea')) return this.textEl;
      if (sel.startsWith('select')) return this.modelEl;
      return null;
    },
    contains(el) { return el === this.textEl || el === this.modelEl; },
  };
}

function fakeDoc(qid, text, model) {
  const doc = { activeElement: null, getElementById: (id) => (id === 'question' ? doc.section : null) };
  doc.section = fakeSection(doc, qid, text, model);
  return doc;
}

test('F17: черновик и выбор модели переживают перерисовку опроса', () => {
  const doc = fakeDoc('q1', 'мой черновик', 'codex:gpt-5');
  doc.section.textEl.focus();
  const draft = captureDraft(doc);
  // перерисовка: свежие пустые поля того же вопроса
  doc.section = fakeSection(doc, 'q1', '', '');
  restoreDraft(doc, draft);
  assert.equal(doc.section.textEl.value, 'мой черновик');
  assert.equal(doc.section.modelEl.value, 'codex:gpt-5');
  assert.equal(doc.activeElement, doc.section.textEl);
});

test('F17: черновик другого вопроса не переносится', () => {
  const doc = fakeDoc('q1', 'старый ответ', '');
  const draft = captureDraft(doc);
  doc.section = fakeSection(doc, 'q2', '', '');
  restoreDraft(doc, draft);
  assert.equal(doc.section.textEl.value, '');
});

test('пустой или отсутствующий actions — никаких запасных кнопок', () => {
  for (const actions of [undefined, []]) {
    const html = renderPage({ question: { id: 'q', text: 't', actions } }, { token: 't' });
    assert.ok(!html.includes('data-action='), `кнопок быть не должно: ${html}`);
    assert.ok(!html.includes('<textarea'), 'без действий текст отправить некуда');
  }
});

test('B17: подписка на чужой хост → 400; чужую живую не перезаписать; revoke удаляет', async () => {
  const env = await setupEnv();
  const other = 'device-token-2';
  await env.DB.prepare(
    'INSERT INTO devices (token_hash, kind, label, device_id, revoked, created_at) VALUES (?, ?, ?, ?, 0, ?)',
  ).bind(await sha256Hex(other), 'device', 'ipad', 'ipad-id', '').run();
  const sub = (endpoint) => ({ endpoint, p256dh: 'abc', auth: 'def' });
  const post = (token, body) => handleFetch(request('POST', '/push/subscribe', { token, body }), env);
  assert.equal((await post(DEVICE_TOKEN, sub('https://attacker.example/x'))).status, 400);
  const ep = 'https://fcm.googleapis.com/fcm/send/abc';
  assert.equal((await post(DEVICE_TOKEN, sub(ep))).status, 200);
  assert.equal((await post(other, sub(ep))).status, 409);
  const owner = await env.DB.prepare('SELECT device_id FROM push_subs WHERE endpoint = ?').bind(ep).first();
  assert.equal(owner.device_id, 'device-id');
  const revoked = await handleFetch(
    request('POST', '/pair/revoke', { token: WRITE_KEY, body: { device_id: 'device-id' } }), env,
  );
  assert.equal(revoked.status, 200);
  const left = await env.DB.prepare('SELECT COUNT(*) AS n FROM push_subs').first();
  assert.equal(Number(left.n), 0);
  assert.equal((await post(other, sub(ep))).status, 200);
});

function memStorage(init = {}) {
  const data = { ...init };
  return { data, getItem: (k) => (k in data ? data[k] : null), setItem: (k, v) => { data[k] = String(v); } };
}

test('B18: токен из ссылки — только из фрагмента, стирается, не подменяет tero_token', () => {
  const replaced = [];
  const hist = { replaceState: (_s, _t, url) => replaced.push(url) };
  const own = memStorage({ tero_token: 'DEVICE' });
  assert.equal(tokenFromLink(own, { hash: '#token=EVIL', pathname: '/', search: '' }, hist), 'DEVICE');
  assert.equal(own.data.tero_token, 'DEVICE');
  assert.equal(replaced[0], '/');
  const fresh = memStorage();
  assert.equal(tokenFromLink(fresh, { hash: '#token=READ', pathname: '/', search: '?run_id=r1' }, hist), 'READ');
  assert.equal(fresh.data.tero_read_token, 'READ');
  assert.ok(!('tero_token' in fresh.data));
  assert.equal(replaced[1], '/?run_id=r1');
  assert.equal(tokenFromLink(memStorage(), { hash: '', pathname: '/', search: '?token=Q' }, hist), '');
  const shell = readFileSync(SHELL, 'utf8');
  assert.ok(shell.includes('tokenFromLink(localStorage, location, history)'));
});

const VIEW = {
  light: 'green', light_text: 'работает', sectors: [{ title: 'a', color: 'yellow' }], role: 'player',
  role_line: 'player · opus', doing: 'пишу код', last_event_at: 1000, built_at: 1010, quiet_after_s: 600,
  log: [{ ts: 1, role: 'player', type: 'text', text: 'x' }], stop_text: '', done_text: '',
};

test('phoneLight: терминальный как есть, мёртвый владелец, старые данные, тихо', () => {
  assert.deepEqual(phoneLight({ ...VIEW, light: 'ready', light_text: 'сдано', done_text: '✓ сдано' },
    { owner_alive: false, updated_at: '2000-01-01T00:00:00Z' }, 5000), { light: 'ready', light_text: 'сдано' });
  assert.deepEqual(phoneLight({ ...VIEW, light: 'red', light_text: 'стоп', stop_text: 'стоп' },
    { updated_at: '2000-01-01T00:00:00Z' }, 5000), { light: 'red', light_text: 'стоп' });
  const dead = phoneLight(VIEW, { owner_alive: false, owner_checked_at: 1100 }, 1200);
  assert.equal(dead.light, 'red');
  assert.match(dead.light_text, /^оборвался · проверено в \d\d:\d\d$/);
  const now = 1_800_000_000;
  const old = new Date((now - 700) * 1000).toISOString();
  assert.deepEqual(phoneLight(VIEW, { updated_at: old }, now), { light: 'grey', light_text: 'нет свежих данных' });
  const fresh = new Date((now - 5) * 1000).toISOString();
  assert.deepEqual(phoneLight({ ...VIEW, last_event_at: now - 900 }, { updated_at: fresh }, now),
    { light: 'yellow', light_text: 'тихо 15 мин' });
  assert.deepEqual(phoneLight({ ...VIEW, last_event_at: now - 30 }, { updated_at: fresh }, now),
    { light: 'green', light_text: 'работает' });
  // yellow stays yellow: the quiet rule is for green only
  assert.deepEqual(phoneLight({ ...VIEW, light: 'yellow', light_text: 'ждёт тебя', last_event_at: now - 900 },
    { updated_at: fresh }, now), { light: 'yellow', light_text: 'ждёт тебя' });
});

test('viewOf: модель как есть плюс лампочка; без view — серая карточка', () => {
  const now = 1_800_000_000;
  const body = { state: { updated_at: new Date((now - 1) * 1000).toISOString(), view: { ...VIEW, last_event_at: now - 3 } }, marks: {} };
  const v = viewOf(body, now);
  assert.equal(v.doing, 'пишу код');
  assert.equal(v.plus, '');
  assert.equal(v.visible, true);
  assert.equal(v.light, 'green');
  const grey = viewOf({ state: { status: 'running' } }, now);
  assert.equal(grey.light, 'grey');
  assert.deepEqual(grey.log, []);
});

test('renderBelow показывает непрочитанные сообщения хозяину', () => {
  const html = renderBelow({ status: 'running', view: { messages_waiting: 2 } }, {});
  assert.ok(html.includes('непрочитанных сообщений хозяину: 2'));
  assert.ok(!renderBelow({ status: 'running', view: { messages_waiting: 0 } }, {}).includes('непрочитанных'));
});

test('renderQuestion экранирует текст вопроса', () => {
  const html = renderQuestion({ id: 'q', text: '<img src=x onerror=alert(1)>', actions: ['skip'] }, true, []);
  assert.ok(!html.includes('<img'));
  assert.ok(html.includes('&lt;img'));
});
