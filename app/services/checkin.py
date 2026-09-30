import logging
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session

from app.timeutil import now_utc, today_tw
from app.database import SessionLocal
from app.models.user import User
from app.models.checkin import DailyCheckin
from app.services.line_notify import send_checkin_message
from app.services.outbox import OutboxService, finish_inline_delivery

logger = logging.getLogger(__name__)


def send_daily_checkins() -> None:
    """Scheduler 每天早上呼叫：發打卡訊息給所有活躍長者"""
    db: Session = SessionLocal()
    try:
        today = today_tw()
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
            try:
                db.flush()
                delivery = OutboxService(db).enqueue_checkin(checkin, elderly)
                delivery_id = str(delivery.id)
                db.commit()
            except IntegrityError:
                # 多個 web process 的排程可能同時啟動；唯一約束讓後到者安全略過。
                db.rollback()
                continue

            if delivery.status == "PROCESSING":
                try:
                    send_checkin_message(elderly.line_uid, str(checkin.id))
                except Exception as exc:
                    finish_inline_delivery(db, delivery_id, exc)
                    logger.error("[checkin] 發送打卡訊息失敗（%s）：%s", elderly.name, exc)
                else:
                    checkin.prompt_sent_at = now_utc()
                    finish_inline_delivery(db, delivery_id)

    finally:
        db.close()


def record_ok(db: Session, user) -> None:
    """長者主動回報平安。今天的打卡存在就更新；還沒有（例如早上 8 點前先回報，或排程沒發）
    就直接記一筆，之前按了「我很好」卻什麼都沒存，畫面還是「今天還沒回報」。"""
    row = db.query(DailyCheckin).filter(DailyCheckin.elderly_id == user.id, DailyCheckin.date == today_tw()).first()
    if row is None:
        if not user.has_role("elderly"):
            return
        row = DailyCheckin(elderly_id=user.id, date=today_tw(), status="pending")
        db.add(row)
        db.commit()
        db.refresh(row)
    if row.status in ("pending", "no_response"):
        mark_checkin(str(row.id), "ok", db)
    from app.services import rollcall
    rollcall.note(db, user, "ok")


def mark_checkin(checkin_id: str, status: str, db: Session) -> DailyCheckin | None:
    """長者按下按鈕後更新打卡狀態"""

    checkin = db.query(DailyCheckin).filter(
        DailyCheckin.id == checkin_id
    ).first()

    if not checkin:
        return None

    was_alerted = False
    if status == "ok":
        from app.models.alert import Alert
        open_alerts = db.query(Alert).filter(Alert.checkin_id == checkin.id, Alert.status == "sent").all()
        was_alerted = bool(open_alerts)
        recipients = {uid for a in open_alerts for uid in (a.notified_users or [])}
        for a in open_alerts:
            a.status = "resolved"
            a.resolved_at = now_utc()
    checkin.status = status
    checkin.responded_at = now_utc()
    db.commit()
    db.refresh(checkin)
    if was_alerted and recipients:
        # 長者遲到才回報平安：之前警報永遠停在「未解除」，家屬也不知道人其實沒事。
        from app.models.user import User
        from app.services.outbox import send_text_reliably
        elder_name = checkin.elderly.name if checkin.elderly else "長者"
        for contact in db.query(User).filter(User.id.in_(list(recipients))).all():
            if contact.line_uid:
                content = f"✅ {elder_name} 已回報平安，先前的未回應警報已解除，不用再擔心了。"
                if not send_text_reliably(
                    aggregate_type="DailyCheckin",
                    aggregate_id=str(checkin.id),
                    destination=contact.line_uid,
                    content=content,
                    dedupe_key=f"checkin-resolved:{checkin.id}:{contact.id}",
                ):
                    logger.warning("[checkin] 聯絡人平安通知已排入重試（%s）", contact.name)
    return checkin


def confirm_safe(checkin_id: str, confirmed_by_id: str, db: Session) -> DailyCheckin | None:
    """志工/家屬確認長者安全 → 同時關閉所有相關 Alert"""
    from app.models.alert import Alert

    checkin = db.query(DailyCheckin).filter(
        DailyCheckin.id == checkin_id
    ).first()

    if not checkin:
        return None

    now = now_utc()
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
