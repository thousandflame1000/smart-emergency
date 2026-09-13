# -*- coding: utf-8 -*-
"""
Lightweight operational ontology layer.

This is not a Palantir Foundry clone. It is a small, explicit semantic
layer over the existing SQLAlchemy models: object types, link types,
actions, functions, and an operational graph that the UI/API can inspect.
"""
from __future__ import annotations

import json
from datetime import date, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint, POINT_SUPPLY_TYPES
from app.models.user import User
from app.services import dispatch, road_network


OBJECT_TYPES: dict[str, dict[str, Any]] = {
    "Person": {
        "source_table": "users",
        "description": "Residents, volunteers, family contacts, and admins.",
        "key_fields": ["id"],
        "display_fields": ["name", "roles", "phone", "address", "lat", "lng", "is_active"],
        "semantic_roles": {
            "Victim": "roles contains elderly",
            "Responder": "roles contains volunteer or admin",
            "CareContact": "roles contains family",
        },
    },
    "ResourceRequest": {
        "source_table": "community_needs",
        "description": "A request for help or supplies created by a person.",
        "key_fields": ["id"],
        "display_fields": ["need_type", "urgency", "status", "quantity", "address", "created_at"],
    },
    "Resource": {
        "source_table": "community_resources",
        "description": "A volunteer-owned resource that can be assigned to a request.",
        "key_fields": ["id"],
        "display_fields": ["resource_type", "name", "quantity", "is_available", "lat", "lng"],
    },
    "Facility": {
        "source_table": "resource_points",
        "description": "Fixed community infrastructure such as shelters, hospitals, fire stations, and warehouses.",
        "key_fields": ["id"],
        "display_fields": ["name", "point_type", "capacity", "current_load", "source", "is_active"],
    },
    "Alert": {
        "source_table": "alerts",
        "description": "Operational warning created from failed check-ins or other risk signals.",
        "key_fields": ["id"],
        "display_fields": ["alert_type", "status", "created_at", "resolved_at"],
    },
    "CheckIn": {
        "source_table": "daily_checkins",
        "description": "Daily safety response from an elderly resident.",
        "key_fields": ["id"],
        "display_fields": ["date", "status", "note", "responded_at", "confirmed_at"],
    },
    "DecisionEvent": {
        "source_table": "dispatch_events",
        "description": "Append-only audit trail for dispatch actions and algorithmic suggestions.",
        "key_fields": ["id"],
        "display_fields": ["action", "outcome", "actor_label", "previous_status", "new_status", "created_at"],
    },
    "RoadNode": {
        "source_table": "system_config.road_network_sandbox + app.services.road_network.NODES",
        "description": "Mutable road topology waypoint used by routing and dispatch what-if analysis.",
        "key_fields": ["id"],
        "display_fields": ["label", "lat", "lng", "custom"],
    },
    "RoadEdge": {
        "source_table": "system_config.road_network_sandbox + app.services.road_network._EDGES",
        "description": "Mutable road segment with normal, slow, or closed operating state.",
        "key_fields": ["id"],
        "display_fields": ["a", "b", "status", "distance_km", "effective_distance_km", "custom"],
    },
}


