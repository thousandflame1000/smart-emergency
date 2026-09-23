BEGIN;

DROP TRIGGER IF EXISTS inventory_events_append_only ON inventory_events;
DROP FUNCTION IF EXISTS reject_inventory_event_mutation();
DROP TABLE IF EXISTS inventory_events;

ALTER TABLE community_needs DROP COLUMN IF EXISTS fulfilled_quantity_amount;
ALTER TABLE community_needs DROP COLUMN IF EXISTS reserved_quantity_amount;
ALTER TABLE community_needs DROP COLUMN IF EXISTS quantity_unit;
ALTER TABLE community_needs DROP COLUMN IF EXISTS quantity_amount;

ALTER TABLE community_resources DROP COLUMN IF EXISTS inventory_version;
ALTER TABLE community_resources DROP COLUMN IF EXISTS reserved_amount;
ALTER TABLE community_resources DROP COLUMN IF EXISTS quantity_unit;
ALTER TABLE community_resources DROP COLUMN IF EXISTS quantity_amount;

COMMIT;
