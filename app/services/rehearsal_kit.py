# -*- coding: utf-8 -*-
"""單帳號演練：一支 LINE 帳號扮演家屬或志工，另一方由不綁 LINE 的演練替身擔任。

只有一支手機時，家屬通知與志工派遣都需要「另一個人」才能走完。替身沒有 LINE，
所以所有真正的 LINE 訊息只會送到測試者本人；替身名字都帶【演練】，清除時一次移除。
"""
from __future__ import annotations

import logging

from sqlalchemy.orm import Session

from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.user import User
from app.timeutil import now_utc, today_tw

log = logging.getLogger(__name__)

MARK = "【演練】"
ELDER_NAME = MARK + "王阿嬤"
RESIDENT_NAME = MARK + "林先生"
ACTIVE = ("open", "suggested", "matched")


def testers(db: Session) -> list[dict]:
    """綁了 LINE 的真人帳號，連同他能不能當派遣測試的志工。"""
    rows = (db.query(User).filter(User.line_uid.isnot(None), User.is_active == True)  # noqa: E712
            .order_by(User.name).all())
    out = []
    for u in rows:
        if u.name.startswith(MARK):
            continue
        supply = _tester_supply(db, u)
        out.append({"id": str(u.id), "name": u.name, "roles": list(u.roles or []),
                    "supply": f"{supply.name}（{supply.quantity or '數量未填'}）" if supply else None})
    return out


def _tester(db: Session, tester_id: str) -> User:
    try:
        user = db.query(User).filter(User.id == tester_id).first()
    except Exception:
        user = None
    if not user or not user.line_uid:
        raise ValueError("請選擇已綁定 LINE 的測試帳號。")
    return user


def _tester_supply(db: Session, user: User) -> CommunityResource | None:
    return (db.query(CommunityResource)
            .filter(CommunityResource.owner_id == user.id, CommunityResource.is_available == True)  # noqa: E712
            .order_by(CommunityResource.last_updated.desc()).first())


def _stand_in(db: Session, name: str, role: str, near) -> User:
    user = db.query(User).filter(User.name == name, User.line_uid.is_(None)).first()
    if user is None:
        user = User(name=name, roles=[role], phone="0900000000")
        db.add(user)
    user.is_active = True
    if near is not None and near.lat is not None and near.lng is not None:
        user.lat, user.lng = near.lat, near.lng
        user.address = near.address or user.address
    user.address = user.address or "花蓮縣光復鄉（演練地址）"
    db.flush()
    return user


def family_alert(db: Session, tester_id: str, status: str) -> dict:
    """測試者成為演練長者的家屬，再讓長者回報「不舒服」或按「需要幫忙」。"""
    if status not in ("unwell", "help_needed"):
        raise ValueError("狀態只能是 unwell 或 help_needed。")
    tester = _tester(db, tester_id)
    elder = _stand_in(db, ELDER_NAME, "elderly", tester)
    relation = db.query(CareRelation).filter(
        CareRelation.elderly_id == elder.id, CareRelation.contact_id == tester.id).first()
    if relation is None:
        db.add(CareRelation(elderly_id=elder.id, contact_id=tester.id, relation="family", notify_order=1))
    else:
        relation.is_active = True
    if "family" not in (tester.roles or []):
        tester.roles = list(tester.roles or []) + ["family"]
    checkin = db.query(DailyCheckin).filter(
        DailyCheckin.elderly_id == elder.id, DailyCheckin.date == today_tw()).first()
    if checkin is None:
        checkin = DailyCheckin(elderly_id=elder.id, date=today_tw(), status="pending")
        db.add(checkin)
        db.flush()
    # 同一天同類警報只發一次；演練要能重按，所以先清掉替身今天的同類警報。
    db.query(Alert).filter(Alert.checkin_id == checkin.id, Alert.alert_type == status).delete(
        synchronize_session=False)
    db.commit()

    if status == "help_needed":
        from app.routers.linebot import _trigger_sos
        result = _trigger_sos(elder, db)
        notified = result["contacts"]
    else:
        from app.services.alert import send_alerts_for_checkin
        checkin.status = "unwell"
        checkin.responded_at = now_utc()
        db.commit()
        notified = send_alerts_for_checkin(checkin.id, "unwell", db)
    what = "求救" if status == "help_needed" else "不舒服"
    return {"elder": elder.name, "notified": notified, "message": f"長者{what}，已通知 {notified} 位家屬"}


def volunteer_task(db: Session, tester_id: str) -> dict:
    """演練居民在測試者物資旁提出同品項需求，並直接建立派給測試者的待核准建議。"""
    from app.services import dispatch
    tester = _tester(db, tester_id)
    if not any(r in (tester.roles or []) for r in ("volunteer", "admin")):
        raise ValueError(f"{tester.name} 還不是志工")
    supply = _tester_supply(db, tester)
    if supply is None:
        raise ValueError("先用 LINE 登記物資")
    resident = _stand_in(db, RESIDENT_NAME, "elderly", supply if supply.lat is not None else tester)
    for old in db.query(CommunityNeed).filter(CommunityNeed.requester_id == resident.id,
                                              CommunityNeed.status.in_(ACTIVE)).all():
        dispatch.cancel_need(str(old.id), db)
    need = CommunityNeed(
        requester_id=resident.id, need_type=supply.resource_type, description=MARK + "單帳號派遣測試",
        address=resident.address, lat=resident.lat, lng=resident.lng, urgency=3, zone_id=supply.zone_id,
    )
    db.add(need)
    db.commit()
    result = dispatch.propose_manual(str(need.id), str(supply.id), db, actor_label="rehearsal")
    if result.get("error"):
        raise ValueError(result["error"])
    from app.routers.linebot import page_admins_about_need
    page_admins_about_need(db, resident.name, need, {
        "volunteer": tester.name, "resource_name": supply.name, "dist_km": 0.0, "line_bound": True})
    return {"need_id": str(need.id), "resource": supply.name,
            "message": f"已建議派給 {tester.name}，到 LINE 按核准"}


def cleanup(db: Session) -> dict:
    """取消替身的進行中需求並移除替身；有稽核紀錄刪不掉的改為停用。"""
    from app.services import dispatch
    from app.services.user_deletion import UserDeletionBlocked, delete_user_data
    removed = disabled = 0
    for user in db.query(User).filter(User.name.like(MARK + "%"), User.line_uid.is_(None)).all():
        for need in db.query(CommunityNeed).filter(CommunityNeed.requester_id == user.id,
                                                   CommunityNeed.status.in_(ACTIVE)).all():
            dispatch.cancel_need(str(need.id), db)
        try:
            delete_user_data(db, user)
            removed += 1
        except UserDeletionBlocked:
            db.rollback()
            user.is_active = False
            db.query(CareRelation).filter(CareRelation.elderly_id == user.id).delete(synchronize_session=False)
            db.commit()
            disabled += 1
    return {"removed": removed, "disabled": disabled,
            "message": f"已清除 {removed + disabled} 位替身"}
