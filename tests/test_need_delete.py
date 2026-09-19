# -*- coding: utf-8 -*-
"""DELETE /api/resources/needs/{id}：只讓還沒進入真實派遣流程的需求能
永久刪除。取消（PUT status=cancelled）之前是唯一的收尾動作，垃圾測試
資料會一直留在清單裡（今天實際遇到的「急需用水」/「水分」那兩筆），
沒有真正移除的辦法。"""
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.routers import resources as resources_router


@pytest.fixture
def client():
    app = FastAPI()
    app.include_router(resources_router.router, prefix="/api/resources")
    return TestClient(app)


def test_delete_open_need_removes_it(db, client):
    elder = User(name="長者", roles=["elderly"])
    db.add(elder); db.commit()
    need = CommunityNeed(requester_id=elder.id, need_type="water", address="1", urgency=3, status="open")
    db.add(need); db.commit()
    need_id = str(need.id)

    res = client.delete(f"/api/resources/needs/{need_id}")
    assert res.status_code == 200

    db2 = SessionLocal()
    assert db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first() is None
    db2.close()


def test_delete_cancelled_need_removes_it(db, client):
    elder = User(name="長者", roles=["elderly"])
    db.add(elder); db.commit()
    need = CommunityNeed(requester_id=elder.id, need_type="water", urgency=3, status="cancelled")
    db.add(need); db.commit()
    need_id = str(need.id)

    res = client.delete(f"/api/resources/needs/{need_id}")
    assert res.status_code == 200
    db2 = SessionLocal()
    assert db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first() is None
    db2.close()


@pytest.mark.parametrize("status", ["suggested", "matched", "fulfilled"])
def test_delete_rejects_needs_already_in_dispatch_flow(db, client, status):
    elder = User(name="長者", roles=["elderly"])
    vol = User(name="志工", roles=["volunteer"])
    db.add_all([elder, vol]); db.commit()
    res_obj = CommunityResource(owner_id=vol.id, resource_type="water", name="水", is_available=False)
    db.add(res_obj); db.commit()
    need = CommunityNeed(requester_id=elder.id, need_type="water", urgency=3, status=status,
                          matched_resource_id=res_obj.id)
    db.add(need); db.commit()
    need_id = str(need.id)

    res = client.delete(f"/api/resources/needs/{need_id}")
    assert res.status_code == 409

    db2 = SessionLocal()
    assert db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first() is not None, \
        "已進入派遣流程的需求不該被真的刪掉"
    db2.close()


def test_delete_missing_need_404s(client):
    res = client.delete("/api/resources/needs/00000000-0000-0000-0000-000000000000")
    assert res.status_code == 404
