# -*- coding: utf-8 -*-
"""
驗證資源點 current_load（目前使用人數）的報到/離開端點。
之前這個欄位只有 model 定義，整個專案沒有任何地方寫入。
"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.resource_point import ResourcePoint
from app.routers import resources as res_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(res_router.router, prefix="/api/resources")
    return TestClient(app)


def _seed_point(db, capacity=5):
    pt = ResourcePoint(name="測試避難所", point_type="shelter", capacity=capacity, current_load=0)
    db.add(pt); db.commit(); db.refresh(pt)
    return str(pt.id)


def test_checkin_increments_load(db, client):
    pt_id = _seed_point(db)
    db.close()
    for _ in range(3):
        r = client.post(f"/api/resources/points/{pt_id}/checkin")
    assert r.json()["current_load"] == 3


def test_checkout_decrements_load(db, client):
    pt_id = _seed_point(db)
    db.close()
    client.post(f"/api/resources/points/{pt_id}/checkin?count=3")
    r = client.post(f"/api/resources/points/{pt_id}/checkout")
    assert r.json()["current_load"] == 2


def test_checkout_never_goes_negative(db, client):
    pt_id = _seed_point(db)
    db.close()
    r = client.post(f"/api/resources/points/{pt_id}/checkout?count=10")
    assert r.json()["current_load"] == 0


def test_put_can_set_current_load_directly(db, client):
    pt_id = _seed_point(db)
    db.close()
    r = client.put(f"/api/resources/points/{pt_id}?current_load=4")
    assert r.status_code == 200
    pts = client.get("/api/resources/points?active_only=false").json()
    pt = next(p for p in pts if p["id"] == pt_id)
    assert pt["current_load"] == 4


def test_put_clamps_negative_current_load_to_zero(db, client):
    pt_id = _seed_point(db)
    db.close()
    client.put(f"/api/resources/points/{pt_id}?current_load=-5")
    pts = client.get("/api/resources/points?active_only=false").json()
    pt = next(p for p in pts if p["id"] == pt_id)
    assert pt["current_load"] == 0
