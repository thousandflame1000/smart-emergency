"""Quantity-aware stock reservation and append-only inventory accounting."""
from __future__ import annotations

import json
import re
from dataclasses import dataclass
from datetime import UTC, datetime

from sqlalchemy.orm import Session

from app.models.inventory import InventoryEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource


MAX_QUANTITY = 1_000_000
_QUANTITY_RE = re.compile(r"^\s*(\d+)\s*(\S{0,20})\s*$")
_INVALID_UNIT_MARKS = frozenset(",/+")


def _now():
    return datetime.now(UTC).replace(tzinfo=None)


class InventoryError(ValueError):
    pass


@dataclass(frozen=True)
class InventoryMovement:
    mode: str
    quantity: int | None
    unit: str | None
    available_after: int | None


def quantity_parts(value: str | None) -> tuple[int, str] | None:
    """Parse a non-negative integer and one opaque unit without converting units."""
    match = _QUANTITY_RE.fullmatch(value or "")
    if not match:
        return None
    amount = int(match.group(1))
    unit = match.group(2) or "unit"
    if amount > MAX_QUANTITY or any(mark in unit for mark in _INVALID_UNIT_MARKS):
        return None
    return amount, unit


def structured_quantity(row) -> tuple[int, str] | None:
    amount = getattr(row, "quantity_amount", None)
    unit = (getattr(row, "quantity_unit", None) or "").strip()
    if amount is not None and unit:
        return int(amount), unit
    return quantity_parts(getattr(row, "quantity", None))


def set_quantity_fields(row, value: str | None) -> tuple[int, str] | None:
    parsed = quantity_parts(value)
    row.quantity = value
    row.quantity_amount = parsed[0] if parsed else None
    row.quantity_unit = parsed[1] if parsed else None
    return parsed


def available_amount(resource: CommunityResource) -> int | None:
    parsed = structured_quantity(resource)
    if parsed is None:
        return None
    return max(0, parsed[0] - int(resource.reserved_amount or 0))


def _event(
    db: Session,
    *,
    resource: CommunityResource,
    need: CommunityNeed,
    event_type: str,
    quantity: int | None,
    unit: str | None,
    on_hand_before: int | None,
    on_hand_after: int | None,
    reserved_before: int | None,
    reserved_after: int | None,
    version: int,
    actor_id: str | None,
    actor_label: str | None,
    details: dict | None = None,
) -> None:
    db.add(
        InventoryEvent(
            resource_id=resource.id,
            need_id=need.id,
            event_type=event_type,
            quantity=quantity,
            unit=unit,
            on_hand_before=on_hand_before,
            on_hand_after=on_hand_after,
            reserved_before=reserved_before,
            reserved_after=reserved_after,
            resource_version=version,
            actor_id=actor_id,
            actor_label=actor_label,
            details_json=json.dumps(details or {}, ensure_ascii=False),
        )
    )


