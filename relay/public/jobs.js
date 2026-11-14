// relay/public/jobs.js — страница /jobs.html: карточки job/ask/approve/captcha/stopped/status.
// Чистые функции до браузерного блока (гонка в node: relay/test/jobs-page.test.js), по образцу pult.js.
// Весь текст с ноута — данные: экранируется; исключение — resume_html, где допускается только <mark>.

export function escapeHtml(value) {
  return String(value ?? '')
    .replace(/&/g, '&amp;')
    .replace(/</g, '&lt;')
    .replace(/>/g, '&gt;')
    .replace(/"/g, '&quot;');
}

// resume_html из ноута: <mark> проходит как есть, остальные теги экранируются целиком.
export function safeMarks(html) {
  const s = String(html ?? '');
  return s.split(/(<\/?mark>)/).map((part) => (part === '<mark>' || part === '</mark>' ? part : escapeHtml(part))).join('');
}

// Те же общие шаблоны, что relay/src/jobs.js scrub (шаг 2): почта, телефон, адрес.
const PHONE_PATTERN = /\+?\d(?:[\s().-]*\d){9,14}/;
export const CONTACT_PATTERNS = [
  /[^\s@]+@[^\s@]+\.[^\s@]+/,
  PHONE_PATTERN,
  /\d+ \w+ (?:St|Street|Ave|Avenue|Rd|Road|Blvd|Way|Dr|Drive)\b/i,
];
export function hasContact(text) {
  const flat = String(text ?? '').normalize('NFKC');
  return CONTACT_PATTERNS.some((re) => re.test(flat));
}

export function isHttps(url) {
  try {
    return new URL(String(url)).protocol === 'https:';
  } catch {
    return false;
  }
}

function linkOrText(url, label) {
  if (!url) return '';
  if (isHttps(url)) return `<a href="${escapeHtml(url)}">${escapeHtml(label ?? url)}</a>`;
  return `<p class="raw-link">${escapeHtml(url)}</p>`;
}

// Плашка «ноут молчит»: отдельно от сети телефона — про тишину демона на ноуте.
export function laptopText(laptopSeenMs, nowMs) {
  const seen = Number(laptopSeenMs);
  const now = nowMs === undefined ? Date.now() : Number(nowMs);
  if (!Number.isFinite(seen) || !Number.isFinite(now) || now - seen <= 300000) return '';
  const d = new Date(seen);
  const p = (n) => ('0' + n).slice(-2);
  return `ноут молчит с ${p(d.getHours())}:${p(d.getMinutes())}`;
}

export function swipeAction(dx) {
  if (dx > 80) return 'yes';
  if (dx < -80) return 'no';
  return null;
}

// Собирает POST /jobs/decide body: ask шлёт все ответы сразу, остальные — пустые data.
export function decisionBody(card, action, form) {
  if (action === 'answers') {
    const items = (card.payload && card.payload.items) || [];
    const entries = [];
    for (const item of items) {
      const value = form && Object.hasOwn(form, item.label) ? String(form[item.label]) : '';
      if (hasContact(item.label) || hasContact(value)) {
        throw new Error('contact');
      }
      entries.push([item.label, value]);
    }
    // Object.fromEntries определяет свойства напрямую, не через присваивание —
    // метка "__proto__" не попадёт в сеттер прототипа и не потеряется (F: jobs.js:71).
    return { card_id: card.id, version: card.version, action, data: Object.fromEntries(entries) };
  }
  if (action === 'engine') {
    return { card_id: card.id, version: card.version, action, data: { engine: form && form.engine } };
  }
  return { card_id: card.id, version: card.version, action, data: {} };
}

// keepOff: на карточке капчи «сменить» на тот же движок — не выбор, кнопка текущего выключена.
function engineToggle(current, locked, keepOff = false) {
  const perm = ' disabled data-perm-disabled="1"';
  const off = (e) => ((e === 'browser-act' && locked) || (keepOff && e === current) ? perm : '');
  const mark = (e) => (e === current ? ' class="on"' : '');
  return '<div class="engine">'
    + `<button data-action="engine" data-engine="chrome"${off('chrome')}${mark('chrome')}>chrome</button>`
    + `<button data-action="engine" data-engine="browser-act"${off('browser-act')}${mark('browser-act')}>browser-act`
    + (locked ? ' <small>ещё не собран</small>' : '') + '</button></div>';
}

function renderPrepared(prepared) {
  if (!prepared) return '';
  const known = prepared.known || [];
  const ask = prepared.ask || [];
  const knownList = known.map((item) => `<li>${escapeHtml(item.label)}: ${escapeHtml(item.value)}</li>`).join('');
  const drafts = ask.filter((item) => item.draft)
    .map((item) => `<li>${escapeHtml(item.label)}: ${escapeHtml(item.draft)}</li>`).join('');
  return `<div class="prepared"><p>готово из базы: ${known.length}</p>`
    + (knownList ? `<ul class="known">${knownList}</ul>` : '')
    + `<p>к тебе: ${ask.length} вопрос${ask.length === 1 ? '' : 'а'}</p>`
    + (drafts ? `<ul class="drafts">${drafts}</ul>` : '')
    + '</div>';
}

function renderJob(id, version, p) {
  return `<section class="card" data-card-id="${escapeHtml(id)}" data-version="${escapeHtml(version)}">`
    + `<h3>${escapeHtml(p.title)}</h3><p class="company">${escapeHtml(p.company)} · ${escapeHtml(p.location || '')}</p>`
    + (p.salary ? `<p class="salary">${escapeHtml(p.salary)}</p>` : '')
    + (p.why ? `<p class="why">${escapeHtml(p.why)}</p>` : '')
    + linkOrText(p.url, 'ссылка на вакансию')
    + renderPrepared(p.prepared)
    + engineToggle(p.engine, true)
    + '<div class="actions"><button data-action="yes">да</button><button data-action="no">нет</button></div>'
    + '<p class="card-msg"></p></section>';
}

function renderAskItem(item) {
  const label = escapeHtml(item.label);
  if (item.kind === 'text') {
    return `<label class="q">${label}<textarea name="${label}" maxlength="2000" data-required="${item.required ? 1 : 0}">`
      + `${escapeHtml(item.draft || '')}</textarea></label>`;
  }
  // Кнопки — сами варианты формы: ноут принимает ответ, только если он совпал с одним из них.
  const own = item.options || [];
  const options = item.kind === 'yesno' && own.length === 0 ? ['Yes', 'No'] : own;
  const buttons = options.map((o) => `<button type="button" data-label="${label}" data-option="${escapeHtml(o)}" aria-pressed="false">${escapeHtml(o)}</button>`).join('');
  return `<div class="q" data-required="${item.required ? 1 : 0}" data-label="${label}"><p>${label}</p>${buttons}</div>`;
}

function renderAsk(id, version, p) {
  const items = (p.items || []).map(renderAskItem).join('');
  return `<section class="card" data-card-id="${escapeHtml(id)}" data-version="${escapeHtml(version)}">`
    + `<h3>${escapeHtml(p.title)}</h3><p class="company">${escapeHtml(p.company)}</p>${items}`
    + '<p class="contact-note">это ноут знает сам — не пиши сюда почту или телефон</p>'
    + '<div class="actions"><button data-action="answers" disabled>отправить ответы</button>'
    + '<button data-action="stop">стоп</button></div><p class="card-msg"></p></section>';
}

function renderApprove(id, version, p) {
  const filled = (p.filled || []).map((f) => `<li>${escapeHtml(f.label)}: ${escapeHtml(f.value)}</li>`).join('');
  return `<section class="card" data-card-id="${escapeHtml(id)}" data-version="${escapeHtml(version)}">`
    + `<h3>${escapeHtml(p.title)}</h3><p class="company">${escapeHtml(p.company)}</p>`
    + `<div class="resume">${safeMarks(p.resume_html || '')}</div><ul class="filled">${filled}</ul>`
    + '<div class="actions"><button data-action="send">отправить</button><button data-action="decline">не отправлять</button></div>'
    + '<p class="card-msg"></p></section>';
}

function renderCaptcha(id, version, p) {
  return `<section class="card" data-card-id="${escapeHtml(id)}" data-version="${escapeHtml(version)}">`
    + `<h3>${escapeHtml(p.title)}</h3><p class="company">${escapeHtml(p.company)}</p><p>${escapeHtml(p.hint)}</p>`
    + (p.assist_url ? linkOrText(p.assist_url, 'remote-assist') : '')
    + engineToggle(p.engine, true, true)
    + '<div class="actions"><button data-action="captcha_done">прошёл</button><button data-action="stop">стоп</button></div>'
    + '<p class="card-msg"></p></section>';
}

function renderStopped(id, version, p) {
  return `<section class="card" data-card-id="${escapeHtml(id)}" data-version="${escapeHtml(version)}">`
    + `<h3>${escapeHtml(p.title)}</h3><p class="company">${escapeHtml(p.company)}</p><p class="reason">${escapeHtml(p.reason)}</p>`
    + linkOrText(p.url, 'ссылка на вакансию')
    + engineToggle(p.engine, true)
    + '<div class="actions"><button data-action="no">убрать</button></div><p class="card-msg"></p></section>';
}

function renderStatus(id, version, p) {
  return `<section class="card" data-card-id="${escapeHtml(id)}" data-version="${escapeHtml(version)}">`
    + `<h3>${escapeHtml(p.title)}</h3><p class="company">${escapeHtml(p.company)}</p><p class="stage">${escapeHtml(p.stage)}</p>`
    + linkOrText(p.url, 'ссылка на вакансию')
    + '<div class="actions"><button data-action="no">убрать</button></div></section>';
}

export function renderCard(card) {
  const p = card.payload || {};
  if (card.kind === 'job') return renderJob(card.id, card.version, p);
  if (card.kind === 'ask') return renderAsk(card.id, card.version, p);
  if (card.kind === 'approve') return renderApprove(card.id, card.version, p);
  if (card.kind === 'captcha') return renderCaptcha(card.id, card.version, p);
  if (card.kind === 'stopped') return renderStopped(card.id, card.version, p);
  if (card.kind === 'status') return renderStatus(card.id, card.version, p);
  return '';
}

// status карточки задания скрыты, пока у того же задания висит ask/approve/captcha.
function slugOf(id) {
  const i = String(id).indexOf(':');
  return i === -1 ? id : id.slice(i + 1);
}
export function filterStatusCards(cards) {
  const pending = new Set(cards.filter((c) => c.kind === 'ask' || c.kind === 'approve' || c.kind === 'captcha').map((c) => slugOf(c.id)));
  return cards.filter((c) => c.kind !== 'status' || !pending.has(slugOf(c.id)));
}

export const BATCH_HOURS = [9, 13, 18];
function bostonHour(nowMs) {
  const parts = new Intl.DateTimeFormat('en-US', { timeZone: 'America/New_York', hour: 'numeric', hour12: false })
    .formatToParts(new Date(nowMs));
  return Number(parts.find((p) => p.type === 'hour').value) % 24;
}
export function nextBatchText(nowMs) {
  const now = nowMs === undefined ? Date.now() : Number(nowMs);
  const hour = bostonHour(now);
  const next = BATCH_HOURS.find((h) => h > hour) ?? BATCH_HOURS[0];
  return `пусто — новых вакансий нет, следующая пачка в ${('0' + next).slice(-2)}:00`;
}

// ---------------------------------------------------------------- браузер
const inBrowser = typeof document !== 'undefined' && typeof fetch === 'function' && document.getElementById;

if (inBrowser) {
  const root = document.getElementById('cards');
  const banner = document.getElementById('laptop-banner');
  const offline = document.getElementById('offline');
  const modeButtons = document.querySelectorAll('#settings button[data-mode]');
  const pushCapInput = document.getElementById('push-cap');
  const token = localStorage.getItem('tero_token') || '';

  function updateSettingsUI(mode, pushCap) {
    modeButtons.forEach((b) => b.classList.toggle('on', b.dataset.mode === mode));
    pushCapInput.dataset.last = String(pushCap);
    if (document.activeElement !== pushCapInput) pushCapInput.value = pushCap;
  }

  modeButtons.forEach((b) => {
    b.onclick = async () => { await post('/jobs/settings', { mode: b.dataset.mode }); load(); };
  });
  if (pushCapInput) {
    pushCapInput.onchange = async () => {
      // F6: только целое 1..7 уходит на релей; дробное (2.5) не отправляем,
      // показываем ошибку и возвращаем последнее известное значение.
      const raw = Number(pushCapInput.value);
      if (!Number.isInteger(raw) || raw < 1 || raw > 7) {
        const msg = document.getElementById('settings-msg');
        if (msg) msg.textContent = 'потолок — целое число 1..7';
        if (pushCapInput.dataset.last !== undefined) pushCapInput.value = pushCapInput.dataset.last;
        return;
      }
      pushCapInput.dataset.last = String(raw);
      await post('/jobs/settings', { push_cap: raw });
      load();
    };
  }

  if (!token) {
    root.innerHTML = '<p id="unpaired">телефон не привязан — <a href="/">привязать</a></p>';
  }

  let fails = 0;
  let offlineLocked = false;
  let cardsById = new Map();
  // F4: id карточек с незавершённым POST /jobs/decide (счётчик висящих запросов).
  // Блокировка живёт до ответа исходного POST, даже если опрос принёс новую версию.
  const pendingPosts = new Map();

  async function get(path) {
    const headers = token ? { authorization: `Bearer ${token}` } : {};
    try {
      const res = await fetch(path, { headers });
      const body = await res.json().catch(() => null);
      return { status: res.status, body, ok: res.ok && body !== null };
    } catch {
      return { status: 0, body: null, ok: false };
    }
  }

  async function post(path, data) {
    try {
      const res = await fetch(path, {
        method: 'POST',
        headers: { 'content-type': 'application/json', authorization: `Bearer ${token}` },
        body: JSON.stringify(data),
      });
      const body = await res.json().catch(() => ({}));
      return { status: res.status, body, ok: res.ok };
    } catch {
      return { status: 0, body: null, ok: false };
    }
  }

  function lockSection(section, on) {
    section.querySelectorAll('button[data-action], button[data-label], textarea[name]').forEach((b) => {
      if (b.dataset.permDisabled === '1') return;
      b.disabled = on;
    });
  }

  function wireCard(section, card) {
    const msg = section.querySelector('.card-msg');
    const lock = (on) => lockSection(section, on);

    section.querySelectorAll('button[data-label]').forEach((btn) => {
      btn.onclick = () => {
        const label = btn.dataset.label;
        const value = btn.dataset.option;
        const q = section.querySelector(`.q[data-label="${CSS.escape(label)}"]`);
        if (q) q.dataset.value = value;
        section.querySelectorAll(`button[data-label="${CSS.escape(label)}"]`).forEach((b) => {
          const on = b.dataset.option === value;
          b.classList.toggle('on', on);
          b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
        updateAskReady(section, card);
      };
    });
    section.querySelectorAll('textarea[name]').forEach((t) => {
      t.oninput = () => updateAskReady(section, card);
    });
    updateAskReady(section, card);

    section.querySelectorAll('button[data-action]').forEach((btn) => {
      btn.onclick = async () => {
        const action = btn.dataset.action;
        let form = {};
        if (action === 'answers') {
          form = collectAskForm(section, card);
          if (form === null) return;
        } else if (action === 'engine') {
          if (btn.disabled) return;
          form = { engine: btn.dataset.engine };
        }
        let body;
        try {
          body = decisionBody(card, action, form);
        } catch {
          return;
        }
        // Незавершённый POST отмечаем на узле и в pendingPosts: штатно отключённая
        // кнопка («отправить ответы» при пустом обязательном поле) — не признак
        // висящего запроса, и перерисовку по ней блокировать нельзя (F14).
        // Счётчик по id переживает смену версии: новая карточка из опроса тоже
        // остаётся заблокированной, пока не ответит исходный POST (F4).
        pendingPosts.set(card.id, (pendingPosts.get(card.id) || 0) + 1);
        section.dataset.pending = '1';
        lock(true);
        const res = await post('/jobs/decide', body);
        const left = (pendingPosts.get(card.id) || 1) - 1;
        if (left <= 0) pendingPosts.delete(card.id);
        else pendingPosts.set(card.id, left);
        // F4: id с висящим POST не перерисовываем (см. render), поэтому исходный
        // узел жив и msg тоже его. Обиратное — та же версия: render мог заменить
        // узел свежим (тогда cardsById уже указывает на живой ответ).
        const nodes = [...root.querySelectorAll(`[data-card-id="${CSS.escape(card.id)}"]`)];
        const current = nodes.find((el) => el.dataset.pending === '1') || section;
        if (!pendingPosts.has(card.id)) {
          delete section.dataset.pending;
          nodes.forEach((el) => delete el.dataset.pending);
        }
        const live = cardsById.get(card.id) || card;
        const msgEl = current.querySelector('.card-msg') || msg;
        // Пока висел POST, два неудачных опроса могли включить offline lock:
        // снятие lock здесь открыло бы кнопки при висящей плашке «нет связи».
        if (!offlineLocked && !pendingPosts.has(card.id)) {
          lockSection(current, false);
          if (live.kind === 'ask') updateAskReady(current, live);
        }
        if (res.status === 0) {
          msgEl.textContent = 'не дошло — нажми ещё раз, когда будет сеть';
          return;
        }
        if (res.status === 409 && res.body && res.body.error === 'decided') {
          msgEl.textContent = `уже решено: ${res.body.action}`;
          return;
        }
        if (res.status === 409) {
          msgEl.textContent = 'карточка обновилась';
          load();
          return;
        }
        if (!res.ok) {
          msgEl.textContent = `отказ: ${res.status}`;
          return;
        }
        load();
      };
    });

    let touchStartX = null;
    section.addEventListener('touchstart', (e) => { touchStartX = e.touches[0].clientX; });
    section.addEventListener('touchend', (e) => {
      if (touchStartX == null) return;
      const dx = e.changedTouches[0].clientX - touchStartX;
      touchStartX = null;
      const action = swipeAction(dx);
      if (!action) return;
      const btn = section.querySelector(`button[data-action="${action}"]`);
      if (btn && !btn.disabled) btn.click();
    });
  }

  function collectAskForm(section, card) {
    const form = Object.create(null);
    for (const item of (card.payload.items || [])) {
      if (item.kind === 'text') {
        const t = section.querySelector(`textarea[name="${CSS.escape(item.label)}"]`);
        form[item.label] = t ? t.value : '';
      } else {
        const q = section.querySelector(`.q[data-label="${CSS.escape(item.label)}"]`);
        form[item.label] = (q && q.dataset.value) || '';
      }
      if (hasContact(form[item.label])) {
        const msg = section.querySelector('.card-msg');
        if (msg) msg.textContent = 'это ноут знает сам — убери контакты из ответа';
        return null;
      }
    }
    return form;
  }

  function updateAskReady(section, card) {
    if (card.kind !== 'ask') return;
    const btn = section.querySelector('button[data-action="answers"]');
    if (!btn) return;
    if (section.dataset.pending === '1') {
      btn.disabled = true;
      return;
    }
    let ready = true;
    for (const item of (card.payload.items || [])) {
      if (!item.required) continue;
      if (item.kind === 'text') {
        const t = section.querySelector(`textarea[name="${CSS.escape(item.label)}"]`);
        if (!t || !t.value.trim()) ready = false;
      } else {
        const q = section.querySelector(`.q[data-label="${CSS.escape(item.label)}"]`);
        if (!q || !q.dataset.value) ready = false;
      }
    }
    btn.disabled = !ready;
  }

  // Незавершённый POST переживает опрос: пока на узле висит отметка pending,
  // узел не трогаем (и ответы пользователя — в нём же). Иначе тот же id и version
  // обновляет содержимое по месту: тот же DOM-узел, перерисовка innerHTML с
  // сохранением введённых ответов ask и выбранных вариантов (F10). Статус при
  // неизменной version "1" тоже обновляется (stage из нового payload).
  function snapshotAsk(section, card) {
    const snap = Object.create(null);
    for (const item of (card.payload.items || [])) {
      if (item.kind === 'text') {
        const t = section.querySelector(`textarea[name="${CSS.escape(item.label)}"]`);
        if (t) snap[item.label] = t.value;
      } else {
        const q = section.querySelector(`.q[data-label="${CSS.escape(item.label)}"]`);
        if (q) snap[item.label] = q.dataset.value || '';
      }
    }
    return snap;
  }

  function restoreAsk(section, card, snap) {
    for (const item of (card.payload.items || [])) {
      const value = snap[item.label];
      if (!value) continue;
      if (item.kind === 'text') {
        const t = section.querySelector(`textarea[name="${CSS.escape(item.label)}"]`);
        if (t && document.activeElement !== t) t.value = value;
      } else {
        const q = section.querySelector(`.q[data-label="${CSS.escape(item.label)}"]`);
        if (q) q.dataset.value = value;
        section.querySelectorAll(`button[data-label="${CSS.escape(item.label)}"]`).forEach((b) => {
          const on = b.dataset.option === value;
          b.classList.toggle('on', on);
          b.setAttribute('aria-pressed', on ? 'true' : 'false');
        });
      }
    }
    updateAskReady(section, card);
  }

  function render(cards, laptopSeen, nowMs) {
    banner.textContent = laptopText(laptopSeen, nowMs);
    banner.hidden = !banner.textContent;
    if (!cards.length) {
      root.innerHTML = `<p id="empty">${nextBatchText(nowMs)}</p>`;
      cardsById = new Map();
      return;
    }
    const sorted = filterStatusCards(cards).sort((a, b) => (b.hot ? 1 : 0) - (a.hot ? 1 : 0) || (a.rank ?? 0) - (b.rank ?? 0));
    cardsById = new Map(sorted.map((c) => [c.id, c]));
    const existing = new Map();
    root.querySelectorAll('[data-card-id]').forEach((el) => existing.set(el.dataset.cardId, el));
    const frag = document.createDocumentFragment();
    sorted.forEach((card) => {
      const prev = existing.get(card.id);
      // F4: id с висящим POST не перерисовываем: свежая версия ждёт ответа
      // исходного запроса, иначе второе решение ушло бы раньше первого.
      if (prev && pendingPosts.has(card.id)) {
        frag.appendChild(prev);
        return;
      }
      if (prev && prev.dataset.version === String(card.version)) {
        const snap = card.kind === 'ask' ? snapshotAsk(prev, card) : null;
        if (prev.dataset.pending === '1') {
          frag.appendChild(prev);
          return;
        }
        const wrapper = document.createElement('div');
        wrapper.innerHTML = renderCard(card);
        const fresh = wrapper.firstElementChild;
        frag.appendChild(fresh);
        wireCard(fresh, card);
        if (snap) restoreAsk(fresh, card, snap);
        return;
      }
      const wrapper = document.createElement('div');
      wrapper.innerHTML = renderCard(card);
      const section = wrapper.firstElementChild;
      frag.appendChild(section);
      wireCard(section, card);
      // F5: id с висящим POST блокируем сразу, даже без предыдущего узла в DOM
      // (опрос мог вернуть пустой список между POST и новой версией).
      if (pendingPosts.has(card.id)) {
        section.dataset.pending = '1';
        lockSection(section, true);
      }
    });
    root.replaceChildren(frag);
  }

  function setOfflineLock(on) {
    root.querySelectorAll('button[data-action], button[data-label]').forEach((b) => {
      if (b.dataset.permDisabled === '1') return;
      const section = b.closest('[data-card-id]');
      // F4: карточка с висящим POST остаётся заблокированной при снятии lock.
      if (section && (section.dataset.pending === '1' || pendingPosts.has(section.dataset.cardId))) return;
      b.disabled = on;
    });
    if (!on) {
      root.querySelectorAll('[data-card-id]').forEach((section) => {
        const card = cardsById.get(section.dataset.cardId);
        if (card && card.kind === 'ask') updateAskReady(section, card);
      });
    }
  }

  let loadSeq = 0;
  async function load() {
    if (!token) return;
    const seq = ++loadSeq;
    const res = await get('/jobs/cards');
    if (seq !== loadSeq) return;   // пришёл ответ старого опроса: новый уже показан или в пути
    if (!res.ok) {
      fails += 1;
      offline.textContent = fails >= 2 ? 'нет связи с релеем' : '';
      offline.hidden = !offline.textContent;
      if (fails >= 2 && !offlineLocked) {
        offlineLocked = true;
        setOfflineLock(true);
      }
      return;
    }
    fails = 0;
    offline.hidden = true;
    offline.textContent = '';
    updateSettingsUI(res.body.mode, res.body.push_cap);
    render(res.body.cards || [], res.body.laptop_seen, res.body.now);
    if (offlineLocked) {
      offlineLocked = false;
      setOfflineLock(false);
    }
  }

  function schedule() {
    const delay = fails > 0 ? 2000 : 10000;
    setTimeout(() => { if (document.visibilityState === 'visible') load().finally(schedule); else schedule(); }, delay);
  }
  document.addEventListener('visibilitychange', () => { if (document.visibilityState === 'visible') load(); });
  if (token) { load(); schedule(); }
}
