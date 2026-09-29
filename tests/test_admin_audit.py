"""計畫書：管理員操作均有時間戳記錄。"""
from fastapi.testclient import TestClient

from app.main import app
from app.models.admin_audit import AdminAudit


def test_write_requests_are_logged_with_time_and_reads_are_not(db):
    client = TestClient(app)
    client.post("/api/dashboard/mode?mode=emergency")
    client.get("/api/dashboard/summary")
    rows = db.query(AdminAudit).all()
    assert [(r.method, r.path) for r in rows] == [("POST", "/api/dashboard/mode?mode=emergency")]
    assert rows[0].created_at is not None and rows[0].status_code == 200

    log = client.get("/api/dashboard/audit").json()
    assert log[0]["path"] == "/api/dashboard/mode?mode=emergency"
    assert log[0]["at"].endswith("+00:00")


def test_audit_failure_does_not_break_the_request(db, monkeypatch):
    from app.services import admin_audit
    monkeypatch.setattr(admin_audit, "SessionLocal", lambda: (_ for _ in ()).throw(RuntimeError("db down")))
    assert TestClient(app).post("/api/dashboard/mode?mode=normal").status_code == 200


def test_line_admin_actions_are_logged_too(db, line_outbox):
    from app.models.need import CommunityNeed
    from tests.test_line_hardening import mk, press
    mk(db, "管理員小張", ["admin"], "U-audit-adm")
    elder = mk(db, "王奶奶", ["elderly"], "U-audit-eld")
    need = CommunityNeed(requester_id=elder.id, need_type="sos", description="一鍵求助", urgency=5)
    db.add(need); db.commit()

    press("U-audit-adm", f"action=admin_sos&need_id={need.id}")
    press("U-audit-adm", "action=admin_confirm&need_id=not-a-need")
    press("U-audit-adm", f"action=admin_cands&need_id={need.id}")  # read only, not logged

    rows = {r.path.split(" ")[0]: r for r in db.query(AdminAudit).filter(AdminAudit.method == "LINE").all()}
    assert set(rows) == {"求救已處理", "核准派遣"}
    assert rows["求救已處理"].actor_label == "管理員小張" and rows["求救已處理"].status_code == 200
    assert rows["核准派遣"].status_code == 409


def test_emergency_broadcast_points_to_supply_lookup(db, line_outbox):
    from app.routers.dashboard import broadcast_mode_change
    from tests.test_line_hardening import mk
    mk(db, "居民甲", ["elderly"], "U-b1")
    mk(db, "停用", ["elderly"], "U-b2", is_active=False)
    assert broadcast_mode_change("emergency") == 1
    kind, to, message = line_outbox.sent[-1]
    assert to == "U-b1" and "查詢物資" in message.text
    assert [i.action.data for i in message.quick_reply.items] == ["cmd=查詢物資", "cmd=需要幫忙"]


def test_audit_log_speaks_chinese_and_skips_read_only_posts(db):
    from app.services.admin_audit import describe, should_record
    assert describe("POST", "/api/dashboard/mode?mode=emergency") == "啟動緊急模式"
    assert describe("POST", "/api/resources/needs/abc-123/confirm_dispatch?expected_version=9f") == "核准派遣"
    assert describe("POST", "/api/resources/needs") == "新增需求"
    assert describe("PUT", "/api/workspaces/ws-1") == "儲存工作區"
    assert describe("DELETE", "/api/resources/res-1") == "刪除物資"
    assert describe("LINE", "求救已處理 need-1") == "求救已處理"
    assert describe("POST", "/api/unknown/thing") == "/api/unknown/thing"
    for path in ("/api/workspaces/analyze", "/api/workspaces/allocate", "/api/rag/query"):
        assert not should_record("POST", path)
    assert should_record("POST", "/api/workspaces/database-push")


def test_every_write_endpoint_has_a_readable_action_name():
    import re
    from app.main import app
    from app.services.admin_audit import describe, should_record
    missing = []
    for route in app.routes:
        for method in getattr(route, "methods", set()) & {"POST", "PUT", "PATCH", "DELETE"}:
            path = re.sub(r"\{[^}]+\}", "x1", route.path)
            if path == "/api/dashboard/mode":
                path += "?mode=emergency"
            if should_record(method, path) and describe(method, path) == path:
                missing.append(f"{method} {route.path}")
    assert not missing, missing