def reserve_for_need(
    db: Session,
    resource: CommunityResource,
    need: CommunityNeed,
    *,
    actor_id: str | None = None,
    actor_label: str | None = None,
) -> InventoryMovement:
    """Reserve stock for one need without changing the dispatch status."""
    same_reservation = (
        str(need.matched_resource_id or "") == str(resource.id)
        and need.status in ("suggested", "matched")
    )
    if same_reservation:
        return InventoryMovement(
            "structured" if int(need.reserved_quantity_amount or 0) else "legacy",
            int(need.reserved_quantity_amount or 0) or None,
            need.quantity_unit,
            available_amount(resource),
        )

    resource_quantity = structured_quantity(resource)
    need_quantity = structured_quantity(need)
    quantity_inferred = False
    if resource_quantity is not None and need_quantity is None:
        need_quantity = (1, resource_quantity[1])
        quantity_inferred = True
    version_before = int(resource.inventory_version or 1)
    version_after = version_before + 1

    if resource_quantity is not None and need_quantity is not None:
        on_hand, resource_unit = resource_quantity
        requested, need_unit = need_quantity
        if requested <= 0:
            raise InventoryError("Requested quantity must be greater than zero")
        if resource_unit != need_unit:
            raise InventoryError(
                f"Quantity unit mismatch: resource uses {resource_unit}, need uses {need_unit}"
            )
        reserved_before = int(resource.reserved_amount or 0)
        if on_hand - reserved_before < requested:
            raise InventoryError("Insufficient unreserved stock")
        reserved_after = reserved_before + requested
        updates = {
            "quantity_amount": on_hand,
            "quantity_unit": resource_unit,
            "reserved_amount": reserved_after,
            "inventory_version": version_after,
            "is_available": on_hand - reserved_after > 0,
            "last_updated": _now(),
        }
        changed = (
            db.query(CommunityResource)
            .filter(
                CommunityResource.id == resource.id,
                CommunityResource.inventory_version == version_before,
            )
            .update(updates, synchronize_session=False)
        )
        if changed != 1:
            raise InventoryError("Inventory changed concurrently; reload and retry")
        db.query(CommunityNeed).filter(CommunityNeed.id == need.id).update(
            {
                "quantity_amount": requested,
                "quantity_unit": need_unit,
                "reserved_quantity_amount": requested,
            },
            synchronize_session=False,
        )
        db.refresh(resource)
        db.refresh(need)
        _event(
            db,
            resource=resource,
            need=need,
            event_type="RESERVE",
            quantity=requested,
            unit=resource_unit,
            on_hand_before=on_hand,
            on_hand_after=on_hand,
            reserved_before=reserved_before,
            reserved_after=reserved_after,
            version=version_after,
            actor_id=actor_id,
            actor_label=actor_label,
            details={"quantity_inferred": quantity_inferred},
        )
        return InventoryMovement("structured", requested, resource_unit, on_hand - reserved_after)

    if not resource.is_available:
        raise InventoryError("Legacy stock lot is already reserved")
    changed = (
        db.query(CommunityResource)
        .filter(
            CommunityResource.id == resource.id,
            CommunityResource.inventory_version == version_before,
            CommunityResource.is_available == True,  # noqa: E712
        )
        .update(
            {"is_available": False, "inventory_version": version_after, "last_updated": _now()},
            synchronize_session=False,
        )
    )
    if changed != 1:
        raise InventoryError("Inventory changed concurrently; reload and retry")
    db.refresh(resource)
    _event(
        db,
        resource=resource,
        need=need,
        event_type="RESERVE_LEGACY",
        quantity=None,
        unit=None,
        on_hand_before=None,
        on_hand_after=None,
        reserved_before=None,
        reserved_after=None,
        version=version_after,
        actor_id=actor_id,
        actor_label=actor_label,
        details={"quantity_text": resource.quantity or ""},
    )
    return InventoryMovement("legacy", None, None, None)


def release_for_need(
    db: Session,
    resource: CommunityResource,
    need: CommunityNeed,
    *,
    actor_id: str | None = None,
    actor_label: str | None = None,
) -> InventoryMovement:
    """Release the exact reservation held by a need."""
    amount = int(need.reserved_quantity_amount or 0)
    version_before = int(resource.inventory_version or 1)
    version_after = version_before + 1
    resource_quantity = structured_quantity(resource)

    if amount and resource_quantity is not None:
        on_hand, unit = resource_quantity
        reserved_before = int(resource.reserved_amount or 0)
        if amount > reserved_before:
            raise InventoryError("Reservation ledger is inconsistent")
        reserved_after = reserved_before - amount
        changed = (
            db.query(CommunityResource)
            .filter(
                CommunityResource.id == resource.id,
                CommunityResource.inventory_version == version_before,
            )
            .update(
                {
                    "reserved_amount": reserved_after,
                    "inventory_version": version_after,
                    "is_available": on_hand - reserved_after > 0,
                    "last_updated": _now(),
                },
                synchronize_session=False,
            )
        )
        if changed != 1:
            raise InventoryError("Inventory changed concurrently; reload and retry")
        db.query(CommunityNeed).filter(CommunityNeed.id == need.id).update(
            {"reserved_quantity_amount": 0}, synchronize_session=False
        )
        db.refresh(resource)
        db.refresh(need)
        _event(
            db,
            resource=resource,
            need=need,
            event_type="RELEASE",
            quantity=amount,
            unit=unit,
            on_hand_before=on_hand,
            on_hand_after=on_hand,
            reserved_before=reserved_before,
            reserved_after=reserved_after,
            version=version_after,
            actor_id=actor_id,
            actor_label=actor_label,
        )
        return InventoryMovement("structured", amount, unit, on_hand - reserved_after)

    changed = (
        db.query(CommunityResource)
        .filter(
            CommunityResource.id == resource.id,
            CommunityResource.inventory_version == version_before,
        )
        .update(
            {"is_available": True, "inventory_version": version_after, "last_updated": _now()},
            synchronize_session=False,
        )
    )
    if changed != 1:
        raise InventoryError("Inventory changed concurrently; reload and retry")
    db.refresh(resource)
    _event(
        db,
        resource=resource,
        need=need,
        event_type="RELEASE_LEGACY",
        quantity=None,
        unit=None,
        on_hand_before=None,
        on_hand_after=None,
        reserved_before=None,
        reserved_after=None,
        version=version_after,
        actor_id=actor_id,
        actor_label=actor_label,
    )
    return InventoryMovement("legacy", None, None, None)


