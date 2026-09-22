from sqlalchemy import CheckConstraint, Column, ForeignKey, Index, Integer, Text, TIMESTAMP, event as sa_event, text
from sqlalchemy.orm import relationship
from sqlalchemy.sql import func

from app.database import Base
from app.models.base_types import GUID


class InventoryEvent(Base):
    """Append-only stock ledger entry.

    Numeric entries record an exact unit movement. Legacy entries deliberately
    keep quantity/unit null so an opaque text quantity is never treated as a
    trustworthy number.
    """

    __tablename__ = "inventory_events"
    __table_args__ = (
        CheckConstraint(
            "event_type IN ('RESERVE','RELEASE','CONSUME','ADJUST','RESERVE_LEGACY','RELEASE_LEGACY','CONSUME_LEGACY')",
            name="ck_inventory_events_type",
        ),
        CheckConstraint("quantity IS NULL OR quantity > 0", name="ck_inventory_events_quantity"),
        CheckConstraint("resource_version >= 1", name="ck_inventory_events_version"),
        Index("ix_inventory_events_resource_created", "resource_id", "created_at"),
        Index("ix_inventory_events_need_created", "need_id", "created_at"),
    )

    id = Column(GUID(), primary_key=True, default=GUID.new)
    resource_id = Column(
        GUID(), ForeignKey("community_resources.id", ondelete="RESTRICT"), nullable=False
    )
    need_id = Column(
        GUID(), ForeignKey("community_needs.id", ondelete="SET NULL"), nullable=True
    )
    event_type = Column(Text, nullable=False)
    quantity = Column(Integer, nullable=True)
    unit = Column(Text, nullable=True)
    on_hand_before = Column(Integer, nullable=True)
    on_hand_after = Column(Integer, nullable=True)
    reserved_before = Column(Integer, nullable=True)
    reserved_after = Column(Integer, nullable=True)
    resource_version = Column(Integer, nullable=False, server_default=text("1"))
    actor_id = Column(GUID(), ForeignKey("users.id", ondelete="SET NULL"), nullable=True)
    actor_label = Column(Text, nullable=True)
    details_json = Column(Text, nullable=True)
    created_at = Column(TIMESTAMP(), nullable=False, server_default=func.now())

    resource = relationship("CommunityResource", foreign_keys=[resource_id])
    need = relationship("CommunityNeed", foreign_keys=[need_id])
    actor = relationship("User", foreign_keys=[actor_id])


def _reject_inventory_event_change(_mapper, _connection, _target):
    raise ValueError("InventoryEvent is append-only")


sa_event.listen(InventoryEvent, "before_update", _reject_inventory_event_change)
sa_event.listen(InventoryEvent, "before_delete", _reject_inventory_event_change)
