# -*- coding: utf-8 -*-
"""端到端抓包後的修正回歸測試。

每一個測試都對應一個實際跑過整條流程（LINE webhook -> 資料庫 -> 後台）才發現的問題，
不是憑空想像的邊界。"""
from datetime import timedelta

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.database import SessionLocal
from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.routers import dashboard as dashboard_router
from app.routers import linebot as lb
from app.routers import resources as resources_router
from app.services import dispatch
from app.timeutil import now_utc, today_tw


# ── 測試用假事件 ─────────────────────────────────────────────
class _Msg:
    def __init__(self, text=None, latitude=None, longitude=None, address=None):
        self.text = text; self.latitude = latitude; self.longitude = longitude; self.address = address


class _Src:
    def __init__(self, uid): self.user_id = uid


class _Ev:
    def __init__(self, uid, text=None, data=None, msg=None):
        self.source = _Src(uid); self.reply_token = "tok"
        self.message = msg or (_Msg(text) if text is not None else None)
        self.postback = type("PB", (), {"data": data})() if data is not None else None


def say(uid, text):
    lb.handle_text(_Ev(uid, text))


def press(uid, data):
    lb.handle_postback(_Ev(uid, data=data))


def readable(message):
    return getattr(message, "text", None) or getattr(message, "alt_text", "") or type(message).__name__


def sent_to(outbox, uid):
    return [readable(m) for kind, to, m in outbox.sent if kind == "push" and to == uid]


def replies(outbox):
    return [readable(m) for kind, to, m in outbox.sent if kind == "reply"]


def mk(db, name, roles, uid=None, **kw):
    u = User(name=name, roles=roles, line_uid=uid, **kw)
    db.add(u); db.commit(); db.refresh(u)
    return u


# ═════════════════ 1. 新用戶第一句話不能被吞掉 ═════════════════
def test_first_message_sos_is_processed_not_swallowed(db, line_outbox):
    say("UnewA", "救命")
    assert any("確認" in r or "緊急" in r for r in replies(line_outbox)), "第一句「救命」必須進入求救確認"
    assert any("歡迎" in t for t in sent_to(line_outbox, "UnewA")), "同時補送歡迎與使用說明"


def test_first_message_need_is_created(db, line_outbox):
    say("UnewB", "需要水")
    db2 = SessionLocal()
    assert db2.query(CommunityNeed).filter(CommunityNeed.need_type == "water").count() == 1
    db2.close()


def test_plain_first_greeting_gets_a_single_welcome(db, line_outbox):
    say("UnewC", "你好")
    assert len(replies(line_outbox)) == 1 and "歡迎" in replies(line_outbox)[0]


def test_follow_event_registers_and_welcomes(db, line_outbox):
    ev = type("F", (), {"source": _Src("UfollowA"), "reply_token": "tok"})()
    lb.handle_follow(ev)
    db2 = SessionLocal()
    assert db2.query(User).filter(User.line_uid == "UfollowA").count() == 1
    db2.close()
    assert any("歡迎" in r for r in replies(line_outbox))


# ═════════════════ 2. 一鍵求助：誠實告知誰被通知 ═════════════════
def _elder_with_family(db, with_checkin):
    elder = mk(db, "王奶奶", ["elderly"], "Uel", lat=24.15, lng=120.68)
    fam = mk(db, "王小華", ["family"], "Ufam")
    db.add(CareRelation(elderly_id=elder.id, contact_id=fam.id, relation="family", notify_order=1, is_active=True))
    if with_checkin:
        db.add(DailyCheckin(elderly_id=elder.id, date=today_tw(), status="pending"))
    db.commit()
    return elder, fam


@pytest.mark.parametrize("with_checkin", [True, False])
def test_sos_notifies_family_even_without_todays_checkin(db, line_outbox, with_checkin):
    _elder_with_family(db, with_checkin)
    press("Uel", "action=confirm_sos")
    assert sent_to(line_outbox, "Ufam"), "當天沒有打卡紀錄時，之前完全沒人被通知卻回覆「正在通知家屬」"
    assert "1 位" in " ".join(replies(line_outbox))


def test_sos_reply_is_honest_when_nobody_can_be_notified(db, line_outbox):
    mk(db, "獨居阿公", ["elderly"], "Ualone", lat=24.1, lng=120.6)
    press("Ualone", "action=confirm_sos")
    text = " ".join(replies(line_outbox))
    assert "沒有登記可以通知" in text and "119" in text
    assert "已通知您的" not in text


def test_sos_creates_sos_type_need_and_pings_admins(db, line_outbox):
    mk(db, "管理員", ["admin"], "Uadmin")
    mk(db, "獨居阿公", ["elderly"], "Ualone", lat=24.1, lng=120.6, address="某地")
    press("Ualone", "action=confirm_sos")
    db2 = SessionLocal()
    need = db2.query(CommunityNeed).one()
    assert need.need_type == "sos" and need.urgency == 5
    db2.close()
    assert any("獨居阿公" in t for t in sent_to(line_outbox, "Uadmin"))
    assert "已通知社區管理員" in " ".join(replies(line_outbox))


