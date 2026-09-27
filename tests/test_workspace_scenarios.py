# -*- coding: utf-8 -*-
import json

from app.models.workspace import TopologyWorkspace
from app.services.workspace import ComparisonBaseline, GraphDocument
from app.services.workspace_allocation import allocate
from app.services.workspace_scenarios import (
    GUANGFU_WORKSPACE_ID,
    NEPAL_WORKSPACE_ID,
    ensure_scenario_workspaces,
    workspace_scenarios,
)


def test_scenarios_are_valid_labelled_simulations_with_useful_test_data():
    scenarios = workspace_scenarios()
    assert {scenario.id for scenario in scenarios} == {NEPAL_WORKSPACE_ID, GUANGFU_WORKSPACE_ID}
    assert {scenario.folder for scenario in scenarios} == {"國際案例", "花蓮案例"}
    for scenario in scenarios:
        graph = GraphDocument.model_validate(scenario.graph.model_dump())
        ComparisonBaseline.model_validate(scenario.baseline.model_dump())
        assert len(graph.nodes) >= 8 and len(graph.edges) >= 8
        assert any(edge.status == "inactive" for edge in graph.edges)
        assert all(node.properties.get("data_status") == "情境模擬" for node in graph.nodes)
        assert all(node.properties.get("source_url", "").startswith("https://") for node in graph.nodes)
        assert all(line.source == "情境模擬數量，非官方庫存或災情統計"
                   for node in graph.nodes for line in node.logistics)
        items = {(line.item, line.unit) for node in graph.nodes for line in node.logistics
                 if line.role == "supply"}
        assert items
        item, unit = sorted(items)[0]
        result = allocate(graph, item, unit, 500)
        assert result["summary"]["stock"] > 0
        assert result["summary"]["requested"] > 0


def test_scenario_seed_is_idempotent_and_preserves_existing_edits(db):
    first = ensure_scenario_workspaces(db)
    assert set(first["created"]) == {NEPAL_WORKSPACE_ID, GUANGFU_WORKSPACE_ID}
    row = db.get(TopologyWorkspace, NEPAL_WORKSPACE_ID)
    row.name = "使用者改過的名稱"
    db.commit()

    second = ensure_scenario_workspaces(db)
    assert second["created"] == []
    assert set(second["existing"]) == {NEPAL_WORKSPACE_ID, GUANGFU_WORKSPACE_ID}
    assert db.get(TopologyWorkspace, NEPAL_WORKSPACE_ID).name == "使用者改過的名稱"
    assert db.query(TopologyWorkspace).count() == 2

    payload = json.loads(db.get(TopologyWorkspace, GUANGFU_WORKSPACE_ID).document)
    assert payload["baseline"]["name"] == "災前道路基準（測試）"
    assert len(payload["graph"]["nodes"]) >= 8
