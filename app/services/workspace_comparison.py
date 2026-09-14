"""Non-mutating what-if comparisons over two explicit topology snapshots."""

import hashlib
import json
from datetime import UTC, datetime

from app.services.workspace import GraphDocument, analyze


def _records(items):
    records = {}
    for item in items:
        record = item.model_dump(exclude={"properties": {"_layout"}})
        if "logistics" in record:
            record["logistics"] = sorted(record["logistics"], key=lambda line: line["id"])
        records[item.id] = record
    return records


def fingerprint(document: GraphDocument) -> str:
    canonical = {"nodes": _records(document.nodes), "edges": _records(document.edges)}
    return hashlib.sha256(json.dumps(canonical, sort_keys=True, ensure_ascii=False,
                                     separators=(",", ":"), allow_nan=False).encode()).hexdigest()


def _changes(before, after):
    old, new = _records(before), _records(after)
    added = [{"id": key, "label": new[key]["label"]} for key in sorted(new.keys() - old.keys())]
    removed = [{"id": key, "label": old[key]["label"]} for key in sorted(old.keys() - new.keys())]
    updated = []
    for key in sorted(old.keys() & new.keys()):
        fields = [field for field in new[key] if old[key][field] != new[key][field]]
        if fields:
            updated.append({"id": key, "label": new[key]["label"], "fields": fields})
    return {"added": added, "removed": removed, "updated": updated}


def compare(baseline: GraphDocument, graph: GraphDocument,
            start: str | None = None, end: str | None = None) -> dict:
    if bool(start) != bool(end):
        raise ValueError("路徑比較須同時選擇起點與終點")
    all_ids = {n.id for n in baseline.nodes + graph.nodes}
    if start and (start not in all_ids or end not in all_ids):
        raise ValueError("路徑端點不在基準或目前情境中")

    def report(document):
        document = GraphDocument(nodes=sorted(document.nodes, key=lambda n: n.id),
                                 edges=sorted(document.edges, key=lambda e: e.id))
        ids = {n.id for n in document.nodes}
        if start and (start not in ids or end not in ids):
            result = analyze(document)
            result["route"] = {"reachable": False, "nodes": [], "edges": [], "minutes": None,
                               "km": None, "reason": "endpoint_missing"}
            return result
        return analyze(document, start, end)

    before, after = report(baseline), report(graph)
    eligible_before = {n.id for n in baseline.nodes if n.kind == "person" and n.available}
    eligible_after = {n.id for n in graph.nodes if n.kind == "person" and n.available}
    # A deleted or disabled person is no longer comparable, not a successful rescue.
    common = eligible_before & eligible_after
    old_isolated, new_isolated = set(before["unreachable_people"]), set(after["unreachable_people"])
    route_delta = None
    if start:
        old, new = before["route"], after["route"]
        both = old["reachable"] and new["reachable"]
        status = ("changed" if old != new else "unchanged") if both else (
            "lost" if old["reachable"] else "restored" if new["reachable"] else "unavailable")
        route_delta = {"status": status,
                       "minutes": round(new["minutes"] - old["minutes"], 2) if both else None,
                       "km": round(new["km"] - old["km"], 3) if both else None}
    return {
        "model": "topology-comparison-v2", "generated_at": datetime.now(UTC).isoformat(),
        "baseline_fingerprint": fingerprint(baseline), "scenario_fingerprint": fingerprint(graph),
        "before": before, "after": after,
        "metric_deltas": {key: after["metrics"][key] - value for key, value in before["metrics"].items()},
        "changes": {"nodes": _changes(baseline.nodes, graph.nodes), "edges": _changes(baseline.edges, graph.edges)},
        "newly_unreachable_people": sorted(common & (new_isolated - old_isolated)),
        "restored_people": sorted(common & (old_isolated - new_isolated)),
        "entered_people": sorted(eligible_after - eligible_before),
        "exited_people": sorted(eligible_before - eligible_after),
        "route_delta": route_delta,
    }
