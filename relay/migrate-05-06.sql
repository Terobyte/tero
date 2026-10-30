-- relay/migrate-05-06.sql — миграция D1 плана 09: kind в таблице ответов.
-- Выполнить ОДИН раз на существующей базе (на новой schema.sql уже с kind):
--   wrangler d1 execute tero-relay --remote --file=relay/migrate-05-06.sql
ALTER TABLE answers ADD COLUMN kind TEXT NOT NULL DEFAULT 'answer';
