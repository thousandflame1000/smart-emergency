from __future__ import annotations

from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.road_workflow import RoadObservation, TaskRoute
from app.models.task_workflow import Proposal, Task, TaskEvent
from app.models.user import User
from app.routers import tasks as tasks_router
from app.services import road_network
from app.services.road_replanning import RoadReplanningService, TaskRouteService


def _utcnow():
    return datetime.now(UTC).replace(tzinfo=None)


def _task_with_proposal(db, *, start="yuli", end="changbin"):
    admin = User(name="Admin", roles=["admin"], is_active=True)
    volunteer = User(name="Volunteer", roles=["volunteer"], is_active=True)
    resident = User(name="Resident", roles=["elderly"], is_active=True)
    db.add_all([admin, volunteer, resident])
    db.flush()
    resource = CommunityResource(
        owner_id=volunteer.id,
        resource_type="water",
        name="Water vehicle",
        is_available=False,
    )
    need = CommunityNeed(
        requester_id=resident.id,
        need_type="water",
        description="Deliver water",
        urgency=5,
        status="matched",
    )
    db.add_all([resource, need])
    db.flush()
    proposal = Proposal(
        need_id=need.id,
        status="APPROVED",
        created_by=admin.id,
        algorithm="initial-dispatch",
        algorithm_version="1",
        score=90.0,
        explanation_json={"reason": "initial route"},
        candidate_resource_id=resource.id,
        candidate_assignee_id=volunteer.id,
        route_reference="initial-route-placeholder",
        fingerprint="initial-proposal-fingerprint",
        version=2,
    )
    db.add(proposal)
    db.flush()
    task = Task(
        need_id=need.id,
        proposal_id=proposal.id,
        task_type="FULFILL_COMMUNITY_NEED",
        status="IN_PROGRESS",
        priority=5,
        version=4,
        approved_by=admin.id,
    )
    db.add(task)
    db.commit()
    route = TaskRouteService(db).attach_route(
        task_id=str(task.id),
        start_node=start,
        end_node=end,
        proposal_id=str(proposal.id),
    )
    return task, proposal, route


def test_route_records_stable_road_segment_ids(db):
    task, _, route = _task_with_proposal(db)

    assert str(task.route_reference) == str(route.id)
    assert route.node_path == ["yuli", "changbin"]
    assert route.segment_ids == [road_network.edge_id("yuli", "changbin")]
    assert route.status == "ACTIVE"


def test_blocked_observation_creates_pending_replan_without_overwriting_history(db):
    task, original_proposal, original_route = _task_with_proposal(db)
    segment_id = road_network.edge_id("yuli", "changbin")
    task_id = str(task.id)
    proposal_id = str(original_proposal.id)
    route_id = str(original_route.id)

    result = RoadReplanningService(db).observe(
        road_segment_id=segment_id,
        state="BLOCKED",
        source="county-road-feed",
        observed_at=_utcnow(),
    )

    current_task = db.get(Task, task_id)
    original_proposal = db.get(Proposal, proposal_id)
    original_route = db.get(TaskRoute, route_id)
    proposals = (
        db.query(Proposal)
        .filter(Proposal.need_id == current_task.need_id)
        .order_by(Proposal.created_at, Proposal.id)
        .all()
    )
    new_proposal = next(item for item in proposals if str(item.id) != proposal_id)
    proposed_route = db.get(TaskRoute, new_proposal.route_reference)

    assert result["affected_task_ids"] == [task_id]
    assert result["proposal_ids"] == [str(new_proposal.id)]
    assert (current_task.status, current_task.version) == ("BLOCKED", 5)
    assert original_proposal.status == "APPROVED"
    assert original_route.status == "BLOCKED"
    assert str(current_task.route_reference) == route_id
    assert new_proposal.status == "PENDING_APPROVAL"
    assert new_proposal.algorithm == "road-replan"
    assert new_proposal.explanation_json["previous_proposal_id"] == proposal_id
    assert proposed_route.status == "PROPOSED"
    assert segment_id not in proposed_route.segment_ids
    assert db.query(Task).count() == 1
    events = (
        db.query(TaskEvent)
        .filter(TaskEvent.task_id == current_task.id)
        .order_by(TaskEvent.occurred_at, TaskEvent.id)
        .all()
    )
    assert {event.event_type for event in events} == {"ROAD_BLOCKED", "REPLAN_PROPOSED"}


def test_no_alternative_route_keeps_task_blocked_and_creates_visible_alert(db):
    road_network.add_node(db, "isolated_drop", 23.34, 121.32, "Isolated drop")
    road_network.add_edge(db, "yuli", "isolated_drop")
    task, original_proposal, route = _task_with_proposal(
        db,
        start="yuli",
        end="isolated_drop",
    )
    segment_id = road_network.edge_id("yuli", "isolated_drop")

    result = RoadReplanningService(db).observe(
        road_segment_id=segment_id,
        state="BLOCKED",
        source="field-team",
    )

    current_task = db.get(Task, task.id)
    current_route = db.get(TaskRoute, route.id)
    proposals = db.query(Proposal).filter(Proposal.need_id == current_task.need_id).all()
    alert = (
        db.query(TaskEvent)
        .filter(
            TaskEvent.task_id == current_task.id,
            TaskEvent.event_type == "REPLAN_UNAVAILABLE",
        )
        .one()
    )
    assert current_task.status == "BLOCKED"
    assert current_route.status == "UNREACHABLE"
    assert len(proposals) == 1
    assert str(proposals[0].id) == str(original_proposal.id)
    assert result["proposal_ids"] == []
    assert result["alerts"]
    assert alert.metadata_json["severity"] == "critical"
    assert segment_id in alert.metadata_json["message"]

    app = FastAPI()
    app.include_router(tasks_router.router, prefix="/api/tasks")
    response = TestClient(app).get(f"/api/tasks/{current_task.id}/events")
    assert response.status_code == 200
    assert any(
        item["event_type"] == "REPLAN_UNAVAILABLE"
        for item in response.json()["events"]
    )


def test_road_observations_are_append_only_and_latest_state_wins(db):
    segment_id = road_network.edge_id("yuli", "changbin")
    first_time = _utcnow()
    service = RoadReplanningService(db)
    first = service.observe(
        road_segment_id=segment_id,
        state="SLOW",
        source="sensor-a",
        observed_at=first_time,
    )
    service.observe(
        road_segment_id=segment_id,
        state="OPEN",
        source="field-verification",
        observed_at=first_time + timedelta(minutes=5),
    )

    rows = (
        db.query(RoadObservation)
        .filter(RoadObservation.road_segment_id == segment_id)
        .order_by(RoadObservation.observed_at)
        .all()
    )
    assert len(rows) == 2
    assert [row.state for row in rows] == ["SLOW", "OPEN"]
    assert road_network.current_road_state(db, segment_id) == "OPEN"

    rows[0].source = "rewritten"
    with pytest.raises(ValueError, match="append-only"):
        db.commit()
    db.rollback()
    assert str(rows[0].id) == first["observation_id"]
