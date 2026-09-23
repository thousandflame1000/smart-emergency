# -*- coding: utf-8 -*-
"""分區（zone）架構：general 是保底分區，自動媒合/志工接單只在同分區內配對，
管理員手動指派可以跨分區（人工判斷已經是監督動作），工作區快照依分區篩選，
分區改派走跟其他寫入一樣的樂觀鎖機制，不會被同時發生的操作悄悄蓋掉。"""
import pytest
from fastapi.testclient import TestClient

from app.models.config import SystemConfig
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.models.zone import GENERAL_ZONE_ID, Zone
from app.services import dispatch, zones as zone_service


@pytest.fixture()
def api():
    from app.main import app
    return TestClient(app)


def mk_user(db, name, roles, **kw):
    user = User(name=name, roles=roles, **kw)
    db.add(user); db.commit(); db.refresh(user)
    return user


def mk_resource(db, owner, zone_id=GENERAL_ZONE_ID, **kw):
    res = CommunityResource(owner_id=owner.id, resource_type="water", name="水",
                            quantity="20箱", is_available=True, zone_id=zone_id, **kw)
    db.add(res); db.commit(); db.refresh(res)
    return res


def mk_need(db, requester, zone_id=GENERAL_ZONE_ID, **kw):
    need = CommunityNeed(requester_id=requester.id, need_type="water", urgency=4,
                         status="open", zone_id=zone_id, **kw)
    db.add(need); db.commit(); db.refresh(need)
    return need


# ═══════════════ general 保底分區 ═══════════════
def test_general_zone_exists_and_is_the_default(db):
    assert db.get(Zone, GENERAL_ZONE_ID) is not None
    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="水", is_available=True)
    db.add(res); db.commit(); db.refresh(res)
    assert res.zone_id == GENERAL_ZONE_ID

    elder = mk_user(db, "長者", ["elderly"], lat=24.0, lng=120.6)
    need = CommunityNeed(requester_id=elder.id, need_type="water", status="open")
    db.add(need); db.commit(); db.refresh(need)
    assert need.zone_id == GENERAL_ZONE_ID


def test_ensure_general_zone_is_idempotent(db):
    zone_service.ensure_general_zone(db)
    zone_service.ensure_general_zone(db)
    assert db.query(Zone).filter(Zone.id == GENERAL_ZONE_ID).count() == 1


# ═══════════════ 自動媒合只在同一分區內配對 ═══════════════
def test_auto_dispatch_does_not_cross_zones(db):
    zone_service.create_zone(db, "分區A")
    zone_a = db.query(Zone).filter(Zone.name == "分區A").one().id

    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    elder = mk_user(db, "長者", ["elderly"], lat=24.001, lng=120.601)
    mk_resource(db, vol, zone_id=zone_a, lat=24.0, lng=120.6)
    need = mk_need(db, elder, zone_id=GENERAL_ZONE_ID, lat=24.001, lng=120.601)

    db.add(SystemConfig(key="mode", value="emergency")); db.commit()

    result = dispatch.auto_dispatch()
    assert result["suggested"] == 0, "物資在分區A、需求在 general，不該被配對"

    db.refresh(need)
    assert need.status == "open"


def test_auto_dispatch_matches_within_the_same_zone(db):
    zone_service.create_zone(db, "分區B")
    zone_b = db.query(Zone).filter(Zone.name == "分區B").one().id

    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    elder = mk_user(db, "長者", ["elderly"], lat=24.001, lng=120.601)
    mk_resource(db, vol, zone_id=zone_b, lat=24.0, lng=120.6)
    need = mk_need(db, elder, zone_id=zone_b, lat=24.001, lng=120.601)

    db.add(SystemConfig(key="mode", value="emergency")); db.commit()

    result = dispatch.auto_dispatch()
    assert result["suggested"] == 1

    db.refresh(need)
    assert need.status == "suggested"


# ═══════════════ 志工自行接單（LINE「接單」）也不能跨分區 ═══════════════
def test_list_claimable_hides_needs_from_other_zones(db):
    zone_service.create_zone(db, "分區C")
    zone_c = db.query(Zone).filter(Zone.name == "分區C").one().id

    vol = mk_user(db, "志工", ["volunteer"], line_uid="U-vol", lat=24.0, lng=120.6)
    elder = mk_user(db, "長者", ["elderly"], lat=24.0, lng=120.6)
    mk_resource(db, vol, zone_id=zone_c)
    mk_need(db, elder, zone_id=GENERAL_ZONE_ID)

    found = dispatch.list_claimable(vol, db)
    assert found["items"] == [], "物資在分區C、需求在 general，不該出現在可接清單"


