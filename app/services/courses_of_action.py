# -*- coding: utf-8 -*-
"""Courses-of-action comparison for disaster operations.

This service is intentionally non-mutating. It reads the current incident
state, dispatch previews, facilities, supply, and road sandbox, then returns
ranked alternatives an operator can compare before changing live state.
"""

from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime
from statistics import mean
from typing import Any

from sqlalchemy.orm import Session

from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import POINT_SUPPLY_TYPES, ResourcePoint
from app.services import dispatch, road_network


CRITICAL_CORRIDORS = [("chenggong", "yuli"), ("hualien", "taitung")]


def compare_courses(db: Session, limit: int = 6) -> dict[str, Any]:
    limit = max(1, min(limit, 20))
    open_needs = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.status == "open")
        .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at)
        .limit(120)
        .all()
    )
    assessments = [_assess_need(need, db) for need in open_needs]
    baseline = _baseline_metrics(db, assessments)

    courses = [
        c
        for c in [
            _surge_resources_course(baseline, assessments),
            _confirm_pending_course(db, baseline),
            _restore_roads_course(baseline),
            _open_overflow_course(baseline),
        ]
        if c
    ]
    if not courses:
        courses.append(_monitor_course(baseline))
    courses.sort(key=lambda c: (-c["rank_score"], c["id"]))

    return {
        "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        "baseline": baseline,
        "counts": {
            "courses": min(len(courses), limit),
            "open_requests": baseline["open_requests"],
            "urgent_uncovered": baseline["urgent_uncovered"],
            "road_disruptions": baseline["road_network"]["closed_edges"] + baseline["road_network"]["slow_edges"],
        },
        "courses": courses[:limit],
        "assumptions": [
            "Courses are non-mutating estimates; operators must explicitly execute actions.",
            "Candidate coverage uses the same dispatch preview/scoring logic as live dispatch.",
            "Resource surge assumes a compatible temporary resource can be staged at or near each uncovered request.",
            "Road restoration estimates the effect of returning sandbox-modified road segments to baseline travel cost.",
        ],
    }


def _assess_need(need: CommunityNeed, db: Session) -> dict[str, Any]:
    preview = dispatch.preview_candidates(str(need.id), db)
    candidates = preview.get("candidates") or []
    top = candidates[0] if candidates else None
    return {
        "need": need,
        "id": str(need.id),
        "type": need.need_type,
        "urgency": need.urgency,
        "has_candidate": top is not None,
        "top_candidate": top,
        "top_score": top.get("score") if top else None,
        "top_distance_km": top.get("dist_km") if top else None,
        "vulnerability": preview.get("vulnerability", 0.0),
    }


def _baseline_metrics(db: Session, assessments: list[dict[str, Any]]) -> dict[str, Any]:
    top_scores = [a["top_score"] for a in assessments if a["top_score"] is not None]
    top_distances = [a["top_distance_km"] for a in assessments if a["top_distance_km"] is not None]
    suggested = db.query(CommunityNeed).filter(CommunityNeed.status == "suggested").all()
    matched = db.query(CommunityNeed).filter(CommunityNeed.status == "matched").all()
    return {
        "open_requests": len(assessments),
        "urgent_open": sum(1 for a in assessments if a["urgency"] >= 4),
        "requests_with_candidate": sum(1 for a in assessments if a["has_candidate"]),
        "uncovered_open": sum(1 for a in assessments if not a["has_candidate"]),
        "urgent_uncovered": sum(1 for a in assessments if a["urgency"] >= 4 and not a["has_candidate"]),
        "suggested_requests": len(suggested),
        "matched_requests": len(matched),
        "avg_top_score": round(mean(top_scores), 1) if top_scores else None,
        "avg_top_distance_km": round(mean(top_distances), 2) if top_distances else None,
        "visible_supply_sources": _visible_supply_sources(db),
        "supply_gaps": _supply_gaps(db, assessments),
        "facility_pressure": _facility_pressure(db),
        "road_network": _road_metrics(db),
    }


