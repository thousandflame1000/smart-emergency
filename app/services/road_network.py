# -*- coding: utf-8 -*-
"""Coarse road-network graph plus a mutable what-if sandbox.

The base graph models the Hualien-Taitung corridor with real town
waypoints on Route 9, Route 11, and the Yuchang Highway connector. The
sandbox overlay is stored in SystemConfig so an operator can close roads,
slow links, drag nodes, add nodes, and add/remove edges without changing
source code. Dispatch can pass a DB session to apply those what-if edits
to live distance scoring.
"""

from __future__ import annotations

import heapq
import json
import re
from typing import Any

from sqlalchemy.orm import Session

from app.models.config import SystemConfig
from app.services.hazard import haversine_km


NODES: dict[str, tuple[float, float]] = {
    "hualien":   (23.9739, 121.6015),
    "fenglin":   (23.7467, 121.4467),
    "guangfu":   (23.6667, 121.4167),
    "ruisui":    (23.5017, 121.3667),
    "yuli":      (23.3333, 121.3167),
    "fuli":      (23.1667, 121.2500),
    "chishang":  (23.1167, 121.2167),
    "guanshan":  (23.0500, 121.1667),
    "luye":      (22.9500, 121.1500),
    "taitung":   (22.7583, 121.1444),
    "fengbin":   (23.5833, 121.5333),
    "changbin":  (23.2833, 121.4667),
    "chenggong": (23.0997, 121.3706),
    "donghe":    (22.9975, 121.2967),
}

_EDGES = [
    ("hualien", "fenglin"), ("fenglin", "guangfu"), ("guangfu", "ruisui"),
    ("ruisui", "yuli"), ("yuli", "fuli"), ("fuli", "chishang"),
    ("chishang", "guanshan"), ("guanshan", "luye"), ("luye", "taitung"),
    ("hualien", "fengbin"), ("fengbin", "changbin"), ("changbin", "chenggong"),
    ("chenggong", "donghe"), ("donghe", "taitung"),
    ("yuli", "changbin"),
]

COVERAGE_KM = 6.0
SANDBOX_KEY = "road_network_sandbox"

STATUS_MULTIPLIER = {
    "normal": 1.0,
    "slow": 2.5,
    "closed": None,
}


def edge_id(a: str, b: str) -> str:
    """Stable undirected edge id used by the API/UI."""
    a, b = sorted([str(a), str(b)])
    return f"{a}__{b}"


def _slug(raw: str) -> str:
    slug = re.sub(r"[^a-zA-Z0-9_-]+", "_", raw.strip().lower()).strip("_")
    return slug or "node"


def _default_sandbox() -> dict[str, Any]:
    return {
        "node_overrides": {},
        "custom_nodes": {},
        "edge_overrides": {},
        "custom_edges": {},
        "deleted_nodes": [],
        "deleted_edges": [],
    }


def _load_sandbox(db: Session | None) -> dict[str, Any]:
    if db is None:
        return _default_sandbox()
    row = db.query(SystemConfig).filter(SystemConfig.key == SANDBOX_KEY).first()
    if not row:
        return _default_sandbox()
    try:
        data = json.loads(row.value or "{}")
    except Exception:
        return _default_sandbox()

    sandbox = _default_sandbox()
    for key, default in sandbox.items():
        value = data.get(key, default)
        if isinstance(default, dict) and isinstance(value, dict):
            sandbox[key] = value
        elif isinstance(default, list) and isinstance(value, list):
            sandbox[key] = value
    return sandbox


def _latest_observation_states(db: Session | None) -> dict[str, str]:
    if db is None:
        return {}
    from app.models.road_workflow import RoadObservation

    rows = (
        db.query(RoadObservation)
        .order_by(
            RoadObservation.observed_at,
            RoadObservation.created_at,
            RoadObservation.id,
        )
        .all()
    )
    return {row.road_segment_id: row.state for row in rows}


def _apply_observation(override: dict[str, Any] | None, state: str | None) -> dict[str, Any]:
    effective = dict(override or {})
    if state == "OPEN":
        effective.update(status="normal", multiplier=1.0)
    elif state == "SLOW":
        effective.update(status="slow", multiplier=STATUS_MULTIPLIER["slow"])
    elif state == "BLOCKED":
        effective.update(status="closed", multiplier=None)
    return effective


def _save_sandbox(db: Session, data: dict[str, Any]) -> None:
    row = db.query(SystemConfig).filter(SystemConfig.key == SANDBOX_KEY).first()
    payload = json.dumps(data, ensure_ascii=False, sort_keys=True)
    if row:
        row.value = payload
    else:
        db.add(SystemConfig(key=SANDBOX_KEY, value=payload))
    db.commit()


