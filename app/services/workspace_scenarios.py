"""Curated, clearly-labelled simulation workspaces used for demos and training."""

from __future__ import annotations

import json
from copy import deepcopy
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.workspace import TopologyWorkspace
from app.models.zone import GENERAL_ZONE_ID
from app.services.workspace import ComparisonBaseline, Edge, GraphDocument, LogisticsRecord, Node


NEPAL_WORKSPACE_ID = "scenario-nepal-rasuwa-2026"
GUANGFU_WORKSPACE_ID = "scenario-guangfu-matai-an-2025"

NEPAL_SOURCE_URL = "https://ndrrma.gov.np/en/situation-report"
NEPAL_HEALTH_SOURCE_URL = "https://heoc.mohp.gov.np/"
GUANGFU_SOURCE_URL = (
    "https://www.ey.gov.tw/Page/448DE008087A1971/"
    "54948b34-b85d-4ee4-ad97-3cbc4e84fa40"
)


@dataclass(frozen=True)
class WorkspaceScenario:
    id: str
    name: str
    folder: str
    graph: GraphDocument
    baseline: ComparisonBaseline


def _record(
    scenario: str,
    slug: str,
    role: str,
    item: str,
    unit: str,
    quantity: int,
    *,
    priority: int = 3,
    dispatch_limit: int | None = None,
) -> LogisticsRecord:
    return LogisticsRecord(
        id=f"{scenario}:logistics:{slug}",
        role=role,
        item=item,
        unit=unit,
        quantity=quantity,
        priority=priority,
        dispatch_limit=dispatch_limit,
        source="情境模擬數量，非官方庫存或災情統計",
    )


def _node(
    scenario: str,
    slug: str,
    label: str,
    kind: str,
    lat: float,
    lng: float,
    source_label: str,
    source_url: str,
    *,
    logistics: list[LogisticsRecord] | None = None,
    **properties,
) -> Node:
    return Node(
        id=f"{scenario}:node:{slug}",
        label=label,
        kind=kind,
        lat=lat,
        lng=lng,
        source=source_label,
        properties={
            "scenario": True,
            "data_status": "情境模擬",
            "coordinates": "近似位置，僅供演練",
            "quantity_status": "測試值，非官方統計",
            "source_url": source_url,
            **properties,
        },
        logistics=logistics or [],
    )


def _edge(
    scenario: str,
    slug: str,
    source: str,
    target: str,
    label: str,
    source_label: str,
    *,
    kind: str = "related",
    status: str = "active",
    directed: bool = True,
    disruption: bool = False,
) -> Edge:
    return Edge(
        id=f"{scenario}:edge:{slug}",
        source=f"{scenario}:node:{source}",
        target=f"{scenario}:node:{target}",
        kind=kind,
        label=label,
        status=status,
        directed=directed,
        provenance=source_label,
        properties={"scenario": True, "data_status": "情境模擬", "disruption": disruption},
    )


def _pre_event_baseline(graph: GraphDocument, name: str, captured_at: datetime) -> ComparisonBaseline:
    baseline = deepcopy(graph)
    for node in baseline.nodes:
        if node.kind == "incident":
            node.available = False
        for line in node.logistics:
            if line.role == "demand":
                line.quantity = 0
    for edge in baseline.edges:
        if edge.properties.get("disruption"):
            edge.status = "active"
    return ComparisonBaseline(name=name, graph=baseline, captured_at=captured_at)