def test_sos_can_alert_again_after_thirty_minutes(db, line_outbox):
    _elder_with_family(db, True)
    press("Uel", "action=confirm_sos")
    first = len(sent_to(line_outbox, "Ufam"))
    press("Uel", "action=confirm_sos")
    assert len(sent_to(line_outbox, "Ufam")) == first, "30 分鐘內重複按不重複轟炸家屬"
    db2 = SessionLocal()
    for a in db2.query(Alert).all():
        a.created_at = now_utc() - timedelta(minutes=45)
    db2.commit(); db2.close()
    press("Uel", "action=confirm_sos")
    assert len(sent_to(line_outbox, "Ufam")) == first + 1, "隔一段時間再求助，家屬必須再收到一次"


def test_help_button_on_checkin_card_actually_alerts(db, line_outbox):
    elder, _ = _elder_with_family(db, True)
    db2 = SessionLocal()
    cid = str(db2.query(DailyCheckin).one().id); db2.close()
    press("Uel", f"action=help&checkin_id={cid}")
    assert sent_to(line_outbox, "Ufam"), "打卡卡片上的「需要幫忙」之前只改狀態、沒有發警報"


def test_sos_need_is_not_a_supply_request(db, line_outbox):
    """一鍵求助是人身安全事件，不是要水要食物；不能參與物資自動媒合，也不能被手動指派物資。"""
    elder = mk(db, "阿嬤", ["elderly"], "Uel", lat=24.15, lng=120.68)
    vol = mk(db, "志工", ["volunteer"], "Uvol", lat=24.15, lng=120.68)
    db.add(CommunityResource(owner_id=vol.id, resource_type="other", name="雜物", lat=24.15, lng=120.68, is_available=True))
    db.add(SystemConfig(key="mode", value="emergency"))
    sos = CommunityNeed(requester_id=elder.id, need_type="sos", urgency=5, lat=24.15, lng=120.68, status="open")
    db.add(sos); db.commit()
    result = dispatch.auto_dispatch()
    assert result["suggested"] == 0 and result["skipped"] == 0
    res_id = str(db.query(CommunityResource).one().id)
    out = dispatch.manual_dispatch(str(sos.id), res_id, db)
    assert "error" in out and "119" in out["error"]


# ═════════════════ 3. 意圖判斷：否定、多需求、缺口 ═════════════════
@pytest.mark.parametrize("text,expected", [
    ("需要水", ["water"]),
    ("我沒有水了", ["water"]),
    ("沒有東西吃", ["food"]),
    ("需要水 需要食物 需要藥", ["water", "food", "first_aid"]),
    ("不需要水", []),
    ("我不缺水", []),
    ("已經有水了不用送", []),
    ("淹水了怎麼辦", []),
])
def test_intent_parsing(text, expected):
    assert lb.parse_intent(text)["needs"] == expected


def test_negated_request_creates_nothing_and_says_so(db, line_outbox):
    mk(db, "阿伯", ["elderly"], "Ua", lat=24.1, lng=120.6)
    say("Ua", "不需要水")
    assert "先不用登記" in " ".join(replies(line_outbox))
    db2 = SessionLocal(); assert db2.query(CommunityNeed).count() == 0; db2.close()


def test_multiple_needs_in_one_sentence_are_all_registered(db, line_outbox):
    mk(db, "阿伯", ["elderly"], "Ua", lat=24.1, lng=120.6)
    say("Ua", "需要水 需要食物 需要藥")
    db2 = SessionLocal()
    assert {n.need_type for n in db2.query(CommunityNeed).all()} == {"water", "food", "first_aid"}
    db2.close()


def test_injury_is_urgent_and_points_to_119(db, line_outbox):
    mk(db, "阿伯", ["elderly"], "Ua", lat=24.1, lng=120.6)
    say("Ua", "我受傷了")
    db2 = SessionLocal()
    assert db2.query(CommunityNeed).one().urgency == 4
    db2.close()
    assert "119" in " ".join(replies(line_outbox))


def test_sos_word_takes_priority_over_supply_need(db, line_outbox):
    mk(db, "阿伯", ["elderly"], "Ua", lat=24.1, lng=120.6)
    say("Ua", "救命我沒水了")
    db2 = SessionLocal(); assert db2.query(CommunityNeed).count() == 0; db2.close()
    assert any("確認" in r or "緊急" in r for r in replies(line_outbox))


def test_need_without_location_asks_for_it_with_a_button(db, line_outbox):
    mk(db, "阿伯", ["elderly"], "Ua")
    say("Ua", "需要水")
    quick = [m for kind, to, m in line_outbox.sent if kind == "reply" and getattr(m, "quick_reply", None)]
    assert quick, "沒有座標時，回覆要帶一鍵分享位置按鈕"
    assert "不知道您在哪" in readable(quick[0])


# ═════════════════ 4. 任務卡：授權與狀態驗證 ═════════════════
def _matched_task(db):
    req = mk(db, "需求者", ["elderly"], "Ureq", lat=24.15, lng=120.68)
    vol = mk(db, "志工", ["volunteer"], "Uvol", lat=24.151, lng=120.681)
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="水", lat=24.151, lng=120.681, is_available=False)
    db.add(res); db.commit()
    need = CommunityNeed(requester_id=req.id, need_type="water", lat=24.15, lng=120.68, urgency=3,
                         status="matched", matched_resource_id=res.id)
    db.add(need); db.commit()
    return str(need.id)


