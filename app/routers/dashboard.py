from datetime import date
from fastapi import APIRouter, Depends, Query, Request
from sqlalchemy.orm import Session
from sqlalchemy import func

from sqlalchemy.exc import IntegrityError

from app.errors import ApiError
from app.validation import check_coords, check_name, check_roles
from app.timeutil import now_utc, today_tw
from app.database import get_db
from app.models.user import User
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.config import SystemConfig
from app.rate_limit import limiter

router = APIRouter()


def _get_user_or_404(db, user_id: str, what: str = "使用者") -> User:
    try:
        user = db.query(User).filter(User.id == user_id).first()
    except Exception:  # 格式不是合法 UUID
        user = None
    if not user:
        raise ApiError(404, f"找不到{what}。")
    return user


@router.get("/summary")
def get_summary(db: Session = Depends(get_db)):
    """今日狀況總覽"""
    today = today_tw()

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


_TAIPEI_OFFSET_HOURS = 8  # 排程本身用 Asia/Taipei（見 app/scheduler.py），
                          # 但 created_at 一律是 server_default=func.now()
                          # 存的 UTC 時間；SQLite 跟 Postgres 兩種資料庫
                          # 的日期函式又不同語法，這裡改成撈原始時間戳，
                          # 統一在 Python 端 +8 小時換算成台灣日曆日再分桶，
                          # 避免午夜到早上 8 點這段時間的資料被歸到前一天。


def _taipei_date(dt) -> str:
    from datetime import timedelta
    return str((dt + timedelta(hours=_TAIPEI_OFFSET_HOURS)).date())


@router.get("/trends")
def get_trends(days: int = 14, db: Session = Depends(get_db)):
    """
    近 N 天的歷史趨勢：每日打卡狀態分布、警報量、需求量。
    給分析儀表板畫圖用，沒有資料的日子補 0，圖表才不會斷開。
    """
    from datetime import timedelta, datetime as dt_cls

    days = max(1, min(days, 90))
    cutoff = today_tw() - timedelta(days=days - 1)
    # 抓寬一天再用 Python 依台灣時區重新分桶，覆蓋掉 UTC/台灣日期
    # 交界處可能漏掉的資料。
    fetch_from = dt_cls.combine(cutoff - timedelta(days=1), dt_cls.min.time())

    checkin_rows = (
        db.query(DailyCheckin.date, DailyCheckin.status, func.count(DailyCheckin.id))
        .filter(DailyCheckin.date >= cutoff)
        .group_by(DailyCheckin.date, DailyCheckin.status)
        .all()
    )
    alert_times = db.query(Alert.created_at).filter(Alert.created_at >= fetch_from).all()
    need_times = db.query(CommunityNeed.created_at, CommunityNeed.status).filter(
        CommunityNeed.created_at >= fetch_from
    ).all()

    checkin_by_date: dict[str, dict[str, int]] = {}
    for d, status, count in checkin_rows:
        checkin_by_date.setdefault(str(d), {})[status] = count

    alert_by_date: dict[str, int] = {}
    for (created_at,) in alert_times:
        if created_at is None:
            continue
        d = _taipei_date(created_at)
        alert_by_date[d] = alert_by_date.get(d, 0) + 1

    need_by_date: dict[str, int] = {}
    need_fulfilled_by_date: dict[str, int] = {}
    for created_at, status in need_times:
        if created_at is None:
            continue
        d = _taipei_date(created_at)
        need_by_date[d] = need_by_date.get(d, 0) + 1
        if status == "fulfilled":
            need_fulfilled_by_date[d] = need_fulfilled_by_date.get(d, 0) + 1

    result = []
    for i in range(days):
        d = str(cutoff + timedelta(days=i))
        cs = checkin_by_date.get(d, {})
        result.append({
            "date": d,
            "checkin_ok": cs.get("ok", 0),
            "checkin_no_response": cs.get("no_response", 0),
            "checkin_help_needed": cs.get("help_needed", 0),
            "checkin_pending": cs.get("pending", 0),
            "alerts": alert_by_date.get(d, 0),
            "needs": need_by_date.get(d, 0),
            "needs_fulfilled": need_fulfilled_by_date.get(d, 0),
        })

    return {"days": result}


@router.get("/elderly")
def list_elderly(db: Session = Depends(get_db)):
    """所有長者及今日打卡狀態"""
    today = today_tw()
    elderly = (
        db.query(User)
        .filter(User.role_filter("elderly"), User.is_active == True)
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
@limiter.limit("5/minute")
def set_mode(request: Request, mode: str, db: Session = Depends(get_db)):
    """
    切換系統模式：normal / emergency，並廣播 LINE 通知。
    這個端點會廣播訊息給所有真實用戶，比其他端點多一層更嚴格的限流
    （5次/分鐘），避免被亂打時直接騷擾到真人。
    """
    if mode not in ("normal", "emergency"):
        raise ApiError(422, "mode 只能是 normal 或 emergency。")

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
                           "如需協助請傳「需要幫忙」；物資不夠請傳「需要水」「需要食物」等。\n"
                           "生命危險請直接撥打 119。\n"
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
    roles: list[str] = Query(...),
    line_uid: str | None = None,
    phone: str | None = None,
    address: str | None = None,
    lat: float | None = None,
    lng: float | None = None,
    db: Session = Depends(get_db),
):
    """新增使用者（長者 / 志工 / 家屬）"""
    name = check_name(name)
    roles = check_roles(roles)
    check_coords(lat, lng)
    line_uid = (line_uid or "").strip() or None
    if line_uid and db.query(User).filter(User.line_uid == line_uid).first():
        raise ApiError(409, "這個 LINE User ID 已經綁定給另一位使用者。")
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
    roles: list[str] | None = Query(default=None),
    db: Session = Depends(get_db),
):
    """
    更新使用者。roles 之前完全沒辦法透過這個端點修改——LINE bot 自動
    註冊的新用戶預設一律是 elderly，如果來的其實是志工/家屬/管理員
    本人在測試，之前沒有任何辦法（不管是 API 還是後台 UI）把角色改
    回來，只能留著錯的角色或整筆刪掉重建。
    """
    user = _get_user_or_404(db, user_id)
    if name is not None:
        name = check_name(name)
    if roles is not None:
        roles = check_roles(roles)
    if lat is not None or lng is not None:
        check_coords(lat if lat is not None else user.lat, lng if lng is not None else user.lng)
    if line_uid is not None:
        line_uid = line_uid.strip() or None
        if line_uid and db.query(User).filter(User.line_uid == line_uid, User.id != user.id).first():
            raise ApiError(409, "這個 LINE User ID 已經綁定給另一位使用者。")
    if name     is not None: user.name     = name
    if phone    is not None: user.phone    = phone
    if address  is not None: user.address  = address
    if line_uid is not None: user.line_uid = line_uid
    if lat      is not None: user.lat      = lat
    if lng      is not None: user.lng      = lng
    if is_active is not None: user.is_active = is_active
    roles_changed = roles is not None and roles != user.roles
    if roles    is not None: user.roles    = roles
    db.commit()
    if roles_changed or line_uid is not None:
        from app.services.rich_menu import sync_user_menu
        sync_user_menu(user)
    return {"message": "更新成功", "id": user_id}


