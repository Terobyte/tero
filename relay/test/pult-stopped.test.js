import test from 'node:test';
import assert from 'node:assert/strict';

import { renderBelow } from '../public/pult.js';

const LIVE = { status: 'running', phase: 'p1', round: 1, role: 'player', model: 'm' };

test('список «остановлены»: терминальные прогоны отдельно от живых', () => {
  const html = renderBelow(LIVE, { runs: [
    { run_id: 'live1', machine_id: 'm', status: 'running' },
    { run_id: 'old1', machine_id: 'm', status: 'stopped', updated_at: '2026-10-20T00:00:00.000Z' },
    { run_id: 'old2', machine_id: 'm', status: 'done', updated_at: '2026-10-21T00:00:00.000Z' },
  ] });
  assert.match(html, /остановлены/);
  assert.match(html, /old1/);
  assert.match(html, /old2/);
  const liveSection = html.split('остановлены')[0];
  assert.doesNotMatch(liveSection, /old1/);
  assert.doesNotMatch(liveSection, /old2/);
  assert.match(liveSection, /live1/);
});
