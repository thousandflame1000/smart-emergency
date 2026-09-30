# -*- coding: utf-8 -*-
"""2026-09-27 彩排卡住的幾件事：新需求沒有自動找志工、後台不能對志工下指示、
只有一支 LINE 無法驗證家屬通知與志工派遣。"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.care_relation import CareRelation
from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.routers import dashboard as dashboard_router
from app.routers import linebot as lb
from app.routers import resources as resources_router
from app.services import rehearsal_kit
from tests.test_line_hardening import mk, press, sent_to
from tests.test_role_interfaces import card_text


def client():
    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api/dashboard")
    app.include_router(resources_router.router, prefix="/api/resources")
    return TestClient(app)


def volunteer_with_water(db, uid="U-vol", name="志工甲"):
    vol = mk(db, name, ["volunteer"], uid, lat=24.0, lng=120.6)
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="甲的水", quantity="10",
                            lat=24.0, lng=120.6, is_available=True)
    db.add(res); db.commit(); db.refresh(res)
    return vol, res


# ═══════════════ 新需求一進來就建議志工，不需要照護關係 ═══════════════
def test_new_need_is_suggested_to_any_matching_volunteer_and_admin_can_approve_from_line(db, line_outbox):
    vol, res = volunteer_with_water(db)
    mk(db, "管理員", ["admin"], "U-adm")
    req = mk(db, "陌生居民", ["elderly"], "U-req", lat=24.01, lng=120.61)

    lb.submit_needs(db, req, ["water"], "網頁表單申請")

    need = db.query(CommunityNeed).filter(CommunityNeed.requester_id == req.id).one()
    assert need.status == "suggested" and str(need.matched_resource_id) == str(res.id)
    assert not db.query(CareRelation).count()  # 沒有任何照護關係也能配到
    assert "action=admin_confirm" in card_text(line_outbox, "U-adm")
    assert not sent_to(line_outbox, "U-vol")  # 核准前不通知志工

    press("U-adm", f"action=admin_confirm&need_id={need.id}")
    db.refresh(need)
    assert need.status == "matched"
    assert "action=task_accept" in card_text(line_outbox, "U-vol")


def test_own_supply_is_never_suggested_for_own_need(db, line_outbox):
    vol, res = volunteer_with_water(db)
    mk(db, "管理員", ["admin"], "U-adm")
    lb.submit_needs(db, vol, ["water"], "自己測試")
    need = db.query(CommunityNeed).filter(CommunityNeed.requester_id == vol.id).one()
    assert need.status == "open"
    assert "action=admin_cands" in card_text(line_outbox, "U-adm")


# ═══════════════ 後台對志工下指示 ═══════════════
def test_admin_can_message_the_assigned_volunteer_and_it_is_recorded(db, line_outbox):
    from app.services import dispatch
    vol, res = volunteer_with_water(db)
    req = mk(db, "居民", ["elderly"], "U-req", lat=24.01, lng=120.61)
    need = CommunityNeed(requester_id=req.id, need_type="water", lat=24.01, lng=120.61, urgency=3, status="open")
    db.add(need); db.commit()
    c = client()

    assert c.post(f"/api/resources/needs/{need.id}/message_assignee", json={"text": "先送收容所"}).status_code == 409

    dispatch.manual_dispatch(str(need.id), str(res.id), db)
    r = c.post(f"/api/resources/needs/{need.id}/message_assignee", json={"text": "改走台9線，先送收容所"})
    assert r.status_code == 200, r.text
    assert any("改走台9線" in t for t in sent_to(line_outbox, "U-vol"))
    event = db.query(DispatchEvent).filter(DispatchEvent.need_id == need.id,
                                           DispatchEvent.action == "admin_message").one()
    assert event.outcome == "sent" and "改走台9線" in event.details_json


# ═══════════════ 單帳號演練 ═══════════════
def test_one_line_account_can_receive_family_alerts_from_a_stand_in_elder(db, line_outbox):
    me = mk(db, "測試者", ["volunteer", "admin"], "U-me", lat=23.66, lng=121.42)
    c = client()
    assert [t["name"] for t in c.get("/api/dashboard/rehearsal/testers").json()] == ["測試者"]

    r = c.post(f"/api/dashboard/rehearsal/family-alert?tester_id={me.id}&status=unwell")
    assert r.status_code == 200, r.text
    assert r.json()["notified"] == 1
    assert sent_to(line_outbox, "U-me")
    # 可以重按；同一天第二次也要送得出去。
    before = len(sent_to(line_outbox, "U-me"))
    assert c.post(f"/api/dashboard/rehearsal/family-alert?tester_id={me.id}&status=unwell").json()["notified"] == 1
    assert len(sent_to(line_outbox, "U-me")) > before

    r = c.post(f"/api/dashboard/rehearsal/family-alert?tester_id={me.id}&status=help_needed")
    assert r.status_code == 200, r.text
    elder = db.query(User).filter(User.name == rehearsal_kit.ELDER_NAME).one()
    assert db.query(CommunityNeed).filter(CommunityNeed.requester_id == elder.id,
                                          CommunityNeed.need_type == "sos", CommunityNeed.status == "open").count() == 1
    # 一支手機走完志工流程：替身長者就在測試者旁邊，測試者（志工）收到「附近有人需要幫忙」
    assert "附近 1 位志工" in r.json()["message"] and "🆘 附近有人需要幫忙" in sent_to(line_outbox, "U-me")
    again = c.post(f"/api/dashboard/rehearsal/family-alert?tester_id={me.id}&status=help_needed").json()
    assert "還沒結案" in again["message"], "上一次的求救沒結案時要說清楚為什麼沒有新的通知"


def test_one_line_account_can_run_the_whole_volunteer_task_loop(db, line_outbox):
    me, res = volunteer_with_water(db, "U-me", "測試者")
    me.roles = ["volunteer", "admin"]; db.commit()
    c = client()

    r = c.post(f"/api/dashboard/rehearsal/volunteer-task?tester_id={me.id}")
    assert r.status_code == 200, r.text
    need = db.query(CommunityNeed).filter(CommunityNeed.id == r.json()["need_id"]).one()
    assert need.status == "suggested" and str(need.matched_resource_id) == str(res.id)
    assert "action=admin_confirm" in card_text(line_outbox, "U-me")

    press("U-me", f"action=admin_confirm&need_id={need.id}")
    db.refresh(need)
    assert need.status == "matched" and "action=task_accept" in card_text(line_outbox, "U-me")

    # 再按一次會先取消上一筆，不會卡在「物資已被保留」。
    db.refresh(res)
    r2 = c.post(f"/api/dashboard/rehearsal/volunteer-task?tester_id={me.id}")
    assert r2.status_code == 200, r2.text
    db.refresh(need)
    assert need.status == "cancelled"

    r = c.post("/api/dashboard/rehearsal/cleanup")
    assert r.status_code == 200, r.text
    assert not db.query(User).filter(User.name.like(rehearsal_kit.MARK + "%"), User.is_active == True).count()  # noqa: E712
    db.refresh(res)
    assert res.is_available


def test_volunteer_task_explains_what_to_do_without_a_registered_supply(db, line_outbox):
    me = mk(db, "測試者", ["volunteer"], "U-me")
    r = client().post(f"/api/dashboard/rehearsal/volunteer-task?tester_id={me.id}")
    assert r.status_code == 409 and "登記物資" in r.text


# ═══════════════ 工作區：即時需求要綁到事件上 ═══════════════
def test_live_needs_and_nearby_points_bind_to_the_workspace_incident(db):
    from app.models.resource_point import ResourcePoint
    from app.services.workspace import GraphDocument, Node
    from app.services.workspace_bridge import EVENT_NODE_ID, merge_database
    req = mk(db, "光復居民", ["elderly"], lat=23.67, lng=121.42)
    far = mk(db, "台中居民", ["elderly"], lat=24.1, lng=120.6)
    db.add_all([
        CommunityNeed(requester_id=req.id, need_type="sos", lat=23.67, lng=121.42, urgency=5, status="open"),
        CommunityNeed(requester_id=far.id, need_type="water", urgency=3, status="open"),  # 沒座標
        CommunityNeed(requester_id=req.id, need_type="food", urgency=3, status="cancelled"),
        ResourcePoint(name="光復國小", point_type="shelter", lat=23.671, lng=121.425, is_active=True),
        ResourcePoint(name="台中活動中心", point_type="community", lat=24.1, lng=120.6, is_active=True),
    ])
    db.commit()
    flood = Node(id="s:flood", label="洪災", kind="incident", lat=23.684, lng=121.428)

    merged, _ = merge_database(GraphDocument(nodes=[flood]), db)
    bound = {e.target: e.label for e in merged.edges if e.source == "s:flood"}
    labels = {n.id: n.label for n in merged.nodes}
    assert sorted(labels[t] for t in bound) == ["光復國小", "光復居民 · 緊急求救", "台中居民 · 飲用水"]
    assert "緊急求救" in bound.values() and "範圍內資源點" in bound.values()

    # 沒有事件物件的工作區（即時營運現況）也不讓需求散落。
    merged, counts = merge_database(GraphDocument(), db)
    assert any(n.id == EVENT_NODE_ID for n in merged.nodes)
    assert sum(e.source == EVENT_NODE_ID for e in merged.edges) == 2
    again, counts = merge_database(merged, db)
    assert counts["removed"] == 0


# ═══════════════ 工作區：刪除不需要的區域 ═══════════════
def test_saved_workspace_can_be_deleted_but_built_in_scenarios_are_kept(db):
    from app.models.workspace import TopologyWorkspace
    from app.routers import workspace as workspace_router
    from app.services.workspace_scenarios import GUANGFU_WORKSPACE_ID, ensure_scenario_workspaces
    app = FastAPI()
    app.include_router(workspace_router.router, prefix="/api/workspaces")
    c = TestClient(app)
    ensure_scenario_workspaces(db)
    created = c.post("/api/workspaces", json={"name": "臺北市中心周邊 500 公尺", "graph": {"nodes": [], "edges": []}})
    assert created.status_code == 201, created.text
    wid = created.json()["id"]

    r = c.delete(f"/api/workspaces/{wid}")
    assert r.status_code == 200 and "臺北市中心周邊" in r.json()["message"]
    db.expire_all()
    assert db.get(TopologyWorkspace, wid) is None
    assert c.delete(f"/api/workspaces/{wid}").status_code == 404
    assert c.delete(f"/api/workspaces/{GUANGFU_WORKSPACE_ID}").status_code == 409


def test_cleanup_closes_the_stand_in_sos_so_the_console_stops_ringing(db, line_outbox):
    me = mk(db, "測試者", ["volunteer", "admin"], "U-me", lat=23.66, lng=121.42)
    c = client()
    assert c.post(f"/api/dashboard/rehearsal/family-alert?tester_id={me.id}&status=help_needed").status_code == 200
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    press("U-me", f"action=sos_go&need_id={need.id}")               # 演練到一半：已受理、還沒結案
    assert c.get("/api/dashboard/summary").json()["open_sos"]

    r = c.post("/api/dashboard/rehearsal/cleanup")
    assert r.status_code == 200, r.text
    db.expire_all()
    assert db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos", CommunityNeed.status == "open").count() == 0
    assert c.get("/api/dashboard/summary").json()["open_sos"] == [], "清掉演練後後台不能再響"
    assert not db.query(User).filter(User.name.like(rehearsal_kit.MARK + "%"), User.is_active == True).count()  # noqa: E712
