import json

import pytest
from fastapi.testclient import TestClient
from pydantic import ValidationError

from app.main import app
from app.models.workspace import TopologyWorkspace
from app.services.workspace import Edge, GraphDocument, ImportRequest, LogisticsRecord, Node, analyze, import_document
from app.services.workspace_allocation import AllocationRequest, plan_allocation


def record(id, role, quantity=1, **kwargs):
    return LogisticsRecord(id=id, role=role, item="飲用水", unit="箱", quantity=quantity, **kwargs)


def network():
    return GraphDocument(nodes=[
        Node(id="s1", label="第一供應點", kind="supply", lat=25, lng=121, logistics=[record("stock1", "supply")]),
        Node(id="s2", label="第二供應點", kind="supply", lat=25.01, lng=121, logistics=[record("stock2", "supply")]),
        Node(id="d1", label="需求一", kind="person", lat=25, lng=121.001, logistics=[record("demand1", "demand")]),
        Node(id="d2", label="需求二", kind="person", lat=25, lng=121.01, logistics=[record("demand2", "demand")]),
    ], edges=[
        Edge(id="near", source="s1", target="d1", kind="access", directed=True),
        Edge(id="far", source="s1", target="d2", kind="access", directed=True),
        Edge(id="limited", source="s2", target="d1", kind="access", directed=True),
    ])


def plan(graph, **kwargs):
    return plan_allocation(AllocationRequest(graph=graph, item="飲用水", unit="箱", **kwargs))


def test_global_flow_avoids_greedy_nearest_assignment_trap_without_mutating():
    graph = network()
    before = graph.model_dump()
    result = plan(graph)
    assert result["after"]["summary"] == {"stock":2, "dispatch_capacity":2, "requested":2, "allocated":2, "unmet":0}
    assert {(a["source"], a["target"]) for a in result["after"]["assignments"]} == {("s1","d2"),("s2","d1")}
    assert graph.model_dump() == before
    assert all(a["route"]["edges"] for a in result["after"]["assignments"])


def test_priority_outweighs_distance_and_dispatch_limit_is_not_stock():
    graph = network()
    graph.nodes[0].logistics[0].quantity = 10
    graph.nodes[0].logistics[0].dispatch_limit = 2
    graph.nodes[1].available = False
    graph.nodes[2].logistics[0].quantity = 3
    graph.nodes[2].logistics[0].priority = 1
    graph.nodes[3].logistics[0].quantity = 4
    graph.nodes[3].logistics[0].priority = 5
    result = plan(graph)["after"]
    assert result["summary"]["allocated"] == 2
    assert result["summary"]["unmet"] == 5
    assert result["assignments"][0]["target"] == "d2"
    assert result["assignments"][0]["quantity"] == 2
    stock = next(s for s in result["inventory"] if s["id"] == "stock1")
    assert stock["stock"] == 10 and stock["remaining"] == 8 and stock["dispatch_capacity"] == 2
    assert all(d["reason"] == "capacity_or_priority" for d in result["demands"])


@pytest.mark.parametrize("change,reason", [("closed","unreachable"),("reverse","unreachable"),("slow","travel_limit"),("unlocated","missing_coordinates"),("unavailable","destination_unavailable"),("limit","no_dispatch_capacity")])
def test_real_graph_constraints_and_deficit_reasons(change, reason):
    graph = network()
    graph.nodes[1].logistics = []
    graph.nodes[2].logistics = []
    if change == "closed":
        graph.edges[1].status = "closed"
    elif change == "reverse":
        graph.edges[1].source, graph.edges[1].target = "d2", "s1"
    elif change == "slow":
        graph.edges[1].status = "slow"
        graph.edges[1].multiplier = 100
    elif change == "unlocated":
        graph.nodes[3].lat = graph.nodes[3].lng = None
    elif change == "unavailable":
        graph.nodes[3].available = False
    else:
        graph.nodes[0].logistics[0].dispatch_limit = 0
    result = plan(graph, max_minutes=10)["after"]
    assert result["summary"]["allocated"] == 0
    assert result["demands"][0]["reason"] == reason


def test_missing_supply_coordinates_and_exact_item_unit_matching():
    graph = network()
    graph.nodes[0].logistics[0].unit = "瓶"
    graph.nodes[1].logistics[0].item = "食品"
    result = plan(graph)["after"]
    assert result["summary"]["stock"] == 0
    assert all(d["reason"] == "no_stock" for d in result["demands"])
    graph.nodes[0].logistics[0].unit = "箱"
    graph.nodes[0].lat = graph.nodes[0].lng = None
    result = plan(graph)["after"]
    assert result["summary"]["stock"] == 1
    assert all(d["reason"] == "no_dispatch_capacity" for d in result["demands"])


