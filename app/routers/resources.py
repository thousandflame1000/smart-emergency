from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.exc import IntegrityError
from sqlalchemy.orm import Session
from datetime import datetime
import json

from app.errors import ApiError, raise_if_error
from app.validation import (
    NEED_TYPES, RESOURCE_TYPES, check_choice, check_coords, check_name, check_urgency,
)
from app.timeutil import now_utc, today_tw
from app.database import get_db
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.user import User
from app.models.resource_point import ResourcePoint, POINT_TYPES, POINT_SUPPLY_TYPES
from app.models.dispatch_event import DispatchEvent
from app.models.inventory import InventoryEvent
from app.models.zone import Zone
from app.security import require_admin, require_staff
from app.services.inventory import available_amount, quantity_parts, set_quantity_fields
from app.services.record_version import row_predicates


def _check_zone(zone_id: str, db: Session) -> None:
    if not db.get(Zone, zone_id):
        raise ApiError(400, "找不到這個分區")

router = APIRouter()


def _unreserved(resource, db):
    if db.query(CommunityNeed.id).filter(CommunityNeed.matched_resource_id == resource.id,
                                         CommunityNeed.status.in_(("suggested", "matched"))).first():
        raise ApiError(409, "物資已被待核准或執行中的任務保留，請先處理任務")


@router.get("/{resource_id}/inventory-events")
def list_inventory_events(resource_id: str, limit: int = 100, db: Session = Depends(get_db)):
    if not db.query(CommunityResource.id).filter(CommunityResource.id == resource_id).first():
        raise HTTPException(status_code=404, detail="Resource not found")
    rows = (
        db.query(InventoryEvent)
        .filter(InventoryEvent.resource_id == resource_id)
        .order_by(InventoryEvent.resource_version.desc(), InventoryEvent.created_at.desc())
        .limit(max(1, min(limit, 500)))
        .all()
    )
    return [
        {
            "id": str(event.id),
            "resource_id": str(event.resource_id),
            "need_id": str(event.need_id) if event.need_id else None,
            "event_type": event.event_type,
            "quantity": event.quantity,
            "unit": event.unit,
            "on_hand_before": event.on_hand_before,
            "on_hand_after": event.on_hand_after,
            "reserved_before": event.reserved_before,
            "reserved_after": event.reserved_after,
            "resource_version": event.resource_version,
            "actor_id": str(event.actor_id) if event.actor_id else None,
            "actor_label": event.actor_label,
            "created_at": str(event.created_at),
        }
        for event in rows
    ]


