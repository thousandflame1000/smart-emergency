from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.database import SessionLocal
from app.models.outbox import OutboxMessage
from app.models.privacy import PrivacyConsent
from app.models.user import User
from app.models.webhook_event import WebhookEvent


def record_notice_acknowledgement(db: Session, user: User, *, source: str) -> None:
    existing = db.query(PrivacyConsent).filter(
        PrivacyConsent.user_id == user.id,
        PrivacyConsent.notice_version == settings.PRIVACY_NOTICE_VERSION,
    ).first()
    if existing:
        return
    db.add(PrivacyConsent(
        user_id=user.id,
        notice_version=settings.PRIVACY_NOTICE_VERSION,
        source=source,
    ))


def purge_expired_technical_records() -> dict[str, int]:
    """Delete delivery envelopes after their documented retention period."""
    now = datetime.now(UTC).replace(tzinfo=None)
    db = SessionLocal()
    try:
        webhook_count = db.query(WebhookEvent).filter(
            WebhookEvent.status.in_(("PROCESSED", "DEAD")),
            WebhookEvent.received_at < now - timedelta(days=settings.WEBHOOK_RETENTION_DAYS),
        ).delete(synchronize_session=False)
        outbox_count = db.query(OutboxMessage).filter(
            OutboxMessage.status.in_(("SENT", "DEAD")),
            OutboxMessage.created_at < now - timedelta(days=settings.OUTBOX_RETENTION_DAYS),
        ).delete(synchronize_session=False)
        db.commit()
        return {"webhook_events": webhook_count, "outbox_messages": outbox_count}
    finally:
        db.close()
