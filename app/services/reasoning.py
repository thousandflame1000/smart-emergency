# -*- coding: utf-8 -*-
"""Operational reasoning over the emergency ontology.

This layer turns the ontology from a passive graph into an active watch
floor: it scans requests, resources, alerts, facilities, and road
topology edits, then emits evidence-backed findings with recommended
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
from app.services import dispatch, road_network


SEVERITY_ORDER = {"critical": 0, "high": 1, "medium": 2, "low": 3}
ACTIVE_NEED_STATUSES = {"open", "suggested", "matched"}


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

    _road_risks(db, findings)
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


def _road_risks(db: Session, findings: list[dict[str, Any]]) -> None:
    snapshot = road_network.sandbox_snapshot(db)
    changed_edges = [e for e in snapshot["edges"] if e["status"] != "normal"]
    if changed_edges:
        closed = [e for e in changed_edges if e["status"] == "closed"]
        slow = [e for e in changed_edges if e["status"] == "slow"]
        severity = "high" if closed else "medium"
        _finding(
            findings,
            fid="road_topology_has_disruptions",
            severity=severity,
            category="road_topology",
            title="Road topology sandbox contains active disruptions",
            summary=f"{len(closed)} closed and {len(slow)} slowed road segments are affecting routing.",
            affected_objects=[_obj("RoadEdge", e["id"], f"{e['a']} -> {e['b']}") for e in changed_edges[:10]],
            evidence={
                "closed_edges": len(closed),
                "slow_edges": len(slow),
                "changed_edge_ids": [e["id"] for e in changed_edges],
            },
            recommended_action={
                "action_id": "mutate_road_topology",
                "label": "Inspect road sandbox and rerun dispatch",
                "endpoint": "/admin#Road%20Sandbox",
                "reason": "Road edits change candidate distances and can invalidate previous assignments.",
            },
        )

    for start, end in [("chenggong", "yuli"), ("hualien", "taitung")]:
        base = road_network.road_distance_km(*road_network.NODES[start], *road_network.NODES[end])
        current = road_network.road_distance_km(*road_network.NODES[start], *road_network.NODES[end], db=db)
        if base is None:
            continue
        if current is None:
            _finding(
                findings,
                fid=f"road_route_unreachable:{start}:{end}",
                severity="critical",
                category="road_topology",
                title="Critical corridor route is unreachable",
                summary=f"{start} to {end} has no reachable road path under the current sandbox.",
                affected_objects=[_obj("RoadNode", start), _obj("RoadNode", end)],
                evidence={"baseline_km": round(base, 3), "current_km": None},
                recommended_action={
                    "action_id": "mutate_road_topology",
                    "label": "Restore or add an alternate road edge",
                    "endpoint": "/api/road-network/sandbox",
                    "reason": "The dispatch engine cannot score road distance across a disconnected corridor.",
                },
            )
        elif current > base * 1.5:
            _finding(
                findings,
                fid=f"road_route_degraded:{start}:{end}",
                severity="high",
                category="road_topology",
                title="Critical corridor route is materially degraded",
                summary=f"{start} to {end} is {current / base:.1f}x longer than baseline.",
                affected_objects=[_obj("RoadNode", start), _obj("RoadNode", end)],
                evidence={"baseline_km": round(base, 3), "current_km": round(current, 3)},
                recommended_action={
                    "action_id": "mutate_road_topology",
                    "label": "Review road closure assumptions",
                    "endpoint": "/api/road-network/route",
                    "reason": "Long detours can change the best resource assignment.",
                },
            )


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
        label = f"{need.need_type} urgency {need.urgency}"
        if need.status == "open":
            candidates = dispatch.preview_candidates(str(need.id), db).get("candidates", [])
            top = candidates[0] if candidates else None
            if not top and need.urgency >= 4:
                _finding(
                    findings,
                    fid=f"open_need_no_candidate:{need.id}",
                    severity="critical" if need.urgency >= 5 else "high",
                    category="dispatch",
                    title="Urgent open request has no feasible candidate",
                    summary=f"{label} cannot currently be matched to an available resource or facility.",
                    affected_objects=[_obj("ResourceRequest", need.id, label)],
                    evidence={"urgency": need.urgency, "age_minutes": age, "candidate_count": 0},
                    recommended_action={
                        "action_id": "manual_dispatch",
                        "label": "Create or connect an emergency resource",
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
                    title="Open request is waiting for dispatch",
                    summary=f"{label} is still open with a top candidate available.",
                    affected_objects=[_obj("ResourceRequest", need.id, label)],
                    evidence={
                        "urgency": need.urgency,
                        "age_minutes": age,
                        "top_candidate": top,
                    },
                    recommended_action={
                        "action_id": "manual_dispatch",
                        "label": "Dispatch the top candidate",
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
                    title="Dispatch suggestion is waiting for human confirmation",
                    summary=f"{label} has been suggested for {event_age} minutes.",
                    affected_objects=[_obj("ResourceRequest", need.id, label)],
                    evidence={"age_minutes": event_age, "matched_resource_id": str(need.matched_resource_id)},
                    recommended_action={
                        "action_id": "confirm_dispatch",
                        "label": "Confirm or decline the suggestion",
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
                    title="Matched request has no completion confirmation",
                    summary=f"{label} has been matched for {event_age} minutes without delivery confirmation.",
                    affected_objects=[_obj("ResourceRequest", need.id, label)],
                    evidence={"age_minutes": event_age, "matched_resource_id": str(need.matched_resource_id)},
                    recommended_action={
                        "action_id": "task_delivered",
                        "label": "Contact volunteer or reassign",
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
            title="Active alert has no linked resource request",
            summary=f"{alert.alert_type} alert is active, but no open dispatch request exists for this person.",
            affected_objects=[
                _obj("Alert", alert.id, alert.alert_type),
                _obj("Person", alert.elderly_id, alert.elderly.name if alert.elderly else None),
            ],
            evidence={"alert_type": alert.alert_type, "alert_status": alert.status},
            recommended_action={
                "action_id": "manual_dispatch",
                "label": "Create a resource request or resolve alert",
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
            title="Resource is unavailable without an active or completed task",
            summary=f"{resource.name} is locked, but no matching request explains the lock.",
            affected_objects=[_obj("Resource", resource.id, resource.name)],
            evidence={"resource_type": resource.resource_type, "is_available": resource.is_available},
            recommended_action={
                "action_id": "mutate_resource_state",
                "label": "Review and release resource if appropriate",
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
            title="Open request demand exceeds visible supply",
            summary=f"{need_count} open {need_type} requests compete for {supply_count} visible supply sources.",
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
                "label": "Add supply, open a facility, or request external support",
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
            title="Facility is near or over capacity",
            summary=f"{facility.name} is at {facility.current_load}/{facility.capacity} capacity.",
            affected_objects=[_obj("Facility", facility.id, facility.name)],
            evidence={"current_load": facility.current_load, "capacity": facility.capacity, "ratio": round(ratio, 3)},
            recommended_action={
                "action_id": "redirect_to_alternate_facility",
                "label": "Redirect new requests or open overflow capacity",
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
