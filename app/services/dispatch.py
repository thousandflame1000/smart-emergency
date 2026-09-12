# -*- coding: utf-8 -*-
"""
物資自動媒合排程服務  v2
=====================================
資料來源（三層）
  Layer 1  個人物資   CommunityResource — 志工自行登記
  Layer 2  固定資源點 ResourcePoint     — 從政府開放資料匯入（避難所、消防分隊…）
  Layer 3  快速需求   CommunityNeed     — 居民透過 LINE 回報

算法（多因子評分 + 批次最佳指派）
-------------------------------------
  score = urgency_pts + vulnerability_pts + affinity_pts + wait_pts
          - dist_penalty - load_penalty

  urgency_pts       = urgency × 12          # 12–60
  vulnerability_pts = 見 _vulnerability_pts # 0–28  (平時關懷資料轉換而來)
  affinity_pts      = affinity × 15         # 0–15  (類型匹配度)
  wait_pts          = min(等待小時 × 1, 15) # 0–15  (deprivation cost 簡化版)
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

Layer 1（個人物資，真正稀缺、一份只能給一筆需求）採批次最佳指派：
每次執行蒐集當下所有待處理需求 × 候選物資的分數，一次用匈牙利演算法
（app/services/hungarian.py，純 Python 實作，不依賴 scipy）解出
總分數最大化的全域指派，
取代舊版「依序處理、每筆搶當下最高分」的貪婪法——貪婪法不保證全域
最優，先處理的需求可能搶走對另一筆需求而言更關鍵的資源。
Layer 2（資源點，固定設施、非稀缺）維持獨立逐筆比對，因為同一個
資源點可以同時是多筆需求的最佳解，不需要、也不該被排他性指派。

概念參照災害物流的緊急度優先分配研究、指派問題的匈牙利演算法應用，
以及 Crisis Cleanup（美國災後志工任務媒合平台）的媒合模式。
"""
import json
import math
from datetime import datetime, timedelta
from typing import NamedTuple
from sqlalchemy.orm import Session

from app.services.hungarian import min_cost_assignment
from app.services import road_network

from app.database import SessionLocal
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint, POINT_SUPPLY_TYPES
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.config import SystemConfig
from app.models.dispatch_event import DispatchEvent
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

# 等待時間懲罰（deprivation cost 的線性簡化版，見 Pérez-Rodríguez &
# Holguín-Veras 2016, Transportation Science 50(4) 的剝奪成本概念）：
# 同樣分數的候選中，開得越久的需求該被往前排，而不是只在 tie-break
# 時比 created_at。
WAIT_PTS_PER_HOUR = 1.0
WAIT_PTS_CAP = 15.0


def _log_dispatch_event(
    db: Session,
    action: str,
    *,
    need: CommunityNeed | None = None,
    resource: CommunityResource | None = None,
    resource_id: str | None = None,
    actor_id: str | None = None,
    actor_label: str | None = None,
    previous_status: str | None = None,
    new_status: str | None = None,
    outcome: str = "success",
    details: dict | None = None,
) -> DispatchEvent:
    event = DispatchEvent(
        action=action,
        outcome=outcome,
        need_id=need.id if need else None,
        resource_id=(resource.id if resource else resource_id),
        actor_id=actor_id,
        actor_label=actor_label,
        previous_status=previous_status,
        new_status=new_status,
        details_json=json.dumps(details or {}, ensure_ascii=False),
    )
    db.add(event)
    return event


def _wait_pts(need: CommunityNeed) -> float:
    if not need.created_at:
        return 0.0
    hours = (datetime.utcnow() - need.created_at).total_seconds() / 3600
    return min(max(hours, 0.0) * WAIT_PTS_PER_HOUR, WAIT_PTS_CAP)


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
    平時照顧、災時派遣的串接點。基礎是 UNDRR 災害風險框架
    Risk = Hazard × Exposure × Vulnerability / Capacity（見
    app/services/hazard.py）——這裡算的是 Vulnerability 那一項。

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


