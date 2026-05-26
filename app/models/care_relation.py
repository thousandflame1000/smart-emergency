from sqlalchemy import Column, Text, Integer, Boolean, ForeignKey, TIMESTAMP, UniqueConstraint
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base_types import GUID


class CareRelation(Base):
    __tablename__ = "care_relations"
    __table_args__ = (UniqueConstraint("elderly_id", "contact_id"),)

    id           = Column(GUID(), primary_key=True, default=GUID.new)
    elderly_id   = Column(GUID(), ForeignKey("users.id"), nullable=False)
    contact_id   = Column(GUID(), ForeignKey("users.id"), nullable=False)
    relation     = Column(Text, nullable=False)
    notify_order = Column(Integer, nullable=False, default=1)
    is_active    = Column(Boolean, nullable=False, default=True)
    created_at   = Column(TIMESTAMP(), server_default=func.now())

    elderly = relationship("User", foreign_keys=[elderly_id])
    contact = relationship("User", foreign_keys=[contact_id])
