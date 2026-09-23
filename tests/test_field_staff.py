# -*- coding: utf-8 -*-
"""基層員工（field_staff）：介於志工與管理員之間的新角色，能在網頁新增／編輯物資與
社區固定資源點，但碰不到派遣決策、使用者管理等管理員專屬動作。"""
import pytest
from fastapi.testclient import TestClient

from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.need import CommunityNeed
from app.models.user import User
from app.security import require_admin, require_staff
from app.services import admin_session
from app.services.admin_session import current_admin
from app.validation import USER_ROLES, check_roles


@pytest.fixture()
def api():
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def mk(db, name, roles, uid=None, **kw):
    u = User(name=name, roles=roles, line_uid=uid, **kw)
    db.add(u); db.commit(); db.refresh(u)
    return u


def login_as(client, user):
    client.cookies.set("admin_session", admin_session.make_session(str(user.id)))


# ═══════════════ 角色本身 ═══════════════
def test_field_staff_is_a_valid_role():
    assert "field_staff" in USER_ROLES
    assert check_roles(["field_staff"]) == ["field_staff"]


# ═══════════════ require_admin / require_staff：單元行為 ═══════════════
def test_open_mode_bypasses_both_gates_when_nobody_is_logged_in():
    marker = current_admin.set(None)
    try:
        assert require_admin() is None
        assert require_staff() is None
    finally:
        current_admin.reset(marker)


def test_require_admin_rejects_field_staff_but_accepts_admin():
    from fastapi import HTTPException
    marker = current_admin.set({"id": "x", "name": "員工", "roles": ["field_staff"]})
    try:
        with pytest.raises(HTTPException) as exc:
            require_admin()
        assert exc.value.status_code == 403
    finally:
        current_admin.reset(marker)

    marker = current_admin.set({"id": "x", "name": "管理員", "roles": ["admin"]})
    try:
        assert require_admin()["roles"] == ["admin"]
    finally:
        current_admin.reset(marker)


def test_require_staff_accepts_admin_and_field_staff_but_not_others():
    from fastapi import HTTPException
    for roles in (["admin"], ["field_staff"], ["admin", "field_staff"]):
        marker = current_admin.set({"id": "x", "name": "y", "roles": roles})
        try:
            assert require_staff() is not None
        finally:
            current_admin.reset(marker)

    marker = current_admin.set({"id": "x", "name": "志工", "roles": ["volunteer"]})
    try:
        with pytest.raises(HTTPException) as exc:
            require_staff()
        assert exc.value.status_code == 403
    finally:
        current_admin.reset(marker)


# ═══════════════ LINE 登入連結兌換 ═══════════════
def test_admin_login_accepts_field_staff_and_rejects_plain_volunteer(db, api):
    staff = mk(db, "基層員工", ["field_staff"], "U-staff")
    vol = mk(db, "志工", ["volunteer"], "U-vol")

    staff_token = admin_session.make_login_token(str(staff.id))
    r1 = api.get(f"/admin/login?t={staff_token}", follow_redirects=False)
    assert r1.status_code == 303 and "admin_session" in r1.headers["set-cookie"]

    vol_token = admin_session.make_login_token(str(vol.id))
    r2 = api.get(f"/admin/login?t={vol_token}", follow_redirects=False)
    assert r2.status_code == 401


# ═══════════════ 端對端：新增資源點與物資，權限分界 ═══════════════
def _seed(db):
    admin = mk(db, "管理員", ["admin"], "U-adm")
    staff = mk(db, "基層員工", ["field_staff"], "U-staff")
    vol = mk(db, "志工乙", ["volunteer"], "U-vol2", lat=24.0, lng=120.6)
    elder = mk(db, "長者", ["elderly"], "U-eld", lat=24.0, lng=120.6)
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="水", quantity="10箱",
                            lat=24.0, lng=120.6, is_available=True)
    need = CommunityNeed(requester_id=elder.id, need_type="water", status="open", lat=24.0, lng=120.6)
    db.add_all([res, need]); db.commit()
    for row in (res, need):
        db.refresh(row)
    return admin, staff, vol, elder, res, need


def _create_point_command(creation_key):
    return {
        "changes": [{
            "node_id": "local:new-point",
            "operation": "create",
            "creation_key": creation_key,
            "point_type": "shelter",
            "values": {"name": "測試避難所", "lat": 24.01, "lng": 120.61, "address": "測試地址",
                      "capacity": 50, "phone": "04-1234567", "operating_hours": "24h"},
        }],
    }


def test_field_staff_can_create_a_resource_point_via_the_workspace(db, api):
    _admin, staff, _vol, _elder, _res, _need = _seed(db)
    login_as(api, staff)

    key = "11111111-1111-1111-1111-111111111111"
    resp = api.post("/api/workspaces/database-push", json=_create_point_command(key))
    assert resp.status_code == 200, resp.text
    applied = resp.json()["applied"]
    assert len(applied) == 1 and applied[0]["database_id"].startswith("db:point:")

    point = db.query(ResourcePoint).filter(ResourcePoint.name == "測試避難所").one()
    assert point.point_type == "shelter" and point.capacity == 50
    assert point.phone == "04-1234567" and point.operating_hours == "24h"
    assert point.source == "workspace" and point.is_active is True


def test_creating_the_same_point_twice_is_idempotent_not_duplicated(db, api):
    _admin, staff, _vol, _elder, _res, _need = _seed(db)
    login_as(api, staff)
    key = "22222222-2222-2222-2222-222222222222"
    command = _create_point_command(key)

    first = api.post("/api/workspaces/database-push", json=command)
    assert first.status_code == 200
    second = api.post("/api/workspaces/database-push", json=command)
    assert second.status_code == 200
    assert db.query(ResourcePoint).filter(ResourcePoint.name == "測試避難所").count() == 1


def test_field_staff_can_register_a_resource_too(db, api):
    _admin, staff, vol, _elder, _res, _need = _seed(db)
    login_as(api, staff)
    resp = api.post("/api/resources/", params={
        "resource_type": "food", "name": "備用餐食", "owner_id": str(vol.id), "quantity": "30份",
    })
    assert resp.status_code == 200, resp.text
    assert db.query(CommunityResource).filter(CommunityResource.name == "備用餐食").one().quantity_amount == 30


def test_field_staff_cannot_confirm_dispatch_or_delete(db, api):
    admin, staff, vol, elder, res, need = _seed(db)
    login_as(api, staff)

    assert api.post("/api/resources/dispatch").status_code == 403
    assert api.post(f"/api/resources/needs/{need.id}/confirm_dispatch").status_code == 403
    assert api.post(f"/api/resources/needs/{need.id}/match", params={"resource_id": str(res.id)}).status_code == 403
    assert api.delete(f"/api/resources/{res.id}").status_code == 403

    login_as(api, admin)
    assert api.post(f"/api/resources/needs/{need.id}/match", params={"resource_id": str(res.id)}).status_code == 200
