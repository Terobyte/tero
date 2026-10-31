// relay/public/pult.js — всё на телефоне кроме карточки (спек пульта 2, п. 6):
// подача view в карточку (window.teroSource), вопрос с кнопками, «уже отвечено»,
// лимиты, прогоны, привязка и пуши. Чистые функции экспортируются и гоняются в
// node (relay/test/pult.test.js); браузерная часть — под гардом document.
// Текст с телефона и состояние — данные: всё интерполированное экранируется.

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Токен для запросов: свой (tero_token) важнее ссылки. Read-токен виджета
// приходит во фрагменте #token= (не уходит на сервер), сразу стирается из
// адреса и хранится отдельно — ссылка не подменяет привязанное устройство (B18).
export function tokenFromLink(storage, loc, hist) {
  const own = storage.getItem('tero_token') || '';
  const fromHash = new URLSearchParams(String(loc.hash || '').replace(/^#/, '')).get('token') || '';
  if (fromHash) {
    hist.replaceState(null, '', loc.pathname + loc.search);
    if (!own) storage.setItem('tero_read_token', fromHash);
  }
  return own || fromHash || storage.getItem('tero_read_token') || '';
}

// Черновик ответа переживает 15-секундную перерисовку #question (F17).
export function captureDraft(doc) {
  const section = doc.getElementById('question');
  if (!section) return null;
  const qidEl = section.querySelector('.qid');
  const textEl = section.querySelector('textarea[name="text"]');
  const modelEl = section.querySelector('select[name="model"]');
  const active = doc.activeElement;
  return {
    qid: qidEl ? qidEl.textContent : '',
    text: textEl ? textEl.value : '',
    model: modelEl ? modelEl.value : '',
    focus: active && section.contains(active) ? active.getAttribute('name') : '',
  };
}

export function restoreDraft(doc, draft) {
  if (!draft) return;
  const section = doc.getElementById('question');
  if (!section) return;
  const qidEl = section.querySelector('.qid');
  if ((qidEl ? qidEl.textContent : '') !== draft.qid) return;  // другой вопрос — черновик не его
  const textEl = section.querySelector('textarea[name="text"]');
  const modelEl = section.querySelector('select[name="model"]');
  if (textEl && draft.text) textEl.value = draft.text;
  if (modelEl && draft.model) modelEl.value = draft.model;
  if (draft.focus === 'text' && textEl) textEl.focus();
  if (draft.focus === 'model' && modelEl) modelEl.focus();
}

export function renderQuestion(question, phoneText, models) {
  // Только действия из question.actions: Worker примет лишь их, запасные кнопки
  // получили бы 422. Пустой список — кнопок нет.
  const rawActions = Array.isArray(question.actions) ? question.actions.slice() : [];
  // Без текстовых ответов только кнопки more|skip|retry|stop: текстовые
  // действия (answer, allow, model) приняли бы пустой ввод или текст по 422-пути.
  const SAFE_SILENT = new Set(['more', 'skip', 'retry', 'stop']);
  const actions = phoneText === false
    ? rawActions.filter((a) => SAFE_SILENT.has(String(a)))
    : rawActions;
  const buttons = actions.map((a) => escapeHtml(String(a))).filter(Boolean)
    .map((action) => (action === 'stop'
      ? '<button data-action="stop" onclick="return confirm(\'Остановить прогон?\')">stop</button>'
      : `<button data-action="${action}">${action}</button>`)).join('\n');
  const text = phoneText === false ? '' : '<textarea name="text" maxlength="2000"></textarea>';
  const wantsModel = actions.includes('model');
  const modelSel = wantsModel && Array.isArray(models) && models.length
    ? `<select name="model" aria-label="model">${models.map((m) => `<option>${escapeHtml(m)}</option>`).join('')}</select>`
    : '';
  if (!buttons) {
    return '<section id="question"><h2>вопрос</h2>'
      + `<p>${escapeHtml(question.text)}</p>`
      + `<p class="qid">${escapeHtml(question.id)}</p>`
      + '<p>нет действий для ответа с телефона</p></section>';
  }
  return '<section id="question"><h2>вопрос</h2>'
    + `<p>${escapeHtml(question.text)}</p>`
    + `<p class="qid">${escapeHtml(question.id)}</p>`
    + buttons + text + modelSel + '</section>';
}

function renderAnswered(answered, decisions) {
  const last = answered || (Array.isArray(decisions) && decisions[decisions.length - 1]) || null;
  const what = last && (last.action || last.what)
    ? ` — ${escapeHtml(last.action || last.what)} (${escapeHtml(last.by || last.who || '')})`
    : '';
  return `<section id="answered"><p>уже отвечено${what}</p></section>`;
}

function renderList(items, render) {
  if (!Array.isArray(items) || !items.length) return '<p>—</p>';
  return `<ul>${items.map((item) => `<li>${render(item)}</li>`).join('')}</ul>`;
}

function renderLimits(limits) {
  if (!limits || typeof limits !== 'object' || Array.isArray(limits)) {
    return '<p>лимиты: неизвестно</p>';
  }
  const rows = Object.entries(limits)
    .filter(([, v]) => typeof v === 'string' && v)
    .map(([k, v]) => `${escapeHtml(k)}: ${escapeHtml(v)}`);
  if (!rows.length) return '<p>лимиты: неизвестно</p>';
  return `<ul>${rows.map((r) => `<li>${r}</li>`).join('')}</ul>`;
}

export function renderPair(unpaired) {
  const head = unpaired
    ? '<h1>привязка недействительна</h1><p>токен отозван — введите новый код с экрана <code>tero pair</code></p>'
    : '<h1>привязка телефона</h1><p>введите код с экрана <code>tero pair</code></p>';
  return `<section id="${unpaired ? 'unpaired' : 'pair'}">${head}`
    + '<input id="pair-code" maxlength="16"><button id="pair-btn">привязать</button></section>';
}

// Над карточкой: открытый вопрос первым (тап по пушу ведёт сюда), иначе итог.
export function renderTop(state = {}) {
  const st = state && typeof state === 'object' ? state : {};
  const phoneText = st.phone_text_answers !== false;
  // Моделей judge в списке выбора нет: judge — не игрок. Роль выбора — player.
  const models = Array.isArray(st.models)
    ? st.models.map((m) => String(m).replace(/^(player|tester)\s+/, '')).filter((m) => m !== 'judge')
    : [];
  if (st.question && st.question.id) return renderQuestion(st.question, phoneText, models);
  return '<section id="idle"><p>нет открытого вопроса</p></section>';
}

// Под карточкой по спеку: «уже отвечено», статус и лимиты, прогоны, решения, долг, пуши
// (сообщение — фаза 1; wake/resume и список «остановлены» — фазы 2–4).
export function renderBelow(state = {}, opts = {}) {
  const st = state && typeof state === 'object' ? state : {};
  const runs = Array.isArray(opts.runs) ? opts.runs : [];
  const isStoppedRun = (r) => r && (String(r.status) === 'done' || String(r.status) === 'stopped');
  const runLink = (r, extra = '') => `<a href="?run_id=${escapeHtml(r.run_id)}`
    + `&amp;machine_id=${escapeHtml(r.machine_id)}" data-run="${escapeHtml(r.run_id)}">`
    + `${escapeHtml(r.run_id)} · ${escapeHtml(r.status)}${extra}</a>`;
  const runsHtml = renderList(runs.filter((r) => !isStoppedRun(r)), runLink);
  const stopped = runs.filter(isStoppedRun);
  const stoppedHtml = stopped.length ? `<section id="stopped"><h2>остановлены</h2>`
    + renderList(stopped, (r) => runLink(r, r.updated_at ? ` · ${escapeHtml(r.updated_at)}` : ''))
    + `</section>` : '';
  const decisionsHtml = renderList(st.decisions, (d) => (d.kind === 'wake'
    ? `${escapeHtml(d.at || '')} — wake (${escapeHtml(d.reason)}): ${escapeHtml(d.result)}`
    : `${escapeHtml(d.at || d.ts || '')} — ${escapeHtml(d.who)}: `
      + `${escapeHtml(d.what || d.action)}${d.why ? ` (${escapeHtml(d.why)})` : ''}`));
  const debtsHtml = renderList(st.debts, (d) => `${escapeHtml(d.phase)} — ${escapeHtml(d.reason)}`);
  const historyHtml = renderList(st.history, (h) => `${escapeHtml(h.phase)} — ${escapeHtml(h.verdict)}`);
  const agent = st.view && st.view.agent;
  const live = !['done', 'stopped', 'failed'].includes(String(st.status));
  const waitingN = st.view ? Number(st.view.messages_waiting) || 0 : 0;
  const noAgent = live && agent && agent.on_duty === false && waitingN > 0;
  const waiting = waitingN > 0
    ? `<p>${noAgent ? 'агента нет, сообщение ждёт' : `непрочитанных сообщений хозяину: ${waitingN}`}</p>` : '';
  const answeredLast = st.answered
    || (Array.isArray(st.decisions) ? [...st.decisions].reverse().find((d) => d && (d.action || d.what)) : null);
  const answered = answeredLast ? renderAnswered(answeredLast, st.decisions) : '';
  const updatedMs = Date.parse(st.updated_at || '');
  const stale = Number.isFinite(updatedMs) && (Date.now() - updatedMs > STALE_AFTER_S * 1000);
  const wakeBtn = live && !stale && agent && agent.on_duty === false
    ? '<section id="wake"><button id="wake-btn">Разбудить агента</button><p id="wake-error"></p></section>' : '';
  return answered + `<section id="run-status"><h2>статус</h2>`
    + `<p>${escapeHtml(st.status)} · фаза ${escapeHtml(st.phase)} · раунд ${escapeHtml(st.round)} · `
    + `${escapeHtml(st.role)} · ${escapeHtml(st.model)}</p>${waiting}`
    + wakeBtn
    + renderResume(opts.resume)
    + (st.phone_text_answers === false ? '' : '<section id="message"><h2>сообщение хозяину</h2>'
      + '<input type="text" name="message" maxlength="2000">'
      + '<button id="msg-btn">Отправить</button><p id="msg-error"></p></section>')
    + `<section id="limits"><h2>лимиты</h2>${renderLimits(st.limits)}</section></section>`
    + `<section id="runs"><h2>прогоны</h2>${runsHtml}</section>`
    + stoppedHtml
    + `<section id="decisions"><h2>решения</h2>${decisionsHtml}</section>`
    + `<section id="debt"><h2>долг и история фазы</h2>${debtsHtml}${historyHtml}</section>`
    + '<section id="push"><button id="push-btn">Включить пуши</button></section>';
}

export const STALE_AFTER_S = 600;
export const MACHINE_STALE_S = 120;

function hhmm(ts) {
  const d = new Date(Number(ts) * 1000);
  return Number.isFinite(d.getTime()) ? `${('0' + d.getHours()).slice(-2)}:${('0' + d.getMinutes()).slice(-2)}` : '?';
}

// Лампочка на телефоне: подмены поверх view.light по порядку (спек п. 6):
// (1) терминальный статус — как есть; (2) владелец мёртв по отметкам сторожа;
// (3) данные старше 10 мин; (4) зелёная, но тихо дольше quiet_after_s.
export function phoneLight(view, marks, now) {
  const v = view || {};
  const m = marks || {};
  const nowS = Number.isFinite(Number(now)) ? Number(now) : Date.now() / 1000;
  const terminal = v.light === 'ready' || !!v.stop_text || !!v.done_text;
  if (terminal) return { light: v.light || 'grey', light_text: v.light_text || '' };
  if (m.owner_alive === false) { // отметки сторожа приходят с фазы 4; до неё ветка спит
    const checked = m.owner_checked_at ? ` · проверено в ${hhmm(m.owner_checked_at)}` : '';
    return { light: 'red', light_text: `оборвался${checked}` };
  }
  const updated = m.updated_at ? Date.parse(m.updated_at) / 1000 : NaN;
  if (Number.isFinite(updated) && nowS - updated > STALE_AFTER_S) {
    return { light: 'grey', light_text: 'нет свежих данных' };
  }
  const quiet = Number(v.quiet_after_s) || STALE_AFTER_S;
  if (v.light === 'green' && v.last_event_at != null && nowS - Number(v.last_event_at) >= quiet) {
    return { light: 'yellow', light_text: `тихо ${Math.floor((nowS - Number(v.last_event_at)) / 60)} мин` };
  }
  return { light: v.light || 'grey', light_text: v.light_text || '' };
}

// Кнопка resume (спек п. 7): режим берётся из marks.resume_w, если отметка сторожа
// позже updated_at, иначе из view.resume. Серая — машина молчит > 2 мин;
// скрыта (с подсказкой) — по машине нет ни одной отметки.
export function resumeButton(body, now) {
  const b = body || {};
  const m = b.marks || {};
  const nowS = Number.isFinite(Number(now)) ? Number(now) : Date.now() / 1000;
  const hasMark = m.machine_last_seen != null || m.owner_checked_at != null || m.resume_w != null;
  if (!hasMark) return { show: false, hint: 'tero watch --install' };
  const updated = b.updated_at ? Date.parse(b.updated_at) / 1000 : NaN;
  const fresh = m.owner_checked_at != null && Number.isFinite(updated) && Number(m.owner_checked_at) > updated;
  const src = fresh ? m.resume_w : (b.view && b.view.resume);
  const mode = src && src.mode;
  if (mode !== 'ok' && mode !== 'needs_text') return { show: false, hint: '' };
  const silent = m.machine_last_seen == null || nowS - Number(m.machine_last_seen) > 120;
  return { show: true, disabled: silent, field: mode === 'needs_text', why: (src && src.why) || '',
    text: silent ? 'машина не на связи' : 'resume', hint: '' };
}

export function renderResume(btn) {
  if (!btn) return '';
  if (!btn.show) return btn.hint ? `<p id="resume-hint">resume: <code>${escapeHtml(btn.hint)}</code></p>` : '';
  return '<section id="resume">'
    + (btn.field ? `<p>${escapeHtml(btn.why)}</p><input type="text" name="resume-text" maxlength="2000">` : '')
    + `<button id="resume-btn"${btn.disabled ? ' disabled class="grey"' : ''}>${escapeHtml(btn.text)}</button>`
    + '<p id="resume-error"></p><p id="resume-result"></p></section>';
}

// Итог последней команды сторожа (GET /commands/last): после отправки resume
// страница показывает код и текст результата, а не только «отправлено».
export function commandResultText(cmd) {
  if (!cmd) return '';
  const tail = cmd.result_text ? ` — ${cmd.result_text}` : '';
  if (cmd.status === 'pending') return 'отправлено сторожу: ожидает';
  if (cmd.status === 'taken') return 'сторож выполняет…';
  if (cmd.status === 'done') return `сторож: готово${tail}`;
  if (cmd.status === 'failed') return `сторож: код ${cmd.code ?? '?'}${tail}`;
  if (cmd.status === 'expired') return 'команда истекла — отправь ещё раз';
  if (cmd.status === 'lost') return 'сторож не ответил (lost) — попробуй ещё раз';
  return `сторож: ${cmd.status}`;
}

// Кладет итог команды сторожа в #resume-result; вызывается и сразу после
// отправки, и после каждой перерисовки опроса — иначе перерисовка стирает итог.
export function applyCommandResult(doc, cmd) {
  const el = doc && doc.getElementById ? doc.getElementById('resume-result') : null;
  if (!el) return '';
  const text = commandResultText(cmd);
  if (text) el.textContent = text;
  return text;
}
// мак-понятия (plus, visible) пустые. Без view (старый ретранслятор) — серая карточка.
// view для карточки из ответа GET /state: модель как есть плюс подмены лампочки;
// мак-понятия (plus, visible) пустые. Без view (старый ретранслятор) — серая карточка.
export function viewOf(body, now) {
  const state = body && body.state && typeof body.state === 'object' ? body.state : {};
  const marks = { ...(body && body.marks ? body.marks : {}), updated_at: state.updated_at };
  const base = state.view && typeof state.view === 'object' ? state.view : null;
  if (!base) {
    return { light: 'grey', light_text: 'нет свежих данных', sectors: [], log: [], doing: 'нет данных',
      role_line: '', role: '', verdict: {}, plus: '', visible: true };
  }
  const light = phoneLight(base, marks, now);
  return { ...base, light: light.light, light_text: light.light_text, plus: '', visible: true };
}

// ---------------------------------------------------------------- браузер
const inBrowser = typeof document !== 'undefined' && typeof fetch === 'function'
  && document.getElementById && document.getElementById('card');

if (inBrowser) {
  const card = document.getElementById('card');
  const top = document.createElement('div');
  top.id = 'pult-top';
  const below = document.createElement('div');
  below.id = 'pult';
  card.parentNode.insertBefore(top, card);
  card.parentNode.insertBefore(below, card.nextSibling);
  let token = localStorage.getItem('tero_token') || '';
  try {
    token = tokenFromLink(localStorage, location, history);
  } catch { token = token || ''; }
  let runs = [];
  let runsAt = 0;
  // Цель отправленного resume: итог (done/failed/lost) обычно готов позже отправки —
  // каждый опрос дотягивает его через /commands/last и кладёт в #resume-result.
  let lastResume = null;

  // Итог последней команды сторожа: вызывается и сразу после отправки, и после
  // каждой перерисовки опроса — перерисовка below.innerHTML иначе стирает итог.
  async function refreshResumeResult(state) {
    const t = (state && state.run_id && state.machine_id) ? state : lastResume;
    if (!t || !t.run_id || !t.machine_id) return;
    const resEl = document.getElementById('resume-result');
    if (!resEl) return;
    try {
      const last = await fetch(`/commands/last?run_id=${encodeURIComponent(t.run_id)}`
        + `&machine_id=${encodeURIComponent(t.machine_id)}`, {
        headers: { authorization: `Bearer ${token}` },
      });
      if (!last.ok) return;
      applyCommandResult(document, (await last.json().catch(() => ({}))).command);
    } catch { /* следующий тик опроса попробует снова */ }
  }

  function currentCode() {
    const fromQuery = new URLSearchParams(location.search).get('code');
    if (fromQuery) return fromQuery;
    const m = /^\/pair\/([A-Za-z0-9]+)\/?$/.exec(location.pathname || '');
    return (m && m[1]) || '';
  }

  (function linkManifestToCode() {
    const code = currentCode();
    if (!code) return;
    const link = document.querySelector('link[rel="manifest"]');
    if (link) link.href = `/manifest.webmanifest?code=${encodeURIComponent(code)}`;
  })();

  function unpaired(res) {
    return res.status === 401 && res.body && res.body.error === 'unpaired';
  }

  async function get(path) {
    const headers = token ? { authorization: `Bearer ${token}` } : {};
    const res = await fetch(path, { headers });
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    return { status: res.status, body };
  }

  function isPwaLaunch() {
    if (window.matchMedia
      && (window.matchMedia('(display-mode: standalone)').matches
        || window.matchMedia('(display-mode: fullscreen)').matches)) return true;
    return window.navigator.standalone === true;
  }

  function showPair(isUnpaired) {
    top.innerHTML = renderPair(isUnpaired);
    below.innerHTML = '';
    card.style.display = 'none';
    window.teroSource.set(null);
    document.getElementById('pair-btn').onclick = () => pair();
  }

  async function load() {
    if (!token) {
      // PWA после «добавить на экран Домой» стартует с чистым хранилищем —
      // код из start_url меняем только здесь. Safari держит код в адресной строке (F14).
      const code = currentCode();
      if (code && isPwaLaunch()) {
        await pair(code, true);
        return;
      }
      showPair(false);
      return;
    }
    const params = new URLSearchParams(location.search);
    const runId = params.get('run_id');
    const machineId = params.get('machine_id');
    if (Date.now() - runsAt > 60000) {
      const runsRes = await get('/runs?all=1');
      if (unpaired(runsRes)) { showPair(true); return; }
      runs = (runsRes.body && runsRes.body.runs) || [];
      runsAt = Date.now();
    }
    const firstLive = runs.find((r) => r
      && String(r.status) !== 'done' && String(r.status) !== 'stopped') || runs[0] || {};
    const target = (runId && machineId)
      ? { run_id: runId, machine_id: machineId }
      : { run_id: firstLive.run_id, machine_id: firstLive.machine_id };
    let body = null;
    if (target.run_id && target.machine_id) {
      const st = await get(`/state?run_id=${encodeURIComponent(target.run_id)}`
        + `&machine_id=${encodeURIComponent(target.machine_id)}`);
      if (unpaired(st)) { showPair(true); return; }
      body = st.body || null;
    }
    const state = (body && body.state) || {};
    card.style.display = '';
    window.teroSource.set(viewOf(body, Date.now() / 1000));
    const draft = captureDraft(document);
    top.innerHTML = renderTop(state);
    below.innerHTML = renderBelow(state, { runs, resume: resumeButton({ ...(body || {}), updated_at: state.updated_at,
      view: state.view }, Date.now() / 1000) });
    wire(state);
    restoreDraft(document, draft);
    refreshResumeResult(state);
  }

  async function pair(presetCode = '', auto = false) {
    const code = String(presetCode
      || (document.getElementById('pair-code') || {}).value || '').trim();
    if (!code) return;
    const res = await fetch('/pair/exchange', {
      method: 'POST',
      headers: { 'content-type': 'application/json' },
      body: JSON.stringify({ code: code.trim() }),
    });
    const body = await res.json().catch(() => null);
    if (res.ok && body && body.device_token) {
      localStorage.setItem('tero_token', body.device_token);
      token = body.device_token;
      if (body.read_token) localStorage.setItem('tero_read_token', body.read_token);
      runsAt = 0;
      await load();
      return;
    }
    if (auto) showPair(false);
  }

  async function answer(action, text, target) {
    const payload = { question_id: target.id, action, text: text || '' };
    if (target.run_id) payload.run_id = target.run_id;
    if (target.machine_id) payload.machine_id = target.machine_id;
    const res = await fetch('/answer', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify(payload),
    });
    if (res.status === 401) {
      const body = await res.json().catch(() => ({}));
      if (body.error === 'unpaired') { showPair(true); return; }
    }
    await load();
  }

  function wire(state) {
    const q = (state && state.question) || {};
    const section = document.getElementById('question');
    if (section) {
      const target = { id: q.id, run_id: state.run_id, machine_id: state.machine_id };
      section.querySelectorAll('button[data-action]').forEach((btn) => {
        if (btn.dataset.action === 'stop') {
          btn.onclick = () => {
            if (typeof confirm === 'function' && !confirm('Остановить прогон?')) return;
            answer('stop', '', target);
          };
        } else if (btn.dataset.action === 'model') {
          btn.onclick = () => {
            const modelEl = section.querySelector('select[name="model"]');
            const model = modelEl ? String(modelEl.value || '').trim() : '';
            const textEl = section.querySelector('textarea[name="text"]');
            const text = textEl ? textEl.value : '';
            // Без текстового поля нет честного model: пустой текст дал бы 422.
            if (!textEl && !model) { load(); return; }
            answer('model', model ? `player ${model}` : text, target);
          };
        } else {
          btn.onclick = () => {
            const textEl = section.querySelector('textarea[name="text"]');
            answer(btn.dataset.action, textEl ? textEl.value : '', target);
          };
        }
      });
    }
    const pushBtn = document.getElementById('push-btn');
    if (pushBtn) pushBtn.onclick = enablePush;
    const msgBtn = document.getElementById('msg-btn');
    if (msgBtn) msgBtn.onclick = () => sendMessage(state);
    const wakeBtn = document.getElementById('wake-btn');
    if (wakeBtn) wakeBtn.onclick = () => sendWake(state);
    const resumeBtn = document.getElementById('resume-btn');
    if (resumeBtn) resumeBtn.onclick = () => sendResume(state);
  }

  async function sendResume(state) {
    const errEl = document.getElementById('resume-error');
    const field = document.querySelector('#resume input[name="resume-text"]');
    const res = await fetch('/commands', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ command: 'resume', run_id: state.run_id, machine_id: state.machine_id,
        text: field ? field.value : '' }),
    });
    if (!res.ok) {
      if (errEl) errEl.textContent = String((await res.json().catch(() => ({}))).error || `отказ: ${res.status}`);
      return;
    }
    if (errEl) errEl.textContent = 'отправлено сторожу';
    lastResume = { run_id: state.run_id, machine_id: state.machine_id };
    await refreshResumeResult(state);
  }

  async function sendWake(state) {
    const errEl = document.getElementById('wake-error');
    const res = await fetch('/answer', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ kind: 'wake', run_id: state.run_id, machine_id: state.machine_id }),
    });
    if (errEl) errEl.textContent = res.ok ? 'запрошено, итог в журнале решений'
      : String((await res.json().catch(() => ({}))).error || `отказ: ${res.status}`);
  }

  async function sendMessage(state) {
    const box = document.querySelector('#message input[name="message"]');
    const errEl = document.getElementById('msg-error');
    const text = box ? box.value.trim() : '';
    if (!text) return;
    const res = await fetch('/answer', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ kind: 'message', text, run_id: state.run_id, machine_id: state.machine_id }),
    });
    if (res.ok) { if (box) box.value = ''; if (errEl) errEl.textContent = 'отправлено'; return; }
    const body = await res.json().catch(() => ({}));
    if (errEl) errEl.textContent = String(body.error || `отказ: ${res.status}`);
  }

  async function enablePush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) return;
    // iOS молча отказывает без явного запроса из жеста: только по кнопке.
    if (window.Notification && Notification.requestPermission) {
      if (Notification.permission === 'denied') return;
      if (Notification.permission !== 'granted' && await Notification.requestPermission() !== 'granted') return;
    }
    const reg = await navigator.serviceWorker.register('/sw.js');
    const vapidRes = await get('/push/vapid');
    const key = vapidRes.body && vapidRes.body.vapid_public_key;
    if (!key) return;
    // Сервер отдаёт публичный ключ JWK-JSON; subscribe нужен несжатый P-256.
    const appKey = jwkToU8(key) || urlToU8(key);
    if (!appKey) return;
    const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: appKey.buffer });
    const p256dh = u8ToB64(new Uint8Array(sub.getKey('p256dh')));
    const auth = u8ToB64(new Uint8Array(sub.getKey('auth')));
    if (!p256dh || !auth) return;
    await fetch('/push/subscribe', {
      method: 'POST',
      headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
      body: JSON.stringify({ endpoint: sub.endpoint, p256dh, auth }),
    });
  }

  function jwkToU8(key) {
    if (typeof key !== 'string') return null;
    let jwk = null;
    try {
      jwk = JSON.parse(key);
    } catch {
      return null;
    }
    if (!jwk || typeof jwk !== 'object' || !jwk.x || !jwk.y) return null;
    const xy = urlToU8(jwk.x);
    const yy = urlToU8(jwk.y);
    if (!xy || !yy || xy.length !== 32 || yy.length !== 32) return null;
    const out = new Uint8Array(65);
    out[0] = 4;
    out.set(xy, 1);
    out.set(yy, 33);
    return out;
  }

  function u8ToB64(bytes) {
    if (!bytes || !bytes.length) return '';
    let bin = '';
    for (const b of bytes) bin += String.fromCharCode(b);
    return btoa(bin);
  }

  function urlToU8(urlKey) {
    try {
      const norm = String(urlKey).replace(/-/g, '+').replace(/_/g, '/');
      const bin = atob(norm);
      const out = new Uint8Array(bin.length);
      for (let i = 0; i < bin.length; i++) out[i] = bin.charCodeAt(i);
      return out.length ? out : null;
    } catch {
      return null;
    }
  }

  // раз в 15 с, только пока вкладка видна; возврат на вкладку — сразу
  // сеть упала — карточка остаётся на последнем view, следующий тик попробует снова
  const tick = () => load().catch((e) => console.warn('pult:', e));
  setInterval(() => { if (document.visibilityState === 'visible') tick(); }, 15000);
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') tick(); });
  tick();
}