LINK_TYPES: list[dict[str, Any]] = [
    {
        "id": "REQUESTED_BY",
        "from": "ResourceRequest",
        "to": "Person",
        "source": "community_needs.requester_id -> users.id",
        "meaning": "Who asked for help.",
    },
    {
        "id": "OWNED_BY",
        "from": "Resource",
        "to": "Person",
        "source": "community_resources.owner_id -> users.id",
        "meaning": "Who can provide or transport a resource.",
    },
    {
        "id": "MATCHED_TO",
        "from": "ResourceRequest",
        "to": "Resource",
        "source": "community_needs.matched_resource_id -> community_resources.id",
        "meaning": "The current dispatch suggestion or confirmed match.",
    },
    {
        "id": "HAS_ALERT",
        "from": "Person",
        "to": "Alert",
        "source": "alerts.elderly_id -> users.id",
        "meaning": "A risk signal attached to a person.",
    },
    {
        "id": "HAS_CHECKIN",
        "from": "Person",
        "to": "CheckIn",
        "source": "daily_checkins.elderly_id -> users.id",
        "meaning": "A safety status observation for a person.",
    },
    {
        "id": "CARE_CONTACT",
        "from": "Person",
        "to": "Person",
        "source": "care_relations.elderly_id/contact_id -> users.id",
        "meaning": "A notification or support relationship.",
    },
    {
        "id": "CAN_SUPPLY",
        "from": "Facility",
        "to": "ResourceRequest",
        "source": "resource_points.point_type -> POINT_SUPPLY_TYPES -> community_needs.need_type",
        "meaning": "A fixed facility can potentially satisfy this request type.",
        "computed": True,
    },
    {
        "id": "ACTION_ON",
        "from": "DecisionEvent",
        "to": "ResourceRequest",
        "source": "dispatch_events.need_id -> community_needs.id",
        "meaning": "Which request a dispatch action changed or evaluated.",
    },
    {
        "id": "ACTION_RESOURCE",
        "from": "DecisionEvent",
        "to": "Resource",
        "source": "dispatch_events.resource_id -> community_resources.id",
        "meaning": "Which resource a dispatch action reserved, released, or confirmed.",
    },
    {
        "id": "ACTION_ACTOR",
        "from": "DecisionEvent",
        "to": "Person",
        "source": "dispatch_events.actor_id -> users.id",
        "meaning": "Which user performed the action when known.",
    },
    {
        "id": "ROAD_CONNECTS",
        "from": "RoadEdge",
        "to": "RoadNode",
        "source": "road_network edge endpoints",
        "meaning": "Which topology nodes a road segment connects.",
        "computed": True,
    },
]


ACTIONS: list[dict[str, Any]] = [
    {
        "id": "propose_dispatch",
        "label": "Propose dispatch",
        "method": "POST",
        "endpoint": "/api/resources/dispatch",
        "preconditions": ["System mode is emergency", "At least one open ResourceRequest exists"],
        "effects": ["Sets feasible volunteer-owned matches to suggested", "Reserves matched resources"],
        "human_in_the_loop": True,
    },
    {
        "id": "confirm_dispatch",
        "label": "Confirm dispatch suggestion",
        "method": "POST",
        "endpoint": "/api/resources/needs/{need_id}/confirm_dispatch",
        "preconditions": ["ResourceRequest.status == suggested"],
        "effects": ["Sets request to matched", "Sends LINE task message when possible"],
        "human_in_the_loop": True,
    },
    {
        "id": "decline_suggestion",
        "label": "Decline dispatch suggestion",
        "method": "POST",
        "endpoint": "/api/resources/needs/{need_id}/decline_suggestion",
        "preconditions": ["ResourceRequest.status == suggested"],
        "effects": ["Reopens request", "Releases reserved resource"],
        "human_in_the_loop": True,
    },
    {
        "id": "cancel_need",
        "label": "Cancel resource request",
        "method": "PUT",
        "endpoint": "/api/resources/needs/{need_id}?status=cancelled",
        "preconditions": ["ResourceRequest exists"],
        "effects": ["Sets request to cancelled", "Releases active matched resource when present"],
        "human_in_the_loop": True,
    },
    {
        "id": "manual_dispatch",
        "label": "Manual dispatch",
        "method": "POST",
        "endpoint": "/api/resources/needs/{need_id}/match?resource_id={resource_id}",
        "preconditions": ["Resource exists", "ResourceRequest exists"],
        "effects": ["Sets request to matched", "Marks resource unavailable", "Notifies volunteer when possible"],
        "human_in_the_loop": True,
    },
    {
        "id": "task_delivered",
        "label": "Volunteer marks task delivered",
        "method": "LINE_POSTBACK",
        "endpoint": "action=task_delivered&need_id={need_id}",
        "preconditions": ["Volunteer received a task message"],
        "effects": ["Sets request to fulfilled", "Records volunteer completion event"],
        "human_in_the_loop": True,
    },
    {
        "id": "task_decline",
        "label": "Volunteer declines task",
        "method": "LINE_POSTBACK",
        "endpoint": "action=task_decline&need_id={need_id}",
        "preconditions": ["Volunteer received a task message"],
        "effects": ["Reopens request", "Releases matched resource", "Records volunteer decline event"],
        "human_in_the_loop": True,
    },
    {
        "id": "resolve_alerts",
        "label": "Resolve person alerts",
        "method": "POST",
        "endpoint": "/api/dashboard/users/{user_id}/resolve_alerts",
        "preconditions": ["Person has active alerts"],
        "effects": ["Marks sent alerts as resolved"],
        "human_in_the_loop": True,
    },
    {
        "id": "mutate_road_topology",
        "label": "Mutate road topology sandbox",
        "method": "PUT/POST/DELETE",
        "endpoint": "/api/road-network/sandbox",
        "preconditions": ["Operator is running a what-if or disaster-routing scenario"],
        "effects": ["Changes road distance calculations", "Updates dispatch candidate scoring"],
        "human_in_the_loop": True,
    },
    {
        "id": "inspect_operational_risks",
        "label": "Inspect operational risk findings",
        "method": "GET",
        "endpoint": "/api/ontology/reasoning/operational-risks",
        "preconditions": ["Operational data exists"],
        "effects": ["Returns evidence-backed risks and recommended actions"],
        "human_in_the_loop": True,
    },
]


