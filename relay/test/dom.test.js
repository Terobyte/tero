// relay/test/dom.test.js — render() карточки из src/hud/view.html на поддельном DOM
// (спек пульта 2, п. 6). Скрипт между /*render:start*/ и /*render:end*/ один и тот же
// на маке и на телефоне; здесь он гоняется в node без браузера.
import test from 'node:test';
import assert from 'node:assert/strict';
import { readFileSync } from 'node:fs';
import vm from 'node:vm';

const HTML = readFileSync(new URL('../../src/hud/view.html', import.meta.url), 'utf8');

function renderScript() {
  const start = HTML.indexOf('/*render:start*/');
  const end = HTML.indexOf('/*render:end*/');
  assert.ok(start > 0 && end > start, 'маркеры render:start / render:end в view.html');
  return HTML.slice(start, end);
}

class FakeEl {
  constructor(tag) {
    this.tag = tag;
    this.children = [];
    this.className = '';
    this.textContent = '';
    this.title = '';
    this.style = {};
    this.scrollTop = 0;
    this.scrollHeight = 0;
    this.clientHeight = 0;
    this.classList = { toggle: () => {} };
  }

  set innerHTML(v) { if (v === '') this.children = []; }

  get innerHTML() { return ''; }

  appendChild(child) {
    if (child instanceof Fragment) this.children.push(...child.children);
    else this.children.push(child);
    return child;
  }

  contains() { return false; }
}

class Fragment {
  constructor() { this.children = []; }

  appendChild(child) { this.children.push(child); return child; }
}

function fakeDocument() {
  const ids = ['light', 'close', 'status', 'phases', 'plan', 'model', 'meta', 'age', 'who', 'foot', 'doing', 'plus', 'log'];
  const byId = Object.fromEntries(ids.map((id) => [id, new FakeEl('div')]));
  return {
    byId,
    getElementById: (id) => byId[id] || null,
    createElement: (tag) => new FakeEl(tag),
    createDocumentFragment: () => new Fragment(),
  };
}

function load(view, nowS, teroSource = null) {
  const document = fakeDocument();
  const ctx = {
    document,
    window: { teroNow: () => nowS },
    JSON,
    Math,
    String,
    Date,
    // the typing animation runs to the end at once
    setInterval: (fn) => { for (let i = 0; i < 400; i++) fn(); return 1; },
    clearInterval: () => {},
    console,
  };
  ctx.window.teroSource = teroSource;
  vm.createContext(ctx);
  vm.runInContext(renderScript(), ctx);
  vm.runInContext('render(__view)', Object.assign(ctx, { __view: view }));
  return document.byId;
}

const VIEW = {
  light: 'yellow', light_text: 'ждёт тебя', role: 'judge', role_line: 'judge · gpt-5',
  sectors: [{ title: 'a', color: 'green' }, { title: 'b', color: 'yellow' }, { title: 'c', color: 'grey' }],
  phase_title: 'b', phase_index: 1, total_phases: 3, round: 2, phase_started_at: 1000 - 125,
  doing: 'идём дальше?', gate_started_at: null, last_event_at: 1000 - 42,
  verdict: { verdict: 'NO_SHIP', blocking: 1, first: 'src/x.py:3' }, verdict_at: 1000 - 300,
  stop_text: '', done_text: '', debt_line: '', agent: { on_duty: true }, messages_waiting: 0, resume: null,
  log: [{ ts: 1, role: 'player', type: 'text', text: 'пишу код' }, { ts: 2, role: 'judge', type: 'tool', text: 'Read a.py' },
    { ts: 3, role: 'host', type: 'text', text: 'смотрю статус' }],
  built_at: 1000, quiet_after_s: 600, plus: '+1',
};