def test_unknown_osm_stock_is_never_invented_and_no_demand_is_valid():
    graph = GraphDocument(nodes=[Node(id="f", kind="facility", lat=25, lng=121, quantity=1000)])
    assert plan(graph)["after"]["summary"] == {"stock":0, "dispatch_capacity":0, "requested":0, "allocated":0, "unmet":0}
    graph.nodes[0].logistics = [record("s", "supply", 10)]
    assert plan(graph)["after"]["inventory"][0]["remaining"] == 10


def test_zero_distance_colocated_flow_and_supply_analysis_consistency():
    graph = GraphDocument(nodes=[Node(id="f", kind="facility", lat=25, lng=121, quantity=0,
        logistics=[record("s", "supply", 10), record("d", "demand", 4)])])
    result = plan(graph)["after"]
    assert result["summary"]["allocated"] == 4
    assert result["assignments"][0]["route"] == {"nodes":["f"],"edges":[],"km":0,"minutes":0}
    graph.nodes.append(Node(id="p", kind="person", lat=25, lng=121.001))
    graph.edges.append(Edge(id="route", source="f", target="p", kind="access"))
    assert analyze(graph)["unreachable_people"] == []
    graph.nodes[0].logistics[0].quantity = 0
    assert analyze(graph)["unreachable_people"] == ["p"]


def test_deleted_or_changed_demands_are_explicit_in_baseline_comparison():
    baseline = network()
    graph = baseline.model_copy(deep=True)
    graph.nodes[2].logistics = []
    graph.nodes[3].logistics[0].quantity = 5
    result = plan(graph, baseline=baseline)
    assert result["comparison"]["exited_demands"] == ["demand1"]
    assert result["comparison"]["changed_demands"] == ["demand2"]
    assert result["before"]["summary"]["allocated"] == 2
    assert result["after"]["summary"]["unmet"] == 4


@pytest.mark.parametrize("value", [True, 1.5, -1, 1_000_001, float("nan")])
def test_fractional_boolean_invalid_quantities_rejected(value):
    with pytest.raises(ValidationError):
        record("bad", "supply", value)


def test_csv_explicit_inventory_and_workspace_roundtrip(db):
    result = import_document(ImportRequest(format="csv", kind="supply", content="編號,名稱,緯度,經度,品項,單位,庫存\ns,供應站,25,121,飲用水,箱,10\n",
        mapping={"id":"編號","label":"名稱","lat":"緯度","lng":"經度","item":"品項","unit":"單位","quantity":"庫存"}))
    assert result["graph"]["nodes"][0]["logistics"][0]["quantity"] == 10
    assert result["graph"]["nodes"][0]["logistics"][0]["source"] == "匯入資料"
    client = TestClient(app)
    saved = client.post('/api/workspaces', json={"name":"物資資料", "graph":result["graph"]}).json()
    assert client.get('/api/workspaces/'+saved['id']).json()["graph"] == result["graph"]
    assert import_document(ImportRequest(format="json", content=json.dumps(saved)))["graph"] == result["graph"]
    with pytest.raises(ValueError, match="明確數量"):
        import_document(ImportRequest(format="csv", kind="person", content="id,item,unit\na,water,box\n"))


def test_records_unique_validation_api_nonmutation_and_invalid_limits(db):
    graph = network()
    graph.nodes[1].logistics[0].id = "stock1"
    with pytest.raises(ValidationError):
        GraphDocument.model_validate(graph.model_dump())
    client = TestClient(app)
    body = {"graph":network().model_dump(),"item":"飲用水","unit":"箱"}
    assert client.post('/api/workspaces/allocate', json=body).status_code == 200
    body["max_minutes"] = 0
    assert client.post('/api/workspaces/allocate', json=body).status_code == 422
    assert db.query(TopologyWorkspace).count() == 0


def test_input_reordering_is_deterministic():
    base = network()
    changed = base.model_copy(deep=True)
    changed.nodes.reverse()
    changed.edges.reverse()
    assert plan(base)["after"] == plan(changed)["after"]


def test_supply_limit_is_explicit_and_does_not_silently_drop_rows():
    graph = GraphDocument(nodes=[Node(id=f"n{i}", logistics=[record(f"s{i}", "supply")]) for i in range(101)])
    with pytest.raises(ValueError, match="100 筆供應"):
        plan(graph)


def test_record_order_is_not_a_data_change_and_import_priority_zero_is_rejected():
    base = network()
    base.nodes[0].logistics.append(record("extra", "demand", 2))
    changed = base.model_copy(deep=True)
    changed.nodes[0].logistics.reverse()
    assert plan(base)["after"] == plan(changed)["after"]
    with pytest.raises(ValidationError):
        import_document(ImportRequest(format="csv", kind="person", content="id,item,unit,quantity,priority\na,water,box,1,0\n"))
