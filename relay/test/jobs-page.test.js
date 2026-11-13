// relay/test/jobs-page.test.js — чистые функции relay/public/jobs.js (без браузера).
import test from 'node:test';
import assert from 'node:assert/strict';
import { renderCard, laptopText, decisionBody, swipeAction, filterStatusCards, nextBatchText } from '../public/jobs.js';

test('renderCard escapes everything but marks', () => {
  const job = renderCard({ id: 'job:a', kind: 'job', version: 'v1',
    payload: { title: '<img onerror=alert(1)>', company: 'c', location: '', salary: '', why: '', path: 'qa', url: '', engine: 'chrome' } });
  assert.ok(!job.includes('<img'));
  assert.ok(job.includes('&lt;img'));

  const approve = renderCard({ id: 'approve:a', kind: 'approve', version: 'v1',
    payload: { title: 't', company: 'c', resume_html: 'a <mark>b</mark> <script>x</script>', filled: [], his: {}, drafts: {} } });
  assert.ok(approve.includes('<mark>b</mark>'));
  assert.ok(!approve.includes('<script>x</script>'));
  assert.ok(approve.includes('&lt;script&gt;'));
});

test('job card shows prepared answers', () => {
  const html = renderCard({ id: 'job:a', kind: 'job', version: 'v1', hot: true,
    payload: { title: 't', company: 'c', location: '', salary: '', why: '', path: 'qa', url: '', engine: 'chrome',
      prepared: {
        known: [{ label: '<img onerror=alert(1)>', value: 'yes' }, { label: 'b', value: '<img src=x onerror=alert(2)>' }],
        ask: [{ label: 'why us', kind: 'text', options: [], required: true, draft: '<script>x</script>' }],
      } } });
  assert.ok(html.includes('готово из базы: 2'));
  assert.ok(html.includes('к тебе: 1 вопрос'));
  assert.ok(!html.includes('<img'));
  assert.ok(!html.includes('<script>x</script>'));
  assert.ok(html.includes('&lt;img'));
  assert.ok(html.includes('&lt;script&gt;'));
  assert.ok(html.includes('&lt;img src=x'));
  assert.ok(html.includes('<ul class="drafts">'));
  assert.match(html, /<ul class="drafts">.*&lt;script&gt;.*<\/ul>/s);
});

test('laptopText', () => {
  assert.equal(laptopText(0, 299000), '');
  assert.ok(laptopText(0, 301000).startsWith('ноут молчит с'));
});

test('decisionBody for ask carries every answer', () => {
  const card = { id: 'ask:a', kind: 'ask', version: 'sha1', payload: { title: 't', company: 'c',
    items: [{ label: 'q1', kind: 'text', required: true }, { label: 'q2', kind: 'yesno', required: true }] } };
  const body = decisionBody(card, 'answers', { q1: 'yes please', q2: 'да' });
  assert.equal(body.card_id, 'ask:a');
  assert.equal(body.version, 'sha1');
  assert.equal(body.action, 'answers');
  assert.deepEqual(body.data, { q1: 'yes please', q2: 'да' });
});

test('decisionBody keeps an answer labeled __proto__', () => {
  const card = { id: 'ask:a', kind: 'ask', version: 'sha1', payload: { title: 't', company: 'c',
    items: [{ label: '__proto__', kind: 'text', required: true }] } };
  const form = JSON.parse('{"__proto__": "плохое значение"}');
  const body = decisionBody(card, 'answers', form);
  assert.equal(Object.hasOwn(body.data, '__proto__'), true);
  assert.equal(body.data.__proto__, 'плохое значение');
});

test('swipeAction', () => {
  assert.equal(swipeAction(79), null);
  assert.equal(swipeAction(81), 'yes');
  assert.equal(swipeAction(-81), 'no');
});

test('links are https only', () => {
  const stopped = renderCard({ id: 'stopped:a', kind: 'stopped', version: 'v1',
    payload: { title: 't', company: 'c', reason: 'r', url: 'javascript:alert(1)', engine: 'chrome' } });
  assert.ok(!stopped.includes('<a href'));
  const captcha = renderCard({ id: 'captcha:a', kind: 'captcha', version: '1',
    payload: { title: 't', company: 'c', engine: 'browser-act', assist_url: 'data:text/html,x', hint: 'h' } });
  assert.ok(!captcha.includes('<a href'));
  const job = renderCard({ id: 'job:b', kind: 'job', version: 'v1',
    payload: { title: 't', company: 'c', location: '', salary: '', why: '', path: 'qa', url: 'https://boards.greenhouse.io/x', engine: 'chrome' } });
  assert.ok(job.includes('<a href="https://boards.greenhouse.io/x"'));
});

test('status card is hidden while its ask/approve/captcha card is pending', () => {
  const statusCard = { id: 'status:a', kind: 'status', version: '1', rank: 1, payload: {} };
  const askCard = { id: 'ask:a', kind: 'ask', version: 'sha1', rank: 2, payload: {} };
  const other = { id: 'status:b', kind: 'status', version: '1', rank: 3, payload: {} };
  const visible = filterStatusCards([statusCard, askCard, other]);
  assert.deepEqual(visible.map((c) => c.id), ['ask:a', 'status:b']);
  const allVisible = filterStatusCards([statusCard, other]);
  assert.deepEqual(allVisible.map((c) => c.id), ['status:a', 'status:b']);
});

test('empty state names the next batch hour', () => {
  const text = nextBatchText(Date.now());
  assert.ok(text.startsWith('пусто — новых вакансий нет, следующая пачка в '), text);
});

test('answers with his contacts are refused on the phone', () => {
  const card = { id: 'ask:a', kind: 'ask', version: 'sha1', payload: { title: 't', company: 'c',
    items: [{ label: 'q1', kind: 'text', required: true }] } };
  assert.throws(() => decisionBody(card, 'answers', { q1: 'пиши на a@b.com' }), /contact/);
  assert.throws(() => decisionBody(card, 'answers', { q1: 'звони +1 617 555 0101' }), /contact/);
  const html = renderCard(card);
  assert.ok(html.includes('это ноут знает сам'));
});
