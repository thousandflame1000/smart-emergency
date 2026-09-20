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
from datetime import UTC, datetime, timedelta
from typing import NamedTuple
from sqlalchemy.orm import Session

from app.config import settings
from app.services.hungarian import min_cost_assignment
from app.services import road_network
from app.services.proposal_workflow import ProposalService

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


def _utcnow_naive() -> datetime:
    """Current UTC time as a naive datetime.

    SQLAlchemy model timestamps in this project are naive UTC values from
    database defaults. Keep arithmetic in that same shape while avoiding
    the deprecated datetime.utcnow() API.
    """
    return datetime.now(UTC).replace(tzinfo=None)


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


NEED_TYPE_ZH = {
    "water": "飲用水", "food": "食物", "first_aid": "急救用品", "shelter": "庇護所",
    "vehicle": "交通工具", "tool": "工具", "other": "物資", "sos": "緊急求助",
    "demo_water": "飲用水",
}


def _push_text(line_uid: str | None, text: str) -> bool:
    """Best-effort LINE text push. Never raises: a notification failure must not roll
    back or block a dispatch decision that has already been committed."""
    if not line_uid:
        return False
    try:
        from app.services import line_notify
        line_notify.send_text(line_uid, text)
        return True
    except Exception:
        return False


def notify_requester(need: CommunityNeed, text: str) -> bool:
    """Tell the person who asked for help what is happening with their request.

    Before this existed the whole dispatch chain only ever talked to the volunteer:
    the resident who reported the need never learned anyone was coming, that the
    volunteer had cancelled, or that the delivery was done."""
    requester = need.requester
    return _push_text(requester.line_uid if requester else None, text)


def assignee_user_id(need: CommunityNeed) -> str | None:
    """User id of the volunteer this need is currently assigned to (owner of the
    matched resource), or None for facility matches / unassigned needs."""
    res = need.matched_resource
    return str(res.owner_id) if res and res.owner_id else None


def _wait_pts(need: CommunityNeed) -> float:
    if not need.created_at:
        return 0.0
    hours = (_utcnow_naive() - need.created_at).total_seconds() / 3600
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
    cutoff = _utcnow_naive().date() - timedelta(days=VULNERABILITY_LOOKBACK_DAYS)

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