@router.post("/users/{user_id}/resolve_alerts")
def resolve_user_alerts(user_id: str, db: Session = Depends(get_db)):
    """
    批次解決某使用者名下所有未解決的警報。

    情境：角色被誤判（例如 LINE 自動註冊預設是 elderly，但其實是志工
    在測試）修正後，之前系統排程每天累積下來的一堆「未回應」警報還
    是掛在那裡，逐筆用 confirm_safe 一天一天解要點一百次；這裡直接
    整批清掉。
    """
    from datetime import datetime
    count = (
        db.query(Alert)
        .filter(Alert.elderly_id == user_id, Alert.status == "sent")
        .update({"status": "resolved", "resolved_at": now_utc()})
    )
    db.commit()
    return {"message": f"已解決 {count} 筆警報", "resolved": count}


@router.post("/users/{user_id}/join_code")
def make_join_code(user_id: str, db: Session = Depends(get_db)):
    """產生綁定碼：對方在 LINE 傳「加入 碼」就會綁定，不用貼 LINE User ID。"""
    from app.services.line_ops import create_join_code
    user = _get_user_or_404(db, user_id)
    if user.line_uid:
        raise ApiError(409, "這位成員已經綁定 LINE，要更換請先解除綁定。")
    code, minutes = create_join_code(db, user)
    return {"code": code, "expires_minutes": minutes, "say": f"加入 {code}"}


@router.post("/users/{user_id}/unlink_line")
def unlink_line(user_id: str, db: Session = Depends(get_db)):
    """解除 LINE 綁定（換手機、綁錯人時用），選單一併收回。"""
    user = _get_user_or_404(db, user_id)
    old_uid = user.line_uid
    user.line_uid = None
    db.commit()
    if old_uid:
        try:
            from app.services.rich_menu import _apis
            _apis()[0].unlink_rich_menu_id_from_user(old_uid)
        except Exception:
            pass
    return {"message": "已解除綁定"}


@router.delete("/users/{user_id}")
def delete_user(user_id: str, db: Session = Depends(get_db)):
    """刪除使用者，連同只屬於他的資料一起清乾淨。

    之前直接 db.delete(user)：只要這個人有打卡、警報、需求、物資或照護關係，資料庫的外鍵
    就會擋下來，整個請求變成沒有任何說明的 500。現在先檢查有沒有進行中的派遣（有就
    拒絕並說明原因），沒有的話依序清掉相關紀錄；仍有稽核紀錄無法刪時回 409，建議改為停用。"""
    from app.services.user_deletion import UserDeletionBlocked, delete_user_data

    user = _get_user_or_404(db, user_id)
    try:
        delete_user_data(db, user)
    except UserDeletionBlocked as exc:
        raise ApiError(409, f"這位使用者{exc}")
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
        .filter(User.role_filter("elderly"), User.is_active == True,
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
    relation = (relation or "").strip()
    if not relation:
        raise ApiError(422, "關係說明不能是空的（例如 family、volunteer）。")
    if not 1 <= notify_order <= 10:
        raise ApiError(422, "通知順序必須是 1 到 10。")
    if str(elderly_id) == str(contact_id):
        raise ApiError(422, "不能把長者本人設成自己的照護聯絡人。")
    _get_user_or_404(db, elderly_id, what="長者")
    _get_user_or_404(db, contact_id, what="聯絡人")
    if db.query(CareRelation).filter(CareRelation.elderly_id == elderly_id,
                                     CareRelation.contact_id == contact_id).first():
        raise ApiError(409, "這組照護關係已經存在。")
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


# ──────────────────────────────────────────────
# 志工自助申請審核——見 app/services/volunteer_application.py
# ──────────────────────────────────────────────
@router.get("/volunteer-applications")
def list_volunteer_applications(status: str = "pending", db: Session = Depends(get_db)):
    from app.services.volunteer_application import list_applications
    return list_applications(db, status=status)


@router.post("/volunteer-applications/{application_id}/decision")
def decide_volunteer_application(
    application_id: str,
    decision: str,
    reviewer_id: str | None = None,
    note: str | None = None,
    db: Session = Depends(get_db),
):
    from app.services.volunteer_application import decide
    return decide(db, application_id, decision=decision, reviewer_id=reviewer_id, note=note)