FUNCTIONS: list[dict[str, Any]] = [
    {
        "id": "dispatch_score",
        "label": "Dispatch scoring function",
        "implementation": "app.services.dispatch._score_breakdown",
        "inputs": ["urgency", "resource affinity", "distance", "volunteer load", "vulnerability", "waiting time"],
        "outputs": ["total score", "explainable score breakdown"],
    },
    {
        "id": "batch_assignment",
        "label": "Batch resource assignment",
        "implementation": "app.services.dispatch._assign_resources_optimally",
        "algorithm": "Hungarian minimum-cost assignment over negative dispatch scores",
        "outputs": ["one-to-one request/resource suggestions"],
    },
    {
        "id": "vulnerability_score",
        "label": "Person vulnerability score",
        "implementation": "app.services.dispatch._vulnerability_pts",
        "inputs": ["recent risky check-ins", "active alerts", "care relation count"],
        "outputs": ["equity-weighted priority points"],
    },
    {
        "id": "road_distance",
        "label": "Road-network distance",
        "implementation": "app.services.road_network.road_distance_km",
        "inputs": ["origin lat/lng", "destination lat/lng", "optional road sandbox overlay"],
        "outputs": ["Hua-Dong corridor road distance, sandbox-aware blocked route, or haversine fallback"],
    },
    {
        "id": "road_topology_sandbox",
        "label": "Road topology what-if sandbox",
        "implementation": "app.services.road_network.sandbox_snapshot",
        "inputs": ["node edits", "edge closures", "edge slowdown multipliers"],
        "outputs": ["mutable road graph used by dispatch scoring"],
    },
    {
        "id": "operational_reasoning",
        "label": "Operational risk reasoning engine",
        "implementation": "app.services.reasoning.operational_risks",
        "inputs": ["requests", "resources", "alerts", "facilities", "road topology sandbox", "dispatch events"],
        "outputs": ["ranked findings", "evidence", "affected ontology objects", "recommended actions"],
    },
]


TYPE_ALIASES = {
    "person": "Person",
    "user": "Person",
    "resourcerequest": "ResourceRequest",
    "request": "ResourceRequest",
    "need": "ResourceRequest",
    "resource": "Resource",
    "facility": "Facility",
    "resourcepoint": "Facility",
    "alert": "Alert",
    "checkin": "CheckIn",
    "decisionevent": "DecisionEvent",
    "dispatch_event": "DecisionEvent",
    "dispatchevent": "DecisionEvent",
    "event": "DecisionEvent",
    "roadnode": "RoadNode",
    "road_node": "RoadNode",
    "roadedge": "RoadEdge",
    "road_edge": "RoadEdge",
}


def schema() -> dict[str, Any]:
    return {
        "name": "Smart Emergency Operational Ontology",
        "style": "Palantir-inspired semantic layer over existing relational tables",
        "object_types": OBJECT_TYPES,
        "link_types": LINK_TYPES,
        "actions": ACTIONS,
        "functions": FUNCTIONS,
    }


