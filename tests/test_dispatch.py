# -*- coding: utf-8 -*-
"""
驗證「管理員確認關卡」：auto_dispatch 只能產生建議，
真正發 LINE 通知志工必須經過 confirm_dispatch。
對應計畫書「所有建議仍須管理員確認後才會執行」的承諾。
"""
from app.database import SessionLocal
from app.models.user import User
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.config import SystemConfig
from app.models.dispatch_event import DispatchEvent
from app.services import dispatch


def _make_scenario(db):
    vol = User(name="志工小明", roles=["volunteer"], line_uid="Uvol123", lat=24.15, lng=120.68)
    db.add(vol); db.commit(); db.refresh(vol)

    res = CommunityResource(owner_id=vol.id, resource_type="water", name="志工小明提供的飲用水",
                             quantity="20箱", lat=24.15, lng=120.68, is_available=True)
    db.add(res); db.commit(); db.refresh(res)

    elder = User(name="王奶奶", roles=["elderly"], lat=24.151, lng=120.681)
    db.add(elder); db.commit(); db.refresh(elder)

    need = CommunityNeed(requester_id=elder.id, need_type="water", description="需要飲用水",
                          address="測試地址", lat=24.151, lng=120.681, urgency=4)
    db.add(need); db.commit(); db.refresh(need)

    db.add(SystemConfig(key="mode", value="emergency"))
    db.commit()
    return str(need.id), str(res.id)


def test_auto_dispatch_only_suggests_does_not_notify(db, monkeypatch):
    need_id, res_id = _make_scenario(db)
    db.close()

    called = {"sent": False}
    monkeypatch.setattr(dispatch, "send_task_message", lambda *a, **kw: called.update(sent=True))

    result = dispatch.auto_dispatch()
    assert called["sent"] is False, "auto_dispatch 不應該自動發 LINE 通知"
    assert result["suggested"] == 1

    db2 = SessionLocal()
    need = db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    res = db2.query(CommunityResource).filter(CommunityResource.id == res_id).first()
    event = db2.query(DispatchEvent).filter(DispatchEvent.need_id == need_id).first()
    assert need.status == "suggested"
    assert res.is_available is False, "建議階段應先保留物資，避免被同時建議給別人"
    assert event.action == "propose_dispatch"
    assert event.outcome == "suggested"
    assert event.previous_status == "open"
    assert event.new_status == "suggested"
    db2.close()


def test_decline_suggestion_releases_resource(db, monkeypatch):
    need_id, res_id = _make_scenario(db)
    db.close()
    monkeypatch.setattr(dispatch, "send_task_message", lambda *a, **kw: None)
    dispatch.auto_dispatch()

    db2 = SessionLocal()
    out = dispatch.decline_suggestion(need_id, db2)
    assert out["message"].startswith("已否決")
    need = db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    res = db2.query(CommunityResource).filter(CommunityResource.id == res_id).first()
    decline_event = (
        db2.query(DispatchEvent)
        .filter(DispatchEvent.need_id == need_id, DispatchEvent.action == "decline_suggestion")
        .first()
    )
    assert need.status == "open" and need.matched_resource_id is None
    assert res.is_available is True
    assert decline_event is not None
    assert decline_event.previous_status == "suggested"
    assert decline_event.new_status == "open"
    db2.close()


def test_cancel_need_releases_active_resource_and_logs_event(db, monkeypatch):
    need_id, res_id = _make_scenario(db)
    db.close()
    monkeypatch.setattr(dispatch, "send_task_message", lambda *a, **kw: None)
    dispatch.auto_dispatch()

    db2 = SessionLocal()
    out = dispatch.cancel_need(need_id, db2)
    assert out["resource_released"] is True

    need = db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    res = db2.query(CommunityResource).filter(CommunityResource.id == res_id).first()
    cancel_event = (
        db2.query(DispatchEvent)
        .filter(DispatchEvent.need_id == need_id, DispatchEvent.action == "cancel_need")
        .first()
    )
    assert need.status == "cancelled"
    assert need.matched_resource_id is None
    assert res.is_available is True
    assert cancel_event is not None
    assert str(cancel_event.resource_id) == res_id
    assert cancel_event.previous_status == "suggested"
    assert cancel_event.new_status == "cancelled"
    assert cancel_event.outcome == "cancelled"
    db2.close()


def test_confirm_dispatch_sends_line_notification(db, monkeypatch):
    need_id, res_id = _make_scenario(db)
    db.close()

    called = {"sent": False}
    monkeypatch.setattr(dispatch, "send_task_message", lambda *a, **kw: called.update(sent=True))
    dispatch.auto_dispatch()

    db2 = SessionLocal()
    called["sent"] = False
    out = dispatch.confirm_dispatch(need_id, db2)
    assert called["sent"] is True, "confirm_dispatch 才應該真的發送 LINE 通知"
    assert out["volunteer_notified"] is True
    need = db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    confirm_event = (
        db2.query(DispatchEvent)
        .filter(DispatchEvent.need_id == need_id, DispatchEvent.action == "confirm_dispatch")
        .first()
    )
    assert need.status == "matched"
    assert confirm_event is not None
    assert confirm_event.outcome == "matched"
    assert confirm_event.previous_status == "suggested"
    assert confirm_event.new_status == "matched"
    db2.close()


def test_confirm_dispatch_rejects_non_suggested_need(db):
    need_id, _ = _make_scenario(db)
    db.close()
    # 沒先跑 auto_dispatch，need 還是 open，不該能被 confirm
    db2 = SessionLocal()
    out = dispatch.confirm_dispatch(need_id, db2)
    assert "error" in out
    db2.close()
