# -*- coding: utf-8 -*-
"""Rebuildable projections of operational records into a versioned planning graph."""
from __future__ import annotations

from collections import Counter
from datetime import UTC, datetime, timedelta

from sqlalchemy import or_
from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.services.workspace import Edge, GraphDocument, LogisticsRecord, Node
from app.services.inventory import available_amount, quantity_parts, structured_quantity
from app.services.workspace_inventory import editable_values, row_version

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


def _aed_for(need, requester) -> str | None:
    """未結案求救旁邊最近的 AED（一行文字）；調度者打電話時可以直接告訴對方去哪裡拿。"""
    from app.services.aed import one_line
    lat = need.lat if need.lat is not None else requester.lat
    lng = need.lng if need.lng is not None else requester.lng
    return one_line(lat, lng)


CLOSED_VISIBLE_DAYS = 7      # 已完成／已取消的需求在即時現況上保留幾天
CHECKIN_HISTORY_DAYS = 30    # 即時現況看的打卡範圍（脆弱度只看 7 天，最近一次打卡通常在幾天內）
CARE_RELATION_ZH = {"family": "家屬", "volunteer": "志工", "neighbor": "鄰居", "other": "聯絡人"}


def operational_snapshot(db: Session, zone_id: str | None = None) -> dict:
    """The live projection a workspace merges into its graph.

    With zone_id given, resources and needs outside that zone are left out entirely —
    a zone's workspace only ever sees, and can only ever propose dispatches against,
    its own data. Omitting zone_id keeps the old system-wide view (existing callers,
    and an admin overview across every zone)."""
    from app.services import dispatch

    stamp = datetime.now(UTC).isoformat()
    users = {str(u.id): u for u in db.query(User).all()}
    resource_q = db.query(CommunityResource)
    need_q = db.query(CommunityNeed)
    if zone_id is not None:
        resource_q = resource_q.filter(CommunityResource.zone_id == zone_id)
        need_q = need_q.filter(CommunityNeed.zone_id == zone_id)
    resources = resource_q.all()
    # 即時現況每 30 秒重抓一次：只帶還在進行的需求和最近結案的，不把好幾年的歷史都畫上地圖
    recent = datetime.now(UTC).replace(tzinfo=None) - timedelta(days=CLOSED_VISIBLE_DAYS)
    needs = need_q.filter(or_(CommunityNeed.status.in_(("open", "suggested", "matched")),
                              CommunityNeed.created_at >= recent)).order_by(
        CommunityNeed.created_at.desc(), CommunityNeed.id).all()
    points = db.query(ResourcePoint).filter(ResourcePoint.is_active.is_(True)).all()
    care = db.query(CareRelation).filter(CareRelation.is_active.is_(True)).all()
    # 最近一次打卡與 7 天脆弱度只需要近期的紀錄；整年的打卡不用每 30 秒全撈一次
    checkin_since = datetime.now(UTC).date() - timedelta(days=CHECKIN_HISTORY_DAYS)
    checkins = db.query(DailyCheckin).filter(DailyCheckin.date >= checkin_since).order_by(
        DailyCheckin.date.desc(), DailyCheckin.created_at.desc(), DailyCheckin.id).all()
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
        total_quantity = structured_quantity(resource)
        available_quantity = available_amount(resource)
        quantity = ((available_quantity, total_quantity[1])
                    if total_quantity is not None and available_quantity is not None
                    else quantity_parts(resource.quantity))
        nodes[node_id] = Node(id=node_id, label=resource.name, kind="supply", **_location(resource),
                             quantity=quantity[0] if quantity else 0,
                             available=bool(resource.is_available and (quantity is None or quantity[0] > 0)),
                             source="平台物資登記", properties={"db": "resource", "owner_id": str(resource.owner_id),
                                 "owner": users[str(resource.owner_id)].name if str(resource.owner_id) in users else "已移除",
                                 "resource_type": resource.resource_type, "version": row_version(resource),
                                 "base_values": editable_values(resource), "observed_at": stamp,
                                 "quantity_verified": quantity is not None, "quantity_text": resource.quantity or "",
                                 "quantity_on_hand": total_quantity[0] if total_quantity else None,
                                 "quantity_reserved": int(resource.reserved_amount or 0),
                                 "quantity_available": available_quantity,
                                 "quantity_unit": total_quantity[1] if total_quantity else None,
                                 "inventory_version": int(resource.inventory_version or 1)},
                             logistics=[LogisticsRecord(id=node_id, role="supply", item=_item(resource.resource_type),
                                         unit=quantity[1], quantity=quantity[0], source="平台物資登記")] if quantity else [])
        relation("owner:" + str(resource.id), owner_id, node_id, "持有", "supplies")
    # 未結案求救的到場時間（一個查詢）：工作區要分得出「有人受理但還在路上」和「人已經到了」
    from app.models.dispatch_event import DispatchEvent
    open_sos = [str(n.id) for n in needs if n.need_type == "sos" and n.status == "open"]
    open_sos_ids = set(open_sos)
    on_scene = {str(e.need_id): e.created_at for e in db.query(DispatchEvent).filter(
        DispatchEvent.action == "sos_on_scene", DispatchEvent.need_id.in_(open_sos)).all()} if open_sos else {}
    for need in needs:
        person_id = person(need.requester_id)
        if not person_id:
            continue
        node_id = f"db:need:{need.id}"
        requester = users[str(need.requester_id)]
        quantity = structured_quantity(need) or quantity_parts(need.quantity)
        pending = need.status in DEMAND_STATUSES and need.need_type != "sos"
        resource_id = f"db:res:{need.matched_resource_id}" if need.matched_resource_id else None
        properties = {"db": "need", "need_type": need.need_type, "status": need.status,
                      "requester_id": person_id, "resource_id": resource_id,
                      "description": need.description or "", "address": need.address or requester.address or "",
                      "urgency": need.urgency, "quantity_text": need.quantity or "",
                      "quantity_verified": quantity is not None, "version": row_version(need), "observed_at": stamp,
                      "reported_at": need.created_at.replace(tzinfo=UTC).isoformat() if need.created_at else None,
                      "responder": need.responder.name if need.responder_id and need.responder else None,
                      "acknowledged_at": need.acknowledged_at.replace(tzinfo=UTC).isoformat() if need.acknowledged_at else None,
                      "on_scene_at": on_scene[str(need.id)].replace(tzinfo=UTC).isoformat() if str(need.id) in on_scene else None,
                      "nearest_aed": _aed_for(need, requester) if str(need.id) in open_sos_ids else None,
                      "location_source": "需求登記" if need.lat is not None and need.lng is not None else "登記人位置"}
        nodes[node_id] = Node(id=node_id, label=f"{requester.name} · {_item(need.need_type)}", kind="custom",
                             **_location(need, requester), quantity=0, available=pending or need.need_type == "sos",
                             source="平台需求單", properties=properties,
                             logistics=[LogisticsRecord(id=node_id, role="demand", item=_item(need.need_type), unit=quantity[1],
                                         quantity=quantity[0], priority=min(max(need.urgency or 3, 1), 5),
                                         source="平台需求單")] if pending and quantity else [])
        relation("request:" + str(need.id), person_id, node_id, "提出需求", "request")
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
        relation("care:" + str(c.id), person(c.contact_id), person(c.elderly_id),
                 "照護 / " + CARE_RELATION_ZH.get(c.relation, c.relation), "care")

    graph = GraphDocument(nodes=list(nodes.values()), edges=edges)
    counts = {"elders": sum(u.is_active and u.has_role("elderly") for u in users.values()),
              "volunteers": sum(u.is_active and u.has_role("volunteer") for u in users.values()),
              "demands": sum(bool(n.logistics) for n in nodes.values() if n.properties.get("db") == "need"),
              "supplies": len(resources), "points": len(points)}
    return {"graph": graph.model_dump(), "counts": counts, "tasks": tasks, "observed_at": stamp,
            "owners": [{"id": str(u.id), "name": u.name} for u in users.values()
                       if u.is_active and (u.has_role("volunteer") or u.has_role("admin"))]}