def test_only_assigned_volunteer_can_decline_or_deliver(db, line_outbox):
    nid = _matched_task(db)
    press("Ureq", f"action=task_decline&need_id={nid}")
    press("Ureq", f"action=task_delivered&need_id={nid}")
    db2 = SessionLocal()
    assert db2.query(CommunityNeed).one().status == "matched", "需求者本人不能替志工把單退回或標成完成"
    db2.close()
    assert sum("不是這筆任務的受派志工" in r for r in replies(line_outbox)) == 2


def test_stale_task_cards_cannot_corrupt_state(db, line_outbox):
    nid = _matched_task(db)
    press("Uvol", f"action=task_delivered&need_id={nid}")
    db2 = SessionLocal(); assert db2.query(CommunityNeed).one().status == "fulfilled"; db2.close()
    press("Uvol", f"action=task_delivered&need_id={nid}")
    assert "已經回報完成" in replies(line_outbox)[-1]
    press("Uvol", f"action=task_decline&need_id={nid}")
    db3 = SessionLocal(); assert db3.query(CommunityNeed).one().status == "fulfilled", "已完成的需求不能被舊卡片復活"; db3.close()


def test_delivered_on_reopened_need_does_not_complete_it(db, line_outbox):
    nid = _matched_task(db)
    db2 = SessionLocal()
    n = db2.query(CommunityNeed).one(); n.status = "open"; n.matched_resource_id = None; db2.commit(); db2.close()
    press("Uvol", f"action=task_delivered&need_id={nid}")
    db3 = SessionLocal(); assert db3.query(CommunityNeed).one().status == "open"; db3.close()


@pytest.mark.parametrize("data", ["action=task_delivered&need_id=not-a-uuid", "action=task_delivered", "action=task_decline&need_id="])
def test_bad_task_payload_is_not_reported_as_success(db, line_outbox, data):
    _matched_task(db)
    press("Uvol", data)
    assert "感謝您完成送達" not in " ".join(replies(line_outbox))
    assert "找不到這筆任務" in replies(line_outbox)[-1]


def test_requester_is_told_about_every_step(db, line_outbox):
    nid = _matched_task(db)
    press("Uvol", f"action=task_decline&need_id={nid}")
    assert any("無法前往" in t for t in sent_to(line_outbox, "Ureq")), "志工退回時要告訴等待的人"
    db2 = SessionLocal()
    n = db2.query(CommunityNeed).one(); res = db2.query(CommunityResource).one()
    n.status = "suggested"; n.matched_resource_id = res.id; res.is_available = False; db2.commit(); db2.close()
    db3 = SessionLocal()
    dispatch.confirm_dispatch(nid, db3)
    assert any("有人接下" in t for t in sent_to(line_outbox, "Ureq")), "派遣確認時要告訴需求者"
    press("Uvol", f"action=task_delivered&need_id={nid}")
    assert any("送達" in t for t in sent_to(line_outbox, "Ureq")), "送達時要告訴需求者"


def test_cancelling_a_matched_need_tells_the_volunteer(db, line_outbox):
    nid = _matched_task(db)
    say("Ureq", "取消需求")
    db2 = SessionLocal(); assert db2.query(CommunityNeed).one().status == "cancelled"; db2.close()
    assert any("已取消" in t for t in sent_to(line_outbox, "Uvol"))
    press("Uvol", f"action=task_delivered&need_id={nid}")
    assert "感謝您完成送達" not in replies(line_outbox)[-1]


# ═════════════════ 5. 派遣邏輯 ═════════════════
def test_a_volunteers_own_resource_never_matches_their_own_need(db):
    person = mk(db, "又是志工又求助", ["elderly", "volunteer"], "Uboth", lat=24.15, lng=120.68)
    db.add(CommunityResource(owner_id=person.id, resource_type="water", name="自己的水", lat=24.15, lng=120.68, is_available=True))
    need = CommunityNeed(requester_id=person.id, need_type="water", lat=24.15, lng=120.68, urgency=3, status="open")
    db.add(need); db.commit()
    assert dispatch.preview_candidates(str(need.id), db)["candidates"] == []
    res_id = str(db.query(CommunityResource).one().id)
    assert "error" in dispatch.manual_dispatch(str(need.id), res_id, db)


def test_suggested_need_still_shows_the_candidate_that_was_proposed(db):
    req = mk(db, "需求者", ["elderly"], lat=24.15, lng=120.68)
    vol = mk(db, "志工", ["volunteer"], "Uv", lat=24.151, lng=120.681)
    db.add(CommunityResource(owner_id=vol.id, resource_type="water", name="水", lat=24.151, lng=120.681, is_available=True))
    db.add(SystemConfig(key="mode", value="emergency"))
    need = CommunityNeed(requester_id=req.id, need_type="water", lat=24.15, lng=120.68, urgency=3, status="open")
    db.add(need); db.commit()
    dispatch.auto_dispatch()
    preview = dispatch.preview_candidates(str(need.id), db)
    assert preview["candidates"], "待確認需求的詳情不能是「無候選資源」"
    assert preview["candidates"][0]["is_current"] is True


# ═════════════════ 6. 取消、非文字訊息、AI ═════════════════
def test_volunteer_can_withdraw_unmatched_resources(db, line_outbox):
    vol = mk(db, "志工", ["volunteer"], "Uv")
    db.add_all([CommunityResource(owner_id=vol.id, resource_type="water", name="a", is_available=True),
                CommunityResource(owner_id=vol.id, resource_type="food", name="b", is_available=False)])
    db.commit()
    say("Uv", "取消物資")
    db2 = SessionLocal()
    assert [r.name for r in db2.query(CommunityResource).all()] == ["b"], "已被媒合的不能撤回"
    db2.close()


