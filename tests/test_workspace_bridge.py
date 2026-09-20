# -*- coding: utf-8 -*-
"""開放資料工作區與平台資料庫的連接：資料庫 → 地圖 → 分配試算 → 派遣建議 → 管理員確認。"""
import pytest
from fastapi.testclient import TestClient

from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.services.workspace import GraphDocument, Node
from app.services.workspace_bridge import database_nodes, merge_database


@pytest.fixture()
def api():
    from app.main import app
    return TestClient(app)


def mk_user(db, name, roles, lat=None, lng=None, **kw):
    user = User(name=name, roles=roles, lat=lat, lng=lng, **kw)
    db.add(user); db.commit(); db.refresh(user)
    return user


def world(db):
    elder = mk_user(db, "王奶奶", ["elderly"], 24.0100, 120.6100, address="台中市南區")
    vol = mk_user(db, "志工甲", ["volunteer"], 24.0000, 120.6000)
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="礦泉水", quantity="20箱",
                            lat=24.0000, lng=120.6000, is_available=True)
    need = CommunityNeed(requester_id=elder.id, need_type="water", quantity="5", urgency=4, status="open",
                         lat=24.0100, lng=120.6100)
    point = ResourcePoint(name="南區活動中心", point_type="community", lat=24.005, lng=120.605, is_active=True)
    db.add_all([res, need, point]); db.commit()
    for row in (res, need, point):
        db.refresh(row)
    return elder, vol, res, need, point


def road_graph():
    """Two road nodes joined by a road, placed so each object is closest to one end."""
    return {"nodes": [
        {"id": "r1", "label": "路口A", "kind": "road_node", "lat": 24.0000, "lng": 120.6000},
        {"id": "r2", "label": "路口B", "kind": "road_node", "lat": 24.0100, "lng": 120.6100}],
        "edges": [{"id": "e1", "source": "r1", "target": "r2", "kind": "road", "label": "道路", "speed_kph": 40}]}


def access(source, target, edge_id):
    return {"id": edge_id, "source": source, "target": target, "kind": "access", "label": "估計接駁",
            "speed_kph": 5, "multiplier": 2.5}


# ── 資料庫 → 工作區物件 ──
def test_database_objects_become_workspace_nodes_with_matching_items(db):
    elder, vol, res, need, point = world(db)
    nodes, counts = database_nodes(db)
    by_id = {n.id: n for n in nodes}
    person = by_id[f"db:person:{elder.id}"]
    assert person.kind == "person" and (person.lat, person.lng) == (24.01, 120.61)
    demand = person.logistics[0]
    assert (demand.role, demand.item, demand.unit, demand.quantity, demand.priority) == ("demand", "飲用水", "份", 5, 4)
    assert demand.id == f"db:need:{need.id}"
    supply = by_id[f"db:res:{res.id}"]
    assert supply.kind == "supply" and supply.logistics[0].quantity == 20, "「20箱」要取出 20"
    assert (supply.logistics[0].item, supply.logistics[0].unit) == (demand.item, demand.unit), "供應與需求品項單位要一致才配得到"
    assert by_id[f"db:point:{point.id}"].kind == "facility"
    assert counts == {"elders": 1, "demands": 1, "supplies": 1, "points": 1}


def test_sos_matched_and_cancelled_needs_are_not_demand(db):
    elder, vol, res, need, point = world(db)
    for kind, status in (("sos", "open"), ("water", "matched"), ("food", "cancelled"), ("water", "fulfilled")):
        db.add(CommunityNeed(requester_id=elder.id, need_type=kind, status=status, urgency=3))
    db.commit()
    nodes, counts = database_nodes(db)
    assert counts["demands"] == 1
    assert len(next(n for n in nodes if n.kind == "person").logistics) == 1


