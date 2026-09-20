# -*- coding: utf-8 -*-
"""每個角色都有對應介面：志工我的任務、管理員在 LINE 上操作、家屬綁定與長輩狀況、
居民紀錄頁、後台用 LINE 登入。都走真實的處理函式與路由。"""
import time

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.config import settings
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.models.volunteer_application import VolunteerApplication
from app.services import admin_session, dispatch, form_token
from app.timeutil import today_tw
from tests.test_line_hardening import mk, press, replies, say, sent_to


def card_text(outbox, to=None):
    return " ".join(str(m.contents.to_dict()) for kind, target, m in outbox.sent
                    if hasattr(m, "contents") and (to is None or target == to))


def world(db):
    vol = mk(db, "志工甲", ["volunteer"], "U-vol", lat=24.0, lng=120.6)
    req = mk(db, "王奶奶", ["elderly"], "U-req", lat=24.01, lng=120.61, phone="0912345678", address="台中市南區")
    adm = mk(db, "管理員小張", ["admin"], "U-adm")
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="甲的水", quantity="10",
                            lat=24.0, lng=120.6, is_available=True)
    need = CommunityNeed(requester_id=req.id, need_type="water", description="要水", address="台中市南區",
                         lat=24.01, lng=120.61, urgency=3, status="open")
    db.add_all([res, need]); db.commit(); db.refresh(res); db.refresh(need)
    return vol, req, adm, res, need