EVENT_NODE_ID = "db:incident:live"
POINT_RANGE_KM = 20.0


def bind_to_incidents(nodes: list[Node]) -> tuple[list[Node], list[Edge]]:
    """Tie every live need to the incident it belongs to, and nearby resource points to that incident.

    Live records only link to each other (requester, owner, assignment), so a workspace opened on an
    event showed its needs and SOS floating beside the incident with nothing connecting them. A need
    joins its nearest incident; one without coordinates joins the first. A workspace that has no
    incident at all gets one live event node so the needs still hang together.
    workspace-operations.js applies the same rule when the browser merges live data."""
    from app.services.geo import haversine_km

    def located(n):
        return n.lat is not None and n.lng is not None

    incidents = [n for n in nodes if n.kind == "incident"]
    needs = [n for n in nodes if n.properties.get("db") == "need" and n.properties.get("status") != "cancelled"]
    extra: list[Node] = []
    if needs and not incidents:
        spots = [n for n in needs if located(n)]
        event = Node(id=EVENT_NODE_ID, label="即時通報事件", kind="incident",
                     lat=sum(n.lat for n in spots) / len(spots) if spots else None,
                     lng=sum(n.lng for n in spots) / len(spots) if spots else None,
                     source="平台需求單彙整", properties={"db": "event", "description": "工作區沒有事件物件時，彙整即時需求"})
        incidents, extra = [event], [event]

    def nearest(node):
        placed = [i for i in incidents if located(i)]
        if not located(node) or not placed:
            return incidents[0], None
        best = min(placed, key=lambda i: haversine_km(node.lat, node.lng, i.lat, i.lng))
        return best, haversine_km(node.lat, node.lng, best.lat, best.lng)

    edges: list[Edge] = []
    if not incidents:
        return extra, edges
    for need in needs:
        incident, _ = nearest(need)
        sos = need.properties.get("need_type") == "sos"
        edges.append(Edge(id=f"db:edge:event:{need.id}", source=incident.id, target=need.id,
                          label="緊急求助" if sos else "事件需求", kind="related", directed=True,
                          provenance="平台資料庫", properties={"db": True, "binding": "incident"}))
    for point in (n for n in nodes if n.properties.get("db") == "point"):
        incident, km = nearest(point)
        if km is not None and km <= POINT_RANGE_KM:
            edges.append(Edge(id=f"db:edge:event:{point.id}", source=incident.id, target=point.id,
                              label="範圍內資源點", kind="related", directed=True,
                              provenance="平台資料庫", properties={"db": True, "binding": "incident"}))
    return extra, edges


