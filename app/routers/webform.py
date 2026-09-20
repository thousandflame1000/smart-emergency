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
KINDS = {"need", "res", "apply"}
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


@router.get("/api/context")
def form_context(t: str, db: Session = Depends(get_db)):
    from app.routers.linebot import _is_staff
    user = _user_from_token(db, t)
    return {"name": user.name, "phone": user.phone, "address": user.address,
            "is_staff": _is_staff(user), "has_location": user.lat is not None}


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


@router.get("/{kind}")
def form_page(kind: str):
    if kind not in KINDS:
        raise ApiError(404, "找不到這個表單。")
    return FileResponse(FORM_PAGE)
