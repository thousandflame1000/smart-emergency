"""災時點名（安否確認）。

參考 LINE 安否確認與日本「避難行動要支援者」制度：啟動緊急模式時，自動問每位長者「平安嗎」，
後台即時看到「平安／不舒服／需要協助／還沒回」的人數，還沒回的依脆弱度排序，讓志工先去敲
最需要的人的門。長者按「我平安」「不舒服」「需要幫忙」、家屬代為確認、後台打電話確認後標記，
都算回報。日常模式沒有點名，這裡的函式都是空操作。
"""
import logging

from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.models.config import SystemConfig
from app.models.safety_check import SafetyCheck
from app.models.user import User
from app.timeutil import now_utc

logger = logging.getLogger(__name__)

ROUND_KEY = "rollcall_round"
STATUS_LABEL = {"ok": "平安", "unwell": "不舒服", "help": "需要協助", "pending": "還沒回"}
PROMPT = ("🚨 社區緊急模式啟動\n\n請回報您是否平安：按下方「我平安」。\n"
          "身體不舒服或需要人幫忙，請按「需要幫忙」。\n生命危險請直接撥打 119。")


def _mode(db: Session) -> str:
    row = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    return row.value if row else "normal"


def current_round(db: Session) -> str | None:
    """進行中的點名：緊急模式時才有。"""
    if _mode(db) != "emergency":
        return None
    row = db.query(SystemConfig).filter(SystemConfig.key == ROUND_KEY).first()
    return row.value if row else None


def start(db: Session) -> str:
    """啟動緊急模式時開新的一輪。"""
    round_id = now_utc().isoformat()
    row = db.query(SystemConfig).filter(SystemConfig.key == ROUND_KEY).first()
    if row:
        row.value = round_id
    else:
        db.add(SystemConfig(key=ROUND_KEY, value=round_id))
    db.commit()
    return round_id


def note(db: Session, user: User, status: str, *, via: str = "line", marked_by: str | None = None) -> bool:
    """記下一位長者這一輪的回報，以最後一次為準。沒有點名或不是長者就什麼都不做。"""
    round_id = current_round(db)
    if not round_id or user is None or not user.has_role("elderly"):
        return False
    for _attempt in range(2):
        row = db.query(SafetyCheck).filter(SafetyCheck.round_id == round_id, SafetyCheck.user_id == user.id).first()
        if row is None:
            db.add(SafetyCheck(round_id=round_id, user_id=user.id, status=status, via=via, marked_by=marked_by))
        else:
            row.status, row.via, row.marked_by = status, via, marked_by
            row.responded_at = now_utc().replace(tzinfo=None)
        try:
            db.commit()
            return True
        except IntegrityError:
            # 連點兩下或家屬與本人同時回報：另一筆剛好先寫進去了，改成更新那一筆
            db.rollback()
    return False


def elders(db: Session) -> list[User]:
    return db.query(User).filter(User.role_filter("elderly"), User.is_active.is_(True)).all()


def board(db: Session) -> dict:
    """點名看板：人數與名單。還沒回的依脆弱度由高到低，需要協助的排在最前面。"""
    from app.services.dispatch import vulnerability_scorer
    round_id = current_round(db)
    if not round_id:
        return {"active": False}
    replies = {str(r.user_id): r for r in db.query(SafetyCheck).filter(SafetyCheck.round_id == round_id).all()}
    rank = {"help": 0, "pending": 1, "unwell": 2, "ok": 3}
    vulnerability = vulnerability_scorer(db)
    people = []
    for u in elders(db):
        reply = replies.get(str(u.id))
        status = reply.status if reply else "pending"
        people.append({
            "id": str(u.id), "name": u.name, "address": u.address or "", "phone": u.phone or "",
            "line": bool(u.line_uid), "lat": u.lat, "lng": u.lng, "status": status,
            "status_label": STATUS_LABEL[status], "via": reply.via if reply else None,
            "marked_by": reply.marked_by if reply else None,
            "responded_at": reply.responded_at.isoformat() + "+00:00" if reply and reply.responded_at else None,
            "vulnerability": round(vulnerability(u.id), 1),
        })
    people.sort(key=lambda p: (rank[p["status"]], -p["vulnerability"], p["name"]))
    counts = {key: sum(p["status"] == key for p in people) for key in STATUS_LABEL}
    return {"active": True, "started_at": round_id, "total": len(people), "counts": counts, "people": people}


NEARBY_KM = 2.0


def nearby_unanswered(db: Session, volunteer: User, limit: int = 5) -> list[dict]:
    """志工附近還沒回報（或需要協助）的長者，由近到遠。災時名冊交給在地志工挨家確認，
    跟日本「避難行動要支援者名簿」災時提供給自主防災組織是同一個道理；日常模式看不到。"""
    from app.services.geo import haversine_km
    if volunteer.lat is None or volunteer.lng is None:
        return []
    board_ = board(db)
    if not board_.get("active"):
        return []
    rows = []
    for p in board_["people"]:
        if p["status"] not in ("pending", "help") or p["lat"] is None or p["id"] == str(volunteer.id):
            continue
        km = haversine_km(volunteer.lat, volunteer.lng, p["lat"], p["lng"])
        if km <= NEARBY_KM:
            rows.append({**p, "km": km})
    rows.sort(key=lambda p: (p["status"] != "help", p["km"]))
    return rows[:limit]


def ask(db: Session, only_pending: bool = False) -> list[str]:
    """推點名卡給長者；only_pending 時只推還沒回的（「再問一次」）。回傳真的推出去的 LINE uid。"""
    from app.services.line_notify import push_flex_message
    from app.services.line_ops import bubble
    round_id = current_round(db)
    if not round_id:
        return []
    answered = {str(r.user_id) for r in db.query(SafetyCheck).filter(SafetyCheck.round_id == round_id).all()}
    card = bubble("🚨 請回報是否平安", "#c0392b", PROMPT.split("\n")[2:],
                  [{"label": "✅ 我平安", "data": "action=safe", "color": "#13795b"},
                   {"label": "🆘 需要幫忙", "text": "需要幫忙", "color": "#c0392b"},
                   {"label": "📦 查詢物資", "text": "查詢物資"}])
    sent = []
    for u in elders(db):
        if not u.line_uid or (only_pending and str(u.id) in answered):
            continue
        try:
            push_flex_message(u.line_uid, "🚨 請回報是否平安", card)
            sent.append(u.line_uid)
        except Exception:
            logger.warning("roll call push failed for %s", u.id, exc_info=True)
    return sent
