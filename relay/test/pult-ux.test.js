import test from 'node:test';
import assert from 'node:assert/strict';

import { actionLabel, decisionRows, fmtWhen, renderBelow, renderQuestion, runLabel } from '../public/pult.js';

test('время: unix-секунды и ISO → часы, чужой день с датой, «10:00» как есть', () => {
  const at = new Date(2026, 10, 12, 14, 8).getTime();
  assert.equal(fmtWhen(at / 1000, at), '14:08');
  assert.equal(fmtWhen(String(at / 1000), at), '14:08');
  assert.equal(fmtWhen(new Date(2026, 9, 27, 9, 5).toISOString(), at), '27.10 09:05');
  assert.equal(fmtWhen('10:00', at), '10:00');
  assert.equal(fmtWhen(null, at), '');
});

test('имя прогона: без метки времени в начале, с датой; чужой формат — как есть', () => {
  assert.equal(runLabel('20261101-234135637839-2026-11-01-engine-worlds'), 'engine-worlds · 01.11 23:41');
  assert.equal(runLabel('r1'), 'r1');
});

test('решения: одинаковые строки схлопнуты в ×N, свежие сверху, fuse по-русски', () => {
  const wake = { kind: 'wake', reason: 'question', result: 'команды пробуждения нет' };
  const rows = decisionRows([
    { ...wake, at: 1 }, { who: 'autopilot', action: 'skip', why: 'fuse', ts: 2 },
    { ...wake, at: 3 }, { who: 'autopilot', action: 'skip', why: 'fuse', ts: 4 }, { ...wake, at: 5 },
  ]);
  assert.equal(rows.length, 2);
  assert.match(rows[0].text, /^пробуждение \(вопрос\)/);
  assert.equal(rows[0].n, 3);
  assert.equal(rows[1].text, 'автопилот: пропустить фазу (предохранитель)');
  assert.equal(rows[1].n, 2);
});

test('кнопки вопроса подписаны по-русски, команда в data-action прежняя', () => {
  const html = renderQuestion({ id: 'q', text: 't', actions: ['more', 'skip', 'model', 'stop'] }, true, ['m1']);
  for (const a of ['more', 'skip', 'model', 'stop']) assert.ok(html.includes(`data-action="${a}"`));
  assert.ok(html.includes('>ещё раунд<') && html.includes('>пропустить фазу<') && html.includes('>стоп<'));
  assert.ok(html.includes('placeholder='), 'у поля ответа есть подсказка');
  assert.equal(actionLabel('weird'), 'weird');
});

test('низ без дублей карточки: нет фазы/раунда/модели, пустые секции не рисуются', () => {
  const html = renderBelow({ status: 'running', phase: 'H21. гейт', round: 3, role: 'judge', model: 'gpt-x' }, {});
  assert.ok(!html.includes('H21') && !html.includes('gpt-x'), 'фаза и модель — только в шапке карточки');
  assert.ok(!html.includes('id="decisions"') && !html.includes('id="debt"'), 'пустые секции скрыты');
  assert.ok(!html.includes('<p>—</p>'), 'нет пустых прочерков');
  assert.ok(!html.includes('tero watch'), 'маковская подсказка на телефоне не нужна');
});

test('долг: находки свёрнуты под фазой', () => {
  const html = renderBelow({ status: 'done', debts: [{ phase: 15, reason: 'fuse', findings: ['F1: беда → как проверить'] }] }, {});
  assert.match(html, /<details><summary>фаза 15 — предохранитель · 1 находка<\/summary>/);
  assert.ok(html.includes('F1: беда') && !html.includes('как проверить'));
});
