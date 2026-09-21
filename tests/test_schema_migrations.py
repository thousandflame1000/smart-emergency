import inspect as py_inspect
import re
from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.schema_migrations import _ensure_postgresql_append_only_triggers, ensure_additive_schema


def test_additive_schema_upgrades_existing_task_table_idempotently():
    engine = create_engine("sqlite:///:memory:")
    with engine.begin() as connection:
        connection.exec_driver_sql(
            "CREATE TABLE tasks (id VARCHAR(36) PRIMARY KEY)"
        )

    ensure_additive_schema(engine)
    ensure_additive_schema(engine)

    inspector = inspect(engine)
    assert "route_reference" in {
        column["name"] for column in inspector.get_columns("tasks")
    }
    assert "outbox_messages" in inspector.get_table_names()


def test_outbox_migration_is_additive_and_reversible():
    root = Path(__file__).resolve().parents[1]
    outbox_up = (root / "migrations" / "003_transactional_outbox.up.sql").read_text("utf-8").upper()
    outbox_down = (root / "migrations" / "003_transactional_outbox.down.sql").read_text("utf-8").upper()
    assert "CREATE TABLE IF NOT EXISTS OUTBOX_MESSAGES" in outbox_up
    assert "DROP TABLE IF EXISTS OUTBOX_MESSAGES" in outbox_down
    assert "DROP TABLE COMMUNITY_NEEDS" not in outbox_up


def test_postgres_trigger_sql_has_no_unescaped_percent():
    """Regression test for a real production incident.

    _ensure_postgresql_append_only_triggers only runs when the engine dialect
    is "postgresql" (never sqlite), so the sqlite-only test above never
    exercises it. exec_driver_sql hands the string straight to psycopg2,
    which uses pyformat paramstyle — a bare "%" in "RAISE EXCEPTION '% is
    append-only'" reads as an incomplete placeholder and crashes app startup
    with "immutabledict is not a sequence" the moment Postgres is the
    dialect, i.e. the first deploy after this file changes. It must be
    "%%" to survive that substitution. Static-check the source instead of
    spinning up a real Postgres, which this test suite doesn't have.
    """
    source = py_inspect.getsource(_ensure_postgresql_append_only_triggers)
    for block in re.findall(r'"""(.*?)"""', source, re.S):
        unescaped = re.findall(r"(?<!%)%(?!%)", block)
        assert not unescaped, f"unescaped '%' will break psycopg2 pyformat substitution: {block!r}"