def _distance_km(lat1, lng1, lat2, lng2) -> float:
    """
    Real road-network distance where available (app/services/road_network.py
    — currently covers the Hua-Dong/east-coast corridor), falling back to
    straight-line haversine everywhere else (e.g. Taichung, where no road
    graph is built). Straight-line distance across mountainous terrain
    systematically understates real travel distance/time; using the real
    road graph where we have one is a materially more honest number to
    score dispatch decisions on, not just to display.
    """
    if None not in (lat1, lng1, lat2, lng2):
        road_d = road_network.road_distance_km(lat1, lng1, lat2, lng2)
        if road_d is not None:
            return road_d
    return _haversine(lat1, lng1, lat2, lng2)


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
    breakdown:   dict         # 給 preview_candidates() 攤開子項用


# ──────────────────────────────────────────────────────────
# 評分函數
# ──────────────────────────────────────────────────────────
def _score_breakdown(
    urgency: int,
    affinity: float,
    dist_km: float,
    vol_active_tasks: int,
    vulnerability_pts: float = 0.0,
    wait_pts: float = 0.0,
) -> dict | None:
    """回傳 None 代表超出距離限制，不列入候選。"""
    max_km = URGENCY_MAX_KM.get(urgency, DEFAULT_MAX_KM)
    if dist_km > max_km:
        return None
    eff_dist = dist_km if not math.isinf(dist_km) else max_km * 0.8

    urgency_pts = urgency * 12
    affinity_pts = affinity * 15
    dist_penalty = (eff_dist / max_km) * 25
    load_penalty = vol_active_tasks * 5
    total = urgency_pts + vulnerability_pts + affinity_pts + wait_pts - dist_penalty - load_penalty
    return {
        "urgency_pts": urgency_pts, "vulnerability_pts": vulnerability_pts,
        "affinity_pts": affinity_pts, "wait_pts": wait_pts,
        "dist_penalty": dist_penalty, "load_penalty": load_penalty,
        "total": total,
    }


