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
    // курсор: опрос раз в 4 с при открытом вопросе иначе кидал бы его в конец
    sel: textEl && active === textEl ? [textEl.selectionStart, textEl.selectionEnd] : null,
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
  if (draft.focus === 'text' && textEl) {
    textEl.focus();
    if (draft.sel && typeof textEl.setSelectionRange === 'function') textEl.setSelectionRange(draft.sel[0], draft.sel[1]);
  }
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
    // «стоп» без confirm: нативный диалог в PWA неудобен — его заменяют 5 с на «отменить»
    .map((action) => `<button data-action="${action}">${escapeHtml(actionLabel(action))}</button>`).join('\n');
  const text = phoneText === false ? ''
    : '<textarea name="text" maxlength="2000" placeholder="пояснение к ответу (можно пусто)"></textarea>';
  const wantsModel = actions.includes('model');
  const modelSel = wantsModel && Array.isArray(models) && models.length
    ? '<label class="field">модель для «сменить модель»'
      + `<select name="model" aria-label="model">${models.map((m) => `<option>${escapeHtml(m)}</option>`).join('')}</select></label>`
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
    + buttons + text + modelSel + '<p id="answer-msg"></p></section>';
}

// Отказы Worker человеческим языком: раньше кнопка на любой отказ, кроме 401, молчала.
const ANSWER_ERRORS = {
  'read-only token': 'этот телефон привязан только на чтение — привяжи заново кодом с `tero pair`',
  'no open question': 'вопрос уже закрыт (ответили с другого места или автопилот)',
  'action not allowed': 'это действие сейчас недоступно',
  'text answers disabled': 'текстовые ответы с телефона выключены в конфиге',
  'text required': 'нужен текст',
  'text too long': 'слишком длинный текст (до 2000 символов)',
  'run finished': 'прогон уже закончен',
};
export function answerErrorText(status, error) {
  if (error && ANSWER_ERRORS[error]) return ANSWER_ERRORS[error];
  if (error) return String(error);
  return `отказ сервера: ${status}`;
}

// Кнопки и журнал говорят по-русски; data-action остаётся командой для Worker.
const ACTION_LABELS = {
  more: 'ещё раунд', skip: 'пропустить фазу', retry: 'заново', stop: 'стоп', model: 'сменить модель',
  answer: 'ответить', allow: 'разрешить', ship: 'сдать', debt: 'сдать с долгом',
};
export function actionLabel(action) {
  return ACTION_LABELS[String(action)] || String(action || '');
}
const WHO_LABELS = { human: 'ты', autopilot: 'автопилот', agent: 'агент', host: 'хозяин' };
const REASON_LABELS = { fuse: 'предохранитель', skip: 'пропущена', question: 'вопрос', manual: 'вручную' };
const STATUS_LABELS = {
  running: 'идёт', waiting_human: 'ждёт тебя', done: 'готово', stopped: 'остановлен', failed: 'упал',
};

// Время из state приходит то unix-секундами, то ISO, то готовым «10:00»:
// человеку — «14:08», не сегодня — «27.10 14:08».
export function fmtWhen(value, now = Date.now()) {
  if (value == null || value === '') return '';
  let ms = NaN;
  if (typeof value === 'number' || /^\d+(\.\d+)?$/.test(String(value))) ms = Number(value) * 1000;
  else if (/^\d{4}-\d\d-\d\d/.test(String(value))) ms = Date.parse(String(value));
  if (!Number.isFinite(ms)) return String(value);
  const d = new Date(ms);
  const p = (n) => ('0' + n).slice(-2);
  const t = `${p(d.getHours())}:${p(d.getMinutes())}`;
  return new Date(now).toDateString() === d.toDateString() ? t : `${p(d.getDate())}.${p(d.getMonth() + 1)} ${t}`;
}

// «20261101-234135637839-2026-11-01-engine-worlds» → «engine-worlds · 01.11 23:41».
export function runLabel(runId) {
  const id = String(runId || '');
  const m = /^(\d{4})(\d\d)(\d\d)-(\d\d)(\d\d)\d*-(.+)$/.exec(id);
  if (!m) return id;
  const name = m[6].replace(/^\d{4}-\d\d-\d\d-/, '');
  return `${name} · ${m[3]}.${m[2]} ${m[4]}:${m[5]}`;
}