def test_image_or_sticker_gets_a_reply_instead_of_silence(db, line_outbox):
    lb.handle_other_message(_Ev("Ux", text=None))
    assert "只看得懂文字" in replies(line_outbox)[-1] and "119" in replies(line_outbox)[-1]


def test_ai_failure_is_reported_honestly(db, line_outbox, monkeypatch):
    mk(db, "阿伯", ["elderly"], "Ua")
    from app.services import rag
    monkeypatch.setattr(rag, "query", lambda q: (_ for _ in ()).throw(RuntimeError("no key")))
    say("Ua", "怎麼處理外傷出血")
    text = replies(line_outbox)[-1]
    assert "暫時無法使用" in text and "119" in text and "收到您的訊息了" not in text


def test_residents_can_use_the_ai_assistant_too(db, line_outbox, monkeypatch):
    mk(db, "阿伯", ["elderly"], "Ua")
    from app.services import rag
    monkeypatch.setattr(rag, "query", lambda q: {"has_answer": True, "answer": "直接加壓止血", "sources": ["急救手冊"]})
    say("Ua", "CPR")
    assert "直接加壓止血" in replies(line_outbox)[-1]


# ═════════════════ 7. 志工登記物資 ═════════════════
def test_registering_the_same_resource_twice_updates_instead_of_duplicating(db, line_outbox):
    mk(db, "志工", ["volunteer"], "Uv", lat=24.15, lng=120.68)
    say("Uv", "我有水")
    say("Uv", "我有 水 20箱 台中市南區")
    db2 = SessionLocal()
    rows = db2.query(CommunityResource).all()
    assert len(rows) == 1 and rows[0].quantity == "20箱"
    db2.close()


def test_resource_without_location_warns_and_is_fixed_by_sharing_location(db, line_outbox):
    mk(db, "志工", ["volunteer"], "Uv")
    say("Uv", "我有 水 20箱 某個查不到的地址")
    assert "不會被媒合" in replies(line_outbox)[-1]
    lb.handle_location(_Ev("Uv", msg=_Msg(latitude=24.15, longitude=120.68)))
    db2 = SessionLocal()
    r = db2.query(CommunityResource).one()
    assert (r.lat, r.lng) == (24.15, 120.68), "分享位置後，之前沒座標的物資也要一起補上"
    db2.close()


def test_resource_address_is_geocoded_when_user_has_no_location(db, line_outbox, monkeypatch):
    from app.services import places
    monkeypatch.setattr(places, "search_places", lambda q: [{"name": q, "lat": 24.2, "lng": 120.7}])
    mk(db, "志工", ["volunteer"], "Uv")
    say("Uv", "我有 水 20箱 台中市南區崇倫街88號")
    db2 = SessionLocal()
    assert (db2.query(CommunityResource).one().lat) == 24.2
    db2.close()


# ═════════════════ 8. 打卡：平安回報與時區 ═════════════════
def test_late_ok_resolves_alerts_and_tells_family(db, line_outbox):
    elder, fam = _elder_with_family(db, True)
    db2 = SessionLocal()
    ck = db2.query(DailyCheckin).one()
    ck.status = "no_response"; ck.created_at = now_utc() - timedelta(hours=4); db2.commit()
    db2.add(Alert(elderly_id=elder.id, checkin_id=ck.id, alert_type="no_response_3h", notified_users=[fam.id], status="sent"))
    db2.commit(); db2.close()
    say("Uel", "我很好")
    db3 = SessionLocal()
    assert db3.query(DailyCheckin).one().status == "ok", "超過 3 小時才回報，也要記成平安"
    assert db3.query(Alert).one().status == "resolved"
    db3.close()
    assert any("已回報平安" in t for t in sent_to(line_outbox, "Ufam"))


def test_no_response_job_uses_utc_not_machine_local_time(db, line_outbox):
    from app.services.alert import check_no_response
    elder, fam = _elder_with_family(db, True)
    check_no_response()  # 剛建立的打卡不能立刻被判定未回應
    db2 = SessionLocal(); assert db2.query(Alert).count() == 0; db2.close()
    db3 = SessionLocal()
    ck = db3.query(DailyCheckin).one(); ck.created_at = now_utc() - timedelta(minutes=61); db3.commit(); db3.close()
    check_no_response()
    db4 = SessionLocal(); assert {a.alert_type for a in db4.query(Alert).all()} == {"no_response_1h"}; db4.close()


def test_someone_elses_checkin_button_is_rejected(db, line_outbox):
    elder, _ = _elder_with_family(db, True)
    mk(db, "路人", ["elderly"], "Uother")
    db2 = SessionLocal(); cid = str(db2.query(DailyCheckin).one().id); db2.close()
    press("Uother", f"action=ok&checkin_id={cid}")
    db3 = SessionLocal(); assert db3.query(DailyCheckin).one().status == "pending"; db3.close()


