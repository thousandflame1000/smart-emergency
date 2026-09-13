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
    assessments = _open_assessments(db)
    baseline = _baseline_metrics(db, assessments)
    courses = _ranked_courses(db, baseline, assessments)

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


def dry_run_course(db: Session, course_id: str) -> dict[str, Any]:
    assessments = _open_assessments(db)
    baseline = _baseline_metrics(db, assessments)
    courses = _ranked_courses(db, baseline, assessments)
    course = next((c for c in courses if c["id"] == course_id), None)
    if not course:
        return {
            "error": "unknown_or_inactive_course",
            "course_id": course_id,
            "available_courses": [c["id"] for c in courses],
        }

    if course_id == "surge_local_resources":
        return _dry_run_surge_resources(course, baseline, assessments)
    if course_id == "restore_road_capacity":
        return _dry_run_restore_roads(course, baseline, assessments, db)
    if course_id == "confirm_pending_dispatch":
        return _dry_run_confirm_pending(course, baseline, db)
    if course_id == "open_overflow_facility":
        return _dry_run_open_overflow(course, baseline)
    return _dry_run_monitor(course, baseline)


def _open_assessments(db: Session) -> list[dict[str, Any]]:
    open_needs = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.status == "open")
        .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at)
        .limit(120)
        .all()
    )
    return [_assess_need(need, db) for need in open_needs]


def _ranked_courses(
    db: Session,
    baseline: dict[str, Any],
    assessments: list[dict[str, Any]],
) -> list[dict[str, Any]]:
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
    return courses


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
        "title": "在缺乏支援的需求附近增援物資",
        "rank_score": 80 + urgent_covered * 16 + covered * 4,
        "summary": f"新增或調度 {covered} 個臨時資源，為目前無支援的需求提供候選資源。",
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
            "affected_requests": [_obj("ResourceRequest", a["id"], f"{a['type']} 緊急程度 {a['urgency']}") for a in uncovered[:12]],
            "need_types": dict(Counter(a["type"] for a in uncovered)),
            "max_urgency": max(a["urgency"] for a in uncovered),
        },
        "tradeoffs": [
            "需要外部志工、庫存或現場集結支援。",
            "須核對正在執行的人工派遣，避免重複調度。",
        ],
        "operator_actions": [
            {"action_id": "create_resource_or_facility", "endpoint": "/api/resources", "label": "新增相容的增援資源"},
            {"action_id": "manual_dispatch", "endpoint": "/api/resources/dispatch", "label": "資源到位後重新試算派遣"},
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
        "title": "確認或釋放待處理派遣建議",
        "rank_score": 68 + len(urgent) * 14 + len(pending) * 3,
        "summary": f"有 {len(pending)} 筆派遣建議等待人工決定。",
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
            "affected_requests": [_obj("ResourceRequest", n.id, f"{n.need_type} 緊急程度 {n.urgency}") for n in pending[:12]],
            "max_urgency": max(n.urgency for n in pending),
        },
        "tradeoffs": [
            "確認失效建議可能使救援人員面對已變更的道路或供應條件。",
            "拒絕建議會釋放資源，但緊急需求可能重新等待支援。",
        ],
        "operator_actions": [
            {"action_id": "confirm_dispatch", "endpoint": "/api/resources/needs/{need_id}/confirm_dispatch", "label": "確認有效建議"},
            {"action_id": "decline_suggestion", "endpoint": "/api/resources/needs/{need_id}/decline_suggestion", "label": "拒絕失效建議"},
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
        "title": "恢復或查核受阻道路",
        "rank_score": 62 + roads["closed_edges"] * 12 + roads["slow_edges"] * 6 + severe * 15,
        "summary": f"有 {disrupted} 段變更道路與 {severe} 項重要走廊問題影響通行假設。",
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
            "道路變更須依現場回報查核。",
            "過早解除封路可能高估派遣可行性。",
        ],
        "operator_actions": [
            {"action_id": "mutate_road_topology", "endpoint": "/admin#Road%20Sandbox", "label": "檢查路網沙盒"},
            {"action_id": "propose_dispatch", "endpoint": "/api/resources/dispatch", "label": "查核道路後重新試算派遣"},
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
        "title": "為高負載地區增開備援設施",
        "rank_score": 58 + unmet * 5 + pressure["over_capacity"] * 15 + pressure["near_capacity"] * 8,
        "summary": f"有 {len(gaps)} 類物資缺口與 {overloaded} 處高負載設施需要增加容量。",
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
            "固定設施能增加服務量，但可能增加緊急到府需求的通行距離。",
            "備援設施需要人員、通訊與負載追蹤配合。",
        ],
        "operator_actions": [
            {"action_id": "create_resource_or_facility", "endpoint": "/api/resources/points", "label": "新增或啟用資源點"},
            {"action_id": "redirect_to_alternate_facility", "endpoint": "/api/resources/points", "label": "將新需求轉往未滿載設施"},
        ],
    }


