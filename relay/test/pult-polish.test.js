import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

import {
  ANSWER_DELAY_MS, ageClass, ageText, connText, pendingStep, pickRun, questionKey,
  renderPending, restoreDraft, captureDraft, runSummary,
} from '../public/pult.js';

test('возраст данных словами и цветом', () => {
  assert.equal(ageText(2), 'обновлено только что');
  assert.equal(ageText(40), 'обновлено 40 с назад');
  assert.equal(ageText(180), 'обновлено 3 мин назад');
  assert.equal(ageText(7300), 'обновлено 2 ч назад');
  assert.equal(ageClass(30), '');
  assert.equal(ageClass(200), 'warn');
  assert.equal(ageClass(301), 'bad');
});

test('связь: одна неудача — не обрыв, две — плашка; без удач — без времени; 4xx — код', () => {
  const now = new Date(2026, 10, 12, 14, 30).getTime();
  const ok = new Date(2026, 10, 12, 14, 8).getTime();
  assert.equal(connText(0, ok, now), '');
  assert.equal(connText(1, ok, now), '', 'медленный запрос не мигает плашкой');
  assert.equal(connText(2, ok, now), 'нет связи с релеем · с 14:08');
  assert.equal(connText(3, 0, now), 'нет связи с релеем');
  assert.equal(connText(2, ok, now, 404), 'релей ответил 404 · с 14:08');
  assert.equal(connText(2, ok, now, 503), 'нет связи с релеем · с 14:08');
});

test('выбор прогона: ждущий ответа > живой > первый; failed — законченный', () => {
  const runs = [
    { run_id: 'a', status: 'failed' }, { run_id: 'b', status: 'running' },
    { run_id: 'c', status: 'waiting_human' }, { run_id: 'd', status: 'done' },
  ];
  assert.equal(pickRun(runs).run_id, 'c');
  assert.equal(pickRun(runs.filter((r) => r.run_id !== 'c')).run_id, 'b');
  assert.equal(pickRun([{ run_id: 'a', status: 'failed' }, { run_id: 'd', status: 'done' }]).run_id, 'a');
  assert.deepEqual(pickRun([]), {});
  assert.deepEqual(pickRun(null), {});
});

test('итог прогона: числа из секторов, без секторов — долг из debts, живой — пусто', () => {
  const sec = (c, n) => Array.from({ length: n }, () => ({ color: c }));
  const done = { status: 'done', view: { sectors: [...sec('green', 22), ...sec('orange', 1)] } };
  assert.equal(runSummary(done), 'сдано 22 из 23 · долг 1 · готово');
  const stopped = { status: 'stopped', debts: [{}, {}], view: { stop_text: 'user_stop at phase 3' } };
  assert.equal(runSummary(stopped), 'долг 2 · остановлен вручную');
  assert.equal(runSummary({ status: 'failed' }), 'упал');
  assert.equal(runSummary({ status: 'running', view: { sectors: sec('green', 3) } }), '');
});

const Q = { run_id: 'r', machine_id: 'm', question: { id: 'q1', text: 't', actions: ['more', 'stop'] } };

test('отсчёт: двойной тап — один ответ; send ровно один раз на дедлайне', () => {
  const key = questionKey(Q);
  let r = pendingStep(null, { type: 'tap', action: 'stop', key }, 1000);
  assert.ok(r.pending && !r.send);
  const first = r.pending;
  r = pendingStep(first, { type: 'tap', action: 'more', key }, 1500);
  assert.equal(r.pending, first, 'второй тап не меняет и не перезапускает отсчёт');
  r = pendingStep(first, { type: 'tick' }, 1000 + ANSWER_DELAY_MS - 1);
  assert.ok(r.pending && !r.send, 'до дедлайна не шлём');
  r = pendingStep(first, { type: 'tick' }, 1000 + ANSWER_DELAY_MS);
  assert.equal(r.pending, null);
  assert.equal(r.send.action, 'stop');
  assert.equal(pendingStep(r.pending, { type: 'tick' }, 99999).send, undefined, 'второго send нет');
});

test('отсчёт: отмена, свёрнутая вкладка, проспанный дедлайн и смена вопроса — без send', () => {
  const key = questionKey(Q);
  const p = pendingStep(null, { type: 'tap', action: 'skip', key }, 0).pending;
  const cancel = pendingStep(p, { type: 'cancel' }, 100);
  assert.equal(cancel.pending, null);
  assert.equal(pendingStep(cancel.pending, { type: 'tick' }, ANSWER_DELAY_MS).send, undefined);
  const hidden = pendingStep(p, { type: 'tick', hidden: true }, ANSWER_DELAY_MS);
  assert.ok(!hidden.send && hidden.pending === null && /свёрнута/.test(hidden.note));
  const slept = pendingStep(p, { type: 'tick' }, ANSWER_DELAY_MS + 60000);
  assert.ok(!slept.send && slept.pending === null, 'телефон спал — не шлём задним числом');
  const other = questionKey({ ...Q, question: { ...Q.question, id: 'q2' } });
  const changed = pendingStep(p, { type: 'state', key: other }, 200);
  assert.ok(!changed.send && changed.pending === null && /закрыт/.test(changed.note));
  assert.equal(pendingStep(p, { type: 'state', key }, 200).pending, p, 'тот же вопрос — отсчёт живёт');
  assert.equal(pendingStep(null, { type: 'tap', action: 'more', key: '' }, 0).pending, null,
    'нет вопроса — нечего ставить в отсчёт');
});

