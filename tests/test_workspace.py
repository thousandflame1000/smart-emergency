import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models.config import SystemConfig
from app.services.workspace import Edge, GraphDocument, ImportRequest, Node, analyze, import_document


def network():
    return GraphDocument(nodes=[
        Node(id="depot", kind="supply", lat=35.0, lng=139.0, quantity=10),
        Node(id="a", kind="road_node", lat=35.0, lng=139.001),
        Node(id="b", kind="road_node", lat=35.0, lng=139.01),
        Node(id="person", kind="person", lat=35.0, lng=139.011),
    ], edges=[
        Edge(id="entry", source="depot", target="a", kind="access"),
        Edge(id="road", source="a", target="b", kind="road", directed=True),
        Edge(id="exit", source="b", target="person", kind="access"),
    ])


def test_arbitrary_region_routing_obeys_direction_closures_and_supply():
    graph = network()
    result = analyze(graph, "depot", "person")
    assert result["route"]["reachable"]
    assert result["route"]["edges"] == ["entry", "road", "exit"]
    assert result["metrics"]["unreachable_people"] == 0
    assert result["critical_edges"] == ["road"]
    assert not analyze(graph, "person", "depot")["route"]["reachable"]
    graph.edges[1].status = "slow"
    assert analyze(graph, "depot", "person")["route"]["minutes"] > result["route"]["minutes"]
    graph.edges[1].status = "closed"
    result = analyze(graph, "depot", "person")
    assert not result["route"]["reachable"]
    assert result["unreachable_people"] == ["person"]
    assert result["metrics"]["road_components"] == 2


def test_parallel_roads_are_not_false_bridges_and_relations_are_not_roads():
    graph = network()
    graph.edges.append(Edge(id="parallel", source="a", target="b", kind="road"))
    assert analyze(graph)["critical_edges"] == []
    graph.edges[1].status = graph.edges[3].status = "closed"
    graph.edges.append(Edge(id="assignment", source="depot", target="person", kind="supplies"))
    assert analyze(graph)["unreachable_people"] == ["person"]


def test_unlocated_unavailable_or_empty_supply_does_not_imply_coverage():
    graph = network()
    graph.nodes[0].quantity = 0
    assert analyze(graph)["unreachable_people"] == ["person"]
    graph.nodes[0].quantity = 1
    graph.nodes[1].available = False
    assert analyze(graph)["unreachable_people"] == ["person"]
    graph.nodes[1].available = True
    graph.nodes[1].lat = graph.nodes[1].lng = None
    assert set(analyze(graph)["ignored_edges"]) == {"entry", "road"}


def test_csv_chinese_mapping_quotes_sources_and_no_coordinates():
    result = import_document(ImportRequest(format="csv", kind="supply", source="開放物資清冊",
        content='編號,名稱,緯度,經度,庫存\nstock,"飲水,備用",35,139,12\nmobile,行動補給,,,4\n',
        mapping={"id":"編號", "label":"名稱", "lat":"緯度", "lng":"經度", "quantity":"庫存"}))
    assert result["graph"]["nodes"][0]["label"] == "飲水,備用"
    assert result["graph"]["nodes"][0]["quantity"] == 12
    assert result["graph"]["nodes"][0]["source"] == "開放物資清冊"
    assert result["graph"]["nodes"][1]["lat"] is None
    assert result["added_nodes"] == 2


def test_geojson_vertices_layers_multilines_and_wgs84():
    data = {"type":"FeatureCollection", "features":[
        {"type":"Feature", "properties":{"name":"路一"}, "geometry":{"type":"LineString", "coordinates":[[139,35],[139.01,35],[139.02,35]]}},
        {"type":"Feature", "properties":{"name":"路二"}, "geometry":{"type":"MultiLineString", "coordinates":[[[139.01,35],[139.01,35.01]]]}},
        {"type":"Feature", "properties":{"name":"高架", "layer":1}, "geometry":{"type":"LineString", "coordinates":[[139.01,35],[139.01,35.01]]}},
    ]}
    result = import_document(ImportRequest(format="geojson", content=json.dumps(data)))
    assert len(result["graph"]["nodes"]) == 6
    assert analyze(GraphDocument.model_validate(result["graph"]))["metrics"]["road_components"] == 2
    data["crs"] = {"properties":{"name":"EPSG:3826"}}
    with pytest.raises(ValueError, match="WGS84"):
        import_document(ImportRequest(format="geojson", content=json.dumps(data)))


