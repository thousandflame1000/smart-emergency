"""Dataset-independent topology documents, import adapters and graph analysis."""

from __future__ import annotations

import csv
import io
import json
from typing import Any, Literal
from uuid import uuid4

import networkx as nx
from pydantic import BaseModel, Field, model_validator

from app.services.hazard import haversine_km


class Node(BaseModel):
    id: str = Field(min_length=1, max_length=180)
    label: str = Field(default="未命名", max_length=300)
    kind: Literal["road_node", "person", "supply", "facility", "custom"] = "custom"
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    quantity: float = Field(default=1, ge=0, le=1e9, allow_inf_nan=False)
    available: bool = True
    source: str = Field(default="手動建立", max_length=500)
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="after")
    def coordinate_pair(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("經緯度必須成對提供")
        return self


class Edge(BaseModel):
    id: str = Field(min_length=1, max_length=180)
    source: str
    target: str
    kind: Literal["road", "access", "assignment", "supplies", "custom"] = "custom"
    label: str = Field(default="連線", max_length=300)
    status: Literal["normal", "slow", "closed"] = "normal"
    directed: bool = False
    speed_kph: float = Field(default=30, gt=0, le=300, allow_inf_nan=False)
    multiplier: float = Field(default=2.5, ge=1, le=100, allow_inf_nan=False)
    provenance: str = Field(default="手動建立", max_length=500)
    properties: dict[str, Any] = Field(default_factory=dict)


class GraphDocument(BaseModel):
    nodes: list[Node] = Field(default_factory=list, max_length=12000)
    edges: list[Edge] = Field(default_factory=list, max_length=24000)

    @model_validator(mode="after")
    def valid_graph(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes) or len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("物件或連線的識別碼重複")
        for e in self.edges:
            if e.source not in ids or e.target not in ids:
                raise ValueError(f"連線 {e.id} 指向不存在的物件")
            if e.source == e.target:
                raise ValueError(f"連線 {e.id} 的起點與終點相同")
        json.dumps(self.model_dump(), allow_nan=False)
        return self


class ImportRequest(BaseModel):
    format: Literal["geojson", "csv", "json", "osm"]
    content: str = Field(max_length=5_000_000)
    kind: Literal["road_node", "person", "supply", "facility", "custom", "relations"] = "custom"
    source: str = Field(default="匯入資料", max_length=500)
    mapping: dict[str, str] = Field(default_factory=dict)
    base: GraphDocument = Field(default_factory=GraphDocument)


def import_document(request: ImportRequest) -> dict:
    nodes, edges = [], []
    warnings = []
    prefix = uuid4().hex[:10]
    mapping = request.mapping

    def json_object():
        data = json.loads(request.content)
        if not isinstance(data, dict):
            raise ValueError("JSON 頂層必須為物件")
        return data

    def field(props, name, default=None):
        return props.get(mapping.get(name, name), default)

    def point(props, index, coords=None, feature_id=None):
        lat, lng = (coords[1], coords[0]) if coords else (field(props, "lat"), field(props, "lng"))
        if lat in (None, "") and lng in (None, ""):
            lat = lng = None
        elif lat in (None, "") or lng in (None, ""):
            raise ValueError(f"第 {index + 1} 筆資料缺少經緯度之一")
        raw_id = field(props, "id", feature_id)
        nodes.append(Node(
            id=str(raw_id) if raw_id not in (None, "") else f"{prefix}:n:{index}",
            label=str(field(props, "label", field(props, "name", f"物件 {index + 1}"))),
            kind=request.kind if request.kind != "relations" else "custom",
            lat=lat, lng=lng, quantity=field(props, "quantity", 1) or 0,
            source=request.source, properties=props,
        ))

    if request.format == "csv":
        reader = csv.DictReader(io.StringIO(request.content.lstrip("\ufeff")))
        if not reader.fieldnames:
            raise ValueError("CSV 缺少標題列")
        if len(set(reader.fieldnames)) != len(reader.fieldnames):
            raise ValueError("CSV 欄位名稱重複，請先改為不同名稱")
        for index, row in enumerate(reader):
            if index >= 24000:
                raise ValueError("CSV 超過 24,000 筆上限，請分區匯入")
            if None in row:
                raise ValueError(f"第 {index + 2} 列的欄位數與標題不符")
            if request.kind == "relations":
                edges.append(Edge(
                    id=str(field(row, "id") or f"{prefix}:e:{index}"),
                    source=str(field(row, "source", "")), target=str(field(row, "target", "")),
                    label=str(field(row, "label", "關係")),
                    kind=field(row, "kind", "custom") or "custom",
                    provenance=request.source, properties=row,
                ))
            else:
                point(row, index)
    elif request.format == "json":
        data = json_object()
        document = GraphDocument.model_validate(data.get("graph", data))
        nodes, edges = document.nodes, document.edges
    elif request.format == "osm":
        data = json_object()
        if data.get("remark"):
            raise ValueError("OpenStreetMap 查詢未完整完成，請縮小範圍重試")
        elements = data.get("elements", [])
        by_id = {str(n["id"]): n for n in elements if n.get("type") == "node"}
        used = set()
        for way in elements:
            if way.get("type") != "way":
                continue
            tags = way.get("tags", {})
            if not tags.get("highway"):
                continue
            refs = [str(n) for n in way.get("nodes", [])]
            for i, (a, b) in enumerate(zip(refs, refs[1:])):
                if a not in by_id or b not in by_id:
                    raise ValueError("道路缺少共用節點，請重新載入完整 OSM 資料")
                if a == b:
                    continue
                used.update([a, b])
                if tags.get("oneway") == "-1":
                    a, b = b, a
                edges.append(Edge(
                    id=f"osm:way:{way['id']}:{i}", source=f"osm:node:{a}", target=f"osm:node:{b}",
                    kind="road", label=tags.get("name", tags.get("highway", "道路")),
                    directed=tags.get("oneway") in ("yes", "1", "true", "-1") or (
                        tags.get("junction") == "roundabout" and tags.get("oneway") != "no"),
                    provenance=request.source, properties=tags,
                ))
        nodes = [Node(id=f"osm:node:{n}", label=f"道路節點 {n}", kind="road_node",
                      lat=by_id[n]["lat"], lng=by_id[n]["lon"], source=request.source) for n in sorted(used)]
        categories = {
            "hospital": "醫院", "clinic": "診所", "pharmacy": "藥局", "fire_station": "消防站",
            "police": "警政據點", "school": "學校", "community_centre": "社區中心",
            "social_facility": "社福設施", "shelter": "庇護設施", "supermarket": "超市", "convenience": "便利商店",
        }
        facilities = {}
        for item in elements:
            tags = item.get("tags", {})
            category = tags.get("amenity") or tags.get("shop")
            if category not in categories:
                continue
            location = item if item.get("type") == "node" else item.get("center", {})
            if "lat" not in location or "lon" not in location:
                continue
            node_id = f"osm:facility:{item['type']}:{item['id']}"
            facilities[node_id] = Node(
                id=node_id, label=tags.get("name:zh") or tags.get("name") or categories[category],
                kind="facility", lat=location["lat"], lng=location["lon"], quantity=0, source=request.source,
                properties={**tags, "facility_category": categories[category], "operational_status": "unknown",
                            "capacity_known": False, "location_method": "point" if item["type"] == "node" else "bbox_center"},
            )
        nodes.extend(facilities.values())
        if facilities:
            warnings.append("公開設施僅代表地圖位置；營運狀態、可用容量與物資庫存尚未提供。")
        warnings.append("保留 OSM 共用節點與單行方向；預設路速 30 公里／時，可逐路調整。")
    else:
        data = json_object()
        if data.get("crs") and "CRS84" not in json.dumps(data["crs"]) and "4326" not in json.dumps(data["crs"]):
            raise ValueError("請先將座標轉換為 WGS84（EPSG:4326）")
        features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
        road_nodes = {}
        skipped = 0
        for i, feature in enumerate(features):
            if not isinstance(feature, dict):
                raise ValueError("GeoJSON 的每筆圖徵必須為物件")
            geo = feature.get("geometry") or {}
            props = feature.get("properties") or {}
            if not isinstance(geo, dict) or not isinstance(props, dict):
                raise ValueError("GeoJSON 的 geometry 與 properties 格式不正確")
            coords = geo.get("coordinates")
            if geo.get("type") == "Point" and isinstance(coords, list) and len(coords) >= 2:
                point(props, i, coords, feature.get("id"))
            elif geo.get("type") in ("LineString", "MultiLineString"):
                lines = [coords] if geo["type"] == "LineString" else coords
                for j, line in enumerate(lines or []):
                    previous = None
                    for k, coord in enumerate(line):
                        # Only explicit shared vertices at the same level are joined.
                        key = (round(float(coord[0]), 7), round(float(coord[1]), 7), str(props.get("layer", 0)))
                        if key not in road_nodes:
                            node_id = f"{prefix}:r:{len(road_nodes)}"
                            road_nodes[key] = node_id
                            nodes.append(Node(id=node_id, label=f"道路節點 {len(road_nodes)}", kind="road_node",
                                              lng=coord[0], lat=coord[1], source=request.source))
                        current = road_nodes[key]
                        if previous and previous != current:
                            reverse = str(props.get("oneway")) == "-1"
                            edges.append(Edge(
                                id=f"{prefix}:e:{i}:{j}:{k}", source=current if reverse else previous,
                                target=previous if reverse else current, kind="road",
                                label=str(field(props, "label", props.get("name", "道路"))),
                                directed=str(props.get("oneway", "")).lower() in ("yes", "true", "1", "-1"),
                                provenance=request.source, properties=props,
                            ))
                        previous = current
            else:
                skipped += 1
        if skipped:
            warnings.append(f"略過 {skipped} 筆不支援的幾何；目前支援點與道路線段。")
        warnings.append("道路僅在共用座標頂點且 layer 相同時相連；幾何交叉不自動建立路口。")
    if not nodes and not edges:
        raise ValueError("資料中沒有可匯入的點或連線")
    merged_nodes = {n.id: n for n in request.base.nodes}
    merged_edges = {e.id: e for e in request.base.edges}
    for collection, incoming in ((merged_nodes, nodes), (merged_edges, edges)):
        for item in incoming:
            if item.id in collection:
                if item.id.startswith("osm:"):
                    continue  # Overlapping OSM tiles retain local edits.
                raise ValueError(f"識別碼重複：{item.id}，請改用取代或調整識別碼")
            collection[item.id] = item
    result = GraphDocument(nodes=list(merged_nodes.values()), edges=list(merged_edges.values()))
    return {"graph": result.model_dump(), "warnings": warnings,
            "added_nodes": len(result.nodes) - len(request.base.nodes),
            "added_edges": len(result.edges) - len(request.base.edges)}


def analyze(document: GraphDocument, start: str | None = None, end: str | None = None) -> dict:
    nodes = {n.id: n for n in document.nodes}
    graph = nx.MultiDiGraph()
    graph.add_nodes_from(n.id for n in document.nodes if n.available)
    road_graph = nx.MultiGraph()
    road_graph.add_nodes_from(n.id for n in document.nodes if n.kind == "road_node" and n.available)
    missing_coordinates = []
    for edge in document.edges:
        if edge.kind not in ("road", "access") or edge.status == "closed":
            continue
        a, b = nodes[edge.source], nodes[edge.target]
        if not a.available or not b.available:
            continue
        if a.lat is None or b.lat is None:
            missing_coordinates.append(edge.id)
            continue
        km = haversine_km(a.lat, a.lng, b.lat, b.lng)
        minutes = km / edge.speed_kph * 60 * (edge.multiplier if edge.status == "slow" else 1)
        graph.add_edge(a.id, b.id, key=edge.id, weight=minutes, km=km)
        if not edge.directed:
            graph.add_edge(b.id, a.id, key=edge.id, weight=minutes, km=km)
        if edge.kind == "road":
            road_graph.add_edge(a.id, b.id, key=edge.id)
    bridges = [next(iter(road_graph[a][b])) for a, b in nx.bridges(road_graph)]
    supplies = [n.id for n in document.nodes if n.kind in ("supply", "facility") and n.available and n.quantity > 0]
    reached = nx.multi_source_dijkstra_path_length(graph, supplies) if supplies else {}
    people = [n for n in document.nodes if n.kind == "person" and n.available]
    isolated = [n.id for n in people if n.id not in reached]
    route = None
    if start or end:
        if start not in nodes or end not in nodes:
            raise ValueError("請選擇有效的起點與終點")
        route = {"reachable": False, "nodes": [], "edges": [], "minutes": None, "km": None}
        if start in graph and end in graph and nx.has_path(graph, start, end):
            path = nx.shortest_path(graph, start, end, weight="weight")
            chosen = [min(graph[a][b].items(), key=lambda item: item[1]["weight"]) for a, b in zip(path, path[1:])]
            route = {"reachable": True, "nodes": path, "edges": [key for key, _ in chosen],
                     "minutes": round(sum(v["weight"] for _, v in chosen), 2),
                     "km": round(sum(v["km"] for _, v in chosen), 3)}
    return {
        "metrics": {"nodes": len(nodes), "edges": len(document.edges), "people": len(people),
                    "supply_quantity": sum(n.quantity for n in document.nodes if n.kind == "supply" and n.available),
                    "road_components": nx.number_connected_components(road_graph),
                    "critical_roads": len(bridges), "unreachable_people": len(isolated)},
        "critical_edges": bridges, "articulation_nodes": list(nx.articulation_points(road_graph)),
        "unreachable_people": isolated, "route": route,
        "ignored_edges": missing_coordinates,
        "assumptions": ["可達性依道路與接駁連線計算，遵守單行方向；供應與指派關係不視為道路。",
                        "僅分析已匯入的路網；範圍外道路未納入，邊界可能截斷可達路徑。",
                        "瓶頸為忽略方向後的橋接邊與割點；不代表真實道路損壞機率。",
                        "物資可達不等於足量或品項相符，未執行配給；接駁為人工假設。",
                        "時間依端點距離、路速與延遲倍率估算，未接入即時交通。"],
    }
