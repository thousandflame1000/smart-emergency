from __future__ import annotations

from sqlalchemy import (
    JSON,
    CheckConstraint,
    Column,
    Float,
    ForeignKey,
    Index,
    Integer,
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


class Proposal(Base):
    __tablename__ = "proposals"
    __table_args__ = (
        CheckConstraint(
            "status IN ('DRAFT','PENDING_APPROVAL','APPROVED','REJECTED','SUPERSEDED','CANCELLED')",
            name="ck_proposals_status",
        ),
        CheckConstraint("version >= 1", name="ck_proposals_version"),
        Index("ix_proposals_need_status", "need_id", "status"),
        Index("ix_proposals_fingerprint", "fingerprint"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    need_id = Column(
        GUID(), ForeignKey("community_needs.id", ondelete="RESTRICT"), nullable=False
    )
    status = Column(
        Text, nullable=False, default="DRAFT", server_default=text("'DRAFT'")
    )
    created_by = Column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    created_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    algorithm = Column(Text, nullable=False)
    algorithm_version = Column(Text, nullable=False)
    score = Column(Float, nullable=True)
    explanation_json = Column(
        JsonDocument, nullable=False, default=dict, server_default=text("'{}'")
    )
    candidate_resource_id = Column(
        GUID(), ForeignKey("community_resources.id", ondelete="SET NULL"), nullable=True
    )
    candidate_assignee_id = Column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    candidate_facility_id = Column(
        GUID(), ForeignKey("resource_points.id", ondelete="SET NULL"), nullable=True
    )
    route_reference = Column(Text, nullable=True)
    fingerprint = Column(Text, nullable=False)
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))

    need = relationship("CommunityNeed", foreign_keys=[need_id])
    creator = relationship("User", foreign_keys=[created_by])
    candidate_resource = relationship("CommunityResource", foreign_keys=[candidate_resource_id])
    candidate_assignee = relationship("User", foreign_keys=[candidate_assignee_id])
    candidate_facility = relationship("ResourcePoint", foreign_keys=[candidate_facility_id])


class Approval(Base):
    __tablename__ = "approvals"
    __table_args__ = (
        CheckConstraint(
            "decision IN ('APPROVE','REJECT','MODIFY')",
            name="ck_approvals_decision",
        ),
        Index("ix_approvals_proposal_created", "proposal_id", "created_at"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    proposal_id = Column(
        GUID(), ForeignKey("proposals.id", ondelete="RESTRICT"), nullable=False
    )
    actor_id = Column(GUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    decision = Column(Text, nullable=False)
    comment = Column(Text, nullable=True)
    proposal_version = Column(Integer, nullable=False)
    created_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())

    proposal = relationship("Proposal", foreign_keys=[proposal_id])
    actor = relationship("User", foreign_keys=[actor_id])


class Task(Base):
    __tablename__ = "tasks"
    __table_args__ = (
        CheckConstraint(
            "status IN ('APPROVED','ASSIGNED','ACKNOWLEDGED','IN_PROGRESS','ARRIVED','COMPLETED','FAILED','CANCELLED','BLOCKED')",
            name="ck_tasks_status",
        ),
        CheckConstraint("priority BETWEEN 1 AND 5", name="ck_tasks_priority"),
        CheckConstraint("version >= 1", name="ck_tasks_version"),
        Index("ix_tasks_need_status", "need_id", "status"),
        Index("ix_tasks_proposal", "proposal_id"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    need_id = Column(
        GUID(), ForeignKey("community_needs.id", ondelete="RESTRICT"), nullable=False
    )
    proposal_id = Column(
        GUID(), ForeignKey("proposals.id", ondelete="SET NULL"), nullable=True
    )
    task_type = Column(Text, nullable=False)
    status = Column(
        Text, nullable=False, default="APPROVED", server_default=text("'APPROVED'")
    )
    priority = Column(Integer, nullable=False, default=3, server_default=text("3"))
    instructions = Column(Text, nullable=True)
    destination_reference = Column(Text, nullable=True)
    route_reference = Column(Text, nullable=True)
    version = Column(Integer, nullable=False, default=1, server_default=text("1"))
    approved_by = Column(
        GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True
    )
    approved_at = Column(TIMESTAMP(), nullable=True)
    created_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    updated_at = Column(
        TIMESTAMP(), nullable=False, server_default=func.now(), onupdate=func.now()
    )

    need = relationship("CommunityNeed", foreign_keys=[need_id])
    proposal = relationship("Proposal", foreign_keys=[proposal_id])
    approver = relationship("User", foreign_keys=[approved_by])
    assignments = relationship(
        "TaskAssignment", back_populates="task", order_by="TaskAssignment.assigned_at"
    )
    events = relationship("TaskEvent", back_populates="task", order_by="TaskEvent.occurred_at")


class TaskAssignment(Base):
    __tablename__ = "task_assignments"
    __table_args__ = (
        CheckConstraint(
            "status IN ('ASSIGNED','ACCEPTED','DECLINED','REVOKED')",
            name="ck_task_assignments_status",
        ),
        CheckConstraint("task_version >= 1", name="ck_task_assignments_version"),
        Index("ix_task_assignments_task_status", "task_id", "status"),
        Index("ix_task_assignments_assignee_status", "assignee_id", "status"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    task_id = Column(GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    assignee_id = Column(GUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    status = Column(
        Text, nullable=False, default="ASSIGNED", server_default=text("'ASSIGNED'")
    )
    assigned_by = Column(GUID(), ForeignKey("users.id", ondelete="RESTRICT"), nullable=False)
    assigned_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    responded_at = Column(TIMESTAMP(), nullable=True)
    task_version = Column(Integer, nullable=False)

    task = relationship("Task", back_populates="assignments", foreign_keys=[task_id])
    assignee = relationship("User", foreign_keys=[assignee_id])
    assigner = relationship("User", foreign_keys=[assigned_by])


class TaskEvent(Base):
    __tablename__ = "task_events"
    __table_args__ = (
        CheckConstraint("task_version >= 1", name="ck_task_events_version"),
        Index("ix_task_events_task_occurred", "task_id", "occurred_at"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    task_id = Column(GUID(), ForeignKey("tasks.id", ondelete="CASCADE"), nullable=False)
    task_version = Column(Integer, nullable=False)
    event_type = Column(Text, nullable=False)
    actor_id = Column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    occurred_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    received_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())
    metadata_json = Column("metadata", JsonDocument, nullable=False, default=dict, server_default=text("'{}'"))

    task = relationship("Task", back_populates="events", foreign_keys=[task_id])
    actor = relationship("User", foreign_keys=[actor_id])


def _reject_immutable_change(_mapper, _connection, target):
    raise ValueError(f"{target.__class__.__name__} is append-only")


for immutable_model in (Approval, TaskEvent):
    sa_event.listen(immutable_model, "before_update", _reject_immutable_change)
    sa_event.listen(immutable_model, "before_delete", _reject_immutable_change)
