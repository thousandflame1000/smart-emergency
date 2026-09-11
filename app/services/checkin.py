from datetime import date
import logging
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.user import User
from app.models.checkin import DailyCheckin
from app.services.line_notify import send_checkin_message

logger = logging.getLogger(__name__)


def send_daily_checkins() -> None:
    """Scheduler 每天早上呼叫：發打卡訊息給所有活躍長者"""
    db: Session = SessionLocal()
    try:
        today = date.today()
        elderly_list = (
            db.query(User)
            .filter(
                User.role_filter("elderly"),
                User.is_active == True,
                User.line_uid != None,
            )
            .all()
        )

        for elderly in elderly_list:
            # 避免重複發送
            exists = (
                db.query(DailyCheckin)
                .filter(
                    DailyCheckin.elderly_id == elderly.id,
                    DailyCheckin.date == today,
                )
                .first()
            )
            if exists:
                continue

            checkin = DailyCheckin(
                elderly_id=elderly.id,
                date=today,
                status="pending",
            )
            db.add(checkin)
            db.commit()
            db.refresh(checkin)

            # 單一使用者發送失敗（LINE ID 失效、API 暫時性錯誤…）不該讓
            # 整個迴圈中斷——後面排隊的其他長者當天就完全收不到打卡
            # 訊息了。打卡記錄已經建立，只是這次推播沒送到，不影響
            # 之後補打卡或管理員後台判斷。
            try:
                send_checkin_message(elderly.line_uid, str(checkin.id))
            except Exception as e:
                logger.error(f"[checkin] 發送打卡訊息失敗（{elderly.name}）：{e}")

    finally:
        db.close()


def mark_checkin(checkin_id: str, status: str, db: Session) -> DailyCheckin | None:
    """長者按下按鈕後更新打卡狀態"""
    from datetime import datetime

    checkin = db.query(DailyCheckin).filter(
        DailyCheckin.id == checkin_id
    ).first()

    if not checkin:
        return None

    checkin.status = status
    checkin.responded_at = datetime.now()
    db.commit()
    db.refresh(checkin)
    return checkin


def confirm_safe(checkin_id: str, confirmed_by_id: str, db: Session) -> DailyCheckin | None:
    """志工/家屬確認長者安全 → 同時關閉所有相關 Alert"""
    from datetime import datetime
    from app.models.alert import Alert

    checkin = db.query(DailyCheckin).filter(
        DailyCheckin.id == checkin_id
    ).first()

    if not checkin:
        return None

    now = datetime.now()
    checkin.status = "confirmed_safe"
    checkin.confirmed_by = confirmed_by_id
    checkin.confirmed_at = now

    # 自動關閉所有未解決的相關警報
    db.query(Alert).filter(
        Alert.checkin_id == checkin.id,
        Alert.status == "sent",
    ).update({"status": "resolved", "resolved_by": confirmed_by_id, "resolved_at": now})

    db.commit()
    db.refresh(checkin)
    return checkin
