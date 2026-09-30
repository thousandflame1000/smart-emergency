"""LINE 裡全螢幕開啟的 App（LIFF）。

身分：App 在 LINE 裡用 LIFF 取得 ID token，伺服器向 LINE 驗證後發一張跟網頁表單相同的
簽章憑證放進 cookie，所以 /f/api/*（我的紀錄、申請物資、邀請家人…）直接沿用，不用登入、
也不用任何驗證碼。測試或 LIFF 打不開時，可改用機器人發的表單憑證（#t=…）。
"""
import json
import logging
import os
import urllib.parse
import urllib.request

from fastapi import APIRouter, Depends, Request
from fastapi.responses import HTMLResponse, JSONResponse
from pydantic import BaseModel, Field
from sqlalchemy.orm import Session

from app.config import settings
from app.database import get_db
from app.errors import ApiError
from app.models.user import User
from app.services.form_token import TOKEN_TTL_SECONDS, make_token, verify_token
from app.timeutil import today_tw

router = APIRouter()
log = logging.getLogger(__name__)
APP_COOKIE = "app_session"
PAGE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "static", "app.html")


class SessionRequest(BaseModel):
    id_token: str = Field(default="", max_length=4000)
    token: str = Field(default="", max_length=600)


class CheckinRequest(BaseModel):
    status: str = Field(pattern="^(ok|unwell)$")


class LocationRequest(BaseModel):
    lat: float = Field(ge=-90, le=90)
    lng: float = Field(ge=-180, le=180)


class AskRequest(BaseModel):
    question: str = Field(min_length=1, max_length=500)


def verify_id_token(id_token: str) -> dict | None:
    """向 LINE 驗證 LIFF 給的 ID token；成功回 payload（sub 就是 LINE user ID）。"""
    if not id_token or not settings.LINE_LOGIN_CHANNEL_ID:
        return None
    body = urllib.parse.urlencode({"id_token": id_token, "client_id": settings.LINE_LOGIN_CHANNEL_ID}).encode()
    request = urllib.request.Request("https://api.line.me/oauth2/v2.1/verify", data=body, method="POST")
    try:
        with urllib.request.urlopen(request, timeout=8) as response:
            payload = json.loads(response.read().decode())
    except Exception:
        log.warning("LIFF id token verification failed", exc_info=True)
        return None
    return payload if payload.get("sub") else None


def _user(db: Session, request: Request) -> User:
    uid = verify_token(request.cookies.get(APP_COOKIE, ""))
    user = db.query(User).filter(User.line_uid == uid).first() if uid else None
    if not user:
        raise ApiError(401, "請從 LINE 重新打開 App。")
    if user.is_active is False:
        raise ApiError(403, "您的帳號目前已停用，請聯絡社區管理員。")
    return user


@router.get("/app", include_in_schema=False)
@router.get("/app/", include_in_schema=False)
def app_page():
    page = open(PAGE, encoding="utf-8").read().replace("{{LIFF_ID}}", settings.LIFF_ID)
    return HTMLResponse(page, headers={"Cache-Control": "no-cache"})


@router.post("/app/api/session")
def open_session(body: SessionRequest, db: Session = Depends(get_db)):
    payload = None
    if body.id_token:
        payload = verify_id_token(body.id_token)
        uid = payload.get("sub") if payload else None
    else:
        uid = verify_token(body.token)
    if not uid:
        raise ApiError(401, "無法確認身分，請從 LINE 重新打開 App。")
    user = db.query(User).filter(User.line_uid == uid).first()
    if user is None:
        # 第一次打開 App 的人跟第一次傳訊息一樣自動註冊成居民
        user = User(name=(payload or {}).get("name") or "LINE 使用者", roles=["elderly"], line_uid=uid)
        db.add(user)
        db.commit()
    elif user.is_active is False:
        raise ApiError(403, "您的帳號目前已停用，請聯絡社區管理員。")
    token = make_token(uid)
    response = JSONResponse({"ok": True})
    for name, path in ((APP_COOKIE, "/app"), ("form_session", "/f")):
        response.set_cookie(name, token, max_age=TOKEN_TTL_SECONDS, httponly=True, samesite="lax",
                            secure=settings.APP_ENV == "production", path=path)
    return response


