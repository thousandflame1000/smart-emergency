# -*- coding: utf-8 -*-
"""
驗證 /api/dashboard/users/{id} 的 PUT 可以修改 roles。

背景：LINE bot 自動註冊的新用戶預設一律是 elderly（見
app/routers/linebot.py 的 handle_text），如果實際傳訊息的人其實是
志工/家屬/管理員在測試，之前完全沒有辦法把角色改回來——不管是 API
還是後台 UI 都沒有這個能力，只能整筆刪掉重建。這在正式環境真的
發生過：一個用真實 LINE 帳號測試的人被誤判成「長者」，系統排程
連續一個月每天發打卡訊息、累積大量未回應警報。
"""
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import dashboard as dashboard_router


def make_client():
    app = FastAPI()
    app.include_router(dashboard_router.router, prefix="/api/dashboard")
    return TestClient(app)


def test_update_user_can_change_roles(db):
    client = make_client()
    created = client.post(
        "/api/dashboard/users?name=誤判長者&roles=elderly"
    ).json()

    r = client.put(f"/api/dashboard/users/{created['id']}?roles=admin")
    assert r.status_code == 200, r.text

    updated = client.get("/api/dashboard/users").json()
    user = next(u for u in updated if u["id"] == created["id"])
    assert user["roles"] == ["admin"]


def test_update_user_without_roles_param_leaves_roles_unchanged(db):
    client = make_client()
    created = client.post(
        "/api/dashboard/users?name=不改角色&roles=volunteer"
    ).json()

    r = client.put(f"/api/dashboard/users/{created['id']}?phone=0912345678")
    assert r.status_code == 200

    updated = client.get("/api/dashboard/users").json()
    user = next(u for u in updated if u["id"] == created["id"])
    assert user["roles"] == ["volunteer"], "沒有傳 roles 參數就不該動到原本的角色"
    assert user["phone"] == "0912345678"
