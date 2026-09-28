# -*- coding: utf-8 -*-
from sqlalchemy import Column, Integer, Text, TIMESTAMP
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID


class AdminAudit(Base):
    """Append-only log of every write request to /api/ (計畫書：管理員操作均有時間戳記錄)."""

    __tablename__ = "admin_audit"

    id          = Column(GUID(), primary_key=True, default=GUID.new)
    actor_id    = Column(Text, nullable=True)
    actor_label = Column(Text, nullable=False)
    method      = Column(Text, nullable=False)
    path        = Column(Text, nullable=False)
    status_code = Column(Integer, nullable=False)
    created_at  = Column(TIMESTAMP(), server_default=func.now(), index=True)
