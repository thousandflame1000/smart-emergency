from __future__ import annotations

import base64
import hashlib
import hmac
import time
from dataclasses import dataclass
from enum import Enum
from urllib.parse import parse_qs, urlencode

from app.config import settings


class TaskLineCommand(str, Enum):
    ACCEPT = "ACCEPT"
    START = "START"
    ARRIVE = "ARRIVE"
    COMPLETE = "COMPLETE"
    DECLINE = "DECLINE"
    FAIL = "FAIL"


class TaskLineSecurityError(Exception):
    status_code = 403
    code = "task_line_forbidden"

    def __init__(self, message: str):
        super().__init__(message)
        self.message = message


class TaskLinePayloadError(TaskLineSecurityError):
    status_code = 400
    code = "task_line_invalid_payload"


@dataclass(frozen=True)
class TaskLinePostback:
    command: TaskLineCommand
    task_id: str
    assignment_id: str
    expected_version: int
    expires_at: int
    token: str


def _secret() -> bytes:
    value = settings.TASK_COMMAND_SECRET or settings.LINE_CHANNEL_SECRET
    if not value:
        raise RuntimeError("TASK_COMMAND_SECRET or LINE_CHANNEL_SECRET is required")
    return value.encode("utf-8")


def _material(
    *,
    command: TaskLineCommand,
    task_id: str,
    assignment_id: str,
    expected_version: int,
    assignee_id: str,
    expires_at: int,
) -> bytes:
    return "|".join(
        [
            command.value,
            task_id,
            assignment_id,
            str(expected_version),
            assignee_id,
            str(expires_at),
        ]
    ).encode("utf-8")


def sign_task_postback(
    *,
    command: TaskLineCommand,
    task_id: str,
    assignment_id: str,
    expected_version: int,
    assignee_id: str,
    expires_at: int,
) -> str:
    digest = hmac.new(
        _secret(),
        _material(
            command=command,
            task_id=task_id,
            assignment_id=assignment_id,
            expected_version=expected_version,
            assignee_id=assignee_id,
            expires_at=expires_at,
        ),
        hashlib.sha256,
    ).digest()
    return base64.urlsafe_b64encode(digest).decode("ascii").rstrip("=")


def build_task_postback_data(
    *,
    command: TaskLineCommand,
    task_id: str,
    assignment_id: str,
    expected_version: int,
    assignee_id: str,
    expires_at: int | None = None,
) -> str:
    expires_at = expires_at or int(time.time()) + 24 * 60 * 60
    token = sign_task_postback(
        command=command,
        task_id=task_id,
        assignment_id=assignment_id,
        expected_version=expected_version,
        assignee_id=assignee_id,
        expires_at=expires_at,
    )
    data = urlencode(
        {
            "action": "task_v2",
            "command": command.value,
            "task_id": task_id,
            "assignment_id": assignment_id,
            "expected_version": expected_version,
            "expires_at": expires_at,
            "token": token,
        }
    )
    if len(data.encode("utf-8")) > 300:
        raise ValueError("LINE postback data exceeds the 300-byte limit")
    return data


def parse_task_postback(raw: str) -> TaskLinePostback:
    try:
        values = parse_qs(raw, strict_parsing=True)
        if values.get("action") != ["task_v2"]:
            raise ValueError("not a task_v2 postback")
        command = TaskLineCommand(values["command"][0])
        task_id = values["task_id"][0]
        assignment_id = values["assignment_id"][0]
        expected_version = int(values["expected_version"][0])
        expires_at = int(values["expires_at"][0])
        token = values["token"][0]
    except (KeyError, IndexError, TypeError, ValueError) as exc:
        raise TaskLinePayloadError("Invalid task command payload") from exc

    if expected_version < 1 or not task_id or not assignment_id or not token:
        raise TaskLinePayloadError("Invalid task command fields")
    return TaskLinePostback(
        command=command,
        task_id=task_id,
        assignment_id=assignment_id,
        expected_version=expected_version,
        expires_at=expires_at,
        token=token,
    )


def verify_task_postback(
    postback: TaskLinePostback,
    *,
    assignee_id: str,
    now: int | None = None,
) -> None:
    if postback.expires_at < (now if now is not None else int(time.time())):
        raise TaskLineSecurityError("Task command has expired")
    expected = sign_task_postback(
        command=postback.command,
        task_id=postback.task_id,
        assignment_id=postback.assignment_id,
        expected_version=postback.expected_version,
        assignee_id=assignee_id,
        expires_at=postback.expires_at,
    )
    if not hmac.compare_digest(postback.token, expected):
        raise TaskLineSecurityError("Task command signature is invalid")
