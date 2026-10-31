-- relay/schema.sql — схема D1 ретранслятора tero (фаза 08-4).
-- Состояние прогонов, токены устройств/чтения, ответы человека.

CREATE TABLE IF NOT EXISTS states (
  run_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  status TEXT NOT NULL DEFAULT '',
  -- план 09, фаза 4: отметки сторожа (relay/migrate-07-08.sql); POST /state их не трогает
  owner_alive INTEGER,
  host_alive INTEGER,
  owner_checked_at INTEGER,
  resume_w TEXT,
  PRIMARY KEY (run_id, machine_id)
);

CREATE INDEX IF NOT EXISTS idx_states_status ON states (status, updated_at);

CREATE TABLE IF NOT EXISTS devices (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  device_id TEXT NOT NULL DEFAULT '',
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_devices_device_id ON devices (device_id);

CREATE TABLE IF NOT EXISTS answers (
  uuid TEXT PRIMARY KEY,
  run_id TEXT NOT NULL DEFAULT '',
  machine_id TEXT NOT NULL DEFAULT '',
  question_id TEXT NOT NULL,
  action TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  who TEXT NOT NULL,
  received_at TEXT NOT NULL,
  -- план 09: answer | message | wake; миграция базы 05 — relay/migrate-05-06.sql
  kind TEXT NOT NULL DEFAULT 'answer'
);

CREATE INDEX IF NOT EXISTS idx_answers_question ON answers (question_id);
CREATE INDEX IF NOT EXISTS idx_answers_run ON answers (run_id, machine_id);

-- Привязка телефона (фаза 08-5): одноразовые коды и счётчик неверных вводов.
-- Миграция с базы фазы 08-4: старой таблице devices не хватает device_id,
-- а `CREATE TABLE IF NOT EXISTS` уже существующую таблицу не меняет —
-- поэтому отдельный скрипт relay/migrate-04-05.sql добавляет колонку
-- (выполнить его перед этим файлом на существующей базе).
CREATE TABLE IF NOT EXISTS pair_codes (
  code TEXT PRIMARY KEY,
  label TEXT NOT NULL DEFAULT '',
  created_at INTEGER NOT NULL,
  used INTEGER NOT NULL DEFAULT 0,
  claim TEXT NOT NULL DEFAULT ''  -- кто забрал код: токены вставляются только под этот claim
);

CREATE TABLE IF NOT EXISTS pair_attempts (
  ip TEXT NOT NULL,
  at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_pair_attempts_ip ON pair_attempts (ip, at);

-- Web push (фаза 08-7): подписки устройств и журнал ответов push-сервиса.
-- 404/410 → push_subs.dead = 1; push_ok = false, пока нет ни одной живой.
CREATE TABLE IF NOT EXISTS push_subs (
  endpoint TEXT PRIMARY KEY,
  p256dh TEXT NOT NULL,
  auth TEXT NOT NULL,
  dead INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL DEFAULT '',
  -- B17: владелец подписки; пуш уходит только неотозванному устройству.
  device_id TEXT NOT NULL DEFAULT ''
);

CREATE TABLE IF NOT EXISTS push_log (
  id INTEGER PRIMARY KEY AUTOINCREMENT,
  run_id TEXT NOT NULL DEFAULT '',
  reason TEXT NOT NULL DEFAULT '',
  code INTEGER NOT NULL,
  at INTEGER NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_push_log_run_reason ON push_log (run_id, reason, at);

-- Атомарный слот кулдауна (фаза 08-7, находка F3): одна строка на
-- (run_id, reason); конкурентный notify вставляет вторым — и пропускает пуш.
CREATE TABLE IF NOT EXISTS push_claims (
  run_id TEXT NOT NULL,
  reason TEXT NOT NULL,
  at INTEGER NOT NULL,
  PRIMARY KEY (run_id, reason)
);

-- План 09, фаза 4: пульс сторожа и команды с телефона (только `resume <run_id>`).
CREATE TABLE IF NOT EXISTS machines (
  machine_id TEXT PRIMARY KEY,
  last_seen INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS commands (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',  -- pending | taken | done | failed | expired | lost
  created_at INTEGER NOT NULL,
  taken_at INTEGER,
  code INTEGER,
  result_text TEXT NOT NULL DEFAULT ''
);

CREATE INDEX IF NOT EXISTS idx_commands_machine ON commands (machine_id, status);
CREATE INDEX IF NOT EXISTS idx_commands_run ON commands (run_id, machine_id, status);
-- Одна ожидающая/взятая команда на прогон: гонку двух POST режет сам индекс (409).
CREATE UNIQUE INDEX IF NOT EXISTS idx_commands_active ON commands (run_id, machine_id)
  WHERE status IN ('pending', 'taken');
