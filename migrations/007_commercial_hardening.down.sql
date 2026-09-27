BEGIN;

DROP TABLE IF EXISTS privacy_consents;
DROP TABLE IF EXISTS webhook_events;
DROP INDEX IF EXISTS ux_outbox_dedupe_key;
ALTER TABLE outbox_messages DROP COLUMN IF EXISTS dedupe_key;
ALTER TABLE daily_checkins DROP CONSTRAINT IF EXISTS uq_daily_checkins_elderly_date;
DROP INDEX IF EXISTS uq_daily_checkins_elderly_date;
ALTER TABLE daily_checkins DROP COLUMN IF EXISTS prompt_sent_at;

COMMIT;
