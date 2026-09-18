from __future__ import annotations

import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Protocol

from sqlalchemy import and_, or_, select, update
from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.need import CommunityNeed
from app.models.outbox import OutboxMessage
from app.models.task_workflow import Task, TaskAssignment
from app.models.user import User
from app.services.task_line_messages import send_task_assignment_message


READY_STATUSES = {"PENDING", "FAILED"}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class OutboxDeliveryError(Exception):
    pass


class OutboxDispatcher(Protocol):
    def deliver(self, db: Session, message: OutboxMessage) -> None: ...


class LineOutboxDispatcher:
    def deliver(self, db: Session, message: OutboxMessage) -> None:
        if message.channel != "LINE" or message.message_type != "TASK_ASSIGNMENT":
            raise OutboxDeliveryError(
                f"Unsupported outbox message {message.channel}/{message.message_type}"
            )
        task_id = str((message.payload or {}).get("task_id") or "")
        assignment_id = str((message.payload or {}).get("assignment_id") or "")
        task = db.get(Task, task_id)
        assignment = db.get(TaskAssignment, assignment_id)
        need = db.get(CommunityNeed, task.need_id) if task else None
        if not task or not assignment or not need:
            raise OutboxDeliveryError("Task assignment delivery context is missing")
        if not message.destination:
            raise OutboxDeliveryError("Task assignee has no LINE destination")
        send_task_assignment_message(message.destination, task, assignment, need)


class OutboxService:
    def __init__(self, db: Session):
        self.db = db

    def enqueue_task_assignment(
        self,
        task: Task,
        assignment: TaskAssignment,
        assignee: User,
    ) -> OutboxMessage:
        has_destination = bool(assignee.line_uid)
        message = OutboxMessage(
            aggregate_type="Task",
            aggregate_id=str(task.id),
            channel="LINE",
            destination=assignee.line_uid or "",
            message_type="TASK_ASSIGNMENT",
            payload={
                "task_id": str(task.id),
                "assignment_id": str(assignment.id),
                "task_version": task.version + 1,
            },
            status="PENDING" if has_destination else "DEAD",
            attempt_count=0,
            available_at=_utcnow(),
            last_error=None if has_destination else "Task assignee has no LINE destination",
        )
        self.db.add(message)
        self.db.flush()
        return message


