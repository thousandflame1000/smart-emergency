# -*- coding: utf-8 -*-
"""志工自助申請：LINE 端送出申請、後台審核核准/拒絕，兩種結果都推播通知。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models.user import User
from app.models.volunteer_application import VolunteerApplication
from app.routers import linebot as lb
from app.routers import dashboard as dashboard_router
from app.services import volunteer_application as va


class _FakeMsg:
    def __init__(self, text):
        self.text = text


class _FakeSource:
    def __init__(self, uid):
        self.user_id = uid


class _FakeEvent:
    def __init__(self, text, uid, reply_token="tok"):
        self.message = _FakeMsg(text)
        self.source = _FakeSource(uid)
        self.reply_token = reply_token


def _client():
    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api/dashboard")
    return TestClient(app)


def test_line_submit_creates_pending_application(db, monkeypatch):
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))

    resident = User(name="陳小美", roles=["elderly"], line_uid="Uapply1")
    db.add(resident); db.commit(); db.close()

    lb.handle_text(_FakeEvent("志工申請 陳小美 0912345678 台中市南區", "Uapply1"))
    assert "已收到您的志工申請" in replies[-1]

    db2 = SessionLocal()
    app_row = db2.query(VolunteerApplication).filter(VolunteerApplication.line_uid == "Uapply1").first()
    assert app_row is not None
    assert app_row.status == "pending"
    assert app_row.phone == "0912345678"
    assert app_row.service_area == "台中市南區"
    assert app_row.applicant_id is not None
    db2.close()


def test_line_missing_name_shows_usage_and_creates_nothing(db, monkeypatch):
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))
    resident = User(name="訪客", roles=["elderly"], line_uid="Uapply2")
    db.add(resident); db.commit(); db.close()

    lb.handle_text(_FakeEvent("志工申請", "Uapply2"))
    assert "請用以下格式" in replies[-1]

    db2 = SessionLocal()
    assert db2.query(VolunteerApplication).count() == 0
    db2.close()


def test_line_existing_volunteer_cannot_reapply(db, monkeypatch):
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))
    vol = User(name="張志工", roles=["volunteer"], line_uid="Uapply3")
    db.add(vol); db.commit(); db.close()

    lb.handle_text(_FakeEvent("志工申請 張志工 0900000000", "Uapply3"))
    assert "已經是志工" in replies[-1]

    db2 = SessionLocal()
    assert db2.query(VolunteerApplication).count() == 0
    db2.close()


def test_line_repeated_submit_while_pending_does_not_duplicate(db, monkeypatch):
    monkeypatch.setattr(lb, "reply_text", lambda token, text: None)
    resident = User(name="李四", roles=["elderly"], line_uid="Uapply4")
    db.add(resident); db.commit(); db.close()

    lb.handle_text(_FakeEvent("志工申請 李四 0911111111", "Uapply4"))
    lb.handle_text(_FakeEvent("志工申請 李四 0911111111", "Uapply4"))

    db2 = SessionLocal()
    assert db2.query(VolunteerApplication).filter(VolunteerApplication.line_uid == "Uapply4").count() == 1
    db2.close()


def test_approve_adds_volunteer_role_and_keeps_existing_roles(db, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.services.line_notify.send_text",
        lambda uid, text: sent.append((uid, text)),
    )
    resident = User(name="王五", roles=["elderly"], line_uid="Uapply5")
    db.add(resident); db.commit(); db.refresh(resident)
    application = va.submit(db, line_uid="Uapply5", name="王五", phone="0922222222", service_area="台中")

    result = va.decide(db, str(application.id), decision="approve", reviewer_id=None, note="現場確認過")
    assert result["status"] == "approved"
    assert result["volunteer_notified"] is True
    assert sent and sent[0][0] == "Uapply5"
    assert "核准" in sent[0][1]

    db2 = SessionLocal()
    updated = db2.query(User).filter(User.line_uid == "Uapply5").first()
    assert set(updated.roles) == {"elderly", "volunteer"}
    row = db2.query(VolunteerApplication).filter(VolunteerApplication.id == application.id).first()
    assert row.status == "approved" and row.reviewed_at is not None
    db2.close()


def test_reject_does_not_touch_roles(db, monkeypatch):
    sent = []
    monkeypatch.setattr(
        "app.services.line_notify.send_text",
        lambda uid, text: sent.append((uid, text)),
    )
    resident = User(name="趙六", roles=["elderly"], line_uid="Uapply6")
    db.add(resident); db.commit()
    application = va.submit(db, line_uid="Uapply6", name="趙六", phone=None, service_area=None)

    result = va.decide(db, str(application.id), decision="reject", note="無法查核身份")
    assert result["status"] == "rejected"
    assert sent and "未能通過" in sent[0][1]

    db2 = SessionLocal()
    unchanged = db2.query(User).filter(User.line_uid == "Uapply6").first()
    assert unchanged.roles == ["elderly"]
    db2.close()


def test_decide_twice_conflicts(db, monkeypatch):
    monkeypatch.setattr("app.services.line_notify.send_text", lambda uid, text: None)
    resident = User(name="孫七", roles=["elderly"], line_uid="Uapply7")
    db.add(resident); db.commit()
    application = va.submit(db, line_uid="Uapply7", name="孫七", phone=None, service_area=None)
    va.decide(db, str(application.id), decision="approve")

    from fastapi import HTTPException
    try:
        va.decide(db, str(application.id), decision="approve")
        assert False, "second decision should be rejected"
    except HTTPException as exc:
        assert exc.status_code == 409


def test_approve_creates_user_when_applicant_never_registered(db, monkeypatch):
    """志工申請格式不要求申請人已經有 User row（理論上不會發生，因為
    要先傳過訊息才會走到這段 handler），但服務層本身要能處理這個邊界
    情況，不能假設 applicant 一定存在。"""
    monkeypatch.setattr("app.services.line_notify.send_text", lambda uid, text: None)
    application = va.submit(db, line_uid="Uapply8", name="全新志工", phone="0933333333", service_area=None)
    result = va.decide(db, str(application.id), decision="approve")
    assert result["status"] == "approved"

    db2 = SessionLocal()
    created = db2.query(User).filter(User.line_uid == "Uapply8").first()
    assert created is not None and "volunteer" in created.roles
    db2.close()


def test_admin_api_lists_and_decides(db, monkeypatch):
    monkeypatch.setattr("app.services.line_notify.send_text", lambda uid, text: None)
    resident = User(name="周八", roles=["elderly"], line_uid="Uapply9")
    db.add(resident); db.commit(); db.close()
    client = _client()

    dbx = SessionLocal()
    va.submit(dbx, line_uid="Uapply9", name="周八", phone="0944444444", service_area="台中市北區")
    dbx.close()

    pending = client.get("/api/dashboard/volunteer-applications?status=pending").json()
    assert len(pending) == 1 and pending[0]["name"] == "周八"

    res = client.post(f"/api/dashboard/volunteer-applications/{pending[0]['id']}/decision",
                       params={"decision": "approve", "note": "電話確認過"})
    assert res.status_code == 200
    assert res.json()["status"] == "approved"

    still_pending = client.get("/api/dashboard/volunteer-applications?status=pending").json()
    assert still_pending == []
    approved = client.get("/api/dashboard/volunteer-applications?status=approved").json()
    assert len(approved) == 1
