-- relay/migrate-jobs.sql — план EMPLOYED 4: карточки вакансий, решения с телефона, настройки.
-- Идемпотентна; те же таблицы лежат в конце schema.sql (её читает тестовый харнесс).
CREATE TABLE IF NOT EXISTS jobs_meta (
  key TEXT PRIMARY KEY,
  value TEXT NOT NULL
);

INSERT OR IGNORE INTO jobs_meta (key, value) VALUES ('mode', 'push');
INSERT OR IGNORE INTO jobs_meta (key, value) VALUES ('push_cap', '4');
INSERT OR IGNORE INTO jobs_meta (key, value) VALUES ('laptop_seen', '0');

CREATE TABLE IF NOT EXISTS jobs_cards (
  id TEXT PRIMARY KEY,
  kind TEXT NOT NULL,
  version TEXT NOT NULL,
  hot INTEGER NOT NULL DEFAULT 0,
  rank REAL NOT NULL DEFAULT 0,
  payload TEXT NOT NULL,
  updated_at INTEGER NOT NULL
);

CREATE TABLE IF NOT EXISTS jobs_decisions (
  id TEXT PRIMARY KEY,
  card_id TEXT NOT NULL,
  version TEXT NOT NULL,
  kind TEXT NOT NULL,  -- копия вида карточки: от него зависят индексы ниже
  action TEXT NOT NULL,
  data TEXT NOT NULL DEFAULT '{}',
  at INTEGER NOT NULL,
  acked INTEGER NOT NULL DEFAULT 0
);

-- Одно решение на (карточка, версия); выбор движка у `job` не решение, а upsert.
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_decisions_once ON jobs_decisions (card_id, version)
  WHERE NOT (kind = 'job' AND action = 'engine');
CREATE UNIQUE INDEX IF NOT EXISTS idx_jobs_decisions_engine ON jobs_decisions (card_id)
  WHERE kind = 'job' AND action = 'engine';
CREATE INDEX IF NOT EXISTS idx_jobs_decisions_at ON jobs_decisions (at);
