// relay/public/render.js — чистая функция страницы-пульта (фаза 08-8).
// Одна и та же логика в браузере (import из index.html) и в тестах node.
// Текст с телефона и состояние — данные: всё интерполированное экранируется.

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// Черновик ответа переживает 15-секундную перерисовку #question (F17).
// doc — document или его тестовая подделка с getElementById/activeElement.
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

function renderQuestion(question, phoneText, models) {
  // Только действия из question.actions: Worker примет лишь их, запасные кнопки
  // получили бы 422. Пустой список — кнопок нет.
  const rawActions = Array.isArray(question.actions) ? question.actions.slice() : [];
  // Без текстовых ответов только кнопки more|skip|retry|stop: текстовые
  // действия (answer, allow, model) приняли бы пустой ввод или текст по
  // 422-пути («text answers disabled»).
  const SAFE_SILENT = new Set(['more', 'skip', 'retry', 'stop']);
  const actions = phoneText === false
    ? rawActions.filter((a) => SAFE_SILENT.has(String(a)))
    : rawActions;
  const buttons = actions.map((a) => escapeHtml(String(a))).filter(Boolean)
    .map((action) => action === 'stop'
      ? `<button data-action="stop" onclick="return confirm('Остановить прогон?')">stop</button>`
      : `<button data-action="${action}">${action}</button>`).join('\n');
  const text = phoneText === false ? '' : '<textarea name="text" maxlength="2000"></textarea>';
  const wantsModel = actions.includes('model');
  const modelSel = wantsModel && Array.isArray(models) && models.length
    ? `<select name="model" aria-label="model">${models.map((m) => `<option>${escapeHtml(m)}</option>`).join('')}</select>`
    : '';
  if (!buttons) {
    return `<section id="question"><h2>вопрос</h2>`
      + `<p>${escapeHtml(question.text)}</p>`
      + `<p class="qid">${escapeHtml(question.id)}</p>`
      + `<p>нет действий для ответа с телефона</p></section>`;
  }
  return `<section id="question"><h2>вопрос</h2>`
    + `<p>${escapeHtml(question.text)}</p>`
    + `<p class="qid">${escapeHtml(question.id)}</p>`
    + buttons + text + modelSel + `</section>`;
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

export function renderPage(state = {}, opts = {}) {
  const token = opts.token || '';
  const runs = Array.isArray(opts.runs) ? opts.runs : [];
  if (!token) {
    return `<!doctype html><html lang="ru"><head><meta charset="utf-8">`
      + `<title>теро-пульт</title></head><body>`
      + `<section id="pair"><h1>привязка телефона</h1>`
      + `<p>введите код с экрана <code>tero pair</code></p>`
      + `<input id="pair-code" maxlength="16"><button id="pair-btn">привязать</button>`
      + `</section></body></html>`;
  }
  if (opts.unpaired) {
    return `<!doctype html><html lang="ru"><head><meta charset="utf-8">`
      + `<title>теро-пульт</title></head><body>`
      + `<section id="unpaired"><h1>привязка недействительна</h1>`
      + `<p>токен отозван — введите новый код с экрана <code>tero pair</code></p>`
      + `<input id="pair-code" maxlength="16"><button id="pair-btn">привязать</button>`
      + `</section></body></html>`;
  }
  const st = state && typeof state === 'object' ? state : {};
  const phoneText = st.phone_text_answers !== false;
  // Моделей judge в списке выбора нет: judge — не игрок.
  // Роль выбора — player: текст `model <player|tester> <спека>` принимает refuse_model,
  // голая спека без роли отклоняется.
  const models = Array.isArray(st.models)
    ? st.models.map((m) => String(m).replace(/^(player|tester)\s+/, '')).filter((m) => m !== 'judge')
    : [];
  const runsHtml = renderList(runs, (r) => `<a href="?run_id=${escapeHtml(r.run_id)}`
    + `&amp;machine_id=${escapeHtml(r.machine_id)}" data-run="${escapeHtml(r.run_id)}">`
    + `${escapeHtml(r.run_id)} · ${escapeHtml(r.status)}</a>`);
  const eventsHtml = renderList(st.events, (e) => `${escapeHtml(e.at)} — ${escapeHtml(e.text)}`);
  const decisionsHtml = renderList(st.decisions, (d) => `${escapeHtml(d.at)} — ${escapeHtml(d.who)}: `
    + `${escapeHtml(d.what)}${d.why ? ` (${escapeHtml(d.why)})` : ''}`);
  const debtsHtml = renderList(st.debts, (d) => `${escapeHtml(d.phase)} — ${escapeHtml(d.reason)}`);
  const historyHtml = renderList(st.history, (h) => `${escapeHtml(h.phase)} — ${escapeHtml(h.verdict)}`);
  const questionHtml = st.question && st.question.id    ? renderQuestion(st.question, phoneText, models)
    : (st.answered || (Array.isArray(st.decisions) && st.decisions.length)
      ? renderAnswered(st.answered, st.decisions)
      : '<section id="idle"><p>нет открытого вопроса</p></section>');
  // Корень всегда показывает открытый вопрос первым: тап по пушу ведёт сюда.
  return `<!doctype html><html lang="ru"><head><meta charset="utf-8">`
    + `<title>теро-пульт</title></head><body>`
    + questionHtml
    + `<section id="status"><h2>статус</h2>`
    + `<p>${escapeHtml(st.status)} · фаза ${escapeHtml(st.phase)} · раунд ${escapeHtml(st.round)} · `
    + `${escapeHtml(st.role)} · ${escapeHtml(st.model)}</p>`
    + `<section id="limits"><h2>лимиты</h2>${renderLimits(st.limits)}</section></section>`
    + `<section id="runs"><h2>прогоны</h2>${runsHtml}</section>`
    + `<section id="events"><h2>журнал</h2>${eventsHtml}</section>`
    + `<section id="decisions"><h2>решения</h2>${decisionsHtml}</section>`
    + `<section id="debt"><h2>долг и история фазы</h2>${debtsHtml}${historyHtml}</section>`
    + `<section id="push"><button id="push-btn">Включить пуши</button></section>`
    + `</body></html>`;
}
