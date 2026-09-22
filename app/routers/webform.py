# -*- coding: utf-8 -*-
"""LINE 內建瀏覽器開的網頁表單（有真正的文字輸入框）。

機器人在對話裡發出每位使用者專屬、限時、簽章過的連結（見 services/form_token.py），
表單送出時驗章確認是誰，然後走和聊天指令完全相同的建立邏輯。"""
import logging
import os
import re

from fastapi import APIRouter, Depends
from fastapi.responses import FileResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.database import get_db
from app.errors import ApiError
from app.models.user import User
from app.services.form_token import verify_token
from app.validation import RESOURCE_TYPES, check_name

logger = logging.getLogger(__name__)
router = APIRouter()

FORM_PAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "form.html")
KINDS = {"need", "res", "apply", "report", "me"}
NEED_CHOICES = {"water", "food", "first_aid", "shelter", "vehicle"}
PHONE_RE = re.compile(r"^[0-9+\-()\s]{7,20}$")


class _Base(BaseModel):
    t: str = Field(min_length=10, max_length=600)
    name: str | None = Field(default=None, max_length=100)
    phone: str | None = Field(default=None, max_length=40)
    address: str | None = Field(default=None, max_length=200)


class NeedForm(_Base):
    types: list[str] = Field(default_factory=list, max_length=10)
    people: int | None = Field(default=None, ge=1, le=99)
    urgent: bool = False
    note: str | None = Field(default=None, max_length=500)


class ResourceForm(_Base):
    rtype: str
    quantity: str = Field(min_length=1, max_length=60)
    resource_name: str | None = Field(default=None, max_length=100)


class ReportForm(BaseModel):
    t: str = Field(min_length=10, max_length=600)
    need_id: str = Field(min_length=8, max_length=64)
    outcome: str
    note: str | None = Field(default=None, max_length=500)


class MeAction(BaseModel):
    t: str = Field(min_length=10, max_length=600)
    target_id: str | None = Field(default=None, max_length=64)


class ApplyForm(_Base):
    service_area: str | None = Field(default=None, max_length=200)


def _user_from_token(db: Session, token: str) -> User:
    uid = verify_token(token)
    if not uid:
        raise ApiError(401, "這個表單連結已過期或無效，請回到 LINE 重新開啟表單。")
    user = db.query(User).filter(User.line_uid == uid).first()
    if not user:
        raise ApiError(404, "找不到您的帳號，請先在 LINE 傳一句話註冊。")
    if user.is_active is False:
        raise ApiError(403, "您的帳號目前已停用，請聯絡管理員。")
    return user


def _clean(value: str | None) -> str | None:
    value = (value or "").strip()
    return value or None


def _apply_profile(user: User, form: _Base) -> str | None:
    """Save name and phone from the form onto the account; returns the cleaned address."""
    name = _clean(form.name)
    if name:
        user.name = check_name(name)
    phone = _clean(form.phone)
    if phone:
        if not PHONE_RE.match(phone):
            raise ApiError(422, "電話格式不正確，只能有數字、加號、括號、橫線與空白。")
        user.phone = phone
    return _clean(form.address)


def _notify(user: User, text: str, ask_location: bool) -> None:
    from app.services import line_notify
    try:
        if ask_location:
            line_notify.send_text_with_location_prompt(user.line_uid, text)
        else:
            line_notify.send_text(user.line_uid, text)
    except Exception:
        logger.warning("web form confirmation push failed", exc_info=True)


def _task_for(db: Session, user: User, need_id: str):
    """The need this volunteer is assigned to; raises a clear error when they are not."""
    from app.models.need import CommunityNeed
    from app.services.dispatch import assignee_user_id
    try:
        need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
    except Exception:
        need = None
    if not need:
        raise ApiError(404, "找不到這筆任務，可能已被刪除。")
    is_admin = bool(user.roles) and "admin" in user.roles
    if assignee_user_id(need) != str(user.id) and not is_admin:
        raise ApiError(403, "這筆任務不是派給您的，無法回報。")
    return need


@router.get("/api/context")
def form_context(t: str, n: str | None = None, db: Session = Depends(get_db)):
    from app.routers.linebot import NEED_ZH, STATUS_ZH, _is_staff
    user = _user_from_token(db, t)
    out = {"name": user.name, "phone": user.phone, "address": user.address,
           "is_staff": _is_staff(user), "has_location": user.lat is not None}
    if n:
        need = _task_for(db, user, n)
        out["task"] = {"need_id": str(need.id), "type": NEED_ZH.get(need.need_type, need.need_type),
                       "address": need.address, "description": need.description,
                       "status": need.status, "status_zh": STATUS_ZH.get(need.status, need.status)}
    return out