def database_nodes(db: Session, zone_id: str | None = None) -> tuple[list[Node], dict]:
    snapshot = operational_snapshot(db, zone_id)
    return [Node.model_validate(n) for n in snapshot["graph"]["nodes"]], snapshot["counts"]


def merge_database(graph: GraphDocument, db: Session, zone_id: str | None = None) -> tuple[GraphDocument, dict]:
    snapshot = operational_snapshot(db, zone_id)
    fresh = GraphDocument.model_validate(snapshot["graph"])
    fresh_by_id = {n.id: n for n in fresh.nodes}
    old_ids = {n.id for n in graph.nodes if is_db_id(n.id) and n.id != EVENT_NODE_ID}
    for node in graph.nodes:
        if node.id in fresh_by_id and "_layout" in node.properties:
            fresh_by_id[node.id].properties["_layout"] = node.properties["_layout"]
    kept = [n for n in graph.nodes if not is_db_id(n.id)]
    ids = {n.id for n in kept} | fresh_by_id.keys()
    edges = [e for e in graph.edges if not e.id.startswith("db:edge:") and e.source in ids and e.target in ids]
    event_nodes, event_edges = bind_to_incidents(kept + list(fresh_by_id.values()))
    merged = GraphDocument(nodes=kept + list(fresh_by_id.values()) + event_nodes,
                           edges=edges + fresh.edges + event_edges)
    counts = snapshot["counts"]
    counts.update(added=len(fresh_by_id.keys() - old_ids), updated=len(old_ids & fresh_by_id.keys()),
                  removed=len(old_ids - fresh_by_id.keys()))
    return merged, counts