// Возраст данных словами — без арифметики «сейчас минус 14:08».
export function ageText(sec) {
  const s = Math.max(0, Math.floor(Number(sec) || 0));
  if (s < 5) return 'обновлено только что';
  if (s < 60) return `обновлено ${s} с назад`;
  if (s < 3600) return `обновлено ${Math.floor(s / 60)} мин назад`;
  if (s < 86400) return `обновлено ${Math.floor(s / 3600)} ч назад`;
  return `обновлено ${Math.floor(s / 86400)} дн назад`;
}
export function ageClass(sec) {
  const s = Number(sec) || 0;
  return s <= 60 ? '' : s <= 300 ? 'warn' : 'bad';
}

// Плашка связи: одна неудача — ещё не обрыв (медленная сеть), две подряд — показываем.
export function connText(fails, lastOkMs, now = Date.now(), status = 0) {
  if (!(fails >= 2)) return '';
  const base = status >= 400 && status < 500 ? `релей ответил ${status}` : 'нет связи с релеем';
  return lastOkMs ? `${base} · с ${fmtWhen(lastOkMs / 1000, now)}` : base;
}

const TERMINAL = new Set(['done', 'stopped', 'failed']);

// Без run_id в адресе: прогон, который ждёт ответа (пуш почти всегда о нём), затем живой.
export function pickRun(runs) {
  const list = Array.isArray(runs) ? runs.filter((r) => r && r.run_id) : [];
  return list.find((r) => String(r.status) === 'waiting_human')
    || list.find((r) => !TERMINAL.has(String(r.status)))
    || list[0] || {};
}

// Итог законченного прогона одной строкой; числа — только из секторов карточки.
export function runSummary(state) {
  const st = state && typeof state === 'object' ? state : {};
  const status = String(st.status || '');
  if (!TERMINAL.has(status)) return '';
  const view = st.view && typeof st.view === 'object' ? st.view : {};
  const secs = Array.isArray(view.sectors) ? view.sectors : [];
  const parts = [];
  if (secs.length) {
    const shipped = secs.filter((s) => s && s.color === 'green').length;
    const debt = secs.filter((s) => s && s.color === 'orange').length;
    parts.push(`сдано ${shipped} из ${secs.length}`);
    if (debt) parts.push(`долг ${debt}`);
  } else if (Array.isArray(st.debts) && st.debts.length) {
    parts.push(`долг ${st.debts.length}`);
  }
  const stop = String(view.stop_text || '');
  if (status === 'failed') parts.push('упал');
  else if (/^user_stop\b/.test(stop)) parts.push('остановлен вручную');
  else if (status === 'stopped') parts.push('остановлен');
  else parts.push(STATUS_LABELS.done);
  return parts.join(' · ');
}

// Ответ уходит не сразу: 5 с на «отменить». Чистая машина состояний — браузер
// только исполняет `send`. Один переход pending → send|отмена: send отдаётся
// ровно один раз, и pending в этот же шаг обнуляется.
export const ANSWER_DELAY_MS = 5000;
export function questionKey(state) {
  const st = state && typeof state === 'object' ? state : {};
  const q = st.question;
  if (!q || !q.id) return '';
  return JSON.stringify([st.run_id || '', st.machine_id || '', q.id, q.text || '',
    Array.isArray(q.actions) ? q.actions : []]);
}
export function pendingStep(pending, ev, now = Date.now()) {
  const e = ev || {};
  if (e.type === 'tap') {
    if (pending || !e.key) return { pending };
    return { pending: { action: e.action, text: e.text || '', key: e.key, target: e.target,
      deadline: now + ANSWER_DELAY_MS } };
  }
  if (!pending) return { pending: null };
  if (e.type === 'cancel') return { pending: null, note: 'отменено — ничего не отправлено' };
  if (e.type === 'state' && e.key !== pending.key) {
    return { pending: null, note: 'вопрос уже закрыт или сменился — ответ не отправлен' };
  }
  if (e.type === 'tick' && now >= pending.deadline) {
    // свёрнутая вкладка или телефон проспал дедлайн — без глаз хозяина не отправляем
    if (e.hidden || now > pending.deadline + 3000) {
      return { pending: null, note: 'страница была свёрнута — ответ не отправлен, нажми ещё раз' };
    }
    return { pending: null, send: pending };
  }
  return { pending };
}
export function renderPending(pending, now = Date.now()) {
  if (!pending) return '';
  const left = Math.max(0, Math.ceil((pending.deadline - now) / 1000));
  return `<p id="pending">«${escapeHtml(actionLabel(pending.action))}» через ${left} с`
    + ' <button id="pending-cancel" class="grey-on">отменить</button></p>';
}

