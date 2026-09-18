from __future__ import annotations

from sqlalchemy import JSON, CheckConstraint, Column, Index, Integer, Text, TIMESTAMP, text
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID


JsonDocument = JSONB().with_variant(JSON(), "sqlite")


class OutboxMessage(Base):
    __tablename__ = "outbox_messages"
    __table_args__ = (
        CheckConstraint(
            "status IN ('PENDING','PROCESSING','SENT','FAILED','DEAD')",
            name="ck_outbox_messages_status",
        ),
        CheckConstraint("attempt_count >= 0", name="ck_outbox_messages_attempt_count"),
        Index("ix_outbox_ready", "status", "available_at", "created_at"),
        Index("ix_outbox_aggregate", "aggregate_type", "aggregate_id", "created_at"),
        Index("ix_outbox_lock", "status", "locked_at"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    aggregate_type = Column(Text, nullable=False)
    aggregate_id = Column(Text, nullable=False)
    channel = Column(Text, nullable=False)
    destination = Column(Text, nullable=False)
    message_type = Column(Text, nullable=False)
    payload = Column(JsonDocument, nullable=False, default=dict, server_default=text("'{}'"))
    status = Column(Text, nullable=False, default="PENDING", server_default=text("'PENDING'"))
    attempt_count = Column(Integer, nullable=False, default=0, server_default=text("0"))
    available_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    created_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    sent_at = Column(TIMESTAMP(), nullable=True)
    last_error = Column(Text, nullable=True)
    locked_at = Column(TIMESTAMP(), nullable=True)
    locked_by = Column(Text, nullable=True)
