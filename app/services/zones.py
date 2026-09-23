"""Zones partition resources and needs; `general` is the fallback everything starts in.

Matching and the workspace snapshot filter strictly by zone_id equality — there is no
wildcard "visible from every zone" state. That keeps the rule simple: nothing can
match, or get pulled into a workspace, across a zone boundary except by an explicit
admin reassignment (which goes through the same row_predicates-guarded update the
rest of the codebase uses for every other state change, so a reassignment racing
another operation on the same row loses cleanly instead of silently corrupting it).
"""
from __future__ import annotations

from uuid import uuid4

from sqlalchemy import func
from sqlalchemy.orm import Session

from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.zone import GENERAL_ZONE_ID, GENERAL_ZONE_NAME, Zone
from app.services.geo import haversine_km
from app.services.record_version import row_predicates


class ZoneError(ValueError):
    pass


def ensure_general_zone(db: Session) -> None:
    """Idempotently seed the one zone that must always exist. Safe to call every startup."""
    if db.get(Zone, GENERAL_ZONE_ID) is None:
        db.add(Zone(id=GENERAL_ZONE_ID, name=GENERAL_ZONE_NAME))
        db.commit()


def list_zones(db: Session) -> list[dict]:
    zones = db.query(Zone).order_by(Zone.created_at).all()
    resource_counts = dict(
        db.query(CommunityResource.zone_id, func.count(CommunityResource.id))
        .group_by(CommunityResource.zone_id).all()
    )
    need_counts = dict(
        db.query(CommunityNeed.zone_id, func.count(CommunityNeed.id))
        .group_by(CommunityNeed.zone_id).all()
    )
    return [
        _serialize_zone(z, resource_count=resource_counts.get(z.id, 0), need_count=need_counts.get(z.id, 0))
        for z in zones
    ]


def create_zone(
    db: Session,
    name: str,
    *,
    center_lat: float | None = None,
    center_lng: float | None = None,
    radius_km: float | None = None,
) -> dict:
    name = (name or "").strip()
    if not name:
        raise ZoneError("分區名稱不可空白")
    if len(name) > 100:
        raise ZoneError("分區名稱過長")
    if db.query(Zone.id).filter(Zone.name == name).first():
        raise ZoneError("已有同名分區")
    if (center_lat is None) != (center_lng is None):
        raise ZoneError("中心點的經緯度必須成對提供")
    if center_lat is not None and not (-90 <= center_lat <= 90 and -180 <= center_lng <= 180):
        raise ZoneError("中心點座標超出範圍")
    if radius_km is not None and (center_lat is None or radius_km <= 0):
        raise ZoneError("要設定範圍必須同時給中心點，且半徑要大於零")
    zone = Zone(id=uuid4().hex, name=name, center_lat=center_lat, center_lng=center_lng, radius_km=radius_km)
    db.add(zone)
    db.commit()
    return _serialize_zone(zone, resource_count=0, need_count=0)


def _serialize_zone(zone: Zone, *, resource_count: int, need_count: int) -> dict:
    return {
        "id": zone.id,
        "name": zone.name,
        "is_general": zone.id == GENERAL_ZONE_ID,
        "center_lat": zone.center_lat,
        "center_lng": zone.center_lng,
        "radius_km": zone.radius_km,
        "resource_count": resource_count,
        "need_count": need_count,
        "created_at": str(zone.created_at),
    }


def resolve_zone_for_point(db: Session, lat: float | None, lng: float | None) -> str:
    """The nearest zone whose radius contains this point, or `general` if none does
    (including when lat/lng is unknown — nothing to resolve against)."""
    if lat is None or lng is None:
        return GENERAL_ZONE_ID
    candidates = db.query(Zone).filter(
        Zone.center_lat.isnot(None), Zone.center_lng.isnot(None), Zone.radius_km.isnot(None)
    ).all()
    best_id, best_dist = None, None
    for zone in candidates:
        dist = haversine_km(lat, lng, zone.center_lat, zone.center_lng)
        if dist <= zone.radius_km and (best_dist is None or dist < best_dist):
            best_id, best_dist = zone.id, dist
    return best_id or GENERAL_ZONE_ID


def delete_zone(db: Session, zone_id: str) -> None:
    if zone_id == GENERAL_ZONE_ID:
        raise ZoneError("general 是系統預設分區，不能刪除")
    zone = db.get(Zone, zone_id)
    if not zone:
        raise ZoneError("找不到這個分區")
    still_used = (
        db.query(CommunityResource.id).filter(CommunityResource.zone_id == zone_id).first()
        or db.query(CommunityNeed.id).filter(CommunityNeed.zone_id == zone_id).first()
    )
    if still_used:
        raise ZoneError("這個分區底下還有物資或需求，請先全部改分區後再刪除")
    db.delete(zone)
    db.commit()


def _resolve_zone(db: Session, zone_id: str) -> Zone:
    zone = db.get(Zone, zone_id)
    if not zone:
        raise ZoneError("找不到這個分區")
    return zone


def reassign_resource_zone(db: Session, resource: CommunityResource, zone_id: str) -> None:
    """Move one resource to another zone with the same compare-and-swap guard every
    other write in this codebase uses, so this can never race a concurrent dispatch
    reservation or another admin's edit into a corrupted in-between state."""
    _resolve_zone(db, zone_id)
    changed = db.query(CommunityResource).filter(*row_predicates(resource)).update(
        {"zone_id": zone_id}, synchronize_session=False)
    if changed != 1:
        db.rollback()
        raise ZoneError("物資已由另一個操作修改，請重新載入")
    db.commit()


def reassign_need_zone(db: Session, need: CommunityNeed, zone_id: str) -> None:
    _resolve_zone(db, zone_id)
    changed = db.query(CommunityNeed).filter(*row_predicates(need)).update(
        {"zone_id": zone_id}, synchronize_session=False)
    if changed != 1:
        db.rollback()
        raise ZoneError("需求已由另一個操作修改，請重新載入")
    db.commit()
