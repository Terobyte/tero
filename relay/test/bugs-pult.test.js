// Тесты багхантa 2026-11-12 (bugs.md): чистые функции pult.js. Красные, пока баги не починены.
import test from 'node:test';
import assert from 'node:assert/strict';
import { renderBelow, renderPending } from '../public/pult.js';

test('B02: failed-прогон в клиенте — в «закончены», а не в «прогоны»', () => {
  const runs = [
    { run_id: '20261110-090000000000-a', machine_id: 'm', status: 'failed', updated_at: '2026-11-10T09:00:00Z' },
    { run_id: '20261112-090000000000-b', machine_id: 'm', status: 'running', updated_at: '2026-11-12T09:00:00Z' },
  ];
  const html = renderBelow({ run_id: 'x', status: 'running' }, { runs });
  const sec = (id) => (html.match(new RegExp(`<section id="${id}">[\\s\\S]*?</section>`)) || [''])[0];
  assert.ok(!/data-run="20261110-090000000000-a"/.test(sec('runs')),
    'failed-прогон не должен быть в секции «прогоны» (живые), а он там');
  assert.ok(/data-run="20261110-090000000000-a"/.test(sec('stopped')),
    'failed-прогон должен быть в секции «закончены»');
});

test('B05: renderPending рисует #pending-left, чтобы отсчёт мог тикать', () => {
  const html = renderPending({ action: 'stop', deadline: 10000 }, 5000);
  assert.ok(/id="pending-left"/.test(html),
    `drawPending ищет #pending-left, а разметка его не содержит: ${html}`);
});
