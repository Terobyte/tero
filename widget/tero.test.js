import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';
import { layout, runWidget } from './tero.js';

const fake = () => ({
  phase: '2/5',
  round: 3,
  question: { id: 'q1' },
  limits: { codex: '40%' },
});

test('импорт без падения; layout по фиктивному состоянию даёт 3 строки', () => {
  for (const family of [undefined, 'lock']) {
    const rows = layout(fake(), family);
    assert.ok(Array.isArray(rows), 'layout возвращает массив строк');
    assert.equal(rows.length, 3, `семья ${family}: блокировка — 2–3 строки`);
    assert.ok(rows[0].includes('2/5'), 'первая строка — фаза');
  }
});

test('в файле нет строки, похожей на токен', () => {
  const src = readFileSync(new URL('./tero.js', import.meta.url), 'utf8');
  const shapes = [
    /sk-[A-Za-z0-9_-]{8,}/,
    /AKIA[0-9A-Z]{16}/,
    /eyJ[A-Za-z0-9_-]{10,}\.[A-Za-z0-9_-]{10,}/,
    /[0-9a-fA-F]{32,}/,
    /[A-Za-z0-9+/]{40,}={0,2}/,
  ];
  for (const re of shapes) {
    assert.ok(!re.test(src), `похоже на токен: ${re}`);
  }
  assert.ok(src.includes('widgetParameter'), 'токен читается из args.widgetParameter');
});

function mockWidgetEnv({ param, family } = {}) {
  class W {
    constructor() { this.rows = []; this.url = ''; }
    addText(s) { this.rows.push(s); }
  }
  globalThis.ListWidget = W;
  globalThis.args = { widgetParameter: param };
  if (family === undefined) delete globalThis.config;
  else globalThis.config = { widgetFamily: family };
  delete globalThis.Script;
  delete globalThis.Safari;
  return W;
}

function cleanupWidgetEnv() {
  delete globalThis.ListWidget;
  delete globalThis.args;
  delete globalThis.config;
  delete globalThis.Request;
  delete globalThis.Safari;
  delete globalThis.Script;
}

test('F8: accessoryRectangular — экран блокировки, не больше 3 строк', () => {
  const st = {
    phase_index: 1, total_phases: 5, round: 3, question: { id: 'q1' },
    role: 'player', model: 'm', limits: { codex: '40%' },
  };
  const rows = layout(st, 'accessoryRectangular');
  assert.ok(rows.length <= 3, `блокировка — 2–3 строки, было ${rows.length}`);
});

test('F1: runWidget показывает загруженное состояние, а не пустое', async () => {
  mockWidgetEnv({ param: 'READ|https://worker.example' });
  globalThis.Request = class {
    constructor(u) { this.url = u; this.headers = {}; }
    async loadJSON() {
      return { state: { phase_index: 1, total_phases: 5, round: 3, question: { id: 'q1' }, limits: { codex: '40%' } } };
    }
  };
  try {
    const w = await runWidget();
    assert.ok(w.rows.some((s) => s.includes('2/5')), `фаза из состояния, строки: ${JSON.stringify(w.rows)}`);
  } finally {
    cleanupWidgetEnv();
  }
});

test('F7: запрос /state идёт с run_id и machine_id', async () => {
  mockWidgetEnv({ param: 'READ|https://worker.example|r1|m1' });
  let seenUrl = '';
  globalThis.Request = class {
    constructor(u) { seenUrl = String(u); this.headers = {}; }
    async loadJSON() {
      const q = new URL(seenUrl).searchParams;
      if (!q.has('run_id') || !q.has('machine_id')) throw new Error('404');
      return { state: { phase_index: 1, total_phases: 5, round: 3, limits: { codex: '40%' } } };
    }
  };
  try {
    const w = await runWidget();
    const q = new URL(seenUrl).searchParams;
    assert.equal(q.get('run_id'), 'r1');
    assert.equal(q.get('machine_id'), 'm1');
    assert.ok(w.rows.some((s) => s.includes('2/5')), `фаза из состояния, строки: ${JSON.stringify(w.rows)}`);
  } finally {
    cleanupWidgetEnv();
  }
});

test('F11: известное значение лимита — с подписью «лимит»', () => {
  const rows = layout({ phase: '2/5', round: 3, limits: { codex: '40%' } }, 'lock');
  assert.equal(rows[2], 'лимит 40%');
});

