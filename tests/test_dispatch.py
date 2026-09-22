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


def test_preview_candidates_flags_unbound_line_before_confirm(db):
    """曾經是按下「確認派遣」才跳出「志工未綁定 LINE」，管理員白做工
    才知道——候選清單本身就該先標出來。"""
    vol_no_line = User(name="志工無LINE", roles=["volunteer"], lat=24.15, lng=120.68)
    db.add(vol_no_line); db.commit(); db.refresh(vol_no_line)
    res = CommunityResource(owner_id=vol_no_line.id, resource_type="water", name="沒綁LINE的水",
                             lat=24.15, lng=120.68, is_available=True)
    db.add(res); db.commit()
    elder = User(name="長者", roles=["elderly"], lat=24.151, lng=120.681)
    db.add(elder); db.commit(); db.refresh(elder)
    need = CommunityNeed(requester_id=elder.id, need_type="water", address="測試",
                          lat=24.151, lng=120.681, urgency=3)
    db.add(need); db.commit()

    preview = dispatch.preview_candidates(str(need.id), db)
    cand = preview["candidates"][0]
    assert cand["notify_channel"] == "line_unbound"


def test_preview_candidates_flags_bound_line_volunteer(db):
    need_id, _ = _make_scenario(db)
    preview = dispatch.preview_candidates(need_id, db)
    cand = preview["candidates"][0]
    assert cand["notify_channel"] == "line", "志工已綁 LINE 應該直接標示會發任務卡"


def test_auto_dispatch_skip_reason_distinguishes_missing_coordinates(db, monkeypatch):
    """今天實際卡住好幾個小時的根因：「沒座標永遠配不到」跟「有座標
    但太遠/沒相符類型」需要不同的處置方式，回傳理由不能混成同一句。"""
    monkeypatch.setattr(dispatch, "send_task_message", lambda *a, **k: None)
    elder_no_coords = User(name="沒填地址的長者", roles=["elderly"])
    elder_far = User(name="太遠的長者", roles=["elderly"], lat=25.5, lng=121.9)
    db.add_all([elder_no_coords, elder_far]); db.commit()
    need_no_coords = CommunityNeed(requester_id=elder_no_coords.id, need_type="water",
                                    address="沒填座標", urgency=3)
    need_far = CommunityNeed(requester_id=elder_far.id, need_type="water",
                              address="太遠了", lat=25.5, lng=121.9, urgency=1)
    db.add_all([need_no_coords, need_far])
    db.add(SystemConfig(key="mode", value="emergency"))
    db.commit()

    result = dispatch.auto_dispatch()
    reasons = {d["need_id"]: d["reason"] for d in result["details"] if d["result"] == "skipped"}
    assert "缺少座標" in reasons[str(need_no_coords.id)]
    assert "缺少座標" not in reasons.get(str(need_far.id), "")


def test_confirm_dispatch_passes_distance_and_destination_to_task_message(db, monkeypatch):
    """任務卡之前只有地址文字，志工接單前完全不知道要跑多遠、也沒有
    地圖連結——距離跟地圖用的目的地座標要在通知時一併算好傳過去。"""
    need_id, res_id = _make_scenario(db)
    db.close()

    captured = {}
    monkeypatch.setattr(dispatch, "send_task_message",
                         lambda **kw: captured.update(kw))
    dispatch.auto_dispatch()

    db2 = SessionLocal()
    dispatch.confirm_dispatch(need_id, db2)
    db2.close()

    assert captured.get("distance_km") is not None
    assert captured["distance_km"] >= 0
    assert captured.get("dest_lat") == 24.151
    assert captured.get("dest_lng") == 120.681


def test_send_task_message_without_coordinates_has_no_distance(db, monkeypatch):
    """需求或物資缺座標時不該假造距離，維持 None 並讓卡片跳過那一行。"""
    vol = User(name="志工無座標", roles=["volunteer"], line_uid="Uvolnc")
    db.add(vol); db.commit()
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="水", is_available=True)
    db.add(res); db.commit()
    elder = User(name="長者無座標", roles=["elderly"])
    db.add(elder); db.commit()
    need = CommunityNeed(requester_id=elder.id, need_type="water", address="沒填座標", urgency=3,
                          status="suggested", matched_resource_id=res.id)
    db.add(need); db.commit()
    need_id = str(need.id)
    db.close()

    captured = {}
    monkeypatch.setattr(dispatch, "send_task_message", lambda **kw: captured.update(kw))
    db2 = SessionLocal()
    dispatch.confirm_dispatch(need_id, db2)
    db2.close()

    assert captured.get("distance_km") is None