@router.post("/api/report")
def submit_report_form(form: ReportForm, db: Session = Depends(get_db)):
    from app.services.dispatch import REPORT_OUTCOME_ZH, report_task
    user = _user_from_token(db, form.t)
    need = _task_for(db, user, form.need_id)
    if form.outcome not in REPORT_OUTCOME_ZH:
        raise ApiError(422, "請選擇回報結果。")
    result = report_task(str(need.id), db, outcome=form.outcome, note=form.note,
                         actor_id=str(user.id), actor_label="volunteer:web")
    if result.get("error") == "invalid task state":
        raise ApiError(409, "這筆任務目前的狀態已經不能回報了（可能已完成、已取消，或已退回重新派遣）。")
    if result.get("error"):
        raise ApiError(422 if "請" in str(result["error"]) else 409, str(result["error"]))
    label = REPORT_OUTCOME_ZH[form.outcome]
    reply = f"📝 已收到您的回報：{label}。" + (f"\n說明：{form.note.strip()}" if (form.note or "").strip() else "")
    if form.outcome == "issue":
        reply += "\n管理員已收到通知，任務仍保留給您；需要換人請按任務卡上的「無法前往」。"
    _notify(user, reply, False)
    return {"ok": True, "message": reply}


@router.post("/api/need")
def submit_need_form(form: NeedForm, db: Session = Depends(get_db)):
    from app.routers.linebot import submit_needs
    user = _user_from_token(db, form.t)
    types = list(dict.fromkeys(form.types))
    if not types or any(t not in NEED_CHOICES for t in types):
        raise ApiError(422, "請至少勾選一項需要的物資。")
    address = _apply_profile(user, form)
    description = "網頁表單申請" + (f"（{form.people}人）" if form.people else "")
    if _clean(form.note):
        description += f"｜補充：{_clean(form.note)}"
    reply, ask = submit_needs(db, user, types, description, urgent=form.urgent, address=address)
    _notify(user, reply, ask)
    return {"ok": True, "message": reply, "need_location": ask}


@router.post("/api/resource")
def submit_resource_form(form: ResourceForm, db: Session = Depends(get_db)):
    from app.routers.linebot import _is_staff, save_resource
    user = _user_from_token(db, form.t)
    if not _is_staff(user):
        raise ApiError(403, "只有志工可以登記物資。想當志工請先送出志工申請。")
    if form.rtype not in RESOURCE_TYPES - {"sos"}:
        raise ApiError(422, "請選擇物資種類。")
    quantity = _clean(form.quantity)
    if not quantity:
        raise ApiError(422, "請填寫數量。")
    address = _apply_profile(user, form)
    reply, ask = save_resource(db, user, form.rtype, quantity, address, _clean(form.resource_name))
    _notify(user, reply, ask)
    return {"ok": True, "message": reply, "need_location": ask}


@router.post("/api/apply")
def submit_apply_form(form: ApplyForm, db: Session = Depends(get_db)):
    from app.routers.linebot import _is_staff
    from app.services.volunteer_application import submit
    user = _user_from_token(db, form.t)
    if _is_staff(user):
        raise ApiError(409, "您已經是志工／家屬／管理員了，不用重新申請。")
    name = check_name(form.name, what="姓名")
    _apply_profile(user, form)
    application = submit(db, line_uid=user.line_uid, name=name, phone=_clean(form.phone),
                         service_area=_clean(form.service_area))
    reply = (f"📋 已收到您的志工申請，{name}！\n管理員審核後會透過 LINE 通知您結果，不用再重複申請。\n"
             f"申請編號：{str(application.id)[:8]}")
    _notify(user, reply, False)
    return {"ok": True, "message": reply, "need_location": False}


def _tw(value) -> str:
    from datetime import timezone
    from app.timeutil import TAIWAN
    if value is None:
        return ""
    return value.replace(tzinfo=timezone.utc).astimezone(TAIWAN).strftime("%m/%d %H:%M")


