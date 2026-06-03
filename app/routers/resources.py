from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session
from datetime import datetime
import json

from app.database import get_db
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.user import User
from app.models.resource_point import ResourcePoint, POINT_TYPES, POINT_SUPPLY_TYPES

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


@router.put("/needs/{need_id}")
def update_need_status(need_id: str, status: str, db: Session = Depends(get_db)):
    """更新需求狀態（例如 cancelled）"""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        raise HTTPException(status_code=404, detail="Not found")
    need.status = status
    db.commit()
    return {"message": "更新成功"}


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


@router.get("/needs/{need_id}/candidates")
def need_candidates(need_id: str, db: Session = Depends(get_db)):
    """預覽此需求的候選資源評分（不執行媒合）"""
    from app.services.dispatch import preview_candidates
    return preview_candidates(need_id, db)


# ──────────────────────────────────────────────
# 固定資源點 (ResourcePoint)
# ──────────────────────────────────────────────

@router.get("/points")
def list_resource_points(
    point_type: str | None = None,
    active_only: bool = True,
    db: Session = Depends(get_db),
):
    """列出社區固定資源點"""
    q = db.query(ResourcePoint)
    if active_only:
        q = q.filter(ResourcePoint.is_active == True)
    if point_type:
        q = q.filter(ResourcePoint.point_type == point_type)
    pts = q.order_by(ResourcePoint.point_type, ResourcePoint.name).all()
    return [_fmt_point(p) for p in pts]


@router.post("/points")
def create_resource_point(
    name: str,
    point_type: str,
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    capacity: int | None = None,
    phone: str | None = None,
    operating_hours: str | None = None,
    note: str | None = None,
    db: Session = Depends(get_db),
):
    """新增固定資源點"""
    if point_type not in POINT_TYPES:
        raise HTTPException(status_code=400,
                            detail=f"point_type 必須是 {list(POINT_TYPES.keys())} 之一")
    pt = ResourcePoint(
        name=name, point_type=point_type,
        address=address, lat=lat, lng=lng,
        capacity=capacity, phone=phone,
        operating_hours=operating_hours, note=note,
        source="manual",
    )
    db.add(pt)
    db.commit()
    db.refresh(pt)
    return {"id": str(pt.id), "message": "資源點新增成功"}


@router.put("/points/{point_id}")
def update_resource_point(
    point_id: str,
    name: str | None = None,
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    capacity: int | None = None,
    phone: str | None = None,
    operating_hours: str | None = None,
    note: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    pt = db.query(ResourcePoint).filter(ResourcePoint.id == point_id).first()
    if not pt:
        raise HTTPException(status_code=404, detail="Not found")
    if name             is not None: pt.name             = name
    if address          is not None: pt.address          = address
    if lat              is not None: pt.lat              = lat
    if lng              is not None: pt.lng              = lng
    if capacity         is not None: pt.capacity         = capacity
    if phone            is not None: pt.phone            = phone
    if operating_hours  is not None: pt.operating_hours  = operating_hours
    if note             is not None: pt.note             = note
    if is_active        is not None: pt.is_active        = is_active
    db.commit()
    return {"message": "更新成功"}


@router.delete("/points/{point_id}")
def delete_resource_point(point_id: str, db: Session = Depends(get_db)):
    pt = db.query(ResourcePoint).filter(ResourcePoint.id == point_id).first()
    if not pt:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(pt)
    db.commit()
    return {"message": "刪除成功"}


@router.post("/points/seed")
def seed_resource_points(city: str = "台中市", clear: bool = False):
    """從政府開放資料匯入固定資源點（管理員觸發）"""
    import threading
    result = {"status": "started"}
    def _run():
        try:
            from seed_resource_points import seed
            count = seed(city=city, clear=clear)
            print(f"[seed] 完成，匯入 {count} 筆")
        except Exception as e:
            print(f"[seed] 失敗：{e}")
    threading.Thread(target=_run, daemon=True).start()
    return {"message": f"正在匯入 {city} 資源點，請稍後刷新頁面查看結果"}


def _fmt_point(p: ResourcePoint) -> dict:
    supplies = {}
    if p.supplies_json:
        try:
            supplies = json.loads(p.supplies_json)
        except Exception:
            pass
    return {
        "id":              str(p.id),
        "name":            p.name,
        "point_type":      p.point_type,
        "point_type_label": POINT_TYPES.get(p.point_type, p.point_type),
        "address":         p.address,
        "lat":             p.lat,
        "lng":             p.lng,
        "capacity":        p.capacity,
        "current_load":    p.current_load,
        "supplies":        supplies,
        "supply_types":    POINT_SUPPLY_TYPES.get(p.point_type, []),
        "phone":           p.phone,
        "operating_hours": p.operating_hours,
        "source":          p.source,
        "is_active":       p.is_active,
        "note":            p.note,
    }
