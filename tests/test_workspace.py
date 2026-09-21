import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models.config import SystemConfig
from app.services.workspace import Edge, GraphDocument, ImportRequest, Node, analyze, import_document


def event_graph():
    return GraphDocument(nodes=[
        Node(id="incident", label="停水事件", kind="incident", lat=24.0, lng=120.6),
        Node(id="resident", label="林秀英", kind="person", lat=24.01, lng=120.61),
        Node(id="need", label="飲用水需求", kind="custom", lat=24.01, lng=120.61,
             properties={"db": "need", "status": "open"}),
        Node(id="water", label="飲用水", kind="supply", lat=24.0, lng=120.6, quantity=10),
        Node(id="unlinked", label="待確認據點", kind="facility", lat=24.02, lng=120.62),
    ], edges=[
        Edge(id="focus", source="incident", target="need", kind="related", label="事件追蹤", directed=True),
        Edge(id="request", source="resident", target="need", kind="request", label="提出需求", directed=True),
        Edge(id="owner", source="resident", target="water", kind="supplies", label="持有", directed=True),
    ])


def test_relation_analysis_reports_data_completeness_without_route_claims():
    result = analyze(event_graph())
    assert result["metrics"] == {
        "nodes": 5, "edges": 3, "people": 1, "available_supplies": 1,
        "open_demands": 1, "components": 2, "unlinked_objects": 1, "inactive_relations": 0,
    }
    assert result["unlinked_objects"] == ["unlinked"]
    assert set(result["critical_edges"]) == {"focus", "request", "owner"}
    assert "route" not in result


def test_parallel_or_inactive_relations_are_handled_explicitly():
    graph = event_graph()
    graph.edges.append(Edge(id="focus-2", source="incident", target="need", kind="related"))
    assert "focus" not in analyze(graph)["critical_edges"]
    graph.edges[-1].status = "inactive"
    result = analyze(graph)
    assert result["inactive_relations"] == ["focus-2"]
    assert "focus" in result["critical_edges"]


def test_legacy_road_objects_are_loaded_as_generic_records():
    graph = GraphDocument.model_validate({
        "nodes": [{"id": "old-node", "kind": "road_node"}, {"id": "target"}],
        "edges": [{"id": "old-edge", "source": "old-node", "target": "target",
                   "kind": "road", "status": "closed", "speed_kph": 30}],
    })
    assert graph.nodes[0].kind == "custom"
    assert graph.nodes[0].properties["legacy_kind"] == "road_node"
    assert graph.edges[0].kind == "related"
    assert graph.edges[0].status == "inactive"
    assert graph.edges[0].properties["legacy_kind"] == "road"


def test_csv_chinese_mapping_quotes_sources_and_no_coordinates():
    result = import_document(ImportRequest(format="csv", kind="supply", source="開放物資清冊",
        content='編號,名稱,緯度,經度,庫存\nstock,"飲水,備用",35,139,12\nmobile,行動補給,,,4\n',
        mapping={"id":"編號", "label":"名稱", "lat":"緯度", "lng":"經度", "quantity":"庫存"}))
    assert result["graph"]["nodes"][0]["label"] == "飲水,備用"
    assert result["graph"]["nodes"][0]["quantity"] == 12
    assert result["graph"]["nodes"][0]["source"] == "開放物資清冊"
    assert result["graph"]["nodes"][1]["lat"] is None
    assert result["added_nodes"] == 2


def test_geojson_accepts_points_and_skips_untyped_geometry():
    data = {"type":"FeatureCollection", "features":[
        {"type":"Feature", "properties":{"name":"活動中心"}, "geometry":{"type":"Point", "coordinates":[121,25]}},
        {"type":"Feature", "properties":{"name":"線資料"}, "geometry":{"type":"LineString", "coordinates":[[121,25],[121.01,25]]}},
    ]}
    result = import_document(ImportRequest(format="geojson", kind="facility", content=json.dumps(data)))
    assert result["added_nodes"] == 1
    assert result["graph"]["nodes"][0]["label"] == "活動中心"
    assert result["warnings"] == ["略過 1 筆非點資料；關係請以 CSV 或工作區 JSON 明確匯入。"]
    data["crs"] = {"properties":{"name":"EPSG:3826"}}
    with pytest.raises(ValueError, match="WGS84"):
        import_document(ImportRequest(format="geojson", content=json.dumps(data)))


def test_relationship_csv_resolves_existing_objects_and_rejects_missing_endpoints():
    base = GraphDocument(nodes=[Node(id="a"), Node(id="b")])
    request = ImportRequest(format="csv", kind="relations", base=base, content="source,target,label,kind\na,b,負責,assignment\n")
    result = import_document(request)
    assert result["graph"]["edges"][0]["label"] == "負責"
    request.content = "source,target\na,missing\n"
    with pytest.raises(ValidationError):
        import_document(request)


def test_graph_validation_and_export_roundtrip():
    graph = event_graph()
    result = import_document(ImportRequest(format="json", content=json.dumps({"graph":graph.model_dump()})))
    assert result["graph"] == graph.model_dump()
    with pytest.raises(ValidationError):
        GraphDocument(nodes=[Node(id="a"), Node(id="a")])
    with pytest.raises(ValidationError):
        Node(id="a", lat=float("nan"), lng=139)
    with pytest.raises(ValidationError):
        Node(id="a", lat=20)


def test_workspace_persistence_isolation_and_conflict(db):
    client = TestClient(app)
    graph = event_graph().model_dump()
    first = client.post('/api/workspaces', json={"name":"事件資料", "graph":graph})
    assert first.status_code == 201
    first = first.json()
    second = client.post('/api/workspaces', json={"name":"空白資料"}).json()
    write = {"name":"修訂名稱", "graph":graph, "revision":1}
    assert client.put('/api/workspaces/'+first['id'], json=write).json()['revision'] == 2
    assert client.put('/api/workspaces/'+first['id'], json=write).status_code == 409
    assert client.get('/api/workspaces/'+second['id']).json()['graph'] == {"nodes":[],"edges":[]}
    assert db.query(SystemConfig).count() == 0
    assert len(client.get('/api/workspaces').json()) == 2


def test_invalid_import_analysis_and_removed_road_endpoint_are_non_mutating(db):
    client = TestClient(app)
    assert client.post('/api/workspaces/import-preview', json={"format":"geojson","content":"invalid"}).status_code == 400
    assert client.post('/api/workspaces/analyze', json={"graph":event_graph().model_dump()}).status_code == 200
    assert "/api/workspaces/openstreetmap" not in app.openapi()["paths"]
    assert client.get('/api/workspaces').json() == []


@pytest.mark.parametrize("content", ["[]", "null", '"text"'])
def test_import_rejects_non_object_json(content):
    assert TestClient(app).post('/api/workspaces/import-preview', json={"format":"geojson", "content":content}).status_code == 400
