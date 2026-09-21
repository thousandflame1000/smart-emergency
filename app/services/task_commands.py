from __future__ import annotations

from datetime import UTC, datetime
from enum import Enum
from typing import Any, Protocol

from sqlalchemy import update
from sqlalchemy.orm import Session

from app.models.task_workflow import Task, TaskAssignment, TaskEvent
from app.models.user import User
from app.services.outbox import OutboxService


class TaskStatus(str, Enum):
    APPROVED = "APPROVED"
    ASSIGNED = "ASSIGNED"
    ACKNOWLEDGED = "ACKNOWLEDGED"
    IN_PROGRESS = "IN_PROGRESS"
    ARRIVED = "ARRIVED"
    COMPLETED = "COMPLETED"
    FAILED = "FAILED"
    CANCELLED = "CANCELLED"
    BLOCKED = "BLOCKED"


class AssignmentStatus(str, Enum):
    ASSIGNED = "ASSIGNED"
    ACCEPTED = "ACCEPTED"
    DECLINED = "DECLINED"
    REVOKED = "REVOKED"


ACTIVE_TASK_STATUSES = {
    TaskStatus.APPROVED.value,
    TaskStatus.ASSIGNED.value,
    TaskStatus.ACKNOWLEDGED.value,
    TaskStatus.IN_PROGRESS.value,
    TaskStatus.ARRIVED.value,
    TaskStatus.BLOCKED.value,
}
ACTIVE_ASSIGNMENT_STATUSES = {
    AssignmentStatus.ASSIGNED.value,
    AssignmentStatus.ACCEPTED.value,
}

