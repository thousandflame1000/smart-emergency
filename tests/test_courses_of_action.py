# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.routers import scenario as scenario_router
from app.services import courses_of_action, road_network


def _client():
    app = FastAPI()
    app.include_router(scenario_router.router, prefix="/api/scenario")
    return TestClient(app)


def test_courses_of_action_recommends_resource_surge_for_uncovered_urgent_need(db):
    elder = User(name="COA Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
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

    report = courses_of_action.compare_courses(db)
    course = next(c for c in report["courses"] if c["id"] == "surge_local_resources")

    assert report["baseline"]["urgent_uncovered"] == 1
    assert course["expected_delta"]["urgent_uncovered"] == -1
    assert course["metrics_after"]["uncovered_open"] == 0
    assert course["evidence"]["affected_requests"][0]["id"] == str(need.id)


def test_courses_of_action_is_non_mutating(db):
    elder = User(name="No Mutation Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
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

    before = db.query(CommunityNeed).count()
    courses_of_action.compare_courses(db)
    after = db.query(CommunityNeed).count()

    assert after == before


def test_courses_of_action_recommends_road_restoration_for_sandbox_closure(db):
    road_network.set_edge_status(db, road_network.edge_id("yuli", "changbin"), "closed")

    report = courses_of_action.compare_courses(db)
    course = next(c for c in report["courses"] if c["id"] == "restore_road_capacity")

    assert report["baseline"]["road_network"]["closed_edges"] == 1
    assert course["expected_delta"]["closed_edges"] == -1
    assert road_network.edge_id("yuli", "changbin") in course["evidence"]["changed_edge_ids"]


def test_courses_of_action_endpoint_returns_ranked_courses(db):
    elder = User(name="Endpoint COA Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
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

    res = _client().get("/api/scenario/courses-of-action")
    data = res.json()

    assert res.status_code == 200
    assert data["baseline"]["urgent_uncovered"] == 1
    assert data["courses"][0]["rank_score"] >= data["courses"][-1]["rank_score"]
    assert any(c["id"] == "surge_local_resources" for c in data["courses"])


def test_dry_run_resource_surge_returns_after_state_without_mutation(db):
    elder = User(name="Dry Run Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
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
    before_resources = db.query(CommunityResource).count()

    report = courses_of_action.dry_run_course(db, "surge_local_resources")

    assert report["non_mutating"] is True
    assert report["impact"]["urgent_uncovered_delta"] == -1
    assert report["simulated_after"]["uncovered_open"] == 0
    assert report["request_changes"][0]["after"]["source"] == "virtual_resource"
    assert db.query(CommunityResource).count() == before_resources


def test_dry_run_road_restoration_recomputes_candidate_without_sandbox(db):
    elder = User(name="Road Dry Run Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
    volunteer = User(name="Road Volunteer", roles=["volunteer"], lat=23.2833, lng=121.4667)
    db.add_all([elder, volunteer])
    db.commit()
    need = CommunityNeed(
        requester_id=elder.id,
        need_type="water",
        urgency=1,
        status="open",
        lat=23.3333,
        lng=121.3167,
    )
    resource = CommunityResource(
        owner_id=volunteer.id,
        resource_type="water",
        name="Changbin water",
        lat=23.2833,
        lng=121.4667,
        is_available=True,
    )
    db.add_all([need, resource])
    db.commit()
    road_network.set_edge_status(db, road_network.edge_id("yuli", "changbin"), "closed")

    report = courses_of_action.dry_run_course(db, "restore_road_capacity")

    assert report["non_mutating"] is True
    assert report["impact"]["newly_covered_requests"] == 1
    assert report["request_changes"][0]["request_id"] == str(need.id)
    assert report["request_changes"][0]["delta"]["candidate_added"] is True
    assert report["request_changes"][0]["after"]["source"] == "resource"


def test_dry_run_endpoint_returns_simulated_impact(db):
    elder = User(name="Endpoint Dry Run Elder", roles=["elderly"], lat=23.3333, lng=121.3167)
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

    res = _client().get("/api/scenario/courses-of-action/surge_local_resources/dry-run")
    data = res.json()

    assert res.status_code == 200
    assert data["course_id"] == "surge_local_resources"
    assert data["impact"]["urgent_uncovered_delta"] == -1
    assert data["non_mutating"] is True
