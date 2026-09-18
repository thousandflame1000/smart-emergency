-- Task workflow v2, Batch A.
-- Additive only: no existing table or column is modified.

BEGIN;

CREATE TABLE IF NOT EXISTS proposals (
    id VARCHAR(36) PRIMARY KEY,
    need_id VARCHAR(36) NOT NULL REFERENCES community_needs(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'DRAFT'
        CHECK (status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED','SUPERSEDED','CANCELLED')),
    created_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    algorithm TEXT NOT NULL,
    algorithm_version TEXT NOT NULL,
    score DOUBLE PRECISION,
    explanation_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    candidate_resource_id VARCHAR(36) REFERENCES community_resources(id) ON DELETE SET NULL,
    candidate_assignee_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    candidate_facility_id VARCHAR(36) REFERENCES resource_points(id) ON DELETE SET NULL,
    route_reference TEXT,
    fingerprint TEXT NOT NULL,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_proposals_need_status ON proposals (need_id, status);
CREATE INDEX IF NOT EXISTS ix_proposals_fingerprint ON proposals (fingerprint);

CREATE TABLE IF NOT EXISTS approvals (
    id VARCHAR(36) PRIMARY KEY,
    proposal_id VARCHAR(36) NOT NULL REFERENCES proposals(id) ON DELETE RESTRICT,
    actor_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    decision TEXT NOT NULL CHECK (decision IN ('APPROVE','REJECT','MODIFY')),
    comment TEXT,
    proposal_version INTEGER NOT NULL,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_approvals_proposal_created ON approvals (proposal_id, created_at);

CREATE TABLE IF NOT EXISTS tasks (
    id VARCHAR(36) PRIMARY KEY,
    need_id VARCHAR(36) NOT NULL REFERENCES community_needs(id) ON DELETE RESTRICT,
    proposal_id VARCHAR(36) REFERENCES proposals(id) ON DELETE SET NULL,
    task_type TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'APPROVED'
        CHECK (status IN ('APPROVED','ASSIGNED','ACKNOWLEDGED','IN_PROGRESS','ARRIVED','COMPLETED','FAILED','CANCELLED','BLOCKED')),
    priority INTEGER NOT NULL DEFAULT 3 CHECK (priority BETWEEN 1 AND 5),
    instructions TEXT,
    destination_reference TEXT,
    version INTEGER NOT NULL DEFAULT 1 CHECK (version >= 1),
    approved_by VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    approved_at TIMESTAMP,
    created_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS ix_tasks_need_status ON tasks (need_id, status);
CREATE INDEX IF NOT EXISTS ix_tasks_proposal ON tasks (proposal_id);

CREATE TABLE IF NOT EXISTS task_assignments (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    assignee_id VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    status TEXT NOT NULL DEFAULT 'ASSIGNED'
        CHECK (status IN ('ASSIGNED','ACCEPTED','DECLINED','REVOKED')),
    assigned_by VARCHAR(36) NOT NULL REFERENCES users(id) ON DELETE RESTRICT,
    assigned_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    responded_at TIMESTAMP,
    task_version INTEGER NOT NULL CHECK (task_version >= 1)
);

CREATE INDEX IF NOT EXISTS ix_task_assignments_task_status ON task_assignments (task_id, status);
CREATE INDEX IF NOT EXISTS ix_task_assignments_assignee_status ON task_assignments (assignee_id, status);

CREATE TABLE IF NOT EXISTS task_events (
    id VARCHAR(36) PRIMARY KEY,
    task_id VARCHAR(36) NOT NULL REFERENCES tasks(id) ON DELETE CASCADE,
    task_version INTEGER NOT NULL CHECK (task_version >= 1),
    event_type TEXT NOT NULL,
    actor_id VARCHAR(36) REFERENCES users(id) ON DELETE SET NULL,
    occurred_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    received_at TIMESTAMP NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE INDEX IF NOT EXISTS ix_task_events_task_occurred ON task_events (task_id, occurred_at);

CREATE OR REPLACE FUNCTION reject_task_workflow_event_mutation()
RETURNS trigger AS $$
BEGIN
    RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
END;
$$ LANGUAGE plpgsql;

DROP TRIGGER IF EXISTS approvals_append_only ON approvals;
CREATE TRIGGER approvals_append_only
BEFORE UPDATE OR DELETE ON approvals
FOR EACH ROW EXECUTE FUNCTION reject_task_workflow_event_mutation();

DROP TRIGGER IF EXISTS task_events_append_only ON task_events;
CREATE TRIGGER task_events_append_only
BEFORE UPDATE OR DELETE ON task_events
FOR EACH ROW EXECUTE FUNCTION reject_task_workflow_event_mutation();

COMMIT;