def _surge_resources_course(
    baseline: dict[str, Any],
    assessments: list[dict[str, Any]],
) -> dict[str, Any] | None:
    uncovered = [a for a in assessments if not a["has_candidate"]]
    if not uncovered:
        return None
    urgent = [a for a in uncovered if a["urgency"] >= 4]
    projected_scores = [_local_resource_score(a) for a in uncovered]
    covered = len(uncovered)
    urgent_covered = len(urgent)
    return {
        "id": "surge_local_resources",
        "title": "Stage compatible surge resources near uncovered requests",
        "rank_score": 80 + urgent_covered * 16 + covered * 4,
        "summary": f"Create or mobilize {covered} temporary resource(s) so every currently uncovered open request has a feasible candidate.",
        "expected_delta": {
            "requests_with_candidate": covered,
            "uncovered_open": -covered,
            "urgent_uncovered": -urgent_covered,
            "avg_projected_local_score": round(mean(projected_scores), 1) if projected_scores else None,
        },
        "metrics_after": _with_delta(
            baseline,
            requests_with_candidate=covered,
            uncovered_open=-covered,
            urgent_uncovered=-urgent_covered,
        ),
        "evidence": {
            "affected_requests": [_obj("ResourceRequest", a["id"], f"{a['type']} urgency {a['urgency']}") for a in uncovered[:12]],
            "need_types": dict(Counter(a["type"] for a in uncovered)),
            "max_urgency": max(a["urgency"] for a in uncovered),
        },
        "tradeoffs": [
            "Requires outside volunteers, inventory, or field staging.",
            "May create duplicate supply if operators do not reconcile with in-flight manual dispatch.",
        ],
        "operator_actions": [
            {"action_id": "create_resource_or_facility", "endpoint": "/api/resources", "label": "Add compatible surge resources"},
            {"action_id": "manual_dispatch", "endpoint": "/api/resources/dispatch", "label": "Rerun dispatch after capacity is visible"},
        ],
    }


def _confirm_pending_course(db: Session, baseline: dict[str, Any]) -> dict[str, Any] | None:
    pending = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.status == "suggested")
        .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at)
        .limit(60)
        .all()
    )
    if not pending:
        return None
    urgent = [n for n in pending if n.urgency >= 4]
    return {
        "id": "confirm_pending_dispatch",
        "title": "Confirm or release pending dispatch suggestions",
        "rank_score": 68 + len(urgent) * 14 + len(pending) * 3,
        "summary": f"{len(pending)} suggested assignment(s) are waiting for a human decision.",
        "expected_delta": {
            "suggested_requests": -len(pending),
            "matched_requests": len(pending),
            "urgent_suggestions_cleared": len(urgent),
        },
        "metrics_after": _with_delta(
            baseline,
            suggested_requests=-len(pending),
            matched_requests=len(pending),
        ),
        "evidence": {
            "affected_requests": [_obj("ResourceRequest", n.id, f"{n.need_type} urgency {n.urgency}") for n in pending[:12]],
            "max_urgency": max(n.urgency for n in pending),
        },
        "tradeoffs": [
            "Confirming stale assignments can send responders into changed road or supply conditions.",
            "Declining releases capacity but can reopen urgent unmet demand.",
        ],
        "operator_actions": [
            {"action_id": "confirm_dispatch", "endpoint": "/api/resources/needs/{need_id}/confirm_dispatch", "label": "Confirm valid suggestions"},
            {"action_id": "decline_suggestion", "endpoint": "/api/resources/needs/{need_id}/decline_suggestion", "label": "Decline stale suggestions"},
        ],
    }


