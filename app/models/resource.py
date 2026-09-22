from sqlalchemy import Column, Text, Float, Boolean, ForeignKey, Integer, TIMESTAMP, text
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
    quantity_amount = Column(Integer, nullable=True)
    quantity_unit = Column(Text, nullable=True)
    reserved_amount = Column(Integer, nullable=False, default=0, server_default=text("0"))
    inventory_version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    lat           = Column(Float, nullable=True)
    lng           = Column(Float, nullable=True)
    address       = Column(Text, nullable=True)
    is_available  = Column(Boolean, nullable=False, default=True)
    note          = Column(Text, nullable=True)
    valid_until   = Column(TIMESTAMP(), nullable=True)
    last_updated  = Column(TIMESTAMP(), server_default=func.now())
    created_at    = Column(TIMESTAMP(), server_default=func.now())

    owner = relationship("User", back_populates="resources")
