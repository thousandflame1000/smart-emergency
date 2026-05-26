from sqlalchemy import Column, Text, Float, Boolean, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func
from app.database import Base
from app.models.base_types import GUID


class CommunityResource(Base):
    __tablename__ = "community_resources"

    id            = Column(GUID(), primary_key=True, default=GUID.new)
    owner_id      = Column(GUID(), ForeignKey("users.id"), nullable=False)
    resource_type = Column(Text, nullable=False)
    name          = Column(Text, nullable=False)
    quantity      = Column(Text, nullable=True)
    lat           = Column(Float, nullable=True)
    lng           = Column(Float, nullable=True)
    address       = Column(Text, nullable=True)
    is_available  = Column(Boolean, nullable=False, default=True)
    note          = Column(Text, nullable=True)
    valid_until   = Column(TIMESTAMP(), nullable=True)
    last_updated  = Column(TIMESTAMP(), server_default=func.now())
    created_at    = Column(TIMESTAMP(), server_default=func.now())

    owner = relationship("User", back_populates="resources")
