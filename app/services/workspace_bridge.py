# -*- coding: utf-8 -*-
"""Rebuildable projections of operational records into a versioned planning graph."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.services.workspace import Edge, GraphDocument, LogisticsRecord, Node
from app.services.workspace_inventory import editable_values, quantity_parts, row_version

PREFIX = "db:"
ITEM_ZH = {"water": "飲用水", "demo_water": "飲用水", "food": "食物", "first_aid": "急救用品", "shelter": "庇護所",
           "vehicle": "交通工具", "tool": "工具", "other": "其他物資", "sos": "緊急求助"}
DEMAND_STATUSES = ("open", "suggested")


def is_db_id(value: str) -> bool:
    return isinstance(value, str) and value.startswith(PREFIX)


def _item(kind: str) -> str:
    return ITEM_ZH.get(kind, kind or "其他物資")


def _location(row, fallback=None):
    if row.lat is not None and row.lng is not None:
        return {"lat": row.lat, "lng": row.lng}
    if fallback is not None and fallback.lat is not None and fallback.lng is not None:
        return {"lat": fallback.lat, "lng": fallback.lng}
    return {"lat": None, "lng": None}


def operational_snapshot(db: Session) -> dict:
    from app.services import dispatch

    stamp = datetime.now(UTC).isoformat()
    users = {str(u.id): u for u in db.query(User).all()}
    resources = db.query(CommunityResource).all()
    needs = db.query(CommunityNeed).order_by(CommunityNeed.created_at.desc(), CommunityNeed.id).all()
    points = db.query(ResourcePoint).filter(ResourcePoint.is_active.is_(True)).all()
    care = db.query(CareRelation).filter(CareRelation.is_active.is_(True)).all()
    checkins = db.query(DailyCheckin).order_by(DailyCheckin.date.desc(), DailyCheckin.created_at.desc(), DailyCheckin.id).all()
    alerts = Counter(str(a.elderly_id) for a in db.query(Alert).filter(Alert.status == "sent").all())
    contacts = Counter(str(c.elderly_id) for c in care)
    cutoff = datetime.now(UTC).date() - timedelta(days=dispatch.VULNERABILITY_LOOKBACK_DAYS)
    risks = Counter(str(c.elderly_id) for c in checkins if c.date >= cutoff and c.status in ("no_response", "help_needed"))
    latest = {}
    for c in checkins:
        latest.setdefault(str(c.elderly_id), {"status": c.status, "date": str(c.date), "note": c.note,
                                            "responded_at": str(c.responded_at) if c.responded_at else None})
    nodes, edges, tasks = {}, [], []

    def person(user_id):
        key = str(user_id)
        user = users.get(key)
        if not user:
            return None
        node_id = f"db:person:{key}"
        if node_id not in nodes:
            vulnerability = (min(risks[key] * dispatch.PTS_PER_RISK_CHECKIN, dispatch.MAX_CHECKIN_PTS)
                             + min(alerts[key] * dispatch.PTS_PER_ACTIVE_ALERT, dispatch.MAX_ALERT_PTS)
                             + dispatch.ISOLATION_PTS.get(contacts[key], 0.0)) if user.has_role("elderly") else None
            nodes[node_id] = Node(id=node_id, label=user.name, kind="person", **_location(user),
                                 available=bool(user.is_active), source="平台人員資料",
                                 properties={"db": "user", "roles": user.roles, "address": user.address or "",
                                             "version": row_version(user), "observed_at": stamp,
                                             "checkin": latest.get(key), "active_alerts": alerts[key],
                                             "vulnerability": vulnerability})
        return node_id

    def relation(key, source, target, label, kind="custom", **properties):
        if source in nodes and target in nodes and source != target:
            edges.append(Edge(id="db:edge:" + key, source=source, target=target, label=label, kind=kind,
                              directed=True, provenance="平台資料庫", properties={"db": True, **properties}))

    for user in users.values():
        if user.is_active:
            person(user.id)
    for resource in resources:
        node_id = f"db:res:{resource.id}"
        owner_id = person(resource.owner_id)
        quantity = quantity_parts(resource.quantity)
        nodes[node_id] = Node(id=node_id, label=resource.name, kind="supply", **_location(resource),
                             quantity=quantity[0] if quantity else 0, available=bool(resource.is_available),
                             source="平台物資登記", properties={"db": "resource", "owner_id": str(resource.owner_id),
                                 "owner": users[str(resource.owner_id)].name if str(resource.owner_id) in users else "已移除",
                                 "resource_type": resource.resource_type, "version": row_version(resource),
                                 "base_values": editable_values(resource), "observed_at": stamp,
                                 "quantity_verified": quantity is not None, "quantity_text": resource.quantity or ""},
                             logistics=[LogisticsRecord(id=node_id, role="supply", item=_item(resource.resource_type),
                                         unit=quantity[1], quantity=quantity[0], source="平台物資登記")] if quantity else [])
        relation("owner:" + str(resource.id), owner_id, node_id, "持有", "supplies")
    for need in needs:
        person_id = person(need.requester_id)
        if not person_id:
            continue
        node_id = f"db:need:{need.id}"
        requester = users[str(need.requester_id)]
        quantity = quantity_parts(need.quantity)
        pending = need.status in DEMAND_STATUSES and need.need_type != "sos"
        resource_id = f"db:res:{need.matched_resource_id}" if need.matched_resource_id else None
        properties = {"db": "need", "need_type": need.need_type, "status": need.status,
                      "requester_id": person_id, "resource_id": resource_id,
                      "description": need.description or "", "address": need.address or requester.address or "",
                      "urgency": need.urgency, "quantity_text": need.quantity or "",
                      "quantity_verified": quantity is not None, "version": row_version(need), "observed_at": stamp,
                      "location_source": "需求登記" if need.lat is not None and need.lng is not None else "登記人位置"}
        nodes[node_id] = Node(id=node_id, label=f"{requester.name} · {_item(need.need_type)}", kind="custom",
                             **_location(need, requester), quantity=0, available=pending or need.need_type == "sos",
                             source="平台需求單", properties=properties,
                             logistics=[LogisticsRecord(id=node_id, role="demand", item=_item(need.need_type), unit=quantity[1],
                                         quantity=quantity[0], priority=min(max(need.urgency or 3, 1), 5),
                                         source="平台需求單")] if pending and quantity else [])
        relation("request:" + str(need.id), person_id, node_id, "提出需求")
        if resource_id and need.status in ("suggested", "matched", "fulfilled"):
            relation("assignment:" + str(need.id), resource_id, node_id,
                     {"suggested": "待核准", "matched": "執行中", "fulfilled": "已完成"}[need.status], "assignment",
                     status=need.status)
        tasks.append({"node_id": node_id, "label": nodes[node_id].label, **properties})
    for point in points:
        node_id = f"db:point:{point.id}"
        nodes[node_id] = Node(id=node_id, label=point.name, kind="facility", **_location(point), quantity=0,
                             source="平台資源點 / " + (point.source or "manual"),
                             properties={"db": "point", "point_type": point.point_type,
                                         "capacity": point.capacity, "current_load": point.current_load,
                                         "version": row_version(point), "base_values": editable_values(point),
                                         "observed_at": stamp, "operational_status": "unknown"})
    for c in care:
        relation("care:" + str(c.id), person(c.contact_id), person(c.elderly_id), "照護 / " + c.relation)

    graph = GraphDocument(nodes=list(nodes.values()), edges=edges)
    counts = {"elders": sum(u.is_active and u.has_role("elderly") for u in users.values()),
              "demands": sum(bool(n.logistics) for n in nodes.values() if n.properties.get("db") == "need"),
              "supplies": len(resources), "points": len(points)}
    return {"graph": graph.model_dump(), "counts": counts, "tasks": tasks, "observed_at": stamp,
            "owners": [{"id": str(u.id), "name": u.name} for u in users.values() if u.is_active]}


def database_nodes(db: Session) -> tuple[list[Node], dict]:
    snapshot = operational_snapshot(db)
    return [Node.model_validate(n) for n in snapshot["graph"]["nodes"]], snapshot["counts"]


def merge_database(graph: GraphDocument, db: Session) -> tuple[GraphDocument, dict]:
    snapshot = operational_snapshot(db)
    fresh = GraphDocument.model_validate(snapshot["graph"])
    fresh_by_id = {n.id: n for n in fresh.nodes}
    old_ids = {n.id for n in graph.nodes if is_db_id(n.id)}
    for node in graph.nodes:
        if node.id in fresh_by_id and "_layout" in node.properties:
            fresh_by_id[node.id].properties["_layout"] = node.properties["_layout"]
    kept = [n for n in graph.nodes if not is_db_id(n.id)]
    ids = {n.id for n in kept} | fresh_by_id.keys()
    edges = [e for e in graph.edges if not e.id.startswith("db:edge:") and e.source in ids and e.target in ids]
    merged = GraphDocument(nodes=kept + list(fresh_by_id.values()), edges=edges + fresh.edges)
    counts = snapshot["counts"]
    counts.update(added=len(fresh_by_id.keys() - old_ids), updated=len(old_ids & fresh_by_id.keys()),
                  removed=len(old_ids - fresh_by_id.keys()))
    return merged, counts
