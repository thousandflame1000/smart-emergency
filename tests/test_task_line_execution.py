from __future__ import annotations

import json
import time

import pytest

from app.database import SessionLocal
from app.models.need import CommunityNeed
from app.models.outbox import OutboxMessage
from app.models.task_workflow import Task, TaskAssignment, TaskEvent
from app.models.user import User
from app.routers import linebot as linebot_router
from app.services.task_commands import (
    InvalidTaskTransitionError,
    TaskAuthorizationError,
    TaskCommandService,
    TaskVersionConflictError,
)
from app.services.task_line_execution import TaskLineExecutionService
from app.services.task_line_messages import build_task_assignment_flex
from app.services.outbox import OutboxWorker
from app.services.task_line_security import (
    TaskLineCommand,
    TaskLineSecurityError,
    build_task_postback_data,
    parse_task_postback,
)


class _FakeSource:
    def __init__(self, user_id):
        self.user_id = user_id


class _FakePostback:
    def __init__(self, data):
        self.data = data


class _FakePostbackEvent:
    def __init__(self, data, user_id, reply_token="reply-token"):
        self.postback = _FakePostback(data)
        self.source = _FakeSource(user_id)
        self.reply_token = reply_token


def _user(db, name, roles, line_uid=None):
    user = User(name=name, roles=roles, line_uid=line_uid, is_active=True)
    db.add(user)
    db.flush()
    return user


def _assigned_task(db):
    admin = _user(db, "Admin", ["admin"])
    volunteer_a = _user(db, "Volunteer A", ["volunteer"], "U-volunteer-a")
    volunteer_b = _user(db, "Volunteer B", ["volunteer"], "U-volunteer-b")
    resident = _user(db, "Private Resident", ["elderly"])
    resident.phone = "0900-private"
    need = CommunityNeed(
        requester_id=resident.id,
        need_type="water",
        description="Deliver two water packs",
        address="No. 1 Test Road",
        urgency=4,
        status="open",
    )
    task = Task(
        need=need,
        task_type="FULFILL_COMMUNITY_NEED",
        status="APPROVED",
        priority=4,
        instructions="Wear a reflective vest and confirm the route.",
        destination_reference="community_need:test",
        version=1,
        approved_by=admin.id,
    )
    db.add_all([need, task])
    db.commit()
    service = TaskCommandService(db)
    task = service.assign_task(str(task.id), str(volunteer_a.id), str(admin.id), 1)
    assignment = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task.id, TaskAssignment.status == "ASSIGNED")
        .one()
    )
    return admin, volunteer_a, volunteer_b, resident, need, task, assignment, service


def _command(task, assignment, command, *, version=None, expires_at=None):
    return build_task_postback_data(
        command=command,
        task_id=str(task.id),
        assignment_id=str(assignment.id),
        expected_version=task.version if version is None else version,
        assignee_id=str(assignment.assignee_id),
        expires_at=expires_at,
    )


def test_assignment_generates_privacy_minimized_line_message(db, monkeypatch):
    captured = {}

    def capture(line_uid, alt_text, contents):
        captured.update(line_uid=line_uid, alt_text=alt_text, contents=contents)

    monkeypatch.setattr(
        "app.services.outbox.send_task_assignment_message",
        lambda line_uid, task, assignment, need: capture(
            line_uid,
            "志工執行任務",
            build_task_assignment_flex(task, assignment, need),
        ),
    )
    _, volunteer, _, resident, _, task, assignment, _ = _assigned_task(db)

    outbox = db.query(OutboxMessage).filter(OutboxMessage.aggregate_id == str(task.id)).one()
    assert outbox.status == "PENDING"
    delivered = OutboxWorker(db, worker_id="line-message-test").process_next()
    assert delivered.status == "SENT"
    assert captured["line_uid"] == volunteer.line_uid
    serialized = json.dumps(captured["contents"], ensure_ascii=False)
    assert str(task.id) in serialized
    assert "Deliver two water packs" in serialized
    assert "No. 1 Test Road" in serialized
    assert "必要注意事項" in serialized
    assert "接受任務" in serialized and "拒絕任務" in serialized
    assert resident.name not in serialized
    assert resident.phone not in serialized

    buttons = captured["contents"]["footer"]["contents"]
    postbacks = [parse_task_postback(button["action"]["data"]) for button in buttons]
    assert {item.command for item in postbacks} == {
        TaskLineCommand.ACCEPT,
        TaskLineCommand.DECLINE,
    }
    assert all(item.task_id == str(task.id) for item in postbacks)
    assert all(item.assignment_id == str(assignment.id) for item in postbacks)
    assert all(item.expected_version == task.version for item in postbacks)


