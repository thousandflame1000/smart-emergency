"""Stable fingerprint of a topology snapshot."""

import hashlib
import json

from app.services.workspace import GraphDocument


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

