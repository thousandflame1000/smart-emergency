-- Roll back Task workflow v2, Batch A.
-- This intentionally removes only the five tables added by the matching up migration.

BEGIN;

DROP TABLE IF EXISTS task_events;
DROP TABLE IF EXISTS task_assignments;
DROP TABLE IF EXISTS tasks;
DROP TABLE IF EXISTS approvals;
DROP TABLE IF EXISTS proposals;
DROP FUNCTION IF EXISTS reject_task_workflow_event_mutation();

COMMIT;
