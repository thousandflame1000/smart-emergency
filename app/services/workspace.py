"""Dataset-independent topology documents, import adapters and graph analysis."""

from __future__ import annotations

import csv
import io
import json
from datetime import UTC, datetime
from typing import Any, Literal
from uuid import uuid4

import networkx as nx
from pydantic import BaseModel, Field, field_validator, model_validator

class LogisticsRecord(BaseModel):
    id: str = Field(min_length=1, max_length=180)
    role: Literal["supply", "demand"]
    item: str = Field(min_length=1, max_length=80)
    unit: str = Field(min_length=1, max_length=40)
    quantity: int = Field(ge=0, le=1_000_000)
    priority: int = Field(default=3, ge=1, le=5)
    dispatch_limit: int | None = Field(default=None, ge=0, le=1_000_000)
    source: str = Field(default="手動填報（未核實）", max_length=500)

    @field_validator("item", "unit", mode="before")
    @classmethod
    def trim_text(cls, value):
        return value.strip() if isinstance(value, str) else value

    @field_validator("quantity", "priority", "dispatch_limit", mode="before")
    @classmethod
    def reject_boolean(cls, value):
        if isinstance(value, bool):
            raise ValueError("數量、上限與優先級不可為布林值")
        return value


class Node(BaseModel):
    id: str = Field(min_length=1, max_length=180)
    label: str = Field(default="未命名", max_length=300)
    kind: Literal["person", "supply", "facility", "incident", "custom"] = "custom"
    lat: float | None = Field(default=None, ge=-90, le=90, allow_inf_nan=False)
    lng: float | None = Field(default=None, ge=-180, le=180, allow_inf_nan=False)
    quantity: float = Field(default=1, ge=0, le=1e9, allow_inf_nan=False)
    available: bool = True
    source: str = Field(default="手動建立", max_length=500)
    properties: dict[str, Any] = Field(default_factory=dict)
    logistics: list[LogisticsRecord] = Field(default_factory=list, max_length=30)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_kind(cls, value):
        if isinstance(value, dict) and value.get("kind") == "road_node":
            value = dict(value)
            properties = dict(value.get("properties") or {})
            properties.setdefault("legacy_kind", "road_node")
            value.update(kind="custom", properties=properties)
        return value

    @model_validator(mode="after")
    def coordinate_pair(self):
        if (self.lat is None) != (self.lng is None):
            raise ValueError("經緯度必須成對提供")
        return self


class Edge(BaseModel):
    id: str = Field(min_length=1, max_length=180)
    source: str
    target: str
    kind: Literal["assignment", "supplies", "care", "request", "related", "custom"] = "custom"
    label: str = Field(default="連線", max_length=300)
    status: Literal["active", "inactive"] = "active"
    directed: bool = False
    provenance: str = Field(default="手動建立", max_length=500)
    properties: dict[str, Any] = Field(default_factory=dict)

    @model_validator(mode="before")
    @classmethod
    def normalize_legacy_edge(cls, value):
        if not isinstance(value, dict):
            return value
        value = dict(value)
        if value.get("kind") in ("road", "access"):
            properties = dict(value.get("properties") or {})
            properties.setdefault("legacy_kind", value["kind"])
            value.update(kind="related", properties=properties)
        legacy_status = value.get("status")
        if legacy_status in ("normal", "slow"):
            value["status"] = "active"
        elif legacy_status == "closed":
            value["status"] = "inactive"
        return value


class GraphDocument(BaseModel):
    nodes: list[Node] = Field(default_factory=list, max_length=12000)
    edges: list[Edge] = Field(default_factory=list, max_length=24000)

    @model_validator(mode="after")
    def valid_graph(self):
        ids = {n.id for n in self.nodes}
        if len(ids) != len(self.nodes) or len({e.id for e in self.edges}) != len(self.edges):
            raise ValueError("物件或連線的識別碼重複")
        logistics_ids = [line.id for node in self.nodes for line in node.logistics]
        if len(logistics_ids) > 2000 or len(set(logistics_ids)) != len(logistics_ids):
            raise ValueError("物資紀錄超過 2,000 筆或識別碼重複")
        for e in self.edges:
            if e.source not in ids or e.target not in ids:
                raise ValueError(f"連線 {e.id} 指向不存在的物件")
            if e.source == e.target:
                raise ValueError(f"連線 {e.id} 的起點與終點相同")
        json.dumps(self.model_dump(), allow_nan=False)
        return self


