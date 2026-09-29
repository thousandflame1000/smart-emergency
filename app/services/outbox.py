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
from app.models.alert import Alert
from app.models.checkin import DailyCheckin
from app.models.need import CommunityNeed
from app.models.outbox import OutboxMessage
from app.models.resource import CommunityResource
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
        if message.channel != "LINE":
            raise OutboxDeliveryError(
                f"Unsupported outbox message {message.channel}/{message.message_type}"
            )
        if message.message_type == "CHECKIN":
            from app.services.line_notify import send_checkin_message
            checkin_id = str((message.payload or {}).get("checkin_id") or "")
            checkin = db.get(DailyCheckin, checkin_id)
            if not checkin:
                raise OutboxDeliveryError("Check-in delivery context is missing")
            send_checkin_message(message.destination, checkin_id)
            checkin.prompt_sent_at = _utcnow()
            return
        if message.message_type == "ALERT":
            from app.services.line_notify import send_alert_message
            payload = message.payload or {}
            alert = db.get(Alert, str(payload.get("alert_id") or ""))
            if not alert:
                raise OutboxDeliveryError("Alert delivery context is missing")
            send_alert_message(
                line_uid=message.destination,
                elderly_name=str(payload.get("elderly_name") or ""),
                alert_type=str(payload.get("alert_type") or ""),
                checkin_id=str(payload.get("checkin_id") or ""),
                elderly=alert.elderly,
            )
            contact_id = str(payload.get("contact_id") or "")
            notified = [str(value) for value in (alert.notified_users or [])]
            if contact_id and contact_id not in notified:
                alert.notified_users = notified + [contact_id]
            return
        if message.message_type == "TEXT":
            from app.services.line_notify import send_text
            content = str((message.payload or {}).get("text") or "")
            if not content:
                raise OutboxDeliveryError("Text notification content is missing")
            send_text(message.destination, content)
            return
        if message.message_type == "LEGACY_TASK_ASSIGNMENT":
            from app.services.line_notify import send_task_message
            payload = message.payload or {}
            need = db.get(CommunityNeed, str(payload.get("need_id") or ""))
            resource = db.get(CommunityResource, str(payload.get("resource_id") or ""))
            if not need or not resource:
                raise OutboxDeliveryError("Legacy task delivery context is missing")
            send_task_message(
                line_uid=message.destination,
                need_description=need.description or need.need_type,
                address=need.address or "地址未填",
                resource_name=resource.name,
                need_id=str(need.id),
                distance_km=payload.get("distance_km"),
                dest_lat=need.lat,
                dest_lng=need.lng,
            )
            return
        if message.message_type != "TASK_ASSIGNMENT":
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
        dedupe_key = f"task-assignment:{assignment.id}"
        existing = self.db.query(OutboxMessage).filter(
            OutboxMessage.dedupe_key == dedupe_key
        ).first()
        if existing:
            return existing
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
            dedupe_key=dedupe_key,
            status="PENDING" if has_destination else "DEAD",
            attempt_count=0,
            available_at=_utcnow(),
            last_error=None if has_destination else "Task assignee has no LINE destination",
        )
        self.db.add(message)
        self.db.flush()
        return message

    def _enqueue_line(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        destination: str,
        message_type: str,
        payload: dict,
        dedupe_key: str,
        immediate: bool = True,
    ) -> OutboxMessage:
        existing = (
            self.db.query(OutboxMessage)
            .filter(OutboxMessage.dedupe_key == dedupe_key)
            .first()
        )
        if existing:
            return existing
        now = _utcnow()
        has_destination = bool(destination)
        message = OutboxMessage(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            channel="LINE",
            destination=destination or "",
            message_type=message_type,
            payload=payload,
            dedupe_key=dedupe_key,
            status=("PROCESSING" if immediate else "PENDING") if has_destination else "DEAD",
            attempt_count=1 if immediate and has_destination else 0,
            available_at=now,
            locked_at=now if immediate and has_destination else None,
            locked_by="inline" if immediate and has_destination else None,
            last_error=None if has_destination else "LINE destination is missing",
        )
        self.db.add(message)
        self.db.flush()
        return message

    def enqueue_checkin(self, checkin: DailyCheckin, user: User) -> OutboxMessage:
        return self._enqueue_line(
            aggregate_type="DailyCheckin",
            aggregate_id=str(checkin.id),
            destination=user.line_uid or "",
            message_type="CHECKIN",
            payload={"checkin_id": str(checkin.id)},
            dedupe_key=f"checkin:{checkin.id}:{user.id}",
        )

    def enqueue_alert(self, alert: Alert, contact: User, *, elderly_name: str) -> OutboxMessage:
        return self._enqueue_line(
            aggregate_type="Alert",
            aggregate_id=str(alert.id),
            destination=contact.line_uid or "",
            message_type="ALERT",
            payload={
                "alert_id": str(alert.id),
                "checkin_id": str(alert.checkin_id or ""),
                "contact_id": str(contact.id),
                "elderly_name": elderly_name,
                "alert_type": alert.alert_type,
            },
            dedupe_key=f"alert:{alert.id}:{contact.id}",
        )

    def enqueue_text(
        self,
        *,
        aggregate_type: str,
        aggregate_id: str,
        destination: str,
        content: str,
        dedupe_key: str,
    ) -> OutboxMessage:
        return self._enqueue_line(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            destination=destination,
            message_type="TEXT",
            payload={"text": content},
            dedupe_key=dedupe_key,
        )

    def enqueue_legacy_task(
        self,
        need: CommunityNeed,
        resource: CommunityResource,
        owner: User | None,
        *,
        dispatch_id: str,
        distance_km: float | None,
    ) -> OutboxMessage:
        return self._enqueue_line(
            aggregate_type="CommunityNeed",
            aggregate_id=str(need.id),
            destination=owner.line_uid if owner and owner.line_uid else "",
            message_type="LEGACY_TASK_ASSIGNMENT",
            payload={
                "need_id": str(need.id),
                "resource_id": str(resource.id),
                "distance_km": distance_km,
            },
            dedupe_key=f"legacy-task:{dispatch_id}",
        )


