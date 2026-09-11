# -*- coding: utf-8 -*-
"""驗證 /api/dashboard/trends 歷史趨勢端點。"""
from datetime import date, timedelta

from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.user import User
from app.routers import dashboard as dashboard_router


def make_client():
    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api/dashboard")
    return TestClient(app)


def test_trends_returns_requested_number_of_days(db):
    client = make_client()
    r = client.get("/api/dashboard/trends?days=7")
    assert r.status_code == 200
    data = r.json()
    assert len(data["days"]) == 7
    assert data["days"][-1]["date"] == str(date.today())


def test_trends_fills_missing_days_with_zero(db):
    client = make_client()
    r = client.get("/api/dashboard/trends?days=5")
    data = r.json()
    for day in data["days"]:
        assert day["checkin_ok"] == 0
        assert day["alerts"] == 0


def test_trends_aggregates_real_checkin_and_alert_data(db):
    elder = User(name="長者", roles=["elderly"])
    db.add(elder)
    db.commit()

    yesterday = date.today() - timedelta(days=1)
    c1 = DailyCheckin(elderly_id=elder.id, date=yesterday, status="ok")
    c2 = DailyCheckin(elderly_id=elder.id, date=date.today(), status="no_response")
    db.add_all([c1, c2])
    db.commit()
    db.refresh(c2)
    db.add(Alert(elderly_id=elder.id, checkin_id=c2.id, alert_type="no_response_1h", status="sent"))
    db.commit()

    client = make_client()
    r = client.get("/api/dashboard/trends?days=3")
    data = {d["date"]: d for d in r.json()["days"]}

    assert data[str(yesterday)]["checkin_ok"] == 1
    assert data[str(date.today())]["checkin_no_response"] == 1
    assert data[str(date.today())]["alerts"] == 1


def test_trends_buckets_by_taipei_date_not_utc_date(db):
    """
    這是真的踩到的 bug 的回歸測試：Alert.created_at 是
    server_default=func.now()（UTC），但「今天」是台灣時區（UTC+8）
    算出來的日期。凌晨 0 點到早上 8 點這段，UTC 日期還停在前一天，
    如果分桶時直接用 UTC 日期，這些資料會被歸到錯的一天。
    這裡直接塞一筆「UTC 時間是前一天深夜、但台灣時間已經是今天凌晨」
    的警報，驗證它有被歸到正確的台灣日期。
    """
    from datetime import datetime, timedelta

    elder = User(name="長者", roles=["elderly"])
    db.add(elder)
    db.commit()

    checkin = DailyCheckin(elderly_id=elder.id, date=date.today(), status="no_response")
    db.add(checkin)
    db.commit()
    db.refresh(checkin)

    # 模擬「UTC 時間 23:30（仍是前一天），但台灣時間已經是隔天 07:30」
    utc_late_last_night = datetime.combine(date.today(), datetime.min.time()) - timedelta(hours=0, minutes=30)
    alert = Alert(elderly_id=elder.id, checkin_id=checkin.id, alert_type="no_response_1h", status="sent")
    db.add(alert)
    db.commit()
    db.refresh(alert)
    alert.created_at = utc_late_last_night
    db.commit()

    client = make_client()
    r = client.get("/api/dashboard/trends?days=3")
    data = {d["date"]: d for d in r.json()["days"]}

    assert data[str(date.today())]["alerts"] == 1, (
        "UTC 時間雖然還是前一天深夜，換算成台灣時區後已經是今天，"
        "應該要被歸到今天，不是前一天"
    )


def test_trends_days_param_is_clamped(db):
    client = make_client()
    r = client.get("/api/dashboard/trends?days=999")
    assert len(r.json()["days"]) == 90
    r2 = client.get("/api/dashboard/trends?days=0")
    assert len(r2.json()["days"]) == 1