# ──────────────────────────────────────────────
# 物資
# ──────────────────────────────────────────────
@router.get("/")
def list_resources(
    resource_type: str | None = None,
    available_only: bool = True,
    zone_id: str | None = None,
    db: Session = Depends(get_db),
):
    q = db.query(CommunityResource)
    if available_only:
        q = q.filter(CommunityResource.is_available == True)
    if resource_type:
        q = q.filter(CommunityResource.resource_type == resource_type)
    if zone_id:
        q = q.filter(CommunityResource.zone_id == zone_id)

    resources = q.order_by(CommunityResource.created_at.desc()).all()
    return [
        {
            "id":            str(r.id),
            "owner":         r.owner.name if r.owner else "unknown",
            "resource_type": r.resource_type,
            "name":          r.name,
            "quantity":      r.quantity,
            "quantity_amount": r.quantity_amount,
            "quantity_unit": r.quantity_unit,
            "reserved_amount": r.reserved_amount,
            "available_amount": available_amount(r),
            "inventory_version": r.inventory_version,
            "address":       r.address,
            "lat":           r.lat,
            "lng":           r.lng,
            "note":          r.note,
            "is_available":  r.is_available,
            "zone_id":       r.zone_id,
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
    zone_id: str | None = None,
    db: Session = Depends(get_db),
    _principal: dict | None = Depends(require_staff),
):
    name = check_name(name, what="物資名稱")
    check_choice(resource_type, RESOURCE_TYPES, what="物資類型")
    check_coords(lat, lng)
    if zone_id is None:
        from app.services.zones import resolve_zone_for_point
        zone_id = resolve_zone_for_point(db, lat, lng)
    else:
        _check_zone(zone_id, db)
    if owner_id:
        try:
            owner = db.query(User).filter(User.id == owner_id).first()
        except Exception:
            owner = None
    elif owner_line_uid:
        owner = db.query(User).filter(User.line_uid == owner_line_uid).first()
    else:
        raise ApiError(400, "owner_id 或 owner_line_uid 必填")
    if not owner:
        raise ApiError(404, "找不到這位物資擁有者。")

    resource = CommunityResource(
        owner_id=owner.id,
        resource_type=resource_type,
        name=name,
        address=address,
        lat=lat,
        lng=lng,
        note=note,
        zone_id=zone_id,
    )
    set_quantity_fields(resource, quantity)
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
    _principal: dict | None = Depends(require_staff),
):
    r = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()
    if not r:
        raise ApiError(404, "找不到這筆物資。")
    _unreserved(r, db)
    if lat is not None or lng is not None:
        check_coords(lat if lat is not None else r.lat, lng if lng is not None else r.lng)
    if name is not None:
        name = check_name(name, what="物資名稱")
    values = {key: value for key, value in {"name": name, "quantity": quantity, "address": address,
              "note": note, "lat": lat, "lng": lng, "is_available": is_available}.items() if value is not None}
    if quantity is not None:
        parsed = quantity_parts(quantity)
        values.update(
            quantity_amount=parsed[0] if parsed else None,
            quantity_unit=parsed[1] if parsed else None,
            inventory_version=int(r.inventory_version or 1) + 1,
        )
    changed = db.query(CommunityResource).filter(*row_predicates(r)).update(
        {**values, "last_updated": now_utc()}, synchronize_session=False)
    if changed != 1:
        db.rollback()
        raise ApiError(409, "物資已由另一個操作修改，請重新載入")
    db.commit()
    return {"message": "更新成功"}


@router.delete("/{resource_id}")
def delete_resource(resource_id: str, db: Session = Depends(get_db), _principal: dict | None = Depends(require_admin)):
    r = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()
    if not r:
        from fastapi import HTTPException
        raise HTTPException(status_code=404, detail="Not found")
    _unreserved(r, db)
    changed = db.query(CommunityResource).filter(*row_predicates(r)).delete(synchronize_session=False)
    if changed != 1:
        db.rollback()
        raise ApiError(409, "物資已由另一個操作修改，請重新載入")
    db.commit()
    return {"message": "刪除成功"}


@router.patch("/{resource_id}/toggle")
def toggle_availability(resource_id: str, db: Session = Depends(get_db), _principal: dict | None = Depends(require_staff)):
    r = db.query(CommunityResource).filter(CommunityResource.id == resource_id).first()
    if not r:
        raise ApiError(404, "找不到這筆物資。")
    _unreserved(r, db)
    available = not r.is_available
    changed = db.query(CommunityResource).filter(*row_predicates(r)).update(
        {"is_available": available, "last_updated": now_utc()}, synchronize_session=False)
    if changed != 1:
        db.rollback()
        raise ApiError(409, "物資已由另一個操作修改，請重新載入")
    db.commit()
    return {"id": resource_id, "is_available": available}


# ──────────────────────────────────────────────
# 緊急需求（災時）
# ──────────────────────────────────────────────
@router.get("/needs")
def list_needs(status: str = "open", zone_id: str | None = None, db: Session = Depends(get_db)):
    q = db.query(CommunityNeed).filter(CommunityNeed.status == status)
    if zone_id:
        q = q.filter(CommunityNeed.zone_id == zone_id)
    needs = q.order_by(CommunityNeed.urgency, CommunityNeed.created_at).all()
    accepted = set()
    if needs and status == "matched":
        from app.services.dispatch import accepted_need_ids
        accepted = accepted_need_ids(db, [n.id for n in needs])
    reports = {}
    if needs:
        rows = (
            db.query(DispatchEvent)
            .filter(DispatchEvent.action == "task_report", DispatchEvent.need_id.in_([n.id for n in needs]))
            .order_by(DispatchEvent.created_at)
            .all()
        )
        for e in rows:
            reports[str(e.need_id)] = e
    return [
        {
            "id":          str(n.id),
            "need_type":   n.need_type,
            "description": n.description,
            "quantity":    n.quantity,
            "quantity_amount": n.quantity_amount,
            "quantity_unit": n.quantity_unit,
            "reserved_quantity_amount": n.reserved_quantity_amount,
            "fulfilled_quantity_amount": n.fulfilled_quantity_amount,
            "address":     n.address,
            "lat":         n.lat,
            "lng":         n.lng,
            "urgency":     n.urgency,
            "status":      n.status,
            "zone_id":     n.zone_id,
            "created_at":  str(n.created_at),
            "last_report": _fmt_report(reports.get(str(n.id))),
            "accepted":    str(n.id) in accepted,
        }
        for n in needs
    ]


