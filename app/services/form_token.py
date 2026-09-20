# -*- coding: utf-8 -*-
"""每位使用者專屬、限時的網頁表單連結。

網頁表單要知道「是誰在填」，但表單是在 LINE 內建瀏覽器開的，瀏覽器裡沒有 LINE 登入狀態。
機器人在對話裡發連結時，把使用者的 LINE ID 和到期時間用伺服器密鑰簽章塞進網址，
表單送出時驗章即可確認身分。偽造或改動網址會驗章失敗，過期也會失效。
"""
import base64
import hashlib
import hmac
import json
import time

from app.config import settings

TOKEN_TTL_SECONDS = 3 * 60 * 60


def _key() -> bytes:
    secret = settings.TASK_COMMAND_SECRET or settings.LINE_CHANNEL_SECRET
    return hashlib.sha256(("webform:" + secret).encode()).digest()


def _b64(data: bytes) -> str:
    return base64.urlsafe_b64encode(data).rstrip(b"=").decode()


def _unb64(text: str) -> bytes:
    return base64.urlsafe_b64decode(text + "=" * (-len(text) % 4))


def make_token(line_uid: str, *, now: float | None = None) -> str:
    payload = _b64(json.dumps({"u": line_uid, "e": int((now or time.time()) + TOKEN_TTL_SECONDS)},
                              separators=(",", ":")).encode())
    sig = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
    return f"{payload}.{sig}"


def verify_token(token: str, *, now: float | None = None) -> str | None:
    """Return the LINE user id, or None when the token is forged, malformed or expired."""
    try:
        payload, sig = token.split(".", 1)
        expected = _b64(hmac.new(_key(), payload.encode(), hashlib.sha256).digest())
        if not hmac.compare_digest(sig, expected):
            return None
        data = json.loads(_unb64(payload))
        if data["e"] < (now or time.time()):
            return None
        return data["u"] or None
    except Exception:
        return None


def form_url(kind: str, line_uid: str) -> str:
    return f"{settings.PUBLIC_BASE_URL.rstrip('/')}/f/{kind}?t={make_token(line_uid)}"