def _build_graph():
    graph: dict[str, list[tuple[str, float]]] = {n: [] for n in NODES}
    for a, b in _EDGES:
        d = haversine_km(*NODES[a], *NODES[b])
        graph[a].append((b, d))
        graph[b].append((a, d))
    return graph


_GRAPH = _build_graph()


def _effective_nodes(db: Session | None = None) -> dict[str, tuple[float, float]]:
    sandbox = _load_sandbox(db)
    deleted = set(sandbox["deleted_nodes"])
    nodes: dict[str, tuple[float, float]] = {
        node_id: coords
        for node_id, coords in NODES.items()
        if node_id not in deleted
    }
    for node_id, item in sandbox["node_overrides"].items():
        if node_id in nodes:
            nodes[node_id] = (float(item["lat"]), float(item["lng"]))
    for node_id, item in sandbox["custom_nodes"].items():
        if node_id not in deleted:
            nodes[node_id] = (float(item["lat"]), float(item["lng"]))
    return nodes


def _edge_record(
    edge_id_value: str,
    a: str,
    b: str,
    nodes: dict[str, tuple[float, float]],
    override: dict[str, Any] | None = None,
    *,
    custom: bool = False,
) -> dict[str, Any] | None:
    if a not in nodes or b not in nodes:
        return None
    override = override or {}
    status = override.get("status", "normal")
    if status not in STATUS_MULTIPLIER:
        status = "normal"

    multiplier = override.get("multiplier", STATUS_MULTIPLIER[status])
    if status == "normal":
        multiplier = 1.0
    elif status == "closed":
        multiplier = None
    elif multiplier is None:
        multiplier = STATUS_MULTIPLIER["slow"]
    else:
        multiplier = max(float(multiplier), 1.0)

    distance = haversine_km(*nodes[a], *nodes[b])
    return {
        "id": edge_id_value,
        "a": a,
        "b": b,
        "status": status,
        "multiplier": multiplier,
        "distance_km": distance,
        "effective_distance_km": None if multiplier is None else distance * float(multiplier),
        "custom": custom,
    }


def _effective_edges(db: Session | None = None) -> list[dict[str, Any]]:
    sandbox = _load_sandbox(db)
    observations = _latest_observation_states(db)
    nodes = _effective_nodes(db)
    deleted = set(sandbox["deleted_edges"])
    edges: list[dict[str, Any]] = []

    for a, b in _EDGES:
        eid = edge_id(a, b)
        if eid in deleted:
            continue
        override = _apply_observation(
            sandbox["edge_overrides"].get(eid), observations.get(eid)
        )
        rec = _edge_record(eid, a, b, nodes, override)
        if rec:
            edges.append(rec)

    for eid, item in sandbox["custom_edges"].items():
        if eid in deleted:
            continue
        override = _apply_observation(item, observations.get(eid))
        rec = _edge_record(eid, item["a"], item["b"], nodes, override, custom=True)
        if rec:
            edges.append(rec)
    return edges


def _graph_from_edges(nodes: dict[str, tuple[float, float]], edges: list[dict[str, Any]]):
    graph: dict[str, list[tuple[str, float]]] = {n: [] for n in nodes}
    for edge in edges:
        if edge["status"] == "closed" or edge["effective_distance_km"] is None:
            continue
        a, b, d = edge["a"], edge["b"], float(edge["effective_distance_km"])
        graph.setdefault(a, []).append((b, d))
        graph.setdefault(b, []).append((a, d))
    return graph


def _nearest_node(
    lat: float,
    lng: float,
    nodes: dict[str, tuple[float, float]] | None = None,
) -> tuple[str, float]:
    nodes = nodes or NODES
    best, best_d = None, float("inf")
    for name, (nlat, nlng) in nodes.items():
        d = haversine_km(lat, lng, nlat, nlng)
        if d < best_d:
            best, best_d = name, d
    return best, best_d


def _dijkstra(
    start: str,
    end: str,
    graph: dict[str, list[tuple[str, float]]] | None = None,
) -> float:
    graph = graph or _GRAPH
    if start == end:
        return 0.0
    dist = {start: 0.0}
    pq = [(0.0, start)]
    visited = set()
    while pq:
        d, node = heapq.heappop(pq)
        if node in visited:
            continue
        visited.add(node)
        if node == end:
            return d
        for neighbor, w in graph.get(node, []):
            nd = d + w
            if nd < dist.get(neighbor, float("inf")):
                dist[neighbor] = nd
                heapq.heappush(pq, (nd, neighbor))
    return float("inf")


