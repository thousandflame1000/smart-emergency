-- Zones partition resources, needs and workspaces. Additive only.
-- 'general' is the fallback every existing row is backfilled into.

BEGIN;

CREATE TABLE IF NOT EXISTS zones (
    id TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    center_lat DOUBLE PRECISION,
    center_lng DOUBLE PRECISION,
    radius_km DOUBLE PRECISION,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

INSERT INTO zones (id, name)
VALUES ('general', '未分區（預設）')
ON CONFLICT (id) DO NOTHING;

ALTER TABLE community_resources ADD COLUMN IF NOT EXISTS zone_id TEXT NOT NULL DEFAULT 'general';
ALTER TABLE community_needs ADD COLUMN IF NOT EXISTS zone_id TEXT NOT NULL DEFAULT 'general';
ALTER TABLE topology_workspaces ADD COLUMN IF NOT EXISTS zone_id TEXT NOT NULL DEFAULT 'general';

ALTER TABLE community_resources
    ADD CONSTRAINT fk_community_resources_zone FOREIGN KEY (zone_id) REFERENCES zones(id);
ALTER TABLE community_needs
    ADD CONSTRAINT fk_community_needs_zone FOREIGN KEY (zone_id) REFERENCES zones(id);
ALTER TABLE topology_workspaces
    ADD CONSTRAINT fk_topology_workspaces_zone FOREIGN KEY (zone_id) REFERENCES zones(id);

CREATE INDEX IF NOT EXISTS ix_community_resources_zone ON community_resources (zone_id);
CREATE INDEX IF NOT EXISTS ix_community_needs_zone ON community_needs (zone_id);

COMMIT;
