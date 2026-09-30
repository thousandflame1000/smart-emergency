from sqlalchemy import Column, Text, TIMESTAMP
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID


class DutyLog(Base):
    """值班紀事：來電、決策、現場狀況這類系統不會自己記到的事，併進災情摘要的時間軸。

    只增不改：跟紙本值班日誌一樣，記錯就再記一筆更正，事後才查得到當時知道什麼。"""
    __tablename__ = "duty_logs"

    id         = Column(GUID(), primary_key=True, default=GUID.new)
    text       = Column(Text, nullable=False)
    author     = Column(Text, nullable=False)
    created_at = Column(TIMESTAMP(), server_default=func.now())
