BEGIN;

DROP TABLE IF EXISTS task_routes;
DROP TABLE IF EXISTS road_observations;
DROP FUNCTION IF EXISTS reject_road_observation_mutation();
ALTER TABLE tasks DROP COLUMN IF EXISTS route_reference;

COMMIT;
