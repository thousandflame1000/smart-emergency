# -*- coding: utf-8 -*-
"""
驗證 /api/dashboard/users 的建立使用者端點。

這裡曾經有一個嚴重的正式 bug：create_user 的 `roles: list[str]` 參數
沒有標成 Query()，FastAPI 因此把它當成 request body 處理，但
admin.html 的 saveUser() 是用 URLSearchParams 把 roles 當成一般查詢
字串參數送出——導致「新增使用者」這個管理後台的核心功能完全打不通
（永遠 422），而且是一叫就 500 或 422，不是隱性錯誤。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import dashboard as dashboard_router


def make_client():
    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api/dashboard")
    return TestClient(app)


def test_create_user_accepts_roles_as_query_param_like_admin_ui_sends():
    client = make_client()
    # admin.html 的 saveUser() 實際送出的格式：URLSearchParams({name, roles: 單一字串, ...})
    r = client.post("/api/dashboard/users?name=志工阿明&roles=volunteer&lat=24.15&lng=120.68")
    assert r.status_code == 200, r.text
    data = r.json()
    assert data["name"] == "志工阿明"
    assert data["roles"] == ["volunteer"]


def test_create_user_missing_name_is_a_clean_422():
    client = make_client()
    r = client.post("/api/dashboard/users?roles=volunteer")
    assert r.status_code == 422