class ComparisonBaseline(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    graph: GraphDocument
    captured_at: datetime = Field(default_factory=lambda: datetime.now(UTC))
    workspace_id: str | None = Field(default=None, max_length=180)
    revision: int = Field(default=0, ge=0)
    unsaved: bool = False


class ImportRequest(BaseModel):
    format: Literal["geojson", "csv", "json"]
    content: str = Field(max_length=5_000_000)
    kind: Literal["person", "supply", "facility", "incident", "custom", "relations"] = "custom"
    source: str = Field(default="匯入資料", max_length=500)
    mapping: dict[str, str] = Field(default_factory=dict)
    base: GraphDocument = Field(default_factory=GraphDocument)


def import_document(request: ImportRequest) -> dict:
    nodes, edges = [], []
    baseline = None
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
        logistics = []
        item, unit, quantity = field(props, "item"), field(props, "unit"), field(props, "quantity")
        if request.kind in ("supply", "person") and (item not in (None, "") or unit not in (None, "")):
            if item in (None, "") or unit in (None, "") or quantity in (None, ""):
                raise ValueError(f"第 {index + 1} 筆物資資料須同時提供品項、單位與明確數量")
            priority = field(props, "priority", 3)
            dispatch_limit = field(props, "dispatch_limit")
            logistics = [LogisticsRecord(id=f"{prefix}:logistics:{index}",
                role="supply" if request.kind == "supply" else "demand", item=item, unit=unit,
                quantity=quantity, priority=3 if priority in (None, "") else priority,
                dispatch_limit=None if dispatch_limit in (None, "") else dispatch_limit, source=request.source)]
        nodes.append(Node(
            id=str(raw_id) if raw_id not in (None, "") else f"{prefix}:n:{index}",
            label=str(field(props, "label", field(props, "name", f"物件 {index + 1}"))),
            kind=request.kind if request.kind != "relations" else "custom",
            lat=lat, lng=lng, quantity=field(props, "quantity", 1) or 0,
            source=request.source, properties=props, logistics=logistics,
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
        if data.get("baseline") is not None:
            baseline = ComparisonBaseline.model_validate(data["baseline"]).model_dump(mode="json")
    else:
        data = json_object()
        if data.get("crs") and "CRS84" not in json.dumps(data["crs"]) and "4326" not in json.dumps(data["crs"]):
            raise ValueError("請先將座標轉換為 WGS84（EPSG:4326）")
        features = data.get("features", []) if data.get("type") == "FeatureCollection" else [data]
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
            else:
                skipped += 1
        if skipped:
            warnings.append(f"略過 {skipped} 筆非點資料；關係請以 CSV 或工作區 JSON 明確匯入。")
    if not nodes and not edges:
        raise ValueError("資料中沒有可匯入的點或連線")
    merged_nodes = {n.id: n for n in request.base.nodes}
    merged_edges = {e.id: e for e in request.base.edges}
    for collection, incoming in ((merged_nodes, nodes), (merged_edges, edges)):
        for item in incoming:
            if item.id in collection:
                raise ValueError(f"識別碼重複：{item.id}，請改用取代或調整識別碼")
            collection[item.id] = item
    result = GraphDocument(nodes=list(merged_nodes.values()), edges=list(merged_edges.values()))
    return {"graph": result.model_dump(), "baseline": baseline, "warnings": warnings,
            "added_nodes": len(result.nodes) - len(request.base.nodes),
            "added_edges": len(result.edges) - len(request.base.edges)}


def has_supply(node: Node) -> bool:
    if not node.available:
        return False
    if node.logistics:
        return any(line.role == "supply" and line.quantity > 0 and line.dispatch_limit != 0 for line in node.logistics)
    return node.kind == "supply" and node.quantity > 0


def analyze(document: GraphDocument) -> dict:
    nodes = {n.id: n for n in document.nodes}
    active = {n.id for n in document.nodes if n.available}
    graph = nx.Graph()
    graph.add_nodes_from(active)
    pair_edges: dict[tuple[str, str], list[str]] = {}
    inactive_edges = []
    for edge in document.edges:
        if edge.status != "active" or edge.source not in active or edge.target not in active:
            inactive_edges.append(edge.id)
            continue
        graph.add_edge(edge.source, edge.target)
        pair = tuple(sorted((edge.source, edge.target)))
        pair_edges.setdefault(pair, []).append(edge.id)
    bridges = [pair_edges[tuple(sorted(pair))][0] for pair in nx.bridges(graph)
               if len(pair_edges[tuple(sorted(pair))]) == 1]
    unlinked = sorted(node_id for node_id, degree in graph.degree if degree == 0)
    people = [n for n in document.nodes if n.kind == "person" and n.available]
    demands = [n for n in document.nodes if n.properties.get("db") == "need"
               and n.properties.get("status") in ("open", "suggested")]
    return {
        "metrics": {"nodes": len(nodes), "edges": len(document.edges), "people": len(people),
                    "available_supplies": sum(has_supply(n) for n in document.nodes),
                    "open_demands": len(demands),
                    "components": nx.number_connected_components(graph) if graph else 0,
                    "unlinked_objects": len(unlinked), "inactive_relations": len(inactive_edges)},
        "critical_edges": bridges,
        "articulation_nodes": sorted(nx.articulation_points(graph)),
        "unlinked_objects": unlinked,
        "inactive_relations": inactive_edges,
        "assumptions": ["關聯分析只使用目前工作區內、啟用中的物件與關係。",
                        "橋接關係與關鍵物件表示資料圖上的單點依賴，不代表道路、通訊或現場一定中斷。",
                        "未連結物件可能是資料尚未補齊，也可能本來就應獨立存在，需由操作人員確認。",
                        "分析不會修改正式資料、扣除庫存、建立派遣或發送通知。"],
    }
