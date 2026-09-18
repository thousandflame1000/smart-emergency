from __future__ import annotations

from sqlalchemy import inspect
from sqlalchemy.engine import Engine

from app.database import Base


def ensure_additive_schema(engine: Engine) -> None:
    """Apply the small additive changes create_all cannot add to existing tables."""
    import app.models  # noqa: F401

    Base.metadata.create_all(bind=engine)
    inspector = inspect(engine)
    if "tasks" in inspector.get_table_names():
        columns = {column["name"] for column in inspector.get_columns("tasks")}
        if "route_reference" not in columns:
            with engine.begin() as connection:
                connection.exec_driver_sql(
                    "ALTER TABLE tasks ADD COLUMN route_reference VARCHAR(36)"
                )

    if engine.dialect.name == "postgresql":
        _ensure_postgresql_append_only_triggers(engine)


def _ensure_postgresql_append_only_triggers(engine: Engine) -> None:
    with engine.begin() as connection:
        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION reject_task_workflow_event_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name, trigger_name in (
            ("approvals", "approvals_append_only"),
            ("task_events", "task_events_append_only"),
        ):
            connection.exec_driver_sql(
                f"DROP TRIGGER IF EXISTS {trigger_name} ON {table_name}"
            )
            connection.exec_driver_sql(
                f"""
                CREATE TRIGGER {trigger_name}
                BEFORE UPDATE OR DELETE ON {table_name}
                FOR EACH ROW EXECUTE FUNCTION reject_task_workflow_event_mutation()
                """
            )

        connection.exec_driver_sql(
            """
            CREATE OR REPLACE FUNCTION reject_road_observation_mutation()
            RETURNS trigger AS $$
            BEGIN
                RAISE EXCEPTION '% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        connection.exec_driver_sql(
            "DROP TRIGGER IF EXISTS road_observations_append_only ON road_observations"
        )
        connection.exec_driver_sql(
            """
            CREATE TRIGGER road_observations_append_only
            BEFORE UPDATE OR DELETE ON road_observations
            FOR EACH ROW EXECUTE FUNCTION reject_road_observation_mutation()
            """
        )