def _dijkstra_path(
    start: str,
    end: str,
    graph: dict[str, list[tuple[str, float]]],
) -> tuple[float, list[str]]:
    if start == end:
        return 0.0, [start]
    dist = {start: 0.0}
    prev: dict[str, str | None] = {start: None}
    pq = [(0.0, start)]
    visited = set()
    while pq:
        d, node = heapq.heappop(pq)
        if node in visited:
            continue
        visited.add(node)
        if node == end:
            path = []
            cur: str | None = end
            while cur is not None:
                path.append(cur)
                cur = prev.get(cur)
            return d, list(reversed(path))
        for neighbor, w in graph.get(node, []):
            nd = d + w
            if nd < dist.get(neighbor, float("inf")):
                dist[neighbor] = nd
                prev[neighbor] = node
                heapq.heappush(pq, (nd, neighbor))
    return float("inf"), []


def road_distance_km(
    lat1: float,
    lng1: float,
    lat2: float,
    lng2: float,
    db: Session | None = None,
) -> float | None:
    """Return road distance, applying the sandbox overlay when db is given."""
    nodes = _effective_nodes(db) if db is not None else NODES
    graph = _graph_from_edges(nodes, _effective_edges(db)) if db is not None else _GRAPH
    n1, d1 = _nearest_node(lat1, lng1, nodes)
    n2, d2 = _nearest_node(lat2, lng2, nodes)
    if d1 > COVERAGE_KM or d2 > COVERAGE_KM:
        return None
    path_d = _dijkstra(n1, n2, graph)
    if path_d == float("inf"):
        return None
    return d1 + path_d + d2


def route_between_nodes(db: Session, start: str, end: str) -> dict[str, Any]:
    nodes = _effective_nodes(db)
    if start not in nodes or end not in nodes:
        return {"error": "unknown node"}
    graph = _graph_from_edges(nodes, _effective_edges(db))
    distance, path = _dijkstra_path(start, end, graph)
    if distance == float("inf"):
        return {
            "start": start,
            "end": end,
            "distance_km": None,
            "path": [],
            "segment_ids": [],
            "reachable": False,
        }
    return {
        "start": start,
        "end": end,
        "distance_km": round(distance, 3),
        "path": path,
        "segment_ids": [edge_id(path[i], path[i + 1]) for i in range(len(path) - 1)],
        "reachable": True,
    }


def current_road_state(db: Session, road_segment_id: str) -> str | None:
    observation = _latest_observation_states(db).get(road_segment_id)
    if observation:
        return observation
    edge = next(
        (item for item in _effective_edges(db) if item["id"] == road_segment_id),
        None,
    )
    if not edge:
        return None
    return {"normal": "OPEN", "slow": "SLOW", "closed": "BLOCKED"}[edge["status"]]


def sandbox_snapshot(db: Session) -> dict[str, Any]:
    nodes = _effective_nodes(db)
    edges = _effective_edges(db)
    closed = sum(1 for e in edges if e["status"] == "closed")
    slow = sum(1 for e in edges if e["status"] == "slow")
    return {
        "nodes": [
            {
                "id": node_id,
                "label": node_id.replace("_", " ").title(),
                "lat": lat,
                "lng": lng,
                "custom": node_id not in NODES,
            }
            for node_id, (lat, lng) in sorted(nodes.items())
        ],
        "edges": [
            {
                **e,
                "distance_km": round(e["distance_km"], 3),
                "effective_distance_km": (
                    None if e["effective_distance_km"] is None
                    else round(e["effective_distance_km"], 3)
                ),
            }
            for e in sorted(edges, key=lambda x: x["id"])
        ],
        "metrics": {
            "nodes": len(nodes),
            "edges": len(edges),
            "closed_edges": closed,
            "slow_edges": slow,
            "custom_nodes": sum(1 for n in nodes if n not in NODES),
            "custom_edges": sum(1 for e in edges if e["custom"]),
        },
    }


