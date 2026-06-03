# -*- coding: utf-8 -*-
"""
物資自動媒合排程服務  v2
=====================================
資料來源（三層）
  Layer 1  個人物資   CommunityResource — 志工自行登記
  Layer 2  固定資源點 ResourcePoint     — 從政府開放資料匯入（避難所、消防分隊…）
  Layer 3  快速需求   CommunityNeed     — 居民透過 LINE 回報

算法（多因子評分 + 優先佇列）
-------------------------------------
  score = urgency_pts + affinity_pts - dist_penalty - load_penalty

  urgency_pts  = urgency × 12          # 12–60
  affinity_pts = affinity × 15         # 0–15  (類型匹配度)
  dist_penalty = (dist/max_dist) × 25  # 0–25  (距離懲罰)
  load_penalty = vol_tasks × 5         # 0–∞   (志工負荷平衡)

  距離上限 (urgency → max km)
    5 極緊急 → 2 km
    4 緊急   → 5 km
    3 一般   → 8 km
    2 輕度   → 15 km
    1 觀察   → 25 km

類型親合度矩陣 (need_type → [(resource_type, affinity)])
  完全匹配 = 1.0，部分匹配 = 0.2–0.4，不匹配 = 不列入候選
"""
import json
import math
from datetime import datetime
from typing import NamedTuple
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint, POINT_SUPPLY_TYPES
from app.models.config import SystemConfig
from app.services.line_notify import send_task_message


# ──────────────────────────────────────────────────────────
# 類型親合度矩陣
# ──────────────────────────────────────────────────────────
TYPE_AFFINITY: dict[str, list[tuple[str, float]]] = {
    "water":     [("water", 1.0), ("food", 0.25)],
    "food":      [("food", 1.0), ("water", 0.25)],
    "first_aid": [("first_aid", 1.0)],
    "shelter":   [("shelter", 1.0)],
    "vehicle":   [("vehicle", 1.0)],
    "tool":      [("tool", 1.0), ("other", 0.35)],
    "other":     [("other", 1.0), ("tool", 0.35)],
}

# urgency (1-5) → 最大可接受距離（km）
URGENCY_MAX_KM: dict[int, float] = {
    5: 2.0,
    4: 5.0,
    3: 8.0,
    2: 15.0,
    1: 25.0,
}
DEFAULT_MAX_KM = 10.0


# ──────────────────────────────────────────────────────────
# Haversine 距離（km）
# ──────────────────────────────────────────────────────────
def _haversine(lat1, lng1, lat2, lng2) -> float:
    if None in (lat1, lng1, lat2, lng2):
        return float("inf")
    R = 6371.0
    d_lat = math.radians(lat2 - lat1)
    d_lng = math.radians(lng2 - lng1)
    a = (math.sin(d_lat / 2) ** 2
         + math.cos(math.radians(lat1))
         * math.cos(math.radians(lat2))
         * math.sin(d_lng / 2) ** 2)
    return R * 2 * math.asin(math.sqrt(a))


# ──────────────────────────────────────────────────────────
# 候選物資結構
# ──────────────────────────────────────────────────────────
class Candidate(NamedTuple):
    score:       float
    source:      str          # "resource" | "resource_point"
    obj_id:      str
    resource_id: str | None   # CommunityResource.id 或 None
    point_id:    str | None   # ResourcePoint.id 或 None
    vol_line_uid: str | None
    vol_name:    str
    res_name:    str
    dist_km:     float


# ──────────────────────────────────────────────────────────
# 評分函數
# ──────────────────────────────────────────────────────────
def _score(
    urgency: int,
    affinity: float,
    dist_km: float,
    vol_active_tasks: int,
) -> float:
    max_km = URGENCY_MAX_KM.get(urgency, DEFAULT_MAX_KM)
    if dist_km > max_km:
        return -1.0  # 超出距離限制
    if math.isinf(dist_km):
        # 無座標 — 保守給低分但不排除
        dist_km = max_km * 0.8

    urgency_pts = urgency * 12
    affinity_pts = affinity * 15
    dist_pts = (dist_km / max_km) * 25
    load_pts = vol_active_tasks * 5
    return urgency_pts + affinity_pts - dist_pts - load_pts


