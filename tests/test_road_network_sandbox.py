# -*- coding: utf-8 -*-
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import road_network as road_router
from app.services import dispatch
from app.services import road_network as rn


def _client():
    app = FastAPI()
    app.include_router(road_router.router, prefix="/api/road-network")
    return TestClient(app)


def test_sandbox_slow_edge_changes_road_distance(db):
    a = rn.NODES["ruisui"]
    b = rn.NODES["yuli"]
    base = rn.road_distance_km(*a, *b)

    rn.set_edge_status(db, rn.edge_id("ruisui", "yuli"), "slow", multiplier=3.0)
    changed = rn.road_distance_km(*a, *b, db=db)

    assert base is not None
    assert changed is not None
    assert changed > base * 2.5


def test_dispatch_distance_uses_sandbox_overlay(db):
    a = rn.NODES["ruisui"]
    b = rn.NODES["yuli"]
    base = dispatch._distance_km(*a, *b, db)

    rn.set_edge_status(db, rn.edge_id("ruisui", "yuli"), "slow", multiplier=3.0)
    changed = dispatch._distance_km(*a, *b, db)

    assert changed > base * 2.5


def test_sandbox_closed_connector_forces_longer_route(db):
    base = rn.route_between_nodes(db, "chenggong", "yuli")

    rn.set_edge_status(db, rn.edge_id("yuli", "changbin"), "closed")
    closed = rn.route_between_nodes(db, "chenggong", "yuli")

    assert base["reachable"] is True
    assert closed["reachable"] is True
    assert closed["distance_km"] > base["distance_km"]
    assert "changbin" in base["path"]
    assert closed["path"] != base["path"]


def test_road_network_sandbox_api_can_add_close_and_reset(db):
    client = _client()

    res = client.get("/api/road-network/sandbox")
    assert res.status_code == 200
    assert res.json()["metrics"]["nodes"] >= len(rn.NODES)

    node_res = client.post(
        "/api/road-network/sandbox/nodes",
        json={"id": "field_hq", "lat": 23.36, "lng": 121.34, "label": "Field HQ"},
    )
    assert node_res.status_code == 200
    assert any(n["id"] == "field_hq" for n in node_res.json()["nodes"])

    edge_res = client.post(
        "/api/road-network/sandbox/edges",
        json={"a": "field_hq", "b": "yuli", "status": "normal"},
    )
    assert edge_res.status_code == 200
    eid = rn.edge_id("field_hq", "yuli")
    assert any(e["id"] == eid for e in edge_res.json()["edges"])

    close_res = client.put(
        f"/api/road-network/sandbox/edges/{eid}",
        json={"status": "closed"},
    )
    assert close_res.status_code == 200
    assert next(e for e in close_res.json()["edges"] if e["id"] == eid)["status"] == "closed"

    reset_res = client.post("/api/road-network/sandbox/reset")
    assert reset_res.status_code == 200
    assert all(n["id"] != "field_hq" for n in reset_res.json()["nodes"])
