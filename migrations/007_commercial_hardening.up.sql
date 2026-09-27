BEGIN;

ALTER TABLE outbox_messages ADD COLUMN IF NOT EXISTS dedupe_key TEXT;
CREATE UNIQUE INDEX IF NOT EXISTS ux_outbox_dedupe_key
    ON outbox_messages (dedupe_key);

CREATE UNIQUE INDEX IF NOT EXISTS uq_daily_checkins_elderly_date
    ON daily_checkins (elderly_id, date);
ALTER TABLE daily_checkins ADD COLUMN IF NOT EXISTS prompt_sent_at TIMESTAMP;

CREATE TABLE IF NOT EXISTS webhook_events (
    id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    payload TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'PROCESSING'
        CHECK (status IN ('PROCESSING','PROCESSED','FAILED','DEAD')),
    attempt_count INTEGER NOT NULL DEFAULT 1 CHECK (attempt_count >= 0),
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    processed_at TIMESTAMP,
    locked_at TIMESTAMP,
    locked_by TEXT,
    last_error TEXT
);

CREATE INDEX IF NOT EXISTS ix_webhook_events_ready
    ON webhook_events (status, available_at, locked_at);

CREATE TABLE IF NOT EXISTS privacy_consents (
    id VARCHAR(36) PRIMARY KEY,
    user_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE CASCADE,
    notice_version TEXT NOT NULL,
    source TEXT NOT NULL,
    granted_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CONSTRAINT uq_privacy_consent_user_version UNIQUE (user_id, notice_version)
);

COMMIT;