# ──────────────────────────────────────────────────────────
# 取得相容資源類型清單
# ──────────────────────────────────────────────────────────
def _compat_types(need_type: str) -> list[tuple[str, float]]:
    """回傳 [(resource_type, affinity), ...] 包含部分匹配"""
    return TYPE_AFFINITY.get(need_type, [(need_type, 1.0)])


# ──────────────────────────────────────────────────────────
# 從 CommunityResource 收集候選
# ──────────────────────────────────────────────────────────
def _collect_from_resources(
    need: CommunityNeed,
    db: Session,
    volunteer_load: dict[str, int],
) -> list[Candidate]:
    compat = _compat_types(need.need_type)
    type_aff = {t: a for t, a in compat}

    res_list = (
        db.query(CommunityResource)
        .filter(
            CommunityResource.resource_type.in_(list(type_aff.keys())),
            CommunityResource.is_available == True,
        )
        .all()
    )

    candidates = []
    for r in res_list:
        affinity = type_aff.get(r.resource_type, 0.0)
        dist = _haversine(need.lat, need.lng, r.lat, r.lng)
        vol_load = volunteer_load.get(str(r.owner_id), 0)
        s = _score(need.urgency, affinity, dist, vol_load)
        if s < 0:
            continue
        owner = r.owner
        candidates.append(Candidate(
            score=s,
            source="resource",
            obj_id=str(r.id),
            resource_id=str(r.id),
            point_id=None,
            vol_line_uid=owner.line_uid if owner else None,
            vol_name=owner.name if owner else "未知志工",
            res_name=r.name,
            dist_km=dist,
        ))
    return candidates


# ──────────────────────────────────────────────────────────
# 從 ResourcePoint 收集候選
# ──────────────────────────────────────────────────────────
def _collect_from_points(
    need: CommunityNeed,
    db: Session,
    volunteer_load: dict[str, int],
) -> list[Candidate]:
    """
    資源點本身不是志工，不會發 LINE 通知；
    但可作為物資來源出現在評分中（媒合後管理員需手動協調）。
    若資源點有 phone，LINE 通知會改成發給最近的有 LINE 的志工。
    """
    compat = _compat_types(need.need_type)
    type_aff = {t: a for t, a in compat}

    all_points = (
        db.query(ResourcePoint)
        .filter(ResourcePoint.is_active == True)
        .all()
    )

    candidates = []
    for pt in all_points:
        # 確認此資源點有相容的供應類型
        pt_supplies = POINT_SUPPLY_TYPES.get(pt.point_type, [])
        best_aff = max(
            (type_aff[t] for t in pt_supplies if t in type_aff),
            default=0.0,
        )
        if best_aff == 0.0:
            continue

        dist = _haversine(need.lat, need.lng, pt.lat, pt.lng)
        # 資源點無志工負荷問題
        s = _score(need.urgency, best_aff, dist, 0)
        if s < 0:
            continue

        # 降權 0.8 — 資源點是固定設施，個人物資更靈活
        candidates.append(Candidate(
            score=s * 0.8,
            source="resource_point",
            obj_id=str(pt.id),
            resource_id=None,
            point_id=str(pt.id),
            vol_line_uid=None,
            vol_name=f"資源點：{pt.name}",
            res_name=pt.name,
            dist_km=dist,
        ))
    return candidates