def graph(db: Session, limit: int = 80) -> dict[str, Any]:
    limit = max(1, min(limit, 250))
    users = db.query(User).order_by(User.created_at.desc()).limit(limit).all()
    needs = db.query(CommunityNeed).order_by(CommunityNeed.created_at.desc()).limit(limit).all()
    resources = db.query(CommunityResource).order_by(CommunityResource.created_at.desc()).limit(limit).all()
    facilities = db.query(ResourcePoint).order_by(ResourcePoint.point_type, ResourcePoint.name).limit(limit).all()
    alerts = db.query(Alert).order_by(Alert.created_at.desc()).limit(limit).all()
    checkins = db.query(DailyCheckin).order_by(DailyCheckin.date.desc()).limit(limit).all()
    relations = db.query(CareRelation).filter(CareRelation.is_active == True).limit(limit).all()
    events = db.query(DispatchEvent).order_by(DispatchEvent.created_at.desc()).limit(limit).all()
    road = road_network.sandbox_snapshot(db)

    nodes = []
    edges = []

    for user in users:
        nodes.append(_person_node(user))
    for need in needs:
        nodes.append(_need_node(need))
        edges.append(_edge("REQUESTED_BY", "ResourceRequest", need.id, "Person", need.requester_id))
        if need.matched_resource_id:
            edges.append(_edge("MATCHED_TO", "ResourceRequest", need.id, "Resource", need.matched_resource_id))
    for resource in resources:
        nodes.append(_resource_node(resource))
        edges.append(_edge("OWNED_BY", "Resource", resource.id, "Person", resource.owner_id))
    for facility in facilities:
        nodes.append(_facility_node(facility))
    for alert in alerts:
        nodes.append(_alert_node(alert))
        edges.append(_edge("HAS_ALERT", "Person", alert.elderly_id, "Alert", alert.id))
    for checkin in checkins:
        nodes.append(_checkin_node(checkin))
        edges.append(_edge("HAS_CHECKIN", "Person", checkin.elderly_id, "CheckIn", checkin.id))
    for relation in relations:
        edges.append({
            **_edge("CARE_CONTACT", "Person", relation.elderly_id, "Person", relation.contact_id),
            "properties": {
                "relation": relation.relation,
                "notify_order": relation.notify_order,
            },
        })
    for event in events:
        nodes.append(_event_node(event))
        if event.need_id:
            edges.append(_edge("ACTION_ON", "DecisionEvent", event.id, "ResourceRequest", event.need_id))
        if event.resource_id:
            edges.append(_edge("ACTION_RESOURCE", "DecisionEvent", event.id, "Resource", event.resource_id))
        if event.actor_id:
            edges.append(_edge("ACTION_ACTOR", "DecisionEvent", event.id, "Person", event.actor_id))
    for road_node in road["nodes"]:
        nodes.append(_road_node(road_node))
    for road_edge in road["edges"]:
        nodes.append(_road_edge_node(road_edge))
        edges.append(_edge("ROAD_CONNECTS", "RoadEdge", road_edge["id"], "RoadNode", road_edge["a"]))
        edges.append(_edge("ROAD_CONNECTS", "RoadEdge", road_edge["id"], "RoadNode", road_edge["b"]))

    return {
        "schema_version": 1,
        "metrics": _metrics(db),
        "nodes": nodes,
        "edges": _dedupe_edges(edges),
        "actions": ACTIONS,
    }


