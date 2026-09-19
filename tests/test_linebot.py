# -*- coding: utf-8 -*-
"""
驗證 LINE Bot 文字/postback 處理邏輯：
1. task_decline 應正確釋放物資（曾經有 bug：先清空欄位才檢查，物資永遠不會恢復可用）
2. 「需要幫忙」SOS 應自動建立 CommunityNeed 進入派遣佇列
3. 家屬代理登記長者（新增長者 指令）
"""
from app.database import SessionLocal
from app.models.dispatch_event import DispatchEvent
from app.models.user import User
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.care_relation import CareRelation
from app.routers import linebot as lb


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


class _FakePostback:
    def __init__(self, data):
        self.data = data


class _FakePostbackEvent:
    def __init__(self, data, uid, reply_token="tok"):
        self.postback = _FakePostback(data)
        self.source = _FakeSource(uid)
        self.reply_token = reply_token


def test_task_decline_releases_resource(db, monkeypatch):
    monkeypatch.setattr(lb, "reply_text", lambda token, text: None)

    vol = User(name="志工阿強", roles=["volunteer"], line_uid="Uvol2")
    db.add(vol); db.commit(); db.refresh(vol)

    res = CommunityResource(owner_id=vol.id, resource_type="food", name="測試物資", is_available=False)
    db.add(res); db.commit(); db.refresh(res)

    elder = User(name="陳阿嬤", roles=["elderly"])
    db.add(elder); db.commit(); db.refresh(elder)

    need = CommunityNeed(requester_id=elder.id, need_type="food", status="matched",
                          matched_resource_id=res.id, urgency=3)
    db.add(need); db.commit(); db.refresh(need)
    need_id, res_id, vol_id = str(need.id), str(res.id), str(vol.id)
    db.close()

    lb.handle_postback(_FakePostbackEvent(f"action=task_decline&need_id={need_id}", "Uvol2"))

    db2 = SessionLocal()
    r = db2.query(CommunityResource).filter(CommunityResource.id == res_id).first()
    n = db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    event = (
        db2.query(DispatchEvent)
        .filter(DispatchEvent.need_id == need_id, DispatchEvent.action == "task_decline")
        .first()
    )
    assert r.is_available is True, "拒絕任務後物資應恢復可用"
    assert n.status == "open" and n.matched_resource_id is None
    assert event is not None
    assert str(event.actor_id) == vol_id
    assert str(event.resource_id) == res_id
    assert event.previous_status == "matched"
    assert event.new_status == "open"
    assert event.outcome == "declined"
    db2.close()


def test_task_delivered_marks_fulfilled_and_logs_event(db, monkeypatch):
    monkeypatch.setattr(lb, "reply_text", lambda token, text: None)

    vol = User(name="Volunteer", roles=["volunteer"], line_uid="Uvol3")
    db.add(vol); db.commit(); db.refresh(vol)

    res = CommunityResource(owner_id=vol.id, resource_type="water", name="Water pack", is_available=False)
    db.add(res); db.commit(); db.refresh(res)

    elder = User(name="Elder", roles=["elderly"])
    db.add(elder); db.commit(); db.refresh(elder)

    need = CommunityNeed(
        requester_id=elder.id,
        need_type="water",
        status="matched",
        matched_resource_id=res.id,
        urgency=3,
    )
    db.add(need); db.commit(); db.refresh(need)
    need_id, res_id, vol_id = str(need.id), str(res.id), str(vol.id)
    db.close()

    lb.handle_postback(_FakePostbackEvent(f"action=task_delivered&need_id={need_id}", "Uvol3"))

    db2 = SessionLocal()
    n = db2.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    r = db2.query(CommunityResource).filter(CommunityResource.id == res_id).first()
    event = (
        db2.query(DispatchEvent)
        .filter(DispatchEvent.need_id == need_id, DispatchEvent.action == "task_delivered")
        .first()
    )
    assert n.status == "fulfilled"
    assert str(n.matched_resource_id) == res_id
    assert r.is_available is False
    assert event is not None
    assert str(event.actor_id) == vol_id
    assert str(event.resource_id) == res_id
    assert event.previous_status == "matched"
    assert event.new_status == "fulfilled"
    assert event.outcome == "fulfilled"
    db2.close()


def test_sos_keyword_only_asks_for_confirmation_first(db, monkeypatch):
    """「需要幫忙」「救命」「緊急」在自由文字裡太容易誤觸（日常聊天講
    到「這件事很緊急」就會觸發），文字關鍵字本身只能跳確認卡，不能
    直接建立需求或發警報——真正觸發要靠 confirm_sos postback。"""
    confirmations = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: None)
    monkeypatch.setattr("app.services.line_notify.reply_sos_confirmation",
                         lambda token: confirmations.append(token))

    elder = User(name="陳阿嬤", roles=["elderly"], line_uid="Uelder1", address="測試地址")
    db.add(elder); db.commit(); db.refresh(elder)
    elder_id = str(elder.id)
    db.close()

    for phrase in ("需要幫忙", "救命", "這件事很緊急啦"):
        lb.handle_text(_FakeEvent(phrase, "Uelder1"))

    assert len(confirmations) == 3, "每次講到關鍵字都應該跳確認卡"
    db2 = SessionLocal()
    assert db2.query(CommunityNeed).filter(CommunityNeed.requester_id == elder_id).count() == 0, \
        "文字關鍵字本身不該直接建立需求，只能先確認"
    db2.close()