def _restore_roads_course(baseline: dict[str, Any]) -> dict[str, Any] | None:
    roads = baseline["road_network"]
    disrupted = roads["closed_edges"] + roads["slow_edges"]
    if disrupted == 0 and roads["degraded_corridors"] == 0 and roads["unreachable_corridors"] == 0:
        return None
    severe = roads["unreachable_corridors"] + roads["degraded_corridors"]
    return {
        "id": "restore_road_capacity",
        "title": "Restore or validate disrupted road capacity",
        "rank_score": 62 + roads["closed_edges"] * 12 + roads["slow_edges"] * 6 + severe * 15,
        "summary": f"{disrupted} edited road segment(s) and {severe} critical corridor issue(s) are affecting travel assumptions.",
        "expected_delta": {
            "closed_edges": -roads["closed_edges"],
            "slow_edges": -roads["slow_edges"],
            "degraded_corridors": -roads["degraded_corridors"],
            "unreachable_corridors": -roads["unreachable_corridors"],
        },
        "metrics_after": {
            **baseline,
            "road_network": {
                **roads,
                "closed_edges": 0,
                "slow_edges": 0,
                "degraded_corridors": 0,
                "unreachable_corridors": 0,
            },
        },
        "evidence": {
            "changed_edge_ids": roads["changed_edge_ids"][:20],
            "critical_corridors": roads["critical_corridors"],
        },
        "tradeoffs": [
            "Road edits must be confirmed against field reports before being trusted.",
            "Removing a closure too early can make dispatch scores optimistic.",
        ],
        "operator_actions": [
            {"action_id": "mutate_road_topology", "endpoint": "/admin#Road%20Sandbox", "label": "Inspect road sandbox"},
            {"action_id": "propose_dispatch", "endpoint": "/api/resources/dispatch", "label": "Rerun dispatch previews after road validation"},
        ],
    }


def _open_overflow_course(baseline: dict[str, Any]) -> dict[str, Any] | None:
    gaps = [g for g in baseline["supply_gaps"] if g["unmet"] > 0]
    pressure = baseline["facility_pressure"]
    overloaded = pressure["over_capacity"] + pressure["near_capacity"]
    if not gaps and overloaded == 0:
        return None
    unmet = sum(g["unmet"] for g in gaps)
    return {
        "id": "open_overflow_facility",
        "title": "Open overflow facility capacity for pressure points",
        "rank_score": 58 + unmet * 5 + pressure["over_capacity"] * 15 + pressure["near_capacity"] * 8,
        "summary": f"{len(gaps)} supply type gap(s) and {overloaded} pressured facility/facilities need added fixed capacity.",
        "expected_delta": {
            "supply_gap_types_reduced": len(gaps),
            "unmet_supply_sources": -unmet,
            "pressured_facilities_to_review": overloaded,
        },
        "metrics_after": {
            **baseline,
            "supply_gaps": [{**g, "unmet": 0} for g in baseline["supply_gaps"]],
        },
        "evidence": {
            "supply_gaps": gaps,
            "facility_pressure": pressure,
        },
        "tradeoffs": [
            "Fixed facilities improve throughput but may increase travel distance for urgent door-to-door requests.",
            "Opening overflow capacity requires staffing, phone handling, and load tracking.",
        ],
        "operator_actions": [
            {"action_id": "create_resource_or_facility", "endpoint": "/api/resources/points", "label": "Open or activate a resource point"},
            {"action_id": "redirect_to_alternate_facility", "endpoint": "/api/resources/points", "label": "Redirect new demand away from saturated sites"},
        ],
    }


def _monitor_course(baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "monitor_current_plan",
        "title": "Maintain current operating plan",
        "rank_score": 10,
        "summary": "No immediate alternative course of action is indicated by current requests, road topology, or facility pressure.",
        "expected_delta": {},
        "metrics_after": baseline,
        "evidence": {"baseline": baseline},
        "tradeoffs": ["Continue monitoring because new alerts and road edits can invalidate the current plan."],
        "operator_actions": [
            {"action_id": "inspect_operational_risks", "endpoint": "/api/ontology/reasoning/operational-risks", "label": "Refresh risk findings"}
        ],
    }


