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
    """切換系統模式：normal / emergency，並廣播 LINE 通知"""
    if mode not in ("normal", "emergency"):
        return {"error": "mode must be 'normal' or 'emergency'"}

    cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    old_mode = cfg.value if cfg else "normal"

    if cfg:
        cfg.value = mode
    else:
        db.add(SystemConfig(key="mode", value=mode))
    db.commit()

    # 模式有變化才廣播
    if old_mode != mode:
        import threading
        def _broadcast():
            try:
                from app.services.line_notify import send_text
                all_users = db.query(User).filter(
                    User.line_uid != None,
                    User.is_active == True,
                ).all()
                if mode == "emergency":
                    msg = ("🚨 社區緊急模式啟動\n\n"
                           "請保持冷靜，確認自身安全。\n"
                           "如需協助請傳「需要幫忙」或按選單按鈕。\n"
                           "管理員將持續更新資訊。")
                else:
                    msg = ("✅ 緊急模式已解除\n\n"
                           "社區恢復日常模式。\n"
                           "感謝所有志工的協助！")
                for u in all_users:
                    try:
                        send_text(u.line_uid, msg)
                    except Exception:
                        pass
            except Exception:
                pass
        threading.Thread(target=_broadcast, daemon=True).start()

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


@router.post("/trigger_checkin")
def trigger_checkin(db: Session = Depends(get_db)):
    """立即發送打卡訊息給所有長者（Demo / 測試用）"""
    import threading
    def _run():
        from app.services.checkin import send_daily_checkins
        send_daily_checkins()
    threading.Thread(target=_run, daemon=True).start()
    elderly_count = (
        db.query(User)
        .filter(User.roles.contains(["elderly"]), User.is_active == True,
                User.line_uid != None)
        .count()
    )
    return {"message": f"已觸發打卡，共 {elderly_count} 位有綁定 LINE 的長者"}


@router.get("/relations")
def list_relations(elderly_id: str | None = None, db: Session = Depends(get_db)):
    """列出照護關係"""
    from app.models.care_relation import CareRelation
    q = db.query(CareRelation)
    if elderly_id:
        q = q.filter(CareRelation.elderly_id == elderly_id)
    rels = q.order_by(CareRelation.elderly_id, CareRelation.notify_order).all()
    return [
        {
            "id":           str(r.id),
            "elderly_id":   str(r.elderly_id),
            "elderly_name": r.elderly.name if r.elderly else "?",
            "contact_id":   str(r.contact_id),
            "contact_name": r.contact.name if r.contact else "?",
            "relation":     r.relation,
            "notify_order": r.notify_order,
        }
        for r in rels
    ]


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
    return {"message": "關係建立成功", "id": str(rel.id)}


@router.delete("/relations/{relation_id}")
def delete_relation(relation_id: str, db: Session = Depends(get_db)):
    """刪除照護關係"""
    from app.models.care_relation import CareRelation
    from fastapi import HTTPException
    rel = db.query(CareRelation).filter(CareRelation.id == relation_id).first()
    if not rel:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(rel)
    db.commit()
    return {"message": "關係刪除成功"}


@router.get("/alerts/history")
def alert_history(limit: int = 50, db: Session = Depends(get_db)):
    """全部警報（含已解決），給儀表板歷史記錄用"""
    alerts = (
        db.query(Alert)
        .order_by(Alert.created_at.desc())
        .limit(limit)
        .all()
    )
    return [
        {
            "id":         str(a.id),
            "elderly":    a.elderly.name if a.elderly else "unknown",
            "alert_type": a.alert_type,
            "status":     a.status,
            "created_at": str(a.created_at),
            "resolved_at": str(a.resolved_at) if a.resolved_at else None,
        }
        for a in alerts
    ]