test('render(view) при now = built_at: лампочка, статус, фазы, модель, время, возраст, плашка, лог', () => {
  const el = load(VIEW, VIEW.built_at);
  assert.equal(el.light.className, 'yellow');
  assert.equal(el.status.textContent, 'ждёт тебя');
  assert.equal(el.phases.children.length, 4, 'три точки и имя текущей фазы');
  assert.equal(el.phases.children[3].textContent, 'b');
  assert.equal(el.model.textContent, 'gpt-5');
  assert.equal(el.model.className, 'r-judge');
  assert.equal(el.meta.textContent, 'р2 · 02:05');
  assert.equal(el.age.textContent, '42 с');
  assert.equal(el.age.className, 'age-normal');
  // the verdict is 5 min old and the judge acts: the plaque shows the judge's line
  assert.equal(el.who.textContent, 'судья');
  assert.equal(el.doing.textContent, 'идём дальше?');
  assert.equal(el.foot.className, 'r-judge');
  assert.deepEqual(el.log.children.map((c) => c.textContent), ['пишу код', 'Read a.py', 'смотрю статус']);
  assert.deepEqual(el.log.children.map((c) => c.className), ['r-player', 'r-judge tool', 'r-host']);
  assert.equal(el.plus.textContent, '+1');
  assert.equal(el.close.style.display, 'none');
  assert.equal(el.plan.children.length, 3);
  assert.equal(el.plan.children[1].children[3].textContent, 'идёт · раунд 2');
});

test('возраст и таймер гейта считаются от now, не от built_at', () => {
  const later = load(VIEW, VIEW.built_at + 400);
  assert.equal(later.age.textContent, '7 мин');
  assert.equal(later.age.className, 'age-red');
  assert.equal(later.meta.textContent, 'р2 · 08:45');
  const gate = load({ ...VIEW, role: 'gate', role_line: 'gate', light: 'green', light_text: 'работает',
    verdict: {}, verdict_at: null, doing: '⏱ тесты идут 0:10', gate_started_at: 990 }, 1161);
  assert.equal(gate.doing.textContent, '⏱ тесты идут 2:51');
  assert.equal(gate.status.textContent, '', 'зелёная лампочка без слов');
});

test('свежий вердикт, стоп и ready перекрывают плашку; ready показывает крестик только на маке', () => {
  const fresh = load({ ...VIEW, verdict_at: VIEW.built_at - 10 }, VIEW.built_at);
  assert.equal(fresh.doing.textContent, 'NO_SHIP · блокеров 1 · src/x.py:3');
  assert.equal(fresh.foot.className, 'bad');
  const stopped = load({ ...VIEW, light: 'red', light_text: 'стоп', stop_text: 'застрял · агента нет' }, VIEW.built_at);
  assert.equal(stopped.doing.textContent, 'застрял · агента нет');
  assert.equal(stopped.who.style.display, 'none');
  const ready = load({ ...VIEW, light: 'ready', light_text: 'сдано', done_text: '✓ сдано', verdict: {}, verdict_at: null }, VIEW.built_at);
  assert.equal(ready.close.style.display, 'flex');
  assert.equal(ready.doing.textContent, '✓ сдано');
  assert.equal(ready.foot.className, 'good');
});

test('телефон: с window.teroSource крестик не показывается даже при ready; render(null) ничего не трогает', () => {
  const source = { view: () => null, set: () => {} };
  const ready = load({ ...VIEW, light: 'ready', light_text: 'сдано', done_text: '✓ сдано', verdict: {}, verdict_at: null }, VIEW.built_at, source);
  assert.equal(ready.close.style.display, 'none');
  assert.equal(ready.doing.textContent, '✓ сдано');
  const empty = load(null, VIEW.built_at, source);
  assert.equal(empty.light.className, '');
  assert.equal(empty.log.children.length, 0);
  const grey = load({ light: 'grey', light_text: 'нет данных', doing: 'нет данных', sectors: [], log: [] }, 5, source);
  assert.equal(grey.light.className, 'grey');
  assert.equal(grey.status.textContent, 'нет данных');
  assert.equal(grey.doing.textContent, 'нет данных');
});
