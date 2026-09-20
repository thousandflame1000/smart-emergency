"""Optimistic row versions shared by inventory and dispatch commands."""
import hashlib
import json
from datetime import datetime

from sqlalchemy import String, cast, inspect, or_


def row_snapshot(row) -> dict:
    return {column.key: getattr(row, column.key) for column in inspect(type(row)).columns}


def row_version(row) -> str:
    return hashlib.sha256(json.dumps(row_snapshot(row), sort_keys=True, default=str,
                                     ensure_ascii=False).encode()).hexdigest()


def row_predicates(row) -> list:
    predicates = []
    for key, value in row_snapshot(row).items():
        column = getattr(type(row), key)
        equal = column == value
        # SQLite CURRENT_TIMESTAMP omits .000000, while the datetime binder includes it.
        if isinstance(value, datetime) and value.microsecond == 0:
            equal = or_(equal, cast(column, String) == str(value))
        predicates.append(equal)
    return predicates