@router.get("/app/api/me")
def me(request: Request, db: Session = Depends(get_db)):
    from app.labels import is_placeholder_name
    from app.models.checkin import DailyCheckin
    from app.models.need import CommunityNeed
    from app.routers.linebot import _get_mode
    from app.models.safety_check import SafetyCheck
    from app.services import rollcall
    user = _user(db, request)
    checkin = (db.query(DailyCheckin)
               .filter(DailyCheckin.elderly_id == user.id, DailyCheckin.date == today_tw()).first())
    roles = user.roles or []
    # 災時點名：這次災害有沒有回報過（早上的打卡不算）
    round_id = rollcall.current_round(db) if user.has_role("elderly") else None
    reply = (db.query(SafetyCheck).filter(SafetyCheck.round_id == round_id, SafetyCheck.user_id == user.id).first()
             if round_id else None)
    return {
        "name": "" if is_placeholder_name(user.name) else user.name,
        "roles": roles,
        "is_volunteer": "volunteer" in roles or "field_staff" in roles,
        "is_admin": "admin" in roles,
        "checkin": checkin.status if checkin else None,
        "emergency": _get_mode(db) == "emergency",
        "safety": (reply.status if reply else "pending") if round_id else None,
        "has_location": user.lat is not None,
        "open_needs": db.query(CommunityNeed).filter(
            CommunityNeed.requester_id == user.id,
            CommunityNeed.status.in_(("open", "suggested", "matched"))).count(),
        "sos_tasks": _sos_tasks(db, user),
        "my_sos": bool(db.query(CommunityNeed).filter(CommunityNeed.requester_id == user.id, CommunityNeed.need_type == "sos",
                                                       CommunityNeed.status == "open").first()),
    }


def _sos_tasks(db: Session, user) -> list[dict]:
    """我受理、還沒結案的求救：App 首頁也要能撥號、導航、回報到場與處理完成，跟 LINE 卡片一樣。"""
    from app.models.dispatch_event import DispatchEvent
    from app.models.need import CommunityNeed
    from app.services.sos import spot
    from app.validation import tel_uri
    rows = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos", CommunityNeed.status == "open",
                                          CommunityNeed.responder_id == str(user.id)).order_by(CommunityNeed.created_at).all()
    arrived = {str(e.need_id) for e in db.query(DispatchEvent).filter(
        DispatchEvent.action == "sos_on_scene", DispatchEvent.need_id.in_([str(n.id) for n in rows])).all()} if rows else set()
    out = []
    for n in rows:
        where = spot(n)
        phone = n.requester.phone if n.requester else None
        out.append({"id": str(n.id), "name": n.requester.name if n.requester else "", "address": n.address or "",
                    "note": n.description or "", "tel": tel_uri(phone), "phone": phone if tel_uri(phone) else None,
                    "navigate": f"https://www.google.com/maps/dir/?api=1&destination={where[0]},{where[1]}" if where else None,
                    "arrived": str(n.id) in arrived})
    return out


@router.post("/app/api/sos/cancel")
def sos_cancel(request: Request, db: Session = Depends(get_db)):
    """長者誤按或已經沒事：取消自己的求救（App 上先跳確認視窗）。"""
    from app.models.need import CommunityNeed
    from app.services import sos
    user = _user(db, request)
    need = db.query(CommunityNeed).filter(CommunityNeed.requester_id == user.id, CommunityNeed.need_type == "sos",
                                          CommunityNeed.status == "open").first()
    if need is None:
        return {"message": "您目前沒有進行中的求救。"}
    result = sos.cancel_by_requester(db, str(need.id), user)
    if result.get("error"):
        raise ApiError(409, result["error"])
    return {"message": "已取消求救，已告訴家人與管理員。如果又需要幫忙，隨時按「需要幫忙」。"}


@router.post("/app/api/sos/{need_id}/{step}")
def sos_step(need_id: str, step: str, request: Request, db: Session = Depends(get_db)):
    """處理人在 App 上回報「已到場」「處理完成」，跟 LINE 卡片的按鈕走同一段邏輯。"""
    from app.services import sos
    user = _user(db, request)
    if step == "arrived":
        result = sos.arrive(db, need_id, user)
    elif step == "done":
        result = sos.finish(db, need_id, user)
    else:
        raise ApiError(404, "不認得的動作")
    if result.get("error"):
        raise ApiError(409, result["error"])
    return {"message": "已回報到場，管理員知道您到了。" if step == "arrived" else "已結案，當事人與管理員都已收到通知。謝謝您！"}


