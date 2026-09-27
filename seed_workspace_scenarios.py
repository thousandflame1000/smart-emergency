# -*- coding: utf-8 -*-
"""Add the two competition simulation workspaces without touching existing data."""

import os
import sys

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.database import SessionLocal, engine
from app.schema_migrations import ensure_additive_schema
from app.services.workspace_scenarios import ensure_scenario_workspaces


def run():
    ensure_additive_schema(engine)
    with SessionLocal() as db:
        result = ensure_scenario_workspaces(db)
    print(f"新增 {len(result['created'])} 個案例；已存在 {len(result['existing'])} 個案例")
    return result


if __name__ == "__main__":
    run()
