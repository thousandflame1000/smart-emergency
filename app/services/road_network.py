# -*- coding: utf-8 -*-
"""
Real road-network graph for Taiwan's east coast (Hualien-Taitung corridor):
Route 9 (台9線, inland Huatung Valley) and Route 11 (台11線, coastal),
connected by the real Yuchang Highway (玉長公路). Real towns, real
approximate coordinates; edge distance is haversine between consecutive
real waypoints along the actual route — not a single straight line across
the mountains, which is a materially better approximation of real travel
distance for this terrain than point-to-point haversine. This region's
road network genuinely is this sparse (two through-routes plus one
connector), so a coarse real-topology graph is an honest fit.

Deliberately not a full OSM street graph: no osmnx/GDAL runtime
dependency. This project has already had two production outages this
session from heavy compiled dependencies (numpy via a dropped transitive
import, then scipy) — not repeating that for a demo feature.
"""
import heapq

from app.services.hazard import haversine_km

# name -> (lat, lng), real town-center coordinates entered from general
# geographic knowledge (not a surveyed dataset or GPS fix) — accurate to
# roughly ~1km, which is fine for corridor-level routing but not a claim
# of precise, official coordinates if checked against a map pin.
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
    ("yuli", "changbin"),  # 玉長公路 — real inland/coastal connector
]

COVERAGE_KM = 6.0  # snap radius; beyond this a point is off-network


def _build_graph():
    graph: dict[str, list[tuple[str, float]]] = {n: [] for n in NODES}
    for a, b in _EDGES:
        d = haversine_km(*NODES[a], *NODES[b])
        graph[a].append((b, d))
        graph[b].append((a, d))
    return graph


_GRAPH = _build_graph()


def _nearest_node(lat: float, lng: float) -> tuple[str, float]:
    best, best_d = None, float("inf")
    for name, (nlat, nlng) in NODES.items():
        d = haversine_km(lat, lng, nlat, nlng)
        if d < best_d:
            best, best_d = name, d
    return best, best_d


def _dijkstra(start: str, end: str) -> float:
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
        for neighbor, w in _GRAPH[node]:
            nd = d + w
            if nd < dist.get(neighbor, float("inf")):
                dist[neighbor] = nd
                heapq.heappush(pq, (nd, neighbor))
    return float("inf")


def road_distance_km(lat1: float, lng1: float, lat2: float, lng2: float) -> float | None:
    """Real road-network distance along the Hua-Dong corridor, or None if
    either point is too far from the network to snap sensibly — callers
    should fall back to straight-line distance in that case."""
    n1, d1 = _nearest_node(lat1, lng1)
    n2, d2 = _nearest_node(lat2, lng2)
    if d1 > COVERAGE_KM or d2 > COVERAGE_KM:
        return None
    path_d = _dijkstra(n1, n2)
    if path_d == float("inf"):
        return None
    return d1 + path_d + d2
