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
from app.services.workspace_inventory import row_version


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
    need = CommunityNeed(requester_id=elder.id, need_type="water", quantity="5箱", urgency=4, status="open",
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
    assert not person.logistics
    demand = by_id[f"db:need:{need.id}"].logistics[0]
    assert (demand.role, demand.item, demand.unit, demand.quantity, demand.priority) == ("demand", "飲用水", "箱", 5, 4)
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
    assert sum(len(n.logistics) for n in nodes if n.properties.get("db") == "need") == 1


def test_people_without_coordinates_are_included_but_unlocated_and_inactive_users_are_not(db):
    elder, vol, res, need, point = world(db)
    nolocation = mk_user(db, "沒定位的長者", ["elderly"])
    db.add(CommunityNeed(requester_id=nolocation.id, need_type="food", urgency=2, status="open")); db.commit()
    mk_user(db, "停用的長者", ["elderly"], 24.2, 120.7, is_active=False)
    nodes, _ = database_nodes(db)
    labels = {n.label: n for n in nodes}
    assert labels["沒定位的長者"].lat is None
    assert any(n.properties.get("requester_id") == f"db:person:{nolocation.id}" and n.lat is None for n in nodes)
    assert "停用的長者" not in labels


# ── 就地同步 ──
def test_merge_is_in_place_and_keeps_layout_manual_objects_and_access_edges(db):
    elder, vol, res, need, point = world(db)
    base = GraphDocument.model_validate({
        **road_graph(),
        "nodes": road_graph()["nodes"] + [{"id": "manual", "label": "手動加的", "kind": "custom", "lat": 24.02, "lng": 120.62}]})
    first, counts = merge_database(base, db)
    assert counts["added"] == 5 and counts["updated"] == 0 and counts["removed"] == 0
    person_id = f"db:person:{elder.id}"
    first.nodes = [n if n.id != person_id else n.model_copy(update={"properties": {**n.properties, "_layout": {"x": 5, "y": 9}}})
                   for n in first.nodes]
    first.edges.append(type(first.edges[0]).model_validate(access(person_id, "r2", "acc1")))
    elder.name = "王奶奶（已改名）"
    db.commit()
    second, counts = merge_database(first, db)
    assert counts["added"] == 0 and counts["updated"] == 5 and counts["removed"] == 0
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
    assert body["counts"]["supplies"] == 1 and len(body["graph"]["nodes"]) == 7
    assert GraphDocument.model_validate(body["graph"])


# ── 整條迴路：資料庫 → 試算 → 派遣建議 → 確認 ──
def _plan(api, db):
    elder, vol, res, need, point = world(db)
    merged = api.post("/api/workspaces/database-merge", json={"graph": road_graph()}).json()["graph"]
    merged["edges"] += [access(f"db:need:{need.id}", "r2", "a1"), access(f"db:res:{res.id}", "r1", "a2")]
    plan = api.post("/api/workspaces/allocate", json={"graph": merged, "item": "飲用水", "unit": "箱", "max_minutes": 240})
    assert plan.status_code == 200, plan.text
    return elder, vol, res, need, plan.json()


def assignment(resource, need, quantity=5):
    return {"supply_id": f"db:res:{resource.id}", "demand_id": f"db:need:{need.id}", "quantity": quantity,
            "supply_version": row_version(resource), "demand_version": row_version(need)}


def test_allocation_over_real_data_becomes_a_pending_suggestion_an_admin_can_confirm(db, api):
    elder, vol, res, need, plan = _plan(api, db)
    after = plan["after"]
    assert after["summary"]["allocated"] == 5 and after["assignments"][0]["supply_id"] == f"db:res:{res.id}"

    applied = api.post("/api/workspaces/apply-allocation", json={"assignments": [
        assignment(res, need, a["quantity"]) for a in after["assignments"]]})
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
        assignment(res, need)]})
    event = db.query(DispatchEvent).filter(DispatchEvent.action == "propose_dispatch").one()
    assert event.outcome == "suggested" and "workspace_allocation" in event.details_json
    assert not line_outbox.sent, "建議階段不能通知任何人"


def test_apply_allocation_skips_what_it_cannot_do_and_says_why(db, api):
    elder, vol, res, need, plan = _plan(api, db)
    other = CommunityResource(owner_id=vol.id, resource_type="water", name="第二份", quantity="9箱", is_available=True)
    db.add(other); db.commit(); db.refresh(other)
    body = {"assignments": [
        {"supply_id": "manual-supply", "demand_id": f"db:need:{need.id}", "quantity": 1},
        assignment(res, need, 5),
        assignment(other, need, 1),
        {"supply_id": "db:res:not-a-uuid", "demand_id": "db:need:also-bad", "quantity": 1}]}
    out = api.post("/api/workspaces/apply-allocation", json=body).json()
    assert len(out["proposed"]) == 1
    reasons = " | ".join(s["reason"] for s in out["skipped"])
    assert "不是資料庫來源" in reasons and "同一筆需求" in reasons and "找不到對應" in reasons
    # 已經有建議的需求不能再建一次
    again = api.post("/api/workspaces/apply-allocation", json={"assignments": [
        assignment(other, need)]}).json()
    assert not again["proposed"] and "版本" in again["skipped"][0]["reason"]