@router.get("/api/me")
def my_records(t: str, db: Session = Depends(get_db)):
    """Everything this person can see or manage about themselves, in one page."""
    from app.models.care_relation import CareRelation
    from app.models.need import CommunityNeed
    from app.routers.linebot import NEED_ZH, STATUS_ZH, _is_staff
    from app.services import dispatch
    from app.services.form_token import form_url
    user = _user_from_token(db, t)
    needs = (db.query(CommunityNeed).filter(CommunityNeed.requester_id == user.id)
             .order_by(CommunityNeed.created_at.desc()).limit(30).all())
    contacts = db.query(CareRelation).filter(CareRelation.elderly_id == user.id, CareRelation.is_active == True).all()  # noqa: E712
    elders = db.query(CareRelation).filter(CareRelation.contact_id == user.id, CareRelation.is_active == True).all()  # noqa: E712
    out = {
        "name": user.name, "is_staff": _is_staff(user),
        "needs": [{"id": str(n.id), "type": NEED_ZH.get(n.need_type, n.need_type),
                   "status_zh": STATUS_ZH.get(n.status, n.status), "description": n.description,
                   "created": _tw(n.created_at), "can_cancel": n.status in ("open", "suggested", "matched")}
                  for n in needs],
        "family": [{"id": str(r.id), "name": r.contact.name if r.contact else "?", "relation": r.relation}
                   for r in contacts],
        "cared": [{"id": str(r.id), "name": r.elderly.name if r.elderly else "?"} for r in elders],
        "resources": [], "tasks": [],
    }
    if _is_staff(user):
        from app.models.resource import CommunityResource
        rows = db.query(CommunityResource).filter(CommunityResource.owner_id == user.id).all()
        out["resources"] = [{"id": str(r.id), "name": r.name, "quantity": r.quantity, "available": bool(r.is_available)}
                            for r in rows]
        out["tasks"] = [{**tk, "report_url": form_url("report", user.line_uid, tk["need_id"])}
                        for tk in dispatch.list_my_tasks(user, db)]
    return out


@router.post("/api/cancel_need")
def cancel_my_need(form: MeAction, db: Session = Depends(get_db)):
    from app.models.need import CommunityNeed
    from app.services import dispatch
    user = _user_from_token(db, form.t)
    try:
        need = db.query(CommunityNeed).filter(CommunityNeed.id == form.target_id,
                                              CommunityNeed.requester_id == user.id).first()
    except Exception:
        need = None
    if not need:
        raise ApiError(404, "找不到這筆需求。")
    if need.status not in ("open", "suggested", "matched"):
        raise ApiError(409, "這筆需求已經結束，不能取消。")
    dispatch.cancel_need(str(need.id), db)
    return {"ok": True}


@router.post("/api/withdraw_resource")
def withdraw_my_resource(form: MeAction, db: Session = Depends(get_db)):
    from app.models.resource import CommunityResource
    user = _user_from_token(db, form.t)
    try:
        res = db.query(CommunityResource).filter(CommunityResource.id == form.target_id,
                                                 CommunityResource.owner_id == user.id).first()
    except Exception:
        res = None
    if not res:
        raise ApiError(404, "找不到這份物資。")
    if not res.is_available:
        raise ApiError(409, "這份物資已被派出或保留中，不能撤回，請聯絡管理員。")
    db.delete(res)
    db.commit()
    return {"ok": True}


@router.post("/api/invite")
def make_family_invite(form: MeAction, db: Session = Depends(get_db)):
    from app.services.line_notify import oa_message_link
    from app.services.line_ops import create_invite
    user = _user_from_token(db, form.t)
    code = create_invite(db, user)
    return {"ok": True, "code": code, "link": oa_message_link(f"綁定 {code}")}


@router.post("/api/unbind")
def unbind_relation(form: MeAction, db: Session = Depends(get_db)):
    """Either side of a family link can end it."""
    from app.models.care_relation import CareRelation
    user = _user_from_token(db, form.t)
    try:
        rel = db.query(CareRelation).filter(CareRelation.id == form.target_id).first()
    except Exception:
        rel = None
    if not rel or str(user.id) not in (str(rel.elderly_id), str(rel.contact_id)):
        raise ApiError(404, "找不到這組關係。")
    db.delete(rel)
    db.commit()
    return {"ok": True}


@router.get("/{kind}")
def form_page(kind: str):
    if kind not in KINDS:
        raise ApiError(404, "找不到這個表單。")
    return FileResponse(FORM_PAGE)
