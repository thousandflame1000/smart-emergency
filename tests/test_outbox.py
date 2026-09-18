from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient
from sqlalchemy.dialects import postgresql

from app.database import SessionLocal
from app.models.need import CommunityNeed
from app.models.outbox import OutboxMessage
from app.models.task_workflow import Task, TaskAssignment
from app.models.user import User
from app.routers import tasks as tasks_router
from app.services.outbox import OutboxWorker, notification_delivery_for_task
from app.services.task_commands import TaskCommandService


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


class _Dispatcher:
    def __init__(self, outcomes=None):
        self.outcomes = list(outcomes or [None])
        self.calls = []

    def deliver(self, db, message):
        self.calls.append(str(message.id))
        outcome = self.outcomes.pop(0) if self.outcomes else None
        if isinstance(outcome, Exception):
            raise outcome


def _approved_task(db):
    admin = User(name="Admin", roles=["admin"], is_active=True)
    volunteer = User(
        name="Volunteer",
        roles=["volunteer"],
        line_uid="U-outbox-volunteer",
        is_active=True,
    )
    resident = User(name="Resident", roles=["elderly"], is_active=True)
    db.add_all([admin, volunteer, resident])
    db.flush()
    need = CommunityNeed(
        requester_id=resident.id,
        need_type="water",
        description="Deliver water",
        address="Test address",
        urgency=4,
        status="open",
    )
    task = Task(
        need=need,
        task_type="FULFILL_COMMUNITY_NEED",
        status="APPROVED",
        priority=4,
        version=1,
        approved_by=admin.id,
    )
    db.add_all([need, task])
    db.commit()
    return admin, volunteer, task


def _assign(db):
    admin, volunteer, task = _approved_task(db)
    task = TaskCommandService(db).assign_task(
        str(task.id),
        str(volunteer.id),
        str(admin.id),
        1,
    )
    assignment = db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).one()
    outbox = db.query(OutboxMessage).filter(OutboxMessage.aggregate_id == str(task.id)).one()
    return admin, volunteer, task, assignment, outbox


def test_assignment_and_outbox_are_created_together(db):
    _, volunteer, task, assignment, outbox = _assign(db)

    assert str(assignment.assignee_id) == str(volunteer.id)
    assert outbox.status == "PENDING"
    assert outbox.channel == "LINE"
    assert outbox.destination == volunteer.line_uid
    assert outbox.message_type == "TASK_ASSIGNMENT"
    assert outbox.payload["task_id"] == str(task.id)
    assert outbox.payload["assignment_id"] == str(assignment.id)


def test_line_success_marks_outbox_sent(db):
    _, _, task, _, outbox = _assign(db)
    dispatcher = _Dispatcher()
    result = OutboxWorker(
        db,
        worker_id="worker-success",
        dispatcher=dispatcher,
    ).process_next(now=_utcnow() + timedelta(seconds=1))

    assert result.status == "SENT"
    assert result.sent_at is not None
    assert result.attempt_count == 1
    assert dispatcher.calls == [str(outbox.id)]
    assert db.get(Task, task.id).status == "ASSIGNED"


def test_line_failure_schedules_retry_with_backoff(db):
    _, _, task, assignment, outbox = _assign(db)
    now = _utcnow() + timedelta(seconds=1)
    result = OutboxWorker(
        db,
        worker_id="worker-failure",
        dispatcher=_Dispatcher([RuntimeError("LINE unavailable")]),
        base_delay_seconds=30,
    ).process_next(now=now)

    assert result.status == "FAILED"
    assert result.attempt_count == 1
    assert result.available_at == now + timedelta(seconds=30)
    assert "LINE unavailable" in result.last_error
    assert db.get(Task, task.id).status == "ASSIGNED"
    assert db.get(TaskAssignment, assignment.id) is not None
    assert db.get(OutboxMessage, outbox.id).status == "FAILED"