# ──────────────────────────────────────────────────────────
# 自動媒合（主排程）
# ──────────────────────────────────────────────────────────
def auto_dispatch() -> dict:
    """
    緊急模式下每 30 分鐘執行一次。
    回傳統計：{matched, skipped, notified, details}
    """
    db: Session = SessionLocal()
    try:
        cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
        if not cfg or cfg.value != "emergency":
            return {"matched": 0, "skipped": 0, "notified": 0,
                    "reason": "not_emergency"}

        # 按緊急度 DESC → 建立時間 ASC（FIFO 相同緊急度）
        open_needs = (
            db.query(CommunityNeed)
            .filter(CommunityNeed.status == "open")
            .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at)
            .all()
        )

        matched   = 0
        skipped   = 0
        notified  = 0
        details   = []

        # 追蹤本批次志工已派任數（負荷平衡）
        volunteer_load: dict[str, int] = {}

        for need in open_needs:
            # 從兩個來源蒐集候選
            cands = (
                _collect_from_resources(need, db, volunteer_load) +
                _collect_from_points(need, db, volunteer_load)
            )

            if not cands:
                skipped += 1
                details.append({
                    "need_id": str(need.id),
                    "result": "skipped",
                    "reason": "無相符物資",
                })
                continue

            # 取最高分
            best = max(cands, key=lambda c: c.score)

            # 更新 need
            need.status = "matched"
            if best.resource_id:
                need.matched_resource_id = best.resource_id
                # 標記個人物資為不可用（防重複媒合）
                res_obj = db.query(CommunityResource).filter(
                    CommunityResource.id == best.resource_id
                ).first()
                if res_obj:
                    res_obj.is_available = False

            matched += 1

            # 更新志工負荷（僅個人物資的擁有者）
            if best.source == "resource" and best.resource_id:
                res_obj = db.query(CommunityResource).filter(
                    CommunityResource.id == best.resource_id
                ).first()
                if res_obj:
                    vid = str(res_obj.owner_id)
                    volunteer_load[vid] = volunteer_load.get(vid, 0) + 1

            # LINE 通知（個人物資才有志工可通知）
            notif_sent = False
            if best.vol_line_uid:
                try:
                    send_task_message(
                        line_uid=best.vol_line_uid,
                        need_description=need.description or need.need_type,
                        address=need.address or "地址未填",
                        resource_name=best.res_name,
                        need_id=str(need.id),
                    )
                    notif_sent = True
                    notified += 1
                except Exception:
                    pass

            details.append({
                "need_id":   str(need.id),
                "result":    "matched",
                "source":    best.source,
                "matched":   best.res_name,
                "dist_km":   round(best.dist_km, 2) if not math.isinf(best.dist_km) else None,
                "score":     round(best.score, 1),
                "notified":  notif_sent,
            })

        db.commit()
        return {
            "matched":  matched,
            "skipped":  skipped,
            "notified": notified,
            "details":  details,
        }

    finally:
        db.close()


# ──────────────────────────────────────────────────────────
# 手動媒合
# ──────────────────────────────────────────────────────────
def manual_dispatch(need_id: str, resource_id: str, db: Session) -> dict:
    """手動指定媒合，立即 LINE 通知志工"""
    need     = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    resource = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()

    if not need or not resource:
        return {"error": "need or resource not found"}

    need.matched_resource_id = resource.id
    need.status  = "matched"
    resource.is_available = False
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

    dist = _haversine(need.lat, need.lng, resource.lat, resource.lng)
    return {
        "message":            "媒合成功",
        "need_id":            need_id,
        "resource_id":        resource_id,
        "volunteer_notified": notified,
        "dist_km":            round(dist, 2) if not math.isinf(dist) else None,
    }


# ──────────────────────────────────────────────────────────
# 評分預覽（給管理員在 UI 查看候選排序）
# ──────────────────────────────────────────────────────────
def preview_candidates(need_id: str, db: Session) -> list[dict]:
    """
    回傳此需求所有候選資源，依評分排序（不執行媒合）
    """
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return []

    cands = (
        _collect_from_resources(need, db, {}) +
        _collect_from_points(need, db, {})
    )
    cands.sort(key=lambda c: c.score, reverse=True)

    return [
        {
            "score":    round(c.score, 1),
            "source":   c.source,
            "name":     c.res_name,
            "vol":      c.vol_name,
            "dist_km":  round(c.dist_km, 2) if not math.isinf(c.dist_km) else None,
            "id":       c.resource_id or c.point_id,
        }
        for c in cands[:20]
    ]
