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


def test_nearby_tab_lists_the_nearest_aed_first(db, client, monkeypatch):
    from app.services import aed
    box = aed.Aed("活動中心", "一樓", 25.031, 121.541, "00:00-23:59", "00:00-23:59", "00:00-23:59", "", None)
    monkeypatch.setattr(aed, "aeds", lambda: (box,))
    db.add(User(name="居民", roles=["elderly"], line_uid="U-app-aed", lat=25.03, lng=121.54)); db.commit()
    _open(client, "U-app-aed")
    data = client.get("/app/api/nearby").json()
    assert data["aeds"][0]["name"] == "活動中心" and data["aeds"][0]["open"] is True
    assert "destination=25.031,121.541" in data["aeds"][0]["navigate"]


def test_responder_handles_the_sos_from_the_app(db, client, line_outbox):
    from app.models.need import CommunityNeed
    from app.routers import linebot as lb
    from app.services import sos
    elder = User(name="阿公", roles=["elderly"], line_uid="U-app-elder", phone="0911222333", lat=23.665, lng=121.418)
    helper = User(name="志工", roles=["volunteer"], line_uid="U-app-helper", lat=23.666, lng=121.418)
    db.add_all([elder, helper]); db.commit()
    lb._trigger_sos(elder, db)
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    _open(client, "U-app-helper")
    assert client.get("/app/api/me").json()["sos_tasks"] == []
    sos.take(db, str(need.id), helper, via="測試")
    task = client.get("/app/api/me").json()["sos_tasks"][0]
    assert task["name"] == "阿公" and task["tel"] == "tel:0911222333" and task["arrived"] is False
    assert client.post(f"/app/api/sos/{need.id}/arrived", json={}).status_code == 200
    assert client.get("/app/api/me").json()["sos_tasks"][0]["arrived"] is True
    assert client.post(f"/app/api/sos/{need.id}/done", json={}).status_code == 200
    assert client.get("/app/api/me").json()["sos_tasks"] == []
    assert client.post(f"/app/api/sos/{need.id}/done", json={}).status_code == 200, "重複按不出錯"


def test_only_the_responder_can_act_on_the_sos_in_the_app(db, client, line_outbox):
    from app.models.need import CommunityNeed
    from app.routers import linebot as lb
    elder = User(name="阿公", roles=["elderly"], line_uid="U-app-e2", lat=23.665, lng=121.418)
    other = User(name="別人", roles=["volunteer"], line_uid="U-app-other")
    db.add_all([elder, other]); db.commit()
    lb._trigger_sos(elder, db)
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    _open(client, "U-app-other")
    assert client.post(f"/app/api/sos/{need.id}/done", json={}).status_code == 409


def test_elder_can_cancel_their_sos_in_the_app(db, client, line_outbox):
    from app.models.need import CommunityNeed
    db.add(User(name="阿嬤", roles=["elderly"], line_uid="U-app-oops", lat=23.66, lng=121.42)); db.commit()
    _open(client, "U-app-oops")
    assert client.get("/app/api/me").json()["my_sos"] is False
    assert client.post("/app/api/sos", json={}).status_code == 200
    assert client.get("/app/api/me").json()["my_sos"] is True
    assert "已取消求救" in client.post("/app/api/sos/cancel", json={}).json()["message"]
    assert db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one().status == "cancelled"
    assert client.get("/app/api/me").json()["my_sos"] is False
    assert "沒有進行中的求救" in client.post("/app/api/sos/cancel", json={}).json()["message"]
