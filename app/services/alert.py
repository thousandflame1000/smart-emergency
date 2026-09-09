from datetime import date, datetime
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.services.line_notify import send_alert_message


def send_alerts_for_checkin(checkin_id, alert_type: str, db: Session) -> None:
    """即時發送警報（長者主動求助時呼叫）"""
    checkin = db.query(DailyCheckin).filter(DailyCheckin.id == checkin_id).first()
    if not checkin:
        return
    _escalate(db, checkin, alert_type, datetime.now())


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

    # 依 notify_order 取聯絡人，再依警報類型分層篩選對象
    all_relations = (
        db.query(CareRelation)
        .filter(
            CareRelation.elderly_id == checkin.elderly_id,
            CareRelation.is_active == True,
        )
        .order_by(CareRelation.notify_order)
        .all()
    )
    relations = _relations_for_alert(all_relations, alert_type)

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


def _relations_for_alert(relations: list[CareRelation], alert_type: str) -> list[CareRelation]:
    """
    依警報類型分層篩選通報對象，對應計畫書「1 小時通知家屬、3 小時
    通知志工上門」的分級通報設計：
      no_response_1h → 優先通知家屬（含未標角色的聯絡人，寧可多通知）
      no_response_3h → 通知志工，請他們上門查看
      其他（help_needed 等主動求助）→ 不分層，全部通知
    任一層級篩不出符合角色的聯絡人時，退回通知全部聯絡人，
    避免因為角色資料沒填而完全沒人被通知。
    """
    def _roles(rel: CareRelation) -> list[str]:
        return (rel.contact.roles or []) if rel.contact else []

    if alert_type == "no_response_1h":
        tier = [r for r in relations if "volunteer" not in _roles(r) or "family" in _roles(r)]
    elif alert_type == "no_response_3h":
        tier = [r for r in relations if "volunteer" in _roles(r)]
    else:
        return relations

    return tier or relations
