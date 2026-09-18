-- Minimal road observation and Task replanning records. Additive only.

BEGIN;

ALTER TABLE tasks ADD COLUMN IF NOT EXISTS route_reference VARCHAR(36);

CREATE TABLE IF NOT EXISTS road_observations (
    id VARCHAR(36) PRIMARY KEY,
    road_segment_id TEXT NOT NULL,
    state TEXT NOT NULL CHECK (state IN ('OPEN','SLOW','BLOCKED')),
    source TEXT NOT NULL,
    observed_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    details JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_road_observations_segment_observed
    ON road_observations (road_segment_id, observed_at, created_at);

CREATE TABLE IF NOT EXISTS task_routes (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    proposal_id VARCHAR(36) REFERENCES proposals(id) ON DELETE SET NULL,
    start_node TEXT NOT NULL,
    end_node TEXT NOT NULL,
    node_path JSONB NOT NULL DEFAULT '[]'::jsonb,
    segment_ids JSONB NOT NULL DEFAULT '[]'::jsonb,
    distance_km DOUBLE PRECISION,
    status TEXT NOT NULL DEFAULT 'ACTIVE'
        CHECK (status IN ('ACTIVE','PROPOSED','BLOCKED','SUPERSEDED','UNREACHABLE')),
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    superseded_at TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_task_routes_task_status
    ON task_routes (task_id, status);
CREATE INDEX IF NOT EXISTS ix_task_routes_proposal
    ON task_routes (proposal_id);

CREATE OR REPLACE FUNCTION reject_road_observation_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS road_observations_append_only ON road_observations;
CREATE TRIGGER road_observations_append_only
BEFORE UPDATE OR DELETE ON road_observations
FOR EACH ROW EXECUTE FUNCTION reject_road_observation_mutation();

COMMIT;
