from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.services import zones as zone_service

router = APIRouter()


@router.get("")
def list_zones(db: Session = Depends(get_db)):
    return zone_service.list_zones(db)


@router.post("", status_code=201)
def create_zone(
    name: str,
    center_lat: float | None = None,
    center_lng: float | None = None,
    radius_km: float | None = None,
    db: Session = Depends(get_db),
):
    try:
        return zone_service.create_zone(
            db, name, center_lat=center_lat, center_lng=center_lng, radius_km=radius_km)
    except zone_service.ZoneError as exc:
        raise ApiError(422, str(exc)) from exc


@router.delete("/{zone_id}")
def delete_zone(zone_id: str, db: Session = Depends(get_db)):
    try:
        zone_service.delete_zone(db, zone_id)
    except zone_service.ZoneError as exc:
        raise ApiError(409, str(exc)) from exc
    return {"message": "已刪除"}


@router.put("/resources/{resource_id}")
def reassign_resource(resource_id: str, zone_id: str, db: Session = Depends(get_db)):
    resource = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()
    if not resource:
        raise ApiError(404, "找不到這筆物資。")
    try:
        zone_service.reassign_resource_zone(db, resource, zone_id)
    except zone_service.ZoneError as exc:
        raise ApiError(409, str(exc)) from exc
    return {"id": resource_id, "zone_id": zone_id}


@router.put("/needs/{need_id}")
def reassign_need(need_id: str, zone_id: str, db: Session = Depends(get_db)):
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        raise ApiError(404, "找不到這筆需求。")
    try:
        zone_service.reassign_need_zone(db, need, zone_id)
    except zone_service.ZoneError as exc:
        raise ApiError(409, str(exc)) from exc
    return {"id": need_id, "zone_id": zone_id}