function renderAnswered(answered, decisions) {
  const last = answered || (Array.isArray(decisions) && decisions[decisions.length - 1]) || null;
  const act = last && (last.action || last.what);
  const who = last && (last.by || last.who);
  const what = act ? ` — ${escapeHtml(actionLabel(act))}${who ? ` (${escapeHtml(WHO_LABELS[who] || who)})` : ''}` : '';
  return `<section id="answered"><p>уже отвечено${what}</p></section>`;
}

// Длинные списки: первые `head` видны, остальное под «ещё N».
function renderList(items, render, head = 0) {
  if (!Array.isArray(items) || !items.length) return '';
  const li = (item) => `<li>${render(item)}</li>`;
  if (!head || items.length <= head) return `<ul>${items.map(li).join('')}</ul>`;
  return `<ul>${items.slice(0, head).map(li).join('')}</ul>`
    + `<details><summary>ещё ${items.length - head}</summary><ul>${items.slice(head).map(li).join('')}</ul></details>`;
}

// «токены 174663» — размер контекста последнего хода (src/providers/limits.py).
function renderLimits(limits) {
  if (!limits || typeof limits !== 'object' || Array.isArray(limits)) return '';
  const rows = Object.entries(limits)
    .filter(([, v]) => typeof v === 'string' && v)
    .map(([k, v]) => {
      const n = /^токены (\d+)$/.exec(v);
      const val = n ? `${Math.round(Number(n[1]) / 1000)}k` : v;
      const bad = v === 'квота' || v === 'нужен вход';
      return `<span class="lim${bad ? ' bad' : ''}${v === 'неизвестно' ? ' dim' : ''}">${escapeHtml(k)} ${escapeHtml(val)}</span>`;
    });
  if (!rows.length) return '';
  return `<p class="limits">контекст: ${rows.join(' · ')}</p>`;
}

// Подряд одинаковые решения (тот же wake каждые 10 мин) — одной строкой «×N»;
// свежие сверху.
export function decisionRows(decisions) {
  const text = (d) => {
    if (!d || typeof d !== 'object') return '';
    if (d.kind === 'wake') {
      return `пробуждение (${REASON_LABELS[d.reason] || d.reason || ''}): ${d.result || ''}`;
    }
    const who = WHO_LABELS[d.who] || d.who || '';
    const why = d.why ? REASON_LABELS[d.why] || d.why : '';
    return `${who}: ${actionLabel(d.what || d.action)}${why ? ` (${why})` : ''}`;
  };
  // одинаковые строки (тот же wake каждые 10 мин вперемешку со skip) — одна строка
  // с последним временем и «×N»; порядок — по последнему появлению, свежие сверху
  const byText = new Map();
  let seq = 0;
  for (const d of Array.isArray(decisions) ? decisions : []) {
    const t = text(d);
    if (!t) continue;
    const when = fmtWhen(d.at ?? d.ts);
    const row = byText.get(t) || { text: t, when, n: 0 };
    row.n += 1;
    row.when = when || row.when;
    row.seq = seq++;
    byText.set(t, row);
  }
  return [...byText.values()].sort((x, y) => y.seq - x.seq).map(({ text: t, when, n }) => ({ text: t, when, n }));
}

export function renderPair(unpaired) {
  const head = unpaired
    ? '<h1>привязка недействительна</h1><p>токен отозван — введите новый код с экрана <code>tero pair</code></p>'
    : '<h1>привязка телефона</h1><p>введите код с экрана <code>tero pair</code></p>';
  return `<section id="${unpaired ? 'unpaired' : 'pair'}">${head}`
    + '<input type="text" id="pair-code" maxlength="16" placeholder="код из 8 букв" autocapitalize="characters"'
    + ' autocomplete="one-time-code"><button id="pair-btn">привязать</button><p id="pair-msg"></p></section>';
}

// Над карточкой: открытый вопрос первым (тап по пушу ведёт сюда), иначе итог,
// затем сообщение хозяину с фильтром «только хозяин» — всё это выше #log.
export function renderTop(state = {}, opts = {}) {
  const st = state && typeof state === 'object' ? state : {};
  const phoneText = st.phone_text_answers !== false;
  // Моделей judge в списке выбора нет: judge — не игрок. Роль выбора — player.
  const models = Array.isArray(st.models)
    ? st.models.map((m) => String(m).replace(/^(player|tester)\s+/, '')).filter((m) => m !== 'judge')
    : [];
  const head = st.question && st.question.id
    ? renderQuestion(st.question, phoneText, models)
    : '';
  return head + renderMessage(st, opts);
}

