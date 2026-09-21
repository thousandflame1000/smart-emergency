# -*- coding: utf-8 -*-
"""
決賽展演期間的臨時保護閘——不是真正的多使用者登入/權限系統，只是一組
共用密碼，擋掉一個很具體、很立即的風險：評審或觀眾在展演現場拿自己
的手機打開 `/` 或 `/admin` 的網址，看到長者姓名/電話/地址/GPS/打卡
異常紀錄這些個資，或亂點切換緊急模式、核准派遣、刪改資料。

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
import time

from starlette.middleware.base import BaseHTTPMiddleware
from starlette.requests import Request
from starlette.responses import Response

from app.config import settings
from app.services import admin_session

# /f/ 是機器人發給民眾與志工的網頁表單，身分由連結上的簽章保證，不能要求他們輸入展演密碼。
# /admin/login 是管理員用 LINE 連結換取登入 cookie 的入口，本身用簽章保護。
# /join 是公開的掃碼加入頁：只有兩個 QR Code，沒有任何資料，讓現場的人可以直接掃碼試用。
_EXEMPT_PREFIXES = ("/webhook", "/health", "/f/", "/admin/login", "/join")

_CACHE_SECONDS = 30
_cache: dict = {}


def reset_cache() -> None:
    _cache.clear()


def _cached(key, compute):
    hit = _cache.get(key)
    if hit and hit[0] > time.monotonic():
        return hit[1]
    value = compute()
    _cache[key] = (time.monotonic() + _CACHE_SECONDS, value)
    return value


def _admin_by_id(user_id: str):
    from app.database import SessionLocal
    from app.models.user import User

    def load():
        db = SessionLocal()
        try:
            user = db.query(User).filter(User.id == user_id).first()
            if user and user.is_active is not False and user.roles and "admin" in user.roles:
                return {"id": str(user.id), "name": user.name}
            return None
        finally:
            db.close()
    return _cached(("admin", user_id), load)


def line_login_enforced() -> bool:
    """Production with at least one admin bound to LINE: the console needs a LINE-issued login.
    With no such admin nobody could ever sign in, so it stays open (and the banner says so)."""
    if not settings.ADMIN_LINE_LOGIN or settings.APP_ENV != "production":
        return False

    def load():
        from app.database import SessionLocal
        from app.models.user import User
        db = SessionLocal()
        try:
            return db.query(User).filter(User.role_filter("admin"), User.line_uid != None,  # noqa: E711
                                         User.is_active != False).first() is not None  # noqa: E712
        finally:
            db.close()
    return _cached("enforced", load)


def auth_mode() -> str:
    if settings.DEMO_PASSWORD:
        return "password"
    return "line-admin" if line_login_enforced() else "open"


def _basic_password_ok(request: Request, password: str) -> bool:
    header = request.headers.get("authorization", "")
    if not header.startswith("Basic "):
        return False
    try:
        decoded = base64.b64decode(header[6:]).decode("utf-8")
        _, _, supplied = decoded.partition(":")
    except Exception:
        return False
    return hmac.compare_digest(supplied, password)


class DemoAuthMiddleware(BaseHTTPMiddleware):
    async def dispatch(self, request: Request, call_next):
        if request.url.path.startswith(_EXEMPT_PREFIXES):
            return await call_next(request)

        admin = None
        cookie = request.cookies.get(admin_session.COOKIE_NAME)
        if cookie:
            uid = admin_session.verify_session(cookie)
            admin = _admin_by_id(uid) if uid else None
        marker = admin_session.current_admin.set(admin)
        try:
            password = settings.DEMO_PASSWORD
            if admin or (password and _basic_password_ok(request, password)):
                return await call_next(request)
            if not password and not line_login_enforced():
                return await call_next(request)
            headers = {"WWW-Authenticate": 'Basic realm="linri-finals"'} if password else {}
            message = ("需要登入才能存取。管理員請在 LINE 傳「後台」取得登入連結。"
                       if not password else "需要密碼才能存取（決賽展演期間的臨時保護）")
            return Response(status_code=401, headers=headers, content=message,
                            media_type="text/plain; charset=utf-8")
        finally:
            admin_session.current_admin.reset(marker)