# ═════════════════ 9. 管理端 API ═════════════════
@pytest.fixture
def api():
    app = FastAPI()
    from app.main import app as real_app
    for exc_cls, handler_fn in real_app.exception_handlers.items():
        app.add_exception_handler(exc_cls, handler_fn)
    app.include_router(dashboard_router.router, prefix="/api/dashboard")
    app.include_router(resources_router.router, prefix="/api/resources")
    return TestClient(app)


def test_delete_user_with_related_data_cleans_up_instead_of_500(db, api):
    elder, fam = _elder_with_family(db, True)
    db.add(CommunityNeed(requester_id=elder.id, need_type="water", status="open")); db.commit()
    res = api.delete(f"/api/dashboard/users/{elder.id}")
    assert res.status_code == 200
    db2 = SessionLocal()
    assert db2.query(User).filter(User.id == elder.id).count() == 0
    assert db2.query(CareRelation).count() == 0 and db2.query(DailyCheckin).count() == 0
    db2.close()


def test_delete_user_with_active_dispatch_is_refused_with_a_reason(db, api):
    _matched_task(db)
    vol_id = str(db.query(User).filter(User.line_uid == "Uvol").one().id)
    res = api.delete(f"/api/dashboard/users/{vol_id}")
    assert res.status_code == 409 and "進行中的派遣" in res.json()["error"]


def test_duplicate_line_uid_and_relation_are_conflicts_not_500(db, api):
    elder, fam = _elder_with_family(db, False)
    dup = api.post("/api/dashboard/users", params={"name": "重複", "roles": ["volunteer"], "line_uid": "Uel"})
    assert dup.status_code == 409
    rel = api.post("/api/dashboard/relations", params={"elderly_id": str(elder.id), "contact_id": str(fam.id), "relation": "family"})
    assert rel.status_code == 409
    self_rel = api.post("/api/dashboard/relations", params={"elderly_id": str(elder.id), "contact_id": str(elder.id), "relation": "self"})
    assert self_rel.status_code == 422


@pytest.mark.parametrize("method,url,params", [
    ("post", "/api/resources/needs", {"need_type": "water", "urgency": 99, "address": "x"}),
    ("post", "/api/resources/needs", {"need_type": "water", "urgency": -3, "address": "x"}),
    ("post", "/api/resources/needs", {"need_type": "", "urgency": 3, "address": "x"}),
    ("post", "/api/resources/needs", {"need_type": "water", "urgency": 3, "lat": 999, "lng": 999}),
    ("post", "/api/dashboard/users", {"name": "", "roles": ["elderly"]}),
    ("post", "/api/dashboard/users", {"name": "駭客", "roles": ["hacker"]}),
    ("post", "/api/dashboard/users", {"name": "座標", "roles": ["elderly"], "lat": 91, "lng": 0}),
    ("post", "/api/dashboard/mode", {"mode": "banana"}),
])
def test_bad_input_is_rejected_with_error_field_the_frontend_reads(db, api, method, url, params):
    mk(db, "某人", ["elderly"])
    res = getattr(api, method)(url, params=params)
    assert res.status_code in (422, 429), res.text
    assert res.json().get("error"), "前端讀的是 data.error，被拒絕時必須有這個欄位"


def test_need_without_requester_is_not_pinned_on_a_random_real_person(db, api):
    real = mk(db, "第一位真人", ["elderly"])
    res = api.post("/api/resources/needs", params={"need_type": "water", "urgency": 3, "address": "x"})
    assert res.status_code == 200
    db2 = SessionLocal()
    need = db2.query(CommunityNeed).one()
    assert need.requester_id != real.id, "沒指定登記人時，不能冒名記在第一個使用者頭上"
    db2.close()


def test_dispatch_errors_use_real_status_codes(db, api):
    assert api.post("/api/resources/needs/not-a-uuid/confirm_dispatch").status_code in (404, 409)
    assert api.put("/api/resources/needs/00000000-0000-0000-0000-000000000000", params={"status": "matched"}).status_code in (404, 422)
    elder = mk(db, "某人", ["elderly"])
    need = CommunityNeed(requester_id=elder.id, need_type="water", status="open"); db.add(need); db.commit()
    res = api.put(f"/api/resources/needs/{need.id}", params={"status": "fulfilled"})
    assert res.status_code == 422, "不能繞過派遣流程直接把需求改成已完成"


def test_approval_updates_the_users_name_to_the_applicants(db):
    from app.services import volunteer_application as va
    mk(db, "小豬豬", ["elderly"], "Uapp")
    app_row = va.submit(db, line_uid="Uapp", name="陳小美", phone=None, service_area=None)
    va.decide(db, str(app_row.id), decision="approve")
    db2 = SessionLocal()
    assert db2.query(User).filter(User.line_uid == "Uapp").one().name == "陳小美"
    db2.close()


# ═════════════════ 10. 系統層 ═════════════════
def test_webhook_swallows_handler_errors_instead_of_500(monkeypatch):
    from app.main import app as real_app
    monkeypatch.setattr(lb.handler, "handle", lambda body, sig: (_ for _ in ()).throw(RuntimeError("boom")))
    res = TestClient(real_app).post("/webhook/line", content=b"{}", headers={"X-Line-Signature": "x"})
    assert res.status_code == 200


def test_security_status_flags_public_production(monkeypatch):
    from app.config import settings
    from app.main import app as real_app
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "")
    assert TestClient(real_app).get("/api/system/security").json()["public_admin"] is True
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "secret")
    assert TestClient(real_app).get("/api/system/security", auth=("x", "secret")).json()["public_admin"] is False