def test_self_match_and_sos_are_refused(db, api):
    elder, vol, res, need, point = world(db)
    own = CommunityResource(owner_id=elder.id, resource_type="water", name="自己的水", quantity="5箱", is_available=True)
    sos = CommunityNeed(requester_id=elder.id, need_type="sos", urgency=5, status="open")
    db.add_all([own, sos]); db.commit(); db.refresh(own); db.refresh(sos)
    out = api.post("/api/workspaces/apply-allocation", json={"assignments": [
        assignment(own, need), assignment(res, sos)]}).json()
    assert not out["proposed"]
    assert any("自己的物資" in s["reason"] for s in out["skipped"]) and any("不一致" in s["reason"] for s in out["skipped"])


def inventory_change(row, **values):
    prefix = "db:res:" if isinstance(row, CommunityResource) else "db:point:"
    return {"node_id": prefix + str(row.id), "expected_version": row_version(row), "values": values}


def test_projection_has_unique_identities_care_ownership_and_separate_need_locations(db, api, line_outbox):
    from datetime import date
    from app.models.care_relation import CareRelation
    from app.models.checkin import DailyCheckin
    elder, vol, res, need, point = world(db)
    need.lat, need.lng = 24.03, 120.63
    db.add(CareRelation(elderly_id=elder.id, contact_id=vol.id, relation="volunteer"))
    db.add(DailyCheckin(elderly_id=elder.id, date=date.today(), status="help_needed", note="需要送水"))
    db.commit()
    first = api.get("/api/workspaces/operational-data").json()
    second = api.post("/api/workspaces/database-merge", json={"graph": first["graph"]}).json()
    graph = GraphDocument.model_validate(second["graph"])
    assert second["counts"]["added"] == second["counts"]["removed"] == 0
    assert len(graph.nodes) == len({n.id for n in graph.nodes}) == 5
    assert len(graph.edges) == len({e.id for e in graph.edges}) == 3
    nodes = {n.id: n for n in graph.nodes}
    assert nodes[f"db:person:{elder.id}"].lat == 24.01
    assert nodes[f"db:need:{need.id}"].lat == 24.03
    assert nodes[f"db:person:{elder.id}"].properties["checkin"]["note"] == "需要送水"
    assert not line_outbox.sent
    assert db.query(CommunityResource).count() == db.query(CommunityNeed).count() == 1


def test_unit_mismatch_and_unknown_quantity_never_become_allocatable_stock(db, api):
    elder, vol, res, need, point = world(db)
    need.quantity = "5瓶"
    db.commit()
    graph, _ = merge_database(GraphDocument.model_validate(road_graph()), db)
    graph.edges += [type(graph.edges[0]).model_validate(access(f"db:need:{need.id}", "r2", "a1")),
                    type(graph.edges[0]).model_validate(access(f"db:res:{res.id}", "r1", "a2"))]
    plan = api.post("/api/workspaces/allocate", json={"graph": graph.model_dump(), "item": "飲用水", "unit": "瓶"}).json()
    assert plan["after"]["summary"]["allocated"] == 0
    res.quantity = "約20箱（每箱24瓶）"
    db.commit()
    nodes, _ = database_nodes(db)
    stock = next(n for n in nodes if n.id == f"db:res:{res.id}")
    assert not stock.logistics and stock.quantity == 0 and not stock.properties["quantity_verified"]


def test_inventory_preview_is_read_only_and_apply_is_audited(db, api, line_outbox):
    from app.models.dispatch_event import DispatchEvent
    elder, vol, res, need, point = world(db)
    body = {"changes": [inventory_change(res, quantity="12箱"), inventory_change(point, lat=24.4, lng=120.4)]}
    preview = api.post("/api/workspaces/database-diff", json=body)
    assert preview.json()["can_apply"] and len(preview.json()["changes"]) == 2
    db.refresh(res)
    assert res.quantity == "20箱" and db.query(DispatchEvent).count() == 0
    applied = api.post("/api/workspaces/database-push", json=body)
    assert applied.status_code == 200, applied.text
    db.refresh(res); db.refresh(point)
    assert res.quantity == "12箱" and point.lat == 24.4
    assert db.query(DispatchEvent).filter_by(action="workspace_inventory").count() == 2
    again = api.post("/api/workspaces/database-push", json=body)
    assert again.status_code == 409
    assert db.query(DispatchEvent).count() == 2 and not line_outbox.sent


