BEGIN;

ALTER TABLE community_resources DROP CONSTRAINT IF EXISTS fk_community_resources_zone;
ALTER TABLE community_needs DROP CONSTRAINT IF EXISTS fk_community_needs_zone;
ALTER TABLE topology_workspaces DROP CONSTRAINT IF EXISTS fk_topology_workspaces_zone;

DROP INDEX IF EXISTS ix_community_resources_zone;
DROP INDEX IF EXISTS ix_community_needs_zone;

ALTER TABLE community_resources DROP COLUMN IF EXISTS zone_id;
ALTER TABLE community_needs DROP COLUMN IF EXISTS zone_id;
ALTER TABLE topology_workspaces DROP COLUMN IF EXISTS zone_id;

DROP TABLE IF EXISTS zones;

COMMIT;
