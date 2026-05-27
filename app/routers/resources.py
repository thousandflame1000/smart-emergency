from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from datetime import datetime

from app.database import get_db
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.user import User

router = APIRouter()


# ──────────────────────────────────────────────
# 物資
# ──────────────────────────────────────────────
@router.get("/")
def list_resources(
    resource_type: str | None = None,
    available_only: bool = True,
    db: Session = Depends(get_db),
):
    q = db.query(CommunityResource)
    if available_only:
        q = q.filter(CommunityResource.is_available == True)
    if resource_type:
        q = q.filter(CommunityResource.resource_type == resource_type)

    resources = q.order_by(CommunityResource.created_at.desc()).all()
    return [
        {
            "id":            str(r.id),
            "owner":         r.owner.name if r.owner else "unknown",
            "resource_type": r.resource_type,
            "name":          r.name,
            "quantity":      r.quantity,
            "address":       r.address,
            "lat":           r.lat,
            "lng":           r.lng,
            "note":          r.note,
            "is_available":  r.is_available,
            "last_updated":  str(r.last_updated),
        }
        for r in resources
    ]


@router.post("/")
def create_resource(
    resource_type: str,
    name: str,
    owner_line_uid: str | None = None,
    owner_id: str | None = None,
    quantity: str | None = None,
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    note: str | None = None,
    db: Session = Depends(get_db),
):
    from fastapi import HTTPException
    if owner_id:
        owner = db.query(User).filter(User.id == owner_id).first()
    elif owner_line_uid:
        owner = db.query(User).filter(User.line_uid == owner_line_uid).first()
    else:
        raise HTTPException(status_code=400, detail="owner_id 或 owner_line_uid 必填")
    if not owner:
        return {"error": "User not found"}

    resource = CommunityResource(
        owner_id=owner.id,
        resource_type=resource_type,
        name=name,
        quantity=quantity,
        address=address,
        lat=lat,
        lng=lng,
        note=note,
    )
    db.add(resource)
    db.commit()
    db.refresh(resource)
    return {"id": str(resource.id), "message": "物資登記成功"}


@router.put("/{resource_id}")
def update_resource(
    resource_id: str,
    name: str | None = None,
    quantity: str | None = None,
    address: str | None = None,
    note: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    is_available: bool | None = None,
    db: Session = Depends(get_db),
):
    r = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()
    if not r:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not found")
    if name         is not None: r.name         = name
    if quantity     is not None: r.quantity      = quantity
    if address      is not None: r.address       = address
    if note         is not None: r.note          = note
    if lat          is not None: r.lat           = lat
    if lng          is not None: r.lng           = lng
    if is_available is not None: r.is_available  = is_available
    r.last_updated = datetime.now()
    db.commit()
    return {"message": "更新成功"}


@router.delete("/{resource_id}")
def delete_resource(resource_id: str, db: Session = Depends(get_db)):
    r = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()
    if not r:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(r)
    db.commit()
    return {"message": "刪除成功"}


@router.patch("/{resource_id}/toggle")
def toggle_availability(resource_id: str, db: Session = Depends(get_db)):
    r = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()
    if not r:
        return {"error": "Not found"}
    r.is_available = not r.is_available
    r.last_updated = datetime.now()
    db.commit()
    return {"id": resource_id, "is_available": r.is_available}


# ──────────────────────────────────────────────
# 緊急需求（災時）
# ──────────────────────────────────────────────
@router.get("/needs")
def list_needs(status: str = "open", db: Session = Depends(get_db)):
    needs = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.status == status)
        .order_by(CommunityNeed.urgency, CommunityNeed.created_at)
        .all()
    )
    return [
        {
            "id":          str(n.id),
            "need_type":   n.need_type,
            "description": n.description,
            "quantity":    n.quantity,
            "address":     n.address,
            "lat":         n.lat,
            "lng":         n.lng,
            "urgency":     n.urgency,
            "status":      n.status,
            "created_at":  str(n.created_at),
        }
        for n in needs
    ]


@router.post("/needs")
def create_need(
    requester_line_uid: str,
    need_type: str,
    description: str | None = None,
    quantity: str | None = None,
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    urgency: int = 2,
    db: Session = Depends(get_db),
):
    requester = db.query(User).filter(User.line_uid == requester_line_uid).first()
    if not requester:
        return {"error": "User not found"}

    need = CommunityNeed(
        requester_id=requester.id,
        need_type=need_type,
        description=description,
        quantity=quantity,
        address=address,
        lat=lat,
        lng=lng,
        urgency=urgency,
    )
    db.add(need)
    db.commit()
    db.refresh(need)
    return {"id": str(need.id), "message": "需求提交成功"}


@router.post("/needs/{need_id}/match")
def match_need(need_id: str, resource_id: str, db: Session = Depends(get_db)):
    """手動媒合需求與物資，並立即 LINE 通知志工"""
    from app.services.dispatch import manual_dispatch
    return manual_dispatch(need_id, resource_id, db)


@router.post("/dispatch")
def run_dispatch():
    """立即執行一次自動媒合（管理員手動觸發）"""
    from app.services.dispatch import auto_dispatch
    result = auto_dispatch()
    return result
