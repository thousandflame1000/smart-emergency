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
  score = urgency_pts + vulnerability_pts + affinity_pts
          - dist_penalty - load_penalty

  urgency_pts       = urgency × 12          # 12–60
  vulnerability_pts = 見 _vulnerability_pts # 0–28  (平時關懷資料轉換而來)
  affinity_pts      = affinity × 15         # 0–15  (類型匹配度)
  dist_penalty      = (dist/max_dist) × 25  # 0–25  (距離懲罰)
  load_penalty      = vol_tasks × 5         # 0–∞   (志工負荷平衡)

  各因子設計依據
  - urgency 係數最高（×12）：緊急度直接關係生命安全，應凌駕效率考量
  - vulnerability_pts 與 urgency 同量級：平時累積的脆弱度訊號與即時緊急度並列，
    體現「平時照顧資料 → 災時派遣優先權」的核心設計
  - affinity 次之（×15）：物資類型不符即使近在咫尺也無實際效用
  - dist_penalty 以 max_km（依緊急度分級）作為歸一化上限，而非硬性排除距離外的候選：
    極緊急情況仍值得跨遠距離調度；各等級距離上限參考社區志工徒步/機車可及範圍訂定
  - load_penalty 線性遞增（×5）：防止單一志工連續被派任，保障任務完成率與志工安全

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
from datetime import datetime, timedelta
from typing import NamedTuple
from sqlalchemy.orm import Session

from app.database import SessionLocal
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint, POINT_SUPPLY_TYPES
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.care_relation import CareRelation
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
# 脆弱度評分 — 把「平時關懷資料」轉換成「災時派遣權重」
# ──────────────────────────────────────────────────────────
VULNERABILITY_LOOKBACK_DAYS = 7
PTS_PER_RISK_CHECKIN = 4.0   # 近 7 天內每次「未回應/求助」打卡
MAX_CHECKIN_PTS = 12.0
PTS_PER_ACTIVE_ALERT = 5.0   # 每筆尚未解決的警報
MAX_ALERT_PTS = 10.0
ISOLATION_PTS = {0: 6.0, 1: 3.0}   # 主動關懷聯絡人數 → 孤立加權


