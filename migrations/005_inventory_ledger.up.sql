-- Quantity-aware stock accounting. Additive and backward-compatible.

BEGIN;

ALTER TABLE community_resources ADD COLUMN IF NOT EXISTS quantity_amount INTEGER;
ALTER TABLE community_resources ADD COLUMN IF NOT EXISTS quantity_unit TEXT;
ALTER TABLE community_resources ADD COLUMN IF NOT EXISTS reserved_amount INTEGER NOT NULL DEFAULT 0;
ALTER TABLE community_resources ADD COLUMN IF NOT EXISTS inventory_version INTEGER NOT NULL DEFAULT 1;

ALTER TABLE community_needs ADD COLUMN IF NOT EXISTS quantity_amount INTEGER;
ALTER TABLE community_needs ADD COLUMN IF NOT EXISTS quantity_unit TEXT;
ALTER TABLE community_needs ADD COLUMN IF NOT EXISTS reserved_quantity_amount INTEGER NOT NULL DEFAULT 0;
ALTER TABLE community_needs ADD COLUMN IF NOT EXISTS fulfilled_quantity_amount INTEGER NOT NULL DEFAULT 0;

CREATE TABLE IF NOT EXISTS inventory_events (
    id UUID PRIMARY KEY,
    resource_id UUID NOT NULL REFERENCES community_resources(id) ON DELETE RESTRICT,
    need_id UUID REFERENCES community_needs(id) ON DELETE SET NULL,
    event_type TEXT NOT NULL CHECK (
        event_type IN ('RESERVE','RELEASE','CONSUME','ADJUST','RESERVE_LEGACY','RELEASE_LEGACY','CONSUME_LEGACY')
    ),
    quantity INTEGER CHECK (quantity IS NULL OR quantity > 0),
    unit TEXT,
    on_hand_before INTEGER,
    on_hand_after INTEGER,
    reserved_before INTEGER,
    reserved_after INTEGER,
    resource_version INTEGER NOT NULL CHECK (resource_version >= 1),
    actor_id UUID REFERENCES users(id) ON DELETE SET NULL,
    actor_label TEXT,
    details_json TEXT,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_inventory_events_resource_created
    ON inventory_events (resource_id, created_at);
CREATE INDEX IF NOT EXISTS ix_inventory_events_need_created
    ON inventory_events (need_id, created_at);

CREATE OR REPLACE FUNCTION reject_inventory_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS inventory_events_append_only ON inventory_events;
CREATE TRIGGER inventory_events_append_only
BEFORE UPDATE OR DELETE ON inventory_events
FOR EACH ROW EXECUTE FUNCTION reject_inventory_event_mutation();

COMMIT;
