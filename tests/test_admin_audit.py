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