def set_edge_status(
    db: Session,
    edge_id_value: str,
    status: str,
    multiplier: float | None = None,
) -> dict[str, Any]:
    status = status or "normal"
    if status not in STATUS_MULTIPLIER:
        raise ValueError("status must be normal, slow, or closed")
    sandbox = _load_sandbox(db)
    deleted = set(sandbox["deleted_edges"])
    deleted.discard(edge_id_value)
    sandbox["deleted_edges"] = sorted(deleted)

    if edge_id_value in sandbox["custom_edges"]:
        sandbox["custom_edges"][edge_id_value]["status"] = status
        if multiplier is not None:
            sandbox["custom_edges"][edge_id_value]["multiplier"] = max(float(multiplier), 1.0)
    elif status == "normal":
        sandbox["edge_overrides"].pop(edge_id_value, None)
    else:
        item: dict[str, Any] = {"status": status}
        if status == "slow":
            item["multiplier"] = max(float(multiplier or STATUS_MULTIPLIER["slow"]), 1.0)
        sandbox["edge_overrides"][edge_id_value] = item
    _save_sandbox(db, sandbox)
    return sandbox_snapshot(db)


def delete_edge(db: Session, edge_id_value: str) -> dict[str, Any]:
    sandbox = _load_sandbox(db)
    sandbox["custom_edges"].pop(edge_id_value, None)
    sandbox["edge_overrides"].pop(edge_id_value, None)
    deleted = set(sandbox["deleted_edges"])
    deleted.add(edge_id_value)
    sandbox["deleted_edges"] = sorted(deleted)
    _save_sandbox(db, sandbox)
    return sandbox_snapshot(db)


def move_node(db: Session, node_id: str, lat: float, lng: float) -> dict[str, Any]:
    sandbox = _load_sandbox(db)
    target = {"lat": float(lat), "lng": float(lng)}
    if node_id in NODES:
        sandbox["node_overrides"][node_id] = target
    elif node_id in sandbox["custom_nodes"]:
        sandbox["custom_nodes"][node_id].update(target)
    else:
        raise ValueError("unknown node")
    _save_sandbox(db, sandbox)
    return sandbox_snapshot(db)


def add_node(
    db: Session,
    node_id: str,
    lat: float,
    lng: float,
    label: str | None = None,
) -> dict[str, Any]:
    sandbox = _load_sandbox(db)
    node_id = _slug(node_id or label or "custom_node")
    if node_id in NODES or node_id in sandbox["custom_nodes"]:
        raise ValueError("node already exists")
    sandbox["custom_nodes"][node_id] = {"lat": float(lat), "lng": float(lng), "label": label or node_id}
    deleted = set(sandbox["deleted_nodes"])
    deleted.discard(node_id)
    sandbox["deleted_nodes"] = sorted(deleted)
    _save_sandbox(db, sandbox)
    return sandbox_snapshot(db)


def delete_node(db: Session, node_id: str) -> dict[str, Any]:
    sandbox = _load_sandbox(db)
    sandbox["custom_nodes"].pop(node_id, None)
    sandbox["node_overrides"].pop(node_id, None)
    deleted_nodes = set(sandbox["deleted_nodes"])
    deleted_nodes.add(node_id)
    sandbox["deleted_nodes"] = sorted(deleted_nodes)

    deleted_edges = set(sandbox["deleted_edges"])
    for edge in list(sandbox["custom_edges"].values()):
        if edge.get("a") == node_id or edge.get("b") == node_id:
            deleted_edges.add(edge_id(edge["a"], edge["b"]))
    for a, b in _EDGES:
        if a == node_id or b == node_id:
            deleted_edges.add(edge_id(a, b))
    sandbox["custom_edges"] = {
        eid: item
        for eid, item in sandbox["custom_edges"].items()
        if item.get("a") != node_id and item.get("b") != node_id
    }
    sandbox["deleted_edges"] = sorted(deleted_edges)
    _save_sandbox(db, sandbox)
    return sandbox_snapshot(db)


def add_edge(
    db: Session,
    a: str,
    b: str,
    status: str = "normal",
    multiplier: float | None = None,
) -> dict[str, Any]:
    if a == b:
        raise ValueError("edge endpoints must differ")
    nodes = _effective_nodes(db)
    if a not in nodes or b not in nodes:
        raise ValueError("unknown edge endpoint")
    if status not in STATUS_MULTIPLIER:
        raise ValueError("status must be normal, slow, or closed")
    sandbox = _load_sandbox(db)
    eid = edge_id(a, b)
    sandbox["custom_edges"][eid] = {
        "a": a,
        "b": b,
        "status": status,
        "multiplier": max(float(multiplier or STATUS_MULTIPLIER.get(status) or 1.0), 1.0),
    }
    deleted = set(sandbox["deleted_edges"])
    deleted.discard(eid)
    sandbox["deleted_edges"] = sorted(deleted)
    _save_sandbox(db, sandbox)
    return sandbox_snapshot(db)


def reset_sandbox(db: Session) -> dict[str, Any]:
    _save_sandbox(db, _default_sandbox())
    return sandbox_snapshot(db)
