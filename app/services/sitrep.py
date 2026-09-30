"""災情摘要（SITREP）：應變中心定時往上回報的那一頁。

緊急模式時從啟動那一刻算起，日常模式時看今天。內容都是系統裡已經有的資料：
點名、求救（受理與結案花了多久）、物資需求、派遣與送達、收容所容量。
"""
from datetime import datetime, timedelta
from statistics import median

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.labels import NEED_STATUS_ZH, NEED_TYPE_ZH
from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.services import rollcall
from app.timeutil import now_utc

TAIPEI = timedelta(hours=8)


def _naive(dt: datetime) -> datetime:
    return dt.replace(tzinfo=None) if dt.tzinfo else dt


def _local(dt: datetime | None) -> str | None:
    return (_naive(dt) + TAIPEI).strftime("%m/%d %H:%M") if dt else None


def _minutes(values: list[float]) -> float | None:
    return round(median(values), 1) if values else None


EVENT_LABEL = {
    "sos_acknowledged": "受理求救", "sos_resolved": "求救結案", "sos_escalated": "求救逾時沒人受理，再通知管理員",
    "sos_reported_119": "轉報 119", "welfare_check_requested": "家屬請人探視",
    "propose_dispatch": "建立派遣建議", "confirm_dispatch": "核准派遣", "manual_dispatch": "派遣",
    "auto_match_facility": "自動媒合資源點", "decline_suggestion": "退回派遣建議", "task_accept": "志工接單",
    "task_decline": "志工婉拒", "task_delivered": "物資送達", "task_report": "現場回報",
    "cancel_need": "取消需求", "admin_message": "管理員傳訊息給志工",
}
TIMELINE_LIMIT = 60


def actor_name(label: str | None) -> str:
    """紀錄裡的操作者代號（manager、admin:王小明）轉成人話。"""
    if not label:
        return "系統"
    if label == "manager" or label.startswith("未登入"):
        return "後台"
    for prefix, name in (("admin:", "管理員："), ("志工:", "志工："), ("管理員:", "管理員："), ("家屬:", "家屬：")):
        if label.startswith(prefix):
            return name + label[len(prefix):]
    return label


def timeline(db: Session, since: datetime) -> list[dict]:
    """這段期間發生的事，新的在前：求救、受理、轉報 119、點名的求助與不舒服、派遣、模式切換。"""
    from app.models.admin_audit import AdminAudit
    from app.models.safety_check import SafetyCheck
    from app.services.admin_audit import describe
    since = since - timedelta(seconds=2)  # 資料庫預設時間有的只到秒；跟啟動同一秒的事件不能漏掉
    rows: list[tuple[datetime, str, str, str]] = []
    for n in db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos", CommunityNeed.created_at >= since).all():
        rows.append((_naive(n.created_at), "通報求救", n.requester.name if n.requester else "", n.address or ""))
    for e in db.query(DispatchEvent).filter(DispatchEvent.created_at >= since).all():
        if e.action in EVENT_LABEL:
            who = e.need.requester.name if e.need is not None and e.need.requester is not None else ""
            rows.append((_naive(e.created_at), EVENT_LABEL[e.action], who, actor_name(e.actor_label)))
    for s in db.query(SafetyCheck).filter(SafetyCheck.responded_at >= since, SafetyCheck.status.in_(("help", "unwell"))).all():
        rows.append((_naive(s.responded_at), "點名回報：" + ("需要協助" if s.status == "help" else "不舒服"),
                     s.user.name if s.user else "", s.marked_by or "本人"))
    mode_switches = db.query(AdminAudit).filter(AdminAudit.created_at >= since, or_(
        AdminAudit.path.like("/api/dashboard/mode%"),  # 後台按鈕
        AdminAudit.path.in_(("啟動緊急模式", "解除緊急模式")))).all()  # 管理員在 LINE 上確認
    for a in mode_switches:
        rows.append((_naive(a.created_at), describe(a.method, a.path), "", actor_name(a.actor_label)))
    rows.sort(key=lambda r: r[0], reverse=True)
    return [{"time": _local(t), "what": what, "who": who, "by": by} for t, what, who, by in rows[:TIMELINE_LIMIT]]


