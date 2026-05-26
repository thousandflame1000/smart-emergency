from datetime import date
from fastapi import APIRouter, Depends
from sqlalchemy.orm import Session
from sqlalchemy import func

from app.database import get_db
from app.models.user import User
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.config import SystemConfig

router = APIRouter()


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """今日狀況總覽"""
    today = date.today()

    checkins = db.query(DailyCheckin).filter(DailyCheckin.date == today).all()
    status_counts = {}
    for c in checkins:
        status_counts[c.status] = status_counts.get(c.status, 0) + 1

    active_alerts = db.query(Alert).filter(Alert.status == "sent").count()
    open_needs    = db.query(CommunityNeed).filter(CommunityNeed.status == "open").count()
    avail_res     = db.query(CommunityResource).filter(
        CommunityResource.is_available == True
    ).count()

    mode_cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    mode     = mode_cfg.value if mode_cfg else "normal"

    return {
        "date": str(today),
        "mode": mode,
        "checkin_summary": status_counts,
        "active_alerts": active_alerts,
        "open_needs": open_needs,
        "available_resources": avail_res,
    }


@router.get("/elderly")
def list_elderly(db: Session = Depends(get_db)):
    """所有長者及今日打卡狀態"""
    today = date.today()
    elderly = (
        db.query(User)
        .filter(User.roles.contains(["elderly"]), User.is_active == True)
        .all()
    )

    result = []
    for e in elderly:
        checkin = (
            db.query(DailyCheckin)
            .filter(DailyCheckin.elderly_id == e.id, DailyCheckin.date == today)
            .first()
        )
        result.append({
            "id":      str(e.id),
            "name":    e.name,
            "phone":   e.phone,
            "address": e.address,
            "lat":     e.lat,
            "lng":     e.lng,
            "today_status": checkin.status if checkin else "not_sent",
            "responded_at": str(checkin.responded_at) if checkin and checkin.responded_at else None,
        })

    return result


@router.get("/alerts")
def list_alerts(db: Session = Depends(get_db)):
    """未解決的警報"""
    alerts = (
        db.query(Alert)
        .filter(Alert.status == "sent")
        .order_by(Alert.created_at.desc())
        .limit(50)
        .all()
    )
    return [
        {
            "id":         str(a.id),
            "elderly":    a.elderly.name if a.elderly else "unknown",
            "alert_type": a.alert_type,
            "created_at": str(a.created_at),
        }
        for a in alerts
    ]


@router.post("/mode")
def set_mode(mode: str, db: Session = Depends(get_db)):
    """切換系統模式：normal / emergency"""
    if mode not in ("normal", "emergency"):
        return {"error": "mode must be 'normal' or 'emergency'"}

    cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    if cfg:
        cfg.value = mode
    else:
        db.add(SystemConfig(key="mode", value=mode))
    db.commit()
    return {"mode": mode, "message": f"已切換為{'緊急' if mode == 'emergency' else '日常'}模式"}


@router.post("/users")
def create_user(
    name: str,
    roles: list[str],
    line_uid: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    db: Session = Depends(get_db),
):
    """新增使用者（長者 / 志工 / 家屬）"""
    user = User(
        name=name,
        roles=roles,
        line_uid=line_uid,
        phone=phone,
        address=address,
        lat=lat,
        lng=lng,
    )
    db.add(user)
    db.commit()
    db.refresh(user)
    return {"id": str(user.id), "name": user.name, "roles": user.roles}


@router.get("/users")
def list_users(db: Session = Depends(get_db)):
    """所有使用者"""
    users = db.query(User).order_by(User.created_at.desc()).all()
    return [
        {
            "id":       str(u.id),
            "name":     u.name,
            "roles":    u.roles,
            "phone":    u.phone,
            "address":  u.address,
            "line_uid": u.line_uid,
            "lat":      u.lat,
            "lng":      u.lng,
            "is_active": u.is_active,
        }
        for u in users
    ]


@router.put("/users/{user_id}")
def update_user(
    user_id: str,
    name: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    line_uid: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    if name     is not None: user.name     = name
    if phone    is not None: user.phone    = phone
    if address  is not None: user.address  = address
    if line_uid is not None: user.line_uid = line_uid
    if lat      is not None: user.lat      = lat
    if lng      is not None: user.lng      = lng
    if is_active is not None: user.is_active = is_active
    db.commit()
    return {"message": "更新成功", "id": user_id}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, db: Session = Depends(get_db)):
    user = db.query(User).filter(User.id == user_id).first()
    if not user:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="User not found")
    db.delete(user)
    db.commit()
    return {"message": "刪除成功"}


@router.post("/relations")
def create_relation(
    elderly_id: str,
    contact_id: str,
    relation: str,
    notify_order: int = 1,
    db: Session = Depends(get_db),
):
    """建立長者 ↔ 家屬/志工 關係"""
    from app.models.care_relation import CareRelation
    rel = CareRelation(
        elderly_id=elderly_id,
        contact_id=contact_id,
        relation=relation,
        notify_order=notify_order,
    )
    db.add(rel)
    db.commit()
    return {"message": "關係建立成功"}