test('ключ вопроса различает прогон, машину, текст и действия', () => {
  const k = questionKey(Q);
  assert.notEqual(k, questionKey({ ...Q, run_id: 'r2' }));
  assert.notEqual(k, questionKey({ ...Q, machine_id: 'm2' }));
  assert.notEqual(k, questionKey({ ...Q, question: { ...Q.question, actions: ['more'] } }));
  assert.equal(questionKey({ run_id: 'r' }), '');
  assert.match(renderPending({ action: 'skip', deadline: 5000 }, 1200), /«пропустить фазу» через 4 с/);
});

test('черновик: курсор в поле ответа переживает перерисовку', () => {
  const doc = { activeElement: null };
  const ta = {
    value: 'привет мир', selectionStart: 3, selectionEnd: 5,
    getAttribute: (k) => (k === 'name' ? 'text' : null),
    focus() { doc.activeElement = this; },
    setSelectionRange(a, b) { this.selectionStart = a; this.selectionEnd = b; },
  };
  const section = {
    querySelector: (sel) => (sel === '.qid' ? { textContent: 'q1' } : sel.startsWith('textarea') ? ta : null),
    contains: (el) => el === ta,
  };
  doc.getElementById = (id) => (id === 'question' ? section : null);
  doc.activeElement = ta;
  const draft = captureDraft(doc);
  ta.selectionStart = 10; ta.selectionEnd = 10; doc.activeElement = null;
  restoreDraft(doc, draft);
  assert.deepEqual([ta.selectionStart, ta.selectionEnd], [3, 5]);
  assert.equal(doc.activeElement, ta);
});

function runSw(windows) {
  const handlers = {};
  const calls = [];
  const ctx = {
    self: {
      location: { origin: 'https://relay.example' },
      addEventListener: (name, fn) => { handlers[name] = fn; },
      registration: { showNotification: (title, opts) => calls.push(['show', title, opts.body]) },
    },
    clients: {
      matchAll: async () => windows,
      openWindow: async (url) => calls.push(['open', url]),
    },
    URL,
  };
  vm.runInNewContext(readFileSync(new URL('../public/sw.js', import.meta.url), 'utf8'), ctx);
  return { handlers, calls };
}

test('sw: текстовый пуш как раньше; тап поднимает открытый пульт, иначе открывает корень', async () => {
  const { handlers, calls } = runSw([]);
  let waited;
  handlers.push({ data: { text: () => 'tero: нужен ответ' }, waitUntil: (p) => { waited = p; } });
  await waited;
  assert.deepEqual(calls[0], ['show', 'теро', 'tero: нужен ответ']);
  handlers.notificationclick({ notification: { close() {} }, waitUntil: (p) => { waited = p; } });
  await waited;
  assert.deepEqual(calls[1], ['open', '/']);

  const focused = [];
  const win = { url: 'https://relay.example/?run_id=x', focus: async () => focused.push(1) };
  const other = { url: 'https://evil.example/', focus: async () => focused.push(2) };
  const sw2 = runSw([other, win]);
  sw2.handlers.notificationclick({ notification: { close() {} }, waitUntil: (p) => { waited = p; } });
  await waited;
  assert.deepEqual(focused, [1], 'фокус только своего окна');
  assert.equal(sw2.calls.length, 0, 'новое окно не открывается');

  const jobsFocused = [];
  const jobsWin = { url: 'https://relay.example/jobs.html', focus: async () => jobsFocused.push(1) };
  const sw3 = runSw([jobsWin]);
  sw3.handlers.notificationclick({ notification: { close() {} }, waitUntil: (p) => { waited = p; } });
  await waited;
  assert.deepEqual(jobsFocused, [1], 'обычный пуш поднимает любое своё окно, даже /jobs.html');
  assert.equal(sw3.calls.length, 0);
});

test('sw: job push opens the jobs page', async () => {
  const { handlers, calls } = runSw([]);
  let waited;
  handlers.notificationclick({
    notification: { body: 'работа: QA at X', close() {} },
    waitUntil: (p) => { waited = p; },
  });
  await waited;
  assert.deepEqual(calls[0], ['open', '/jobs.html']);

  const pult = { url: 'https://relay.example/', focus: async () => {} };
  const sw2 = runSw([pult]);
  sw2.handlers.notificationclick({
    notification: { body: 'работа: QA at X', close() {} },
    waitUntil: (p) => { waited = p; },
  });
  await waited;
  assert.deepEqual(sw2.calls[0], ['open', '/jobs.html'], 'открытый пульт на / не считается');

  const focused = [];
  const jobsWin = { url: 'https://relay.example/jobs.html', focus: async () => focused.push(1) };
  const sw3 = runSw([jobsWin]);
  sw3.handlers.notificationclick({
    notification: { body: 'работа: QA at X', close() {} },
    waitUntil: (p) => { waited = p; },
  });
  await waited;
  assert.deepEqual(focused, [1]);
  assert.equal(sw3.calls.length, 0);
});
