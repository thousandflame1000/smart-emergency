from datetime import date, datetime
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.services.line_notify import send_alert_message


def check_no_response() -> None:
    """Scheduler 每 15 分鐘呼叫：偵測未回應長者並升級通知"""
    db: Session = SessionLocal()
    try:
        today = date.today()
        now = datetime.now()

        pending = (
            db.query(DailyCheckin)
            .filter(
                DailyCheckin.date == today,
                DailyCheckin.status == "pending",
            )
            .all()
        )

        for checkin in pending:
            elapsed_min = int((now - checkin.created_at.replace(tzinfo=None)).total_seconds() / 60)

            if elapsed_min >= 60:
                _escalate(db, checkin, "no_response_1h", now)

            if elapsed_min >= 180:
                _escalate(db, checkin, "no_response_3h", now)

    finally:
        db.close()


def _escalate(db: Session, checkin: DailyCheckin, alert_type: str, now: datetime) -> None:
    """發出警報（避免重複）"""
    already = (
        db.query(Alert)
        .filter(
            Alert.checkin_id == checkin.id,
            Alert.alert_type == alert_type,
        )
        .first()
    )
    if already:
        return

    # 依 notify_order 取聯絡人
    relations = (
        db.query(CareRelation)
        .filter(
            CareRelation.elderly_id == checkin.elderly_id,
            CareRelation.is_active == True,
        )
        .order_by(CareRelation.notify_order)
        .all()
    )

    notified_ids = []
    for rel in relations:
        contact = rel.contact
        if contact and contact.line_uid:
            send_alert_message(
                line_uid=contact.line_uid,
                elderly_name=checkin.elderly.name,
                alert_type=alert_type,
                checkin_id=str(checkin.id),
            )
            notified_ids.append(contact.id)

    alert = Alert(
        elderly_id=checkin.elderly_id,
        checkin_id=checkin.id,
        alert_type=alert_type,
        notified_users=notified_ids,
        status="sent",
    )
    db.add(alert)

    # 超過 3 小時標記為 no_response
    if alert_type == "no_response_3h":
        checkin.status = "no_response"

    db.commit()
