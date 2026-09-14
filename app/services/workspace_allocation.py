"""Integer, priority-aware allocation. Planning only; never changes stock or dispatch."""

from datetime import UTC, datetime

import networkx as nx
from pydantic import BaseModel, Field

from app.services.workspace import GraphDocument, build_routing_graphs
from app.services.workspace_comparison import fingerprint


class AllocationRequest(BaseModel):
    graph: GraphDocument
    item: str = Field(min_length=1, max_length=80)
    unit: str = Field(min_length=1, max_length=40)
    max_minutes: float = Field(default=120, gt=0, le=1440, allow_inf_nan=False)
    baseline: GraphDocument | None = None


def allocate(document: GraphDocument, item: str, unit: str, max_minutes: float) -> dict:
    document = GraphDocument(nodes=sorted(document.nodes, key=lambda n: n.id),
                             edges=sorted(document.edges, key=lambda e: e.id))
    supplies, demands = [], []
    for node in document.nodes:
        for line in sorted(node.logistics, key=lambda line: line.id):
            if (line.item, line.unit) == (item, unit):
                (supplies if line.role == "supply" else demands).append((node, line))
    if len(supplies) > 100 or len(demands) > 300:
        raise ValueError("單一品項試算上限為 100 筆供應、300 筆需求，請分區規劃")
    travel, _, ignored = build_routing_graphs(document)
    capacity = {line.id: min(line.quantity, line.dispatch_limit if line.dispatch_limit is not None else line.quantity)
                if node.available and node.lat is not None else 0 for node, line in supplies}
    distances = {}
    for node, line in supplies:
        if capacity[line.id] and node.id not in distances:
            distances[node.id] = nx.single_source_dijkstra_path_length(travel, node.id, weight="weight")
    candidates = {}
    for source, stock in supplies:
        if not capacity[stock.id]:
            continue
        for target, demand in demands:
            if not target.available or target.lat is None or not demand.quantity:
                continue
            minutes = distances[source.id].get(target.id)
            if minutes is not None and minutes <= max_minutes:
                candidates[stock.id, demand.id] = minutes
    total = sum(line.quantity for _, line in demands)
    max_cost = max((round(minutes * 60000) for minutes in candidates.values()), default=0)
    # One unmet higher-priority unit outweighs all lower-priority shortages and travel costs.
    penalties = {1: total * max_cost + 1}
    for priority in range(2, 6):
        penalties[priority] = total * penalties[priority - 1] + total * max_cost + 1
    network = nx.DiGraph()
    root = ("source",)
    network.add_node(root, demand=-total)
    for _, stock in supplies:
        network.add_edge(root, ("s", stock.id), capacity=capacity[stock.id], weight=0)
    for _, demand in demands:
        key = ("d", demand.id)
        network.add_node(key, demand=demand.quantity)
        network.add_edge(root, key, capacity=demand.quantity, weight=penalties[demand.priority])
    for (stock_id, demand_id), minutes in candidates.items():
        network.add_edge(("s", stock_id), ("d", demand_id), capacity=capacity[stock_id], weight=round(minutes * 60000))
    flow = nx.network_simplex(network)[1] if total else {}
    assignments, supplied, received, paths = [], {}, {}, {}
    supply_by_id = {line.id: node for node, line in supplies}
    demand_by_id = {line.id: node for node, line in demands}
    for (stock_id, demand_id), minutes in candidates.items():
        quantity = flow.get(("s", stock_id), {}).get(("d", demand_id), 0)
        if not quantity:
            continue
        source, target = supply_by_id[stock_id], demand_by_id[demand_id]
        pair = source.id, target.id
        if pair not in paths:
            path = nx.shortest_path(travel, *pair, weight="weight")
            edges = [min(travel[a][b].items(), key=lambda edge: (edge[1]["weight"], edge[0])) for a, b in zip(path, path[1:])]
            paths[pair] = {"nodes": path, "edges": [edge_id for edge_id, _ in edges],
                           "km": round(sum(data["km"] for _, data in edges), 3), "minutes": round(minutes, 2)}
        assignments.append({"supply_id": stock_id, "demand_id": demand_id, "source": source.id, "target": target.id,
                            "source_label": source.label, "target_label": target.label, "quantity": quantity, "route": paths[pair]})
        supplied[stock_id] = supplied.get(stock_id, 0) + quantity
        received[demand_id] = received.get(demand_id, 0) + quantity
    deficits = []
    for node, line in demands:
        quantity = received.get(line.id, 0)
        reason = None
        if quantity < line.quantity:
            if not node.available:
                reason = "destination_unavailable"
            elif node.lat is None:
                reason = "missing_coordinates"
            elif not any(stock.quantity for _, stock in supplies):
                reason = "no_stock"
            elif not any(capacity.values()):
                reason = "no_dispatch_capacity"
            elif not any(node.id in distances.get(source.id, {}) for source, stock in supplies if capacity[stock.id]):
                reason = "unreachable"
            elif not any(demand_id == line.id for _, demand_id in candidates):
                reason = "travel_limit"
            else:
                reason = "capacity_or_priority"
        deficits.append({"id": line.id, "node_id": node.id, "label": node.label, "priority": line.priority, "source": line.source,
                         "requested": line.quantity, "allocated": quantity, "unmet": line.quantity - quantity, "reason": reason})
    inventory = [{"id": line.id, "node_id": node.id, "label": node.label, "stock": line.quantity, "source": line.source,
                  "dispatch_capacity": capacity[line.id], "allocated": supplied.get(line.id, 0),
                  "remaining": line.quantity - supplied.get(line.id, 0)} for node, line in supplies]
    return {"summary": {"stock": sum(line.quantity for _, line in supplies), "dispatch_capacity": sum(capacity.values()),
                        "requested": total, "allocated": sum(received.values()), "unmet": total - sum(received.values())},
            "assignments": assignments, "demands": deficits, "inventory": inventory, "ignored_edges": ignored,
            "fingerprint": fingerprint(document)}


