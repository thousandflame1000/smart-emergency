# -*- coding: utf-8 -*-
"""志工申請的審核狀態機：pending -> approved / rejected。

核准時才把 volunteer 角色寫進 User.roles（用附加的方式，不動使用者
既有角色，例如長者本人申請兼志工不會丟掉 elderly 角色）；兩種結果都
主動推播通知申請者，不讓他自己來問審到哪。
"""
from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from fastapi import HTTPException
from sqlalchemy.orm import Session

from app.models.user import User
from app.models.volunteer_application import VolunteerApplication


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def submit(
    db: Session,
    *,
    line_uid: str,
    name: str,
    phone: str | None,
    service_area: str | None,
) -> VolunteerApplication:
    applicant = db.query(User).filter(User.line_uid == line_uid).first()
    existing_pending = (
        db.query(VolunteerApplication)
        .filter(VolunteerApplication.line_uid == line_uid, VolunteerApplication.status == "pending")
        .first()
    )
    if existing_pending:
        return existing_pending
    application = VolunteerApplication(
        line_uid=line_uid,
        name=name,
        phone=phone,
        service_area=service_area,
        applicant_id=applicant.id if applicant else None,
        status="pending",
    )
    db.add(application)
    db.commit()
    db.refresh(application)
    return application


def list_applications(db: Session, *, status: str = "pending") -> list[dict[str, Any]]:
    rows = (
        db.query(VolunteerApplication)
        .filter(VolunteerApplication.status == status)
        .order_by(VolunteerApplication.created_at)
        .all()
    )
    return [serialize(row) for row in rows]


def serialize(row: VolunteerApplication) -> dict[str, Any]:
    return {
        "id": str(row.id),
        "line_uid": row.line_uid,
        "name": row.name,
        "phone": row.phone,
        "service_area": row.service_area,
        "status": row.status,
        "applicant_id": str(row.applicant_id) if row.applicant_id else None,
        "created_at": str(row.created_at),
        "reviewed_at": str(row.reviewed_at) if row.reviewed_at else None,
        "review_note": row.review_note,
    }


def decide(
    db: Session,
    application_id: str,
    *,
    decision: str,
    reviewer_id: str | None = None,
    note: str | None = None,
) -> dict[str, Any]:
    if decision not in ("approve", "reject"):
        raise HTTPException(status_code=422, detail="decision 必須是 approve 或 reject。")
    application = db.query(VolunteerApplication).filter(VolunteerApplication.id == application_id).first()
    if not application:
        raise HTTPException(status_code=404, detail="找不到這筆申請。")
    if application.status != "pending":
        raise HTTPException(status_code=409, detail=f"這筆申請已經是「{application.status}」，不能重複審核。")

    application.status = "approved" if decision == "approve" else "rejected"
    application.reviewed_by = reviewer_id
    application.review_note = note
    application.reviewed_at = _now()

    notified = False
    if decision == "approve":
        user = db.query(User).filter(User.line_uid == application.line_uid).first()
        if not user:
            user = User(name=application.name, roles=[], line_uid=application.line_uid,
                        phone=application.phone, is_active=True)
            db.add(user)
            db.flush()
        roles = list(user.roles or [])
        if "volunteer" not in roles:
            roles.append("volunteer")
        user.roles = roles
        # 申請表上填的是本人姓名；帳號裡原本是 LINE 顯示名稱（例如「小豬豬」）。
        # 核准後名單與物資名稱都要看得出是誰，不然管理員對不上人。
        if application.name:
            user.name = application.name
        if not user.phone and application.phone:
            user.phone = application.phone
        application.applicant_id = user.id
        db.commit()
        from app.services.rich_menu import sync_user_menu
        sync_user_menu(user)
        try:
            from app.services.line_notify import send_text
            send_text(application.line_uid,
                       f"✅ 志工申請已核准，歡迎加入！\n"
                       f"傳「登記物資」可以開始登記您能提供的物資，\n"
                       f"傳「我的物資」查看已登記項目。")
            notified = True
        except Exception:
            pass
    else:
        db.commit()
        try:
            from app.services.line_notify import send_text
            send_text(application.line_uid,
                       "很抱歉，您的志工申請這次未能通過審核。\n"
                       "如有疑問請直接聯繫社區管理員。")
            notified = True
        except Exception:
            pass

    return {**serialize(application), "volunteer_notified": notified}