def test_assignee_accepts_task_from_line_router(db, monkeypatch):
    _, volunteer, _, _, _, task, assignment, _ = _assigned_task(db)
    raw = _command(task, assignment, TaskLineCommand.ACCEPT)
    task_id = str(task.id)
    assignment_id = str(assignment.id)
    line_uid = volunteer.line_uid
    db.close()
    replies = []
    monkeypatch.setattr(
        linebot_router,
        "reply_task_progress_message",
        lambda *args: replies.append("progress"),
    )
    monkeypatch.setattr(
        linebot_router,
        "reply_text",
        lambda token, text: replies.append(text),
    )

    linebot_router.handle_postback(_FakePostbackEvent(raw, line_uid))

    db2 = SessionLocal()
    try:
        current = db2.get(Task, task_id)
        current_assignment = db2.get(TaskAssignment, assignment_id)
        assert (current.status, current.version) == ("ACKNOWLEDGED", 3)
        assert current_assignment.status == "ACCEPTED"
        assert replies == ["progress"]
    finally:
        db2.close()


def test_every_line_command_contains_signed_task_context(db):
    _, _, _, _, need, task, assignment, _ = _assigned_task(db)
    expected = {
        "ASSIGNED": {TaskLineCommand.ACCEPT, TaskLineCommand.DECLINE},
        "ACKNOWLEDGED": {TaskLineCommand.START, TaskLineCommand.FAIL},
        "IN_PROGRESS": {TaskLineCommand.ARRIVE, TaskLineCommand.FAIL},
        "ARRIVED": {TaskLineCommand.COMPLETE, TaskLineCommand.FAIL},
    }

    for version, (status, commands) in enumerate(expected.items(), start=2):
        task.status = status
        task.version = version
        flex = build_task_assignment_flex(task, assignment, need)
        postbacks = [
            parse_task_postback(button["action"]["data"])
            for button in flex["footer"]["contents"]
        ]
        assert {item.command for item in postbacks} == commands
        assert all(item.task_id == str(task.id) for item in postbacks)
        assert all(item.assignment_id == str(assignment.id) for item in postbacks)
        assert all(item.expected_version == version for item in postbacks)
        assert all(item.token for item in postbacks)


def test_other_volunteer_cannot_accept_task_from_line(db):
    _, _, volunteer_b, _, _, task, assignment, _ = _assigned_task(db)
    raw = _command(task, assignment, TaskLineCommand.ACCEPT)

    with pytest.raises(TaskAuthorizationError) as exc:
        TaskLineExecutionService(db).execute(raw, volunteer_b.line_uid)

    assert exc.value.status_code == 403
    assert db.get(Task, task.id).status == "ASSIGNED"


def test_line_rejects_assigned_to_complete(db):
    _, volunteer, _, _, _, task, assignment, _ = _assigned_task(db)
    raw = _command(task, assignment, TaskLineCommand.COMPLETE)

    with pytest.raises(InvalidTaskTransitionError) as exc:
        TaskLineExecutionService(db).execute(raw, volunteer.line_uid)

    assert exc.value.status_code == 409
    assert db.get(Task, task.id).status == "ASSIGNED"


