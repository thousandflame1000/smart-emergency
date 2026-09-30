# -*- coding: utf-8 -*-
"""整個網站只有一個入口：`/` 是外殼與單一側邊欄，內容在裡面切換；`/admin` 留白給之後的密碼管理。"""
import re

import pytest
from fastapi.testclient import TestClient

from app.config import settings


@pytest.fixture()
def web():
    from app.main import app
    return TestClient(app)


def test_root_is_one_shell_with_every_section_in_a_single_nav(web):
    page = web.get("/")
    assert page.status_code == 200 and "<iframe" in page.text
    for label in ("事件處置工作區", "總覽", "長者狀態", "趨勢分析", "人員管理",
                  "志工申請", "照護關係", "AI 助手", "知識庫"):
        assert label in page.text, label
    for removed in ("社區地圖", "情境模擬", "路網沙盤", "調度（需求・物資）"):
        assert removed not in page.text, removed
    assert "/admin" not in page.text, "外殼不能再把人導去另一個後台"


def test_shell_targets_only_pages_that_exist(web):
    shell = web.get("/").text
    targets = re.findall(r"page: '([^']+)'", shell)
    console, dashboard = web.get("/view/console").text, web.get("/view/dashboard").text
    for page in targets:
        if page.startswith("page-"):
            assert f'id="{page}"' in console, page
        else:
            assert f'id="page-{page}"' in dashboard, page


def test_both_views_can_be_embedded_and_hide_their_own_sidebar(web):
    for path in ("/view/console", "/view/dashboard"):
        html = web.get(path).text
        assert "classList.add('embed')" in html and ".embed .sidebar { display: none" in html
        assert "data.navigate" in html, "要能接收外殼的切換訊息"


def test_workspace_works_inside_the_shell(web):
    html = web.get("/workspace").text
    assert "classList.add('embed')" in html and 'href="/admin"' not in html


def test_mobile_shell_keeps_navigation_and_mode_labels_visible(web):
    html = web.get("/").text
    assert 'id="nav-toggle"' in html
    assert 'id="mobile-title"' in html
    assert 'id="mobile-mode-text">日常模式' in html
    assert 'aria-controls="sidebar"' in html
    assert ".nav .label" not in html, "行動版不能再把導覽文字整批隱藏"


def test_workspace_separates_core_actions_from_advanced_tools(web):
    html = web.get("/workspace").text
    assert 'class="advanced-tools"' in html
    assert 'id="sync-db" class="primary"' in html
    assert 'id="close-inspector"' in html and 'id="inspector-backdrop"' in html
    assert 'id="add-object"' in html and 'id="object-menu"' in html
    for kind in ("incident", "person", "supply", "facility", "custom"):
        assert f'data-add-kind="{kind}"' in html


def test_console_has_a_direct_mobile_navigation_drawer(web):
    html = web.get("/view/console").text
    assert 'id="admin-menu-button"' in html
    assert 'aria-controls="admin-sidebar"' in html
    assert 'id="admin-nav-scrim"' in html


def test_admin_path_is_blank_and_reserved_for_password_management(web):
    page = web.get("/admin")
    assert page.status_code == 200
    body = re.search(r"<body[^>]*>(.*)</body>", page.text, re.S).group(1)
    assert body.strip() == "", "/admin 要留白"


def test_line_login_and_logout_land_on_the_single_site(web, monkeypatch):
    from app import demo_auth
    monkeypatch.setattr(settings, "APP_ENV", "production")
    demo_auth.reset_cache()
    out = web.get("/admin/logout", follow_redirects=False)
    assert out.status_code == 303 and out.headers["location"] == "/"
    demo_auth.reset_cache()


def test_views_stay_behind_the_login_when_it_is_enabled(web, monkeypatch, db):
    from app import demo_auth
    from app.models.user import User
    db.add(User(name="管理員", roles=["admin"], line_uid="U-adm")); db.commit()
    monkeypatch.setattr(settings, "APP_ENV", "production")
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "")
    monkeypatch.setattr(settings, "ADMIN_LINE_LOGIN", True)
    demo_auth.reset_cache()
    for path in ("/", "/view/console", "/view/dashboard", "/admin"):
        assert web.get(path).status_code == 401, path
    assert web.get("/join").status_code == 200 and web.get("/f/need").status_code == 200
    demo_auth.reset_cache()


def test_osm_tile_layers_send_an_origin_referrer_despite_page_no_referrer_policy():
    """頁面預設 no-referrer，OSM 圖磚伺服器收不到 Referer 就回「blocked」圖磚；
    每個圖磚圖層都要自己送網域（不含路徑，表單 token 不會外洩）。"""
    import re
    from pathlib import Path
    static = Path(__file__).resolve().parents[1] / "app" / "static"
    layers = []
    for path in list(static.glob("*.html")) + list(static.glob("*.js")):
        layers += re.findall(r"L\.tileLayer\([^)]*\)", path.read_text(encoding="utf-8"))
    assert layers
    assert all("referrerPolicy:'strict-origin-when-cross-origin'" in layer for layer in layers)


def test_health_reports_deployed_commit(monkeypatch):
    from fastapi.testclient import TestClient
    from app.main import app
    monkeypatch.setenv("RAILWAY_GIT_COMMIT_SHA", "b3cf058deadbeef")
    assert TestClient(app).get("/health").json()["commit"] == "b3cf058"
    monkeypatch.delenv("RAILWAY_GIT_COMMIT_SHA")
    assert TestClient(app).get("/health").json()["commit"] == "local"


def test_browsers_get_a_readable_404_page_while_apis_keep_json():
    from fastapi.testclient import TestClient
    from app.main import app
    client = TestClient(app)
    page = client.get("/no-such-page", headers={"Accept": "text/html"})
    assert page.status_code == 404 and "找不到這個頁面" in page.text and 'href="/"' in page.text
    form = client.get("/f/unknown", headers={"Accept": "text/html"})
    assert form.status_code == 404 and "找不到這個表單" in form.text and "LINE" in form.text
    api = client.get("/api/resources/needs/nope/events", headers={"Accept": "text/html"})
    assert api.headers["content-type"].startswith("application/json")
    assert client.get("/no-such-page").json()["error"]
