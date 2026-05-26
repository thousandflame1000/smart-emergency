from sqlalchemy import Column, Text, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base_types import GUID, ArrayOfUUID


class Alert(Base):
    __tablename__ = "alerts"

    id             = Column(GUID(), primary_key=True, default=GUID.new)
    elderly_id     = Column(GUID(), ForeignKey("users.id"), nullable=False)
    checkin_id     = Column(GUID(), ForeignKey("daily_checkins.id"), nullable=True)
    alert_type     = Column(Text, nullable=False)
    notified_users = Column(ArrayOfUUID(), nullable=False, default=list)
    status         = Column(Text, nullable=False, default="sent")
    resolved_by    = Column(GUID(), ForeignKey("users.id"), nullable=True)
    resolved_at    = Column(TIMESTAMP(), nullable=True)
    created_at     = Column(TIMESTAMP(), server_default=func.now())

    elderly  = relationship("User", foreign_keys=[elderly_id], back_populates="alerts")
    checkin  = relationship("DailyCheckin", back_populates="alerts")
    resolver = relationship("User", foreign_keys=[resolved_by])