def test_disabled_account_is_told_not_silently_served(db, line_outbox):
    mk(db, "停用者", ["elderly"], "Udis", is_active=False)
    say("Udis", "需要水")
    assert "已停用" in replies(line_outbox)[-1]
    db2 = SessionLocal(); assert db2.query(CommunityNeed).count() == 0; db2.close()


def test_taiwan_date_is_used_for_today():
    from datetime import datetime, timedelta, timezone
    assert today_tw() == datetime.now(timezone(timedelta(hours=8))).date()


# ═════════════════ 11. 二次抓包：修正本身帶出的問題 ═════════════════
def test_commands_break_out_of_the_guided_application_instead_of_being_swallowed(db, line_outbox):
    """重跑端到端時抓到：進入「我要當志工」問卷後，接下來傳的「幫助」「登記物資」都被當成
    姓名／電話吞掉，30 分鐘內什麼指令都用不了。"""
    mk(db, "阿伯", ["elderly"], "Ua", lat=24.1, lng=120.6)
    say("Ua", "我要當志工")
    say("Ua", "幫助")
    assert "可用指令" in replies(line_outbox)[-1], "問卷中傳「幫助」要正常回覆指令說明"
    say("Ua", "我要當志工")
    say("Ua", "需要水")
    assert "已登記您的需求" in replies(line_outbox)[-1]
    say("Ua", "陳小美")
    assert "請問聯絡電話" not in replies(line_outbox)[-1], "問卷已被中斷，不能再把姓名當成答案"


def test_one_shot_application_text_works_even_while_a_questionnaire_is_open(db, line_outbox):
    mk(db, "阿伯", ["elderly"], "Ua")
    say("Ua", "我要當志工")
    say("Ua", "志工申請 王小明 0912345678 台中市南區")
    assert "已收到您的志工申請" in replies(line_outbox)[-1]


def test_ordinary_long_chatter_does_not_call_the_paid_ai(db, line_outbox, monkeypatch):
    from app.services import rag
    calls = []
    monkeypatch.setattr(rag, "query", lambda q: calls.append(q) or {"has_answer": False})
    mk(db, "阿伯", ["elderly"], "Ua", lat=24.1, lng=120.6)
    say("Ua", "今天天氣真好啊各位鄰居大家早安")
    say("Ua", "x" * 3000)
    say("Ua", "<script>alert(1)</script>")
    assert calls == [], "一般閒聊、亂碼、超長訊息不該每則都呼叫一次 Gemini"
    say("Ua", "斷電了要怎麼辦？")
    assert len(calls) == 1


def test_admin_can_close_a_sos_and_the_requester_is_told(db, line_outbox, api):
    mk(db, "獨居阿公", ["elderly"], "Ualone", lat=24.1, lng=120.6)
    press("Ualone", "action=confirm_sos")
    db2 = SessionLocal(); nid = str(db2.query(CommunityNeed).one().id); db2.close()
    res = api.post(f"/api/resources/needs/{nid}/resolve_sos")
    assert res.status_code == 200
    db3 = SessionLocal(); assert db3.query(CommunityNeed).one().status == "fulfilled"; db3.close()
    assert any("管理員已確認處理" in t for t in sent_to(line_outbox, "Ualone"))
    assert api.post(f"/api/resources/needs/{nid}/resolve_sos").status_code == 200  # 重複按不出錯


def test_resolve_sos_refuses_ordinary_supply_needs(db, api):
    elder = mk(db, "某人", ["elderly"])
    need = CommunityNeed(requester_id=elder.id, need_type="water", status="open"); db.add(need); db.commit()
    assert api.post(f"/api/resources/needs/{need.id}/resolve_sos").status_code == 409


def test_resource_point_rejects_bad_coordinates_and_negative_capacity(db, api):
    bad = api.post("/api/resources/points", params={"name": "某處", "point_type": "shelter", "lat": 500, "lng": 500})
    assert bad.status_code == 422
    neg = api.post("/api/resources/points", params={"name": "某處", "point_type": "shelter", "capacity": -5})
    assert neg.status_code == 422


# ── Rich Menu ────────────────────────────────────────────────────────────────
class _FakeMenuApi:
    def __init__(self, existing=()):
        self.calls = []
        self.existing = list(existing)

    def get_rich_menu_list(self):
        class R: pass
        r = R()
        r.richmenus = self.existing
        return r

    def delete_rich_menu(self, mid): self.calls.append(("delete", mid))

    def create_rich_menu(self, req):
        self.calls.append(("create", req))
        class R: pass
        r = R()
        r.rich_menu_id = f"menu-{len([c for c in self.calls if c[0] == 'create'])}"
        return r

    def set_default_rich_menu(self, mid): self.calls.append(("default", mid))
    def link_rich_menu_id_to_user(self, uid, mid): self.calls.append(("link", uid, mid))
    def unlink_rich_menu_id_from_user(self, uid): self.calls.append(("unlink", uid))


class _FakeBlob:
    def __init__(self): self.images = []
    def set_rich_menu_image(self, mid, body=None, **kw): self.images.append((mid, len(body), kw))


