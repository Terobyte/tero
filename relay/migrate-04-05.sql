-- relay/migrate-04-05.sql — миграция D1 с фазы 08-4 на 08-5.
-- Выполнить ОДИН раз на существующей базе перед relay/schema.sql:
--   wrangler d1 execute tero-relay --remote --file=relay/migrate-04-05.sql
--   wrangler d1 execute tero-relay --remote --file=relay/schema.sql
-- Новая база в миграции не нуждается: schema.sql создаёт devices уже с device_id.
-- Повторный прогон: D1 останавливается на первом упавшем стейтменте, поэтому
-- прогонять один и тот же файл дважды нельзя — ALTER упадёт с duplicate column.
-- Это нормально: миграция одноразовая, schema.sql после неё идемпотентен.

-- Добавить device_id (на базе 08-4 колонки нет; на новой базе файл не нужен).
ALTER TABLE devices ADD COLUMN device_id TEXT NOT NULL DEFAULT '';
CREATE INDEX IF NOT EXISTS idx_devices_device_id ON devices (device_id);