def _vulnerability_pts(requester_id, db: Session) -> float:
    """
    平時照顧、災時派遣的串接點。

    依三項已在系統中持續累積的關懷資料算出加權（0–28 分）：
      checkin_pts   = min(近 7 天「未回應/求助」打卡次數 × 4, 12)
      alert_pts     = min(尚未解決的警報數 × 5, 10)
      isolation_pts = 主動關懷聯絡人 0 人 → 6 分；1 人 → 3 分；≥2 人 → 0 分

    回傳值會直接加進緊急度評分，讓「平時就被持續關注、追蹤、
    身邊照顧者很少」的人，在派遣排序中自動取得優先權——這份
    優先權不是對方在 LINE 上臨時描述出來的，而是平時日常打卡
    累積下來的真實紀錄。
    """
    cutoff = datetime.utcnow().date() - timedelta(days=VULNERABILITY_LOOKBACK_DAYS)

    risk_checkins = (
        db.query(DailyCheckin)
        .filter(
            DailyCheckin.elderly_id == requester_id,
            DailyCheckin.date >= cutoff,
            DailyCheckin.status.in_(["no_response", "help_needed"]),
        )
        .count()
    )
    checkin_pts = min(risk_checkins * PTS_PER_RISK_CHECKIN, MAX_CHECKIN_PTS)

    active_alerts = (
        db.query(Alert)
        .filter(
            Alert.elderly_id == requester_id,
            Alert.status == "sent",
        )
        .count()
    )
    alert_pts = min(active_alerts * PTS_PER_ACTIVE_ALERT, MAX_ALERT_PTS)

    contacts = (
        db.query(CareRelation)
        .filter(
            CareRelation.elderly_id == requester_id,
            CareRelation.is_active == True,
        )
        .count()
    )
    isolation_pts = ISOLATION_PTS.get(contacts, 0.0)

    return checkin_pts + alert_pts + isolation_pts


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
    vulnerability_pts: float = 0.0,
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
    return urgency_pts + vulnerability_pts + affinity_pts - dist_pts - load_pts


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
    vulnerability: float = 0.0,
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
        s = _score(need.urgency, affinity, dist, vol_load, vulnerability)
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
    vulnerability: float = 0.0,
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
        s = _score(need.urgency, best_aff, dist, 0, vulnerability)
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
    注意：這裡只「建議」個人物資媒合，不會自動通知志工——
    所有建議會先進入 need.status = "suggested"，管理員必須在後台按下
    confirm_dispatch 才會真的發 LINE 通知（對應計畫書「所有建議仍須
    管理員確認後才會執行」的承諾）。資源點（固定設施）媒合本來就不會
    通知志工，維持直接標記完成。
    回傳統計：{matched, suggested, skipped, notified, details}
    """
    db: Session = SessionLocal()
    try:
        cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
        if not cfg or cfg.value != "emergency":
            return {"matched": 0, "suggested": 0, "skipped": 0, "notified": 0,
                    "reason": "not_emergency"}

        open_needs = (
            db.query(CommunityNeed)
            .filter(CommunityNeed.status == "open")
            .all()
        )

        # 排序依「緊急度 → 脆弱度 → 建立時間 FIFO」。這裡曾經是真的
        # bug：只用 urgency.desc() + created_at 排序處理順序，脆弱度
        # 只有在同一筆需求「內部」比較候選資源時才加分，資源不足、
        # 兩筆需求緊急度剛好一樣、要搶同一份物資時，完全沒有反映
        # 「平時打卡異常/警報未解決/照顧網絡孤立」這些關懷資料——
        # 跟計畫書「脆弱度偏高會被自動排入優先派遣名單」的核心敘述
        # 對不起來。這裡先幫每筆需求把脆弱度算好，一起排進處理順序，
        # 同時避免迴圈內每筆需求重算一次的重複查詢。
        needs_with_vuln = [
            (need, _vulnerability_pts(need.requester_id, db))
            for need in open_needs
        ]
        needs_with_vuln.sort(
            key=lambda item: (
                item[0].urgency,
                item[1],
                -(item[0].created_at.timestamp() if item[0].created_at else 0),
            ),
            reverse=True,
        )

        matched   = 0
        suggested = 0
        skipped   = 0
        notified  = 0
        details   = []

        # 追蹤本批次志工已派任數（負荷平衡）
        volunteer_load: dict[str, int] = {}

        for need, vulnerability in needs_with_vuln:
            # 從兩個來源蒐集候選
            cands = (
                _collect_from_resources(need, db, volunteer_load, vulnerability) +
                _collect_from_points(need, db, volunteer_load, vulnerability)
            )

            if not cands:
                skipped += 1
                details.append({
                    "need_id": str(need.id),
                    "result": "skipped",
                    "reason": "無相符物資",
                    "vulnerability": round(vulnerability, 1),
                })
                continue

            # 取最高分
            best = max(cands, key=lambda c: c.score)

            if best.source == "resource" and best.resource_id:
                # 個人物資：這裡只「建議」，不自動通知志工——
                # 計畫書明文承諾「所有建議仍須管理員確認後才會執行」，
                # 真正發 LINE 通知要等管理員按下確認（見 confirm_dispatch）。
                need.status = "suggested"
                need.matched_resource_id = best.resource_id
                res_obj = db.query(CommunityResource).filter(
                    CommunityResource.id == best.resource_id
                ).first()
                if res_obj:
                    res_obj.is_available = False  # 先保留，避免同時被建議給別人
                    vid = str(res_obj.owner_id)
                    volunteer_load[vid] = volunteer_load.get(vid, 0) + 1
                    # SessionLocal 是 autoflush=False（見 app/database.py），
                    # 這裡如果不主動 flush，上面這行 is_available 的變更只存在
                    # Python 端的物件狀態、還沒真的送進資料庫——迴圈跑到下一筆
                    # 需求時，_collect_from_resources() 會重新對資料庫下
                    # SELECT ... WHERE is_available=True，撈到的還是舊值，
                    # 導致同一份實際只有一份的物資被「建議」給兩筆不同需求
                    # （這是真的用測試重現過的 bug，不是理論上的疑慮）。
                    db.flush()

                suggested += 1
                details.append({
                    "need_id":       str(need.id),
                    "result":        "suggested",
                    "source":        best.source,
                    "matched":       best.res_name,
                    "dist_km":       round(best.dist_km, 2) if not math.isinf(best.dist_km) else None,
                    "score":         round(best.score, 1),
                    "vulnerability": round(vulnerability, 1),
                })
            else:
                # 資源點（固定設施）：本來就不會發 LINE 通知志工，
                # 只是把「有哪個資源點可用」標記出來給管理員手動協調，
                # 不牽涉「自動指派志工」，維持直接標記完成。
                need.status = "matched"
                matched += 1
                details.append({
                    "need_id":       str(need.id),
                    "result":        "matched",
                    "source":        best.source,
                    "matched":       best.res_name,
                    "dist_km":       round(best.dist_km, 2) if not math.isinf(best.dist_km) else None,
                    "score":         round(best.score, 1),
                    "vulnerability": round(vulnerability, 1),
                    "notified":      False,
                })

        db.commit()
        return {
            "matched":   matched,
            "suggested": suggested,
            "skipped":   skipped,
            "notified":  notified,
            "details":   details,
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
# 確認 / 否決 自動媒合建議（管理員確認關卡）
# ──────────────────────────────────────────────────────────
def confirm_dispatch(need_id: str, db: Session) -> dict:
    """
    管理員確認一筆 auto_dispatch 產生的建議 —— 這一步才會真正發
    LINE 通知志工。對應計畫書「所有建議仍須管理員確認後才會執行」。
    """
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need or need.status != "suggested":
        return {"error": "此需求目前沒有待確認的媒合建議"}

    resource = db.query(CommunityResource).filter(
        CommunityResource.id == need.matched_resource_id
    ).first()
    if not resource:
        need.status = "open"
        need.matched_resource_id = None
        db.commit()
        return {"error": "候選物資已不存在，需求已退回待媒合"}

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

    need.status = "matched"
    db.commit()

    return {
        "message":            "已確認派遣，志工已收到 LINE 通知" if notified
                              else "已確認派遣（志工未綁定 LINE，請自行聯繫）",
        "need_id":            need_id,
        "volunteer_notified": notified,
    }


def decline_suggestion(need_id: str, db: Session) -> dict:
    """管理員否決一筆自動媒合建議 —— 釋放物資、需求退回待媒合佇列。"""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need or need.status != "suggested":
        return {"error": "此需求目前沒有待確認的媒合建議"}

    if need.matched_resource_id:
        resource = db.query(CommunityResource).filter(
            CommunityResource.id == need.matched_resource_id
        ).first()
        if resource:
            resource.is_available = True

    need.status = "open"
    need.matched_resource_id = None
    db.commit()

    return {"message": "已否決此建議，需求退回待媒合", "need_id": need_id}


# ──────────────────────────────────────────────────────────
# 評分預覽（給管理員在 UI 查看候選排序）
# ──────────────────────────────────────────────────────────
def preview_candidates(need_id: str, db: Session) -> dict:
    """
    回傳此需求的脆弱度評分 + 所有候選資源排序（不執行媒合）。
    讓管理員在 UI 上能直接看到「這個人為什麼被排到前面」——
    脆弱度分數是平時打卡/警報/照顧關係資料即時算出來的，
    不是憑空給的優先權。
    """
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return {"vulnerability": 0.0, "candidates": []}

    vulnerability = _vulnerability_pts(need.requester_id, db)

    cands = (
        _collect_from_resources(need, db, {}, vulnerability) +
        _collect_from_points(need, db, {}, vulnerability)
    )
    cands.sort(key=lambda c: c.score, reverse=True)

    return {
        "vulnerability": round(vulnerability, 1),
        "candidates": [
            {
                "score":    round(c.score, 1),
                "source":   c.source,
                "name":     c.res_name,
                "vol":      c.vol_name,
                "dist_km":  round(c.dist_km, 2) if not math.isinf(c.dist_km) else None,
                "id":       c.resource_id or c.point_id,
            }
            for c in cands[:20]
        ],
    }
