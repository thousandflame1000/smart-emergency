"""LINE 裡的 App（LIFF）：身分由 LINE 驗證，功能與聊天室按鈕一致。"""
import pytest
from fastapi.testclient import TestClient

from app.models.checkin import DailyCheckin
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.routers import liff_app
from app.services.form_token import make_token
from app.timeutil import today_tw


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def _open(client, uid):
    assert client.post("/app/api/session", json={"token": make_token(uid)}).status_code == 200


def test_page_carries_the_liff_id(client):
    page = client.get("/app?go=nearby")
    assert page.status_code == 200 and "2011793999-3QbSnXoe" in page.text and "{{LIFF_ID}}" not in page.text


def test_line_id_token_logs_in_and_registers_new_people(db, client, monkeypatch):
    monkeypatch.setattr(liff_app, "verify_id_token", lambda t: {"sub": "U-liff-new", "name": "陳阿姨"} if t == "good" else None)
    assert client.post("/app/api/session", json={"id_token": "forged"}).status_code == 401
    assert client.post("/app/api/session", json={"id_token": "good"}).status_code == 200
    user = db.query(User).filter(User.line_uid == "U-liff-new").one()
    assert user.name == "陳阿姨" and user.roles == ["elderly"]
    assert client.get("/app/api/me").json()["name"] == "陳阿姨"
    assert client.get("/f/api/me").status_code == 200, "同一次登入也要能用既有的網頁功能"


def test_api_needs_a_session(client):
    assert client.get("/app/api/me").status_code == 401


def test_check_in_unwell_and_sos_from_the_app(db, client, line_outbox):
    elder = User(name="王奶奶", roles=["elderly"], line_uid="U-app-elder")
    db.add(elder); db.commit()
    db.add(DailyCheckin(elderly_id=elder.id, date=today_tw(), status="pending")); db.commit()
    _open(client, "U-app-elder")
    assert "收到" in client.post("/app/api/checkin", json={"status": "ok"}).json()["message"]
    assert client.get("/app/api/me").json()["checkin"] == "ok"
    assert client.post("/app/api/checkin", json={"status": "bad"}).status_code == 422
    assert client.post("/app/api/sos", json={}).status_code == 200
    assert client.get("/app/api/me").json()["checkin"] == "help_needed"


def test_locate_then_nearby_points(db, client):
    db.add_all([User(name="居民", roles=["elderly"], line_uid="U-app-loc"),
                ResourcePoint(name="玉里國小", point_type="shelter", lat=23.3368, lng=121.3150, phone="03-888-1234")])
    db.commit()
    _open(client, "U-app-loc")
    assert client.get("/app/api/nearby").json() == {"has_location": False, "points": []}
    assert client.post("/app/api/location", json={"lat": 23.3343, "lng": 121.3177}).status_code == 200
    point = client.get("/app/api/nearby").json()["points"][0]
    assert point["name"] == "玉里國小" and point["tel"] == "tel:038881234" and "destination=23.3368,121.315" in point["navigate"]
    assert client.post("/app/api/location", json={"lat": 999, "lng": 0}).status_code == 422


def test_ask_uses_the_knowledge_base_and_is_rate_limited(db, client, monkeypatch):
    from app.routers import linebot
    from app.services import rag
    db.add(User(name="志工", roles=["volunteer"], line_uid="U-app-ask")); db.commit()
    monkeypatch.setattr(rag, "query", lambda q: {"answer": "撥 119", "sources": ["急救指引"], "has_answer": True})
    _open(client, "U-app-ask")
    for _ in range(linebot.QUESTION_LIMIT):
        assert client.post("/app/api/ask", json={"question": "中風怎麼辦"}).json()["answer"] == "撥 119"
    assert client.post("/app/api/ask", json={"question": "中風怎麼辦"}).status_code == 429


def test_saying_fine_before_the_morning_prompt_still_counts(db, client, line_outbox):
    """8 點前、排程還沒發打卡就先按「我很好」，也要記成今天平安，之後不再重發打卡。"""
    db.add(User(name="早起阿伯", roles=["elderly"], line_uid="U-early")); db.commit()
    _open(client, "U-early")
    client.post("/app/api/checkin", json={"status": "ok"})
    assert client.get("/app/api/me").json()["checkin"] == "ok"
    from tests.test_line_hardening import say
    db.add(User(name="早起阿嬤", roles=["elderly"], line_uid="U-early-line")); db.commit()
    say("U-early-line", "我很好")
    user = db.query(User).filter(User.line_uid == "U-early-line").one()
    assert db.query(DailyCheckin).filter(DailyCheckin.elderly_id == user.id).one().status == "ok"


def test_nearby_marks_official_shelters(db, client, monkeypatch):
    from app.services import nearby
    shelter = nearby.OpenShelter("大安國小", "臺北市大安區", 25.03, 121.54, 200, "02-27000000", True)
    monkeypatch.setattr(nearby, "open_shelters", lambda: (shelter,))
    db.add(User(name="居民", roles=["elderly"], line_uid="U-app-open", lat=25.031, lng=121.541)); db.commit()
    _open(client, "U-app-open")
    point = client.get("/app/api/nearby").json()["points"][0]
    assert point["official"] == "臺北市大安區・內政部公告" and point["capacity"] == "可收容 200 人・可安置長者與身障者"
    assert point["emergency"] and point["tel"] == "tel:0227000000"


def test_during_an_emergency_home_asks_about_this_disaster_not_this_morning(db, client):
    from app.models.config import SystemConfig
    from app.services import checkin as checkin_svc
    from app.services import rollcall
    elder = User(name="阿嬤", roles=["elderly"], line_uid="U-app-roll")
    db.add(elder); db.commit()
    checkin_svc.record_ok(db, elder)  # 早上打過卡
    _open(client, "U-app-roll")
    assert client.get("/app/api/me").json()["safety"] is None, "日常模式沒有點名"
    db.add(SystemConfig(key="mode", value="emergency")); db.commit()
    rollcall.start(db)
    assert client.get("/app/api/me").json()["safety"] == "pending", "早上的打卡不算這次災害的平安"
    assert "已回報平安" in client.post("/app/api/checkin", json={"status": "ok"}).json()["message"]
    assert client.get("/app/api/me").json()["safety"] == "ok"
