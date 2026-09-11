# -*- coding: utf-8 -*-
"""
驗證限流設定：系統目前沒有登入驗證，/api/dashboard/mode 會廣播 LINE
訊息給所有真實用戶，是風險最高的端點，額外套用更嚴格的限流（見
app/rate_limit.py 的說明）。/health 必須完全不受限流影響，否則
Railway 的 healthcheck 會被誤判成服務掛掉而重啟部署。

這裡直接用 app.main.app（真正跑的那個 app 實例，包含完整的
middleware 疊層），而不是像其他測試那樣自己組一個乾淨的 FastAPI()
只掛單一 router——因為限流是掛在 app 層級的 middleware，不是掛在
單一 router 上，用隔離的 app 測不到真的限流行為。
"""
import pytest
from fastapi.testclient import TestClient

from app.rate_limit import limiter


@pytest.fixture(autouse=True)
def reset_rate_limiter():
    """限流的計數狀態是 process 內共用的全域物件，測試之間要重置，
    不然前一個測試打光額度，下一個測試會被誤傷。"""
    limiter.reset()
    yield
    limiter.reset()


@pytest.fixture
def client():
    from app.main import app
    return TestClient(app)


def test_health_endpoint_is_exempt_from_rate_limit(client):
    for _ in range(20):
        r = client.get("/health")
        assert r.status_code == 200, "健康檢查端點被限流了，Railway 會誤判服務掛掉並重啟部署"


def test_set_mode_is_rate_limited_after_5_calls_per_minute(client, db):
    statuses = [client.post("/api/dashboard/mode", params={"mode": "normal"}).status_code
                for _ in range(7)]
    assert statuses[:5] == [200] * 5, f"前 5 次應該都成功：{statuses}"
    assert statuses[5:] == [429, 429], f"第 6、7 次應該被限流擋下：{statuses}"


def test_normal_browsing_volume_is_not_rate_limited(client, db):
    """模擬一個人正常操作後台：切幾個分頁、各打幾個 API，
    總量遠低於全域限流的 60/分鐘，不該有任何一個被擋。"""
    for _ in range(20):
        r = client.get("/api/dashboard/summary")
        assert r.status_code == 200
