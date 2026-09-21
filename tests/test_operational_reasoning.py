# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.alert import Alert
from app.models.need import CommunityNeed
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.routers import ontology as ontology_router
from app.services import reasoning


def _client():
    app = FastAPI()
    app.include_router(ontology_router.router, prefix="/api/ontology")
    return TestClient(app)


def _ids(report):
    return {f["id"] for f in report["findings"]}


def test_reasoning_flags_urgent_open_need_without_candidate(db):
    elder = User(name="Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
    db.add(elder)
    db.commit()
    need = CommunityNeed(
        requester_id=elder.id,
        need_type="water",
        urgency=5,
        status="open",
        lat=23.3333,
        lng=121.3167,
    )
    db.add(need)
    db.commit()

    report = reasoning.operational_risks(db)
    finding = next(f for f in report["findings"] if f["id"] == f"open_need_no_candidate:{need.id}")

    assert finding["severity"] == "critical"
    assert finding["category"] == "dispatch"
    assert finding["recommended_action"]["action_id"] == "manual_dispatch"


def test_reasoning_flags_active_alert_without_resource_request(db):
    elder = User(name="No response elder", roles=["elderly"])
    db.add(elder)
    db.commit()
    alert = Alert(elderly_id=elder.id, alert_type="no_response_3h", status="sent")
    db.add(alert)
    db.commit()

    report = reasoning.operational_risks(db)

    assert f"alert_without_need:{alert.id}" in _ids(report)


def test_reasoning_flags_facility_capacity_pressure(db):
    facility = ResourcePoint(
        name="Shelter A",
        point_type="shelter",
        capacity=10,
        current_load=10,
        is_active=True,
    )
    db.add(facility)
    db.commit()

    report = reasoning.operational_risks(db)
    finding = next(f for f in report["findings"] if f["id"] == f"facility_capacity:{facility.id}")

    assert finding["severity"] == "high"
    assert finding["recommended_action"]["action_id"] == "redirect_to_alternate_facility"


def test_operational_risks_endpoint_returns_ranked_findings(db):
    elder = User(name="Endpoint Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
    db.add(elder)
    db.commit()
    db.add(CommunityNeed(
        requester_id=elder.id,
        need_type="water",
        urgency=5,
        status="open",
        lat=23.3333,
        lng=121.3167,
    ))
    db.commit()

    res = _client().get("/api/ontology/reasoning/operational-risks")
    data = res.json()

    assert res.status_code == 200
    assert data["summary"]["critical"] >= 1
    assert data["findings"][0]["severity"] == "critical"


def test_operational_playbook_groups_findings_into_action_steps(db):
    elder = User(name="Playbook Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
    db.add(elder)
    db.commit()
    need = CommunityNeed(
        requester_id=elder.id,
        need_type="water",
        urgency=5,
        status="open",
        lat=23.3333,
        lng=121.3167,
    )
    db.add(need)
    db.commit()

    report = reasoning.operational_playbook(db)
    step = next(s for s in report["steps"] if s["action_id"] == "manual_dispatch")

    assert step["severity"] == "critical"
    assert step["priority"] >= 100
    assert f"open_need_no_candidate:{need.id}" in step["finding_ids"]
    assert step["expected_impact"]
    assert step["operator_checklist"]
    assert step["blocked_by"]


def test_operational_playbook_endpoint_returns_prioritized_steps(db):
    elder = User(name="Endpoint Playbook Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
    db.add(elder)
    db.commit()
    db.add(CommunityNeed(
        requester_id=elder.id,
        need_type="water",
        urgency=5,
        status="open",
        lat=23.3333,
        lng=121.3167,
    ))
    db.commit()

    res = _client().get("/api/ontology/reasoning/playbook")
    data = res.json()

    assert res.status_code == 200
    assert data["counts"]["critical_steps"] >= 1
    assert data["steps"][0]["priority"] >= data["steps"][-1]["priority"]
    assert any(step["action_id"] == "manual_dispatch" for step in data["steps"])