def test_rich_menu_layout_covers_canvas_and_uses_known_commands():
    from app.routers.linebot import FIXED_COMMANDS, parse_intent
    from app.services import rich_menu as rm
    for name, spec in rm.MENUS.items():
        cells = rm.layout(spec["rows"])
        assert len(cells) == 8
        assert sum(w * h for _, _, w, h, _ in cells) == rm.W * rm.H
        for *_, cell in cells:
            text = cell[4]
            assert text in FIXED_COMMANDS or parse_intent(text)["needs"] or parse_intent(text)["sos"], (name, text)


def test_install_menus_creates_two_and_links_staff(db, monkeypatch):
    from app.models.user import User
    from app.services import rich_menu as rm
    api, blob = _FakeMenuApi(), _FakeBlob()
    monkeypatch.setattr(rm, "_apis", lambda: (api, blob))
    db.add_all([User(name="志工", roles=["volunteer"], line_uid="U-vol", is_active=True),
                User(name="長者", roles=["elderly"], line_uid="U-eld", is_active=True)])
    db.commit()
    result = rm.install_menus(db)
    assert len([c for c in api.calls if c[0] == "create"]) == 2
    assert len(blob.images) == 2 and all(size > 1000 for _, size, _ in blob.images)
    assert ("default", result["menus"][rm.RESIDENT_NAME]) in api.calls
    assert api.calls.count(("link", "U-vol", result["menus"][rm.STAFF_NAME])) == 1
    assert not [c for c in api.calls if c[0] == "link" and c[1] == "U-eld"]
    assert result["staff_linked"] == 1


def test_install_menus_removes_old_menus(db, monkeypatch):
    from app.services import rich_menu as rm
    class M:
        def __init__(self, i, n): self.rich_menu_id, self.name = i, n
    api = _FakeMenuApi(existing=[M("old-1", "鄰里守望選單"), M("other", "別人的選單")])
    monkeypatch.setattr(rm, "_apis", lambda: (api, _FakeBlob()))
    result = rm.install_menus(db)
    assert ("delete", "old-1") in api.calls and ("delete", "other") not in api.calls
    assert result["removed_old"] == 1


def test_sync_user_menu_is_best_effort(monkeypatch):
    from app.models.user import User
    from app.services import rich_menu as rm
    def boom(): raise RuntimeError("LINE down")
    monkeypatch.setattr(rm, "_apis", boom)
    rm.sync_user_menu(User(name="x", roles=["volunteer"], line_uid="U-x"))  # must not raise


def test_share_location_command_offers_location_button(db, line_outbox):
    mk(db, "長者", ["elderly"], uid="U-loc", lat=23.9, lng=121.6)
    say("U-loc", "分享位置")
    assert any("分享我的位置" in t for t in replies(line_outbox))


# ── 卡片表單 ─────────────────────────────────────────────────────────────────
def _flex_replies(outbox):
    return [m for kind, _to, m in outbox.sent if kind == "reply" and type(m).__name__ == "FlexMessage"]


