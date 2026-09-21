import json

from fastapi.testclient import TestClient

from app.main import app
from app.models.workspace import TopologyWorkspace
from app.services.workspace import ComparisonBaseline, Edge, GraphDocument, ImportRequest, Node, import_document
from app.services.workspace_comparison import compare, fingerprint


def event_graph():
    return GraphDocument(nodes=[
        Node(id="incident", label="停水事件", kind="incident"),
        Node(id="need", label="飲用水需求", kind="custom", properties={"db":"need", "status":"open"}),
        Node(id="person", label="居民", kind="person"),
    ], edges=[
        Edge(id="focus", source="incident", target="need", kind="related", label="事件追蹤"),
        Edge(id="request", source="person", target="need", kind="request", label="提出需求"),
    ])


def test_relation_change_evidence_is_non_mutating():
    base = event_graph()
    graph = base.model_copy(deep=True)
    graph.edges[0].status = "inactive"
    original = graph.model_dump()
    result = compare(base, graph)
    assert result["newly_unlinked_objects"] == ["incident"]
    assert result["newly_linked_objects"] == []
    assert result["metric_deltas"]["unlinked_objects"] == 1
    assert result["changes"]["edges"]["updated"] == [{"id":"focus", "label":"事件追蹤", "fields":["status"]}]
    assert result["baseline_fingerprint"] != result["scenario_fingerprint"]
    assert graph.model_dump() == original
    assert base.edges[0].status == "active"
    assert compare(graph, base)["newly_linked_objects"] == ["incident"]


def test_deleted_or_disabled_objects_are_not_counted_as_newly_linked():
    base = event_graph()
    base.edges[0].status = "inactive"
    graph = base.model_copy(deep=True)
    graph.nodes[0].available = False
    result = compare(base, graph)
    assert result["newly_linked_objects"] == []
    assert result["exited_objects"] == ["incident"]


def test_added_object_and_relation_are_reported_by_stable_identity():
    base = event_graph()
    graph = base.model_copy(deep=True)
    graph.nodes.append(Node(id="volunteer", label="志工", kind="person"))
    graph.edges.append(Edge(id="assignment", source="volunteer", target="need", kind="assignment"))
    result = compare(base, graph)
    assert result["entered_objects"] == ["volunteer"]
    assert result["changes"]["nodes"]["added"] == [{"id":"volunteer", "label":"志工"}]
    assert result["changes"]["edges"]["added"] == [{"id":"assignment", "label":"連線"}]


def test_reordering_and_layout_do_not_change_fingerprints_or_diffs():
    base = event_graph()
    graph = base.model_copy(deep=True)
    graph.nodes.reverse()
    graph.edges.reverse()
    graph.nodes[0].properties["_layout"] = {"x":0, "y":10}
    result = compare(base, graph)
    assert fingerprint(base) == fingerprint(graph)
    assert all(value == 0 for value in result["metric_deltas"].values())
    assert result["changes"]["nodes"] == {"added":[], "removed":[], "updated":[]}


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


def test_compare_api_is_read_only(db):
    client = TestClient(app)
    payload = {"baseline":event_graph().model_dump(), "graph":event_graph().model_dump()}
    assert client.post('/api/workspaces/compare', json=payload).status_code == 200
    payload["graph"]["edges"][0]["target"] = "missing"
    assert client.post('/api/workspaces/compare', json=payload).status_code == 422
    assert db.query(TopologyWorkspace).count() == 0