# ═══════════════ 志工：我的任務 ═══════════════
def test_my_tasks_lists_assigned_work_with_actions(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-vol", "我的任務")
    assert any("沒有進行中的任務" in t for t in replies(line_outbox))
    dispatch.manual_dispatch(str(need.id), str(res.id), db)
    say("U-vol", "我的任務")
    card = card_text(line_outbox, None)
    assert "action=task_accept" in card and "action=task_delivered" in card and "/f/report?t=" in card


def test_my_tasks_hides_accept_once_accepted_and_ignores_others_tasks(db, line_outbox):
    vol, req, adm, res, need = world(db)
    dispatch.manual_dispatch(str(need.id), str(res.id), db)
    press("U-vol", f"action=task_accept&need_id={need.id}")
    say("U-vol", "我的任務")
    latest = str(line_outbox.sent[-1][2].contents.to_dict())
    assert "您已確認接單" in latest and "action=task_accept" not in latest
    mk(db, "別的志工", ["volunteer"], "U-other")
    say("U-other", "我的任務")
    assert any("沒有進行中的任務" in t for t in replies(line_outbox))
    mk(db, "民眾", ["elderly"], "U-res")
    say("U-res", "我的任務")
    assert any("僅限志工" in t for t in replies(line_outbox))


# ═══════════════ 管理員：在 LINE 上決策 ═══════════════
def test_admin_commands_are_admin_only(db, line_outbox):
    world(db)
    for cmd in ("總覽", "待派", "待審", "求救單", "後台"):
        say("U-vol", cmd)
    assert sum("僅限管理員" in t for t in replies(line_outbox)) == 5


def test_admin_overview_counts(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-adm", "總覽")
    text = replies(line_outbox)[-1]
    assert "待派遣需求：1" in text and "待處理求救單：0" in text and "待審志工申請：0" in text


def test_admin_can_dispatch_from_line_and_it_is_audited(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-adm", "待派")
    card = card_text(line_outbox, None)
    assert f"action=admin_match&need_id={need.id}&resource_id={res.id}" in card
    press("U-adm", f"action=admin_match&need_id={need.id}&resource_id={res.id}")
    assert any("已派遣" in t for t in replies(line_outbox))
    assert any("任務" in t for t in sent_to(line_outbox, "U-vol"))
    db.expire_all()
    assert db.query(CommunityNeed).one().status == "matched"
    ev = db.query(DispatchEvent).filter(DispatchEvent.action == "manual_dispatch").one()
    assert ev.actor_label == "admin:管理員小張" and str(ev.actor_id) == str(adm.id)


def test_admin_postbacks_refuse_non_admins(db, line_outbox):
    vol, req, adm, res, need = world(db)
    press("U-vol", f"action=admin_match&need_id={need.id}&resource_id={res.id}")
    assert any("僅限管理員" in t for t in replies(line_outbox))
    db.expire_all()
    assert db.query(CommunityNeed).one().status == "open"


def test_admin_gets_application_card_and_can_approve_from_line(db, line_outbox):
    vol, req, adm, res, need = world(db)
    mk(db, "路人", ["elderly"], "U-new")
    from app.services.volunteer_application import submit
    app_row = submit(db, line_uid="U-new", name="陳小美", phone="0912345678", service_area="南區")
    card = card_text(line_outbox, "U-adm")
    assert f"action=admin_app&id={app_row.id}&d=approve" in card and "陳小美" in card
    say("U-adm", "待審")
    press("U-adm", f"action=admin_app&id={app_row.id}&d=approve")
    assert any("已核准" in t for t in replies(line_outbox))
    db.expire_all()
    assert "volunteer" in db.query(User).filter(User.line_uid == "U-new").one().roles
    assert db.query(VolunteerApplication).one().status == "approved"
    press("U-adm", f"action=admin_app&id={app_row.id}&d=approve")
    assert any("已經是" in t for t in replies(line_outbox)), "重複按要說明，不能出錯"


def test_sos_notification_has_resolve_button_that_works(db, line_outbox):
    vol, req, adm, res, need = world(db)
    press("U-req", "action=confirm_sos")
    sos = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    assert f"action=admin_sos&need_id={sos.id}" in card_text(line_outbox, "U-adm")
    say("U-adm", "求救單")
    assert "tel:0912345678" in card_text(line_outbox, None), "求救單要能一鍵撥給當事人"
    press("U-adm", f"action=admin_sos&need_id={sos.id}")
    db.expire_all()
    assert db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one().status == "fulfilled"
    assert any("聯繫處理" in t for t in replies(line_outbox))


# ═══════════════ 家屬 ═══════════════
def test_family_binding_end_to_end(db, line_outbox, monkeypatch):
    from app.services import rich_menu
    synced = []
    monkeypatch.setattr(rich_menu, "sync_user_menu", lambda u: synced.append(u.line_uid))
    vol, req, adm, res, need = world(db)
    mk(db, "女兒", ["elderly"], "U-fam")
    say("U-req", "邀請家人")
    code = [t for t in replies(line_outbox) if "綁定" in t][-1].split("綁定 ")[1][:6]
    assert code.isdigit()
    say("U-fam", f"綁定 {code}")
    assert any("已綁定為 王奶奶 的家屬" in t for t in replies(line_outbox))
    db.expire_all()
    assert db.query(CareRelation).filter(CareRelation.relation == "family").count() == 1
    assert "family" in db.query(User).filter(User.line_uid == "U-fam").one().roles
    assert synced == ["U-fam"], "綁定後要換成家屬選單"
    assert any("已成為您的家屬" in t for t in sent_to(line_outbox, "U-req"))
    say("U-fam", f"綁定 {code}")
    assert any("無效或已過期" in t for t in replies(line_outbox)), "綁定碼只能用一次"


def test_family_binding_rejects_bad_codes_and_self_use(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-vol", "綁定 123456")
    assert any("無效或已過期" in t for t in replies(line_outbox))
    say("U-req", "邀請家人")
    code = [t for t in replies(line_outbox) if "綁定" in t][-1].split("綁定 ")[1][:6]
    say("U-req", f"綁定 {code}")
    assert any("自己的綁定碼" in t for t in replies(line_outbox))
    row = db.query(SystemConfig).filter(SystemConfig.key == f"invite:{code}").one()
    row.value = row.value.replace(row.value.split('"exp": "')[1][:4], "2001")
    db.commit()
    say("U-vol", f"綁定 {code}")
    assert db.query(CareRelation).count() == 0, "過期的綁定碼不能綁定"


def test_new_invite_replaces_the_old_one(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-req", "邀請家人")
    say("U-req", "邀請家人")
    assert db.query(SystemConfig).filter(SystemConfig.key.like("invite:%")).count() == 1


def test_family_sees_elder_status_and_can_confirm_safe(db, line_outbox):
    vol, req, adm, res, need = world(db)
    fam = mk(db, "女兒", ["family"], "U-fam")
    db.add(CareRelation(elderly_id=req.id, contact_id=fam.id, relation="family", notify_order=1))
    checkin = DailyCheckin(elderly_id=req.id, date=today_tw(), status="pending")
    db.add(checkin); db.commit(); db.refresh(checkin)
    say("U-fam", "長輩狀況")
    card = card_text(line_outbox, None)
    assert "王奶奶" in card and "還沒回覆" in card and "tel:0912345678" in card
    assert f"action=confirm_safe&checkin_id={checkin.id}" in card
    press("U-fam", f"action=confirm_safe&checkin_id={checkin.id}")
    assert any("感謝您的確認" in t for t in replies(line_outbox))
    say("U-vol", "長輩狀況")
    assert any("還沒有綁定的長輩" in t for t in replies(line_outbox))


# ═══════════════ 居民：我的需求卡片與紀錄頁 ═══════════════
@pytest.fixture()
def webclient():
    from app.main import app
    return TestClient(app, base_url="https://testserver")


def test_me_page_lists_needs_family_and_lets_the_owner_cancel(db, webclient, line_outbox):
    vol, req, adm, res, need = world(db)
    t = form_token.make_token("U-req")
    data = webclient.get("/f/api/me", params={"t": t}).json()
    assert data["needs"][0]["can_cancel"] is True and data["is_staff"] is False
    assert webclient.post("/f/api/cancel_need", json={"t": form_token.make_token("U-vol"), "target_id": str(need.id)}
                          ).status_code == 404, "不能取消別人的需求"
    assert webclient.post("/f/api/cancel_need", json={"t": t, "target_id": str(need.id)}).status_code == 200
    db.expire_all()
    assert db.query(CommunityNeed).one().status == "cancelled"
    assert webclient.post("/f/api/cancel_need", json={"t": t, "target_id": str(need.id)}).status_code == 409


def test_me_page_invite_and_unbind(db, webclient):
    vol, req, adm, res, need = world(db)
    fam = mk(db, "女兒", ["family"], "U-fam")
    rel = CareRelation(elderly_id=req.id, contact_id=fam.id, relation="family", notify_order=1)
    db.add(rel); db.commit(); db.refresh(rel)
    t = form_token.make_token("U-req")
    assert len(webclient.post("/f/api/invite", json={"t": t}).json()["code"]) == 6
    me = webclient.get("/f/api/me", params={"t": t}).json()
    assert me["family"][0]["name"] == "女兒"
    assert webclient.post("/f/api/unbind", json={"t": form_token.make_token("U-vol"), "target_id": str(rel.id)}
                          ).status_code == 404, "與這組關係無關的人不能解除"
    assert webclient.post("/f/api/unbind", json={"t": t, "target_id": str(rel.id)}).status_code == 200
    assert db.query(CareRelation).count() == 0


def test_me_page_shows_volunteer_resources_and_tasks_and_can_withdraw(db, webclient):
    vol, req, adm, res, need = world(db)
    t = form_token.make_token("U-vol")
    me = webclient.get("/f/api/me", params={"t": t}).json()
    assert me["resources"][0]["available"] is True and me["tasks"] == []
    dispatch.manual_dispatch(str(need.id), str(res.id), db)
    me = webclient.get("/f/api/me", params={"t": t}).json()
    assert me["tasks"][0]["report_url"].startswith("http") and me["resources"][0]["available"] is False
    assert webclient.post("/f/api/withdraw_resource", json={"t": t, "target_id": str(res.id)}).status_code == 409
    other = mk(db, "民眾", ["elderly"], "U-x")
    assert webclient.post("/f/api/withdraw_resource", json={"t": form_token.make_token("U-x"), "target_id": str(res.id)}
                          ).status_code in (403, 404)


def test_my_needs_card_and_cancel_button(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-req", "我的需求")
    card = card_text(line_outbox, None)
    assert "待媒合" in card and "action=cancel_needs" in card and "/f/me?t=" in card
    press("U-req", "action=cancel_needs")
    assert any("已幫您取消 1 筆" in t for t in replies(line_outbox))


# ═══════════════ 後台：用 LINE 登入 ═══════════════
def test_login_tokens_are_signed_short_lived_and_not_interchangeable():
    token = admin_session.make_login_token("u1")
    assert admin_session.verify_login_token(token) == "u1"
    assert admin_session.verify_session(token) is None, "登入連結不能直接當作 session"
    assert admin_session.verify_login_token(admin_session.make_session("u1")) is None
    assert admin_session.verify_login_token(token + "x") is None
    old = admin_session.make_login_token("u1", now=time.time() - admin_session.LOGIN_TTL - 5)
    assert admin_session.verify_login_token(old) is None


@pytest.fixture()
def enforced(monkeypatch):
    from app import demo_auth
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "")
    monkeypatch.setattr(settings, "ADMIN_LINE_LOGIN", True)
    demo_auth.reset_cache()
    yield
    demo_auth.reset_cache()


def test_console_is_open_until_an_admin_is_bound_to_line(db, enforced, webclient):
    mk(db, "沒綁 LINE 的管理員", ["admin"], None)
    assert webclient.get("/api/dashboard/users").status_code == 200
    assert webclient.get("/api/system/security").json()["public_admin"] is True


def test_console_requires_line_login_once_an_admin_is_bound(db, enforced, webclient, line_outbox):
    adm = mk(db, "管理員小張", ["admin"], "U-adm")
    assert webclient.get("/api/dashboard/users").status_code == 401
    assert webclient.get("/admin").status_code == 401
    assert webclient.get("/health").status_code == 200 and webclient.get("/f/need").status_code == 200
    assert webclient.post("/webhook/line", content=b"{}").status_code in (200, 400)

    say("U-adm", "後台")
    link = card_text(line_outbox, None).split("/admin/login?t=")[1].split("'")[0]
    r = webclient.get(f"/admin/login?t={link}", follow_redirects=False)
    assert r.status_code == 303 and "admin_session" in r.headers["set-cookie"]
    assert "HttpOnly" in r.headers["set-cookie"]
    assert webclient.get("/api/dashboard/users").status_code == 200, "登入後 cookie 帶著就能用"
    sec = webclient.get("/api/system/security").json()
    assert sec["admin"] == "管理員小張" and sec["public_admin"] is False and sec["auth_mode"] == "line-admin"


def test_login_link_is_refused_for_bad_or_non_admin_tokens(db, enforced, webclient):
    mk(db, "管理員", ["admin"], "U-adm")
    vol = mk(db, "志工", ["volunteer"], "U-vol")
    assert webclient.get("/admin/login?t=garbage").status_code == 401
    forged = admin_session.make_login_token(str(vol.id))
    assert webclient.get(f"/admin/login?t={forged}").status_code == 401, "非管理員拿不到登入"
    assert webclient.get("/admin/login").status_code == 401


def test_forged_or_stale_cookie_does_not_log_in(db, enforced, webclient):
    adm = mk(db, "管理員", ["admin"], "U-adm")
    webclient.cookies.set("admin_session", "forged.cookie")
    assert webclient.get("/api/dashboard/users").status_code == 401
    webclient.cookies.set("admin_session", admin_session.make_session(str(adm.id), now=time.time() - 13 * 3600))
    assert webclient.get("/api/dashboard/users").status_code == 401


def test_demo_password_still_works_and_admin_login_overrides_nothing_else(db, monkeypatch, webclient):
    from app import demo_auth
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "secret")
    demo_auth.reset_cache()
    assert webclient.get("/api/dashboard/users").status_code == 401
    assert webclient.get("/api/dashboard/users", auth=("x", "secret")).status_code == 200
    demo_auth.reset_cache()


def test_dispatch_from_the_console_records_which_admin_did_it(db, enforced, webclient, line_outbox):
    vol, req, adm, res, need = world(db)
    cookie = admin_session.make_session(str(adm.id))
    webclient.cookies.set("admin_session", cookie)
    r = webclient.post(f"/api/resources/needs/{need.id}/match", params={"resource_id": str(res.id)})
    assert r.status_code == 200, r.text
    db.expire_all()
    ev = db.query(DispatchEvent).filter(DispatchEvent.action == "manual_dispatch").one()
    assert ev.actor_label == "admin:管理員小張"


def test_admin_can_rebuild_menus_from_line(db, line_outbox, monkeypatch):
    from app.services import rich_menu
    monkeypatch.setattr(rich_menu, "install_menus", lambda d: {"menus": {"a": 1, "b": 2}, "staff_linked": 3})
    world(db)
    say("U-adm", "更新選單")
    assert any("已重建 2 張選單" in t and "3 位" in t for t in replies(line_outbox))
    say("U-vol", "更新選單")
    assert any("僅限管理員" in t for t in replies(line_outbox))


# ═══════════════ 刪除我的帳號 ═══════════════
def test_delete_my_account_asks_first_and_warns_the_only_admin(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-adm", "刪除我的帳號")
    card = card_text(line_outbox, None)
    assert "action=delete_me" in card and "唯一的管理員" in card
    assert db.query(User).filter(User.line_uid == "U-adm").count() == 1, "還沒按確定不能刪"
    press("U-adm", "action=keep_me")
    assert db.query(User).filter(User.line_uid == "U-adm").count() == 1
    say("U-req", "刪除我的帳號")
    assert "唯一的管理員" not in str(line_outbox.sent[-1][2].contents.to_dict())


def test_confirming_deletes_the_account_and_everything_that_belongs_to_it(db, line_outbox, monkeypatch):
    from app.services import rich_menu
    unlinked = []

    class Api:
        def unlink_rich_menu_id_from_user(self, uid):
            unlinked.append(uid)

    monkeypatch.setattr(rich_menu, "_apis", lambda: (Api(), None))
    vol, req, adm, res, need = world(db)
    db.add(DailyCheckin(elderly_id=req.id, date=today_tw(), status="ok"))
    db.add(SystemConfig(key="flow:U-req", value="{}"))
    db.commit()
    old_id = str(req.id)
    press("U-req", "action=delete_me")
    db.expire_all()
    assert db.query(User).filter(User.line_uid == "U-req").count() == 0
    assert db.query(CommunityNeed).count() == 0 and db.query(DailyCheckin).count() == 0
    assert db.query(SystemConfig).filter(SystemConfig.key == "flow:U-req").count() == 0
    assert db.query(User).filter(User.line_uid == "U-vol").count() == 1, "別人的資料不能被動到"
    assert unlinked == ["U-req"]
    assert any("已刪除" in t for t in replies(line_outbox))
    say("U-req", "我很好")
    db.expire_all()
    fresh = db.query(User).filter(User.line_uid == "U-req").one()
    assert fresh.roles == ["elderly"] and str(fresh.id) != old_id, "刪除後再傳訊息要重新註冊成新的居民"


def test_deleting_an_admin_keeps_the_audit_trail(db, line_outbox):
    vol, req, adm, res, need = world(db)
    press("U-adm", f"action=admin_match&need_id={need.id}&resource_id={res.id}")
    dispatch.cancel_need(str(need.id), db)                       # 任務結束，才不會被進行中的派遣擋住
    press("U-adm", "action=delete_me")
    db.expire_all()
    assert db.query(User).filter(User.line_uid == "U-adm").count() == 0
    ev = db.query(DispatchEvent).filter(DispatchEvent.action == "manual_dispatch").one()
    assert ev.actor_label == "admin:管理員小張", "稽核紀錄留著名字，但不再指向已刪除的帳號"


def test_delete_is_refused_while_a_dispatch_is_in_progress(db, line_outbox):
    vol, req, adm, res, need = world(db)
    dispatch.manual_dispatch(str(need.id), str(res.id), db)
    press("U-vol", "action=delete_me")
    press("U-req", "action=delete_me")
    assert sum("現在還不能刪除" in t for t in replies(line_outbox)) == 2
    assert db.query(User).filter(User.line_uid.in_(["U-vol", "U-req"])).count() == 2


def test_console_delete_still_works_and_reports_blockers(db):
    from app.main import app
    from fastapi.testclient import TestClient
    vol, req, adm, res, need = world(db)
    c = TestClient(app)
    dispatch.manual_dispatch(str(need.id), str(res.id), db)
    blocked = c.delete(f"/api/dashboard/users/{vol.id}")
    assert blocked.status_code == 409 and "進行中的派遣" in blocked.text
    dispatch.cancel_need(str(need.id), db)
    assert c.delete(f"/api/dashboard/users/{vol.id}").status_code == 200


# ═══════════════ 用綁定碼加入成員（不用貼 LINE User ID）═══════════════
@pytest.fixture()
def console():
    from app.main import app
    return TestClient(app)


def _create_member(console, name, role):
    r = console.post("/api/dashboard/users", params={"name": name, "roles": role})
    assert r.status_code == 200, r.text
    return r.json()["id"]


def _join_code(console, member_id):
    r = console.post(f"/api/dashboard/users/{member_id}/join_code")
    assert r.status_code == 200, r.text
    return r.json()


def test_console_makes_a_join_code_and_the_person_binds_by_sending_it(db, console, line_outbox, monkeypatch):
    from app.services import rich_menu
    synced = []
    monkeypatch.setattr(rich_menu, "sync_user_menu", lambda u: synced.append((u.name, u.line_uid)))
    member_id = _create_member(console, "管理員小張", "admin")
    info = _join_code(console, member_id)
    assert len(info["code"]) == 8 and info["say"] == f"加入 {info['code']}" and info["expires_minutes"] == 30

    say("U-newbie", info["say"])                                  # 對方第一句話就是綁定碼
    db.expire_all()
    member = db.query(User).filter(User.id == member_id).one()
    assert member.line_uid == "U-newbie" and member.roles == ["admin"]
    assert db.query(User).filter(User.line_uid == "U-newbie").count() == 1, "臨時居民帳號要被換掉，不能留下兩個"
    assert db.query(User).count() == 1
    assert any("已綁定為「管理員小張」" in t and "後台" in t for t in replies(line_outbox))
    assert not any("歡迎加入" in t for t in sent_to(line_outbox, "U-newbie")), "已綁定成員不需要再歡迎"
    assert synced == [("管理員小張", "U-newbie")]
    say("U-other", info["say"])
    assert any("無效或已過期" in t for t in replies(line_outbox)), "綁定碼只能用一次"


def test_volunteer_codes_last_a_week_and_admin_codes_half_an_hour(db, console):
    assert _join_code(console, _create_member(console, "志工", "volunteer"))["expires_minutes"] == 7 * 24 * 60
    assert _join_code(console, _create_member(console, "管理員", "admin"))["expires_minutes"] == 30


def test_a_new_code_replaces_the_old_and_bound_members_cannot_get_one(db, console):
    member_id = _create_member(console, "志工", "volunteer")
    first = _join_code(console, member_id)["code"]
    second = _join_code(console, member_id)["code"]
    assert db.query(SystemConfig).filter(SystemConfig.key.like("join:%")).count() == 1
    say("U-a", f"加入 {first}")
    db.expire_all()
    assert db.query(User).filter(User.id == member_id).one().line_uid is None, "被取代的舊碼不能用"
    say("U-a", f"加入 {second}")
    r = console.post(f"/api/dashboard/users/{member_id}/join_code")
    assert r.status_code == 409


def test_join_is_refused_for_an_account_that_already_has_history(db, console, line_outbox):
    vol, req, adm, res, need = world(db)
    member_id = _create_member(console, "新志工", "volunteer")
    code = _join_code(console, member_id)["code"]
    say("U-req", f"加入 {code}")                                   # 王奶奶有需求紀錄，不能被蓋掉
    assert any("已經有使用紀錄" in t for t in replies(line_outbox))
    db.expire_all()
    assert db.query(User).filter(User.line_uid == "U-req").count() == 1
    assert db.query(User).filter(User.id == member_id).one().line_uid is None


def test_expired_code_is_rejected(db, console, line_outbox):
    member_id = _create_member(console, "志工", "volunteer")
    code = _join_code(console, member_id)["code"]
    row = db.query(SystemConfig).filter(SystemConfig.key == f"join:{code}").one()
    row.value = row.value.replace(row.value.split('"exp": "')[1][:4], "2001")
    db.commit()
    say("U-late", f"加入 {code}")
    assert any("無效或已過期" in t for t in replies(line_outbox))


def test_guessing_codes_is_rate_limited_even_for_the_right_code(db, console, line_outbox):
    member_id = _create_member(console, "管理員", "admin")
    code = _join_code(console, member_id)["code"]
    for i in range(5):
        say("U-guess", f"加入 1000000{i}")
    say("U-guess", f"加入 {code}")                                 # 猜錯 5 次後，連正確的也先擋住
    assert any("次數太多" in t for t in replies(line_outbox))
    db.expire_all()
    assert db.query(User).filter(User.id == member_id).one().line_uid is None
    say("U-real", f"加入 {code}")                                  # 不同的 LINE 帳號不受影響
    db.expire_all()
    assert db.query(User).filter(User.id == member_id).one().line_uid == "U-real"


def test_family_codes_are_rate_limited_too(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-req", "邀請家人")
    code = [t for t in replies(line_outbox) if "綁定" in t][-1].split("綁定 ")[1][:6]
    for i in range(5):
        say("U-vol", f"綁定 99999{i}")
    say("U-vol", f"綁定 {code}")
    assert any("次數太多" in t for t in replies(line_outbox))
    assert db.query(CareRelation).count() == 0


def test_unlinking_line_frees_the_member(db, console, monkeypatch):
    from app.services import rich_menu
    unlinked = []

    class Api:
        def unlink_rich_menu_id_from_user(self, uid):
            unlinked.append(uid)

    monkeypatch.setattr(rich_menu, "_apis", lambda: (Api(), None))
    vol, req, adm, res, need = world(db)
    assert console.post(f"/api/dashboard/users/{vol.id}/unlink_line").status_code == 200
    db.expire_all()
    assert db.query(User).filter(User.id == vol.id).one().line_uid is None
    assert unlinked == ["U-vol"]
    assert console.post(f"/api/dashboard/users/{vol.id}/join_code").status_code == 200


# ═══════════════ 掃碼加入 ═══════════════
@pytest.fixture()
def bot_id(monkeypatch):
    from app.services import line_notify
    monkeypatch.setattr(line_notify, "_basic_id", None)
    monkeypatch.setattr(settings, "LINE_BOT_BASIC_ID", "@571hpppb")
    yield "@571hpppb"
    monkeypatch.setattr(line_notify, "_basic_id", None)


def test_oa_message_link_prefills_the_join_message(bot_id):
    from app.services.line_notify import oa_message_link
    link = oa_message_link("加入 12345678")
    assert link == "https://line.me/R/oaMessage/@571hpppb/?%E5%8A%A0%E5%85%A5%2012345678"


def test_basic_id_comes_from_the_line_api_and_only_successes_are_cached(monkeypatch):
    from app.services import line_notify
    monkeypatch.setattr(settings, "LINE_BOT_BASIC_ID", "")
    monkeypatch.setattr(line_notify, "_basic_id", None)
    calls = []

    class Api:
        def get_bot_info(self):
            calls.append(1)
            if len(calls) == 1:
                raise RuntimeError("LINE hiccup")
            return type("Info", (), {"basic_id": "@abc123"})()

    monkeypatch.setattr(line_notify, "_get_api", lambda: Api())
    assert line_notify.bot_basic_id() is None, "查詢失敗時不能讓掃碼功能當機"
    assert line_notify.bot_basic_id() == "@abc123"
    assert line_notify.bot_basic_id() == "@abc123" and len(calls) == 2, "成功後要快取"
    monkeypatch.setattr(line_notify, "_basic_id", None)


def test_join_code_comes_with_a_scannable_qr_when_the_bot_id_is_known(db, console, bot_id):
    import base64
    member_id = _create_member(console, "志工", "volunteer")
    info = _join_code(console, member_id)
    assert info["link"].endswith("%E5%8A%A0%E5%85%A5%20" + info["code"])
    assert info["qr"].startswith("data:image/svg+xml;base64,")
    svg = base64.b64decode(info["qr"].split(",", 1)[1]).decode()
    assert svg.startswith("<svg") and "path" in svg


def test_join_code_still_works_without_a_qr_when_the_bot_id_is_unknown(db, console, monkeypatch):
    from app.services import line_notify
    monkeypatch.setattr(settings, "LINE_BOT_BASIC_ID", "")
    monkeypatch.setattr(line_notify, "_basic_id", None)
    info = _join_code(console, _create_member(console, "志工", "volunteer"))
    assert info["qr"] is None and info["link"] is None and info["say"].startswith("加入 ")


def test_family_invite_gives_a_tap_to_bind_link(db, line_outbox, bot_id):
    vol, req, adm, res, need = world(db)
    say("U-req", "邀請家人")
    text = [t for t in replies(line_outbox) if "綁定" in t][-1]
    assert "https://line.me/R/oaMessage/@571hpppb/?" in text and "24 小時內有效" in text


def test_me_page_invite_returns_the_link(db, webclient, bot_id):
    vol, req, adm, res, need = world(db)
    out = webclient.post("/f/api/invite", json={"t": form_token.make_token("U-req")}).json()
    assert out["link"].startswith("https://line.me/R/oaMessage/@571hpppb/?") and len(out["code"]) == 6


# ═══════════════ 公開的掃碼加入頁 ═══════════════
def test_join_page_is_public_even_when_the_console_requires_login(db, enforced, webclient, bot_id):
    mk(db, "管理員", ["admin"], "U-adm")
    assert webclient.get("/api/dashboard/users").status_code == 401 and webclient.get("/admin").status_code == 401
    page = webclient.get("/join")
    assert page.status_code == 200
    assert page.text.count("data:image/svg+xml;base64,") == 2
    assert "https://line.me/R/ti/p/%40571hpppb" in page.text
    assert "oaMessage/@571hpppb/?%E6%88%91%E8%A6%81%E7%95%B6%E5%BF%97%E5%B7%A5" in page.text


def test_join_page_exposes_no_member_data(db, webclient, bot_id):
    vol, req, adm, res, need = world(db)
    text = webclient.get("/join").text
    for private in ("王奶奶", "志工甲", "管理員小張", "0912345678", "U-req", "台中市南區"):
        assert private not in text


def test_join_page_degrades_gracefully_without_the_bot_id(monkeypatch, webclient):
    from app.services import line_notify
    monkeypatch.setattr(settings, "LINE_BOT_BASIC_ID", "")
    monkeypatch.setattr(line_notify, "_basic_id", None)
    page = webclient.get("/join")
    assert page.status_code == 200 and "暫時查不到官方帳號資訊" in page.text


def test_console_needs_no_login_by_default_even_with_a_bound_admin(db, monkeypatch, webclient):
    from app import demo_auth
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "")
    demo_auth.reset_cache()
    assert settings.ADMIN_LINE_LOGIN is False
    mk(db, "管理員", ["admin"], "U-adm")
    assert webclient.get("/admin").status_code == 200
    assert webclient.get("/api/dashboard/users").status_code == 200
    assert webclient.get("/").status_code == 200
    demo_auth.reset_cache()