def _score(
    urgency: int,
    affinity: float,
    dist_km: float,
    vol_active_tasks: int,
    vulnerability_pts: float = 0.0,
    wait_pts: float = 0.0,
) -> float:
    b = _score_breakdown(urgency, affinity, dist_km, vol_active_tasks, vulnerability_pts, wait_pts)
    return b["total"] if b else -1.0


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

    wait_pts = _wait_pts(need)
    candidates = []
    for r in res_list:
        affinity = type_aff.get(r.resource_type, 0.0)
        dist = _distance_km(need.lat, need.lng, r.lat, r.lng)
        vol_load = volunteer_load.get(str(r.owner_id), 0)
        b = _score_breakdown(need.urgency, affinity, dist, vol_load, vulnerability, wait_pts)
        if b is None:
            continue
        owner = r.owner
        candidates.append(Candidate(
            score=b["total"],
            source="resource",
            obj_id=str(r.id),
            resource_id=str(r.id),
            point_id=None,
            vol_line_uid=owner.line_uid if owner else None,
            vol_name=owner.name if owner else "未知志工",
            res_name=r.name,
            dist_km=dist,
            breakdown=b,
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

    wait_pts = _wait_pts(need)
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

        dist = _distance_km(need.lat, need.lng, pt.lat, pt.lng)
        # 資源點無志工負荷問題
        b = _score_breakdown(need.urgency, best_aff, dist, 0, vulnerability, wait_pts)
        if b is None:
            continue

        # 降權 0.8 — 資源點是固定設施，個人物資更靈活
        candidates.append(Candidate(
            score=b["total"] * 0.8,
            source="resource_point",
            obj_id=str(pt.id),
            resource_id=None,
            point_id=str(pt.id),
            vol_line_uid=None,
            vol_name=f"資源點：{pt.name}",
            res_name=pt.name,
            dist_km=dist,
            breakdown={**b, "total": b["total"] * 0.8, "fixed_facility_discount": 0.8},
        ))
    return candidates


# ──────────────────────────────────────────────────────────
# 自動媒合（主排程）
# ──────────────────────────────────────────────────────────
def _assign_resources_optimally(
    needs_with_vuln: list[tuple[CommunityNeed, float]],
    db: Session,
) -> dict[str, Candidate]:
    """
    Layer 1（CommunityResource）批次最佳指派。每份物資只能給一筆需求，
    這是真正的稀缺資源，適合用匈牙利演算法一次解出總分數最大化的
    全域指派——不是「先處理的需求先搶」。
    volunteer_load 在整批次內固定不變（不像舊版貪婪法邊指派邊累加）：
    批次求解本來就沒有「先後」，用同一套負荷快照對所有需求一視同仁；
    唯一的取捨是同一位志工在同一批次擁有多份物資時，這幾份物資之間
    不會再互相加重負荷懲罰，影響很小（多數志工同時只有 1 份物資）。
    回傳 {need_id: Candidate}，只包含真的指派成功的需求。
    """
    volunteer_load: dict[str, int] = {}
    cands_by_need: dict[str, list[Candidate]] = {}
    resource_ids: list[str] = []
    seen_resources: set[str] = set()

    for need, vulnerability in needs_with_vuln:
        cands = _collect_from_resources(need, db, volunteer_load, vulnerability)
        cands_by_need[str(need.id)] = cands
        for c in cands:
            if c.resource_id not in seen_resources:
                seen_resources.add(c.resource_id)
                resource_ids.append(c.resource_id)

    if not resource_ids:
        return {}

    need_ids = [str(n.id) for n, _ in needs_with_vuln]
    r_index = {rid: j for j, rid in enumerate(resource_ids)}
    BIG = 1e6  # 代表「不相容/超出距離」，minimize 時演算法會盡量避開
    cost = [[BIG] * len(resource_ids) for _ in need_ids]
    lookup: dict[tuple[int, int], Candidate] = {}

    for i, nid in enumerate(need_ids):
        for c in cands_by_need[nid]:
            j = r_index[c.resource_id]
            cost[i][j] = -c.score  # 求解器是 minimize，取負號變成 maximize
            lookup[(i, j)] = c

    row_to_col = min_cost_assignment(cost)

    assignment: dict[str, Candidate] = {}
    for r, c in row_to_col.items():
        if cost[r][c] >= BIG:
            continue  # 矩陣形狀逼出來的配對，實際上不相容，不採用
        assignment[need_ids[r]] = lookup[(r, c)]
    return assignment


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
        needs_with_vuln = [
            (need, _vulnerability_pts(need.requester_id, db))
            for need in open_needs
        ]
        # 排序只影響回傳的 details 顯示順序（批次指派本身跟處理順序
        # 無關），維持「緊急度 → 脆弱度 → 先到先得」方便閱讀。
        needs_with_vuln.sort(
            key=lambda item: (
                item[0].urgency,
                item[1],
                -(item[0].created_at.timestamp() if item[0].created_at else 0),
            ),
            reverse=True,
        )

        resource_assignment = _assign_resources_optimally(needs_with_vuln, db)

        matched = suggested = skipped = notified = 0
        details = []

        for need, vulnerability in needs_with_vuln:
            best = resource_assignment.get(str(need.id))

            if best is None:
                # Layer 2（資源點）非稀缺，不需要參與批次指派，逐筆
                # 獨立比對即可——同一個資源點可以同時是多筆需求的解。
                point_cands = _collect_from_points(need, db, {}, vulnerability)
                best = max(point_cands, key=lambda c: c.score) if point_cands else None

            if best is None:
                skipped += 1
                details.append({
                    "need_id": str(need.id),
                    "result": "skipped",
                    "reason": "無相符物資",
                    "vulnerability": round(vulnerability, 1),
                })
                continue

            if best.source == "resource" and best.resource_id:
                # 個人物資：這裡只「建議」，不自動通知志工——
                # 計畫書明文承諾「所有建議仍須管理員確認後才會執行」，
                # 真正發 LINE 通知要等管理員按下確認（見 confirm_dispatch）。
                previous_status = need.status
                need.status = "suggested"
                need.matched_resource_id = best.resource_id
                res_obj = db.query(CommunityResource).filter(
                    CommunityResource.id == best.resource_id
                ).first()
                if res_obj:
                    res_obj.is_available = False
                _log_dispatch_event(
                    db,
                    "propose_dispatch",
                    need=need,
                    resource=res_obj,
                    actor_label="system:auto_dispatch",
                    previous_status=previous_status,
                    new_status=need.status,
                    outcome="suggested",
                    details={
                        "source": best.source,
                        "resource_name": best.res_name,
                        "score": round(best.score, 3),
                        "dist_km": round(best.dist_km, 3) if not math.isinf(best.dist_km) else None,
                        "vulnerability": round(vulnerability, 3),
                        "breakdown": best.breakdown,
                    },
                )

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
                previous_status = need.status
                need.status = "matched"
                _log_dispatch_event(
                    db,
                    "auto_match_facility",
                    need=need,
                    actor_label="system:auto_dispatch",
                    previous_status=previous_status,
                    new_status=need.status,
                    outcome="matched",
                    details={
                        "source": best.source,
                        "point_id": best.point_id,
                        "facility_name": best.res_name,
                        "score": round(best.score, 3),
                        "dist_km": round(best.dist_km, 3) if not math.isinf(best.dist_km) else None,
                        "vulnerability": round(vulnerability, 3),
                        "breakdown": best.breakdown,
                    },
                )
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

    previous_status = need.status
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

    dist = _distance_km(need.lat, need.lng, resource.lat, resource.lng)
    _log_dispatch_event(
        db,
        "manual_dispatch",
        need=need,
        resource=resource,
        actor_label="manager",
        previous_status=previous_status,
        new_status=need.status,
        outcome="matched",
        details={
            "resource_name": resource.name,
            "volunteer_notified": notified,
            "dist_km": round(dist, 3) if not math.isinf(dist) else None,
        },
    )
    db.commit()
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
        previous_status = need.status
        need.status = "open"
        need.matched_resource_id = None
        _log_dispatch_event(
            db,
            "confirm_dispatch",
            need=need,
            actor_label="manager",
            previous_status=previous_status,
            new_status=need.status,
            outcome="resource_missing",
            details={"reason": "matched resource no longer exists"},
        )
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

    previous_status = need.status
    need.status = "matched"
    db.commit()
    _log_dispatch_event(
        db,
        "confirm_dispatch",
        need=need,
        resource=resource,
        actor_label="manager",
        previous_status=previous_status,
        new_status=need.status,
        outcome="matched",
        details={
            "resource_name": resource.name,
            "volunteer_notified": notified,
        },
    )
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

    previous_status = need.status
    previous_resource_id = str(need.matched_resource_id) if need.matched_resource_id else None
    if need.matched_resource_id:
        resource = db.query(CommunityResource).filter(
            CommunityResource.id == need.matched_resource_id
        ).first()
        if resource:
            resource.is_available = True

    need.status = "open"
    need.matched_resource_id = None
    _log_dispatch_event(
        db,
        "decline_suggestion",
        need=need,
        resource_id=previous_resource_id,
        actor_label="manager",
        previous_status=previous_status,
        new_status=need.status,
        outcome="declined",
        details={"released_resource_id": previous_resource_id},
    )
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
                "breakdown": {k: round(v, 1) for k, v in c.breakdown.items()},
            }
            for c in cands[:20]
        ],
    }
