import json

from fastapi.testclient import TestClient

from app.main import app
from app.models.workspace import TopologyWorkspace
from app.services.workspace import ComparisonBaseline, Edge, GraphDocument, ImportRequest, Node, import_document
from app.services.workspace_comparison import fingerprint


def event_graph():
    return GraphDocument(nodes=[
        Node(id="incident", label="停水事件", kind="incident"),
        Node(id="need", label="飲用水需求", kind="custom", properties={"db":"need", "status":"open"}),
        Node(id="person", label="居民", kind="person"),
    ], edges=[
        Edge(id="focus", source="incident", target="need", kind="related", label="事件追蹤"),
        Edge(id="request", source="person", target="need", kind="request", label="提出需求"),
    ])


def test_reordering_and_layout_do_not_change_fingerprints():
    base = event_graph()
    graph = base.model_copy(deep=True)
    graph.nodes.reverse()
    graph.edges.reverse()
    graph.nodes[0].properties["_layout"] = {"x":0, "y":10}
    assert fingerprint(base) == fingerprint(graph)


def test_snapshot_persistence_normalizes_legacy_data_and_preserves_baseline(db):
    client = TestClient(app)
    legacy_document = {"nodes":[{"id":"old", "kind":"road_node"}, {"id":"target"}],
                       "edges":[{"id":"edge", "source":"old", "target":"target", "kind":"road"}]}
    db.add(TopologyWorkspace(id="legacy", name="舊工作區", document=json.dumps(legacy_document),
                             revision=2, updated_at="2026-09-14"))
    db.commit()
    legacy = client.get('/api/workspaces/legacy').json()
    assert legacy["graph"]["nodes"][0]["kind"] == "custom"
    assert legacy["graph"]["edges"][0]["kind"] == "related"

    base = event_graph()
    baseline = ComparisonBaseline(name="固定基準", graph=base, workspace_id="legacy", revision=2).model_dump(mode="json")
    changed = base.model_copy(deep=True)
    changed.edges[0].status = "inactive"
    saved = client.post('/api/workspaces', json={"name":"事件快照", "graph":changed.model_dump(), "baseline":baseline}).json()
    reopened = client.get('/api/workspaces/'+saved['id']).json()
    assert reopened["baseline"] == baseline
    assert reopened["graph"]["edges"][0]["status"] == "inactive"
    imported = import_document(ImportRequest(format="json", content=json.dumps(reopened)))
    assert imported["baseline"] == baseline

    write = {"name":"舊客戶端儲存", "graph":changed.model_dump(), "revision":1}
    assert client.put('/api/workspaces/'+saved['id'], json=write).json()["baseline"] == baseline
    assert client.put('/api/workspaces/'+saved['id'], json=write).status_code == 409
    write.update(revision=2, baseline=None)
    assert client.put('/api/workspaces/'+saved['id'], json=write).json()["baseline"] is None
