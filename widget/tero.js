// widget/tero.js — виджет tero для Scriptable (план 08-9).
// Без синтаксиса ES-модулей: Scriptable исполняет файл как скрипт; node
// импортирует его же как CommonJS (module.exports внизу).
// Разметка — чистая функция layout(state, family): файл импортируется в
// node без Scriptable API, вызовы ListWidget/Request/args — только под
// typeof-гардом. Токен читается из args.widgetParameter, в коде его нет.

// widgetParameter несёт токен и, через "|", адрес Worker. Без адреса —
// развёрнутый ретранслятор (не localhost: на телефоне это сам телефон, F12;
// не .invalid: такой домен не резолвится). Адрес — не секрет, токена в коде нет.
const DEFAULT_WORKER_BASE = 'https://tero-relay.highskilltero.workers.dev';

function limitLine(state) {
  const limits = state && typeof state === 'object' ? state.limits : null;
  if (!limits || typeof limits !== 'object' || Array.isArray(limits)) return 'лимит — неизвестно';
  const known = Object.values(limits).find((v) => typeof v === 'string' && v && v !== 'неизвестно');
  if (known) return `лимит ${String(known)}`.slice(0, 80);
  return 'лимит — неизвестно';
}

function headLine(state) {
  // Relay отдаёт название фазы + phase_index; виджет показывает i/n.
  const st = state && typeof state === 'object' ? state : {};
  const idx = Number(st.phase_index);
  const phase = Number.isFinite(idx) && idx >= 0 ? `${idx + 1}` : null;
  const total = Number.isFinite(Number(st.total_phases)) && Number(st.total_phases) > 0
    ? `${Number(st.total_phases)}` : null;
  if (phase !== null && total !== null) return `${phase}/${total} · р${st.round ?? '?'}`;
  if (phase !== null) return `${phase} · р${st.round ?? '?'}`;
  return `${st.phase || '?'} · р${st.round ?? '?'}`;
}

// Request — под typeof-гардом: node этот блок не видит.
async function loadJson(url, token) {
  if (typeof Request === 'undefined') return null;
  const req = new Request(url);
  req.headers = { authorization: `Bearer ${token}` };
  return req.loadJSON();
}

// Семьи экрана блокировки Scriptable: только взглянуть, 2–3 строки.
// Всё остальное (small/medium/large дома) — подробный экран.
function isLockFamily(family) {
  if (family === undefined || family === null || family === '') return true;
  const f = String(family);
  return f === 'lock' || f.startsWith('accessory');
}

// Экран блокировки: 2–3 строки (фаза · раунд / вопрос / лимит).
// Домашний экран — подробнее: статус, роль/модель, вопрос, лимит.
function layout(state = {}, family) {
  const st = state && typeof state === 'object' ? state : {};
  const head = headLine(st);
  const rows = [head];
  if (st.question && typeof st.question === 'object' && st.question.id) {
    rows.push('⚠ ответ');
  } else {
    rows.push(st.status ? String(st.status).slice(0, 40) : 'нет вопроса');
  }
  if (!isLockFamily(family)) {
    if (st.role || st.model) rows.push(`${st.role || '?'} · ${st.model || '?'}`.slice(0, 80));
    rows.push(limitLine(st).slice(0, 80));
    return rows;
  }
  rows.push(limitLine(st).slice(0, 40));
  return rows.slice(0, 3);
}

// Точка входа Scriptable: токен только из параметра виджета.
// fetch состояния — через Request под гардом (node его не видит);
// адрес Worker читается из args.widgetParameter вместе с токеном.
// Формат параметра: "READ|https://host|run_id|machine_id|семья".
// run_id/machine_id опциональны: без них виджет сначала читает GET /runs
// (как страница-пульт) и берёт первый незавершённый прогон.
// Без адреса Worker — DEFAULT_WORKER_BASE (развёрнутый ретранслятор).
// Тап ведёт на пульт с #token= (и run_id/machine_id, если заданы).
async function runWidget() {
  if (typeof ListWidget === 'undefined') return layout({}, 'lock');
  const raw = String((typeof args !== 'undefined' && args.widgetParameter) || '');
  const [token, baseArg, runId, machineId, fam] = raw.split('|').map((s) => (s || '').trim());
  const cleanBase = (baseArg || DEFAULT_WORKER_BASE).replace(/\/+$/, '');
  const w = new ListWidget();
  if (!token) {
    w.addText('tero: нет токена');
  } else {
    let state = {};
    let got = null;
    let rid = runId || '', mid = machineId || '';
    if (typeof Request !== 'undefined') {
      // ids из параметра — сразу /state; иначе discovery как у страницы:
      // GET /runs → первый незавершённый прогон → /state с его ids.
      if (rid && mid) {
        try {
          const body = await loadJson(`${cleanBase}/state?run_id=${encodeURIComponent(rid)}`
            + `&machine_id=${encodeURIComponent(mid)}`, token);
          if (body && typeof body === 'object') state = body.state || body;
        } catch { state = {}; }
      } else {
        try {
          got = await loadJson(`${cleanBase}/runs`, token);
        } catch { got = null; }
        if (got && typeof got === 'object') {
          if (got.state && typeof got.state === 'object') {
            state = got.state;  // прямая выдача состояния (тестовые подделки)
          } else if (Array.isArray(got.runs) && got.runs.length) {
            rid = String(got.runs[0].run_id || '');
            mid = String(got.runs[0].machine_id || '');
            if (rid && mid) {
              try {
                const body = await loadJson(`${cleanBase}/state?run_id=${encodeURIComponent(rid)}`
                  + `&machine_id=${encodeURIComponent(mid)}`, token);
                if (body && typeof body === 'object') state = body.state || body;
              } catch { state = {}; }
            }
          }
        }
      }
    }
    const family = (typeof config !== 'undefined' && config.widgetFamily) || fam || 'lock';
    for (const row of layout(state, family)) w.addText(row);
    if (cleanBase && typeof Safari !== 'undefined') {
      // Токен — во фрагменте (#), не в query: фрагмент не уходит на сервер (B18).
      w.url = `${cleanBase}/`
        + (runId ? `?run_id=${encodeURIComponent(runId)}` : '')
        + (runId && machineId ? `&machine_id=${encodeURIComponent(machineId)}` : '')
        + `#token=${encodeURIComponent(token)}`;
    }
  }
  if (typeof Script !== 'undefined') Script.setWidget(w);
  return w;
}

if (typeof module !== 'undefined' && module.exports) {
  module.exports = { DEFAULT_WORKER_BASE, layout, limitLine, runWidget };
}

// Точка входа Scriptable: собрать виджет, отдать его системе и завершить скрипт.
if (typeof ListWidget !== 'undefined' && typeof Script !== 'undefined') {
  runWidget().then(() => Script.complete());
}
