from sqlalchemy import Column, ForeignKey, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID


class PrivacyConsent(Base):
    __tablename__ = "privacy_consents"
    __table_args__ = (
        UniqueConstraint("user_id", "notice_version", name="uq_privacy_consent_user_version"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    user_id = Column(GUID(), ForeignKey("users.id", ondelete="CASCADE"), nullable=False)
    notice_version = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    granted_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