test('F10: без run_id в параметре — discovery через /runs, затем /state с id', async () => {
  mockWidgetEnv({ param: 'READ|https://worker.example' });
  let stateUrl = '';
  globalThis.Request = class {
    constructor(u) { this.url = String(u); this.headers = {}; }
    async loadJSON() {
      if (this.url.endsWith('/runs')) {
        return { runs: [{ run_id: 'r1', machine_id: 'm1', status: 'running' }] };
      }
      stateUrl = this.url;
      const q = new URL(this.url).searchParams;
      if (!q.has('run_id') || !q.has('machine_id')) throw new Error('404');
      return { state: { phase_index: 1, total_phases: 5, round: 3, limits: { codex: '40%' } } };
    }
  };
  try {
    const w = await runWidget();
    const q = new URL(stateUrl).searchParams;
    assert.equal(q.get('run_id'), 'r1');
    assert.equal(q.get('machine_id'), 'm1');
    assert.ok(w.rows.some((s) => s.includes('2/5')), `фаза из состояния: ${JSON.stringify(w.rows)}`);
  } finally {
    cleanupWidgetEnv();
  }
});

test('F9: только токен в параметре — виджет пытается загрузить состояние', async () => {
  mockWidgetEnv({ param: 'READ' });
  globalThis.Request = class {
    constructor(u) { this.url = String(u); this.headers = {}; }
    async loadJSON() {
      return { state: { phase_index: 1, total_phases: 5, round: 3, limits: { codex: '40%' } } };
    }
  };
  try {
    const w = await runWidget();
    assert.ok(w.rows.some((s) => s.includes('2/5')), `фаза из состояния: ${JSON.stringify(w.rows)}`);
  } finally {
    cleanupWidgetEnv();
  }
});

test('F12: только токен в параметре — запрос и переход идут не на localhost', async () => {
  mockWidgetEnv({ param: 'READ' });
  globalThis.Safari = {};
  let seenUrl = '';
  globalThis.Request = class {
    constructor(u) { seenUrl = String(u); this.headers = {}; }
    async loadJSON() { return { runs: [] }; }
  };
  try {
    const w = await runWidget();
    assert.notEqual(new URL(seenUrl).hostname, 'localhost', `запрос ушёл на ${seenUrl}`);
    assert.notEqual(new URL(w.url).hostname, 'localhost', `переход ведёт на ${w.url}`);
  } finally {
    cleanupWidgetEnv();
  }
});

test('F13: вопрос на экране блокировки — ровно «⚠ ответ», без id', () => {
  const rows = layout({ phase: '2/5', round: 3, question: { id: 'q1' }, limits: { codex: '40%' } }, 'lock');
  assert.equal(rows[1], '⚠ ответ');
});

function runAsScriptable(param) {
  // Как в Scriptable: файл — скрипт без модулей, API — глобалы.
  const src = readFileSync(new URL('./tero.js', import.meta.url), 'utf8');
  const seen = { urls: [], set: null, completed: false };
  const ctx = {
    args: { widgetParameter: param },
    config: { widgetFamily: 'accessoryRectangular' },
    Safari: {},
    ListWidget: class { constructor() { this.rows = []; this.url = ''; } addText(t) { this.rows.push(t); } },
    Request: class {
      constructor(url) { seen.urls.push(url); this.url = url; }
      async loadJSON() {
        return this.url.endsWith('/runs')
          ? { runs: [{ run_id: 'r1', machine_id: 'm1' }] }
          : { state: { phase_index: 1, total_phases: 5, round: 3, question: { id: 'q1' } } };
      }
    },
    Script: { setWidget(w) { seen.set = w; }, complete() { seen.completed = true; } },
  };
  vm.runInNewContext(src, ctx);
  return seen;
}

test('Scriptable: файл без export, сам вызывает setWidget и complete', async () => {
  const src = readFileSync(new URL('./tero.js', import.meta.url), 'utf8');
  assert.ok(!/^\s*export\s/m.test(src), 'Scriptable не понимает export');
  const seen = runAsScriptable('READ');
  await new Promise((r) => setTimeout(r, 20));
  assert.ok(seen.set, 'Script.setWidget вызван');
  assert.ok(seen.completed, 'Script.complete вызван');
  assert.deepEqual(seen.set.rows, ['2/5 · р3', '⚠ ответ', 'лимит — неизвестно']);
});

test('Scriptable: без адреса в параметре — развёрнутый Worker, не localhost/.invalid', async () => {
  const seen = runAsScriptable('READ');
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(seen.urls[0], 'https://tero-relay.highskilltero.workers.dev/runs');
  assert.ok(seen.urls[1].startsWith('https://tero-relay.highskilltero.workers.dev/state?run_id=r1'));
  assert.equal(seen.set.url, 'https://tero-relay.highskilltero.workers.dev/#token=READ');
  assert.ok(!seen.set.url.includes('?token='), 'токен не в query: он ушёл бы на сервер');
  const custom = runAsScriptable('READ|https://my.example/');
  await new Promise((r) => setTimeout(r, 20));
  assert.equal(custom.urls[0], 'https://my.example/runs');
});