class OutboxWorker:
    def __init__(
        self,
        db: Session,
        *,
        worker_id: str,
        dispatcher: OutboxDispatcher | None = None,
        max_attempts: int | None = None,
        base_delay_seconds: int | None = None,
        max_delay_seconds: int | None = None,
        lease_seconds: int | None = None,
    ):
        self.db = db
        self.worker_id = worker_id
        self.dispatcher = dispatcher or LineOutboxDispatcher()
        self.max_attempts = max_attempts or settings.OUTBOX_MAX_ATTEMPTS
        self.base_delay_seconds = (
            base_delay_seconds
            if base_delay_seconds is not None
            else settings.OUTBOX_BASE_DELAY_SECONDS
        )
        self.max_delay_seconds = (
            max_delay_seconds
            if max_delay_seconds is not None
            else settings.OUTBOX_MAX_DELAY_SECONDS
        )
        self.lease_seconds = lease_seconds or settings.OUTBOX_LEASE_SECONDS

    @staticmethod
    def _eligible(now: datetime, stale_before: datetime):
        return or_(
            and_(
                OutboxMessage.status.in_(READY_STATUSES),
                OutboxMessage.available_at <= now,
            ),
            and_(
                OutboxMessage.status == "PROCESSING",
                or_(
                    OutboxMessage.locked_at.is_(None),
                    OutboxMessage.locked_at <= stale_before,
                ),
            ),
        )

    @classmethod
    def postgres_claim_statement(cls, now: datetime, stale_before: datetime):
        return (
            select(OutboxMessage)
            .where(cls._eligible(now, stale_before))
            .order_by(OutboxMessage.available_at, OutboxMessage.created_at, OutboxMessage.id)
            .limit(1)
            .with_for_update(skip_locked=True)
        )

    def claim_next(self, *, now: datetime | None = None) -> OutboxMessage | None:
        now = now or _utcnow()
        stale_before = now - timedelta(seconds=self.lease_seconds)
        dialect = self.db.get_bind().dialect.name

        if dialect == "postgresql":
            message = self.db.execute(
                self.postgres_claim_statement(now, stale_before)
            ).scalars().first()
            if not message:
                self.db.rollback()
                return None
            message.status = "PROCESSING"
            message.attempt_count += 1
            message.locked_at = now
            message.locked_by = self.worker_id
            self.db.commit()
            return self.db.get(OutboxMessage, message.id)

        candidates = self.db.execute(
            select(OutboxMessage.id)
            .where(self._eligible(now, stale_before))
            .order_by(OutboxMessage.available_at, OutboxMessage.created_at, OutboxMessage.id)
            .limit(10)
        ).scalars().all()
        for message_id in candidates:
            result = self.db.execute(
                update(OutboxMessage)
                .where(
                    OutboxMessage.id == message_id,
                    self._eligible(now, stale_before),
                )
                .values(
                    status="PROCESSING",
                    attempt_count=OutboxMessage.attempt_count + 1,
                    locked_at=now,
                    locked_by=self.worker_id,
                )
                .execution_options(synchronize_session=False)
            )
            if result.rowcount == 1:
                self.db.commit()
                return self.db.get(OutboxMessage, message_id)
            self.db.rollback()
        return None

    def deliver_claimed(
        self,
        message_id: str,
        *,
        now: datetime | None = None,
    ) -> OutboxMessage:
        now = now or _utcnow()
        message = self.db.get(OutboxMessage, message_id)
        if (
            not message
            or message.status != "PROCESSING"
            or message.locked_by != self.worker_id
        ):
            raise OutboxDeliveryError("Outbox message is not claimed by this worker")

        try:
            self.dispatcher.deliver(self.db, message)
        except Exception as exc:
            self.db.rollback()
            message = self.db.get(OutboxMessage, message_id)
            message.last_error = str(exc)[:2000]
            message.locked_at = None
            message.locked_by = None
            if message.attempt_count >= self.max_attempts:
                message.status = "DEAD"
            else:
                delay = min(
                    self.base_delay_seconds * (2 ** (message.attempt_count - 1)),
                    self.max_delay_seconds,
                )
                message.status = "FAILED"
                message.available_at = now + timedelta(seconds=delay)
            self.db.commit()
            return self.db.get(OutboxMessage, message_id)

        message.status = "SENT"
        message.sent_at = now
        message.last_error = None
        message.locked_at = None
        message.locked_by = None
        self.db.commit()
        return self.db.get(OutboxMessage, message_id)

    def process_next(self, *, now: datetime | None = None) -> OutboxMessage | None:
        message = self.claim_next(now=now)
        if not message:
            return None
        return self.deliver_claimed(str(message.id), now=now)


def notification_delivery_for_task(db: Session, task_id: str) -> dict:
    task = db.get(Task, task_id)
    if not task:
        raise LookupError("Task not found")
    rows = (
        db.query(OutboxMessage)
        .filter(
            OutboxMessage.aggregate_type == "Task",
            OutboxMessage.aggregate_id == str(task.id),
        )
        .order_by(OutboxMessage.created_at, OutboxMessage.id)
        .all()
    )
    return {
        "task_id": str(task.id),
        "task_status": task.status,
        "notifications": [
            {
                "id": str(row.id),
                "channel": row.channel,
                "destination": row.destination,
                "message_type": row.message_type,
                "status": row.status,
                "attempt_count": row.attempt_count,
                "available_at": row.available_at.isoformat() if row.available_at else None,
                "created_at": row.created_at.isoformat() if row.created_at else None,
                "sent_at": row.sent_at.isoformat() if row.sent_at else None,
                "last_error": row.last_error,
            }
            for row in rows
        ],
    }


def process_outbox_batch(limit: int = 25) -> int:
    worker_id = f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"
    processed = 0
    db = SessionLocal()
    try:
        worker = OutboxWorker(db, worker_id=worker_id)
        for _ in range(limit):
            if worker.process_next() is None:
                break
            processed += 1
        return processed
    finally:
        db.close()
