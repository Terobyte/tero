import test from 'node:test';
import assert from 'node:assert/strict';
import { filterState, redactSecrets, sanitizeFinding } from '../src/filter.js';

const LONG_TOKEN = 'sk-abcdefghij1234567890klmnopqrstuvwxyzABCD';

test('токен посреди 500-символьной находки вырезан', () => {
  const pad = 'x'.repeat(235);
  const text = `${pad} ${LONG_TOKEN} ${pad}`.slice(0, 500);
  assert.equal(text.length, 500);
  const finding = sanitizeFinding({ title: 't', path: 'a/b.py', test: 'test_x', text });
  assert.ok(!finding.text.includes(LONG_TOKEN));
  assert.ok(finding.text.includes('[redacted]'));
  assert.ok(finding.text.length <= 500);
});

test('относительный путь цел', () => {
  const finding = sanitizeFinding({
    title: 'boom',
    path: 'src/loop/phase.py',
    test: 'test_ship',
    text: 'plain',
  });
  assert.equal(finding.path, 'src/loop/phase.py');
});

test('блок ``` и diff --git отброшены', () => {
  const text = [
    'header line',
    '```python',
    'secret = "hunter2hunter2hunter2hunter2hunter2"',
    '```',
    'diff --git a/src/x.py b/src/x.py',
    'index 111..222 100644',
    '--- a/src/x.py',
    '+++ b/src/x.py',
    '@@ -1 +1 @@',
    '-old',
    '+new',
    'trailer line',
  ].join('\n');
  const finding = sanitizeFinding({ title: 't', path: 'p', test: 't', text });
  assert.ok(finding.text.includes('header line'));
  assert.ok(finding.text.includes('trailer line'));
  assert.ok(!finding.text.includes('hunter2'));
  assert.ok(!finding.text.includes('diff --git'));
  assert.ok(!finding.text.includes('-old'));
});

test('дифф принимает текст обратно только по чистому маркеру', () => {
  // diff --git без хедера: строка '-old' остаётся — фильтр гасит только стройный diff.
  const plain = sanitizeFinding({ title: 't', path: 'p', test: 'x', text: '-old\n-trailer' });
  assert.ok(plain.text.includes('-old'));
});

test('секреты в любом месте строки; путь секрета не утекает целиком', () => {
  assert.ok(!redactSecrets('key AKIAIOSFODNN7EXAMPLE here').includes('AKIAIOSFODNN7EXAMPLE'));
  assert.ok(!redactSecrets('-----BEGIN PRIVATE KEY-----\nabc\n-----END PRIVATE KEY-----').includes('BEGIN'));
  const hex = 'a'.repeat(40);
  assert.equal(redactSecrets(`tok ${hex} end`), 'tok [redacted] end');
});

test('лишние поля состояния отбрасываются, список полей — из спеки', () => {
  const state = filterState({
    run_id: 'r', machine_id: 'm', status: 'running',
    evil: 'drop me', code: 'print(1)',
  });
  assert.equal(state.run_id, 'r');
  assert.ok(!('evil' in state));
  assert.ok(!('code' in state));
});

test('дифф гасится целиком: метаданные и содержимое после new file mode', () => {
  const text = [
    'header line',
    'diff --git a/x b/x',
    'new file mode 100644',
    'index 0000000..1111111',
    '--- /dev/null',
    '+++ b/x',
    '+added secret line',
    'trailer line',
  ].join('\n');
  const finding = sanitizeFinding({ title: 't', path: 'p', test: 'test_x', text });
  assert.ok(finding.text.includes('header line'));
  assert.ok(finding.text.includes('trailer line'));
  assert.ok(!finding.text.includes('added secret line'));
  assert.ok(!finding.text.includes('new file mode'));
});

test('секретный путь сокращается до basename, обычный относительный цел', () => {
  const secret = sanitizeFinding({ title: 't', path: '/private/repo/.env', test: 'x', text: 'plain' });
  assert.equal(secret.path, '.env');
  const rel = sanitizeFinding({ title: 't', path: 'src/loop/phase.py', test: 'x', text: 'plain' });
  assert.equal(rel.path, 'src/loop/phase.py');
});

test('F9: combined diff --cc гасится целиком', () => {
  const text = [
    'header line',
    'diff --cc x',
    'index 1..2',
    '@@@ -1,1 -1,1 +1,1 @@@',
    '++SECRET',
    'trailer line',
  ].join('\n');
  const finding = sanitizeFinding({ title: 't', path: 'p', test: 't', text });
  assert.ok(finding.text.includes('header line'));
  assert.ok(finding.text.includes('trailer line'));
  assert.ok(!finding.text.includes('SECRET'));
  assert.ok(!finding.text.includes('diff --cc'));
});

test('F12: copy from/to не прерывают diff', () => {
  const text = [
    'header line',
    'diff --git a/a b/b',
    'similarity index 80%',
    'copy from a',
    'copy to b',
    'index aa..bb',
    '--- a/a',
    '+++ b/b',
    '@@ -1 +1 @@',
    '+COPY_PATCH_SECRET',
    'trailer line',
  ].join('\n');
  const finding = sanitizeFinding({ title: 't', path: 'p', test: 't', text });
  assert.ok(finding.text.includes('header line'));
  assert.ok(finding.text.includes('trailer line'));
  assert.ok(!finding.text.includes('COPY_PATCH_SECRET'));
  assert.ok(!finding.text.includes('copy from'));
});

test('F13: секрет в ключе вложенного поля маскируется', () => {
  const state = filterState({ decisions: { 'sk-supersecret-token': 'safe' } });
  assert.ok(!JSON.stringify(state).includes('sk-supersecret-token'));
});