def _monitor_course(baseline: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": "monitor_current_plan",
        "title": "維持目前應變方案",
        "rank_score": 10,
        "summary": "依目前需求、路網與設施負載，尚無需立即改採其他方案。",
        "expected_delta": {},
        "metrics_after": baseline,
        "evidence": {"baseline": baseline},
        "tradeoffs": ["持續監測新增警報與道路變更，必要時調整目前方案。"],
        "operator_actions": [
            {"action_id": "inspect_operational_risks", "endpoint": "/api/ontology/reasoning/operational-risks", "label": "更新風險分析"}
        ],
    }


def _dry_run_surge_resources(
    course: dict[str, Any],
    baseline: dict[str, Any],
    assessments: list[dict[str, Any]],
) -> dict[str, Any]:
    uncovered = [a for a in assessments if not a["has_candidate"]]
    request_changes = []
    for assessment in uncovered:
        score = _local_resource_score(assessment)
        request_changes.append({
            "request_id": assessment["id"],
            "need_type": assessment["type"],
            "urgency": assessment["urgency"],
            "before": {"has_candidate": False, "top_score": None, "top_distance_km": None},
            "after": {
                "has_candidate": True,
                "top_score": round(score, 1),
                "top_distance_km": 0.0,
                "source": "virtual_resource",
            },
            "delta": {"candidate_added": True, "score_delta": None},
        })
    return _dry_run_report(
        course,
        baseline,
        course["metrics_after"],
        request_changes,
        virtual_objects=[
            {
                "type": "Resource",
                "label": f"模擬 {a['type']} 增援資源",
                "near_request_id": a["id"],
                "projected_score": round(_local_resource_score(a), 1),
            }
            for a in uncovered
        ],
        confidence="medium",
    )


def _dry_run_restore_roads(
    course: dict[str, Any],
    baseline: dict[str, Any],
    assessments: list[dict[str, Any]],
    db: Session,
) -> dict[str, Any]:
    request_changes = []
    added = urgent_added = 0
    score_deltas = []
    for assessment in assessments:
        restored = _best_candidate_for_need(assessment["need"], db, use_sandbox=False)
        current_score = assessment["top_score"]
        if not restored:
            continue
        restored_score = restored["score"]
        score_delta = None if current_score is None else round(restored_score - current_score, 1)
        candidate_added = not assessment["has_candidate"]
        improved = candidate_added or (score_delta is not None and score_delta > 0.1)
        if not improved:
            continue
        if candidate_added:
            added += 1
            if assessment["urgency"] >= 4:
                urgent_added += 1
        if score_delta is not None:
            score_deltas.append(score_delta)
        request_changes.append({
            "request_id": assessment["id"],
            "need_type": assessment["type"],
            "urgency": assessment["urgency"],
            "before": {
                "has_candidate": assessment["has_candidate"],
                "top_score": current_score,
                "top_distance_km": assessment["top_distance_km"],
            },
            "after": restored,
            "delta": {
                "candidate_added": candidate_added,
                "score_delta": score_delta,
                "distance_delta_km": _distance_delta(assessment["top_distance_km"], restored["dist_km"]),
            },
        })

    simulated_after = _with_delta(
        course["metrics_after"],
        requests_with_candidate=added,
        uncovered_open=-added,
        urgent_uncovered=-urgent_added,
    )
    return _dry_run_report(
        course,
        baseline,
        simulated_after,
        request_changes,
        route_changes=baseline["road_network"]["critical_corridors"],
        confidence="medium" if request_changes else "low",
        extra_impact={
            "avg_score_delta": round(mean(score_deltas), 1) if score_deltas else None,
            "newly_covered_requests": added,
        },
    )


def _dry_run_confirm_pending(course: dict[str, Any], baseline: dict[str, Any], db: Session) -> dict[str, Any]:
    pending = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.status == "suggested")
        .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at)
        .limit(60)
        .all()
    )
    request_changes = [
        {
            "request_id": str(need.id),
            "need_type": need.need_type,
            "urgency": need.urgency,
            "before": {"status": "suggested", "matched_resource_id": str(need.matched_resource_id) if need.matched_resource_id else None},
            "after": {"status": "matched", "matched_resource_id": str(need.matched_resource_id) if need.matched_resource_id else None},
            "delta": {"status_change": "suggested -> matched"},
        }
        for need in pending
    ]
    return _dry_run_report(course, baseline, course["metrics_after"], request_changes, confidence="high")