def nepal_rasuwa_scenario() -> WorkspaceScenario:
    """Recent Rasuwa-Bhote Koshi flood context with simulated operational values."""
    scenario = "nepal-rasuwa-2026"
    source = "尼泊爾 NDRRMA 情勢報告（2026-09-16 查核）"
    nodes = [
        _node(
            scenario, "flood", "Rasuwa–Bhote Koshi 洪災", "incident", 28.2700, 85.3780,
            source, NEPAL_SOURCE_URL, event_date="2026-08-26", checked_at="2026-09-27",
            event_status="搜救與救援應變持續中",
            secondary_source_url=NEPAL_HEALTH_SOURCE_URL,
        ),
        _node(
            scenario, "dhunche-hub", "Dhunche 應變物資中心（演練）", "facility", 28.1110, 85.2970,
            source, NEPAL_SOURCE_URL, function="區域物資集散與轉運",
        ),
        _node(
            scenario, "syabrubesi-shelter", "Syabrubesi 臨時安置點（演練）", "facility", 28.1620, 85.3480,
            source, NEPAL_SOURCE_URL, function="臨時安置與需求彙整",
        ),
        _node(
            scenario, "trishuli-health", "Trishuli 醫療支援點（演練）", "facility", 27.9270, 85.1460,
            "尼泊爾 HEOC 緊急監測（2026-09-07 起）", NEPAL_HEALTH_SOURCE_URL,
            function="醫療轉介與基本藥品支援",
        ),
        _node(
            scenario, "dhunche-stock", "Dhunche 飲水庫存（測試）", "supply", 28.1090, 85.2990,
            source, NEPAL_SOURCE_URL,
            logistics=[_record(scenario, "dhunche-water", "supply", "飲用水", "箱", 180,
                               dispatch_limit=120)],
        ),
        _node(
            scenario, "trishuli-stock", "Trishuli 救援物資（測試）", "supply", 27.9250, 85.1480,
            source, NEPAL_SOURCE_URL,
            logistics=[
                _record(scenario, "trishuli-water", "supply", "飲用水", "箱", 260,
                        dispatch_limit=180),
                _record(scenario, "trishuli-medicine", "supply", "基本藥品", "箱", 80,
                        dispatch_limit=45),
            ],
        ),
        _node(
            scenario, "timure-demand", "Timure 受災家戶需求（測試）", "person", 28.2750, 85.3790,
            source, NEPAL_SOURCE_URL,
            logistics=[_record(scenario, "timure-water", "demand", "飲用水", "箱", 240, priority=5)],
            population_scope="彙總家戶，非個人資料",
        ),
        _node(
            scenario, "syabrubesi-demand", "Syabrubesi 安置需求（測試）", "person", 28.1600, 85.3500,
            source, NEPAL_SOURCE_URL,
            logistics=[_record(scenario, "syabrubesi-water", "demand", "飲用水", "箱", 160, priority=4)],
            population_scope="彙總安置需求，非個人資料",
        ),
        _node(
            scenario, "devighat-demand", "Devighat 安置需求（測試）", "person", 27.9580, 85.1780,
            source, NEPAL_SOURCE_URL,
            logistics=[
                _record(scenario, "devighat-water", "demand", "飲用水", "箱", 120, priority=4),
                _record(scenario, "devighat-medicine", "demand", "基本藥品", "箱", 30, priority=5),
            ],
            population_scope="彙總安置需求，非個人資料",
        ),
        _node(
            scenario, "route-break", "Bhote Koshi 聯外道路中斷（情境）", "custom", 28.2200, 85.3650,
            source, NEPAL_SOURCE_URL, object_type="transport_disruption",
        ),
    ]
    edges = [
        _edge(scenario, "flood-timure", "flood", "timure-demand", "洪災影響", source),
        _edge(scenario, "flood-route", "flood", "route-break", "洪災造成交通中斷", source),
        _edge(scenario, "route-timure", "route-break", "timure-demand", "聯外路段", source,
              status="inactive", disruption=True),
        _edge(scenario, "dhunche-stock", "dhunche-hub", "dhunche-stock", "管理庫存", source,
              kind="assignment"),
        _edge(scenario, "dhunche-syabrubesi", "dhunche-stock", "syabrubesi-demand", "候選供應線", source,
              kind="supplies"),
        _edge(scenario, "dhunche-timure", "dhunche-stock", "timure-demand", "受中斷影響的供應線", source,
              kind="supplies", status="inactive", disruption=True),
        _edge(scenario, "shelter-demand", "syabrubesi-shelter", "syabrubesi-demand", "彙整安置需求", source,
              kind="care"),
        _edge(scenario, "health-stock", "trishuli-health", "trishuli-stock", "醫療物資協調", source,
              kind="assignment"),
        _edge(scenario, "trishuli-devighat", "trishuli-stock", "devighat-demand", "候選供應線", source,
              kind="supplies"),
    ]
    graph = GraphDocument(nodes=nodes, edges=edges)
    return WorkspaceScenario(
        id=NEPAL_WORKSPACE_ID,
        name="尼泊爾 Rasuwa 洪災（2026-08 演練）",
        folder="國際案例",
        graph=graph,
        baseline=_pre_event_baseline(graph, "災前通行基準（測試）", datetime(2026, 8, 25, tzinfo=UTC)),
    )