def test_claim_need_rejects_a_cross_zone_attempt_even_if_forced(db):
    zone_service.create_zone(db, "分區D")
    zone_d = db.query(Zone).filter(Zone.name == "分區D").one().id

    vol = mk_user(db, "志工", ["volunteer"], line_uid="U-vol", lat=24.0, lng=120.6)
    elder = mk_user(db, "長者", ["elderly"], lat=24.0, lng=120.6)
    mk_resource(db, vol, zone_id=zone_d)
    need = mk_need(db, elder, zone_id=GENERAL_ZONE_ID)

    result = dispatch.claim_need(str(need.id), vol, db)
    assert "error" in result, "就算志工手動打 claim_need，跨分區也不該成立"


# ═══════════════ 管理員手動指派：監督動作，允許跨分區 ═══════════════
def test_manual_dispatch_is_allowed_to_cross_zones(db):
    zone_service.create_zone(db, "分區E")
    zone_e = db.query(Zone).filter(Zone.name == "分區E").one().id

    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    elder = mk_user(db, "長者", ["elderly"], lat=24.0, lng=120.6)
    res = mk_resource(db, vol, zone_id=zone_e)
    need = mk_need(db, elder, zone_id=GENERAL_ZONE_ID)

    result = dispatch.manual_dispatch(str(need.id), str(res.id), db)
    assert "error" not in result, "管理員手動指派是監督動作，允許跨分區調度"


# ═══════════════ 工作區快照依分區篩選 ═══════════════
def test_operational_snapshot_filters_by_zone(db):
    from app.services.workspace_bridge import operational_snapshot

    zone_service.create_zone(db, "分區F")
    zone_f = db.query(Zone).filter(Zone.name == "分區F").one().id

    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    res_general = mk_resource(db, vol, zone_id=GENERAL_ZONE_ID)
    res_f = mk_resource(db, vol, zone_id=zone_f)

    snapshot_all = operational_snapshot(db)
    ids_all = {n["id"] for n in snapshot_all["graph"]["nodes"]}
    assert f"db:res:{res_general.id}" in ids_all and f"db:res:{res_f.id}" in ids_all

    snapshot_f = operational_snapshot(db, zone_id=zone_f)
    ids_f = {n["id"] for n in snapshot_f["graph"]["nodes"]}
    assert f"db:res:{res_f.id}" in ids_f
    assert f"db:res:{res_general.id}" not in ids_f, "分區篩選後不該看到其他分區的物資"


# ═══════════════ 分區改派：跟其他寫入共用同一套樂觀鎖 ═══════════════
def test_reassign_resource_zone_uses_the_same_optimistic_lock(db):
    from app.database import SessionLocal

    zone_service.create_zone(db, "分區G")
    zone_g = db.query(Zone).filter(Zone.name == "分區G").one().id

    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    res = mk_resource(db, vol, zone_id=GENERAL_ZONE_ID)

    # 開第二個 session 模擬另一個並行請求，各自讀到改派前的那份資料。
    other_db = SessionLocal()
    try:
        stale = other_db.query(CommunityResource).filter(CommunityResource.id == res.id).first()

        zone_service.reassign_resource_zone(db, res, zone_g)
        db.refresh(res)
        assert res.zone_id == zone_g

        with pytest.raises(zone_service.ZoneError):
            # `stale` 是另一個 session 在改派前讀到的快照；這一列已經被上面
            # 那次改派動過，row_predicates 比對不上，必須乾淨地失敗，不能
            # 悄悄把剛才的改派蓋掉。
            zone_service.reassign_resource_zone(other_db, stale, GENERAL_ZONE_ID)
    finally:
        other_db.close()


def test_reassign_to_a_nonexistent_zone_is_rejected(db):
    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    res = mk_resource(db, vol)
    with pytest.raises(zone_service.ZoneError):
        zone_service.reassign_resource_zone(db, res, "no-such-zone")