def _fmt_report(event):
    if event is None:
        return None
    try:
        details = json.loads(event.details_json or "{}")
    except ValueError:
        details = {}
    return {"outcome": event.outcome, "note": details.get("note"),
            "actor": event.actor_label, "created_at": str(event.created_at)}


PROXY_REQUESTER_NAME = "管理員代建（未指定登記人）"


def _proxy_requester(db: Session) -> User:
    proxy = db.query(User).filter(User.name == PROXY_REQUESTER_NAME, User.is_active == False).first()
    if not proxy:
        proxy = User(name=PROXY_REQUESTER_NAME, roles=[], is_active=False)
        db.add(proxy)
        db.commit()
        db.refresh(proxy)
    return proxy


@router.post("/needs")
def create_need(
    need_type: str,
    requester_line_uid: str | None = None,
    requester_id: str | None = None,
    description: str | None = None,
    quantity: str | None = None,
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    urgency: int = 2,
    zone_id: str | None = None,
    db: Session = Depends(get_db),
    _principal: dict | None = Depends(require_admin),
):
    check_choice(need_type, NEED_TYPES, what="需求類型")
    check_urgency(urgency)
    check_coords(lat, lng)
    if zone_id is None:
        from app.services.zones import resolve_zone_for_point
        zone_id = resolve_zone_for_point(db, lat, lng)
    else:
        _check_zone(zone_id, db)
    if requester_id:
        try:
            requester = db.query(User).filter(User.id == requester_id).first()
        except Exception:
            requester = None
    elif requester_line_uid:
        requester = db.query(User).filter(User.line_uid == requester_line_uid).first()
    else:
        # 管理員沒指定登記人：之前是隨便抓「第一個啟用中的使用者」當登記人，
        # 這筆需求就會冒名記在某位真實長者頭上（派遣通知、脆弱度評分、「我的需求」
        # 全都算到他身上）。改成專用的代建帳號，不屬於任何真人。
        requester = _proxy_requester(db)
    if not requester:
        raise ApiError(404, "找不到這位登記人。")

    need = CommunityNeed(
        requester_id=requester.id,
        need_type=need_type,
        description=description,
        address=address,
        lat=lat,
        lng=lng,
        urgency=urgency,
        zone_id=zone_id,
    )
    set_quantity_fields(need, quantity)
    db.add(need)
    db.commit()
    db.refresh(need)
    return {"id": str(need.id), "message": "需求提交成功"}


@router.put("/needs/{need_id}")
def update_need_status(need_id: str, status: str, db: Session = Depends(get_db),
                       _principal: dict | None = Depends(require_admin)):
    """更新需求狀態（例如 cancelled）"""
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        raise ApiError(404, "找不到這筆需求。")
    if status != "cancelled":
        # 之前可以任意改成 matched / fulfilled 之類，需求會在沒有任何指派、
        # 沒有稽核紀錄的情況下直接跳過整個派遣流程。
        raise ApiError(422, "只能手動把需求改成 cancelled；其他狀態要走媒合、確認派遣、任務回報的流程。")
    from app.services.dispatch import cancel_need
    return raise_if_error(cancel_need(need_id, db))