def object_context(db: Session, object_type: str, object_id: str) -> dict[str, Any]:
    canonical = _canonical_type(object_type)
    if canonical == "Person":
        user = _get_or_404(db, User, object_id, "Person")
        return {
            "object": _person_node(user),
            "related": {
                "requests": [_need_node(n) for n in user.needs],
                "resources": [_resource_node(r) for r in user.resources],
                "alerts": [_alert_node(a) for a in user.alerts],
                "checkins": [_checkin_node(c) for c in user.checkins],
                "care_contacts": [
                    _person_node(r.contact)
                    for r in db.query(CareRelation)
                    .filter(CareRelation.elderly_id == user.id, CareRelation.is_active == True)
                    .all()
                    if r.contact
                ],
            },
        }
    if canonical == "ResourceRequest":
        need = _get_or_404(db, CommunityNeed, object_id, "ResourceRequest")
        return decision_context(db, str(need.id))
    if canonical == "Resource":
        resource = _get_or_404(db, CommunityResource, object_id, "Resource")
        matched = db.query(CommunityNeed).filter(CommunityNeed.matched_resource_id == resource.id).all()
        return {
            "object": _resource_node(resource),
            "related": {
                "owner": _person_node(resource.owner) if resource.owner else None,
                "matched_requests": [_need_node(n) for n in matched],
            },
        }
    if canonical == "Facility":
        facility = _get_or_404(db, ResourcePoint, object_id, "Facility")
        compatible = _compatible_open_needs(db, facility)
        return {
            "object": _facility_node(facility),
            "related": {"compatible_open_requests": [_need_node(n) for n in compatible]},
        }
    if canonical == "Alert":
        alert = _get_or_404(db, Alert, object_id, "Alert")
        return {
            "object": _alert_node(alert),
            "related": {"person": _person_node(alert.elderly) if alert.elderly else None},
        }
    if canonical == "CheckIn":
        checkin = _get_or_404(db, DailyCheckin, object_id, "CheckIn")
        return {
            "object": _checkin_node(checkin),
            "related": {"person": _person_node(checkin.elderly) if checkin.elderly else None},
        }
    if canonical == "DecisionEvent":
        event = _get_or_404(db, DispatchEvent, object_id, "DecisionEvent")
        return {
            "object": _event_node(event),
            "related": {
                "request": _need_node(event.need) if event.need else None,
                "resource": _resource_node(event.resource) if event.resource else None,
                "actor": _person_node(event.actor) if event.actor else None,
            },
        }
    if canonical in {"RoadNode", "RoadEdge"}:
        road = road_network.sandbox_snapshot(db)
        if canonical == "RoadNode":
            node = next((n for n in road["nodes"] if n["id"] == object_id), None)
            if not node:
                raise HTTPException(status_code=404, detail="RoadNode not found")
            incident_edges = [e for e in road["edges"] if e["a"] == object_id or e["b"] == object_id]
            return {"object": _road_node(node), "related": {"edges": [_road_edge_node(e) for e in incident_edges]}}
        road_edge = next((e for e in road["edges"] if e["id"] == object_id), None)
        if not road_edge:
            raise HTTPException(status_code=404, detail="RoadEdge not found")
        endpoints = [n for n in road["nodes"] if n["id"] in {road_edge["a"], road_edge["b"]}]
        return {"object": _road_edge_node(road_edge), "related": {"nodes": [_road_node(n) for n in endpoints]}}
    raise HTTPException(status_code=400, detail=f"Unsupported object_type: {object_type}")


def decision_context(db: Session, need_id: str) -> dict[str, Any]:
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        raise HTTPException(status_code=404, detail="ResourceRequest not found")

    candidates = dispatch.preview_candidates(need_id, db)
    matched = need.matched_resource
    requester = need.requester
    top_candidate = (candidates.get("candidates") or [None])[0]
    recommended_action = _recommended_action(need, top_candidate)

    return {
        "object": _need_node(need),
        "requester": _person_node(requester) if requester else None,
        "matched_resource": _resource_node(matched) if matched else None,
        "decision_graph": {
            "nodes": [
                n for n in [
                    _need_node(need),
                    _person_node(requester) if requester else None,
                    _resource_node(matched) if matched else None,
                ] if n
            ],
            "edges": [
                _edge("REQUESTED_BY", "ResourceRequest", need.id, "Person", need.requester_id),
                *(
                    [_edge("MATCHED_TO", "ResourceRequest", need.id, "Resource", need.matched_resource_id)]
                    if need.matched_resource_id else []
                ),
            ],
        },
        "candidate_resources": candidates.get("candidates", []),
        "decision_events": [_event_node(e) for e in _decision_events(db, str(need.id), limit=20)],
        "vulnerability": candidates.get("vulnerability", 0.0),
        "recommended_action": recommended_action,
        "constraints": {
            "distance_limits_km_by_urgency": dispatch.URGENCY_MAX_KM,
            "human_confirmation_required": True,
            "automatic_line_notification": False,
        },
        "explanation": _decision_explanation(need, top_candidate),
    }


def _metrics(db: Session) -> dict[str, int]:
    today = date.today()
    return {
        "people": db.query(User).count(),
        "active_people": db.query(User).filter(User.is_active == True).count(),
        "open_requests": db.query(CommunityNeed).filter(CommunityNeed.status == "open").count(),
        "suggested_requests": db.query(CommunityNeed).filter(CommunityNeed.status == "suggested").count(),
        "matched_requests": db.query(CommunityNeed).filter(CommunityNeed.status == "matched").count(),
        "available_resources": db.query(CommunityResource).filter(CommunityResource.is_available == True).count(),
        "facilities": db.query(ResourcePoint).filter(ResourcePoint.is_active == True).count(),
        "active_alerts": db.query(Alert).filter(Alert.status == "sent").count(),
        "decision_events": db.query(DispatchEvent).count(),
        "road_closed_edges": road_network.sandbox_snapshot(db)["metrics"]["closed_edges"],
        "road_slow_edges": road_network.sandbox_snapshot(db)["metrics"]["slow_edges"],
        "risky_checkins_today": db.query(DailyCheckin).filter(
            DailyCheckin.date == today,
            DailyCheckin.status.in_(["no_response", "help_needed"]),
        ).count(),
    }


