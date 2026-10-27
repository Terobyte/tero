// relay/src/filter.js — вторая линия фильтра: те же правила, что src/relay/redact.py.
// Секреты в любом месте строки; блоки кода и diff --git не принимаются;
// находка ≤ 500 символов; относительные пути целы.

const MASK = '[redacted]';
const SK_RE = /sk-[A-Za-z0-9_-]+/g;
const AKIA_RE = /AKIA[0-9A-Z]{16}/g;
// Как _PREFIXED / _ASSIGN в redact.py (B13).
const PREFIXED_RE = /(?:gh[pousr]_[A-Za-z0-9]{20,}|github_pat_[A-Za-z0-9_]{20,}|xox[abprs]-[A-Za-z0-9-]{10,}|AIza[0-9A-Za-z_-]{30,}|[sr]k_(?:live|test)_[A-Za-z0-9]{10,}|eyJ[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,}\.[A-Za-z0-9_-]{8,})/g;
const ASSIGN_RE = /\b([A-Z0-9_]*(?:API_?KEY|SECRET|TOKEN|PASSWORD|PASSWD)[A-Z0-9_]*\s*[=:]\s*)("[^"\n]*"|'[^'\n]*'|[^\s,;]+)/gi;
const BEGIN_RE = /-----BEGIN ([A-Z0-9 ]+)-----/g;
// Как _B64 в redact.py: без / в токене (иначе длинные пути маскируются), без / в границах
// (токен между слешами ловится).
const B64_RE = /(?<![A-Za-z0-9+=])[A-Za-z0-9+]{40,}={0,2}(?![A-Za-z0-9+=])/g;
const HEX_RE = /(?<![0-9a-fA-F])[0-9a-fA-F]{32,}(?![0-9a-fA-F])/g;
const FENCED_RE = /```[\s\S]*?(```|$)/g;
// F9: стройный, combined (--cc) и обычный unified дифф — все гасятся целиком.
const DIFF_START_RE = /^diff --(?:git |cc )/;
// Стройный дифф: метаданные git (new/old/deleted file mode, index,
// similarity, rename/copy from/to) и содержимое — всё гасится целиком.
const DIFF_LINE_RE = /^(index |--- |\+\+\+ |@@@? |[ +\-\\]|new |old |deleted |similarity |dissimilarity |rename |copy |Binary files )/;

// Маски секретных файлов из src/loop/git.py SECRET_PATTERNS (fnmatch):
// путь к секретному файлу сокращается до basename.
const SECRET_GLOBS = [
  '.env',
  '.env.*',
  '*.pem',
  '*.p12',
  'id_rsa',
  'id_ed25519',
  'credentials.json',
  '*secret*',
  '*credentials*',
];

function globToRegExp(glob) {
  return new RegExp(`^${glob.split('*').map((part) => part.replace(/[.+?^${}()|[\]\\]/g, '\\$&')).join('.*')}$`);
}

const SECRET_RES = SECRET_GLOBS.map(globToRegExp);

function isSecretPath(path) {
  const raw = String(path);
  const parts = raw.split(/[/\\]/);
  const candidates = [raw, parts[parts.length - 1], ...parts];
  return SECRET_RES.some((re) => candidates.some((cand) => re.test(cand)));
}

function isAbsolutePath(path) {
  const raw = String(path).trim();
  // F19: UNC (\\server\share\...) и posix //server/share — тоже абсолютные.
  // rooted Windows (\\repo\\a.py) — тоже абсолютный.
  return raw.startsWith('/') || raw.startsWith('\\') || /^[A-Za-z]:[\\/]/.test(raw) || /^(\\\\|\/\/)/.test(raw);
}

export function basenameSecretPath(path) {
  const raw = String(path ?? '');
  // F14: из пути тоже удаляются код и дифф; абсолютный путь обезвреживается
  // до basename, секретный с любым разделителем — до basename.
  const clean = stripCodeAndDiffs(raw);
  const parts = clean.split(/[/\\]/);
  const base = parts[parts.length - 1];
  if (isSecretPath(clean) || isAbsolutePath(clean)) return redactSecrets(base);
  return redactSecrets(clean);
}

export const FINDING_TEXT_LIMIT = 500;

// Только список полей из спеки (раздел 3); лишнее отбрасывается.
const STATE_FIELDS = new Set([
  'run_id',
  'machine_id',
  'status',
  'phase',
  'phase_index',
  'round',
  'role',
  'model',
  'question',
  'actions',
  'models',
  'decisions',
  'debts',
  'limits',
  'findings',
  'phone_text_answers',
  'updated_at',
]);