def _dry_run_open_overflow(course: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    gaps = course.get("evidence", {}).get("supply_gaps") or []
    virtual_objects = [
        {
            "type": "Facility",
            "label": f"備援 {gap['need_type']} 容量",
            "need_type": gap["need_type"],
            "projected_supply_sources": gap["unmet"],
        }
        for gap in gaps
    ]
    return _dry_run_report(
        course,
        baseline,
        course["metrics_after"],
        [],
        virtual_objects=virtual_objects,
        confidence="medium",
    )


def _dry_run_monitor(course: dict[str, Any], baseline: dict[str, Any]) -> dict[str, Any]:
    return _dry_run_report(course, baseline, baseline, [], confidence="high")


def _dry_run_report(
    course: dict[str, Any],
    baseline: dict[str, Any],
    simulated_after: dict[str, Any],
    request_changes: list[dict[str, Any]],
    *,
    route_changes: list[dict[str, Any]] | None = None,
    virtual_objects: list[dict[str, Any]] | None = None,
    confidence: str,
    extra_impact: dict[str, Any] | None = None,
) -> dict[str, Any]:
    impact = {
        "requests_with_candidate_delta": _metric_delta(baseline, simulated_after, "requests_with_candidate"),
        "uncovered_open_delta": _metric_delta(baseline, simulated_after, "uncovered_open"),
        "urgent_uncovered_delta": _metric_delta(baseline, simulated_after, "urgent_uncovered"),
        "suggested_requests_delta": _metric_delta(baseline, simulated_after, "suggested_requests"),
        "matched_requests_delta": _metric_delta(baseline, simulated_after, "matched_requests"),
        "changed_requests": len(request_changes),
        "confidence": confidence,
    }
    if extra_impact:
        impact.update(extra_impact)
    return {
        "generated_at": datetime.now(UTC).replace(tzinfo=None).isoformat() + "Z",
        "course_id": course["id"],
        "course": course,
        "baseline": baseline,
        "simulated_after": simulated_after,
        "impact": impact,
        "request_changes": request_changes[:30],
        "route_changes": route_changes or [],
        "virtual_objects": virtual_objects or [],
        "non_mutating": True,
        "assumptions": [
            "Dry-run does not create, update, reserve, or delete database records.",
            "Dispatch scoring reuses the production urgency, distance, vulnerability, and wait-time formula.",
            "Operators must explicitly execute one of the returned actions to change live state.",
        ],
    }


def _best_candidate_for_need(need: CommunityNeed, db: Session, *, use_sandbox: bool) -> dict[str, Any] | None:
    vulnerability = dispatch._vulnerability_pts(need.requester_id, db)
    wait_pts = dispatch._wait_pts(need)
    compat = dispatch._compat_types(need.need_type)
    type_aff = {t: a for t, a in compat}
    candidates: list[dict[str, Any]] = []

    for resource in (
        db.query(CommunityResource)
        .filter(CommunityResource.is_available == True, CommunityResource.resource_type.in_(list(type_aff.keys())))
        .all()
    ):
        dist = _distance_for_mode(need.lat, need.lng, resource.lat, resource.lng, db, use_sandbox)
        breakdown = dispatch._score_breakdown(
            need.urgency,
            type_aff.get(resource.resource_type, 0.0),
            dist,
            0,
            vulnerability,
            wait_pts,
        )
        if breakdown is None:
            continue
        candidates.append({
            "has_candidate": True,
            "source": "resource",
            "id": str(resource.id),
            "name": resource.name,
            "score": round(breakdown["total"], 1),
            "dist_km": round(dist, 2),
        })

    for point in db.query(ResourcePoint).filter(ResourcePoint.is_active == True).all():
        pt_supplies = POINT_SUPPLY_TYPES.get(point.point_type, [])
        best_aff = max((type_aff[t] for t in pt_supplies if t in type_aff), default=0.0)
        if best_aff == 0.0:
            continue
        dist = _distance_for_mode(need.lat, need.lng, point.lat, point.lng, db, use_sandbox)
        breakdown = dispatch._score_breakdown(need.urgency, best_aff, dist, 0, vulnerability, wait_pts)
        if breakdown is None:
            continue
        candidates.append({
            "has_candidate": True,
            "source": "resource_point",
            "id": str(point.id),
            "name": point.name,
            "score": round(breakdown["total"] * 0.8, 1),
            "dist_km": round(dist, 2),
        })

    if not candidates:
        return None
    candidates.sort(key=lambda c: c["score"], reverse=True)
    return candidates[0]


def _distance_for_mode(lat1, lng1, lat2, lng2, db: Session, use_sandbox: bool) -> float:
    return dispatch._distance_km(lat1, lng1, lat2, lng2, db if use_sandbox else None)


def _distance_delta(before: float | None, after: float | None) -> float | None:
    if before is None or after is None:
        return None
    return round(after - before, 2)


def _metric_delta(before: dict[str, Any], after: dict[str, Any], key: str) -> int | None:
    b = before.get(key)
    a = after.get(key)
    if isinstance(b, int) and isinstance(a, int):
        return a - b
    return None


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