def _canonical_type(object_type: str) -> str:
    key = object_type.replace("_", "").replace("-", "").lower()
    if key in TYPE_ALIASES:
        return TYPE_ALIASES[key]
    if object_type in OBJECT_TYPES:
        return object_type
    raise HTTPException(status_code=400, detail=f"Unknown object_type: {object_type}")


def _get_or_404(db: Session, model, object_id: str, label: str):
    row = db.query(model).filter(model.id == object_id).first()
    if not row:
        raise HTTPException(status_code=404, detail=f"{label} not found")
    return row


def _object_id(object_type: str, raw_id: Any) -> str:
    return f"{object_type}:{raw_id}"


def _node(object_type: str, raw_id: Any, label: str, properties: dict[str, Any]) -> dict[str, Any]:
    return {
        "id": _object_id(object_type, raw_id),
        "type": object_type,
        "raw_id": str(raw_id),
        "label": label,
        "properties": _clean(properties),
    }


def _person_node(user: User) -> dict[str, Any]:
    roles = user.roles or []
    return _node("Person", user.id, user.name, {
        "roles": roles,
        "semantic_roles": _semantic_roles(roles),
        "phone": user.phone,
        "address": user.address,
        "lat": user.lat,
        "lng": user.lng,
        "is_active": user.is_active,
        "created_at": user.created_at,
    })


def _need_node(need: CommunityNeed) -> dict[str, Any]:
    return _node("ResourceRequest", need.id, f"{need.need_type} / urgency {need.urgency}", {
        "need_type": need.need_type,
        "description": need.description,
        "quantity": need.quantity,
        "urgency": need.urgency,
        "status": need.status,
        "address": need.address,
        "lat": need.lat,
        "lng": need.lng,
        "created_at": need.created_at,
        "valid_until": need.valid_until,
    })


def _resource_node(resource: CommunityResource) -> dict[str, Any]:
    return _node("Resource", resource.id, resource.name, {
        "resource_type": resource.resource_type,
        "quantity": resource.quantity,
        "address": resource.address,
        "lat": resource.lat,
        "lng": resource.lng,
        "is_available": resource.is_available,
        "note": resource.note,
        "last_updated": resource.last_updated,
    })


def _facility_node(facility: ResourcePoint) -> dict[str, Any]:
    return _node("Facility", facility.id, facility.name, {
        "point_type": facility.point_type,
        "supply_types": POINT_SUPPLY_TYPES.get(facility.point_type, []),
        "address": facility.address,
        "lat": facility.lat,
        "lng": facility.lng,
        "capacity": facility.capacity,
        "current_load": facility.current_load,
        "source": facility.source,
        "is_active": facility.is_active,
    })


def _alert_node(alert: Alert) -> dict[str, Any]:
    return _node("Alert", alert.id, alert.alert_type, {
        "alert_type": alert.alert_type,
        "status": alert.status,
        "created_at": alert.created_at,
        "resolved_at": alert.resolved_at,
    })


def _checkin_node(checkin: DailyCheckin) -> dict[str, Any]:
    return _node("CheckIn", checkin.id, f"{checkin.date} / {checkin.status}", {
        "date": checkin.date,
        "status": checkin.status,
        "note": checkin.note,
        "responded_at": checkin.responded_at,
        "confirmed_at": checkin.confirmed_at,
    })


def _event_node(event: DispatchEvent) -> dict[str, Any]:
    details = {}
    if event.details_json:
        try:
            details = json.loads(event.details_json)
        except Exception:
            details = {"raw": event.details_json}
    return _node("DecisionEvent", event.id, event.action, {
        "action": event.action,
        "outcome": event.outcome,
        "actor_label": event.actor_label,
        "previous_status": event.previous_status,
        "new_status": event.new_status,
        "details": details,
        "created_at": event.created_at,
    })