ALLOWED_TRANSITIONS = {
    TaskStatus.APPROVED.value: {
        TaskStatus.ASSIGNED.value,
        TaskStatus.FAILED.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.ASSIGNED.value: {
        TaskStatus.ACKNOWLEDGED.value,
        TaskStatus.FAILED.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.ACKNOWLEDGED.value: {
        TaskStatus.IN_PROGRESS.value,
        TaskStatus.FAILED.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.IN_PROGRESS.value: {
        TaskStatus.ARRIVED.value,
        TaskStatus.FAILED.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.ARRIVED.value: {
        TaskStatus.COMPLETED.value,
        TaskStatus.FAILED.value,
        TaskStatus.BLOCKED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.BLOCKED.value: {
        TaskStatus.ASSIGNED.value,
        TaskStatus.FAILED.value,
        TaskStatus.CANCELLED.value,
    },
    TaskStatus.COMPLETED.value: set(),
    TaskStatus.FAILED.value: set(),
    TaskStatus.CANCELLED.value: set(),
}


def _utcnow() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


class TaskWorkflowError(Exception):
    status_code = 400
    code = "task_workflow_error"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TaskNotFoundError(TaskWorkflowError):
    status_code = 404
    code = "task_not_found"


class TaskVersionConflictError(TaskWorkflowError):
    status_code = 409
    code = "task_version_conflict"


class InvalidTaskTransitionError(TaskWorkflowError):
    status_code = 409
    code = "invalid_task_transition"


class TaskAuthorizationError(TaskWorkflowError):
    status_code = 403
    code = "task_forbidden"


class TaskAssigneeNotFoundError(TaskWorkflowError):
    status_code = 404
    code = "task_assignee_not_found"


class TaskAuthorizationPolicy(Protocol):
    def can_manage(self, actor: User) -> bool: ...

    def can_execute(self, actor: User, assignment: TaskAssignment | None) -> bool: ...


class DefaultTaskAuthorizationPolicy:
    """Small Phase 1 hook; it does not replace the application's auth system."""

    def can_manage(self, actor: User) -> bool:
        return bool(actor.is_active and actor.has_role("admin"))

    def can_execute(self, actor: User, assignment: TaskAssignment | None) -> bool:
        return bool(
            actor.is_active
            and assignment is not None
            and str(assignment.assignee_id) == str(actor.id)
            and assignment.status in ACTIVE_ASSIGNMENT_STATUSES
        )


class TaskCommandService:
    """The sole write path for Task state transitions in workflow v2."""

    def __init__(
        self,
        db: Session,
        authorization: TaskAuthorizationPolicy | None = None,
    ):
        self.db = db
        self.authorization = authorization or DefaultTaskAuthorizationPolicy()

    def assign_task(
        self,
        task_id: str,
        assignee_id: str,
        actor_id: str,
        expected_version: int,
    ) -> Task:
        try:
            task, actor = self._prepare(task_id, actor_id)
            self._require_manager(actor)
            self._validate_transition(task.status, TaskStatus.ASSIGNED.value)
            self._require_expected_version(task, expected_version)

            assignee = self.db.query(User).filter(User.id == assignee_id, User.is_active == True).first()
            if not assignee:
                raise TaskAssigneeNotFoundError("Active assignee not found")

            is_reassignment = task.status == TaskStatus.BLOCKED.value
            previous = self._active_assignment(task.id)
            if previous:
                previous.status = AssignmentStatus.REVOKED.value
                previous.responded_at = previous.responded_at or _utcnow()

            new_version = self._transition(
                task, expected_version, TaskStatus.ASSIGNED.value
            )
            assignment = TaskAssignment(
                task_id=task.id,
                assignee_id=assignee.id,
                status=AssignmentStatus.ASSIGNED.value,
                assigned_by=actor.id,
                task_version=new_version,
            )
            self.db.add(assignment)
            self.db.flush()
            OutboxService(self.db).enqueue_task_assignment(task, assignment, assignee)
            self._event(
                task,
                new_version,
                "REASSIGNED" if is_reassignment or previous else "ASSIGNED",
                actor.id,
                {
                    "assignment_id": str(assignment.id),
                    "assignee_id": str(assignee.id),
                    "previous_assignment_id": str(previous.id) if previous else None,
                },
            )
            return self._commit_and_get(task.id)
        except Exception:
            self.db.rollback()
            raise

    def acknowledge_task(
        self, task_id: str, actor_id: str, expected_version: int
    ) -> Task:
        return self._assignee_transition(
            task_id,
            actor_id,
            expected_version,
            TaskStatus.ASSIGNED.value,
            TaskStatus.ACKNOWLEDGED.value,
            "ACKNOWLEDGED",
            accept_assignment=True,
        )

    def start_task(self, task_id: str, actor_id: str, expected_version: int) -> Task:
        return self._assignee_transition(
            task_id,
            actor_id,
            expected_version,
            TaskStatus.ACKNOWLEDGED.value,
            TaskStatus.IN_PROGRESS.value,
            "DEPARTED",
        )

    def mark_arrived(self, task_id: str, actor_id: str, expected_version: int) -> Task:
        return self._assignee_transition(
            task_id,
            actor_id,
            expected_version,
            TaskStatus.IN_PROGRESS.value,
            TaskStatus.ARRIVED.value,
            "ARRIVED",
        )

    def complete_task(
        self,
        task_id: str,
        actor_id: str,
        expected_version: int,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        return self._assignee_transition(
            task_id,
            actor_id,
            expected_version,
            TaskStatus.ARRIVED.value,
            TaskStatus.COMPLETED.value,
            "COMPLETED",
            metadata=metadata,
        )

    def decline_task(
        self,
        task_id: str,
        actor_id: str,
        expected_version: int,
        reason: str | None = None,
    ) -> Task:
        try:
            task, actor = self._prepare(task_id, actor_id)
            assignment = self._active_assignment(task.id)
            self._require_assignee(actor, assignment)
            if task.status != TaskStatus.ASSIGNED.value:
                raise InvalidTaskTransitionError(
                    f"Task in {task.status} cannot be declined"
                )
            if assignment.status != AssignmentStatus.ASSIGNED.value:
                raise InvalidTaskTransitionError("Only a pending assignment can be declined")
            self._require_expected_version(task, expected_version)

            assignment.status = AssignmentStatus.DECLINED.value
            assignment.responded_at = _utcnow()
            new_version = self._transition(
                task, expected_version, TaskStatus.BLOCKED.value
            )
            self._event(
                task,
                new_version,
                "ASSIGNMENT_DECLINED",
                actor.id,
                {"assignment_id": str(assignment.id), "reason": reason},
            )
            return self._commit_and_get(task.id)
        except Exception:
            self.db.rollback()
            raise

    def fail_task(
        self,
        task_id: str,
        actor_id: str,
        expected_version: int,
        reason: str,
    ) -> Task:
        return self._operator_transition(
            task_id,
            actor_id,
            expected_version,
            TaskStatus.FAILED.value,
            "FAILED",
            {"reason": reason},
        )

    def block_task(
        self,
        task_id: str,
        actor_id: str,
        expected_version: int,
        reason: str,
    ) -> Task:
        return self._operator_transition(
            task_id,
            actor_id,
            expected_version,
            TaskStatus.BLOCKED.value,
            "BLOCKED",
            {"reason": reason},
        )

    def cancel_task(
        self,
        task_id: str,
        actor_id: str,
        expected_version: int,
        reason: str | None = None,
    ) -> Task:
        try:
            task, actor = self._prepare(task_id, actor_id)
            self._require_manager(actor)
            self._validate_transition(task.status, TaskStatus.CANCELLED.value)
            self._require_expected_version(task, expected_version)
            assignment = self._active_assignment(task.id)
            if assignment:
                assignment.status = AssignmentStatus.REVOKED.value
                assignment.responded_at = assignment.responded_at or _utcnow()
            new_version = self._transition(
                task, expected_version, TaskStatus.CANCELLED.value
            )
            self._event(
                task,
                new_version,
                "CANCELLED",
                actor.id,
                {
                    "reason": reason,
                    "assignment_id": str(assignment.id) if assignment else None,
                },
            )
            return self._commit_and_get(task.id)
        except Exception:
            self.db.rollback()
            raise

    def _assignee_transition(
        self,
        task_id: str,
        actor_id: str,
        expected_version: int,
        required_status: str,
        target_status: str,
        event_type: str,
        *,
        accept_assignment: bool = False,
        metadata: dict[str, Any] | None = None,
    ) -> Task:
        try:
            task, actor = self._prepare(task_id, actor_id)
            assignment = self._active_assignment(task.id)
            self._require_assignee(actor, assignment)
            if task.status != required_status:
                raise InvalidTaskTransitionError(
                    f"Task in {task.status} cannot transition to {target_status}"
                )
            if accept_assignment:
                if assignment.status != AssignmentStatus.ASSIGNED.value:
                    raise InvalidTaskTransitionError("Assignment is not awaiting acceptance")
            elif assignment.status != AssignmentStatus.ACCEPTED.value:
                raise InvalidTaskTransitionError("Assignment has not been accepted")
            self._require_expected_version(task, expected_version)
            if accept_assignment:
                assignment.status = AssignmentStatus.ACCEPTED.value
                assignment.responded_at = _utcnow()

            new_version = self._transition(task, expected_version, target_status)
            details = {"assignment_id": str(assignment.id)}
            details.update(metadata or {})
            self._event(task, new_version, event_type, actor.id, details)
            return self._commit_and_get(task.id)
        except Exception:
            self.db.rollback()
            raise

    def _operator_transition(
        self,
        task_id: str,
        actor_id: str,
        expected_version: int,
        target_status: str,
        event_type: str,
        metadata: dict[str, Any],
    ) -> Task:
        try:
            task, actor = self._prepare(task_id, actor_id)
            assignment = self._active_assignment(task.id)
            if not (
                self.authorization.can_manage(actor)
                or self.authorization.can_execute(actor, assignment)
            ):
                raise TaskAuthorizationError("Actor cannot operate this task")
            self._validate_transition(task.status, target_status)
            self._require_expected_version(task, expected_version)
            new_version = self._transition(task, expected_version, target_status)
            self._event(task, new_version, event_type, actor.id, metadata)
            return self._commit_and_get(task.id)
        except Exception:
            self.db.rollback()
            raise

    def _prepare(self, task_id: str, actor_id: str) -> tuple[Task, User]:
        task = (
            self.db.query(Task)
            .filter(Task.id == task_id)
            .with_for_update()
            .first()
        )
        if not task:
            raise TaskNotFoundError("Task not found")
        actor = self.db.query(User).filter(User.id == actor_id).first()
        if not actor or not actor.is_active:
            raise TaskAuthorizationError("Active actor not found")
        return task, actor

    @staticmethod
    def _require_expected_version(task: Task, expected_version: int) -> None:
        if task.version != expected_version:
            raise TaskVersionConflictError(
                f"Expected task version {expected_version}, current version is {task.version}"
            )

    def _transition(self, task: Task, expected_version: int, target_status: str) -> int:
        self._validate_transition(task.status, target_status)
        new_version = expected_version + 1
        result = self.db.execute(
            update(Task)
            .where(Task.id == task.id, Task.version == expected_version)
            .values(
                status=target_status,
                version=new_version,
                updated_at=_utcnow(),
            )
            .execution_options(synchronize_session=False)
        )
        if result.rowcount != 1:
            raise TaskVersionConflictError("Task was changed by another command")
        return new_version

    def _event(
        self,
        task: Task,
        task_version: int,
        event_type: str,
        actor_id: str | None,
        metadata: dict[str, Any] | None = None,
    ) -> None:
        self.db.add(
            TaskEvent(
                task_id=task.id,
                task_version=task_version,
                event_type=event_type,
                actor_id=actor_id,
                metadata_json=metadata or {},
            )
        )

    def _active_assignment(self, task_id: str) -> TaskAssignment | None:
        return (
            self.db.query(TaskAssignment)
            .filter(
                TaskAssignment.task_id == task_id,
                TaskAssignment.status.in_(ACTIVE_ASSIGNMENT_STATUSES),
            )
            .order_by(TaskAssignment.assigned_at.desc(), TaskAssignment.id.desc())
            .with_for_update()
            .first()
        )

    def _require_manager(self, actor: User) -> None:
        if not self.authorization.can_manage(actor):
            raise TaskAuthorizationError("Administrator role required")

    def _require_assignee(
        self, actor: User, assignment: TaskAssignment | None
    ) -> None:
        if not self.authorization.can_execute(actor, assignment):
            raise TaskAuthorizationError("Actor is not the active task assignee")

    @staticmethod
    def _validate_transition(current_status: str, target_status: str) -> None:
        if target_status not in ALLOWED_TRANSITIONS.get(current_status, set()):
            raise InvalidTaskTransitionError(
                f"Illegal task transition: {current_status} -> {target_status}"
            )

    def _commit_and_get(self, task_id: str) -> Task:
        self.db.commit()
        return self.db.query(Task).filter(Task.id == task_id).one()
