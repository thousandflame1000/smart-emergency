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


def compare(baseline: GraphDocument, graph: GraphDocument) -> dict:
    def report(document):
        document = GraphDocument(nodes=sorted(document.nodes, key=lambda n: n.id),
                                 edges=sorted(document.edges, key=lambda e: e.id))
        return analyze(document)

    before, after = report(baseline), report(graph)
    eligible_before = {n.id for n in baseline.nodes if n.available}
    eligible_after = {n.id for n in graph.nodes if n.available}
    common = eligible_before & eligible_after
    old_unlinked, new_unlinked = set(before["unlinked_objects"]), set(after["unlinked_objects"])
    return {
        "model": "relation-comparison-v1", "generated_at": datetime.now(UTC).isoformat(),
        "baseline_fingerprint": fingerprint(baseline), "scenario_fingerprint": fingerprint(graph),
        "before": before, "after": after,
        "metric_deltas": {key: after["metrics"][key] - value for key, value in before["metrics"].items()},
        "changes": {"nodes": _changes(baseline.nodes, graph.nodes), "edges": _changes(baseline.edges, graph.edges)},
        "newly_unlinked_objects": sorted(common & (new_unlinked - old_unlinked)),
        "newly_linked_objects": sorted(common & (old_unlinked - new_unlinked)),
        "entered_objects": sorted(eligible_after - eligible_before),
        "exited_objects": sorted(eligible_before - eligible_after),
    }