@router.delete("/needs/{need_id}")
def delete_need(need_id: str, db: Session = Depends(get_db), _principal: dict | None = Depends(require_admin)):
    """永久刪除一筆需求，只允許還沒真的進入現場流程的狀態。

    "取消"（PUT status=cancelled）之前是唯一的收尾動作，垃圾測試資料
    （例如缺座標、地址打"1"這種永遠配不到的紀錄）會一直留在清單裡
    洗版，沒有真正移除的辦法。suggested/matched/fulfilled 已經牽涉
    真實的派遣決策與稽核紀錄，刻意不讓刪，只能走取消保留歷史。
    """
    need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    if not need:
        raise HTTPException(status_code=404, detail="Not found")
    if need.status not in ("open", "cancelled"):
        raise HTTPException(
            status_code=409,
            detail=f"狀態為「{need.status}」的需求已進入派遣流程，不能刪除，只能取消以保留紀錄。",
        )
    try:
        db.delete(need)
        db.commit()
    except IntegrityError:
        # Task Workflow V2（TASK_WORKFLOW_V2）啟用時，即便需求本身回到
        # open/cancelled，底下可能還留著 append-only 的 Task/Proposal
        # 稽核紀錄（ondelete=RESTRICT，刻意不讓連帶砍掉）——今天才真的
        # 撞過一次「刪使用者連帶被資料庫擋下但程式沒接住、變成 500」，
        # 這裡明確接住轉成乾淨的錯誤，不要再重演一次。
        db.rollback()
        raise HTTPException(status_code=409, detail="這筆需求仍有派遣稽核紀錄關聯，無法刪除，只能取消。")
    return {"message": "已永久刪除"}


@router.get("/needs/{need_id}/events")
def list_need_dispatch_events(
    need_id: str,
    limit: int = 20,
    db: Session = Depends(get_db),
):
    """列出單筆需求的派遣決策稽核紀錄。"""
    limit = max(1, min(limit, 100))
    events = (
        db.query(DispatchEvent)
        .filter(DispatchEvent.need_id == need_id)
        .order_by(DispatchEvent.created_at.desc())
        .limit(limit)
        .all()
    )
    return [_fmt_dispatch_event(e) for e in events]


@router.post("/needs/{need_id}/resolve_sos")
def resolve_sos_need(need_id: str, db: Session = Depends(get_db), _principal: dict | None = Depends(require_admin)):
    """管理員確認已聯繫、處理完一筆一鍵求助"""
    from app.services.dispatch import resolve_sos
    return raise_if_error(resolve_sos(need_id, db))


@router.post("/needs/{need_id}/match")
def match_need(need_id: str, resource_id: str, db: Session = Depends(get_db),
              _principal: dict | None = Depends(require_admin)):
    """手動媒合需求與物資，並立即 LINE 通知志工"""
    from app.services.dispatch import manual_dispatch
    return raise_if_error(manual_dispatch(need_id, resource_id, db))


@router.post("/dispatch")
def run_dispatch(_principal: dict | None = Depends(require_admin)):
    """
    立即執行一次自動媒合（管理員手動觸發）。
    只會產生「建議」（need.status = suggested），不會自動通知志工——
    管理員需個別呼叫 /needs/{id}/confirm_dispatch 才會真正發送 LINE 通知。
    """
    from app.services.dispatch import auto_dispatch
    result = auto_dispatch()
    return result


@router.post("/needs/{need_id}/confirm_dispatch")
def confirm_dispatch(need_id: str, expected_version: str | None = None, db: Session = Depends(get_db),
                     _principal: dict | None = Depends(require_admin)):
    """管理員確認自動媒合建議，此時才真正 LINE 通知志工"""
    from app.services.dispatch import confirm_dispatch as _confirm_dispatch
    return raise_if_error(_confirm_dispatch(need_id, db, expected_version=expected_version))


@router.post("/needs/{need_id}/decline_suggestion")
def decline_suggestion(need_id: str, expected_version: str | None = None, db: Session = Depends(get_db),
                       _principal: dict | None = Depends(require_admin)):
    """管理員否決自動媒合建議，物資恢復可用、需求退回待媒合"""
    from app.services.dispatch import decline_suggestion as _decline_suggestion
    return raise_if_error(_decline_suggestion(need_id, db, expected_version=expected_version))


