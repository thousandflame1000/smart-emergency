import json

import pytest
from fastapi.testclient import TestClient

from app.main import app
from app.models.workspace import TopologyWorkspace
from app.services.workspace import ComparisonBaseline, Edge, GraphDocument, ImportRequest, Node, import_document
from app.services.workspace_comparison import compare, fingerprint


def network():
    return GraphDocument(nodes=[
        Node(id="s", label="補給站", kind="supply", lat=25, lng=121, quantity=10),
        Node(id="a", label="路口甲", kind="road_node", lat=25, lng=121.001),
        Node(id="b", label="路口乙", kind="road_node", lat=25, lng=121.01),
        Node(id="p", label="待援點", kind="person", lat=25, lng=121.011),
    ], edges=[
        Edge(id="entry", source="s", target="a", kind="access"),
        Edge(id="road", source="a", target="b", kind="road", directed=True),
        Edge(id="exit", source="b", target="p", kind="access"),
    ])


def test_closed_road_impact_and_evidence_are_non_mutating():
    base = network()
    graph = base.model_copy(deep=True)
    graph.edges[1].status = "closed"
    original = graph.model_dump()
    result = compare(base, graph, "s", "p")
    assert result["newly_unreachable_people"] == ["p"]
    assert result["restored_people"] == []
    assert result["route_delta"] == {"status":"lost", "minutes":None, "km":None}
    assert result["metric_deltas"]["road_components"] == 1
    assert result["changes"]["edges"]["updated"] == [{"id":"road", "label":"連線", "fields":["status"]}]
    assert result["baseline_fingerprint"] != result["scenario_fingerprint"]
    assert graph.model_dump() == original
    assert base.edges[1].status == "normal"
    assert compare(graph, base, "s", "p")["restored_people"] == ["p"]


def test_detour_is_slower_and_one_way_is_respected():
    base = network()
    base.edges.append(Edge(id="detour", source="a", target="b", kind="road", speed_kph=5, directed=True))
    graph = base.model_copy(deep=True)
    graph.edges[1].status = "closed"
    result = compare(base, graph, "s", "p")
    assert result["route_delta"]["status"] == "changed"
    assert result["route_delta"]["minutes"] > 0
    assert result["newly_unreachable_people"] == []
    assert result["after"]["route"]["edges"] == ["entry", "detour", "exit"]
    assert compare(base, graph, "p", "s")["route_delta"]["status"] == "unavailable"


@pytest.mark.parametrize("change", ["delete", "disable", "retype"])
def test_removing_an_unreachable_person_is_not_a_recovery(change):
    base = network()
    base.edges[1].status = "closed"
    graph = base.model_copy(deep=True)
    if change == "delete":
        graph.nodes = graph.nodes[:-1]
        graph.edges = graph.edges[:-1]
    elif change == "disable":
        graph.nodes[-1].available = False
    else:
        graph.nodes[-1].kind = "custom"
    result = compare(base, graph, "s", "p")
    assert result["restored_people"] == []
    assert result["exited_people"] == ["p"]
    assert result["metric_deltas"]["unreachable_people"] == -1
    if change == "delete":
        assert result["after"]["route"]["reason"] == "endpoint_missing"


def test_new_person_not_counted_as_existing_cohort_impact_and_empty_supply():
    base = network()
    graph = base.model_copy(deep=True)
    graph.nodes[0].quantity = 0
    graph.nodes.append(Node(id="new", kind="person"))
    result = compare(base, graph)
    assert result["entered_people"] == ["new"]
    assert result["newly_unreachable_people"] == ["p"]
    assert result["after"]["metrics"]["unreachable_people"] == 2
    graph.nodes[1].lat = graph.nodes[1].lng = None
    assert set(compare(base, graph)["after"]["ignored_edges"]) == {"entry", "road"}


def test_reordering_and_layout_do_not_change_fingerprints_or_diffs():
    base = network()
    graph = base.model_copy(deep=True)
    graph.nodes.reverse()
    graph.edges.reverse()
    graph.nodes[0].properties["_layout"] = {"x":0, "y":10}
    result = compare(base, graph, "s", "p")
    assert fingerprint(base) == fingerprint(graph)
    assert all(value == 0 for value in result["metric_deltas"].values())
    assert result["changes"]["nodes"] == {"added":[], "removed":[], "updated":[]}
    assert result["route_delta"]["status"] == "unchanged"


def test_equal_cost_routes_are_stable_after_input_reordering():
    base = network()
    base.edges.append(base.edges[1].model_copy(update={"id":"parallel"}))
    graph = base.model_copy(deep=True)
    graph.edges.reverse()
    result = compare(base, graph, "s", "p")
    assert result["before"]["route"] == result["after"]["route"]
    assert result["route_delta"]["status"] == "unchanged"


def test_snapshot_persistence_legacy_reads_conflicts_and_import(db):
    client = TestClient(app)
    base = network()
    db.add(TopologyWorkspace(id="legacy", name="舊工作區", document=base.model_dump_json(), revision=2, updated_at="2026-09-14"))
    db.commit()
    legacy = client.get('/api/workspaces/legacy').json()
    assert legacy["graph"] == base.model_dump() and legacy["baseline"] is None
    baseline = ComparisonBaseline(name="固定基準", graph=base, workspace_id="legacy", revision=2).model_dump(mode="json")
    changed = base.model_copy(deep=True)
    changed.edges[1].status = "closed"
    saved = client.post('/api/workspaces', json={"name":"封路情境", "graph":changed.model_dump(), "baseline":baseline}).json()
    reopened = client.get('/api/workspaces/'+saved['id']).json()
    assert reopened["baseline"] == baseline
    assert reopened["graph"]["edges"][1]["status"] == "closed"
    assert client.get('/api/workspaces/legacy').json() == legacy
    imported = import_document(ImportRequest(format="json", content=json.dumps(reopened)))
    assert imported["baseline"] == baseline
    # Older clients omit baseline; a normal save must not erase it.
    write = {"name":"舊客戶端儲存", "graph":changed.model_dump(), "revision":1}
    assert client.put('/api/workspaces/'+saved['id'], json=write).json()["baseline"] == baseline
    assert client.put('/api/workspaces/'+saved['id'], json=write).status_code == 409
    write.update(revision=2, baseline=None)
    assert client.put('/api/workspaces/'+saved['id'], json=write).json()["baseline"] is None


def test_compare_api_rejects_incomplete_endpoints_and_does_not_save(db):
    client = TestClient(app)
    payload = {"baseline":network().model_dump(), "graph":network().model_dump()}
    assert client.post('/api/workspaces/compare', json=payload).status_code == 200
    payload["start"] = "s"
    assert client.post('/api/workspaces/compare', json=payload).status_code == 400
    payload["end"] = "missing"
    assert client.post('/api/workspaces/compare', json=payload).status_code == 400
    payload["baseline"]["nodes"].pop()
    assert client.post('/api/workspaces/compare', json=payload).status_code == 422
    assert db.query(TopologyWorkspace).count() == 0