def guangfu_matai_an_scenario() -> WorkspaceScenario:
    """2025 Guangfu flood context with simulated needs, stock and route disruption."""
    scenario = "guangfu-matai-an-2025"
    source = "行政院馬太鞍堰塞湖災後復原情形（2025-10-02）"
    nodes = [
        _node(
            scenario, "flood", "馬太鞍溪堰塞湖溢流事件", "incident", 23.6840, 121.4280,
            source, GUANGFU_SOURCE_URL, event_date="2025-09-23", checked_at="2026-09-27",
            event_context="光復鄉洪水與土砂淤積",
        ),
        _node(
            scenario, "sugar-factory", "光復糖廠前進協調點（演練）", "facility", 23.6738, 121.4260,
            source, GUANGFU_SOURCE_URL, function="志工與物資協調",
        ),
        _node(
            scenario, "town-office", "光復鄉公所協調點（演練）", "facility", 23.6695, 121.4224,
            source, GUANGFU_SOURCE_URL, function="行政協調與需求彙整",
        ),
        _node(
            scenario, "elementary-shelter", "光復國小安置點（演練）", "facility", 23.6710, 121.4250,
            source, GUANGFU_SOURCE_URL, function="臨時安置",
        ),
        _node(
            scenario, "sugar-water", "光復糖廠飲水庫存（測試）", "supply", 23.6736, 121.4257,
            source, GUANGFU_SOURCE_URL,
            logistics=[_record(scenario, "sugar-water", "supply", "飲用水", "箱", 320,
                               dispatch_limit=220)],
        ),
        _node(
            scenario, "office-tools", "鄉公所清理工具（測試）", "supply", 23.6693, 121.4220,
            source, GUANGFU_SOURCE_URL,
            logistics=[_record(scenario, "office-tools", "supply", "清理工具", "套", 60,
                               dispatch_limit=40)],
        ),
        _node(
            scenario, "datong-demand", "大同村家戶需求（測試）", "person", 23.6721, 121.4265,
            source, GUANGFU_SOURCE_URL,
            logistics=[_record(scenario, "datong-water", "demand", "飲用水", "箱", 180, priority=5)],
            population_scope="彙總家戶，非個人資料",
        ),
        _node(
            scenario, "dahua-demand", "大華村清理需求（測試）", "person", 23.6650, 121.4188,
            source, GUANGFU_SOURCE_URL,
            logistics=[_record(scenario, "dahua-tools", "demand", "清理工具", "套", 45, priority=4)],
            population_scope="彙總家戶，非個人資料",
        ),
        _node(
            scenario, "school-demand", "光復國小安置需求（測試）", "person", 23.6708, 121.4248,
            source, GUANGFU_SOURCE_URL,
            logistics=[_record(scenario, "school-water", "demand", "飲用水", "箱", 120, priority=4)],
            population_scope="彙總安置需求，非個人資料",
        ),
        _node(
            scenario, "bridge-break", "台 9 線馬太鞍溪橋中斷（情境）", "custom", 23.7000, 121.4210,
            source, GUANGFU_SOURCE_URL, object_type="transport_disruption",
        ),
    ]
    edges = [
        _edge(scenario, "flood-bridge", "flood", "bridge-break", "洪災造成橋梁中斷", source),
        _edge(scenario, "bridge-datong", "bridge-break", "datong-demand", "北側聯外路段", source,
              status="inactive", disruption=True),
        _edge(scenario, "sugar-stock", "sugar-factory", "sugar-water", "管理飲水庫存", source,
              kind="assignment"),
        _edge(scenario, "sugar-datong", "sugar-water", "datong-demand", "候選供應線", source,
              kind="supplies"),
        _edge(scenario, "sugar-school", "sugar-water", "school-demand", "候選供應線", source,
              kind="supplies"),
        _edge(scenario, "school-shelter", "elementary-shelter", "school-demand", "彙整安置需求", source,
              kind="care"),
        _edge(scenario, "office-stock", "town-office", "office-tools", "管理工具庫存", source,
              kind="assignment"),
        _edge(scenario, "office-dahua", "office-tools", "dahua-demand", "候選供應線", source,
              kind="supplies"),
        _edge(scenario, "office-datong", "town-office", "datong-demand", "需求彙整", source,
              kind="request"),
    ]
    graph = GraphDocument(nodes=nodes, edges=edges)
    return WorkspaceScenario(
        id=GUANGFU_WORKSPACE_ID,
        name="光復鄉馬太鞍溪洪災（2025-09 演練）",
        folder="花蓮案例",
        graph=graph,
        baseline=_pre_event_baseline(graph, "災前道路基準（測試）", datetime(2025, 9, 22, tzinfo=UTC)),
    )


def workspace_scenarios() -> tuple[WorkspaceScenario, ...]:
    return nepal_rasuwa_scenario(), guangfu_matai_an_scenario()


def ensure_scenario_workspaces(db: Session) -> dict[str, list[str]]:
    """Insert missing scenarios without overwriting edits to an existing seeded workspace."""
    created, existing = [], []
    now = datetime.now(UTC).isoformat()
    for scenario in workspace_scenarios():
        if db.get(TopologyWorkspace, scenario.id):
            existing.append(scenario.id)
            continue
        document = json.dumps(
            {
                "graph": scenario.graph.model_dump(mode="json"),
                "baseline": scenario.baseline.model_dump(mode="json"),
            },
            ensure_ascii=False,
            separators=(",", ":"),
        )
        db.add(TopologyWorkspace(
            id=scenario.id,
            name=scenario.name,
            document=document,
            revision=1,
            updated_at=now,
            zone_id=GENERAL_ZONE_ID,
            folder=scenario.folder,
        ))
        created.append(scenario.id)
    db.commit()
    return {"created": created, "existing": existing}
