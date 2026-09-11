# -*- coding: utf-8 -*-
"""
驗證 /api/dashboard/users/{id}/resolve_alerts：批次解決某使用者名下
所有未解決警報。

背景：正式環境真的發生過角色被誤判（LINE 自動註冊預設 elderly），
系統排程連續近一個月每天累積「未回應」警報，修正角色後這些舊警報
還是掛在那裡，需要一次清掉而不是一筆一筆點。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models.user import User
from app.models.alert import Alert
from app.routers import dashboard as dashboard_router


def make_client():
    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api/dashboard")
    return TestClient(app)


def test_resolve_alerts_resolves_only_that_users_sent_alerts(db):
    client = make_client()

    elder1 = User(name="長者甲", roles=["elderly"])
    elder2 = User(name="長者乙", roles=["elderly"])
    db.add_all([elder1, elder2])
    db.commit()

    for _ in range(3):
        db.add(Alert(elderly_id=elder1.id, alert_type="no_response_1h", status="sent"))
    db.add(Alert(elderly_id=elder1.id, alert_type="no_response_3h", status="resolved"))
    db.add(Alert(elderly_id=elder2.id, alert_type="no_response_1h", status="sent"))
    db.commit()

    r = client.post(f"/api/dashboard/users/{elder1.id}/resolve_alerts")
    assert r.status_code == 200
    assert r.json()["resolved"] == 3

    db2 = SessionLocal()
    elder1_sent = db2.query(Alert).filter(Alert.elderly_id == elder1.id, Alert.status == "sent").count()
    elder2_sent = db2.query(Alert).filter(Alert.elderly_id == elder2.id, Alert.status == "sent").count()
    assert elder1_sent == 0, "長者甲的未解決警報都該被清掉"
    assert elder2_sent == 1, "不該動到別人的警報"
    db2.close()


def test_resolve_alerts_returns_zero_when_nothing_to_resolve(db):
    client = make_client()
    elder = User(name="沒有警報的人", roles=["elderly"])
    db.add(elder)
    db.commit()

    r = client.post(f"/api/dashboard/users/{elder.id}/resolve_alerts")
    assert r.status_code == 200
    assert r.json()["resolved"] == 0