def finish_inline_delivery(db: Session, message_id: str, error: Exception | None = None) -> bool:
    message = db.get(OutboxMessage, message_id)
    if not message:
        return False
    now = _utcnow()
    message.locked_at = None
    message.locked_by = None
    if error is None:
        message.status = "SENT"
        message.sent_at = now
        message.last_error = None
        db.commit()
        return True
    message.last_error = str(error)[:2000]
    if message.attempt_count >= settings.OUTBOX_MAX_ATTEMPTS:
        message.status = "DEAD"
    else:
        message.status = "FAILED"
        delay = min(
            settings.OUTBOX_BASE_DELAY_SECONDS * (2 ** max(0, message.attempt_count - 1)),
            settings.OUTBOX_MAX_DELAY_SECONDS,
        )
        message.available_at = now + timedelta(seconds=delay)
    db.commit()
    return False


def send_text_reliably(
    *,
    aggregate_type: str,
    aggregate_id: str,
    destination: str | None,
    content: str,
    dedupe_key: str,
) -> bool:
    """Persist a text notification before attempting the immediate LINE push."""
    db = SessionLocal()
    try:
        existing = db.query(OutboxMessage).filter(
            OutboxMessage.dedupe_key == dedupe_key
        ).first()
        if existing:
            return existing.status == "SENT"
        message = OutboxService(db).enqueue_text(
            aggregate_type=aggregate_type,
            aggregate_id=aggregate_id,
            destination=destination or "",
            content=content,
            dedupe_key=dedupe_key,
        )
        message_id = str(message.id)
        db.commit()
        if message.status != "PROCESSING":
            return False
        try:
            from app.services.line_notify import send_text
            send_text(message.destination, content)
        except Exception as exc:
            return finish_inline_delivery(db, message_id, exc)
        return finish_inline_delivery(db, message_id)
    finally:
        db.close()


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
