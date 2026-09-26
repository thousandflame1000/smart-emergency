# -*- coding: utf-8 -*-
"""Operational reasoning over the emergency ontology.

This layer turns the ontology from a passive graph into an active watch
floor: it scans requests, resources, alerts, and facilities, then emits evidence-backed findings with recommended
operator actions.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from typing import Any

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import POINT_SUPPLY_TYPES, ResourcePoint
from app.services import dispatch


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
SEVERITY_SCORE = {"critical": 100, "high": 70, "medium": 40, "low": 10}
ACTIVE_NEED_STATUSES = {"open", "suggested", "matched"}


PLAYBOOK_ACTIONS: dict[str, dict[str, Any]] = {
    "manual_dispatch": {
        "title": "處理緊急派遣阻礙",
        "summary": "將待援需求與未解決警報轉成現場行動。",
        "checklist": [
            "開啟優先處理的需求或警報。",
            "檢查可用候選資源及位置資料。",
            "派遣志工、建立需求或請求外部支援。",
        ],
    },
    "confirm_dispatch": {
        "title": "處理待核准派遣",
        "summary": "確認待處理的派遣建議，或釋放預留資源。",
        "checklist": [
            "開啟每筆逾期待核准建議。",
            "候選資源仍可用時確認派遣。",
            "拒絕失效建議，讓資源重新可用。",
        ],
    },
    "task_delivered": {
        "title": "查核逾期派遣任務",
        "summary": "找出尚未確認送達、可能停滯的任務。",
        "checklist": [
            "聯絡承接任務的志工或設施。",
            "完成任務後標記已送達。",
            "承接人無法完成時重新派遣。",
        ],
    },
    "mutate_resource_state": {
        "title": "釋放無任務對應的資源",
        "summary": "檢查沒有進行中任務卻無法使用的資源。",
        "checklist": [
            "檢查資源持有人與近期派遣紀錄。",
            "確認沒有現場任務占用後釋放資源。",
            "留下決策紀錄供查核。",
        ],
    },
    "create_resource_or_facility": {
        "title": "補足物資缺口",
        "summary": "需求超過供應時增援物資、啟用設施或請求外部支援。",
        "checklist": [
            "依需求類型與緊急程度檢查需求。",
            "啟用相容的社區設施或志工資源。",
            "向外部單位通報未滿足的緊急需求。",
        ],
    },
    "redirect_to_alternate_facility": {
        "title": "降低設施超載",
        "summary": "減輕接近滿載的收容與服務設施壓力。",
        "checklist": [
            "檢查目前負載及其他相容設施。",
            "將新增需求轉往負載較低的設施。",
            "所有替代設施滿載時啟用備援容量。",
        ],
    },
}


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _age_minutes(dt: datetime | None, now: datetime) -> int | None:
    if not dt:
        return None
    return max(0, int((now - dt.replace(tzinfo=None)).total_seconds() / 60))


def _obj(object_type: str, raw_id: Any, label: str | None = None) -> dict[str, Any]:
    return {"type": object_type, "id": str(raw_id), "label": label or str(raw_id)}


def _finding(
    findings: list[dict[str, Any]],
    *,
    fid: str,
    severity: str,
    category: str,
    title: str,
    summary: str,
    affected_objects: list[dict[str, Any]],
    evidence: dict[str, Any],
    recommended_action: dict[str, Any],
) -> None:
    findings.append({
        "id": fid,
        "severity": severity,
        "category": category,
        "title": title,
        "summary": summary,
        "affected_objects": affected_objects,
        "evidence": evidence,
        "recommended_action": recommended_action,
    })


def operational_risks(db: Session, limit: int = 30) -> dict[str, Any]:
    limit = max(1, min(limit, 100))
    now = _now()
    findings: list[dict[str, Any]] = []

    _request_risks(db, findings, now)
    _alert_risks(db, findings)
    _resource_integrity_risks(db, findings)
    _supply_risks(db, findings)
    _facility_risks(db, findings)

    findings.sort(key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["category"], f["id"]))
    findings = findings[:limit]
    summary = Counter(f["severity"] for f in findings)
    return {
        "generated_at": now.isoformat() + "Z",
        "summary": {
            "total": len(findings),
            "critical": summary.get("critical", 0),
            "high": summary.get("high", 0),
            "medium": summary.get("medium", 0),
            "low": summary.get("low", 0),
        },
        "findings": findings,
    }


def operational_playbook(db: Session, limit: int = 12) -> dict[str, Any]:
    """Aggregate risk findings into prioritized operator playbook steps."""
    limit = max(1, min(limit, 50))
    source_limit = min(100, max(30, limit * 5))
    risk_report = operational_risks(db, limit=source_limit)
    findings = risk_report["findings"]
    groups: dict[str, list[dict[str, Any]]] = {}
    for finding in findings:
        action = finding.get("recommended_action") or {}
        action_id = action.get("action_id") or "inspect_operational_risks"
        groups.setdefault(action_id, []).append(finding)

    steps = [_playbook_step(action_id, items) for action_id, items in groups.items()]
    steps.sort(key=lambda s: (-s["priority"], SEVERITY_ORDER.get(s["severity"], 9), s["id"]))
    steps = steps[:limit]
    severity_summary = Counter(step["severity"] for step in steps)
    return {
        "generated_at": risk_report["generated_at"],
        "source_summary": risk_report["summary"],
        "counts": {
            "steps": len(steps),
            "critical_steps": severity_summary.get("critical", 0),
            "high_steps": severity_summary.get("high", 0),
            "source_findings": len(findings),
        },
        "steps": steps,
    }


def _playbook_step(action_id: str, findings: list[dict[str, Any]]) -> dict[str, Any]:
    ordered = sorted(findings, key=lambda f: (SEVERITY_ORDER.get(f["severity"], 9), f["id"]))
    top = ordered[0]
    meta = PLAYBOOK_ACTIONS.get(action_id, {})
    severity = top["severity"]
    breakdown = Counter(f["severity"] for f in ordered)
    categories = sorted({f["category"] for f in ordered})
    critical_or_high = breakdown.get("critical", 0) + breakdown.get("high", 0)
    priority = (
        SEVERITY_SCORE.get(severity, 0)
        + min(len(ordered) - 1, 8) * 4
        + min(len(categories), 4) * 3
        + min(critical_or_high, 5) * 2
    )
    endpoints = _unique([
        (f.get("recommended_action") or {}).get("endpoint")
        for f in ordered
        if (f.get("recommended_action") or {}).get("endpoint")
    ])
    affected = _unique_objects(obj for f in ordered for obj in (f.get("affected_objects") or []))
    return {
        "id": f"playbook:{action_id}",
        "priority": priority,
        "severity": severity,
        "action_id": action_id,
        "title": meta.get("title") or ((top.get("recommended_action") or {}).get("label") or top["title"]),
        "summary": meta.get("summary") or top["summary"],
        "expected_impact": _expected_impact(ordered, categories),
        "confidence": _confidence(ordered),
        "finding_ids": [f["id"] for f in ordered],
        "finding_count": len(ordered),
        "affected_objects": affected[:12],
        "evidence": {
            "severity_breakdown": dict(breakdown),
            "categories": categories,
            "primary_finding": top["id"],
            "primary_evidence": top.get("evidence") or {},
        },
        "endpoint": endpoints[0] if endpoints else None,
        "related_endpoints": endpoints[:6],
        "blocked_by": _blocked_by(action_id, ordered),
        "operator_checklist": meta.get("checklist") or ["檢查風險證據。", "選擇並記錄人工確認的行動。"],
    }


def _expected_impact(findings: list[dict[str, Any]], categories: list[str]) -> str:
    breakdown = Counter(f["severity"] for f in findings)
    severe = breakdown.get("critical", 0) + breakdown.get("high", 0)
    if "dispatch" in categories:
        return f"推進 {len(findings)} 項停滯需求的派遣、確認、送達或重新指派。"
    if "capacity" in categories:
        return f"降低 {len(findings)} 項物資或設施的供需壓力。"
    if "care" in categories:
        return f"將 {len(findings)} 項未解決照護警報轉為派遣或結案。"
    return f"處理 {len(findings)} 項營運風險，其中 {severe} 項為高風險。"


def _confidence(findings: list[dict[str, Any]]) -> str:
    if all(f.get("evidence") and f.get("affected_objects") for f in findings):
        return "high"
    if any(f.get("evidence") for f in findings):
        return "medium"
    return "low"


def _blocked_by(action_id: str, findings: list[dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    ids = [f["id"] for f in findings]
    if action_id == "manual_dispatch" and any(fid.startswith("open_need_no_candidate:") for fid in ids):
        blockers.append("至少一筆緊急需求沒有可用候選資源；須先增援物資或請求外部支援。")
    if action_id == "create_resource_or_facility":
        blockers.append("目前供應低於待處理需求，增援前可能仍無法派遣。")
    if action_id == "confirm_dispatch":
        blockers.append("確認或拒絕建議前，預留資源會持續占用。")
    return blockers


def _unique(values: list[Any]) -> list[Any]:
    seen = set()
    out = []
    for value in values:
        key = _dedupe_key(value)
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _unique_objects(values: Any) -> list[dict[str, Any]]:
    seen = set()
    out: list[dict[str, Any]] = []
    for value in values:
        key = (value.get("type"), value.get("id"))
        if key in seen:
            continue
        seen.add(key)
        out.append(value)
    return out


def _dedupe_key(value: Any) -> str:
    if isinstance(value, (dict, list)):
        return str(value)
    return str(value)


def _request_risks(db: Session, findings: list[dict[str, Any]], now: datetime) -> None:
    needs = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.status.in_(["open", "suggested", "matched"]))
        .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at)
        .limit(80)
        .all()
    )
    for need in needs:
        age = _age_minutes(need.created_at, now)
        label = f"{need.need_type} 緊急程度 {need.urgency}"
        if need.status == "open":
            candidates = dispatch.preview_candidates(str(need.id), db).get("candidates", [])
            top = candidates[0] if candidates else None
            if not top and need.urgency >= 4:
                _finding(
                    findings,
                    fid=f"open_need_no_candidate:{need.id}",
                    severity="critical" if need.urgency >= 5 else "high",
                    category="dispatch",
                    title="緊急需求沒有可用候選資源",
                    summary=f"{label} 目前無法媒合至可用物資或設施。",
                    affected_objects=[_obj("ResourceRequest", need.id, label)],
                    evidence={"urgency": need.urgency, "age_minutes": age, "candidate_count": 0},
                    recommended_action={
                        "action_id": "manual_dispatch",
                        "label": "建立或連接緊急資源",
                        "endpoint": f"/api/resources/needs/{need.id}/candidates",
                        "reason": "The automatic assignment engine has no feasible candidate.",
                    },
                )
            elif need.urgency >= 5 or (age is not None and age >= 120):
                _finding(
                    findings,
                    fid=f"open_need_waiting:{need.id}",
                    severity="high" if need.urgency >= 5 else "medium",
                    category="dispatch",
                    title="待處理需求仍在等待派遣",
                    summary=f"{label} 尚未派遣，已有可用候選資源。",
                    affected_objects=[_obj("ResourceRequest", need.id, label)],
                    evidence={
                        "urgency": need.urgency,
                        "age_minutes": age,
                        "top_candidate": top,
                    },
                    recommended_action={
                        "action_id": "manual_dispatch",
                        "label": "派遣最佳候選資源",
                        "endpoint": f"/api/resources/needs/{need.id}/match",
                        "reason": "A feasible candidate exists but the request is still open.",
                    },
                )
        elif need.status == "suggested":
            event = _latest_event(db, str(need.id), ["propose_dispatch"])
            event_age = _age_minutes(event.created_at if event else need.created_at, now)
            if event_age is not None and event_age >= 30:
                _finding(
                    findings,
                    fid=f"suggestion_stale:{need.id}",
                    severity="high" if need.urgency >= 4 else "medium",
                    category="dispatch",
                    title="派遣建議等待人工確認",
                    summary=f"{label} 的派遣建議已等待 {event_age} 分鐘。",
                    affected_objects=[_obj("ResourceRequest", need.id, label)],
                    evidence={"age_minutes": event_age, "matched_resource_id": str(need.matched_resource_id)},
                    recommended_action={
                        "action_id": "confirm_dispatch",
                        "label": "確認或拒絕派遣建議",
                        "endpoint": f"/api/resources/needs/{need.id}/confirm_dispatch",
                        "reason": "Reserved resources should not sit unconfirmed during a disaster.",
                    },
                )
        elif need.status == "matched":
            event = _latest_event(db, str(need.id), ["confirm_dispatch", "manual_dispatch", "auto_match_facility"])
            event_age = _age_minutes(event.created_at if event else need.created_at, now)
            threshold = 60 if need.urgency >= 5 else 120
            if event_age is not None and event_age >= threshold:
                _finding(
                    findings,
                    fid=f"matched_need_overdue:{need.id}",
                    severity="high",
                    category="dispatch",
                    title="執行中需求尚未確認完成",
                    summary=f"{label} 已進入執行中 {event_age} 分鐘，尚未確認送達。",
                    affected_objects=[_obj("ResourceRequest", need.id, label)],
                    evidence={"age_minutes": event_age, "matched_resource_id": str(need.matched_resource_id)},
                    recommended_action={
                        "action_id": "task_delivered",
                        "label": "聯絡志工或重新派遣",
                        "endpoint": f"/api/resources/needs/{need.id}/events",
                        "reason": "A matched task may be stalled in the field.",
                    },
                )


def _alert_risks(db: Session, findings: list[dict[str, Any]]) -> None:
    alerts = (
        db.query(Alert)
        .filter(Alert.status == "sent")
        .order_by(Alert.created_at.desc())
        .limit(60)
        .all()
    )
    for alert in alerts:
        active_need = (
            db.query(CommunityNeed)
            .filter(
                CommunityNeed.requester_id == alert.elderly_id,
                CommunityNeed.status.in_(list(ACTIVE_NEED_STATUSES)),
            )
            .first()
        )
        if active_need:
            continue
        severity = "high" if alert.alert_type in {"help_needed", "no_response_3h"} else "medium"
        _finding(
            findings,
            fid=f"alert_without_need:{alert.id}",
            severity=severity,
            category="care",
            title="未解決警報沒有對應資源需求",
            summary=f"{alert.alert_type} 警報尚未解除，此人員沒有待處理派遣需求。",
            affected_objects=[
                _obj("Alert", alert.id, alert.alert_type),
                _obj("Person", alert.elderly_id, alert.elderly.name if alert.elderly else None),
            ],
            evidence={"alert_type": alert.alert_type, "alert_status": alert.status},
            recommended_action={
                "action_id": "manual_dispatch",
                "label": "建立資源需求或解除警報",
                "endpoint": "/api/resources/needs",
                "reason": "Care alerts should either trigger a need or be explicitly resolved.",
            },
        )


def _resource_integrity_risks(db: Session, findings: list[dict[str, Any]]) -> None:
    resources = db.query(CommunityResource).filter(CommunityResource.is_available == False).limit(80).all()
    for resource in resources:
        linked = (
            db.query(CommunityNeed)
            .filter(
                CommunityNeed.matched_resource_id == resource.id,
                CommunityNeed.status.in_(["suggested", "matched", "fulfilled"]),
            )
            .first()
        )
        if linked:
            continue
        _finding(
            findings,
            fid=f"resource_locked_without_task:{resource.id}",
            severity="medium",
            category="data_integrity",
            title="資源無法使用且沒有對應任務",
            summary=f"{resource.name} 已被占用，但沒有對應需求。",
            affected_objects=[_obj("Resource", resource.id, resource.name)],
            evidence={"resource_type": resource.resource_type, "is_available": resource.is_available},
            recommended_action={
                "action_id": "mutate_resource_state",
                "label": "查核並視情況釋放資源",
                "endpoint": f"/api/resources/{resource.id}/toggle",
                "reason": "Orphaned locks reduce dispatch capacity.",
            },
        )


def _supply_risks(db: Session, findings: list[dict[str, Any]]) -> None:
    open_needs = db.query(CommunityNeed).filter(CommunityNeed.status == "open").all()
    if not open_needs:
        return
    by_type = Counter(n.need_type for n in open_needs)
    max_urgency = {
        need_type: max(n.urgency for n in open_needs if n.need_type == need_type)
        for need_type in by_type
    }
    for need_type, need_count in by_type.items():
        compat = [t for t, _ in dispatch._compat_types(need_type)]
        resource_count = (
            db.query(CommunityResource)
            .filter(
                CommunityResource.is_available == True,
                CommunityResource.resource_type.in_(compat),
            )
            .count()
        )
        facility_count = 0
        for point in db.query(ResourcePoint).filter(ResourcePoint.is_active == True).all():
            if any(t in compat for t in POINT_SUPPLY_TYPES.get(point.point_type, [])):
                facility_count += 1
        supply_count = resource_count + facility_count
        if supply_count >= need_count:
            continue
        severity = "high" if max_urgency[need_type] >= 4 and supply_count == 0 else "medium"
        _finding(
            findings,
            fid=f"supply_gap:{need_type}",
            severity=severity,
            category="capacity",
            title="待處理需求超過可用供應",
            summary=f"有 {need_count} 筆 {need_type} 待處理需求，目前僅 {supply_count} 個供應來源。",
            affected_objects=[],
            evidence={
                "need_type": need_type,
                "open_need_count": need_count,
                "available_personal_resources": resource_count,
                "compatible_facilities": facility_count,
                "compatible_resource_types": compat,
            },
            recommended_action={
                "action_id": "create_resource_or_facility",
                "label": "增援物資、啟用設施或請求外部支援",
                "endpoint": "/api/resources",
                "reason": "Demand exceeds supply visible to the dispatch engine.",
            },
        )


def _facility_risks(db: Session, findings: list[dict[str, Any]]) -> None:
    facilities = db.query(ResourcePoint).filter(ResourcePoint.is_active == True, ResourcePoint.capacity != None).all()
    for facility in facilities:
        if not facility.capacity:
            continue
        ratio = (facility.current_load or 0) / facility.capacity
        if ratio < 0.9:
            continue
        _finding(
            findings,
            fid=f"facility_capacity:{facility.id}",
            severity="high" if ratio >= 1 else "medium",
            category="capacity",
            title="設施接近滿載或已超載",
            summary=f"{facility.name} 目前負載為 {facility.current_load}/{facility.capacity}。",
            affected_objects=[_obj("Facility", facility.id, facility.name)],
            evidence={"current_load": facility.current_load, "capacity": facility.capacity, "ratio": round(ratio, 3)},
            recommended_action={
                "action_id": "redirect_to_alternate_facility",
                "label": "轉介新增需求或啟用備援容量",
                "endpoint": f"/api/resources/points/{facility.id}",
                "reason": "Overloaded facilities create secondary operational risk.",
            },
        )


def _latest_event(db: Session, need_id: str, actions: list[str]) -> DispatchEvent | None:
    return (
        db.query(DispatchEvent)
        .filter(DispatchEvent.need_id == need_id, DispatchEvent.action.in_(actions))
        .order_by(DispatchEvent.created_at.desc())
        .first()
    )
