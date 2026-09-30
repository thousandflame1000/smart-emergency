from sqlalchemy import Column, ForeignKey, Text, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID


class SafetyCheck(Base):
    """災時點名：每次啟動緊急模式是一輪（round_id＝啟動時間），每位長者一輪一筆，以最後一次回報為準。

    不沿用每日打卡：早上已經打卡「我很好」的人，災害發生後仍然要重新確認。"""
    __tablename__ = "safety_checks"
    __table_args__ = (UniqueConstraint("round_id", "user_id"),)

    id           = Column(GUID(), primary_key=True, default=GUID.new)
    round_id     = Column(Text, nullable=False)
    user_id      = Column(GUID(), ForeignKey("users.id"), nullable=False)
    status       = Column(Text, nullable=False)                 # ok / unwell / help
    via          = Column(Text, nullable=False, default="line")  # line / app / family / console
    marked_by    = Column(Text, nullable=True)                  # 代為確認的人（家屬、後台）
    responded_at = Column(TIMESTAMP(), server_default=func.now(), onupdate=func.now())

    user = relationship("User")
