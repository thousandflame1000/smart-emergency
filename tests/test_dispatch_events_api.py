# -*- coding: utf-8 -*-
import json
from datetime import UTC, datetime, timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.routers import resources as resources_router


def _utcnow_naive():
    return datetime.now(UTC).replace(tzinfo=None)


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(resources_router.router, prefix="/api/resources")
    return TestClient(app)


def _seed_need_and_resource(db):
    elder = User(name="Need owner", roles=["elderly"], lat=24.151, lng=120.681)
    volunteer = User(name="Volunteer", roles=["volunteer"], lat=24.150, lng=120.680)
    db.add_all([elder, volunteer])
    db.commit()

    resource = CommunityResource(
        owner_id=volunteer.id,
        resource_type="water",
        name="Water pack",
        lat=24.150,
        lng=120.680,
        is_available=False,
    )
    db.add(resource)
    db.commit()
    need = CommunityNeed(
        requester_id=elder.id,
        need_type="water",
        urgency=4,
        status="matched",
        matched_resource_id=resource.id,
        lat=24.151,
        lng=120.681,
    )
    db.add(need)
    db.commit()
    return str(need.id), str(resource.id)


def test_need_dispatch_events_endpoint_returns_latest_first(db, client):
    need_id, resource_id = _seed_need_and_resource(db)
    now = _utcnow_naive()
    db.add_all([
        DispatchEvent(
            action="propose_dispatch",
            outcome="suggested",
            need_id=need_id,
            resource_id=resource_id,
            actor_label="system:auto_dispatch",
            previous_status="open",
            new_status="suggested",
            details_json=json.dumps({"score": 72.5, "dist_km": 0.2}),
            created_at=now - timedelta(minutes=2),
        ),
        DispatchEvent(
            action="confirm_dispatch",
            outcome="matched",
            need_id=need_id,
            resource_id=resource_id,
            actor_label="manager",
            previous_status="suggested",
            new_status="matched",
            details_json=json.dumps({"volunteer_notified": True}),
            created_at=now,
        ),
    ])
    db.commit()
    db.close()

    res = client.get(f"/api/resources/needs/{need_id}/events")
    data = res.json()

    assert res.status_code == 200
    assert [e["action"] for e in data] == ["confirm_dispatch", "propose_dispatch"]
    assert data[0]["resource_name"] == "Water pack"
    assert data[0]["previous_status"] == "suggested"
    assert data[0]["new_status"] == "matched"
    assert data[0]["details"]["volunteer_notified"] is True
    assert data[1]["details"]["score"] == 72.5


def test_need_dispatch_events_endpoint_returns_empty_list(db, client):
    need_id, _ = _seed_need_and_resource(db)
    db.close()

    res = client.get(f"/api/resources/needs/{need_id}/events")

    assert res.status_code == 200
    assert res.json() == []


def test_cancel_need_endpoint_releases_resource_and_lists_event(db, client):
    need_id, resource_id = _seed_need_and_resource(db)
    db.close()

    res = client.put(f"/api/resources/needs/{need_id}?status=cancelled")
    assert res.status_code == 200
    assert res.json()["resource_released"] is True

    db2 = SessionLocal()
    need = db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    resource = db2.query(CommunityResource).filter(CommunityResource.id == resource_id).first()
    assert need.status == "cancelled"
    assert need.matched_resource_id is None
    assert resource.is_available is True
    db2.close()

    events_res = client.get(f"/api/resources/needs/{need_id}/events")
    events = events_res.json()

    assert events_res.status_code == 200
    assert events[0]["action"] == "cancel_need"
    assert events[0]["resource_id"] == resource_id
    assert events[0]["previous_status"] == "matched"
    assert events[0]["new_status"] == "cancelled"
    assert events[0]["details"]["resource_released"] is True
