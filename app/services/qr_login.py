"""電腦後台掃碼登入：電腦顯示 QR Code，管理員用手機 LINE 掃描、按送出，電腦自動登入。

跟 LINE 電腦版、WhatsApp Web 一樣的流程，不用密碼，也不用把登入連結從手機搬到電腦。
登入碼 3 分鐘有效、只能用一次，只有管理員（或基層員工）的 LINE 能核准。
"""
import json
import re
import secrets
from datetime import timedelta

from sqlalchemy.orm import Session

from app.models.config import SystemConfig
from app.timeutil import now_utc

TTL = timedelta(minutes=3)
PREFIX = "qrlogin:"
ALPHABET = "ABCDEFGHJKLMNPQRSTUVWXYZ23456789"  # 去掉容易看錯的 0/O、1/I
CODE_RE = re.compile(r"^登入\s*([A-Z2-9]{6})$")
ALLOWED_ROLES = {"admin", "field_staff"}


def _row(db: Session, code: str):
    return db.query(SystemConfig).filter(SystemConfig.key == PREFIX + code).first()


def _load(row) -> dict:
    try:
        return json.loads(row.value)
    except (TypeError, ValueError):
        return {}


def _expired(info: dict) -> bool:
    from datetime import datetime
    return datetime.fromisoformat(info.get("exp", "1970-01-01")) < now_utc()


def start(db: Session) -> str:
    for row in db.query(SystemConfig).filter(SystemConfig.key.like(PREFIX + "%")).all():
        if _expired(_load(row)):
            db.delete(row)
    code = "".join(secrets.choice(ALPHABET) for _ in range(6))
    db.add(SystemConfig(key=PREFIX + code, value=json.dumps({"exp": (now_utc() + TTL).isoformat(), "user_id": None})))
    db.commit()
    return code


def approve(db: Session, code: str, user) -> str:
    """手機 LINE 傳來「登入 碼」。回 ok / expired / forbidden。"""
    if not ALLOWED_ROLES & set(user.roles or []):
        return "forbidden"
    row = _row(db, code)
    info = _load(row) if row else {}
    if not row or _expired(info) or info.get("user_id"):
        return "expired"
    info["user_id"] = str(user.id)
    row.value = json.dumps(info)
    db.commit()
    return "ok"


def claim(db: Session, code: str) -> tuple[str, str | None]:
    """電腦端輪詢。回 (waiting|ok|expired, user_id)；ok 時登入碼作廢，不能再用。"""
    row = _row(db, code)
    info = _load(row) if row else {}
    if not row or _expired(info):
        return "expired", None
    if not info.get("user_id"):
        return "waiting", None
    db.delete(row)
    db.commit()
    return "ok", info["user_id"]
