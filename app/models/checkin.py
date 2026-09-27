from sqlalchemy import Column, Text, Date, ForeignKey, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base_types import GUID


class DailyCheckin(Base):
    __tablename__ = "daily_checkins"
    __table_args__ = (
        UniqueConstraint("elderly_id", "date", name="uq_daily_checkins_elderly_date"),
    )

    id           = Column(GUID(), primary_key=True, default=GUID.new)
    elderly_id   = Column(GUID(), ForeignKey("users.id"), nullable=False)
    date         = Column(Date, nullable=False)
    status       = Column(Text, nullable=False, default="pending")
    note         = Column(Text, nullable=True)
    prompt_sent_at = Column(TIMESTAMP(), nullable=True)
    responded_at = Column(TIMESTAMP(), nullable=True)
    confirmed_by = Column(GUID(), ForeignKey("users.id"), nullable=True)
    confirmed_at = Column(TIMESTAMP(), nullable=True)
    created_at   = Column(TIMESTAMP(), server_default=func.now())

    elderly   = relationship("User", foreign_keys=[elderly_id], back_populates="checkins")
    confirmer = relationship("User", foreign_keys=[confirmed_by])
    alerts    = relationship("Alert", back_populates="checkin")