def test_confirm_sos_postback_creates_dispatch_need(db, monkeypatch):
    monkeypatch.setattr(lb, "reply_text", lambda token, text: None)

    elder = User(name="陳阿嬤", roles=["elderly"], line_uid="Uelder1", address="測試地址")
    db.add(elder); db.commit(); db.refresh(elder)
    elder_id = str(elder.id)
    db.close()

    lb.handle_postback(_FakePostbackEvent("action=confirm_sos", "Uelder1"))

    db2 = SessionLocal()
    need = db2.query(CommunityNeed).filter(
        CommunityNeed.requester_id == elder_id,
        CommunityNeed.description == "LINE 一鍵求助（需要幫忙）",
    ).first()
    assert need is not None, "確認後應該建立 CommunityNeed，進入派遣佇列"
    assert need.urgency == 5
    assert need.status == "open"
    db2.close()


def test_dismiss_sos_postback_creates_nothing(db, monkeypatch):
    monkeypatch.setattr(lb, "reply_text", lambda token, text: None)
    elder = User(name="陳阿伯", roles=["elderly"], line_uid="Uelder2", address="測試地址")
    db.add(elder); db.commit(); db.refresh(elder)
    elder_id = str(elder.id)
    db.close()

    lb.handle_postback(_FakePostbackEvent("action=dismiss_sos", "Uelder2"))

    db2 = SessionLocal()
    assert db2.query(CommunityNeed).filter(CommunityNeed.requester_id == elder_id).count() == 0
    db2.close()


def test_confirm_sos_does_not_duplicate_need_on_repeat(db, monkeypatch):
    monkeypatch.setattr(lb, "reply_text", lambda token, text: None)

    elder = User(name="陳阿嬤", roles=["elderly"], line_uid="Uelder1", address="測試地址")
    db.add(elder); db.commit(); db.refresh(elder)
    elder_id = str(elder.id)
    db.close()

    lb.handle_postback(_FakePostbackEvent("action=confirm_sos", "Uelder1"))
    lb.handle_postback(_FakePostbackEvent("action=confirm_sos", "Uelder1"))

    db2 = SessionLocal()
    count = db2.query(CommunityNeed).filter(
        CommunityNeed.requester_id == elder_id,
        CommunityNeed.description == "LINE 一鍵求助（需要幫忙）",
    ).count()
    assert count == 1, "重複確認不應該產生重複的派遣需求"
    db2.close()


def test_family_can_register_elderly(db, monkeypatch):
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))

    family = User(name="王小華", roles=["family"], line_uid="Ufamily1")
    db.add(family); db.commit(); db.refresh(family)
    family_id = str(family.id)
    db.close()

    lb.handle_text(_FakeEvent("新增長者 王奶奶 台中市南區崇倫街88號", "Ufamily1"))
    assert "已建立長者資料" in replies[-1]

    db2 = SessionLocal()
    elder = db2.query(User).filter(User.name == "王奶奶", User.roles.contains(["elderly"])).first()
    assert elder is not None
    assert elder.address == "台中市南區崇倫街88號"
    rel = db2.query(CareRelation).filter(
        CareRelation.elderly_id == elder.id,
        CareRelation.contact_id == family_id,
    ).first()
    assert rel is not None and rel.relation == "家屬代理登記"
    db2.close()

    # 重複登記不應該產生重複資料
    lb.handle_text(_FakeEvent("新增長者 王奶奶 台中市南區崇倫街88號", "Ufamily1"))
    assert "已找到長者資料" in replies[-1]

    db3 = SessionLocal()
    assert db3.query(User).filter(User.name == "王奶奶", User.roles.contains(["elderly"])).count() == 1
    assert db3.query(CareRelation).filter(CareRelation.contact_id == family_id).count() == 1
    db3.close()


def test_elderly_cannot_register_others(db, monkeypatch):
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))

    plain_elder = User(name="陳阿伯", roles=["elderly"], line_uid="Uelder99")
    db.add(plain_elder); db.commit()
    db.close()

    lb.handle_text(_FakeEvent("新增長者 測試長者 測試地址", "Uelder99"))

    db2 = SessionLocal()
    leaked = db2.query(User).filter(User.name == "測試長者").first()
    db2.close()
    assert leaked is None, "一般長者角色不應該能代辦新增長者"