def _road_node(node: dict[str, Any]) -> dict[str, Any]:
    return _node("RoadNode", node["id"], node.get("label") or node["id"], {
        "lat": node.get("lat"),
        "lng": node.get("lng"),
        "custom": node.get("custom", False),
    })


def _road_edge_node(edge: dict[str, Any]) -> dict[str, Any]:
    return _node("RoadEdge", edge["id"], f"{edge['a']} -> {edge['b']}", {
        "a": edge.get("a"),
        "b": edge.get("b"),
        "status": edge.get("status"),
        "distance_km": edge.get("distance_km"),
        "effective_distance_km": edge.get("effective_distance_km"),
        "multiplier": edge.get("multiplier"),
        "custom": edge.get("custom", False),
    })


def _edge(
    link_type: str,
    from_type: str,
    from_id: Any,
    to_type: str,
    to_id: Any,
) -> dict[str, Any]:
    return {
        "id": f"{link_type}:{from_type}:{from_id}->{to_type}:{to_id}",
        "type": link_type,
        "from": _object_id(from_type, from_id),
        "to": _object_id(to_type, to_id),
    }


def _dedupe_edges(edges: list[dict[str, Any]]) -> list[dict[str, Any]]:
    seen = set()
    out = []
    for edge in edges:
        if edge["id"] in seen:
            continue
        seen.add(edge["id"])
        out.append(edge)
    return out


def _semantic_roles(roles: list[str]) -> list[str]:
    out = []
    if "elderly" in roles:
        out.append("Victim")
    if "volunteer" in roles or "admin" in roles:
        out.append("Responder")
    if "family" in roles:
        out.append("CareContact")
    return out or ["Person"]


def _compatible_open_needs(db: Session, facility: ResourcePoint) -> list[CommunityNeed]:
    supply_types = POINT_SUPPLY_TYPES.get(facility.point_type, [])
    if not supply_types:
        return []
    return (
        db.query(CommunityNeed)
        .filter(
            CommunityNeed.status == "open",
            CommunityNeed.need_type.in_(supply_types),
        )
        .order_by(CommunityNeed.urgency.desc(), CommunityNeed.created_at)
        .limit(20)
        .all()
    )


def _decision_events(db: Session, need_id: str, limit: int = 20) -> list[DispatchEvent]:
    return (
        db.query(DispatchEvent)
        .filter(DispatchEvent.need_id == need_id)
        .order_by(DispatchEvent.created_at.desc())
        .limit(limit)
        .all()
    )


def _recommended_action(need: CommunityNeed, top_candidate: dict[str, Any] | None) -> dict[str, Any]:
    if need.status == "suggested":
        return {"action_id": "confirm_dispatch", "reason": "A dispatch suggestion is waiting for manager approval."}
    if need.status == "open" and top_candidate:
        if top_candidate.get("source") == "resource":
            return {"action_id": "manual_dispatch", "reason": "A feasible volunteer-owned resource is available."}
        return {"action_id": "propose_dispatch", "reason": "A fixed facility can support the request, but needs operator handling."}
    if need.status == "open":
        return {"action_id": "propose_dispatch", "reason": "No candidate is currently visible; rerun dispatch after resources change."}
    return {"action_id": None, "reason": f"Request status is {need.status}; no dispatch action is recommended."}


def _decision_explanation(need: CommunityNeed, top_candidate: dict[str, Any] | None) -> str:
    if not top_candidate:
        return "No feasible candidate satisfies the type, distance, and availability constraints right now."
    breakdown = top_candidate.get("breakdown") or {}
    return (
        f"Top candidate {top_candidate.get('name')} scores {top_candidate.get('score')} "
        f"for urgency {need.urgency}, vulnerability {breakdown.get('vulnerability_pts')}, "
        f"affinity {breakdown.get('affinity_pts')}, wait {breakdown.get('wait_pts')}, "
        f"distance penalty {breakdown.get('dist_penalty')}, and load penalty {breakdown.get('load_penalty')}."
    )


def _clean(value: Any) -> Any:
    if isinstance(value, dict):
        return {k: _clean(v) for k, v in value.items()}
    if isinstance(value, list):
        return [_clean(v) for v in value]
    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if value is None or isinstance(value, (str, int, float, bool)):
        return value
    return str(value)
