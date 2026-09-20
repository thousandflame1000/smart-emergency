# -*- coding: utf-8 -*-
"""卡頓回歸：一則慢訊息不能凍住整台伺服器，對外呼叫都要有逾時。"""
import asyncio

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.routers import linebot as lb
from app.services import line_notify, places


def test_webhook_handlers_do_not_run_on_the_event_loop(monkeypatch):
    """同步的處理器（資料庫、LINE、地址查詢）直接跑在 async 路由裡會擋住事件迴圈。"""
    seen = {}

    def fake_handle(body, signature):
        try:
            asyncio.get_running_loop()
            seen["on_loop"] = True
        except RuntimeError:
            seen["on_loop"] = False

    monkeypatch.setattr(lb.handler, "handle", fake_handle)
    app = FastAPI()
    app.include_router(lb.router, prefix="/webhook")
    assert TestClient(app).post("/webhook/line", content=b"{}", headers={"X-Line-Signature": "x"}).status_code == 200
    assert seen == {"on_loop": False}


def test_line_client_is_shared_and_every_call_has_a_timeout(monkeypatch):
    monkeypatch.undo()
    monkeypatch.setattr(line_notify, "_api", None)
    api = line_notify._get_api()
    assert line_notify._get_api() is api, "每次新建連線會讓每則推播多一次 TLS 握手"

    captured = {}

    def fake_send(*args, _request_timeout=None, **kwargs):
        captured["timeout"] = _request_timeout
        raise RuntimeError("stop before the network")

    # 換掉底層送出函式後重建，確認包裝器有補上逾時
    monkeypatch.setattr(line_notify, "_api", None)
    from linebot.v3.messaging import ApiClient
    original = ApiClient.__init__

    def patched_init(self, *a, **kw):
        original(self, *a, **kw)
        self.rest_client.request = fake_send

    monkeypatch.setattr(ApiClient, "__init__", patched_init)
    fresh = line_notify._get_api()
    with pytest.raises(RuntimeError):
        fresh.get_profile("U-x")
    assert captured["timeout"] == line_notify.LINE_TIMEOUT
    monkeypatch.setattr(line_notify, "_api", None)


def test_geocoding_gives_up_quickly_and_remembers_failures(monkeypatch):
    monkeypatch.undo()
    calls = []

    def boom(request, timeout=None):
        calls.append(timeout)
        raise OSError("network down")

    monkeypatch.setattr(places, "urlopen", boom)
    monkeypatch.setattr(places, "_failed", {})
    monkeypatch.setattr(places, "_last_request", 0.0)
    assert places.geocode_address("台中市南區崇倫街88號") is None
    assert calls == [5], "等使用者回話的路徑不能等滿 15 秒"
    assert places.geocode_address("台中市南區崇倫街88號") is None
    assert calls == [5], "剛失敗過的地址短時間內不要再打一次外部服務"
