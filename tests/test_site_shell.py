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
    for label in ("總覽", "長者狀態", "社區地圖", "趨勢分析", "長者管理", "志工 / 家屬", "照護關係",
                  "調度（需求・物資）", "營運工作區", "情境模擬", "AI 助手", "知識庫"):
        assert label in page.text, label
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
        assert "e.data.navigate" in html, "要能接收外殼的切換訊息"


def test_workspace_works_inside_the_shell(web):
    html = web.get("/workspace").text
    assert "classList.add('embed')" in html and 'href="/admin"' not in html


def test_admin_path_is_blank_and_reserved_for_password_management(web):
    page = web.get("/admin")
    assert page.status_code == 200
    body = re.search(r"<body[^>]*>(.*)</body>", page.text, re.S).group(1)
    assert body.strip() == "", "/admin 要留白"


def test_line_login_and_logout_land_on_the_single_site(web, monkeypatch):
    from app import demo_auth
    from app.services import admin_session
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
