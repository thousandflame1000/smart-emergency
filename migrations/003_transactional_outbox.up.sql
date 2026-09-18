-- Reliable notification outbox. Additive only.

BEGIN;

CREATE TABLE IF NOT EXISTS outbox_messages (
    id VARCHAR(36) PRIMARY KEY,
    aggregate_type TEXT NOT NULL,
    aggregate_id TEXT NOT NULL,
    channel TEXT NOT NULL,
    destination TEXT NOT NULL,
    message_type TEXT NOT NULL,
    payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'PENDING'
        CHECK (status IN ('PENDING','PROCESSING','SENT','FAILED','DEAD')),
    attempt_count INTEGER NOT NULL DEFAULT 0 CHECK (attempt_count >= 0),
    available_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    sent_at TIMESTAMP,
    last_error TEXT,
    locked_at TIMESTAMP,
    locked_by TEXT
);

CREATE INDEX IF NOT EXISTS ix_outbox_ready
    ON outbox_messages (status, available_at, created_at);
CREATE INDEX IF NOT EXISTS ix_outbox_aggregate
    ON outbox_messages (aggregate_type, aggregate_id, created_at);
CREATE INDEX IF NOT EXISTS ix_outbox_lock
    ON outbox_messages (status, locked_at);

COMMIT;