// Фильтр лога хозяина живёт рядом с полем сообщения, но не зависит от него:
// phone_text_answers прячет только отправку текста, не фильтрацию (F4).
function renderMessage(st, opts = {}) {
  const checked = opts.hostOnly ? ' checked' : '';
  const filter = '<label class="host-filter"><input type="checkbox" id="host-only"'
    + `${checked}> только хозяин</label>`;
  // законченному прогону писать некому: Worker ответит 422 — поле не показываем
  const over = ['done', 'stopped', 'failed'].includes(String(st.status));
  if (st.phone_text_answers === false || over) {
    return `<section id="message"><h2>${over ? 'лог' : 'сообщение хозяину'}</h2>${filter}</section>`;
  }
  return `<section id="message"><h2>сообщение хозяину</h2>${filter}`
    + '<input type="text" name="message" maxlength="2000" placeholder="написать хозяину…">'
    + '<button id="msg-btn">Отправить</button><p id="msg-error"></p></section>';
}

// Под карточкой по спеку: «уже отвечено», статус и лимиты, прогоны, решения, долг, пуши
// (сообщение — фаза 1; wake/resume и список «остановлены» — фазы 2–4).
export function renderBelow(state = {}, opts = {}) {
  const st = state && typeof state === 'object' ? state : {};
  const runs = Array.isArray(opts.runs) ? opts.runs : [];
  const isStoppedRun = (r) => r && (String(r.status) === 'done' || String(r.status) === 'stopped');
  const runLink = (r, extra = '') => `<a href="?run_id=${escapeHtml(r.run_id)}`
    + `&amp;machine_id=${escapeHtml(r.machine_id)}" data-run="${escapeHtml(r.run_id)}">`
    + `${escapeHtml(runLabel(r.run_id))}${r.run_id === st.run_id ? ' · открыт' : ''}`
    + ` · ${escapeHtml(STATUS_LABELS[r.status] || r.status)}${extra}</a>`;
  const liveRuns = runs.filter((r) => !isStoppedRun(r));
  const runsHtml = liveRuns.length
    ? `<section id="runs"><h2>прогоны</h2>${renderList(liveRuns, (r) => runLink(r))}</section>` : '';
  const stopped = runs.filter(isStoppedRun);
  const stoppedHtml = stopped.length ? `<section id="stopped"><h2>закончены</h2>`
    + renderList(stopped, (r) => runLink(r, r.updated_at ? ` · ${escapeHtml(fmtWhen(r.updated_at))}` : ''), 3)
    + `</section>` : '';
  const decisions = decisionRows(st.decisions);
  const decisionsHtml = decisions.length ? `<section id="decisions"><h2>решения</h2>`
    + renderList(decisions, (d) => `${d.when ? `<span class="when">${escapeHtml(d.when)}</span> ` : ''}`
      + `${escapeHtml(d.text)}${d.n > 1 ? ` <span class="times">×${d.n}</span>` : ''}`, 5)
    + '</section>' : '';
  const debtRow = (d) => {
    const findings = Array.isArray(d.findings) ? d.findings : [];
    const head = `фаза ${escapeHtml(d.phase)} — ${escapeHtml(REASON_LABELS[d.reason] || d.reason)}`
      + (findings.length ? ` · ${findings.length} ${findings.length === 1 ? 'находка' : 'находок'}` : '');
    if (!findings.length) return head;
    return `<details><summary>${head}</summary><ul class="findings">`
      + findings.map((f) => `<li>${escapeHtml(String(f).split(' → ')[0])}</li>`).join('') + '</ul></details>';
  };
  const debtsHtml = renderList(st.debts, debtRow);
  const historyHtml = renderList(st.history, (h) => `фаза ${escapeHtml(h.phase)} — ${escapeHtml(h.verdict)}`);
  const debtSection = debtsHtml || historyHtml
    ? `<section id="debt"><h2>долг и история фаз</h2>${debtsHtml}${historyHtml}</section>` : '';
  const agent = st.view && st.view.agent;
  const live = !['done', 'stopped', 'failed'].includes(String(st.status));
  const waitingN = st.view ? Number(st.view.messages_waiting) || 0 : 0;
  const noAgent = live && agent && agent.on_duty === false && waitingN > 0;
  const waiting = waitingN > 0
    ? `<p>${noAgent ? 'агента нет, сообщение ждёт' : `непрочитанных сообщений хозяину: ${waitingN}`}</p>` : '';
  const answeredLast = st.answered
    || (Array.isArray(st.decisions) ? [...st.decisions].reverse().find((d) => d && (d.action || d.what)) : null);
  // после конца прогона «уже отвечено» — шум: то же есть в решениях
  const answered = answeredLast && live ? renderAnswered(answeredLast, st.decisions) : '';
  const updatedMs = Date.parse(st.updated_at || '');
  const stale = Number.isFinite(updatedMs) && (Date.now() - updatedMs > STALE_AFTER_S * 1000);
  const wakeBtn = live && !stale && agent && agent.on_duty === false
    ? '<section id="wake"><button id="wake-btn">Разбудить агента</button><p id="wake-error"></p></section>' : '';
  // фаза, раунд, роль и модель уже в шапке карточки — здесь только то, чего там нет
  const statusWord = runSummary(st) || STATUS_LABELS[st.status] || st.status || '';
  // возраст данных тикает сам (data-at), без запроса; цвет — только у живого прогона
  const ageS = (Date.now() - updatedMs) / 1000;
  const fresh = Number.isFinite(updatedMs)
    ? ` · <span id="data-age" class="age ${live ? ageClass(ageS) : ''}" data-at="${updatedMs}"`
      + ` data-live="${live ? 1 : 0}">${escapeHtml(ageText(ageS))}</span>` : '';
  return answered + `<section id="run-status"><h2>статус</h2>`
    + `<p>${escapeHtml(statusWord)}${fresh}${stale && live ? ' · <b class="warn">ноут молчит</b>' : ''}</p>${waiting}`
    + wakeBtn
    + renderResume(opts.resume)
    // итог команды сторожа — вне секции resume: кнопка прячется, когда прогон ожил,
    // а итог (done/failed/lost) должен остаться виден
    + '<p id="resume-result"></p>'
    + renderLimits(st.limits) + '</section>'
    + runsHtml
    + stoppedHtml
    + decisionsHtml
    + debtSection
    + '<section id="push"><button id="push-btn">Включить пуши</button><p id="push-msg"></p></section>';
}

