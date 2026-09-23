from app.models.inventory import InventoryEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.services import dispatch


def _world(db):
    volunteer = User(name="Stock volunteer", roles=["volunteer"])
    resident_a = User(name="Resident A", roles=["elderly"])
    resident_b = User(name="Resident B", roles=["elderly"])
    db.add_all([volunteer, resident_a, resident_b])
    db.commit()

    resource = CommunityResource(
        owner_id=volunteer.id,
        resource_type="water",
        name="Water cases",
        quantity="20case",
        quantity_amount=20,
        quantity_unit="case",
        lat=24.0,
        lng=120.6,
    )
    need_a = CommunityNeed(
        requester_id=resident_a.id,
        need_type="water",
        quantity="5case",
        quantity_amount=5,
        quantity_unit="case",
        lat=24.001,
        lng=120.601,
    )
    need_b = CommunityNeed(
        requester_id=resident_b.id,
        need_type="water",
        quantity="15case",
        quantity_amount=15,
        quantity_unit="case",
        lat=24.002,
        lng=120.602,
    )
    db.add_all([resource, need_a, need_b])
    db.commit()
    return resource, need_a, need_b


def test_reserve_release_and_consume_are_quantity_aware_and_audited(db):
    resource, need_a, need_b = _world(db)

    first = dispatch.manual_dispatch(str(need_a.id), str(resource.id), db)
    assert "error" not in first
    db.refresh(resource)
    db.refresh(need_a)
    assert resource.quantity_amount == 20
    assert resource.reserved_amount == 5
    assert resource.is_available is True
    assert need_a.reserved_quantity_amount == 5

    second = dispatch.manual_dispatch(str(need_b.id), str(resource.id), db)
    assert "error" not in second
    db.refresh(resource)
    assert resource.quantity_amount == 20
    assert resource.reserved_amount == 20
    assert resource.is_available is False

    delivered = dispatch.mark_task_delivered(str(need_a.id), db, actor_label="test")
    assert "error" not in delivered
    db.refresh(resource)
    db.refresh(need_a)
    assert resource.quantity == "15case"
    assert resource.quantity_amount == 15
    assert resource.reserved_amount == 15
    assert resource.is_available is False
    assert need_a.reserved_quantity_amount == 0
    assert need_a.fulfilled_quantity_amount == 5

    declined = dispatch.decline_task_assignment(str(need_b.id), db, actor_label="test")
    assert "error" not in declined
    db.refresh(resource)
    db.refresh(need_b)
    assert resource.quantity_amount == 15
    assert resource.reserved_amount == 0
    assert resource.is_available is True
    assert need_b.reserved_quantity_amount == 0

    events = (
        db.query(InventoryEvent)
        .filter(InventoryEvent.resource_id == resource.id)
        .order_by(InventoryEvent.resource_version)
        .all()
    )
    assert [event.event_type for event in events] == [
        "RESERVE",
        "RESERVE",
        "CONSUME",
        "RELEASE",
    ]
    assert [(event.quantity, event.unit) for event in events] == [
        (5, "case"),
        (15, "case"),
        (5, "case"),
        (15, "case"),
    ]


def test_over_reservation_and_unit_mismatch_leave_rows_unchanged(db):
    resource, need_a, need_b = _world(db)
    assert "error" not in dispatch.manual_dispatch(str(need_a.id), str(resource.id), db)

    need_b.quantity = "16case"
    need_b.quantity_amount = 16
    db.commit()
    failed = dispatch.manual_dispatch(str(need_b.id), str(resource.id), db)
    assert "error" in failed
    db.refresh(resource)
    db.refresh(need_b)
    assert resource.reserved_amount == 5
    assert need_b.status == "open"
    assert need_b.matched_resource_id is None

    need_b.quantity = "1bottle"
    need_b.quantity_amount = 1
    need_b.quantity_unit = "bottle"
    db.commit()
    mismatch = dispatch.manual_dispatch(str(need_b.id), str(resource.id), db)
    assert "unit mismatch" in mismatch["error"].lower()
    assert db.query(InventoryEvent).filter(InventoryEvent.resource_id == resource.id).count() == 1


def test_missing_need_quantity_reserves_one_registered_unit(db):
    resource, need_a, _ = _world(db)
    need_a.quantity = None
    need_a.quantity_amount = None
    need_a.quantity_unit = None
    db.commit()

    result = dispatch.manual_dispatch(str(need_a.id), str(resource.id), db)
    assert "error" not in result
    db.refresh(resource)
    db.refresh(need_a)
    assert resource.reserved_amount == 1
    assert need_a.quantity_amount == 1
    assert need_a.quantity_unit == "case"
    event = db.query(InventoryEvent).filter_by(resource_id=resource.id).one()
    assert event.event_type == "RESERVE"
    assert '"quantity_inferred": true' in event.details_json


def test_duplicate_delivery_does_not_consume_twice(db):
    resource, need_a, _ = _world(db)
    assert "error" not in dispatch.manual_dispatch(str(need_a.id), str(resource.id), db)
    assert "error" not in dispatch.mark_task_delivered(str(need_a.id), db)
    again = dispatch.mark_task_delivered(str(need_a.id), db)
    assert again["already_fulfilled"] is True
    db.refresh(resource)
    assert resource.quantity_amount == 15
    assert db.query(InventoryEvent).filter_by(
        resource_id=resource.id, event_type="CONSUME"
    ).count() == 1
