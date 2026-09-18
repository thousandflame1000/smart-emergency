from __future__ import annotations

import pytest
from sqlalchemy.exc import IntegrityError

from app.models.need import CommunityNeed
from app.models.task_workflow import Approval, Proposal, Task, TaskAssignment, TaskEvent
from app.models.user import User
from app.services.task_commands import (
    AssignmentStatus,
    InvalidTaskTransitionError,
    TaskAuthorizationError,
    TaskCommandService,
    TaskStatus,
    TaskVersionConflictError,
)


def _user(db, name, roles):
    user = User(name=name, roles=roles, is_active=True)
    db.add(user)
    db.flush()
    return user


def _task_fixture(db, *, with_proposal=False):
    admin = _user(db, "Admin", ["admin"])
    volunteer_a = _user(db, "Volunteer A", ["volunteer"])
    volunteer_b = _user(db, "Volunteer B", ["volunteer"])
    resident = _user(db, "Resident", ["elderly"])
    need = CommunityNeed(
        requester_id=resident.id,
        need_type="water",
        description="Need water",
        urgency=4,
        status="open",
    )
    db.add(need)
    db.flush()

    proposal = None
    if with_proposal:
        proposal = Proposal(
            need_id=need.id,
            status="APPROVED",
            created_by=admin.id,
            algorithm="legacy_dispatch",
            algorithm_version="phase-1-test",
            score=88.5,
            explanation_json={"distance_km": 1.2, "vulnerability": 9},
            candidate_assignee_id=volunteer_a.id,
            fingerprint="test-fingerprint",
            version=2,
        )
        db.add(proposal)
        db.flush()

    task = Task(
        need_id=need.id,
        proposal_id=proposal.id if proposal else None,
        task_type="DELIVER_SUPPLY",
        status=TaskStatus.APPROVED.value,
        priority=4,
        instructions="Deliver water",
        destination_reference="need:destination",
        version=1,
        approved_by=admin.id,
    )
    db.add(task)
    db.commit()
    return admin, volunteer_a, volunteer_b, resident, need, proposal, task


def test_task_domain_models_preserve_references_without_copying_need(db):
    admin, volunteer, _, _, need, proposal, task = _task_fixture(db, with_proposal=True)
    approval = Approval(
        proposal_id=proposal.id,
        actor_id=admin.id,
        decision="APPROVE",
        comment="Verified current proposal",
        proposal_version=proposal.version,
    )
    db.add(approval)
    db.commit()

    assert str(task.need_id) == str(need.id)
    assert str(task.proposal_id) == str(proposal.id)
    assert not hasattr(task, "need_description")
    assert proposal.explanation_json["vulnerability"] == 9
    assert approval.proposal_version == 2
    assert str(proposal.candidate_assignee_id) == str(volunteer.id)


def test_approval_and_task_event_are_append_only(db):
    admin, _, _, _, _, proposal, task = _task_fixture(db, with_proposal=True)
    approval = Approval(
        proposal_id=proposal.id,
        actor_id=admin.id,
        decision="APPROVE",
        proposal_version=proposal.version,
    )
    event = TaskEvent(
        task_id=task.id,
        task_version=1,
        event_type="TASK_CREATED",
        actor_id=admin.id,
        metadata_json={},
    )
    db.add_all([approval, event])
    db.commit()

    approval.comment = "rewrite"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()

    event.event_type = "REWRITTEN"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()


def test_complete_happy_path_writes_versioned_timeline(db):
    admin, volunteer, _, _, _, _, task = _task_fixture(db)
    service = TaskCommandService(db)

    task = service.assign_task(str(task.id), str(volunteer.id), str(admin.id), 1)
    assert (task.status, task.version) == (TaskStatus.ASSIGNED.value, 2)
    task = service.acknowledge_task(str(task.id), str(volunteer.id), 2)
    task = service.start_task(str(task.id), str(volunteer.id), 3)
    task = service.mark_arrived(str(task.id), str(volunteer.id), 4)
    task = service.complete_task(
        str(task.id), str(volunteer.id), 5, metadata={"proof": "photo-ref"}
    )

    assert (task.status, task.version) == (TaskStatus.COMPLETED.value, 6)
    events = db.query(TaskEvent).filter(TaskEvent.task_id == task.id).order_by(TaskEvent.task_version).all()
    assert [e.event_type for e in events] == [
        "ASSIGNED",
        "ACKNOWLEDGED",
        "DEPARTED",
        "ARRIVED",
        "COMPLETED",
    ]
    assert [e.task_version for e in events] == [2, 3, 4, 5, 6]
    assert events[-1].metadata_json["proof"] == "photo-ref"