def period(db: Session) -> tuple[datetime, str]:
    """回報的起點（UTC、無時區）與說明。"""
    round_id = rollcall.current_round(db)
    if round_id:
        start = _naive(datetime.fromisoformat(round_id))
        return start, f"緊急模式啟動（{_local(start)}）至今"
    local_midnight = (_naive(now_utc()) + TAIPEI).replace(hour=0, minute=0, second=0, microsecond=0)
    return local_midnight - TAIPEI, "今日 00:00 至今"


def build(db: Session) -> dict:
    since, label = period(db)
    now = _naive(now_utc())
    window = since - timedelta(seconds=2)  # 跟 timeline 一樣，資料庫時間只到秒時不漏掉同一秒的紀錄

    # 這段期間新增的，加上之前就在、到現在還沒結案的（持續中的案子不能因為換期就從報告消失）
    needs = db.query(CommunityNeed).filter(or_(
        CommunityNeed.created_at >= window, CommunityNeed.status.in_(("open", "suggested", "matched")))).all()
    sos = [n for n in needs if n.need_type == "sos"]
    supplies = [n for n in needs if n.need_type != "sos"]
    resolved_at = {str(e.need_id): _naive(e.created_at) for e in db.query(DispatchEvent).filter(
        DispatchEvent.action == "sos_resolved", DispatchEvent.need_id.in_([str(n.id) for n in sos])).all()} if sos else {}

    def sos_row(n: CommunityNeed) -> dict:
        state = "已結案" if n.status == "fulfilled" else "處理中" if n.responder_id else "未受理" if n.status == "open" else "已取消"
        return {"name": n.requester.name if n.requester else "", "address": n.address or "",
                "reported": _local(n.created_at), "state": state,
                "responder": n.responder.name if n.responder_id and n.responder else None,
                "waiting_min": round((now - _naive(n.created_at)).total_seconds() / 60) if state == "未受理" else None}

    ack = [(_naive(n.acknowledged_at) - _naive(n.created_at)).total_seconds() / 60 for n in sos if n.acknowledged_at]
    close = [(resolved_at[str(n.id)] - _naive(n.created_at)).total_seconds() / 60 for n in sos if str(n.id) in resolved_at]

    by_type: dict[str, dict] = {}
    for n in supplies:
        row = by_type.setdefault(NEED_TYPE_ZH.get(n.need_type, n.need_type), {"total": 0, "open": 0, "done": 0})
        row["total"] += 1
        if n.status == "fulfilled":
            row["done"] += 1
        elif n.status in ("open", "suggested", "matched"):
            row["open"] += 1
    events = db.query(DispatchEvent).filter(DispatchEvent.created_at >= window).all()
    count = lambda *actions: sum(e.action in actions for e in events)  # noqa: E731

    shelters = db.query(ResourcePoint).filter(ResourcePoint.is_active.is_(True), ResourcePoint.point_type == "shelter").all()
    capacity = sum(p.capacity or 0 for p in shelters)
    load = sum(p.current_load or 0 for p in shelters)

    board = rollcall.board(db)
    volunteers = db.query(User).filter(User.role_filter("volunteer"), User.is_active.is_(True)).count()
    return {
        "generated_at": _local(now), "period": label, "mode": "緊急模式" if board.get("active") else "日常模式",
        "rollcall": {"counts": board["counts"], "total": board["total"],
                     "follow_up": [{k: p[k] for k in ("name", "status_label", "address", "phone", "vulnerability")}
                                   for p in board["people"] if p["status"] in ("help", "pending")]} if board.get("active") else None,
        "sos": {"total": len(sos), "waiting": sum(r["state"] == "未受理" for r in map(sos_row, sos)),
                "in_progress": sum(1 for n in sos if n.status == "open" and n.responder_id),
                "closed": sum(1 for n in sos if n.status == "fulfilled"),
                "median_ack_min": _minutes(ack), "median_close_min": _minutes(close),
                "rows": [sos_row(n) for n in sorted(sos, key=lambda n: n.created_at or now)]},
        "needs": {"total": len(supplies), "by_type": by_type,
                  "by_status": {NEED_STATUS_ZH.get(s, s): sum(n.status == s for n in supplies)
                                for s in ("open", "suggested", "matched", "fulfilled", "cancelled")}},
        "dispatch": {"dispatched": count("confirm_dispatch", "manual_dispatch", "auto_match_facility"),
                     "accepted": count("task_accept"), "delivered": count("task_delivered")},
        "shelters": {"count": len(shelters), "capacity": capacity, "load": load},
        "volunteers": volunteers,
        "timeline": timeline(db, since),
    }
