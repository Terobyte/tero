-- relay/migrate-07-08.sql — миграция D1 плана 09 (фаза 4): отметки сторожа, машины, команды.
-- Выполнить ОДИН раз на существующей базе (на новой schema.sql уже всё есть):
--   wrangler d1 execute tero-relay --remote --file=relay/migrate-07-08.sql
ALTER TABLE states ADD COLUMN owner_alive INTEGER;
ALTER TABLE states ADD COLUMN host_alive INTEGER;
ALTER TABLE states ADD COLUMN owner_checked_at INTEGER;
ALTER TABLE states ADD COLUMN resume_w TEXT;
CREATE TABLE IF NOT EXISTS machines (
  machine_id TEXT PRIMARY KEY,
  last_seen INTEGER NOT NULL
);
CREATE TABLE IF NOT EXISTS commands (
  id TEXT PRIMARY KEY,
  run_id TEXT NOT NULL,
  machine_id TEXT NOT NULL,
  text TEXT NOT NULL DEFAULT '',
  status TEXT NOT NULL DEFAULT 'pending',
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
