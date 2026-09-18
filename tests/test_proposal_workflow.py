from pathlib import Path

import pytest
from sqlalchemy import create_engine, inspect

from app.database import Base, SessionLocal
from app.models.config import SystemConfig
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.task_workflow import Approval, Proposal, Task, TaskAssignment, TaskEvent
from app.models.user import User
from app.services import dispatch
from app.services.proposal_workflow import (
    ApprovalService,
    ProposalAuthorizationError,
    ProposalFingerprintConflictError,
    ProposalService,
    ProposalVersionConflictError,
)


WORKFLOW_TABLES = [Proposal, Approval, Task, TaskAssignment, TaskEvent]
WORKFLOW_TABLE_NAMES = {model.__tablename__ for model in WORKFLOW_TABLES}


def _user(db, name, roles):
    user = User(name=name, roles=roles, is_active=True)
    db.add(user)
    db.flush()
    return user


def _need_and_proposal(db):
    admin = _user(db, "Admin", ["admin"])
    volunteer = _user(db, "Volunteer", ["volunteer"])
    resident = _user(db, "Resident", ["elderly"])
    resource = CommunityResource(
        owner_id=volunteer.id,
        resource_type="water",
        name="Water delivery",
        is_available=True,
    )
    need = CommunityNeed(
        requester_id=resident.id,
        need_type="water",
        description="Need drinking water",
        address="Private resident address",
        urgency=4,
        status="open",
    )
    db.add_all([resource, need])
    db.flush()
    proposal = ProposalService(db).create_from_dispatch_suggestion(
        need=need,
        algorithm="test-dispatch",
        algorithm_version="1",
        score=91.25,
        explanation_json={"score": 91.25, "reason": "closest available resource"},
        candidate_resource_id=resource.id,
        candidate_assignee_id=volunteer.id,
        created_by=admin.id,
    )
    db.commit()
    return admin, volunteer, resident, resource, need, proposal


def test_need_to_proposal_from_dispatch_suggestion(db, monkeypatch):
    volunteer = _user(db, "Dispatch volunteer", ["volunteer"])
    resident = _user(db, "Dispatch resident", ["elderly"])
    resource = CommunityResource(
        owner_id=volunteer.id,
        resource_type="water",
        name="Bottled water",
        lat=24.15,
        lng=120.68,
        is_available=True,
    )
    need = CommunityNeed(
        requester_id=resident.id,
        need_type="water",
        description="Water request",
        lat=24.151,
        lng=120.681,
        urgency=4,
        status="open",
    )
    db.add_all([resource, need, SystemConfig(key="mode", value="emergency")])
    db.commit()
    need_id = str(need.id)
    resource_id = str(resource.id)
    volunteer_id = str(volunteer.id)
    db.close()

    sent = {"value": False}
    monkeypatch.setattr(dispatch.settings, "TASK_WORKFLOW_V2", True)
    monkeypatch.setattr(
        dispatch,
        "send_task_message",
        lambda *args, **kwargs: sent.update(value=True),
    )

    result = dispatch.auto_dispatch()

    assert result["suggested"] == 1
    assert sent["value"] is False
    db2 = SessionLocal()
    try:
        proposal = db2.query(Proposal).filter(Proposal.need_id == need_id).one()
        assert proposal.status == "PENDING_APPROVAL"
        assert proposal.version == 1
        assert proposal.fingerprint
        assert str(proposal.candidate_resource_id) == resource_id
        assert str(proposal.candidate_assignee_id) == volunteer_id
        assert result["details"][0]["proposal_id"] == str(proposal.id)
    finally:
        db2.close()


def test_proposal_approval_creates_task_and_event_atomically(db):
    admin, _, _, _, need, proposal = _need_and_proposal(db)

    result = ApprovalService(db).approve(
        proposal_id=str(proposal.id),
        actor_id=str(admin.id),
        expected_version=1,
        expected_fingerprint=proposal.fingerprint,
        comment="Capacity confirmed",
    )

    assert (result.proposal.status, result.proposal.version) == ("APPROVED", 2)
    assert result.approval.decision == "APPROVE"
    assert result.approval.proposal_version == 1
    assert str(result.approval.actor_id) == str(admin.id)
    assert result.approval.created_at is not None
    assert result.task.status == "APPROVED"
    assert result.task.version == 1
    assert str(result.task.need_id) == str(need.id)
    assert str(result.task.proposal_id) == str(proposal.id)
    assert not hasattr(result.task, "requester_id")
    assert not hasattr(result.task, "need_description")
    event = db.query(TaskEvent).filter(TaskEvent.task_id == result.task.id).one()
    assert event.event_type == "TASK_CREATED"
    assert event.task_version == 1
    assert event.metadata_json["proposal_version"] == 1