def test_line_rejects_stale_task_version(db):
    _, volunteer, _, _, _, task, assignment, _ = _assigned_task(db)
    raw = _command(task, assignment, TaskLineCommand.ACCEPT, version=1)

    with pytest.raises(TaskVersionConflictError) as exc:
        TaskLineExecutionService(db).execute(raw, volunteer.line_uid)

    assert exc.value.status_code == 409
    assert db.get(Task, task.id).status == "ASSIGNED"


def test_line_full_execution_flow_appends_every_task_event(db):
    _, volunteer, _, _, _, task, assignment, _ = _assigned_task(db)
    execution = TaskLineExecutionService(db)

    for command, expected_status in [
        (TaskLineCommand.ACCEPT, "ACKNOWLEDGED"),
        (TaskLineCommand.START, "IN_PROGRESS"),
        (TaskLineCommand.ARRIVE, "ARRIVED"),
        (TaskLineCommand.COMPLETE, "COMPLETED"),
    ]:
        raw = _command(task, assignment, command)
        result = execution.execute(raw, volunteer.line_uid)
        task = result.task
        assignment = result.assignment
        assert task.status == expected_status

    events = (
        db.query(TaskEvent)
        .filter(TaskEvent.task_id == task.id)
        .order_by(TaskEvent.task_version)
        .all()
    )
    assert [event.event_type for event in events] == [
        "ASSIGNED",
        "ACKNOWLEDGED",
        "DEPARTED",
        "ARRIVED",
        "COMPLETED",
    ]
    assert [event.task_version for event in events] == [2, 3, 4, 5, 6]


def test_line_decline_allows_reassignment_without_losing_history(db):
    admin, volunteer_a, volunteer_b, _, _, task, assignment, _ = _assigned_task(db)
    raw = _command(task, assignment, TaskLineCommand.DECLINE)

    task = TaskLineExecutionService(db).execute(raw, volunteer_a.line_uid).task
    assert (task.status, task.version) == ("BLOCKED", 3)
    assert db.get(TaskAssignment, assignment.id).status == "DECLINED"

    task = TaskCommandService(db).assign_task(
        str(task.id),
        str(volunteer_b.id),
        str(admin.id),
        3,
    )
    history = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task.id)
        .order_by(TaskAssignment.task_version)
        .all()
    )
    assert (task.status, task.version) == ("ASSIGNED", 4)
    assert [item.status for item in history] == ["DECLINED", "ASSIGNED"]
    assert [str(item.assignee_id) for item in history] == [
        str(volunteer_a.id),
        str(volunteer_b.id),
    ]


def test_line_fail_appends_task_event(db):
    _, volunteer, _, _, _, task, assignment, _ = _assigned_task(db)
    execution = TaskLineExecutionService(db)
    task = execution.execute(
        _command(task, assignment, TaskLineCommand.ACCEPT),
        volunteer.line_uid,
    ).task
    result = execution.execute(
        _command(task, assignment, TaskLineCommand.FAIL),
        volunteer.line_uid,
    )

    assert result.task.status == "FAILED"
    event = (
        db.query(TaskEvent)
        .filter(TaskEvent.task_id == task.id, TaskEvent.event_type == "FAILED")
        .one()
    )
    assert event.task_version == 4


def test_tampered_or_expired_line_postback_is_rejected(db):
    _, volunteer, _, _, _, task, assignment, _ = _assigned_task(db)
    valid = _command(task, assignment, TaskLineCommand.ACCEPT)
    tampered = valid.replace("command=ACCEPT", "command=START")
    expired = _command(
        task,
        assignment,
        TaskLineCommand.ACCEPT,
        expires_at=int(time.time()) - 1,
    )

    for raw in (tampered, expired):
        with pytest.raises(TaskLineSecurityError) as exc:
            TaskLineExecutionService(db).execute(raw, volunteer.line_uid)
        assert exc.value.status_code == 403

    assert db.get(Task, task.id).status == "ASSIGNED"
