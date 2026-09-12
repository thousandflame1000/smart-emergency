# -*- coding: utf-8 -*-
from sqlalchemy import Column, Text, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID


class DispatchEvent(Base):
    """Append-only audit trail for dispatch-related decisions."""

    __tablename__ = "dispatch_events"

    id              = Column(GUID(), primary_key=True, default=GUID.new)
    action          = Column(Text, nullable=False)
    outcome         = Column(Text, nullable=False, default="success")
    need_id         = Column(GUID(), ForeignKey("community_needs.id", ondelete="SET NULL"), nullable=True)
    resource_id     = Column(GUID(), ForeignKey("community_resources.id", ondelete="SET NULL"), nullable=True)
    actor_id        = Column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_label     = Column(Text, nullable=True)
    previous_status = Column(Text, nullable=True)
    new_status      = Column(Text, nullable=True)
    details_json    = Column(Text, nullable=True)
    created_at      = Column(TIMESTAMP(), server_default=func.now())

    need     = relationship("CommunityNeed", foreign_keys=[need_id])
    resource = relationship("CommunityResource", foreign_keys=[resource_id])
    actor    = relationship("User", foreign_keys=[actor_id])
