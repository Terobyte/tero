-- relay/migrate-push-device.sql — B17: владелец подписки на пуши.
-- Нужна только базе, где push_subs уже создана без device_id (до B17):
--   wrangler d1 execute tero-relay --remote --file=relay/migrate-push-device.sql
-- Подписки без владельца пушей больше не получают — телефон подпишется заново.
ALTER TABLE push_subs ADD COLUMN device_id TEXT NOT NULL DEFAULT '';
