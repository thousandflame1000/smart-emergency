# -*- coding: utf-8 -*-
"""
物資自動媒合排程服務
緊急模式下每 30 分鐘執行一次：
  1. 找出所有 open 需求
  2. 按 need_type 比對可用物資
  3. 計算距離，取最近一筆
  4. 自動媒合 + 通知物資登記人（志工）
"""
import math
from datetime import datetime
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.config import SystemConfig
from app.services.line_notify import send_task_message


# ── 距離計算（Haversine，單位：公里）─────────
def _haversine(lat1, lng1, lat2, lng2) -> float:
    if None in (lat1, lng1, lat2, lng2):
        return float("inf")
    R = 6371
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(d_lng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ── 主排程函數 ────────────────────────────────
def auto_dispatch() -> dict:
    """
    自動媒合物資與需求
    回傳：{"matched": N, "skipped": N, "notified": N}
    """
    db: Session = SessionLocal()
    try:
        # 只在緊急模式下執行
        cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
        if not cfg or cfg.value != "emergency":
            return {"matched": 0, "skipped": 0, "notified": 0, "reason": "not_emergency"}

        # 取所有未媒合的需求，按緊急度排序
        open_needs = (
            db.query(CommunityNeed)
            .filter(CommunityNeed.status == "open")
            .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at)
            .all()
        )

        matched = 0
        skipped = 0
        notified = 0

        for need in open_needs:
            # 找相同類型的可用物資
            candidates = (
                db.query(CommunityResource)
                .filter(
                    CommunityResource.resource_type == need.need_type,
                    CommunityResource.is_available == True,
                )
                .all()
            )

            if not candidates:
                skipped += 1
                continue

            # 若有座標，選最近一筆；否則選第一筆
            if need.lat and need.lng:
                candidates.sort(
                    key=lambda r: _haversine(need.lat, need.lng, r.lat, r.lng)
                )
            best = candidates[0]

            # 媒合 + 標記物資為不可用（避免重複媒合）
            need.matched_resource_id = best.id
            need.status = "matched"
            best.is_available = False          # ← 斷點 3 修復
            matched += 1

            # 通知物資登記人（志工），帶 need_id 讓他能回報送達
            owner = best.owner
            if owner and owner.line_uid:
                try:
                    send_task_message(
                        line_uid=owner.line_uid,
                        need_description=need.description or need.need_type,
                        address=need.address or "地址未填",
                        resource_name=best.name,
                        need_id=str(need.id),  # ← 用於送達確認
                    )
                    notified += 1
                except Exception:
                    pass

        db.commit()
        return {"matched": matched, "skipped": skipped, "notified": notified}

    finally:
        db.close()


# ── 手動媒合（API 呼叫用）────────────────────
def manual_dispatch(need_id: str, resource_id: str, db: Session) -> dict:
    """
    手動指定媒合，並立即通知志工
    """
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    resource = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()

    if not need or not resource:
        return {"error": "need or resource not found"}

    need.matched_resource_id = resource.id
    need.status = "matched"
    resource.is_available = False   # ← 避免重複媒合
    db.commit()

    notified = False
    owner = resource.owner
    if owner and owner.line_uid:
        try:
            send_task_message(
                line_uid=owner.line_uid,
                need_description=need.description or need.need_type,
                address=need.address or "地址未填",
                resource_name=resource.name,
                need_id=str(need.id),
            )
            notified = True
        except Exception:
            pass

    return {
        "message": "媒合成功",
        "need_id": need_id,
        "resource_id": resource_id,
        "volunteer_notified": notified,
    }