# ═══════════════ 分區 CRUD ═══════════════
def test_create_zone_rejects_duplicate_names(db):
    zone_service.create_zone(db, "分區H")
    with pytest.raises(zone_service.ZoneError):
        zone_service.create_zone(db, "分區H")


def test_general_zone_cannot_be_deleted(db):
    with pytest.raises(zone_service.ZoneError):
        zone_service.delete_zone(db, GENERAL_ZONE_ID)


def test_zone_with_data_cannot_be_deleted(db):
    zone = zone_service.create_zone(db, "分區I")
    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    mk_resource(db, vol, zone_id=zone["id"])
    with pytest.raises(zone_service.ZoneError):
        zone_service.delete_zone(db, zone["id"])


def test_empty_zone_can_be_deleted(db):
    zone = zone_service.create_zone(db, "分區J")
    zone_service.delete_zone(db, zone["id"])
    assert db.get(Zone, zone["id"]) is None


def test_list_zones_reports_counts(db):
    zone = zone_service.create_zone(db, "分區K")
    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    elder = mk_user(db, "長者", ["elderly"], lat=24.0, lng=120.6)
    mk_resource(db, vol, zone_id=zone["id"])
    mk_need(db, elder, zone_id=zone["id"])

    listed = {z["id"]: z for z in zone_service.list_zones(db)}
    assert listed[GENERAL_ZONE_ID]["is_general"] is True
    assert listed[zone["id"]]["resource_count"] == 1
    assert listed[zone["id"]]["need_count"] == 1


# ═══════════════ API 層：/api/zones ═══════════════
def test_zones_api_create_list_and_delete(api, db):
    created = api.post("/api/zones", params={"name": "分區L"})
    assert created.status_code == 201
    zone_id = created.json()["id"]

    listed = api.get("/api/zones").json()
    assert any(z["id"] == zone_id for z in listed)

    assert api.delete(f"/api/zones/{zone_id}").status_code == 200
    assert api.delete(f"/api/zones/{GENERAL_ZONE_ID}").status_code == 409


def test_resources_api_accepts_and_reports_zone_id(api, db):
    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    zone = zone_service.create_zone(db, "分區M")

    created = api.post("/api/resources/", params={
        "resource_type": "water", "name": "水", "owner_id": str(vol.id), "zone_id": zone["id"],
    })
    assert created.status_code == 200
    resource_id = created.json()["id"]

    listed = api.get("/api/resources/", params={"available_only": False, "zone_id": zone["id"]}).json()
    assert any(r["id"] == resource_id and r["zone_id"] == zone["id"] for r in listed)


def test_zones_api_reassigns_resource_and_need(api, db):
    vol = mk_user(db, "志工", ["volunteer"], lat=24.0, lng=120.6)
    elder = mk_user(db, "長者", ["elderly"], lat=24.0, lng=120.6)
    res = mk_resource(db, vol)
    need = mk_need(db, elder)
    zone = zone_service.create_zone(db, "分區N")

    r1 = api.put(f"/api/zones/resources/{res.id}", params={"zone_id": zone["id"]})
    assert r1.status_code == 200
    db.refresh(res)
    assert res.zone_id == zone["id"]

    r2 = api.put(f"/api/zones/needs/{need.id}", params={"zone_id": zone["id"]})
    assert r2.status_code == 200
    db.refresh(need)
    assert need.zone_id == zone["id"]


# ═══════════════ 工作區文件也帶著分區 ═══════════════
def test_workspace_defaults_to_general_and_can_be_created_in_a_real_zone(api, db):
    default_ws = api.post("/api/workspaces", json={"name": "測試工作區"})
    assert default_ws.status_code == 201
    assert default_ws.json()["zone_id"] == GENERAL_ZONE_ID

    zone = zone_service.create_zone(db, "分區O")
    scoped_ws = api.post("/api/workspaces", json={"name": "分區工作區", "zone_id": zone["id"]})
    assert scoped_ws.status_code == 201
    assert scoped_ws.json()["zone_id"] == zone["id"]


def test_workspace_save_without_zone_id_keeps_its_existing_zone(api, db):
    zone = zone_service.create_zone(db, "分區P")
    created = api.post("/api/workspaces", json={"name": "工作區", "zone_id": zone["id"]}).json()

    saved = api.put(f"/api/workspaces/{created['id']}", json={
        "name": "工作區改名", "revision": created["revision"], "graph": created["graph"],
    })
    assert saved.status_code == 200
    assert saved.json()["zone_id"] == zone["id"], "沒帶 zone_id 時不該把工作區重置回 general"


