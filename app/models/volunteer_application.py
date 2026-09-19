# -*- coding: utf-8 -*-
"""志工自助申請。

現有流程是「LINE 自動註冊一律先當長者，志工角色只能管理員手動改」，
申請者完全看不到自己審到哪。這張表把「申請」這一步交給使用者自己，
「審核」仍然留給管理員——志工一旦通過就能看到長者地址與需求細節，
這道人工關卡本身是刻意保留的，不是要拿掉。
"""
from sqlalchemy import Column, Text, ForeignKey, TIMESTAMP
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID

STATUSES = ("pending", "approved", "rejected")


class VolunteerApplication(Base):
    __tablename__ = "volunteer_applications"

    id           = Column(GUID(), primary_key=True, default=GUID.new)
    line_uid     = Column(Text, nullable=False)
    name         = Column(Text, nullable=False)
    phone        = Column(Text, nullable=True)
    service_area = Column(Text, nullable=True)
    status       = Column(Text, nullable=False, default="pending")
    applicant_id = Column(GUID(), ForeignKey("users.id"), nullable=True)
    reviewed_by  = Column(GUID(), ForeignKey("users.id"), nullable=True)
    review_note  = Column(Text, nullable=True)
    created_at   = Column(TIMESTAMP(), server_default=func.now())
    reviewed_at  = Column(TIMESTAMP(), nullable=True)

    applicant = relationship("User", foreign_keys=[applicant_id])
    reviewer  = relationship("User", foreign_keys=[reviewed_by])
