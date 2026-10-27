// relay/bench.js — 1000 POST /state на фейке, среднее CPU. Критерий: < 10 мс на запрос.
import { handleFetch } from './src/worker.js';
import { openFakeDb } from './test/fake_d1.js';

const N = 1000;
const LIMIT_MS = 10;

const env = { DB: openFakeDb(), RELAY_WRITE_KEY: 'bench-key' };

function stateRequest(i) {
  return new Request('https://relay.test/state', {
    method: 'POST',
    headers: { 'content-type': 'application/json', authorization: 'Bearer bench-key' },
    body: JSON.stringify({
      run_id: `run-${i % 10}`,
      machine_id: 'bench',
      status: 'running',
      phase: `phase ${i}`,
      round: i,
      question: { id: `q-${i}`, text: 'идём?', actions: ['more', 'skip'] },
      findings: [{ title: 'red', path: 'src/a.py', test: 'test_a', text: 'boom' }],
    }),
  });
}

const cpuStart = process.cpuUsage();
for (let i = 0; i < N; i++) {
  const res = await handleFetch(stateRequest(i), env);
  if (res.status !== 200) {
    console.error(`bench: POST /state #${i} → ${res.status}`);
    process.exit(1);
  }
  await res.text();
}
const cpu = process.cpuUsage(cpuStart);
const avg = (cpu.user + cpu.system) / 1000 / N;
console.log(`bench: ${N} POST /state, среднее CPU ${avg.toFixed(3)} мс/запрос (лимит ${LIMIT_MS})`);
if (avg >= LIMIT_MS) {
  console.error('bench: СЛИШКОМ МЕДЛЕННО');
  process.exit(1);
}
