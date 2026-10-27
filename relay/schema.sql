-- relay/schema.sql — схема D1 ретранслятора tero (фаза 08-4).
-- Состояние прогонов, токены устройств/чтения, ответы человека.

CREATE TABLE IF NOT EXISTS states (
  run_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  payload TEXT NOT NULL,
  updated_at TEXT NOT NULL,
  PRIMARY KEY (run_id, machine_id)
);

CREATE TABLE IF NOT EXISTS devices (
  token_hash TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  label TEXT NOT NULL DEFAULT '',
  revoked INTEGER NOT NULL DEFAULT 0,
  created_at TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS answers (
  uuid TEXT PRIMARY KEY,
  run_id TEXT NOT NULL DEFAULT '',
  machine_id TEXT NOT NULL DEFAULT '',
  question_id TEXT NOT NULL,
  action TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  who TEXT NOT NULL,
  received_at TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_answers_question ON answers (question_id);
CREATE INDEX IF NOT EXISTS idx_answers_run ON answers (run_id, machine_id);