def test_duplicate_need_reply_is_honest_not_a_repeat_of_success(db, monkeypatch):
    """曾經無論是否已有同類型待處理需求，回覆文字都一樣「已登記您的
    需求」，使用者完全分不出這次有沒有真的送出新的一筆。"""
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))
    elder = User(name="林阿姨", roles=["elderly"], line_uid="Uelder3", address="測試地址", lat=24.15, lng=120.68)
    db.add(elder); db.commit(); db.refresh(elder)
    elder_id = str(elder.id)
    db.close()

    lb.handle_text(_FakeEvent("需要水", "Uelder3"))
    assert "已登記您的需求" in replies[-1]

    lb.handle_text(_FakeEvent("缺水啦", "Uelder3"))
    assert "已登記您的需求" not in replies[-1], "第二次應該誠實告知已經在處理，不是重新送出一筆"
    assert "稍早已提出" in replies[-1]

    db2 = SessionLocal()
    count = db2.query(CommunityNeed).filter(
        CommunityNeed.requester_id == elder_id, CommunityNeed.need_type == "water",
    ).count()
    assert count == 1, "第二次不應該真的建立第二筆需求"
    db2.close()


def test_my_needs_command_shows_status(db, monkeypatch):
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))
    elder = User(name="周伯伯", roles=["elderly"], line_uid="Uelder4", address="測試地址")
    db.add(elder); db.commit(); db.refresh(elder)
    db.close()

    lb.handle_text(_FakeEvent("我的需求", "Uelder4"))
    assert "沒有提出過的需求" in replies[-1]

    lb.handle_text(_FakeEvent("需要食物", "Uelder4"))
    lb.handle_text(_FakeEvent("我的需求", "Uelder4"))
    assert "食物" in replies[-1] and "待媒合" in replies[-1]


class _FakeLocationMsg:
    def __init__(self, latitude, longitude, address=None):
        self.latitude = latitude
        self.longitude = longitude
        self.address = address


class _FakeLocationEvent:
    def __init__(self, latitude, longitude, uid, address=None, reply_token="tok"):
        self.message = _FakeLocationMsg(latitude, longitude, address)
        self.source = _FakeSource(uid)
        self.reply_token = reply_token


def test_share_location_updates_user_coordinates(db, monkeypatch):
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))
    resident = User(name="陳小華", roles=["elderly"], line_uid="Uloc1")
    db.add(resident); db.commit(); db.refresh(resident)
    resident_id = str(resident.id)
    db.close()

    lb.handle_location(_FakeLocationEvent(24.151, 120.681, "Uloc1", address="台中市南區崇倫街88號"))
    assert "已更新您的位置" in replies[-1]

    db2 = SessionLocal()
    updated = db2.query(User).filter(User.id == resident_id).first()
    assert updated.lat == 24.151 and updated.lng == 120.681
    assert updated.address == "台中市南區崇倫街88號"
    db2.close()


def test_share_location_backfills_own_open_needs_missing_coordinates(db, monkeypatch):
    """今天實際卡住好幾小時的根因：沒座標的需求永遠配不到——分享位置
    時應該順手把自己名下沒座標的待處理需求一起補上。"""
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))
    resident = User(name="李小美", roles=["elderly"], line_uid="Uloc2")
    db.add(resident); db.commit(); db.refresh(resident)
    resident_id = str(resident.id)
    stuck_need = CommunityNeed(requester_id=resident.id, need_type="water",
                                description="需要水", address="沒填座標", urgency=3, status="open")
    other_need = CommunityNeed(requester_id=resident.id, need_type="food",
                                description="需要食物", lat=1.0, lng=1.0, urgency=3, status="open")
    db.add_all([stuck_need, other_need]); db.commit()
    stuck_id, other_id = str(stuck_need.id), str(other_need.id)
    db.close()

    lb.handle_location(_FakeLocationEvent(24.15, 120.68, "Uloc2"))
    assert "補上 1 筆" in replies[-1]

    db2 = SessionLocal()
    fixed = db2.query(CommunityNeed).filter(CommunityNeed.id == stuck_id).first()
    untouched = db2.query(CommunityNeed).filter(CommunityNeed.id == other_id).first()
    assert fixed.lat == 24.15 and fixed.lng == 120.68
    assert untouched.lat == 1.0, "已經有座標的需求不該被位置分享覆蓋"
    db2.close()


def test_share_location_without_prior_message_registers_and_saves(db, monkeypatch):
    """之前要先傳一句話才能分享位置；現在直接自動註冊並存下座標。"""
    replies = []
    monkeypatch.setattr(lb, "reply_text", lambda token, text: replies.append(text))
    lb.handle_location(_FakeLocationEvent(24.15, 120.68, "UlocUnknown"))
    assert "已更新您的位置" in replies[-1]
    db2 = SessionLocal()
    saved = db2.query(User).filter(User.line_uid == "UlocUnknown").first()
    assert saved is not None and saved.lat == 24.15 and saved.lng == 120.68
    db2.close()
