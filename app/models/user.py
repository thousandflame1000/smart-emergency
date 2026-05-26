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