def _distance_km(lat1, lng1, lat2, lng2, db: Session | None = None) -> float:
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
        road_d = road_network.road_distance_km(lat1, lng1, lat2, lng2, db=db)
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
    include_resource_id=None,
) -> list[Candidate]:
    compat = _compat_types(need.need_type)
    type_aff = {t: a for t, a in compat}

    availability = CommunityResource.is_available == True
    if include_resource_id is not None:
        # 已被建議給這筆需求的物資因為保留而 is_available=False，如果不特別
        # 帶進來，管理員打開「待確認」需求的詳情只會看到「無候選資源」，
        # 完全看不到系統為什麼配這個人。
        availability = (availability) | (CommunityResource.id == include_resource_id)
    res_list = (
        db.query(CommunityResource)
        .filter(
            CommunityResource.resource_type.in_(list(type_aff.keys())),
            availability,
        )
        .all()
    )

    wait_pts = _wait_pts(need)
    candidates = []
    for r in res_list:
        if r.owner_id == need.requester_id:
            continue  # 不能把自己的物資配給自己的需求
        affinity = type_aff.get(r.resource_type, 0.0)
        dist = _distance_km(need.lat, need.lng, r.lat, r.lng, db)
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

        dist = _distance_km(need.lat, need.lng, pt.lat, pt.lng, db)
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
            .filter(CommunityNeed.status == "open", CommunityNeed.need_type != "sos")
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
        proposal_service = ProposalService(db) if settings.TASK_WORKFLOW_V2 else None

        for need, vulnerability in needs_with_vuln:
            best = resource_assignment.get(str(need.id))

            if best is None:
                # Layer 2（資源點）非稀缺，不需要參與批次指派，逐筆
                # 獨立比對即可——同一個資源點可以同時是多筆需求的解。
                point_cands = _collect_from_points(need, db, {}, vulnerability)
                best = max(point_cands, key=lambda c: c.score) if point_cands else None

            if best is None:
                skipped += 1
                # "無相符物資" 這個單一理由曾經逼管理員自己一筆筆點「查看
                # 詳情」才查得出來——今天實際卡住好幾個小時的根因就是
                # 「這筆需求根本沒有座標，永遠配不到，不管重試幾次」，
                # 跟「有座標但單純太遠／沒有同類型物資」是完全不同的
                # 處置方式，前者要去補地址，後者才是等資源或擴大範圍。
                if need.lat is None or need.lng is None:
                    reason = "缺少座標，需要先補地址才配得到（不是資源不足）"
                else:
                    reason = "距離過遠或沒有相符類型的可用物資"
                details.append({
                    "need_id": str(need.id),
                    "result": "skipped",
                    "reason": reason,
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
                proposal = None
                decision_details = {
                    "source": best.source,
                    "resource_name": best.res_name,
                    "score": round(best.score, 3),
                    "dist_km": round(best.dist_km, 3) if not math.isinf(best.dist_km) else None,
                    "vulnerability": round(vulnerability, 3),
                    "breakdown": best.breakdown,
                }
                if proposal_service:
                    proposal = proposal_service.create_from_dispatch_suggestion(
                        need=need,
                        algorithm="legacy-dispatch-hungarian",
                        algorithm_version="1",
                        score=best.score,
                        explanation_json=decision_details,
                        candidate_resource_id=best.resource_id,
                        candidate_assignee_id=res_obj.owner_id if res_obj else None,
                    )
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
                        **decision_details,
                        "proposal_id": str(proposal.id) if proposal else None,
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
                    "proposal_id":   str(proposal.id) if proposal else None,
                })
            else:
                # 資源點（固定設施）：本來就不會發 LINE 通知志工，
                # 只是把「有哪個資源點可用」標記出來給管理員手動協調，
                # 不牽涉「自動指派志工」，維持直接標記完成。
                previous_status = need.status
                decision_details = {
                    "source": best.source,
                    "point_id": best.point_id,
                    "facility_name": best.res_name,
                    "score": round(best.score, 3),
                    "dist_km": round(best.dist_km, 3) if not math.isinf(best.dist_km) else None,
                    "vulnerability": round(vulnerability, 3),
                    "breakdown": best.breakdown,
                }
                proposal = None
                if proposal_service:
                    need.status = "suggested"
                    proposal = proposal_service.create_from_dispatch_suggestion(
                        need=need,
                        algorithm="legacy-dispatch-facility",
                        algorithm_version="1",
                        score=best.score,
                        explanation_json=decision_details,
                        candidate_facility_id=best.point_id,
                    )
                else:
                    need.status = "matched"
                _log_dispatch_event(
                    db,
                    "propose_dispatch" if proposal_service else "auto_match_facility",
                    need=need,
                    actor_label="system:auto_dispatch",
                    previous_status=previous_status,
                    new_status=need.status,
                    outcome="suggested" if proposal_service else "matched",
                    details={
                        **decision_details,
                        "proposal_id": str(proposal.id) if proposal else None,
                    },
                )
                if proposal_service:
                    suggested += 1
                else:
                    matched += 1
                    notify_requester(
                        need,
                        f"📍 已為您的「{NEED_TYPE_ZH.get(need.need_type, need.need_type)}」需求找到資源點："
                        f"{best.res_name}。資源點不會主動送到府，管理員會協助聯繫；"
                        f"如需人力協助請傳「需要幫忙」。",
                    )
                details.append({
                    "need_id":       str(need.id),
                    "result":        "suggested" if proposal_service else "matched",
                    "source":        best.source,
                    "matched":       best.res_name,
                    "dist_km":       round(best.dist_km, 2) if not math.isinf(best.dist_km) else None,
                    "score":         round(best.score, 1),
                    "vulnerability": round(vulnerability, 1),
                    "notified":      False,
                    "proposal_id":   str(proposal.id) if proposal else None,
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
def _announce_match_to_requester(need: CommunityNeed, resource: CommunityResource | None) -> None:
    label = NEED_TYPE_ZH.get(need.need_type, need.need_type)
    who = resource.owner.name if resource is not None and resource.owner else "社區志工"
    notify_requester(
        need,
        f"🚚 好消息！已有人接下您的「{label}」需求：{who} 正在準備前往。\n"
        f"傳「我的需求」可以查看最新進度；如果一直沒等到人，請傳「需要幫忙」。",
    )


def manual_dispatch(need_id: str, resource_id: str, db: Session, *,
                    actor_id: str | None = None, actor_label: str = "manager") -> dict:
    """手動指定媒合，立即 LINE 通知志工"""
    need     = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    resource = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()

    if not need or not resource:
        return {"error": "need or resource not found"}
    if need.status not in ("open", "suggested"):
        return {"error": f"需求目前狀態為「{need.status}」，只有待媒合或待確認的需求可以手動指派。"}
    if need.need_type == "sos":
        return {"error": "緊急求助是人身安全事件，不是物資需求，請直接聯絡當事人或撥打 119。"}
    if resource.owner_id == need.requester_id:
        return {"error": "不能把需求者自己的物資指派給自己的需求。"}
    if not resource.is_available and str(resource.id) != str(need.matched_resource_id or ""):
        return {"error": "這份物資已經被其他需求保留，不能重複指派。"}

    previous_status = need.status
    if need.matched_resource_id and str(need.matched_resource_id) != str(resource.id):
        old = db.query(CommunityResource).filter(CommunityResource.id == need.matched_resource_id).first()
        if old:
            old.is_available = True  # 放掉原本建議的那份，不要讓它永遠卡在保留狀態
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
                distance_km=None if need.lat is None or need.lng is None or resource.lat is None or resource.lng is None else _distance_km(need.lat, need.lng, resource.lat, resource.lng, db),
                dest_lat=need.lat,
                dest_lng=need.lng,
            )
            notified = True
        except Exception:
            pass

    dist = _distance_km(need.lat, need.lng, resource.lat, resource.lng, db)
    _announce_match_to_requester(need, resource)
    _log_dispatch_event(
        db,
        "manual_dispatch",
        need=need,
        resource=resource,
        actor_id=actor_id,
        actor_label=actor_label,
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
                distance_km=None if need.lat is None or need.lng is None or resource.lat is None or resource.lng is None else _distance_km(need.lat, need.lng, resource.lat, resource.lng, db),
                dest_lat=need.lat,
                dest_lng=need.lng,
            )
            notified = True
        except Exception:
            pass

    previous_status = need.status
    need.status = "matched"
    db.commit()
    _announce_match_to_requester(need, resource)
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
# 取消需求 / 資源釋放
# ──────────────────────────────────────────────────────────
def resolve_sos(need_id: str, db: Session, *, actor_label: str = "manager") -> dict:
    """管理員確認已經聯繫、處理完一筆一鍵求助。

    一鍵求助不是物資需求，不會被媒合、也沒有志工任務卡可以「送達」，所以之前它一旦建立就
    只能被「取消」——取消的語意是「這個需求不成立」，不是「人已經確認安全」，稽核紀錄上
    分不出來，當事人也不會收到任何後續。"""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return {"error": "need not found"}
    if need.need_type != "sos":
        return {"error": "只有一鍵求助可以用「已聯繫處理」關閉，一般物資需求請走媒合流程。"}
    if need.status == "fulfilled":
        return {"message": "already resolved", "need_id": need_id, "already_resolved": True}
    if need.status != "open":
        return {"error": f"求助單目前狀態為「{need.status}」，不能標成已處理。"}
    previous_status = need.status
    need.status = "fulfilled"
    _log_dispatch_event(
        db, "sos_resolved", need=need, actor_label=actor_label,
        previous_status=previous_status, new_status=need.status, outcome="resolved",
        details={},
    )
    db.commit()
    notify_requester(
        need,
        "✅ 管理員已確認處理您的緊急求助。如果您仍然需要協助，請再傳「需要幫忙」；"
        "生命危險請直接撥打 119。",
    )
    return {"message": "sos resolved", "need_id": need_id}


def cancel_need(need_id: str, db: Session) -> dict:
    """Cancel a request and release any active resource reservation."""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return {"error": "need not found"}

    previous_status = need.status
    previous_resource_id = str(need.matched_resource_id) if need.matched_resource_id else None

    if previous_status == "cancelled":
        return {
            "message": "need already cancelled",
            "need_id": need_id,
            "already_cancelled": True,
        }

    resource_released = False
    if need.matched_resource_id and previous_status in {"suggested", "matched"}:
        resource = db.query(CommunityResource).filter(
            CommunityResource.id == need.matched_resource_id
        ).first()
        if resource:
            resource.is_available = True
            resource_released = True

    volunteer_uid = None
    if previous_status == "matched" and need.matched_resource_id:
        _res = db.query(CommunityResource).filter(CommunityResource.id == need.matched_resource_id).first()
        if _res is not None and _res.owner is not None:
            volunteer_uid = _res.owner.line_uid
    need.status = "cancelled"
    need.matched_resource_id = None
    if volunteer_uid:
        _push_text(
            volunteer_uid,
            f"ℹ️ 剛才派給您的「{NEED_TYPE_ZH.get(need.need_type, need.need_type)}」任務已取消"
            f"（需求已撤回），不需要再前往，謝謝您！",
        )
    _log_dispatch_event(
        db,
        "cancel_need",
        need=need,
        resource_id=previous_resource_id,
        actor_label="manager",
        previous_status=previous_status,
        new_status=need.status,
        outcome="cancelled",
        details={
            "released_resource_id": previous_resource_id,
            "resource_released": resource_released,
        },
    )
    db.commit()

    return {
        "message": "need cancelled",
        "need_id": need_id,
        "released_resource_id": previous_resource_id,
        "resource_released": resource_released,
    }


# ──────────────────────────────────────────────────────────
# LINE volunteer task callbacks
# ──────────────────────────────────────────────────────────
def mark_task_delivered(
    need_id: str,
    db: Session,
    *,
    actor_id: str | None = None,
    actor_label: str = "volunteer:line",
) -> dict:
    """Mark a matched task as fulfilled and keep an audit event."""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return {"error": "need not found"}

    previous_status = need.status
    previous_resource_id = str(need.matched_resource_id) if need.matched_resource_id else None

    if previous_status == "fulfilled":
        return {
            "message": "task already fulfilled",
            "need_id": need_id,
            "already_fulfilled": True,
        }
    if previous_status != "matched":
        # 過期的任務卡：需求已被退回、取消或還沒核准，不能憑一次按鈕就直接
        # 變成「已完成」——之前需求退回 open 之後志工再按「已送達」，會在
        # 沒有任何指派的狀況下直接完成，物資也沒有被消耗。
        return {
            "error": "invalid task state",
            "need_id": need_id,
            "need_status": previous_status,
        }

    need.status = "fulfilled"
    _log_dispatch_event(
        db,
        "task_delivered",
        need=need,
        resource_id=previous_resource_id,
        actor_id=actor_id,
        actor_label=actor_label,
        previous_status=previous_status,
        new_status=need.status,
        outcome="fulfilled",
        details={"matched_resource_id": previous_resource_id},
    )
    db.commit()
    notify_requester(
        need,
        f"✅ 志工回報已把「{NEED_TYPE_ZH.get(need.need_type, need.need_type)}」送達。\n"
        f"如果您其實還沒收到，請傳「需要幫忙」讓我們知道。",
    )

    return {
        "message": "task marked fulfilled",
        "need_id": need_id,
        "resource_id": previous_resource_id,
    }


# ── 接單：志工自己挑需求，或確認管理員派給他的任務 ─────────────────────────────
def list_claimable(user, db: Session, limit: int = 5) -> dict:
    """Open needs this volunteer could take right now: they own an available resource of the same type.

    Returns {"items": [{need, resource, dist_km}], "open_total": n, "has_resources": bool}."""
    mine = db.query(CommunityResource).filter(
        CommunityResource.owner_id == user.id, CommunityResource.is_available == True,  # noqa: E712
    ).all()
    by_type: dict[str, CommunityResource] = {}
    for r in mine:
        by_type.setdefault(r.resource_type, r)
    needs = db.query(CommunityNeed).filter(
        CommunityNeed.status == "open", CommunityNeed.need_type != "sos",
        CommunityNeed.requester_id != user.id,
    ).all()
    items = []
    for n in needs:
        r = by_type.get(n.need_type)
        if r is None:
            continue
        d = _distance_km(n.lat, n.lng, r.lat, r.lng, db)
        items.append({"need": n, "resource": r, "dist_km": None if math.isinf(d) else round(d, 1)})
    items.sort(key=lambda i: (-(i["need"].urgency or 0), i["dist_km"] if i["dist_km"] is not None else 1e9))
    return {"items": items[:limit], "open_total": len(needs), "has_resources": bool(mine)}


def claim_need(need_id: str, user, db: Session) -> dict:
    """A volunteer takes an open need with their own matching resource."""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return {"error": "找不到這筆需求，可能已被取消或刪除。"}
    if need.status != "open":
        return {"error": "這筆需求已經有人接走或處理了，請重新傳「接單」看最新清單。"}
    resource = db.query(CommunityResource).filter(
        CommunityResource.owner_id == user.id, CommunityResource.resource_type == need.need_type,
        CommunityResource.is_available == True,  # noqa: E712
    ).first()
    if not resource:
        return {"error": "您沒有可提供的對應物資，請先傳「登記物資」。"}
    result = manual_dispatch(need_id, str(resource.id), db, actor_id=str(user.id), actor_label="volunteer:claim")
    if "error" in result:
        return result
    db.refresh(need)
    _log_dispatch_event(
        db, "task_accept", need=need, resource=resource, actor_id=str(user.id), actor_label="volunteer:claim",
        previous_status="matched", new_status="matched", outcome="claimed", details={"claimed": True},
    )
    db.commit()
    from app.services.alert import notify_admins
    try:
        notify_admins(db, f"🙋 志工「{user.name}」自行接單：{NEED_TYPE_ZH.get(need.need_type, need.need_type)}"
                          f"（{need.address or '地址未填'}）")
    except Exception:
        pass
    return {"message": "claimed", "need_id": need_id, "resource_name": resource.name}


ASSIGNMENT_ACTIONS = ("manual_dispatch", "confirm_dispatch")


def accepted_need_ids(db: Session, need_ids: list) -> set[str]:
    """Needs whose current assignment the volunteer has confirmed: a task_accept event newer
    than the most recent dispatch. (A re-dispatch after a decline starts unconfirmed again.)"""
    if not need_ids:
        return set()
    events = (db.query(DispatchEvent)
              .filter(DispatchEvent.need_id.in_(need_ids),
                      DispatchEvent.action.in_(("task_accept",) + ASSIGNMENT_ACTIONS))
              .order_by(DispatchEvent.created_at, DispatchEvent.id).all())
    state: dict[str, bool] = {}
    for e in events:
        state[str(e.need_id)] = e.action == "task_accept"
    return {k for k, v in state.items() if v}


def accept_task(need_id: str, db: Session, *, actor_id: str | None = None,
                actor_label: str = "volunteer:line") -> dict:
    """Volunteer confirms they are going. Requester is told; admin sees the task as confirmed."""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return {"error": "need not found"}
    if need.status != "matched":
        return {"error": "invalid task state", "need_id": need_id, "need_status": need.status}
    if str(need.id) in accepted_need_ids(db, [need.id]):
        return {"message": "already accepted", "need_id": need_id, "already_accepted": True}
    _log_dispatch_event(
        db, "task_accept", need=need, resource_id=str(need.matched_resource_id) if need.matched_resource_id else None,
        actor_id=actor_id, actor_label=actor_label, previous_status="matched", new_status="matched",
        outcome="accepted", details={},
    )
    db.commit()
    notify_requester(
        need,
        f"🚚 志工已確認接下您的「{NEED_TYPE_ZH.get(need.need_type, need.need_type)}」需求，正在準備前往。",
    )
    return {"message": "accepted", "need_id": need_id}


REPORT_OUTCOMES = {"delivered", "cannot_go", "issue"}
REPORT_OUTCOME_ZH = {"delivered": "已送達", "cannot_go": "無法前往", "issue": "現場遇到狀況"}


def report_task(
    need_id: str,
    db: Session,
    *,
    outcome: str,
    note: str | None = None,
    actor_id: str | None = None,
    actor_label: str = "volunteer:web",
) -> dict:
    """A volunteer's written report on a task: delivered, can't go, or "something came up".

    The card's two buttons could only close or drop a task; the volunteer had no way to say
    "nobody answered the door" or "address doesn't exist", so the admin only ever saw a status
    flip. The note is stored on the audit trail and pushed to the admins."""
    if outcome not in REPORT_OUTCOMES:
        return {"error": "invalid outcome"}
    note = (note or "").strip() or None
    if outcome == "issue" and not note:
        return {"error": "回報現場狀況時，請寫下發生了什麼事。"}

    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return {"error": "need not found"}
    resource_id = str(need.matched_resource_id) if need.matched_resource_id else None
    need_status = need.status

    if outcome == "delivered":
        result = mark_task_delivered(need_id, db, actor_id=actor_id, actor_label=actor_label)
    elif outcome == "cannot_go":
        result = decline_task_assignment(need_id, db, actor_id=actor_id, actor_label=actor_label)
    else:
        if need_status != "matched":
            return {"error": "invalid task state", "need_id": need_id, "need_status": need_status}
        result = {"message": "issue recorded", "need_id": need_id}
    if "error" in result:
        return result

    if note or outcome != "delivered":
        db.refresh(need)
        _log_dispatch_event(
            db, "task_report", need=need, resource_id=resource_id, actor_id=actor_id,
            actor_label=actor_label, previous_status=need_status, new_status=need.status,
            outcome=outcome, details={"note": note, "outcome": outcome},
        )
        db.commit()
        from app.services.alert import notify_admins
        label = NEED_TYPE_ZH.get(need.need_type, need.need_type)
        where = f"（{need.address}）" if need.address else ""
        try:
            notify_admins(
                db,
                f"📝 志工回報【{REPORT_OUTCOME_ZH[outcome]}】{label}{where}\n"
                + (f"說明：{note}" if note else "（沒有附說明）")
                + ("\n需求已退回待媒合，請重新派遣。" if outcome == "cannot_go" else ""),
            )
        except Exception:
            pass
    return result


def decline_task_assignment(
    need_id: str,
    db: Session,
    *,
    actor_id: str | None = None,
    actor_label: str = "volunteer:line",
) -> dict:
    """Let a volunteer decline a task, reopening the need and releasing the resource."""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        return {"error": "need not found"}

    previous_status = need.status
    previous_resource_id = str(need.matched_resource_id) if need.matched_resource_id else None

    if previous_status == "open" and previous_resource_id is None:
        return {
            "message": "task already open",
            "need_id": need_id,
            "already_open": True,
        }
    if previous_status != "matched":
        # 已完成或已取消的需求不能被舊的「無法前往」按鈕復活回 open。
        return {
            "error": "invalid task state",
            "need_id": need_id,
            "need_status": previous_status,
        }

    resource_released = False
    if need.matched_resource_id:
        resource = db.query(CommunityResource).filter(
            CommunityResource.id == need.matched_resource_id
        ).first()
        if resource:
            resource.is_available = True
            resource_released = True

    need.status = "open"
    need.matched_resource_id = None
    _log_dispatch_event(
        db,
        "task_decline",
        need=need,
        resource_id=previous_resource_id,
        actor_id=actor_id,
        actor_label=actor_label,
        previous_status=previous_status,
        new_status=need.status,
        outcome="declined",
        details={
            "released_resource_id": previous_resource_id,
            "resource_released": resource_released,
        },
    )
    db.commit()
    notify_requester(
        need,
        f"⚠️ 原本要送「{NEED_TYPE_ZH.get(need.need_type, need.need_type)}」給您的志工臨時無法前往，"
        f"系統正在重新尋找其他志工，請再稍等一下。",
    )

    return {
        "message": "task declined",
        "need_id": need_id,
        "released_resource_id": previous_resource_id,
        "resource_released": resource_released,
    }


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

    include_id = need.matched_resource_id if need.status in ("suggested", "matched") else None
    cands = (
        _collect_from_resources(need, db, {}, vulnerability, include_resource_id=include_id) +
        _collect_from_points(need, db, {}, vulnerability)
    )
    cands.sort(key=lambda c: c.score, reverse=True)
    current_id = str(include_id) if include_id else None

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
                "is_current": bool(current_id and c.resource_id == current_id),
                "breakdown": {k: round(v, 1) for k, v in c.breakdown.items()},
                # 管理員按「確認派遣」之前就該知道這筆會不會真的發出 LINE
                # 通知——之前是按下去才跳「志工未綁定 LINE」，等於白做工
                # 才發現。資源點（固定設施）本來就不靠 LINE 通知，維持
                # 直接標記完成，跟「志工沒綁 LINE」是兩種不同狀況，不能
                # 都顯示成一樣的警告。
                "notify_channel": (
                    "auto" if c.source == "resource_point"
                    else "line" if c.vol_line_uid
                    else "line_unbound"
                ),
            }
            for c in cands[:20]
        ],
    }
