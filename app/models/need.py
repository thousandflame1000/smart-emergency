from sqlalchemy import Column, Text, Float, Integer, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base_types import GUID


class CommunityNeed(Base):
    __tablename__ = "community_needs"

    id                  = Column(GUID(), primary_key=True, default=GUID.new)
    requester_id        = Column(GUID(), ForeignKey("users.id"), nullable=False)
    need_type           = Column(Text, nullable=False)
    description         = Column(Text, nullable=True)
    quantity            = Column(Text, nullable=True)
    lat                 = Column(Float, nullable=True)
    lng                 = Column(Float, nullable=True)
    address             = Column(Text, nullable=True)
    urgency             = Column(Integer, nullable=False, default=2)
    status              = Column(Text, nullable=False, default="open")
    matched_resource_id = Column(GUID(), ForeignKey("community_resources.id"), nullable=True)
    valid_until         = Column(TIMESTAMP(), nullable=True)
    created_at          = Column(TIMESTAMP(), server_default=func.now())

    requester        = relationship("User", back_populates="needs")
    matched_resource = relationship("CommunityResource")
