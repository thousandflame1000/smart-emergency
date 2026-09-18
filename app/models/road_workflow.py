from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Text,
    TIMESTAMP,
    event as sa_event,
    text,
)
from sqlalchemy.dialects.postgresql import JSONB
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID


JsonDocument = JSONB().with_variant(JSON(), "sqlite")


class RoadObservation(Base):
    __tablename__ = "road_observations"
    __table_args__ = (
        CheckConstraint(
            "state IN ('OPEN','SLOW','BLOCKED')",
            name="ck_road_observations_state",
        ),
        Index(
            "ix_road_observations_segment_observed",
            "road_segment_id",
            "observed_at",
            "created_at",
        ),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    road_segment_id = Column(Text, nullable=False)
    state = Column(Text, nullable=False)
    source = Column(Text, nullable=False)
    observed_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    created_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    details_json = Column(
        "details",
        JsonDocument,
        nullable=False,
        default=dict,
        server_default=text("'{}'"),
    )


class TaskRoute(Base):
    __tablename__ = "task_routes"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ACTIVE','PROPOSED','BLOCKED','SUPERSEDED','UNREACHABLE')",
            name="ck_task_routes_status",
        ),
        Index("ix_task_routes_task_status", "task_id", "status"),
        Index("ix_task_routes_proposal", "proposal_id"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    task_id = Column(GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    proposal_id = Column(
        GUID(), ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True
    )
    start_node = Column(Text, nullable=False)
    end_node = Column(Text, nullable=False)
    node_path = Column(JsonDocument, nullable=False, default=list, server_default=text("'[]'"))
    segment_ids = Column(JsonDocument, nullable=False, default=list, server_default=text("'[]'"))
    distance_km = Column(Float, nullable=True)
    status = Column(Text, nullable=False, default="ACTIVE", server_default=text("'ACTIVE'"))
    created_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    superseded_at = Column(TIMESTAMP(), nullable=True)

    task = relationship("Task", foreign_keys=[task_id])
    proposal = relationship("Proposal", foreign_keys=[proposal_id])


def _reject_observation_change(_mapper, _connection, target):
    raise ValueError(f"{target.__class__.__name__} is append-only")


sa_event.listen(RoadObservation, "before_update", _reject_observation_change)
sa_event.listen(RoadObservation, "before_delete", _reject_observation_change)