test('F14: путь без кода/диффа; windows-секрет и абсолютный путь обезврежены', () => {
  const coded = sanitizeFinding({ title: 'x', path: '```js\nPATH_SECRET\n```', test: 'x', text: 'ok' });
  assert.ok(!coded.path.includes('```'));
  assert.ok(!coded.path.includes('PATH_SECRET'));
  const win = sanitizeFinding({ title: 'x', path: 'C:\\Users\\alice\\.env', test: 'x', text: 'ok' });
  assert.equal(win.path, '.env');
  const abs = sanitizeFinding({ title: 'x', path: '/repo/src/a.py', test: 'x', text: 'ok' });
  assert.equal(abs.path, 'a.py');
});

test('F15: лимит 500 — только finding.text, вопрос цел', () => {
  const q = 'Question sentence. '.repeat(35);
  assert.ok(q.length > 500);
  const state = filterState({ question: { id: 'q', text: q, actions: ['more'] } });
  assert.equal(state.question.text, q);
  // Кириллица: не матчится B64/HEX-правилами, проверяет именно лимит.
  const finding = sanitizeFinding({ title: 't', path: 'p', test: 't', text: 'слово '.repeat(120) });
  assert.equal(finding.text.length, 500);
});

test('B64 как в redact.py: длинный путь цел, токен между слешами вырезан', () => {
  const longPath = 'src/verylongdirectory/anotherlongdirectory/filename-with-many-characters';
  assert.equal(redactSecrets(longPath), longPath);
  assert.equal(redactSecrets(`a/${'Q'.repeat(40)}/b`), 'a/[redacted]/b');
});

test('rooted Windows-путь и GIT binary patch не утекают', () => {
  const f = sanitizeFinding({ title: 't', path: '\\repo\\src\\a.py', test: 't', text: 'diff --git a/x b/x\nGIT binary patch\nliteral 8\nmDP+&a\n\nпосле' });
  assert.equal(f.path, 'a.py');
  assert.ok(!f.text.includes('mDP+&a') && !f.text.includes('literal'));
  assert.ok(f.text.includes('после'));
});

test('B13: токены с префиксом и NAME_KEY=value маскируются как в redact.py', () => {
  for (const tok of ['ghp_' + 'a1B2'.repeat(9), 'xoxb-' + '123-abc-'.repeat(3), 'AIza' + 'Sy'.repeat(18), 'sk_live_' + 'Zq'.repeat(10), 'eyJhbGciOiJI.eyJzdWIiOiIx.SflKxwRJSMeK']) {
    assert.equal(redactSecrets(`leak ${tok} here`), 'leak [redacted] here', tok);
  }
  assert.equal(redactSecrets('TYPESAFE_API_KEY=abc123 password: "hunter 2" db_token=x;'),
    'TYPESAFE_API_KEY=[redacted] password: [redacted] db_token=[redacted];');
});

test('question не объектом тоже проходит фильтр', () => {
  const tok = 'sk-' + 'Ab3dEf7h'.repeat(4);
  assert.ok(!JSON.stringify(filterState({ question: `leak ${tok}` })).includes(tok));
  assert.ok(!JSON.stringify(filterState({ question: [`leak ${tok}`] })).includes(tok));
});

test('X_SECRET=-----BEGIN… — материал ключа не утекает; длинное слово без взрыва', () => {
  const out = redactSecrets('X_SECRET=-----BEGIN PRIVATE KEY-----\nMATERIAL\n-----END PRIVATE KEY----- end');
  assert.ok(!out.includes('MATERIAL') && !out.includes('BEGIN'));
  const t = Date.now();
  redactSecrets('A' + '_'.repeat(20000) + 'x');
  assert.ok(Date.now() - t < 1000);
});

test('B14: длинное имя и вложенное присваивание маскируются', () => {
  assert.equal(redactSecrets('ab_'.repeat(40) + 'TOKEN=v1'), 'ab_'.repeat(40) + 'TOKEN=[redacted]');
  assert.equal(redactSecrets('note: password=abc'), 'note: password=[redacted]');
  assert.equal(redactSecrets('status: ok'), 'status: ok');
});

test('view: вложенные белые списки, лог ≤ 40 строк по 300 символов, pid не проходит', async () => {
  const { filterState, VIEW_FIELDS, AGENT_FIELDS, LOG_FIELDS } = await import('../src/filter.js');
  const log = Array.from({ length: 50 }, (_, i) => ({ ts: i, role: 'player', type: 'text', text: 'слово '.repeat(80), extra: 1 }));
  const out = filterState({
    run_id: 'r', machine_id: 'm',
    view: {
      light: 'green', light_text: 'работает', log, agent: { host: 'claude', on_duty: true, pid: 42 },
      sectors: [{ title: 'a', color: 'green', secret: 'sk-aaaaaaaaaaaaaaaaaaaaaaaa' }],
      verdict: { verdict: 'SHIP', blocking: 0, first: '', raw: 'x' }, resume: null, evil: 'drop',
    },
  });
  assert.equal(out.view.log.length, 40);
  assert.equal(out.view.log[0].text.length, 300);
  assert.ok(!('extra' in out.view.log[0]));
  assert.deepEqual(out.view.agent, { host: 'claude', on_duty: true });
  assert.deepEqual(out.view.sectors, [{ title: 'a', color: 'green' }]);
  assert.deepEqual(out.view.verdict, { verdict: 'SHIP', blocking: 0, first: '' });
  assert.equal(out.view.resume, null);
  assert.ok(!('evil' in out.view));
  assert.ok(VIEW_FIELDS.has('log') && AGENT_FIELDS.has('on_duty') && LOG_FIELDS.has('text'));
  assert.ok(!JSON.stringify(out).includes('"pid"'));
});