function maskPem(text) {
  let out = '';
  let pos = 0;
  const re = new RegExp(BEGIN_RE.source, 'g');
  let match;
  while ((match = re.exec(text)) !== null) {
    if (match.index < pos) continue;
    const endMarker = `-----END ${match[1]}-----`;
    const end = text.indexOf(endMarker, match.index + match[0].length);
    out += text.slice(pos, match.index) + MASK;
    if (end < 0) {
      const eol = text.indexOf('\n', match.index + match[0].length);
      pos = eol < 0 ? text.length : eol;
    } else {
      pos = end + endMarker.length;
    }
  }
  return out + text.slice(pos);
}

export function redactSecrets(text) {
  if (text == null) return text;
  const out = String(text).replace(SK_RE, MASK).replace(AKIA_RE, MASK).replace(PREFIXED_RE, MASK)
    .replace(ASSIGN_RE, (_m, name) => name + MASK);
  // B64 раньше HEX: иначе HEX съедает A-F-префикс base64-токена.
  return maskPem(out).replace(B64_RE, MASK).replace(HEX_RE, MASK);
}

export function stripCodeAndDiffs(text) {
  if (text == null) return text;
  const lines = String(text).replace(FENCED_RE, '').split('\n');
  const kept = [];
  let inDiff = false;
  let inBinary = false;
  for (const line of lines) {
    if (DIFF_START_RE.test(line)) {
      inDiff = true;
      inBinary = false;
      continue;
    }
    if (inDiff) {
      // GIT binary patch: base85-строки до пустой строки — тоже дифф.
      if (line.startsWith('GIT binary patch')) inBinary = true;
      if (inBinary) {
        if (line === '') inBinary = false;
        continue;
      }
      if (DIFF_LINE_RE.test(line)) continue;
      inDiff = false;
    }
    kept.push(line);
  }
  return kept.join('\n');
}

export function sanitizeFinding(finding) {
  if (!finding || typeof finding !== 'object' || Array.isArray(finding)) return null;
  return {
    // F15: лимит 500 — только текст находки (контракт фазы), заголовок/путь/тест целы.
    title: questionSafeText(finding.title ?? ''),
    path: basenameSecretPath(finding.path ?? ''),
    test: questionSafeText(finding.test ?? ''),
    text: cleanText(finding.text ?? ''),
  };
}

function deepRedact(value) {
  // F10: код и дифф чистятся во всех передаваемых текстовых значениях,
  // не только в finding.text: иначе код в decisions/question/findings.title
  // утекает в ретранслятор.
  // F13: секрет в ключе тоже маскируется.
  if (typeof value === 'string') return redactSecrets(stripCodeAndDiffs(value));
  if (Array.isArray(value)) return value.map(deepRedact);
  if (value && typeof value === 'object') {
    const out = {};
    for (const [key, val] of Object.entries(value)) {
      out[redactSecrets(stripCodeAndDiffs(key))] = deepRedact(val);
    }
    return out;
  }
  return value;
}

function sanitizeQuestion(question) {
  // не объект (строка, массив) — тоже недоверенный: фильтр, а не как есть
  if (!question || typeof question !== 'object' || Array.isArray(question)) return deepRedact(question);
  const out = {};
  if ('id' in question) out.id = questionSafeText(String(question.id ?? ''));
  // F15: вопрос сохраняется целиком — лимит 500 из контракта относится
  // только к тексту находки.
  if ('text' in question) out.text = questionSafeText(String(question.text ?? ''));
  if ('actions' in question && Array.isArray(question.actions)) {
    out.actions = question.actions.map((action) => questionSafeText(String(action)));
  }
  return out;
}

function questionSafeText(value) {
  return redactSecrets(stripCodeAndDiffs(value));
}

function cleanText(value) {
  return redactSecrets(stripCodeAndDiffs(value)).slice(0, FINDING_TEXT_LIMIT);
}

export function filterState(state) {
  if (!state || typeof state !== 'object' || Array.isArray(state)) return {};
  const out = {};
  for (const key of STATE_FIELDS) {
    if (!(key in state)) continue;
    const value = state[key];
    if (key === 'question') out[key] = sanitizeQuestion(value);
    else if (key === 'findings' && Array.isArray(value)) {
      out[key] = value.map(sanitizeFinding).filter((finding) => finding !== null);
    } else {
      out[key] = deepRedact(value);
    }
  }
  return out;
}
