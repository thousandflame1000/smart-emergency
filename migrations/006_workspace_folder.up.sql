-- Add a purely organizational folder label to saved workspaces, independent of zone_id
-- (zone_id is geographic and constrains automated dispatch matching; folder is not).
ALTER TABLE topology_workspaces ADD COLUMN folder TEXT NOT NULL DEFAULT '';