@router.get("/needs/{need_id}/candidates")
def need_candidates(need_id: str, db: Session = Depends(get_db)):
    """預覽此需求的候選資源評分（不執行媒合）"""
    from app.services.dispatch import preview_candidates
    return preview_candidates(need_id, db)


def _fmt_dispatch_event(e: DispatchEvent) -> dict:
    details = {}
    if e.details_json:
        try:
            details = json.loads(e.details_json)
        except Exception:
            details = {"raw": e.details_json}
    return {
        "id":              str(e.id),
        "action":          e.action,
        "outcome":         e.outcome,
        "need_id":         str(e.need_id) if e.need_id else None,
        "resource_id":     str(e.resource_id) if e.resource_id else None,
        "resource_name":   e.resource.name if e.resource else None,
        "actor_id":        str(e.actor_id) if e.actor_id else None,
        "actor_label":     e.actor_label,
        "previous_status": e.previous_status,
        "new_status":      e.new_status,
        "details":         details,
        "created_at":      str(e.created_at),
    }


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
    _principal: dict | None = Depends(require_staff),
):
    """新增固定資源點"""
    name = check_name(name, what="資源點名稱")
    check_coords(lat, lng)
    if capacity is not None and capacity < 0:
        raise ApiError(422, "容量不能是負數。")
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
    current_load: int | None = None,
    phone: str | None = None,
    operating_hours: str | None = None,
    note: str | None = None,
    is_active: bool | None = None,
    db: Session = Depends(get_db),
    _principal: dict | None = Depends(require_staff),
):
    pt = db.query(ResourcePoint).filter(ResourcePoint.id == point_id).first()
    if not pt:
        raise HTTPException(status_code=404, detail="Not found")
    if name             is not None: pt.name             = name
    if address          is not None: pt.address          = address
    if lat              is not None: pt.lat              = lat
    if lng              is not None: pt.lng              = lng
    if capacity         is not None: pt.capacity         = capacity
    if current_load     is not None: pt.current_load     = max(0, current_load)
    if phone            is not None: pt.phone            = phone
    if operating_hours  is not None: pt.operating_hours  = operating_hours
    if note             is not None: pt.note             = note
    if is_active        is not None: pt.is_active        = is_active
    db.commit()
    return {"message": "更新成功"}


@router.post("/points/{point_id}/checkin")
def checkin_to_point(point_id: str, count: int = 1, db: Session = Depends(get_db),
                     _principal: dict | None = Depends(require_staff)):
    """
    回報有人（例：災民、住戶）抵達此資源點（避難所/收容點），
    current_load 隨即 +count；管理員在儀表板一鍵操作，不需要手動計算人數。
    """
    pt = db.query(ResourcePoint).filter(ResourcePoint.id == point_id).first()
    if not pt:
        raise HTTPException(status_code=404, detail="Not found")
    pt.current_load = max(0, pt.current_load + count)
    db.commit()
    return {"id": point_id, "current_load": pt.current_load, "capacity": pt.capacity}


@router.post("/points/{point_id}/checkout")
def checkout_from_point(point_id: str, count: int = 1, db: Session = Depends(get_db),
                        _principal: dict | None = Depends(require_staff)):
    """回報有人離開此資源點，current_load 隨即 -count（不會低於 0）"""
    pt = db.query(ResourcePoint).filter(ResourcePoint.id == point_id).first()
    if not pt:
        raise HTTPException(status_code=404, detail="Not found")
    pt.current_load = max(0, pt.current_load - count)
    db.commit()
    return {"id": point_id, "current_load": pt.current_load, "capacity": pt.capacity}


@router.delete("/points/{point_id}")
def delete_resource_point(point_id: str, db: Session = Depends(get_db),
                          _principal: dict | None = Depends(require_admin)):
    pt = db.query(ResourcePoint).filter(ResourcePoint.id == point_id).first()
    if not pt:
        raise HTTPException(status_code=404, detail="Not found")
    db.delete(pt)
    db.commit()
    return {"message": "刪除成功"}


@router.post("/points/seed")
def seed_resource_points(city: str = "花蓮縣", clear: bool = False,
                         _principal: dict | None = Depends(require_admin)):
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
