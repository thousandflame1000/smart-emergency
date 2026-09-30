from sqlalchemy import Column, Text, Float, Integer, ForeignKey, TIMESTAMP, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base_types import GUID
from app.models.zone import GENERAL_ZONE_ID


class CommunityNeed(Base):
    __tablename__ = "community_needs"

    id                  = Column(GUID(), primary_key=True, default=GUID.new)
    requester_id        = Column(GUID(), ForeignKey("users.id"), nullable=False)
    need_type           = Column(Text, nullable=False)
    description         = Column(Text, nullable=True)
    quantity            = Column(Text, nullable=True)
    quantity_amount     = Column(Integer, nullable=True)
    quantity_unit       = Column(Text, nullable=True)
    reserved_quantity_amount = Column(Integer, nullable=False, default=0, server_default=text("0"))
    fulfilled_quantity_amount = Column(Integer, nullable=False, default=0, server_default=text("0"))
    lat                 = Column(Float, nullable=True)
    lng                 = Column(Float, nullable=True)
    address             = Column(Text, nullable=True)
    urgency             = Column(Integer, nullable=False, default=2)
    status              = Column(Text, nullable=False, default="open")
    matched_resource_id = Column(GUID(), ForeignKey("community_resources.id"), nullable=True)
    valid_until         = Column(TIMESTAMP(), nullable=True)
    created_at          = Column(TIMESTAMP(), server_default=func.now())
    # 一鍵求救的「受理」：第一個說「我過去／我來處理」的人，其他人就知道已經有人在處理
    responder_id        = Column(GUID(), ForeignKey("users.id"), nullable=True)
    acknowledged_at     = Column(TIMESTAMP(), nullable=True)
    zone_id             = Column(Text, ForeignKey("zones.id"), nullable=False,
                                 default=GENERAL_ZONE_ID, server_default=text(f"'{GENERAL_ZONE_ID}'"))

    requester        = relationship("User", back_populates="needs", foreign_keys=[requester_id])
    responder        = relationship("User", foreign_keys=[responder_id])
    matched_resource = relationship("CommunityResource")
    zone             = relationship("Zone")
