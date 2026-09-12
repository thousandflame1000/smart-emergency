# -*- coding: utf-8 -*-
"""
決賽展演期間的臨時保護閘——不是真正的多使用者登入/權限系統，只是一組
共用密碼，擋掉一個很具體、很立即的風險：評審或觀眾在展演現場拿自己
的手機打開 `/` 或 `/admin` 的網址，看到長者姓名/電話/地址/GPS/打卡
異常紀錄這些個資，或亂點正在展示的情境模擬、切換緊急模式、刪改資料。

`/` 跟 `/admin` 這兩頁其實都是完整的操作主控台（都能切換模式、確認
派遣），不是一個「公開」一個「後台」的區隔，所以兩者要同時保護。

刻意做成「夠用就好」而不是完整登入系統：
- 用瀏覽器原生 HTTP Basic Auth——瀏覽器對同一個網站只要輸入一次，
  之後整個瀏覽器工作階段都會自動帶密碼，適合「主持人開場輸入一次，
  接下來 10 分鐘展演都不用再管」的場景。
- 沒有設定 DEMO_PASSWORD 環境變數時完全不啟用，本地開發、pytest
  都不會被影響到，也不需要另外寫繞過測試的邏輯。
- LINE 官方伺服器呼叫 /webhook、Railway 用 /health 判斷服務存活
  （見 railway.toml healthcheckPath）都沒辦法帶密碼，明確排除在外。
"""
import base64
import hmac

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings

_EXEMPT_PREFIXES = ("/webhook", "/health")


class DemoAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        password = settings.DEMO_PASSWORD
        if not password or request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        supplied = ""
        header = request.headers.get("authorization", "")
        if header.startswith("Basic "):
            try:
                decoded = base64.b64decode(header[6:]).decode("utf-8")
                _, _, supplied = decoded.partition(":")
            except Exception:
                supplied = ""

        if hmac.compare_digest(supplied, password):
            return await call_next(request)

        return Response(
            status_code=401,
            headers={"WWW-Authenticate": 'Basic realm="linri-finals"'},
            content="需要密碼才能存取（決賽展演期間的臨時保護）",
        )