def test_stale_inventory_without_timestamp_change_rejects_entire_batch(db, api):
    from app.models.dispatch_event import DispatchEvent
    elder, vol, res, need, point = world(db)
    body = {"changes": [inventory_change(point, lat=24.4, lng=120.4), inventory_change(res, quantity="12箱")]}
    assert api.post("/api/workspaces/database-diff", json=body).json()["can_apply"]
    old_stamp = res.last_updated
    res.quantity = "8箱"
    db.commit()
    assert res.last_updated == old_stamp
    response = api.post("/api/workspaces/database-push", json=body)
    assert response.status_code == 409
    db.refresh(point); db.refresh(res)
    assert point.lat == 24.005 and res.quantity == "8箱" and db.query(DispatchEvent).count() == 0


def test_duplicate_and_unsupported_inventory_fields_are_rejected(db, api):
    elder, vol, res, need, point = world(db)
    change = inventory_change(res, quantity="12箱")
    assert api.post("/api/workspaces/database-push", json={"changes": [change, change]}).status_code == 422
    assert api.post("/api/workspaces/database-push", json={"changes": [inventory_change(res, owner_id=str(elder.id))]}).status_code == 422
    assert api.post("/api/workspaces/database-push", json={"changes": [inventory_change(point, quantity="5份")]}).status_code == 400
    assert api.post("/api/workspaces/database-push", json={"changes": [inventory_change(res, lat=None)]}).status_code == 400


def test_promoting_same_scenario_object_twice_creates_one_resource(db, api):
    from app.models.dispatch_event import DispatchEvent
    elder, vol, res, need, point = world(db)
    body = {"changes": [{"node_id": "scenario-stock-123", "operation": "create", "owner_id": str(vol.id),
                         "creation_key": "fb501f6d-09d5-4d46-8470-8c8496870cd6",
                         "resource_type": "food", "values": {"name": "乾糧", "quantity": "30包", "lat": 24.1, "lng": 120.1}}]}
    first = api.post("/api/workspaces/database-push", json=body)
    second = api.post("/api/workspaces/database-push", json=body)
    assert first.status_code == second.status_code == 200
    assert first.json()["applied"][0]["database_id"] == second.json()["applied"][0]["database_id"]
    assert second.json()["applied"][0]["operation"] == "already_applied"
    assert db.query(CommunityResource).count() == 2 and db.query(DispatchEvent).count() == 1
    body["changes"][0]["node_id"] = "renamed-object-in-a-copy"
    assert api.post("/api/workspaces/database-push", json=body).json()["applied"][0]["operation"] == "already_applied"
    body["changes"][0]["node_id"] = "scenario-stock-123"
    body["changes"][0]["creation_key"] = "356cbf84-3e29-4dc9-89cb-61d087de1d68"
    assert api.post("/api/workspaces/database-push", json=body).status_code == 200
    assert db.query(CommunityResource).count() == 3


def test_reserved_resource_cannot_be_reenabled_or_recounted_by_workspace(db, api):
    elder, vol, res, need, point = world(db)
    assert api.post("/api/workspaces/apply-allocation", json={"assignments": [assignment(res, need)]}).json()["proposed"]
    db.refresh(res)
    body = {"changes": [inventory_change(res, quantity="100箱", is_available=True)]}
    assert not api.post("/api/workspaces/database-diff", json=body).json()["can_apply"]
    assert api.post("/api/workspaces/database-push", json=body).status_code == 409
    assert api.put(f"/api/resources/{res.id}", params={"quantity": "100箱", "is_available": True}).status_code == 409
    assert api.patch(f"/api/resources/{res.id}/toggle").status_code == 409
    assert api.delete(f"/api/resources/{res.id}").status_code == 409


def test_stale_approval_and_decline_do_not_change_a_new_suggestion(db, api, line_outbox):
    elder, vol, res, need, point = world(db)
    api.post("/api/workspaces/apply-allocation", json={"assignments": [assignment(res, need)]})
    db.refresh(need)
    version = row_version(need)
    need.description = "更新後的需求說明"
    db.commit()
    for action in ("confirm_dispatch", "decline_suggestion"):
        assert api.post(f"/api/resources/needs/{need.id}/{action}", params={"expected_version": version}).status_code == 409
    db.refresh(need); db.refresh(res)
    assert need.status == "suggested" and not res.is_available and not line_outbox.sent


