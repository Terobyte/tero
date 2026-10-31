-- relay/migrate-06-07.sql — миграция D1 плана 09 (фаза 3): колонка status в states.
-- /runs и cron фильтруют по ней, а не парсят payload. Выполнить ОДИН раз на существующей базе
-- (на новой schema.sql уже с колонкой):
--   wrangler d1 execute tero-relay --remote --file=relay/migrate-06-07.sql
ALTER TABLE states ADD COLUMN status TEXT NOT NULL DEFAULT '';
UPDATE states SET status = COALESCE(json_extract(payload, '$.status'), '');
CREATE INDEX IF NOT EXISTS idx_states_status ON states (status, updated_at);