def plan_allocation(request: AllocationRequest) -> dict:
    item, unit = request.item.strip(), request.unit.strip()
    if not item or not unit:
        raise ValueError("請選擇明確的品項與單位")
    after = allocate(request.graph, item, unit, request.max_minutes)
    before = allocate(request.baseline, item, unit, request.max_minutes) if request.baseline is not None else None
    old = {d["id"]: d for d in before["demands"]} if before else {}
    new = {d["id"]: d for d in after["demands"]}
    comparison = None
    if before:
        comparison = {"deltas": {k: after["summary"][k] - v for k, v in before["summary"].items()},
                      "entered_demands": sorted(new.keys() - old.keys()), "exited_demands": sorted(old.keys() - new.keys()),
                      "changed_demands": sorted(k for k in old.keys() & new.keys()
                                                if old[k]["requested"] != new[k]["requested"] or old[k]["priority"] != new[k]["priority"])}
    return {"model": "capacitated-allocation-v1", "solver": f"NetworkX {nx.__version__} network_simplex",
            "generated_at": datetime.now(UTC).isoformat(), "item": item, "unit": unit, "max_minutes": request.max_minutes,
            "before": before, "after": after, "comparison": comparison,
            "assumptions": ["只分配明確填寫且品項、單位完全相同的整數數量；不推測公開設施庫存或換算單位。",
                            "依使用者優先級由 5 至 1 逐級最大化滿足量，同級再最小化每單位路程時間總和；允許部分分配。",
                            "遵守封路、單行、停用物件與路程時間上限；未定位的供應與需求不假設可配送。",
                            "出貨上限屬單筆供應紀錄，不是跨品項車輛容量；未計算車次、裝載、道路容量或出勤排程。",
                            "時間以端點距離、路速及緩行倍率估算，最小成本流採整數毫秒成本，非即時交通或整體完成時間。",
                            "僅為規劃結果，不扣除庫存、不建立正式派遣、不發送通知；原始資料與優先級仍須現場確認。"]}
