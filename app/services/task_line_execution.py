from __future__ import annotations

from dataclasses import dataclass

from sqlalchemy.orm import Session

from app.models.need import CommunityNeed
from app.models.task_workflow import Task, TaskAssignment
from app.models.user import User
from app.services.task_commands import TaskAuthorizationError, TaskCommandService
from app.services.task_line_security import (
    TaskLineCommand,
    TaskLineSecurityError,
    parse_task_postback,
    verify_task_postback,
)


@dataclass(frozen=True)
class TaskLineExecutionResult:
    task: Task
    assignment: TaskAssignment
    need: CommunityNeed
    command: TaskLineCommand


class TaskLineExecutionService:
    """Authenticated LINE command adapter for the Task command service."""

    def __init__(self, db: Session):
        self.db = db

    def execute(self, raw_postback: str, line_uid: str) -> TaskLineExecutionResult:
        postback = parse_task_postback(raw_postback)
        assignment = self.db.get(TaskAssignment, postback.assignment_id)
        if not assignment or str(assignment.task_id) != postback.task_id:
            raise TaskLineSecurityError("Task assignment does not match the command")

        verify_task_postback(postback, assignee_id=str(assignment.assignee_id))

        actor = (
            self.db.query(User)
            .filter(User.line_uid == line_uid, User.is_active == True)
            .first()
        )
        if not actor or str(actor.id) != str(assignment.assignee_id):
            raise TaskAuthorizationError("Current LINE user is not the task assignee")
        if assignment.status not in {"ASSIGNED", "ACCEPTED"}:
            raise TaskAuthorizationError("Task assignment is no longer active")

        commands = TaskCommandService(self.db)
        actor_id = str(actor.id)
        task_id = postback.task_id
        version = postback.expected_version

        if postback.command == TaskLineCommand.ACCEPT:
            task = commands.acknowledge_task(task_id, actor_id, version)
        elif postback.command == TaskLineCommand.START:
            task = commands.start_task(task_id, actor_id, version)
        elif postback.command == TaskLineCommand.ARRIVE:
            task = commands.mark_arrived(task_id, actor_id, version)
        elif postback.command == TaskLineCommand.COMPLETE:
            task = commands.complete_task(task_id, actor_id, version)
        elif postback.command == TaskLineCommand.DECLINE:
            task = commands.decline_task(
                task_id,
                actor_id,
                version,
                reason="Declined from LINE",
            )
        elif postback.command == TaskLineCommand.FAIL:
            task = commands.fail_task(
                task_id,
                actor_id,
                version,
                reason="Reported failed from LINE",
            )
        else:  # pragma: no cover - Enum parsing rejects unknown commands
            raise TaskLineSecurityError("Unsupported task command")

        current_assignment = self.db.get(TaskAssignment, assignment.id)
        need = self.db.get(CommunityNeed, task.need_id)
        if not current_assignment or not need:
            raise TaskLineSecurityError("Task execution context is unavailable")
        return TaskLineExecutionResult(
            task=task,
            assignment=current_assignment,
            need=need,
            command=postback.command,
        )