def test_retry_can_succeed_after_failure(db):
    _, _, _, _, _ = _assign(db)
    dispatcher = _Dispatcher([RuntimeError("temporary"), None])
    worker = OutboxWorker(
        db,
        worker_id="worker-retry",
        dispatcher=dispatcher,
        base_delay_seconds=5,
    )
    failed = worker.process_next(now=_utcnow() + timedelta(seconds=1))
    retry_at = failed.available_at + timedelta(seconds=1)
    assert failed.status == "FAILED"
    sent = worker.process_next(now=retry_at)

    assert sent.status == "SENT"
    assert sent.attempt_count == 2
    assert sent.sent_at is not None
    assert len(dispatcher.calls) == 2


def test_retry_limit_moves_message_to_dead_and_keeps_error(db):
    _, _, _, _, _ = _assign(db)
    dispatcher = _Dispatcher([RuntimeError("first"), RuntimeError("final")])
    worker = OutboxWorker(
        db,
        worker_id="worker-dead",
        dispatcher=dispatcher,
        max_attempts=2,
        base_delay_seconds=1,
    )
    failed = worker.process_next(now=_utcnow() + timedelta(seconds=1))
    dead = worker.process_next(now=failed.available_at + timedelta(seconds=1))

    assert dead.status == "DEAD"
    assert dead.attempt_count == 2
    assert dead.sent_at is None
    assert "final" in dead.last_error


def test_two_workers_cannot_claim_or_send_the_same_message(db):
    _, _, _, _, outbox = _assign(db)
    dispatcher = _Dispatcher()
    now = _utcnow() + timedelta(seconds=1)
    worker_a = OutboxWorker(db, worker_id="worker-a", dispatcher=dispatcher)
    claimed = worker_a.claim_next(now=now)

    db_b = SessionLocal()
    try:
        worker_b = OutboxWorker(db_b, worker_id="worker-b", dispatcher=dispatcher)
        assert worker_b.claim_next(now=now) is None
    finally:
        db_b.close()

    sent = worker_a.deliver_claimed(str(claimed.id), now=now)
    assert sent.status == "SENT"
    assert dispatcher.calls == [str(outbox.id)]


def test_postgresql_claim_statement_uses_skip_locked():
    now = _utcnow()
    statement = OutboxWorker.postgres_claim_statement(
        now,
        now - timedelta(minutes=2),
    )
    sql = str(statement.compile(dialect=postgresql.dialect())).upper()

    assert "FOR UPDATE SKIP LOCKED" in sql


def test_assignment_and_outbox_roll_back_as_one_transaction(db, monkeypatch):
    admin, volunteer, task = _approved_task(db)
    task_id = str(task.id)

    def fail_commit():
        raise RuntimeError("database commit failed")

    monkeypatch.setattr(db, "commit", fail_commit)
    with pytest.raises(RuntimeError, match="database commit failed"):
        TaskCommandService(db).assign_task(
            task_id,
            str(volunteer.id),
            str(admin.id),
            1,
        )

    verify = SessionLocal()
    try:
        assert verify.query(TaskAssignment).filter(TaskAssignment.task_id == task_id).count() == 0
        assert verify.query(OutboxMessage).filter(OutboxMessage.aggregate_id == task_id).count() == 0
        assert verify.get(Task, task_id).status == "APPROVED"
    finally:
        verify.close()


def test_task_notification_delivery_status_is_queryable(db):
    _, _, task, _, outbox = _assign(db)
    data = notification_delivery_for_task(db, str(task.id))

    assert data["task_status"] == "ASSIGNED"
    assert data["notifications"][0]["id"] == str(outbox.id)
    assert data["notifications"][0]["status"] == "PENDING"

    app = FastAPI()
    app.include_router(tasks_router.router, prefix="/api/tasks")
    response = TestClient(app).get(f"/api/tasks/{task.id}/notifications")
    assert response.status_code == 200
    assert response.json()["notifications"][0]["status"] == "PENDING"
