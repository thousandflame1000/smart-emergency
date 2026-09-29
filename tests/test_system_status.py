"""後台「系統狀態」：設定好時沒有紅燈，常見錯誤各自亮紅燈並附修正方式。"""
from types import SimpleNamespace

import pytest

from app.config import settings
from app.models.care_relation import CareRelation
from app.models.knowledge import KnowledgeChunk
from app.models.outbox import OutboxMessage
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.services import line_notify, system_status


@pytest.fixture(autouse=True)
def fresh_line_cache():
    system_status._line_cache.update(at=0.0, result=None)
    yield
    system_status._line_cache.update(at=0.0, result=None)


class _Bot:
    def get_bot_info(self):
        return SimpleNamespace(display_name="鄰里守望", basic_id="@571hpppb")


def _healthy(db, monkeypatch):
    monkeypatch.setattr(line_notify, "_get_api", lambda: _Bot())
    monkeypatch.setattr(settings, "EXTERNAL_AI_ENABLED", True)
    monkeypatch.setattr(settings, "GEMINI_API_KEY", "key")
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "pw")
    admin = User(name="管理員", roles=["admin"], line_uid="U-a")
    elder = User(name="王奶奶", roles=["elderly"])
    fam = User(name="女兒", roles=["family"])
    db.add_all([admin, elder, fam]); db.commit()
    db.add_all([CareRelation(elderly_id=elder.id, contact_id=fam.id, relation="family"),
                KnowledgeChunk(content="CPR", source="AHA", category="first_aid", embedding="[1]"),
                ResourcePoint(name="光復國小", point_type="shelter", lat=23.67, lng=121.42)])
    db.commit()


def _by_key(db):
    return {i["key"]: i for i in system_status.checks(db)}


def test_a_configured_site_has_no_red_items(db, monkeypatch):
    _healthy(db, monkeypatch)
    items = _by_key(db)
    assert not [k for k, i in items.items() if i["level"] == "error" and k != "scheduler"], items
    assert items["line"]["detail"].startswith("已連線") and items["ai"]["level"] == "ok"


def test_common_misconfigurations_turn_red_with_a_fix(db, monkeypatch):
    _healthy(db, monkeypatch)
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "")
    monkeypatch.setattr(settings, "ADMIN_LINE_LOGIN", False)
    from app import demo_auth
    demo_auth.reset_cache()
    db.query(User).filter(User.line_uid == "U-a").update({"line_uid": None})
    db.add(OutboxMessage(aggregate_type="alert", aggregate_id="1", channel="line", destination="U-x",
                         message_type="TEXT", payload={}, status="DEAD", last_error="401 invalid token"))
    db.commit()
    monkeypatch.setattr(line_notify, "_get_api", lambda: (_ for _ in ()).throw(RuntimeError("401")))
    items = _by_key(db)
    for key in ("auth", "admins", "outbox", "line"):
        assert items[key]["level"] == "error" and items[key]["hint"], key
    assert "401 invalid token" in items["outbox"]["detail"]
    demo_auth.reset_cache()


def test_status_endpoint_needs_an_admin(db, monkeypatch):
    from fastapi.testclient import TestClient
    from app import demo_auth
    from app.main import app
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "pw")
    demo_auth.reset_cache()
    assert TestClient(app).get("/api/system/status").status_code == 401
    demo_auth.reset_cache()


def test_line_errors_are_explained_not_dumped(monkeypatch):
    class Unauthorized(Exception):
        status = 401
    monkeypatch.setattr(line_notify, "_get_api", lambda: (_ for _ in ()).throw(Unauthorized("HTTPHeaderDict(...)")))
    item = system_status._line_check()
    assert item["detail"] == "LINE 拒絕權杖（401 未授權）" and "HTTPHeaderDict" not in item["detail"]


def test_shelters_far_from_every_elder_are_flagged(db):
    db.add_all([User(name="玉里阿嬤", roles=["elderly"], lat=23.334, lng=121.318),
                ResourcePoint(name="台中避難所", point_type="shelter", lat=24.14, lng=120.68)])
    db.commit()
    item = _by_key(db)["coverage"]
    assert item["level"] == "warn" and "公里" in item["detail"] and item["hint"]
    db.add(ResourcePoint(name="玉里國小", point_type="shelter", lat=23.336, lng=121.314)); db.commit()
    assert _by_key(db)["coverage"]["level"] == "ok"


def test_outdated_line_menu_is_spotted(monkeypatch):
    from app.services import rich_menu

    def menus(texts_by_name):
        return SimpleNamespace(richmenus=[
            SimpleNamespace(name=name, areas=[SimpleNamespace(action=SimpleNamespace(text=t)) for t in texts])
            for name, texts in texts_by_name.items()])

    current = {name: [c[4] for row in spec["rows"] for c in row] for name, spec in rich_menu.MENUS.items()}
    old = {name: [t for t in texts if t != "查詢物資"] + ["分享位置"] for name, texts in current.items()}
    for live, level in ((old, "warn"), (current, "ok")):
        api = SimpleNamespace(get_rich_menu_list=lambda live=live: menus(live))
        monkeypatch.setattr(rich_menu, "_apis", lambda api=api: (api, None))
        assert system_status._menu_check()["level"] == level