def test_stale_expected_version_returns_conflict_without_side_effect(db):
    admin, volunteer, _, _, _, _, task = _task_fixture(db)
    service = TaskCommandService(db)
    task = service.assign_task(str(task.id), str(volunteer.id), str(admin.id), 1)

    with pytest.raises(TaskVersionConflictError) as exc:
        service.acknowledge_task(str(task.id), str(volunteer.id), 1)

    assert exc.value.status_code == 409
    current = db.get(Task, task.id)
    assert (current.status, current.version) == (TaskStatus.ASSIGNED.value, 2)
    assert db.query(TaskEvent).filter(TaskEvent.task_id == task.id).count() == 1


def test_non_assignee_cannot_complete_task(db):
    admin, volunteer_a, volunteer_b, _, _, _, task = _task_fixture(db)
    service = TaskCommandService(db)
    task = service.assign_task(str(task.id), str(volunteer_a.id), str(admin.id), 1)
    task = service.acknowledge_task(str(task.id), str(volunteer_a.id), 2)
    task = service.start_task(str(task.id), str(volunteer_a.id), 3)
    task = service.mark_arrived(str(task.id), str(volunteer_a.id), 4)

    with pytest.raises(TaskAuthorizationError) as exc:
        service.complete_task(str(task.id), str(volunteer_b.id), 5)

    assert exc.value.status_code == 403
    current = db.get(Task, task.id)
    assert (current.status, current.version) == (TaskStatus.ARRIVED.value, 5)


def test_actor_authorization_is_checked_before_stale_version(db):
    admin, volunteer_a, volunteer_b, _, _, _, task = _task_fixture(db)
    service = TaskCommandService(db)
    task = service.assign_task(str(task.id), str(volunteer_a.id), str(admin.id), 1)

    with pytest.raises(TaskAuthorizationError):
        service.acknowledge_task(str(task.id), str(volunteer_b.id), 1)

    current = db.get(Task, task.id)
    assert (current.status, current.version) == (TaskStatus.ASSIGNED.value, 2)


def test_assigned_task_cannot_skip_directly_to_completed(db):
    admin, volunteer, _, _, _, _, task = _task_fixture(db)
    service = TaskCommandService(db)
    task = service.assign_task(str(task.id), str(volunteer.id), str(admin.id), 1)

    with pytest.raises(InvalidTaskTransitionError):
        service.complete_task(str(task.id), str(volunteer.id), 2)

    current = db.get(Task, task.id)
    assert (current.status, current.version) == (TaskStatus.ASSIGNED.value, 2)
    assert db.query(TaskEvent).filter(TaskEvent.task_id == task.id).count() == 1


def test_decline_blocks_task_and_reassignment_preserves_history(db):
    admin, volunteer_a, volunteer_b, _, _, _, task = _task_fixture(db)
    service = TaskCommandService(db)
    task = service.assign_task(str(task.id), str(volunteer_a.id), str(admin.id), 1)
    task = service.decline_task(str(task.id), str(volunteer_a.id), 2, "unsafe route")
    assert (task.status, task.version) == (TaskStatus.BLOCKED.value, 3)

    task = service.assign_task(str(task.id), str(volunteer_b.id), str(admin.id), 3)
    assignments = (
        db.query(TaskAssignment)
        .filter(TaskAssignment.task_id == task.id)
        .order_by(TaskAssignment.task_version)
        .all()
    )
    assert (task.status, task.version) == (TaskStatus.ASSIGNED.value, 4)
    assert [a.status for a in assignments] == [
        AssignmentStatus.DECLINED.value,
        AssignmentStatus.ASSIGNED.value,
    ]
    assert [str(a.assignee_id) for a in assignments] == [
        str(volunteer_a.id),
        str(volunteer_b.id),
    ]
    events = db.query(TaskEvent).filter(TaskEvent.task_id == task.id).order_by(TaskEvent.task_version).all()
    assert [e.event_type for e in events] == [
        "ASSIGNED",
        "ASSIGNMENT_DECLINED",
        "REASSIGNED",
    ]


def test_only_manager_can_cancel_task(db):
    admin, volunteer, _, _, _, _, task = _task_fixture(db)
    service = TaskCommandService(db)
    task = service.assign_task(str(task.id), str(volunteer.id), str(admin.id), 1)

    with pytest.raises(TaskAuthorizationError):
        service.cancel_task(str(task.id), str(volunteer.id), 2)

    task = service.cancel_task(str(task.id), str(admin.id), 2, "operator decision")
    assignment = db.query(TaskAssignment).filter(TaskAssignment.task_id == task.id).one()
    assert (task.status, task.version) == (TaskStatus.CANCELLED.value, 3)
    assert assignment.status == AssignmentStatus.REVOKED.value


def test_database_rejects_unknown_task_status(db):
    admin, _, _, _, need, _, _ = _task_fixture(db)
    db.add(
        Task(
            need_id=need.id,
            task_type="INVALID",
            status="WHATEVER",
            priority=3,
            approved_by=admin.id,
        )
    )
    with pytest.raises(IntegrityError):
        db.commit()
    db.rollback()
