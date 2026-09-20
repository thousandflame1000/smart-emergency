from datetime import date, datetime
import logging
from sqlalchemy.orm import Session

from app.timeutil import now_utc, today_tw
from app.database import SessionLocal
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.services.line_notify import send_alert_message

logger = logging.getLogger(__name__)


HELP_REALERT_MINUTES = 30


def send_alerts_for_checkin(checkin_id, alert_type: str, db: Session) -> int:
    """即時發送警報（長者主動求助時呼叫）。回傳實際被通知的聯絡人數，
    呼叫端要用這個數字誠實告訴求助的人「有沒有人被通知到」。"""
    checkin = db.query(DailyCheckin).filter(DailyCheckin.id == checkin_id).first()
    if not checkin:
        return 0
    return _escalate(db, checkin, alert_type, now_utc())


def notify_admins(db: Session, text: str, buttons: list[dict] | None = None) -> int:
    """推播給所有綁了 LINE 的管理員。一鍵求助之前只通知照護聯絡人，長者沒有
    聯絡人時完全沒有任何人知道，管理員只能靠自己盯著後台。"""
    from app.models.user import User
    from app.services.line_notify import push_flex_message, send_text
    sent = 0
    card = None
    if buttons:
        from app.services.line_ops import bubble
        card = bubble("📣 需要您處理", "#c0392b", text.split("\n"), buttons)
    for admin in db.query(User).filter(User.role_filter("admin"), User.line_uid != None).all():
        try:
            if card:
                push_flex_message(admin.line_uid, text.split("\n")[0][:300], card)
            else:
                send_text(admin.line_uid, text)
            sent += 1
        except Exception as e:
            logger.error(f"[alert] 通知管理員失敗（{admin.name}）：{e}")
    return sent


def check_no_response() -> None:
    """Scheduler 每 15 分鐘呼叫：偵測未回應長者並升級通知"""
    db: Session = SessionLocal()
    try:
        today = today_tw()
        now = now_utc()

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


def _escalate(db: Session, checkin: DailyCheckin, alert_type: str, now: datetime) -> int:
    """發出警報（避免重複），回傳被通知的聯絡人數。"""
    already = (
        db.query(Alert)
        .filter(
            Alert.checkin_id == checkin.id,
            Alert.alert_type == alert_type,
        )
        .order_by(Alert.created_at.desc())
        .first()
    )
    if already:
        # 主動求助（help_needed）不能一天只通知一次：下午再求助一次就完全沒人
        # 收到——超過 HELP_REALERT_MINUTES 就視為新的一次求助，重新通知。
        stale = (
            alert_type == "help_needed"
            and already.created_at is not None
            and (now - already.created_at.replace(tzinfo=None)).total_seconds() > HELP_REALERT_MINUTES * 60
        )
        if not stale:
            return 0

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
            # 單一聯絡人發送失敗不該讓整個升級流程中斷——不然下面
            # 建立 Alert 記錄那段完全執行不到，這筆警報永遠不會被
            # 標記成「已發過」，排程每 15 分鐘重跑就會卡在同一步
            # 一直重試、其他排在後面的聯絡人也永遠收不到通知。
            try:
                send_alert_message(
                    line_uid=contact.line_uid,
                    elderly_name=checkin.elderly.name,
                    alert_type=alert_type,
                    checkin_id=str(checkin.id),
                )
                notified_ids.append(contact.id)
            except Exception as e:
                logger.error(f"[alert] 發送警報訊息失敗（{contact.name}）：{e}")

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
    return len(notified_ids)


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
