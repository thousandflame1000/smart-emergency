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

from app.rate_limit import limiter, _client_ip_key


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


class _FakeClient:
    host = "127.0.0.1"


class _FakeRequest:
    """最小化模擬 Starlette Request，只提供 _client_ip_key 用得到的
    headers / client 屬性。"""
    def __init__(self, headers=None, client_host="127.0.0.1"):
        self.headers = headers or {}
        self.client = _FakeClient()
        self.client.host = client_host


def test_key_func_prefers_x_real_ip_over_direct_tcp_peer():
    """
    這是這次正式環境限流完全沒生效的根因回歸測試：Railway 的 edge
    網路透過會逐次變動的內部 CGNAT 位址（100.64.0.0/10）轉發請求，
    如果直接用 request.client.host 當限流 key，每個請求都會被當成
    不同來源，計數永遠不會累積。X-Real-IP 是 Railway 依照它自己
    觀察到的真實來源設定的，才是穩定可信的 key。
    """
    req = _FakeRequest(
        headers={"x-real-ip": "218.166.140.120", "x-forwarded-for": "218.166.140.120, 79.127.228.18"},
        client_host="100.64.0.4",  # 模擬 Railway 每次都不同的內部位址
    )
    assert _client_ip_key(req) == "218.166.140.120"

    req2 = _FakeRequest(
        headers={"x-real-ip": "218.166.140.120", "x-forwarded-for": "218.166.140.120, 79.127.228.17"},
        client_host="100.64.0.7",  # 下一個請求，內部位址變了
    )
    assert _client_ip_key(req2) == "218.166.140.120", "同一個真實使用者，兩次請求應該解析出同一個 key"


def test_key_func_falls_back_to_forwarded_for_first_hop_without_real_ip():
    req = _FakeRequest(headers={"x-forwarded-for": "203.0.113.5, 10.0.0.1"})
    assert _client_ip_key(req) == "203.0.113.5"


def test_key_func_falls_back_to_client_host_for_local_dev():
    """本機開發沒有任何 proxy header，行為要跟原本一樣直接用
    request.client.host。"""
    req = _FakeRequest(headers={}, client_host="127.0.0.1")
    assert _client_ip_key(req) == "127.0.0.1"