def test_osm_shared_ids_reverse_oneway_overlap_preserves_local_edits():
    data = {"elements":[
        {"type":"node", "id":1, "lat":51, "lon":0}, {"type":"node", "id":2, "lat":51, "lon":0.01},
        {"type":"way", "id":10, "nodes":[1,2], "tags":{"oneway":"-1", "name":"道路"}},
    ]}
    request = ImportRequest(format="osm", content=json.dumps(data))
    result = import_document(request)
    graph = GraphDocument.model_validate(result["graph"])
    assert graph.edges[0].source == "osm:node:2"
    assert graph.edges[0].directed
    graph.edges[0].status = "closed"
    request.base = graph
    result = import_document(request)
    assert result["added_edges"] == result["added_nodes"] == 0
    assert result["graph"]["edges"][0]["status"] == "closed"


def test_relationship_csv_resolves_existing_objects_and_rejects_missing_endpoints():
    base = GraphDocument(nodes=[Node(id="a"), Node(id="b")])
    request = ImportRequest(format="csv", kind="relations", base=base, content="source,target,label\na,b,負責\n")
    result = import_document(request)
    assert result["graph"]["edges"][0]["label"] == "負責"
    request.content = "source,target\na,missing\n"
    with pytest.raises(ValidationError):
        import_document(request)


def test_graph_validation_and_export_roundtrip():
    graph = network()
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
    graph = network().model_dump()
    first = client.post('/api/workspaces', json={"name":"東京資料", "graph":graph})
    assert first.status_code == 201
    first = first.json()
    second = client.post('/api/workspaces', json={"name":"空白資料"}).json()
    write = {"name":"修訂名稱", "graph":graph, "revision":1}
    assert client.put('/api/workspaces/'+first['id'], json=write).json()['revision'] == 2
    assert client.put('/api/workspaces/'+first['id'], json=write).status_code == 409
    assert client.get('/api/workspaces/'+second['id']).json()['graph'] == {"nodes":[],"edges":[]}
    assert db.query(SystemConfig).count() == 0
    assert len(client.get('/api/workspaces').json()) == 2


def test_invalid_import_and_analysis_are_non_mutating(db):
    client = TestClient(app)
    assert client.post('/api/workspaces/import-preview', json={"format":"geojson","content":"invalid"}).status_code == 400
    assert client.post('/api/workspaces/analyze', json={"graph":network().model_dump()}).status_code == 200
    assert client.post('/api/workspaces/openstreetmap', json={"south":20,"north":30,"west":120,"east":121}).status_code == 400
    assert client.get('/api/workspaces').json() == []


@pytest.mark.parametrize("content", ["[]", "null", '"text"'])
def test_import_rejects_non_object_json(content):
    assert TestClient(app).post('/api/workspaces/import-preview', json={"format":"geojson", "content":content}).status_code == 400


def test_online_roads_falls_back_without_forwarding_workspace_data(monkeypatch):
    import io
    from urllib.error import URLError

    calls = []

    def fake_open(request, timeout):
        calls.append(request)
        if len(calls) == 1:
            raise URLError("upstream unavailable")
        return io.BytesIO(json.dumps({"elements":[
            {"type":"node","id":1,"lat":35,"lon":139},
            {"type":"node","id":2,"lat":35.01,"lon":139},
            {"type":"way","id":3,"nodes":[1,2],"tags":{"highway":"residential"}},
        ]}).encode())

    monkeypatch.setattr('app.routers.workspace.urlopen', fake_open)
    response = TestClient(app).post('/api/workspaces/openstreetmap', json={
        "south":35,"north":35.01,"west":139,"east":139.01,
        "base":{"nodes":[{"id":"private-name","label":"private-name"}],"edges":[]},
    })
    assert response.status_code == 200
    assert response.json()["added_edges"] == 1
    assert response.json()["provider"] == "FOSSGIS"
    assert "FOSSGIS" in response.json()["graph"]["edges"][0]["provenance"]
    assert len(calls) == 2
    assert b"private-name" not in calls[1].data


def test_online_roads_reports_all_services_unavailable(monkeypatch):
    from urllib.error import URLError

    calls = []

    def offline(request, timeout):
        calls.append(request.full_url)
        raise URLError("unavailable")

    monkeypatch.setattr('app.routers.workspace.urlopen', offline)
    response = TestClient(app).post('/api/workspaces/openstreetmap', json={
        "south":35,"north":35.01,"west":139,"east":139.01,
    })
    assert response.status_code == 502
    assert len(calls) == 3