def _local_resource_score(assessment: dict[str, Any]) -> float:
    need = assessment["need"]
    affinity = max((a for _, a in dispatch._compat_types(need.need_type)), default=1.0)
    breakdown = dispatch._score_breakdown(
        need.urgency,
        affinity,
        0.0,
        0,
        float(assessment.get("vulnerability") or 0.0),
        dispatch._wait_pts(need),
    )
    return float((breakdown or {}).get("total", 0.0))


def _visible_supply_sources(db: Session) -> int:
    resources = db.query(CommunityResource).filter(CommunityResource.is_available == True).count()
    facilities = db.query(ResourcePoint).filter(ResourcePoint.is_active == True).count()
    return resources + facilities


def _supply_gaps(db: Session, assessments: list[dict[str, Any]]) -> list[dict[str, Any]]:
    by_type = Counter(a["type"] for a in assessments)
    gaps = []
    active_points = db.query(ResourcePoint).filter(ResourcePoint.is_active == True).all()
    for need_type, demand in sorted(by_type.items()):
        compat = [t for t, _ in dispatch._compat_types(need_type)]
        resource_count = (
            db.query(CommunityResource)
            .filter(CommunityResource.is_available == True, CommunityResource.resource_type.in_(compat))
            .count()
        )
        facility_count = sum(
            1 for point in active_points
            if any(t in compat for t in POINT_SUPPLY_TYPES.get(point.point_type, []))
        )
        visible = resource_count + facility_count
        if visible < demand:
            gaps.append({
                "need_type": need_type,
                "demand": demand,
                "visible_supply": visible,
                "unmet": demand - visible,
                "compatible_types": compat,
            })
    return gaps


def _facility_pressure(db: Session) -> dict[str, Any]:
    facilities = db.query(ResourcePoint).filter(ResourcePoint.is_active == True, ResourcePoint.capacity != None).all()
    pressured = []
    for facility in facilities:
        if not facility.capacity:
            continue
        ratio = (facility.current_load or 0) / facility.capacity
        if ratio >= 0.9:
            pressured.append({
                "id": str(facility.id),
                "name": facility.name,
                "ratio": round(ratio, 3),
                "current_load": facility.current_load,
                "capacity": facility.capacity,
            })
    return {
        "near_capacity": sum(1 for f in pressured if f["ratio"] < 1.0),
        "over_capacity": sum(1 for f in pressured if f["ratio"] >= 1.0),
        "facilities": pressured[:12],
    }


def _road_metrics(db: Session) -> dict[str, Any]:
    snapshot = road_network.sandbox_snapshot(db)
    changed = [e for e in snapshot["edges"] if e["status"] != "normal"]
    corridors = []
    for start, end in CRITICAL_CORRIDORS:
        base = road_network.road_distance_km(*road_network.NODES[start], *road_network.NODES[end])
        current = road_network.road_distance_km(*road_network.NODES[start], *road_network.NODES[end], db=db)
        if base is None:
            continue
        ratio = None if current is None else round(current / base, 3)
        state = "normal"
        if current is None:
            state = "unreachable"
        elif ratio and ratio > 1.15:
            state = "degraded"
        corridors.append({
            "start": start,
            "end": end,
            "baseline_km": round(base, 3),
            "current_km": None if current is None else round(current, 3),
            "ratio": ratio,
            "state": state,
        })
    return {
        "closed_edges": snapshot["metrics"]["closed_edges"],
        "slow_edges": snapshot["metrics"]["slow_edges"],
        "changed_edge_ids": [e["id"] for e in changed],
        "critical_corridors": corridors,
        "degraded_corridors": sum(1 for c in corridors if c["state"] == "degraded"),
        "unreachable_corridors": sum(1 for c in corridors if c["state"] == "unreachable"),
    }


def _with_delta(baseline: dict[str, Any], **delta: int) -> dict[str, Any]:
    after = dict(baseline)
    for key, change in delta.items():
        value = after.get(key)
        if isinstance(value, int):
            after[key] = max(0, value + change)
    return after


def _obj(object_type: str, raw_id: Any, label: str | None = None) -> dict[str, Any]:
    return {"type": object_type, "id": str(raw_id), "label": label or str(raw_id)}