def test_non_admin_cannot_approve_proposal(db):
    _, volunteer, _, _, _, proposal = _need_and_proposal(db)

    with pytest.raises(ProposalAuthorizationError) as exc:
        ApprovalService(db).approve(
            proposal_id=str(proposal.id),
            actor_id=str(volunteer.id),
            expected_version=1,
            expected_fingerprint=proposal.fingerprint,
        )

    assert exc.value.status_code == 403
    assert db.query(Approval).count() == 0
    assert db.query(Task).count() == 0


def test_stale_proposal_version_returns_conflict(db):
    admin, _, _, _, _, proposal = _need_and_proposal(db)

    with pytest.raises(ProposalVersionConflictError) as exc:
        ApprovalService(db).approve(
            proposal_id=str(proposal.id),
            actor_id=str(admin.id),
            expected_version=0,
            expected_fingerprint=proposal.fingerprint,
        )

    assert exc.value.status_code == 409
    assert db.query(Approval).count() == 0
    assert db.query(Task).count() == 0


def test_stale_proposal_fingerprint_returns_conflict(db):
    admin, _, _, _, _, proposal = _need_and_proposal(db)

    with pytest.raises(ProposalFingerprintConflictError) as exc:
        ApprovalService(db).approve(
            proposal_id=str(proposal.id),
            actor_id=str(admin.id),
            expected_version=1,
            expected_fingerprint="stale-review-fingerprint",
        )

    assert exc.value.status_code == 409
    assert db.query(Approval).count() == 0
    assert db.query(Task).count() == 0


def test_mutated_proposal_payload_invalidates_its_fingerprint(db):
    admin, _, _, _, _, proposal = _need_and_proposal(db)
    reviewed_fingerprint = proposal.fingerprint
    proposal.score = 1.0
    db.commit()

    with pytest.raises(ProposalFingerprintConflictError):
        ApprovalService(db).approve(
            proposal_id=str(proposal.id),
            actor_id=str(admin.id),
            expected_version=1,
            expected_fingerprint=reviewed_fingerprint,
        )

    assert db.query(Approval).count() == 0
    assert db.query(Task).count() == 0


def test_task_workflow_migration_is_additive_and_reversible():
    engine = create_engine("sqlite:///:memory:")
    workflow_tables = [model.__table__ for model in WORKFLOW_TABLES]
    baseline_tables = [
        table for table in Base.metadata.sorted_tables
        if table.name not in WORKFLOW_TABLE_NAMES
    ]

    Base.metadata.create_all(engine, tables=baseline_tables)
    baseline_names = set(inspect(engine).get_table_names())
    Base.metadata.create_all(engine, tables=workflow_tables)
    migrated_names = set(inspect(engine).get_table_names())

    assert migrated_names == baseline_names | WORKFLOW_TABLE_NAMES

    for table in reversed(workflow_tables):
        table.drop(engine)
    assert set(inspect(engine).get_table_names()) == baseline_names

    root = Path(__file__).resolve().parents[1]
    up_sql = (root / "migrations" / "002_task_workflow_v2.up.sql").read_text("utf-8").upper()
    down_sql = (root / "migrations" / "002_task_workflow_v2.down.sql").read_text("utf-8").upper()
    for table_name in WORKFLOW_TABLE_NAMES:
        assert f"CREATE TABLE IF NOT EXISTS {table_name.upper()}" in up_sql
        assert f"DROP TABLE IF EXISTS {table_name.upper()}" in down_sql
    assert "DROP TABLE COMMUNITY_NEEDS" not in up_sql
    assert "ALTER TABLE COMMUNITY_NEEDS" not in up_sql