def test_people_without_coordinates_are_included_but_unlocated_and_inactive_users_are_not(db):
    elder, vol, res, need, point = world(db)
    nolocation = mk_user(db, "沒定位的長者", ["elderly"])
    db.add(CommunityNeed(requester_id=nolocation.id, need_type="food", urgency=2, status="open")); db.commit()
    mk_user(db, "停用的長者", ["elderly"], 24.2, 120.7, is_active=False)
    nodes, _ = database_nodes(db)
    labels = {n.label: n for n in nodes}
    assert labels["沒定位的長者"].lat is None and labels["沒定位的長者"].logistics
    assert "停用的長者" not in labels


# ── 就地同步 ──
def test_merge_is_in_place_and_keeps_layout_manual_objects_and_access_edges(db):
    elder, vol, res, need, point = world(db)
    base = GraphDocument.model_validate({
        **road_graph(),
        "nodes": road_graph()["nodes"] + [{"id": "manual", "label": "手動加的", "kind": "custom", "lat": 24.02, "lng": 120.62}]})
    first, counts = merge_database(base, db)
    assert counts["added"] == 3 and counts["updated"] == 0 and counts["removed"] == 0
    person_id = f"db:person:{elder.id}"
    first.nodes = [n if n.id != person_id else n.model_copy(update={"properties": {**n.properties, "_layout": {"x": 5, "y": 9}}})
                   for n in first.nodes]
    first.edges.append(type(first.edges[0]).model_validate(access(person_id, "r2", "acc1")))
    elder.name = "王奶奶（已改名）"
    db.commit()
    second, counts = merge_database(first, db)
    assert counts["added"] == 0 and counts["updated"] == 3 and counts["removed"] == 0
    person = next(n for n in second.nodes if n.id == person_id)
    assert person.label == "王奶奶（已改名）" and person.properties["_layout"] == {"x": 5, "y": 9}
    assert any(e.id == "acc1" for e in second.edges), "接駁連線不能因為同步而消失"
    assert any(n.id == "manual" for n in second.nodes)


def test_objects_deleted_from_the_database_leave_the_map_with_their_edges(db):
    elder, vol, res, need, point = world(db)
    graph, _ = merge_database(GraphDocument.model_validate(road_graph()), db)
    supply_id = f"db:res:{res.id}"
    graph.edges.append(type(graph.edges[0]).model_validate(access(supply_id, "r1", "acc-s")))
    db.delete(db.query(CommunityResource).filter(CommunityResource.id == res.id).one()); db.commit()
    merged, counts = merge_database(graph, db)
    assert counts["removed"] == 1
    assert all(n.id != supply_id for n in merged.nodes) and all(e.id != "acc-s" for e in merged.edges)
    assert any(n.id == "r1" for n in merged.nodes), "路網不能被誤刪"


def test_merge_endpoint_returns_a_valid_graph(db, api):
    world(db)
    r = api.post("/api/workspaces/database-merge", json={"graph": road_graph()})
    assert r.status_code == 200, r.text
    body = r.json()
    assert body["counts"]["supplies"] == 1 and len(body["graph"]["nodes"]) == 5
    assert GraphDocument.model_validate(body["graph"])


# ── 整條迴路：資料庫 → 試算 → 派遣建議 → 確認 ──
def _plan(api, db):
    elder, vol, res, need, point = world(db)
    merged = api.post("/api/workspaces/database-merge", json={"graph": road_graph()}).json()["graph"]
    merged["edges"] += [access(f"db:person:{elder.id}", "r2", "a1"), access(f"db:res:{res.id}", "r1", "a2")]
    plan = api.post("/api/workspaces/allocate", json={"graph": merged, "item": "飲用水", "unit": "份", "max_minutes": 240})
    assert plan.status_code == 200, plan.text
    return elder, vol, res, need, plan.json()


