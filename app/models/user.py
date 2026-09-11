import json
from sqlalchemy import Column, Text, Boolean, Float, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base_types import GUID, ArrayOfText


class User(Base):
    __tablename__ = "users"

    id         = Column(GUID(), primary_key=True, default=GUID.new)
    line_uid   = Column(Text, unique=True, nullable=True)
    name       = Column(Text, nullable=False)
    phone      = Column(Text, nullable=True)
    roles      = Column(ArrayOfText(), nullable=False, default=list)
    lat        = Column(Float, nullable=True)
    lng        = Column(Float, nullable=True)
    address    = Column(Text, nullable=True)
    is_active  = Column(Boolean, nullable=False, default=True)
    created_at = Column(TIMESTAMP(), server_default=func.now())

    checkins  = relationship("DailyCheckin", back_populates="elderly",
                             foreign_keys="DailyCheckin.elderly_id")
    alerts    = relationship("Alert", back_populates="elderly",
                             foreign_keys="Alert.elderly_id")
    resources = relationship("CommunityResource", back_populates="owner")
    needs     = relationship("CommunityNeed", back_populates="requester")

    def has_role(self, role: str) -> bool:
        return role in (self.roles or [])

    @classmethod
    def role_filter(cls, role: str):
        """
        查詢用的角色篩選條件（給 .filter() 用，不是 Python 端的 has_role()）。

        roles 欄位用 ArrayOfText 存成 JSON 字串（如 '["elderly","volunteer"]'）。
        直接 `roles.contains([role])` 會把 [role] 也編碼成 JSON 字串再做 LIKE
        比對，結果變成只比對「唯一角色剛好等於 role」的字串，同時有多個角色的
        使用者（例如同時是 elderly + volunteer）會被漏掉查不到——這是曾經真實
        存在的 bug：多重角色的長者會從 /api/dashboard/elderly 清單、每日打卡
        排程 (send_daily_checkins) 中完全消失。

        這裡改成用「帶引號的角色片段」（例如 '"elderly"'）去 LIKE 比對，
        json.dumps 出來剛好是合法 JSON 字串，會被 ArrayOfText.process_bind_param
        原樣保留，不會再被重新包成單一元素陣列。
        """
        return cls.roles.contains(json.dumps(role))
