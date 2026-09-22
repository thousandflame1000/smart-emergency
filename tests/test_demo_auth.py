# -*- coding: utf-8 -*-
"""
決賽現場的臨時密碼閘（app/demo_auth.py）回歸測試。

背景：/admin 跟 / 都完全沒有登入驗證，決賽現場評審/觀眾用自己手機
打開網址就能看到長者姓名/電話/地址/GPS/打卡異常紀錄，或亂點正在
切換緊急模式、核准派遣或刪改資料。這裡驗證：
1. 沒設定 DEMO_PASSWORD 時完全不啟用（本地開發/一般測試不受影響）。
2. 設定了才會擋掉沒帶密碼或密碼錯的請求，兩個主控台頁面 + API 都要擋。
3. /webhook、/health 永遠不受影響（LINE 伺服器跟 Railway healthcheck
   沒辦法帶密碼）。
4. 帶對密碼可以正常通過。
"""
import base64

import pytest
from fastapi.testclient import TestClient

from app.demo_auth import settings


def _basic_auth_header(password: str, username: str = "demo") -> dict:
    token = base64.b64encode(f"{username}:{password}".encode()).decode()
    return {"Authorization": f"Basic {token}"}


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


@pytest.fixture(autouse=True)
def restore_demo_auth_settings():
    original = (settings.DEMO_PASSWORD, settings.APP_ENV, settings.ADMIN_LINE_LOGIN)
    settings.APP_ENV = "development"
    settings.ADMIN_LINE_LOGIN = False
    yield
    settings.DEMO_PASSWORD, settings.APP_ENV, settings.ADMIN_LINE_LOGIN = original


def test_disabled_by_default_does_not_block_anything(client):
    settings.DEMO_PASSWORD = ""
    assert client.get("/").status_code == 200
    assert client.get("/admin").status_code == 200
    assert client.get("/api/dashboard/summary").status_code == 200


def test_blocks_unauthenticated_requests_when_enabled(client):
    settings.DEMO_PASSWORD = "typhoon2026"
    r = client.get("/")
    assert r.status_code == 401
    assert "Basic" in r.headers.get("www-authenticate", "")

    r_admin = client.get("/admin")
    assert r_admin.status_code == 401

    r_api = client.get("/api/dashboard/summary")
    assert r_api.status_code == 401


def test_blocks_wrong_password(client):
    settings.DEMO_PASSWORD = "typhoon2026"
    r = client.get("/", headers=_basic_auth_header("wrong-password"))
    assert r.status_code == 401


def test_allows_correct_password(client):
    settings.DEMO_PASSWORD = "typhoon2026"
    r = client.get("/", headers=_basic_auth_header("typhoon2026"))
    assert r.status_code == 200

    r_admin = client.get("/admin", headers=_basic_auth_header("typhoon2026"))
    assert r_admin.status_code == 200

    r_api = client.get("/api/dashboard/summary", headers=_basic_auth_header("typhoon2026"))
    assert r_api.status_code == 200


def test_webhook_and_health_stay_exempt_even_when_enabled(client, db):
    settings.DEMO_PASSWORD = "typhoon2026"
    assert client.get("/health").status_code == 200
    # webhook 簽章驗證會擋掉沒有正確 LINE 簽章的請求，但不該是因為
    # 密碼閘擋下（不會是 401）
    r = client.post("/webhook/line", content=b"{}")
    assert r.status_code != 401


def test_production_without_auth_fails_closed_but_keeps_diagnostics_public(client):
    settings.APP_ENV = "production"
    settings.DEMO_PASSWORD = ""
    settings.ADMIN_LINE_LOGIN = False
    assert client.get("/").status_code == 503
    security = client.get("/api/system/security")
    assert security.status_code == 200
    assert security.json()["auth_mode"] == "locked"
    assert security.json()["locked"] is True
    assert security.json()["public_admin"] is False
    assert client.get("/api/rag/stats").status_code == 200
