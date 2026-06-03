# -*- coding: utf-8 -*-
"""
社區固定資源點 (ResourcePoint)
—— 與 CommunityResource（個人登記物資）不同：
   ResourcePoint 是社區固定基礎設施（避難所、消防分隊、衛生所、活動中心等），
   由管理員或 seed 腳本從政府開放資料匯入，作為物資分發與避難的樞紐。
"""
from sqlalchemy import Column, Text, Float, Integer, Boolean, TIMESTAMP
from sqlalchemy.sql import func
from app.database import Base
from app.models.base_types import GUID


# 資源點類型
POINT_TYPES = {
    "shelter":      "避難收容所",
    "community":    "里民活動中心",
    "hospital":     "醫療院所",
    "fire_station": "消防分隊",
    "police":       "警察局/派出所",
    "store":        "物資分發點（超商/全聯）",
    "warehouse":    "物資倉庫",
    "clinic":       "衛生所",
    "other":        "其他",
}

# 資源點 → 預設可提供的物資類型（媒合時使用）
POINT_SUPPLY_TYPES = {
    "shelter":      ["shelter", "water", "food"],
    "community":    ["water", "food", "shelter"],
    "hospital":     ["first_aid"],
    "fire_station": ["first_aid", "tool", "vehicle"],
    "police":       ["shelter", "vehicle"],
    "store":        ["water", "food"],
    "warehouse":    ["water", "food", "first_aid", "tool", "other"],
    "clinic":       ["first_aid"],
    "other":        ["other"],
}


class ResourcePoint(Base):
    __tablename__ = "resource_points"

    id              = Column(GUID(), primary_key=True, default=GUID.new)
    name            = Column(Text, nullable=False)          # 場所名稱
    point_type      = Column(Text, nullable=False, default="other")  # 見 POINT_TYPES
    address         = Column(Text, nullable=True)
    lat             = Column(Float, nullable=True)
    lng             = Column(Float, nullable=True)
    capacity        = Column(Integer, nullable=True)        # 收容人數上限
    current_load    = Column(Integer, nullable=False, default=0)  # 目前使用人數
    supplies_json   = Column(Text, nullable=True)           # JSON: {"water":"充足","food":"有限"}
    phone           = Column(Text, nullable=True)
    operating_hours = Column(Text, nullable=True)           # "24h" / "08:00-22:00"
    source          = Column(Text, nullable=False, default="manual")
    # source: manual | gov_shelter | gov_fire | gov_clinic | line_report
    is_active       = Column(Boolean, nullable=False, default=True)
    note            = Column(Text, nullable=True)
    created_at      = Column(TIMESTAMP(), server_default=func.now())
    updated_at      = Column(TIMESTAMP(), server_default=func.now(),
                             onupdate=func.now())
