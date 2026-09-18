from pathlib import Path

from sqlalchemy import create_engine, inspect

from app.schema_migrations import ensure_additive_schema


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
    assert {
        "outbox_messages",
        "road_observations",
        "task_routes",
    }.issubset(set(inspector.get_table_names()))


def test_outbox_and_road_migrations_are_additive_and_reversible_definitions():
    root = Path(__file__).resolve().parents[1]
    outbox_up = (root / "migrations" / "003_transactional_outbox.up.sql").read_text("utf-8").upper()
    outbox_down = (root / "migrations" / "003_transactional_outbox.down.sql").read_text("utf-8").upper()
    road_up = (root / "migrations" / "004_road_replanning.up.sql").read_text("utf-8").upper()
    road_down = (root / "migrations" / "004_road_replanning.down.sql").read_text("utf-8").upper()

    assert "CREATE TABLE IF NOT EXISTS OUTBOX_MESSAGES" in outbox_up
    assert "DROP TABLE IF EXISTS OUTBOX_MESSAGES" in outbox_down
    assert "CREATE TABLE IF NOT EXISTS ROAD_OBSERVATIONS" in road_up
    assert "CREATE TABLE IF NOT EXISTS TASK_ROUTES" in road_up
    assert "ALTER TABLE TASKS ADD COLUMN IF NOT EXISTS ROUTE_REFERENCE" in road_up
    assert "DROP TABLE IF EXISTS TASK_ROUTES" in road_down
    assert "DROP TABLE IF EXISTS ROAD_OBSERVATIONS" in road_down
    assert "DROP TABLE COMMUNITY_NEEDS" not in outbox_up + road_up
