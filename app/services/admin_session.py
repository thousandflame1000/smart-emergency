# -*- coding: utf-8 -*-
"""管理員用 LINE 身分登入後台。

後台一直沒有登入：任何人有網址就能改資料，稽核紀錄也看不出是誰做的。這裡不新增密碼系統，
而是讓管理員在 LINE 傳「後台」，機器人回一條 10 分鐘有效、簽章過的登入連結；點開後換成
12 小時的登入 cookie。這樣後台知道操作者是哪位管理員，派遣紀錄也會記下他的名字。
"""
import base64
import hashlib
import hmac
import json
import time
from contextvars import ContextVar

from app.config import settings

LOGIN_TTL = 10 * 60
SESSION_TTL = 12 * 60 * 60
COOKIE_NAME = "admin_session"

# 目前這個請求是哪位管理員（沒登入為 None）。派遣稽核紀錄會拿來記名字。
current_admin: ContextVar[dict | None] = ContextVar("current_admin", default=None)


def _key(purpose: str) -> bytes:
    secret = settings.TASK_COMMAND_SECRET or settings.LINE_CHANNEL_SECRET
    return hashlib.sha256((f"admin-{purpose}:" + secret).encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def _sign(purpose: str, user_id: str, ttl: int, now: float | None = None) -> str:
    payload = _b64(json.dumps({"u": user_id, "e": int((now or time.time()) + ttl), "p": purpose},
                              separators=(",", ":")).encode())
    return f"{payload}.{_b64(hmac.new(_key(purpose), payload.encode(), hashlib.sha256).digest())}"


def _verify(purpose: str, token: str, now: float | None = None) -> str | None:
    try:
        payload, sig = token.split(".", 1)
        if not hmac.compare_digest(sig, _b64(hmac.new(_key(purpose), payload.encode(), hashlib.sha256).digest())):
            return None
        data = json.loads(_unb64(payload))
        if data["p"] != purpose or data["e"] < (now or time.time()):
            return None
        return data["u"] or None
    except Exception:
        return None


def make_login_token(user_id: str, now: float | None = None) -> str:
    return _sign("login", user_id, LOGIN_TTL, now)


def verify_login_token(token: str, now: float | None = None) -> str | None:
    return _verify("login", token, now)


def make_session(user_id: str, now: float | None = None) -> str:
    return _sign("session", user_id, SESSION_TTL, now)


def verify_session(token: str, now: float | None = None) -> str | None:
    return _verify("session", token, now)


def login_url(user_id: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/admin/login?t={make_login_token(user_id)}"