def _selected_labels(flex_message) -> list[str]:
    out = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "button" and node["action"]["label"].startswith("✅"):
                out.append(node["action"]["label"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(flex_message.contents.to_dict() if hasattr(flex_message.contents, "to_dict") else flex_message.contents)
    return out


def test_need_form_end_to_end(db, line_outbox):
    mk(db, "長者", ["elderly"], uid="U-form", lat=23.9, lng=121.6)
    say("U-form", "申請物資")
    assert len(_flex_replies(line_outbox)) == 1
    press("U-form", "action=form&f=need&op=type&v=water")
    press("U-form", "action=form&f=need&op=type&v=food")
    press("U-form", "action=form&f=need&op=type&v=food")          # 再按一次取消勾選
    press("U-form", "action=form&f=need&op=people&v=3")
    press("U-form", "action=form&f=need&op=urgent&v=1")
    labels = _selected_labels(_flex_replies(line_outbox)[-1])
    assert any("飲用水" in l for l in labels) and not any("食物" in l for l in labels)
    press("U-form", "action=form&f=need&op=go")
    needs = db.query(CommunityNeed).all()
    assert [(n.need_type, n.urgency) for n in needs] == [("water", 4)]
    assert "3人" in needs[0].description
    assert any("已登記您的需求" in t for t in replies(line_outbox))
    press("U-form", "action=form&f=need&op=go")                    # 表單已用完，不能重複送出
    assert len(db.query(CommunityNeed).all()) == 1


def test_need_form_requires_a_choice_and_can_be_cancelled(db, line_outbox):
    mk(db, "長者", ["elderly"], uid="U-form2")
    say("U-form2", "申請物資")
    press("U-form2", "action=form&f=need&op=go")
    assert any("至少一項" in t for t in replies(line_outbox))
    assert db.query(CommunityNeed).count() == 0
    press("U-form2", "action=form&f=need&op=cancel")
    press("U-form2", "action=form&f=need&op=type&v=water")
    assert any("失效" in t for t in replies(line_outbox))


def test_resource_form_is_staff_only_and_registers(db, line_outbox):
    mk(db, "民眾", ["elderly"], uid="U-res-no")
    say("U-res-no", "登記物資")
    assert any("僅限志工" in t for t in replies(line_outbox))
    press("U-res-no", "action=form&f=res&op=type&v=water")
    assert db.query(CommunityResource).count() == 0

    mk(db, "志工", ["volunteer"], uid="U-res-ok", lat=23.9, lng=121.6)
    say("U-res-ok", "登記物資")
    press("U-res-ok", "action=form&f=res&op=type&v=water")
    press("U-res-ok", "action=form&f=res&op=qty&v=30份")
    press("U-res-ok", "action=form&f=res&op=go")
    res = db.query(CommunityResource).all()
    assert [(r.resource_type, r.quantity) for r in res] == [("water", "30份")]


def test_resource_form_needs_type_and_quantity_and_resets_qty_on_type_change(db, line_outbox):
    mk(db, "志工", ["volunteer"], uid="U-res2")
    say("U-res2", "登記物資")
    press("U-res2", "action=form&f=res&op=go")
    assert any("種類和數量" in t for t in replies(line_outbox))
    press("U-res2", "action=form&f=res&op=type&v=water")
    press("U-res2", "action=form&f=res&op=qty&v=30份")
    press("U-res2", "action=form&f=res&op=type&v=vehicle")         # 換種類：舊數量不適用
    press("U-res2", "action=form&f=res&op=qty&v=30份")             # 車輛沒有「30份」，要被忽略
    press("U-res2", "action=form&f=res&op=go")
    assert db.query(CommunityResource).count() == 0


def test_form_does_not_hijack_text_and_forged_values_are_ignored(db, line_outbox):
    mk(db, "長者", ["elderly"], uid="U-form3", lat=23.9, lng=121.6)
    say("U-form3", "申請物資")
    say("U-form3", "我很好")                                        # 表單開著時一般指令照常運作
    assert any("太好了" in t or "平安" in t or "今天" in t for t in replies(line_outbox))
    press("U-form3", "action=form&f=need&op=type&v=<script>")
    press("U-form3", "action=form&f=need&op=go")
    assert db.query(CommunityNeed).count() == 0


def test_flex_cards_keep_their_content_when_serialized():
    """字典直接塞給 FlexMessage 時，SDK 只留下 type，送出去是空的 bubble。"""
    from linebot.v3.messaging import ApiClient, Configuration, ReplyMessageRequest
    from app.services import line_forms
    from app.services.line_notify import _flex
    client = ApiClient(Configuration(access_token="x"))
    for card in (line_forms.need_card({"types": ["water"]}), line_forms.resource_card({"rtype": "water"})):
        req = ReplyMessageRequest(reply_token="t", messages=[_flex("alt", card)])
        sent = client.sanitize_for_serialization(req)["messages"][0]["contents"]
        assert "body" in sent and "footer" in sent and len(str(sent)) > 1000


def test_text_fields_open_keyboard_with_prefill_and_survive_serialization():
    from linebot.v3.messaging import ApiClient, Configuration, ReplyMessageRequest
    from app.services import line_forms
    from app.services.line_notify import _flex
    client = ApiClient(Configuration(access_token="x"))
    for card, prefix in ((line_forms.need_card({}), "補充："), (line_forms.resource_card({}), "地址：")):
        req = ReplyMessageRequest(reply_token="t", messages=[_flex("alt", card)])
        text = str(client.sanitize_for_serialization(req))
        assert "openKeyboard" in text and prefix in text


def test_need_form_note_is_saved_with_the_need(db, line_outbox):
    mk(db, "長者", ["elderly"], uid="U-note", lat=23.9, lng=121.6)
    say("U-note", "申請物資")
    press("U-note", "action=form&f=need&op=type&v=water")
    press("U-note", "action=form&op=noop")                         # 按鈕本身送出的 postback 要被安靜略過
    say("U-note", "補充：樓梯很陡，家裡有行動不便的長者")
    assert any("樓梯很陡" in " ".join(_selected_or_text(m)) for m in _flex_replies(line_outbox)[-1:])
    press("U-note", "action=form&f=need&op=go")
    need = db.query(CommunityNeed).one()
    assert "樓梯很陡" in need.description


def _selected_or_text(flex_message):
    out = []
    def walk(node):
        if isinstance(node, dict):
            if node.get("type") == "text":
                out.append(node["text"])
            for v in node.values():
                walk(v)
        elif isinstance(node, list):
            for v in node:
                walk(v)
    walk(flex_message.contents.to_dict())
    return out


def test_resource_form_address_with_keyword_does_not_change_type(db, line_outbox, monkeypatch):
    from app.services import places
    monkeypatch.setattr(places, "geocode_address", lambda a: (24.15, 120.68))
    mk(db, "志工", ["volunteer"], uid="U-addr")
    say("U-addr", "登記物資")
    press("U-addr", "action=form&f=res&op=type&v=food")
    press("U-addr", "action=form&f=res&op=qty&v=30份")
    say("U-addr", "地址：台中市西區水湳路99號")                       # 地址裡有「水」，不能被當成飲用水
    press("U-addr", "action=form&f=res&op=go")
    res = db.query(CommunityResource).one()
    assert (res.resource_type, res.quantity, res.address) == ("food", "30份", "台中市西區水湳路99號")
    assert (res.lat, res.lng) == (24.15, 120.68)


def test_form_text_prefix_without_open_form_falls_through(db, line_outbox):
    mk(db, "長者", ["elderly"], uid="U-nf")
    say("U-nf", "補充：隨便")                                        # 沒有開表單就當一般訊息，不能吞掉也不能壞掉
    assert replies(line_outbox)
