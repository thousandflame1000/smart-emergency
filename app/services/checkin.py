from datetime import date
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.user import User
from app.models.checkin import DailyCheckin
from app.services.line_notify import send_checkin_message


def send_daily_checkins() -> None:
    """Scheduler 每天早上呼叫：發打卡訊息給所有活躍長者"""
    db: Session = SessionLocal()
    try:
        today = date.today()
        elderly_list = (
            db.query(User)
            .filter(
                User.roles.contains(["elderly"]),
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

            send_checkin_message(elderly.line_uid, str(checkin.id))

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
