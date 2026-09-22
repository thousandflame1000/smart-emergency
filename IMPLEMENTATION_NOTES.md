# Task Workflow V2 Implementation Notes

Updated: 2026-09-18

## Scope

This batch completes the first four Task Workflow V2 gates:

- Proposal
- Approval
- Task
- TaskAssignment
- TaskEvent
- TaskCommandService
- ProposalService and ApprovalService
- feature-flagged dispatch-to-Proposal adapter
- signed LINE volunteer execution commands
- transactional notification outbox with PostgreSQL-safe claiming
- reversible PostgreSQL migration
- workflow, authorization, concurrency, migration, and regression tests

It does not introduce Kafka/Redis or modify inventory, ontology, RAG, or the
workspace. Task Workflow V2 is disabled by default.

## Repository Audit

### A ORM and migration technology

- ORM: SQLAlchemy 2.0 Declarative models.
- Runtime schema creation: `Base.metadata.create_all()` in `app/main.py`.
- There is no active Alembic or other migration runner.
- There is no separate `schemas/` package; existing Pydantic request models are
  declared next to their routers or services.
- `migrations/001_init.sql` is explicitly marked as obsolete historical
  documentation and is not executed by the application.
- Batch A therefore supplies paired PostgreSQL up/down SQL files while keeping
  model registration compatible with the existing `create_all()` startup path.

### B CommunityNeed lifecycle

Current application states are `open`, `suggested`, `matched`, `fulfilled`, and
`cancelled`. `CommunityNeed` currently owns request state, dispatch suggestion,
matching, and task-like completion state.

### C Dispatch suggestion lifecycle

- Personal resources: `auto_dispatch()` reserves a resource and changes the
  need from `open` to `suggested`.
- An administrator confirms the suggestion, which sends LINE and changes the
  need to `matched`.
- With `TASK_WORKFLOW_V2=false`, fixed facilities retain their legacy direct
  match behavior.
- With `TASK_WORKFLOW_V2=true`, both personal-resource and fixed-facility
  suggestions create a versioned Proposal and wait for human approval.

### D LINE task and report lifecycle

The legacy LINE task payload remains available for compatibility. Workflow V2
adds signed, expiring postbacks containing Task, Assignment, command, and
expected-version data. The execution adapter verifies the current LINE user is
the active assignee before delegating every state change to TaskCommandService.

### E Transaction boundaries

Existing routers and services call `commit()` at multiple workflow steps.
LINE delivery and business-state mutation do not share a transactional outbox.
TaskCommandService keeps each Assignment, TaskEvent, and OutboxMessage in one
database transaction. LINE delivery happens only in the outbox worker after
commit, with exponential backoff, DEAD handling, a processing lease, and
PostgreSQL `FOR UPDATE SKIP LOCKED` claiming.

### F Endpoints that directly modify status

- `PUT /api/resources/needs/{need_id}?status=...` accepts arbitrary non-cancel
  status values.
- Legacy dispatch services directly assign `CommunityNeed.status`.
- LINE delivered/declined callbacks directly change `CommunityNeed.status`.

No generic Task status endpoint is added. New Task state can only be changed by
TaskCommandService.

### G Existing test coverage

The pre-change baseline is `176 passed, 1 skipped`. Existing tests cover legacy
dispatch scoring, human confirmation, LINE delivered/declined callbacks,
DispatchEvent history and workspace analysis.
They did not cover a formal Task state machine, assignment authorization, or
optimistic Task version conflicts.

### H Minimum insertion point

The new models are registered through `app/models/__init__.py`. Dispatch uses
`ProposalService` only when `TASK_WORKFLOW_V2=true`; the default legacy workflow
is unchanged. `ApprovalService` is the transaction boundary that records the
Approval and creates the Task plus its initial TaskEvent.

## Code and Requested Design Differences

1. The repository has no active migration framework. This batch adds reversible,
   idempotent PostgreSQL SQL rather than introducing Alembic as an unrelated
   infrastructure change.
2. The requested Task status list does not contain `DECLINED`, while
   TaskAssignment does. A decline therefore changes the assignment to
   `DECLINED` and the Task to `BLOCKED`; it can then be reassigned.
3. Approval and TaskEvent are protected from update/delete by SQLAlchemy event
   hooks. The PostgreSQL migration also installs append-only triggers.
4. Actor authorization is an injectable policy. The default policy allows an
   active administrator to manage tasks and only the active assignee to execute
   assignment commands. It does not replace the existing authentication system.
5. Optimistic concurrency uses `expected_version` plus an atomic conditional
   update. PostgreSQL row locks are also requested when loading the Task.

## Deferred by Explicit Scope

- legacy status synchronization
- general task-management API
- dashboard integration
- feedback records

These belong to later gates and are not partially wired in this change.

## Retired Demo Code

The fixed Hua-Dong road sandbox, typhoon-night simulation, scenario API, and
road-replanning models were removed on 2026-09-21. They are not current product
capabilities. Generic road data can still be imported into a workspace for
non-authoritative planning, but dispatch scoring does not claim live road state.
