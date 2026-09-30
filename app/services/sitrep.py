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

    # 這段期間新增的，加上之前就在、到現在還沒結案的（持續中的案子不能因為換期就從報告消失）
    needs = db.query(CommunityNeed).filter(or_(
        CommunityNeed.created_at >= since, CommunityNeed.status.in_(("open", "suggested", "matched")))).all()
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
    events = db.query(DispatchEvent).filter(DispatchEvent.created_at >= since).all()
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
    }
