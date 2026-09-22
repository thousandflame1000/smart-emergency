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

    if "knowledge_base" in inspector.get_table_names():
        kb_columns = {column["name"] for column in inspector.get_columns("knowledge_base")}
        if "version" not in kb_columns:
            with engine.begin() as connection:
                connection.exec_driver_sql("ALTER TABLE knowledge_base ADD COLUMN version TEXT")

    if "community_resources" in inspector.get_table_names():
        resource_columns = {column["name"] for column in inspector.get_columns("community_resources")}
        additions = {
            "quantity_amount": "INTEGER",
            "quantity_unit": "TEXT",
            "reserved_amount": "INTEGER NOT NULL DEFAULT 0",
            "inventory_version": "INTEGER NOT NULL DEFAULT 1",
        }
        with engine.begin() as connection:
            for name, sql_type in additions.items():
                if name not in resource_columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE community_resources ADD COLUMN {name} {sql_type}"
                    )

    if "community_needs" in inspector.get_table_names():
        need_columns = {column["name"] for column in inspector.get_columns("community_needs")}
        additions = {
            "quantity_amount": "INTEGER",
            "quantity_unit": "TEXT",
            "reserved_quantity_amount": "INTEGER NOT NULL DEFAULT 0",
            "fulfilled_quantity_amount": "INTEGER NOT NULL DEFAULT 0",
        }
        with engine.begin() as connection:
            for name, sql_type in additions.items():
                if name not in need_columns:
                    connection.exec_driver_sql(
                        f"ALTER TABLE community_needs ADD COLUMN {name} {sql_type}"
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
                RAISE EXCEPTION '%% is append-only', TG_TABLE_NAME;
            END;
            $$ LANGUAGE plpgsql
            """
        )
        for table_name, trigger_name in (
            ("approvals", "approvals_append_only"),
            ("task_events", "task_events_append_only"),
            ("inventory_events", "inventory_events_append_only"),
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
