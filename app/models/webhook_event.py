from sqlalchemy import Column, Integer, Text, TIMESTAMP, Index, CheckConstraint, text
from sqlalchemy.sql import func

from app.database import Base


class WebhookEvent(Base):
    """Durable LINE webhook inbox entry keyed by LINE's webhookEventId."""

    __tablename__ = "webhook_events"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PROCESSING','PROCESSED','FAILED','DEAD')",
            name="ck_webhook_events_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_webhook_events_attempt_count"),
        Index("ix_webhook_events_ready", "status", "available_at", "locked_at"),
    )

    id = Column(Text, primary_key=True)
    event_type = Column(Text, nullable=False)
    payload = Column(Text, nullable=False)
    status = Column(Text, nullable=False, default="PROCESSING", server_default=text("'PROCESSING'"))
    attempt_count = Column(Integer, nullable=False, default=1, server_default=text("1"))
    available_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    received_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    processed_at = Column(TIMESTAMP(), nullable=True)
    locked_at = Column(TIMESTAMP(), nullable=True)
    locked_by = Column(Text, nullable=True)
    last_error = Column(Text, nullable=True)