def test_allocation_over_real_data_becomes_a_pending_suggestion_an_admin_can_confirm(db, api):
    elder, vol, res, need, plan = _plan(api, db)
    after = plan["after"]
    assert after["summary"]["allocated"] == 5 and after["assignments"][0]["supply_id"] == f"db:res:{res.id}"

    applied = api.post("/api/workspaces/apply-allocation", json={"assignments": [
        {"supply_id": a["supply_id"], "demand_id": a["demand_id"], "quantity": a["quantity"]} for a in after["assignments"]]})
    assert applied.status_code == 200 and len(applied.json()["proposed"]) == 1 and not applied.json()["skipped"]
    db.expire_all()
    n = db.query(CommunityNeed).filter(CommunityNeed.id == need.id).one()
    assert n.status == "suggested" and str(n.matched_resource_id) == str(res.id)
    assert db.query(CommunityResource).filter(CommunityResource.id == res.id).one().is_available is False

    confirmed = api.post(f"/api/resources/needs/{need.id}/confirm_dispatch")
    assert confirmed.status_code == 200, confirmed.text
    db.expire_all()
    assert db.query(CommunityNeed).filter(CommunityNeed.id == need.id).one().status == "matched"


def test_suggestion_is_audited_reserves_the_resource_and_notifies_nobody(db, api, line_outbox):
    from app.models.dispatch_event import DispatchEvent
    elder, vol, res, need, plan = _plan(api, db)
    api.post("/api/workspaces/apply-allocation", json={"assignments": [
        {"supply_id": f"db:res:{res.id}", "demand_id": f"db:need:{need.id}", "quantity": 5}]})
    event = db.query(DispatchEvent).filter(DispatchEvent.action == "propose_dispatch").one()
    assert event.outcome == "suggested" and "workspace_allocation" in event.details_json
    assert not line_outbox.sent, "建議階段不能通知任何人"


def test_apply_allocation_skips_what_it_cannot_do_and_says_why(db, api):
    elder, vol, res, need, plan = _plan(api, db)
    other = CommunityResource(owner_id=vol.id, resource_type="water", name="第二份", quantity="9", is_available=True)
    db.add(other); db.commit(); db.refresh(other)
    body = {"assignments": [
        {"supply_id": "manual-supply", "demand_id": f"db:need:{need.id}", "quantity": 1},
        {"supply_id": f"db:res:{res.id}", "demand_id": f"db:need:{need.id}", "quantity": 4},
        {"supply_id": f"db:res:{other.id}", "demand_id": f"db:need:{need.id}", "quantity": 1},
        {"supply_id": "db:res:not-a-uuid", "demand_id": "db:need:also-bad", "quantity": 1}]}
    out = api.post("/api/workspaces/apply-allocation", json=body).json()
    assert len(out["proposed"]) == 1
    reasons = " | ".join(s["reason"] for s in out["skipped"])
    assert "不是資料庫來源" in reasons and "同一筆需求" in reasons and "找不到對應" in reasons
    # 已經有建議的需求不能再建一次
    again = api.post("/api/workspaces/apply-allocation", json={"assignments": [
        {"supply_id": f"db:res:{other.id}", "demand_id": f"db:need:{need.id}", "quantity": 1}]}).json()
    assert not again["proposed"] and "只有待媒合" in again["skipped"][0]["reason"]


def test_self_match_and_sos_are_refused(db, api):
    elder, vol, res, need, point = world(db)
    own = CommunityResource(owner_id=elder.id, resource_type="water", name="自己的水", quantity="3", is_available=True)
    sos = CommunityNeed(requester_id=elder.id, need_type="sos", urgency=5, status="open")
    db.add_all([own, sos]); db.commit(); db.refresh(own); db.refresh(sos)
    out = api.post("/api/workspaces/apply-allocation", json={"assignments": [
        {"supply_id": f"db:res:{own.id}", "demand_id": f"db:need:{need.id}", "quantity": 1},
        {"supply_id": f"db:res:{res.id}", "demand_id": f"db:need:{sos.id}", "quantity": 1}]}).json()
    assert not out["proposed"]
    assert any("自己的物資" in s["reason"] for s in out["skipped"]) and any("緊急求助" in s["reason"] for s in out["skipped"])