def test_partial_or_stale_allocation_never_creates_full_dispatch(db, api, line_outbox):
    elder, vol, res, need, point = world(db)
    partial = api.post("/api/workspaces/apply-allocation", json={"assignments": [assignment(res, need, 2)]}).json()
    assert not partial["proposed"] and "部分" in partial["skipped"][0]["reason"]
    stale = assignment(res, need)
    res.quantity = "3箱"
    db.commit()
    assert not api.post("/api/workspaces/apply-allocation", json={"assignments": [stale]}).json()["proposed"]
    db.refresh(need); db.refresh(res)
    assert need.status == "open" and res.is_available and not line_outbox.sent


def test_workflow_approval_report_and_refresh_keep_same_identity(db, api, line_outbox):
    from app.services.dispatch import report_task
    from app.models.dispatch_event import DispatchEvent
    elder, vol, res, need, point = world(db)
    vol.line_uid = "U-integrated-volunteer"
    db.commit()
    before_id = f"db:need:{need.id}"
    payload = {"assignments": [assignment(res, need)]}
    assert len(api.post("/api/workspaces/apply-allocation", json=payload).json()["proposed"]) == 1
    assert not api.post("/api/workspaces/apply-allocation", json=payload).json()["proposed"]
    db.refresh(need)
    url = f"/api/resources/needs/{need.id}/confirm_dispatch?expected_version={row_version(need)}"
    assert api.post(url).status_code == 200
    notifications = len(line_outbox.sent)
    assert notifications > 0 and api.post(url).status_code == 409 and len(line_outbox.sent) == notifications
    db.refresh(need)
    result = report_task(str(need.id), db, outcome="delivered", note="已送達門口", actor_id=str(vol.id))
    assert not result.get("error"), result
    snapshot = api.get("/api/workspaces/operational-data").json()
    task = next(t for t in snapshot["tasks"] if t["node_id"] == before_id)
    assert task["status"] == "fulfilled"
    assert not next(n for n in snapshot["graph"]["nodes"] if n["id"] == before_id)["logistics"]
    assert db.query(DispatchEvent).filter_by(action="confirm_dispatch").count() == 1
    assert any(e["details"].get("note") == "已送達門口" for e in api.get(f"/api/resources/needs/{need.id}/events").json())


@pytest.mark.parametrize("entry_points", [("workspace", "workspace"), ("workspace", "manual"), ("manual", "manual")])
def test_two_concurrent_proposals_reserve_a_resource_once(db, entry_points):
    from concurrent.futures import ThreadPoolExecutor
    from threading import Barrier
    from app.database import SessionLocal
    from app.services.dispatch import propose_manual, manual_dispatch
    from app.models.dispatch_event import DispatchEvent
    elder, vol, res, need, point = world(db)
    other = CommunityNeed(requester_id=elder.id, need_type="water", quantity="5箱", status="open")
    db.add(other); db.commit()
    ids, resource_id = [str(need.id), str(other.id)], str(res.id)
    barrier = Barrier(2)
    def propose(item):
        need_id, entry_point = item
        with SessionLocal() as session:
            cached = [session.get(CommunityNeed, need_id), session.get(CommunityResource, resource_id)]
            barrier.wait(timeout=10)
            return (propose_manual if entry_point == "workspace" else manual_dispatch)(need_id, resource_id, session)
    with ThreadPoolExecutor(max_workers=2) as pool:
        results = list(pool.map(propose, zip(ids, entry_points)))
    assert sum(not result.get("error") for result in results) == 1
    db.expire_all()
    assert db.query(CommunityNeed).filter(CommunityNeed.status.in_(("suggested", "matched"))).count() == 1
    assert db.query(DispatchEvent).filter(DispatchEvent.action.in_(("propose_dispatch", "manual_dispatch"))).count() == 1


def test_automatic_dispatch_cannot_reuse_a_resource_claimed_after_its_plan(db, monkeypatch):
    from app.database import SessionLocal
    from app.models.config import SystemConfig
    from app.services import dispatch
    elder, vol, res, need, point = world(db)
    point.is_active = False
    db.add(SystemConfig(key="mode", value="emergency")); db.commit()
    elder_id, resource_id = str(elder.id), str(res.id)
    plan = dispatch._assign_resources_optimally
    def interleave(needs, session):
        result = plan(needs, session)
        assert result
        with SessionLocal() as other_session:
            other = CommunityNeed(requester_id=elder_id, need_type="water", quantity="5箱", status="open")
            other_session.add(other); other_session.commit()
            assert not dispatch.propose_manual(str(other.id), resource_id, other_session).get("error")
        return result
    monkeypatch.setattr(dispatch, "_assign_resources_optimally", interleave)
    result = dispatch.auto_dispatch()
    assert result["suggested"] == 0 and result["skipped"] == 1
    db.expire_all()
    assert db.query(CommunityNeed).filter(CommunityNeed.status.in_(("suggested", "matched"))).count() == 1