def test_workspace_create_rejects_an_unknown_zone(api):
    resp = api.post("/api/workspaces", json={"name": "工作區", "zone_id": "no-such-zone"})
    assert resp.status_code == 400


# ═══════════════ 地理判斷：中心點＋半徑，自動抓最近的分區 ═══════════════
def test_resolve_zone_for_point_picks_the_nearest_overlapping_zone(db):
    near = zone_service.create_zone(db, "近", center_lat=24.000, center_lng=120.600, radius_km=3)
    far = zone_service.create_zone(db, "遠", center_lat=24.050, center_lng=120.650, radius_km=20)
    # 這個點同時落在兩個分區的半徑內；「遠」中心比較遠，但半徑夠大也涵蓋到——
    # 判斷依據是實際距離最近，不是半徑大小。
    zone_id = zone_service.resolve_zone_for_point(db, 24.001, 120.601)
    assert zone_id == near["id"]
    assert zone_id != far["id"]


def test_resolve_zone_for_point_falls_back_to_general_outside_every_radius(db):
    zone_service.create_zone(db, "分區Q", center_lat=25.0, center_lng=121.5, radius_km=1)
    assert zone_service.resolve_zone_for_point(db, 24.0, 120.6) == GENERAL_ZONE_ID


def test_resolve_zone_for_point_falls_back_to_general_without_coordinates(db):
    zone_service.create_zone(db, "分區R", center_lat=24.0, center_lng=120.6, radius_km=5)
    assert zone_service.resolve_zone_for_point(db, None, None) == GENERAL_ZONE_ID


def test_create_zone_validates_center_and_radius(db):
    with pytest.raises(zone_service.ZoneError):
        zone_service.create_zone(db, "分區S", center_lat=24.0)  # 缺 lng
    with pytest.raises(zone_service.ZoneError):
        zone_service.create_zone(db, "分區T", center_lat=24.0, center_lng=120.6, radius_km=0)
    with pytest.raises(zone_service.ZoneError):
        zone_service.create_zone(db, "分區U", radius_km=5)  # 沒中心點卻給半徑


# ═══════════════ LINE 流程：登記物資/需求時自動依座標分區 ═══════════════
def test_line_resource_registration_auto_resolves_zone_from_location(db):
    from app.routers.linebot import save_resource

    zone = zone_service.create_zone(db, "分區V", center_lat=24.0, center_lng=120.6, radius_km=2)
    vol = User(name="志工", roles=["volunteer"], line_uid="U-vol", lat=24.001, lng=120.601)
    db.add(vol); db.commit(); db.refresh(vol)

    save_resource(db, vol, "water", "10箱", None)
    res = db.query(CommunityResource).filter(CommunityResource.owner_id == vol.id).one()
    assert res.zone_id == zone["id"]


def test_line_need_registration_auto_resolves_zone_from_location(db):
    from app.routers.linebot import submit_needs

    zone = zone_service.create_zone(db, "分區W", center_lat=24.0, center_lng=120.6, radius_km=2)
    elder = User(name="長者", roles=["elderly"], line_uid="U-eld", lat=24.001, lng=120.601)
    db.add(elder); db.commit(); db.refresh(elder)

    submit_needs(db, elder, ["water"], "需要水")
    need = db.query(CommunityNeed).filter(CommunityNeed.requester_id == elder.id).one()
    assert need.zone_id == zone["id"]


def test_resources_api_auto_resolves_zone_when_not_specified(api, db):
    vol = User(name="志工", roles=["volunteer"]); db.add(vol); db.commit(); db.refresh(vol)
    zone = zone_service.create_zone(db, "分區X", center_lat=24.0, center_lng=120.6, radius_km=2)

    created = api.post("/api/resources/", params={
        "resource_type": "water", "name": "水", "owner_id": str(vol.id),
        "lat": 24.001, "lng": 120.601,
    })
    assert created.status_code == 200
    resource_id = created.json()["id"]
    listed = api.get("/api/resources/", params={"available_only": False}).json()
    assert any(r["id"] == resource_id and r["zone_id"] == zone["id"] for r in listed)