/* the phone's «только хозяин»: view.html's render() reads the global logFilter once a second */
export function applyHostOnly(win, on) {
  win.logFilter = on ? 'host' : '';
}

// Привязка чекбокса «только хозяин»: опрос заменяет innerHTML целиком — вернуть
// чекбоксу состояние фильтра, иначе включённый фильтр выглядит выключенным.
export function wireHostOnly(doc, win) {
  const box = doc.getElementById('host-only');
  if (!box) return null;
  box.checked = win.logFilter === 'host';
  box.onchange = () => applyHostOnly(win, box.checked);
  return box;
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
  // подсказка `tero watch --install` — для ноута; на телефоне кнопки просто нет
  if (!btn.show) return '';
  return '<section id="resume">'
    + (btn.field ? `<p>${escapeHtml(btn.why)}</p><input type="text" name="resume-text" maxlength="2000">` : '')
    + `<button id="resume-btn"${btn.disabled ? ' disabled class="grey"' : ''}>${escapeHtml(btn.text)}</button>`
    + '<p id="resume-error"></p></section>';
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
  // плашка «нет связи» — отдельно от #pult-top: её не стирает перерисовка
  const conn = document.createElement('div');
  conn.id = 'offline';
  conn.hidden = true;
  card.parentNode.insertBefore(conn, card);
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
  let answerNote = null;
  // связь: подряд идущие неудачи загрузки и время последней удачной
  let fails = 0;
  let lastFailStatus = 0;
  let lastOkMs = 0;
  let lastState = {};
  // ответ в отсчёте (pendingStep) и ответ в полёте — кнопки заперты
  let pending = null;
  let pendingTimer = null;
  let sending = false;
  // #pult-top не перерисовываем, пока хозяин печатает (не дольше 30 с подряд)
  let topKey = null;
  let frozenSince = 0;

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

  // Никогда не бросает: сеть упала → status 0. ok — только 2xx с JSON.
  async function get(path) {
    const headers = token ? { authorization: `Bearer ${token}` } : {};
    let res;
    try { res = await fetch(path, { headers }); } catch { return { status: 0, body: null, ok: false }; }
    let body = null;
    try { body = await res.json(); } catch { body = null; }
    return { status: res.status, body, ok: res.ok && body !== null };
  }

  function showConn() {
    const text = connText(fails, lastOkMs, Date.now(), lastFailStatus);
    conn.textContent = text;
    conn.hidden = !text;
    document.body.classList.toggle('stale', !!text);
    applyLocks();
  }

  function failed(status) {
    fails += 1;
    lastFailStatus = status;
    showConn();
    return 'fail';
  }

  function applyLocks() {
    const lock = !!pending || sending || fails >= 2;
    document.querySelectorAll('#question button[data-action]').forEach((b) => { b.disabled = lock; });
    const textEl = document.querySelector('#question textarea[name="text"]');
    if (textEl) textEl.readOnly = !!pending;
  }

  function drawPending() {
    const old = document.getElementById('pending');
    if (!pending) {
      if (old) old.remove();
    } else if (old) {
      const left = document.getElementById('pending-left');
      const fresh = document.createElement('div');
      fresh.innerHTML = renderPending(pending);
      const newLeft = fresh.querySelector('#pending-left');
      if (left && newLeft) left.textContent = newLeft.textContent;
    } else {
      // сразу под кнопками — там, куда смотрит палец, а не под полем и моделью
      const btns = document.querySelectorAll('#question button[data-action]');
      const last = btns.length ? btns[btns.length - 1] : document.getElementById('answer-msg');
      if (last) {
        last.insertAdjacentHTML(btns.length ? 'afterend' : 'beforebegin', renderPending(pending));
        const cancel = document.getElementById('pending-cancel');
        if (cancel) cancel.onclick = () => stepPending({ type: 'cancel' });
      }
    }
    applyLocks();
  }

  function stepPending(ev) {
    const r = pendingStep(pending, ev, Date.now());
    pending = r.pending;
    if (pending && !pendingTimer) {
      pendingTimer = setInterval(() => stepPending({ type: 'tick',
        hidden: document.visibilityState !== 'visible' }), 250);
    }
    if (!pending && pendingTimer) { clearInterval(pendingTimer); pendingTimer = null; }
    if (r.note) {
      const q = lastState && lastState.question;
      answerNote = { qid: q ? q.id : '', text: r.note };
      const el = document.getElementById('answer-msg');
      if (el) el.textContent = r.note;
    }
    drawPending();
    if (r.send) answer(r.send.action, r.send.text, r.send.target);
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

  // Одна загрузка за раз: вызов во время загрузки ставит «ещё раз», а не второй
  // параллельный запрос (ответы приходили бы в разном порядке и перерисовывали назад).
  let loading = null;
  let again = false;
  function load() {
    if (loading) { again = true; return loading; }
    loading = (async () => {
      try {
        do { again = false; await loadOnce(); } while (again);
      } finally { loading = null; }
    })();
    return loading;
  }

  async function loadOnce() {
    if (!token) {
      // PWA после «добавить на экран Домой» стартует с чистым хранилищем —
      // код из start_url меняем только здесь. Safari держит код в адресной строке (F14).
      const code = currentCode();
      if (code && isPwaLaunch()) {
        // не ждём: pair() сам вызовет load(), а ждать его отсюда — ждать самих себя
        pair(code, true);
        return 'ok';
      }
      showPair(false);
      return 'ok';
    }
    const params = new URLSearchParams(location.search);
    const runId = params.get('run_id');
    const machineId = params.get('machine_id');
    if (Date.now() - runsAt > 60000) {
      const runsRes = await get('/runs?all=1');
      if (runsRes.status === 401) { showPair(true); return 'unpaired'; }
      if (!runsRes.ok) return failed(runsRes.status);
      runs = (runsRes.body && runsRes.body.runs) || [];
      runsAt = Date.now();
    }
    const picked = pickRun(runs);
    const target = (runId && machineId)
      ? { run_id: runId, machine_id: machineId }
      : { run_id: picked.run_id, machine_id: picked.machine_id };
    let body = null;
    if (target.run_id && target.machine_id) {
      const st = await get(`/state?run_id=${encodeURIComponent(target.run_id)}`
        + `&machine_id=${encodeURIComponent(target.machine_id)}`);
      if (st.status === 401) { showPair(true); return 'unpaired'; }
      if (!st.ok) return failed(st.status);
      body = st.body || null;
    }
    fails = 0;
    lastOkMs = Date.now();
    const state = (body && body.state) || {};
    lastState = state;
    const qkey = questionKey(state);
    stepPending({ type: 'state', key: qkey });
    card.style.display = '';
    window.teroSource.set(viewOf(body, Date.now() / 1000));
    const draft = captureDraft(document);
    // поле «написать хозяину» тоже переживает перерисовку раз в 15 с
    const msgEl = document.querySelector('#message input[name="message"]');
    const msgDraft = msgEl ? { value: msgEl.value, focus: document.activeElement === msgEl } : null;
    const act = document.activeElement;
    const typing = !!act && top.contains(act) && /^(INPUT|TEXTAREA|SELECT)$/.test(act.tagName);
    const now = Date.now();
    if (typing && qkey === topKey) { if (!frozenSince) frozenSince = now; } else frozenSince = 0;
    const freeze = !!frozenSince && now - frozenSince < 30000;
    if (!freeze) {
      frozenSince = 0;
      top.innerHTML = renderTop(state, { hostOnly: window.logFilter === 'host' });
      topKey = qkey;
    }
    below.innerHTML = renderBelow(state, { runs, resume: resumeButton({ ...(body || {}), updated_at: state.updated_at,
      view: state.view }, Date.now() / 1000) });
    wire(state);
    restoreDraft(document, draft);
    const msgNew = document.querySelector('#message input[name="message"]');
    if (msgNew && msgDraft) {
      msgNew.value = msgDraft.value;
      if (msgDraft.focus) msgNew.focus();
    }
    const note = document.getElementById('answer-msg');
    if (note && answerNote && state.question && answerNote.qid === state.question.id) note.textContent = answerNote.text;
    drawPending();
    showConn();
    showPushState();
    refreshResumeResult(state);
    return 'ok';
  }

  async function pair(presetCode = '', auto = false) {
    const code = String(presetCode
      || (document.getElementById('pair-code') || {}).value || '').trim();
    const say = (msg) => { const el = document.getElementById('pair-msg'); if (el) el.textContent = msg; };
    if (!code) { say('введи код'); return; }
    say('проверяю код…');
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
    const why = {
      410: 'код истёк или уже использован — запусти `tero pair` ещё раз',
      429: 'слишком много неверных попыток — подожди и попробуй снова',
      404: 'такого кода нет — проверь буквы',
    }[res.status];
    say(why || (body && body.error) || `не вышло: ${res.status}`);
  }

  async function answer(action, text, target) {
    const payload = { question_id: target.id, action, text: text || '' };
    if (target.run_id) payload.run_id = target.run_id;
    if (target.machine_id) payload.machine_id = target.machine_id;
    // итог ответа (и отказ тоже) запоминается в answerNote: опрос раз в 4 с иначе стирал его
    const say = (msg) => {
      answerNote = { qid: target.id, text: msg };
      const el = document.getElementById('answer-msg');
      if (el) el.textContent = msg;
    };
    const btns = document.querySelectorAll('#question button[data-action]');
    btns.forEach((b) => { b.disabled = true; });
    say(`отправляю «${actionLabel(action)}»…`);
    sending = true;
    let res;
    try {
      res = await fetch('/answer', {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify(payload),
      });
    } catch {
      sending = false;
      btns.forEach((b) => { b.disabled = false; });
      say('нет сети — ответ не ушёл, нажми ещё раз');
      return;
    }
    sending = false;
    if (!res.ok) {
      const body = await res.json().catch(() => ({}));
      if (res.status === 401 && body.error === 'unpaired') { showPair(true); return; }
      btns.forEach((b) => { b.disabled = false; });
      say(answerErrorText(res.status, body.error));
      return;
    }
    // перерисовка каждые 15 с стёрла бы это — load() кладёт его обратно, пока вопрос тот же
    say(`«${actionLabel(action)}» отправлено — ноут заберёт ответ`);
    await load();
  }

  function wire(state) {
    const q = (state && state.question) || {};
    const section = document.getElementById('question');
    if (section) {
      const target = { id: q.id, run_id: state.run_id, machine_id: state.machine_id };
      const key = questionKey(state);
      // любое действие (и «стоп») — сначала 5 с с кнопкой «отменить», потом POST
      section.querySelectorAll('button[data-action]').forEach((btn) => {
        btn.onclick = () => {
          const action = btn.dataset.action;
          const textEl = section.querySelector('textarea[name="text"]');
          let text = action === 'stop' ? '' : (textEl ? textEl.value : '');
          if (action === 'model') {
            const modelEl = section.querySelector('select[name="model"]');
            const model = modelEl ? String(modelEl.value || '').trim() : '';
            // Без текстового поля нет честного model: пустой текст дал бы 422.
            if (!textEl && !model) { load(); return; }
            text = model ? `player ${model}` : text;
          }
          stepPending({ type: 'tap', action, text, key, target });
        };
      });
    }
    const pushBtn = document.getElementById('push-btn');
    if (pushBtn) pushBtn.onclick = enablePush;
    wireHostOnly(document, window);
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
    if (res.ok) {
      if (box) box.value = '';
      if (errEl) errEl.textContent = 'отправлено';
      if (box) box.blur();
      load();  // сообщение попадает в лог — показать сразу
      return;
    }
    const body = await res.json().catch(() => ({}));
    if (errEl) errEl.textContent = String(body.error || `отказ: ${res.status}`);
  }

  // Пуши уже включены — кнопка не врёт «Включить»; иначе раньше она молчала на любой отказ.
  let pushOn = false;
  async function showPushState() {
    const btn = document.getElementById('push-btn');
    if (!btn) return;
    if (!pushOn && 'serviceWorker' in navigator && 'PushManager' in window) {
      try {
        const reg = await navigator.serviceWorker.getRegistration('/sw.js');
        pushOn = !!(reg && await reg.pushManager.getSubscription());
      } catch { pushOn = false; }
    }
    const el = document.getElementById('push-btn');
    if (el && pushOn) { el.textContent = 'Пуши включены ✓ (переподписать)'; el.classList.add('grey-on'); }
  }

  function pushMsg(text) {
    const el = document.getElementById('push-msg');
    if (el) el.textContent = text;
  }

  async function enablePush() {
    if (!('serviceWorker' in navigator) || !('PushManager' in window)) {
      pushMsg(isPwaLaunch() ? 'этот браузер не умеет пуши'
        : 'пуши на айфоне только из иконки на экране «Домой»: Поделиться → На экран «Домой»');
      return;
    }
    try {
      // iOS молча отказывает без явного запроса из жеста: только по кнопке.
      if (window.Notification && Notification.requestPermission) {
        if (Notification.permission === 'denied') {
          pushMsg('уведомления запрещены: Настройки → Уведомления → теро-пульт');
          return;
        }
        if (Notification.permission !== 'granted' && await Notification.requestPermission() !== 'granted') {
          pushMsg('уведомления не разрешены');
          return;
        }
      }
      pushMsg('подписываю…');
      const reg = await navigator.serviceWorker.register('/sw.js');
      const vapidRes = await get('/push/vapid');
      const key = vapidRes.body && vapidRes.body.vapid_public_key;
      if (!key) { pushMsg('на сервере не задан ключ пушей'); return; }
      // Сервер отдаёт публичный ключ JWK-JSON; subscribe нужен несжатый P-256.
      const appKey = jwkToU8(key) || urlToU8(key);
      if (!appKey) { pushMsg('ключ пушей на сервере битый'); return; }
      const sub = await reg.pushManager.subscribe({ userVisibleOnly: true, applicationServerKey: appKey.buffer });
      const p256dh = u8ToB64(new Uint8Array(sub.getKey('p256dh')));
      const auth = u8ToB64(new Uint8Array(sub.getKey('auth')));
      if (!p256dh || !auth) { pushMsg('браузер не отдал ключи подписки'); return; }
      const res = await fetch('/push/subscribe', {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify({ endpoint: sub.endpoint, p256dh, auth }),
      });
      if (!res.ok) { pushMsg(`сервер не принял подписку: ${res.status}`); return; }
      pushOn = true;
      pushMsg('готово, пуши включены');
      showPushState();
    } catch (e) {
      pushMsg(`не вышло: ${e && e.message ? e.message : e}`);
    }
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

  // Опрос: 4 с, пока открыт вопрос (ответ мог уйти с Мака), иначе 15 с; только при
  // видимой вкладке. Сеть упала — карточка остаётся на последнем view, плашка после 2 неудач.
  let pollTimer = null;
  function schedule() {
    clearTimeout(pollTimer);
    if (document.visibilityState !== 'visible') return;
    const open = lastState && lastState.question && lastState.question.id;
    pollTimer = setTimeout(tick, open ? 4000 : 15000);
  }
  const tick = () => load().catch((e) => console.warn('pult:', e)).finally(schedule);
  document.addEventListener('visibilitychange', () => {
    if (document.visibilityState === 'visible') {
      runsAt = 0;  // вернулись из пуша — список прогонов мог смениться
      tick();
    } else clearTimeout(pollTimer);
  });
  // «обновлено N назад» тикает само, без запроса
  setInterval(() => {
    const el = document.getElementById('data-age');
    if (!el) return;
    const sec = (Date.now() - Number(el.dataset.at)) / 1000;
    el.textContent = ageText(sec);
    el.className = `age ${el.dataset.live === '1' ? ageClass(sec) : ''}`;
  }, 5000);
  tick();
}