def consume_for_need(
    db: Session,
    resource: CommunityResource,
    need: CommunityNeed,
    *,
    actor_id: str | None = None,
    actor_label: str | None = None,
) -> InventoryMovement:
    """Convert a reservation into delivered stock and close its ledger position."""
    amount = int(need.reserved_quantity_amount or 0)
    version_before = int(resource.inventory_version or 1)
    version_after = version_before + 1
    resource_quantity = structured_quantity(resource)

    if amount and resource_quantity is not None:
        on_hand, unit = resource_quantity
        reserved_before = int(resource.reserved_amount or 0)
        if amount > reserved_before or amount > on_hand:
            raise InventoryError("Reservation ledger is inconsistent")
        on_hand_after = on_hand - amount
        reserved_after = reserved_before - amount
        changed = (
            db.query(CommunityResource)
            .filter(
                CommunityResource.id == resource.id,
                CommunityResource.inventory_version == version_before,
            )
            .update(
                {
                    "quantity": f"{on_hand_after}{unit}",
                    "quantity_amount": on_hand_after,
                    "quantity_unit": unit,
                    "reserved_amount": reserved_after,
                    "inventory_version": version_after,
                    "is_available": on_hand_after - reserved_after > 0,
                    "last_updated": _now(),
                },
                synchronize_session=False,
            )
        )
        if changed != 1:
            raise InventoryError("Inventory changed concurrently; reload and retry")
        db.query(CommunityNeed).filter(CommunityNeed.id == need.id).update(
            {
                "reserved_quantity_amount": 0,
                "fulfilled_quantity_amount": int(need.fulfilled_quantity_amount or 0) + amount,
            },
            synchronize_session=False,
        )
        db.refresh(resource)
        db.refresh(need)
        _event(
            db,
            resource=resource,
            need=need,
            event_type="CONSUME",
            quantity=amount,
            unit=unit,
            on_hand_before=on_hand,
            on_hand_after=on_hand_after,
            reserved_before=reserved_before,
            reserved_after=reserved_after,
            version=version_after,
            actor_id=actor_id,
            actor_label=actor_label,
        )
        return InventoryMovement(
            "structured", amount, unit, on_hand_after - reserved_after
        )

    changed = (
        db.query(CommunityResource)
        .filter(
            CommunityResource.id == resource.id,
            CommunityResource.inventory_version == version_before,
        )
        .update(
            {"is_available": False, "inventory_version": version_after, "last_updated": _now()},
            synchronize_session=False,
        )
    )
    if changed != 1:
        raise InventoryError("Inventory changed concurrently; reload and retry")
    db.refresh(resource)
    _event(
        db,
        resource=resource,
        need=need,
        event_type="CONSUME_LEGACY",
        quantity=None,
        unit=None,
        on_hand_before=None,
        on_hand_after=None,
        reserved_before=None,
        reserved_after=None,
        version=version_after,
        actor_id=actor_id,
        actor_label=actor_label,
        details={"quantity_text": resource.quantity or ""},
    )
    return InventoryMovement("legacy", None, None, None)