@router.post("/app/api/checkin")
def checkin(body: CheckinRequest, request: Request, db: Session = Depends(get_db)):
    from app.routers.linebot import _report_unwell
    from app.services import checkin as checkin_svc
    user = _user(db, request)
    if body.status == "unwell":
        return {"message": _report_unwell(user, db)}
    checkin_svc.record_ok(db, user)
    from app.services import rollcall
    if rollcall.current_round(db):
        return {"message": "✅ 已回報平安，社區與家人都看得到。需要幫忙隨時按「需要幫忙」。"}
    return {"message": "✅ 收到，今天也要保重喔！"}


@router.post("/app/api/sos")
def sos(request: Request, db: Session = Depends(get_db)):
    from app.routers.linebot import _sos_reply_text, _trigger_sos
    user = _user(db, request)
    return {"message": _sos_reply_text(_trigger_sos(user, db))}


@router.post("/app/api/location")
def location(body: LocationRequest, request: Request, db: Session = Depends(get_db)):
    from app.routers.linebot import save_location
    user = _user(db, request)
    needs_fixed, res_fixed = save_location(db, user, body.lat, body.lng)
    return {"ok": True, "needs_fixed": needs_fixed, "resources_fixed": res_fixed}


@router.get("/app/api/nearby")
def nearby(request: Request, db: Session = Depends(get_db)):
    from app.models.resource_point import EMERGENCY_POINT_TYPES, POINT_TYPES
    from app.routers.linebot import _get_mode
    from app.services.nearby import OpenShelter, _supplies, capacity_text, nearest_points
    from app.validation import tel_uri
    user = _user(db, request)
    if user.lat is None:
        return {"has_location": False, "points": []}
    emergency = _get_mode(db) == "emergency"
    points = []
    for row in nearest_points(db, user.lat, user.lng, emergency, limit=8):
        p = row["point"]
        points.append({
            "name": p.name, "type": POINT_TYPES.get(p.point_type, p.point_type), "km": round(row["km"], 1),
            "emergency": p.point_type in EMERGENCY_POINT_TYPES, "supplies": _supplies(p),
            "capacity": capacity_text(p), "hours": p.operating_hours,
            "official": p.area + "・內政部公告" if isinstance(p, OpenShelter) else None,
            "tel": tel_uri(p.phone), "phone": p.phone if tel_uri(p.phone) else None,
            "navigate": f"https://www.google.com/maps/dir/?api=1&destination={p.lat},{p.lng}",
        })
    from app.services import aed
    aeds = [{"name": r["aed"].name, "place": r["aed"].place, "km": round(r["km"], 2), "hours": r["hours"],
             "open": r["open"], "navigate": f"https://www.google.com/maps/dir/?api=1&destination={r['aed'].lat},{r['aed'].lng}"}
            for r in aed.nearest(user.lat, user.lng, limit=2)]
    return {"has_location": True, "emergency": emergency, "points": points, "aeds": aeds}


@router.post("/app/api/ask")
def ask(body: AskRequest, request: Request, db: Session = Depends(get_db)):
    from app.routers.linebot import _question_allowed
    from app.services import rag
    user = _user(db, request)
    if not _question_allowed(user.line_uid):
        raise ApiError(429, "問題有點多，請稍等一分鐘再問。有生命危險請直接撥 119。")
    try:
        result = rag.query(body.question.strip())
    except Exception:
        log.exception("app ask failed")
        raise ApiError(503, "AI 助手暫時無法使用，請稍後再試。有生命危險請直接撥 119。")
    from app.services import aed
    answer = result.get("answer")
    if answer and aed.about_cpr(body.question):
        where = aed.one_line(user.lat, user.lng)
        answer += f"\n\n📍 {where}" if where else "\n\n📍 在「物資」分頁定位後，再問一次就會告訴您最近的 AED。"
    return {"answer": answer, "sources": result.get("sources", [])[:3],
            "has_answer": bool(result.get("has_answer")), "mode": result.get("mode", "ai")}
