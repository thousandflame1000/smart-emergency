import logging
import re

from fastapi import APIRouter, Request, HTTPException
from starlette.concurrency import run_in_threadpool
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    FollowEvent, MessageEvent, PostbackEvent,
    TextMessageContent, LocationMessageContent,
)

from app.config import settings
from app.database import get_db
from app.services import checkin as checkin_svc
from app.services import conversation, line_ops
from app.services.line_notify import reply_text
from app.services.task_commands import TaskWorkflowError
from app.services.task_line_execution import TaskLineExecutionService
from app.services.task_line_messages import reply_task_progress_message
from app.services.task_line_security import TaskLineSecurityError
from app.models.user import User
from app.timeutil import now_utc, today_tw

logger = logging.getLogger(__name__)

router  = APIRouter()
handler = WebhookHandler(settings.LINE_CHANNEL_SECRET)

_DEV_MODE = settings.LINE_CHANNEL_SECRET in ("dummy_secret", "test", "dev")

STAFF_ROLES = ["volunteer", "family", "admin"]
EMERGENCY_TIP = "🚨 如果有生命危險（大量出血、意識不清、呼吸困難、火災），請立刻撥打 119，不要等訊息回覆。"
LOCATION_HINT = "📍 我們還不知道您在哪裡，志工可能找不到您。請點下面的按鈕分享位置（或點 LINE 的「＋」→「位置資訊」）。"

NEED_ZH = {"water": "💧飲用水", "food": "🍱食物", "first_aid": "🩹急救用品",
           "shelter": "🏠庇護所", "vehicle": "🚗交通工具", "other": "📦其他物資",
           "sos": "🆘 緊急求助", "demo_water": "💧飲用水"}
STATUS_ZH = {"open": "⏳ 待媒合", "suggested": "🔎 系統已建議，等待管理員核准",
             "matched": "🚚 已派遣，志工正在處理", "fulfilled": "✅ 已完成", "cancelled": "已取消"}
RES_TYPE_ZH = {"water": "飲用水", "food": "食物", "first_aid": "急救用品", "shelter": "庇護所",
               "vehicle": "交通工具", "tool": "工具", "other": "其他"}


# ──────────────────────────────────────────────
# Webhook 入口
# ──────────────────────────────────────────────
@router.post("/line")
async def line_webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body      = await request.body()

    try:
        # The handlers are synchronous (database, LINE calls, address lookup). Run directly in this
        # async route they block the event loop, freezing every other request while one slow
        # message is processed.
        await run_in_threadpool(handler.handle, body.decode(), signature)
    except InvalidSignatureError:
        if _DEV_MODE:
            pass   # dev 模式略過驗證
        else:
            raise HTTPException(status_code=400, detail="Invalid signature")
    except Exception:
        # 單一事件處理失敗不能讓整個 webhook 回 500：LINE 會一直重送同一批
        # 事件，一個壞訊息就會反覆卡住之後所有人的訊息。記錄下來、回 200。
        logger.exception("[linebot] 事件處理失敗")

    return "OK"


# ── 模擬打卡（dev 測試用，正式環境移除）────────
@router.post("/line/simulate_checkin")
async def simulate_checkin(elderly_name: str):
    """
    Dev only: 手動幫指定長者建立今日打卡記錄（並嘗試真的推播 LINE 訊息）
    POST /webhook/line/simulate_checkin?elderly_name=陳月英
    """
    if not _DEV_MODE:
        raise HTTPException(status_code=403, detail="只在開發模式下開放")

    from app.database import SessionLocal
    from app.models.checkin import DailyCheckin
    from app.models.user import User as U

    db = SessionLocal()
    try:
        elderly = db.query(U).filter(
            U.name == elderly_name,
            U.role_filter("elderly"),
        ).first()

        if not elderly:
            return {"error": f"找不到長者：{elderly_name}"}

        existing = db.query(DailyCheckin).filter(
            DailyCheckin.elderly_id == elderly.id,
            DailyCheckin.date == today_tw()
        ).first()
        if existing:
            return {
                "message": f"{elderly_name} 今日已有打卡記錄",
                "checkin_id": str(existing.id),
                "status": existing.status,
                "note": "如需重新測試請先清除今日記錄或改用其他長者",
            }

        checkin = DailyCheckin(elderly_id=elderly.id, date=today_tw(), status="pending")
        db.add(checkin)
        db.commit()
        db.refresh(checkin)

        line_sent = False
        if elderly.line_uid:
            try:
                from app.services.line_notify import send_checkin_message
                send_checkin_message(elderly.line_uid, str(checkin.id))
                line_sent = True
            except Exception:
                pass

        return {
            "message": f"已為 {elderly_name} 建立今日打卡記錄"
                       + ("，並已推播 LINE 打卡訊息" if line_sent else "（此長者未綁定 LINE，僅建立記錄，可用 postback API 手動測試按鈕行為）"),
            "checkin_id": str(checkin.id),
            "line_sent": line_sent,
        }
    finally:
        db.close()


# ──────────────────────────────────────────────
# 共用小工具
# ──────────────────────────────────────────────
def _say(event, text: str, ask_location: bool = False) -> None:
    """Reply; optionally with a one-tap "share my location" button attached."""
    if ask_location:
        from app.services.line_notify import reply_text_with_location_prompt
        reply_text_with_location_prompt(event.reply_token, text)
    else:
        reply_text(event.reply_token, text)


def _is_staff(user) -> bool:
    return bool(user.roles) and any(r in user.roles for r in STAFF_ROLES)


def _welcome_text(name: str) -> str:
    return (
        f"👋 {name}，歡迎加入鄰里守望！\n\n"
        "您已自動註冊。先從下方「居民服務」開始：\n"
        "・有危險：選「緊急求助」或傳「需要幫忙」\n"
        "・物資或生活需求：選「申請需求」一次填寫\n"
        "・每天早上會問您平安，按「我很好」就可以\n"
        "・「居民中心」有家屬綁定、紀錄與完整說明\n\n"
        f"{LOCATION_HINT}"
    )


def _register_user(db, line_uid: str) -> User:
    from app.services.line_notify import get_display_name
    display_name = get_display_name(line_uid)
    user = User(name=display_name, roles=["elderly"], line_uid=line_uid)
    db.add(user)
    db.commit()
    db.refresh(user)
    return user


def _resolve_coordinates(user, address: str | None = None):
    """Best coordinates we can get for this person right now: what they shared, else a
    geocode of an address, else None. Remembers a successful geocode on the user."""
    if user.lat is not None and user.lng is not None and not address:
        return user.lat, user.lng
    from app.services import places
    if address:
        found = places.geocode_address(address)
        if found:
            return found
        if user.lat is not None and user.lng is not None:
            return user.lat, user.lng
        return None
    if user.address:
        found = places.geocode_address(user.address)
        if found:
            user.lat, user.lng = found
            return found
    return None


# ──────────────────────────────────────────────
# 意圖判斷：否定詞、多需求、關鍵字涵蓋
# ──────────────────────────────────────────────
NEED_KEYWORDS = {
    "water":     ["需要水", "缺水", "沒水", "沒有水", "要水", "沒水喝", "想喝水", "水不夠", "水用完", "斷水", "停水"],
    "food":      ["需要食物", "需要食", "缺食", "沒食物", "沒有食物", "要食物", "沒吃", "沒東西吃",
                  "沒有東西吃", "肚子餓", "沒飯吃", "糧食不足", "斷糧"],
    "first_aid": ["需要藥", "需要醫療", "缺藥", "沒有藥", "藥吃完", "受傷", "需要急救", "流血", "骨折", "發燒", "跌倒起不來"],
    "shelter":   ["需要庇護", "無家可歸", "房子損壞", "沒地方住", "住不了", "屋頂破"],
    "vehicle":   ["需要車", "需要接送", "出不去", "需要載"],
}
# 命中這些詞代表可能是人身安全事件：先跳確認卡，不直接建立需求。
SOS_WORDS = ["需要幫忙", "救命", "緊急", "昏倒", "胸痛", "無法呼吸", "呼吸困難", "失去意識",
             "流血不止", "大量出血", "起火", "火災", "著火"]
NEGATORS = ("不", "沒", "別", "免", "無需", "已經有", "已有")
URGENCY_BY_TYPE = {"first_aid": 4}
CANCEL_NEED_WORDS = ("取消需求", "取消求助", "我不需要了", "不需要了", "已經收到了", "已收到物資")
CHECKIN_OK_WORDS = ("我很好", "好", "OK", "ok", "沒事", "沒事了", "平安")
KNOWN_TOPICS = ("CPR", "cpr", "AED", "aed", "止血", "心肺復甦", "燒燙傷", "骨折", "中暑", "溺水",
                "哽塞", "哈姆立克", "地震", "颱風", "淹水", "電線", "停電", "壓瘡", "褥瘡", "失智",
                "癲癇", "抽搐", "過敏", "一氧化碳", "土石流", "跌倒預防", "低血糖", "失溫")
QUESTION_HINTS = ("怎麼", "如何", "怎樣", "什麼", "該怎", "要怎", "能不能", "可以嗎", "嗎", "呢", "？", "?", "教我")


def _negated(text: str, idx: int) -> bool:
    return any(n in text[max(0, idx - 3):idx] for n in NEGATORS)


def _find_positive(text: str, keywords) -> tuple[bool, bool]:
    """(has un-negated hit, has any negated hit)."""
    positive = negated = False
    for kw in keywords:
        for m in re.finditer(re.escape(kw), text):
            if _negated(text, m.start()):
                negated = True
            else:
                positive = True
    return positive, negated


def parse_intent(text: str) -> dict:
    needs, saw_negated = [], False
    for ntype, kws in NEED_KEYWORDS.items():
        pos, neg = _find_positive(text, kws)
        if pos:
            needs.append(ntype)
        elif neg:
            saw_negated = True
    sos_pos, sos_neg = _find_positive(text, SOS_WORDS)
    return {"needs": needs, "sos": sos_pos, "negated": saw_negated or sos_neg}


MAX_QUESTION_LENGTH = 200


def _looks_like_question(text: str) -> bool:
    """只有真的像在問問題（有疑問詞或已知急救主題）才送給付費的 AI 助手。

    之前是「志工傳 8 個字以上的任何訊息」都送出去；開放給所有人之後如果沿用這個條件，
    隨便一句閒聊或貼一大段亂碼都會呼叫一次 Gemini。"""
    if len(text) > MAX_QUESTION_LENGTH:
        return False
    return any(t in text for t in KNOWN_TOPICS) or any(h in text for h in QUESTION_HINTS)


# ──────────────────────────────────────────────
# 一鍵求助
# ──────────────────────────────────────────────
def _trigger_sos(user, db) -> dict:
    """真的觸發緊急求助，並回報「實際上有誰被通知」。

    只從「使用者在確認卡上按了『對，我需要立即救援』」（confirm_sos）或打卡卡片上的
    「需要幫忙」按鈕呼叫，不會從自由文字直接觸發。

    之前的問題：當天沒有打卡紀錄時完全不會通知任何人，卻照樣回覆「正在通知家屬和
    志工」；一鍵求助建立的需求類型是 other，水／食物／藥的志工根本配不到；打卡卡片
    上的「需要幫忙」按鈕則只改狀態、根本沒有發警報。"""
    from app.models.checkin import DailyCheckin
    from app.models.need import CommunityNeed
    from app.services.alert import notify_admins, send_alerts_for_checkin

    checkin = (
        db.query(DailyCheckin)
        .filter(DailyCheckin.elderly_id == user.id, DailyCheckin.date == today_tw())
        .first()
    )
    if checkin is None:
        checkin = DailyCheckin(elderly_id=user.id, date=today_tw(), status="help_needed")
        db.add(checkin)
    checkin.status = "help_needed"
    checkin.responded_at = now_utc()
    db.commit()
    db.refresh(checkin)

    try:
        contacts = send_alerts_for_checkin(checkin.id, "help_needed", db)
    except Exception:
        logger.exception("[sos] 通知照護聯絡人失敗")
        contacts = 0

    existing_sos = db.query(CommunityNeed).filter(
        CommunityNeed.requester_id == user.id,
        CommunityNeed.status.in_(["open", "suggested", "matched"]),
        CommunityNeed.need_type == "sos",
    ).first()
    created = False
    sos_need = existing_sos
    if not existing_sos:
        from app.services.zones import resolve_zone_for_point
        coords = _resolve_coordinates(user)
        sos_need = CommunityNeed(
            requester_id=user.id,
            need_type="sos",
            description="LINE 一鍵求助（需要幫忙）",
            address=user.address,
            lat=coords[0] if coords else None,
            lng=coords[1] if coords else None,
            urgency=5,
            zone_id=resolve_zone_for_point(db, coords[0] if coords else None, coords[1] if coords else None),
        )
        db.add(sos_need)
        db.commit()
        created = True

    where = user.address or ("已分享的位置" if user.lat is not None else "位置未知")
    admins = notify_admins(
        db,
        f"🆘 {user.name} 剛按下一鍵求助（{where}）。{'已' if created else '之前已'}建立緊急求助單，請立即聯繫確認。",
        buttons=[{"label": "✅ 已聯繫處理", "data": f"action=admin_sos&need_id={sos_need.id}", "color": "#c0392b"}],
    )
    return {"contacts": contacts, "admins": admins, "created": created}


def _report_unwell(user, db, checkin=None) -> str:
    """Elder says they are not feeling well: record it and let their family know.

    Not an emergency, so no SOS need is created and admins are not paged. It is a middle step
    between "I'm fine" and "help": family gets a card, the elder gets clear next steps."""
    from app.models.checkin import DailyCheckin
    from app.services.alert import send_alerts_for_checkin
    if checkin is None:
        checkin = (db.query(DailyCheckin)
                   .filter(DailyCheckin.elderly_id == user.id, DailyCheckin.date == today_tw()).first())
    if checkin is None:
        checkin = DailyCheckin(elderly_id=user.id, date=today_tw(), status="pending")
        db.add(checkin)
        db.commit()
        db.refresh(checkin)
    if checkin.status == "help_needed":
        return "您剛剛已經按過「需要幫忙」，家人和管理員都已收到通知，請保持手機暢通。"
    if checkin.status == "unwell":
        return "您今天已經回報過身體不舒服，家人已收到通知。如果變嚴重請按「需要幫忙」，或直接撥 119。"
    checkin_svc.mark_checkin(str(checkin.id), "unwell", db)
    try:
        told = send_alerts_for_checkin(checkin.id, "unwell", db)
    except Exception:
        logger.exception("[unwell] 通知家人失敗")
        told = 0
    lines = ["收到了，請好好休息 🙏"]
    lines.append("已通知您的家人關心您。" if told else "目前沒有登記可以通知的家人，可以傳「邀請家人」請家人綁定。")
    lines.append("如果變嚴重（胸痛、呼吸困難、跌倒起不來、意識不清），請立刻按選單「需要幫忙」，或直接撥 119。")
    return "\n\n".join(lines)


def _sos_reply_text(result: dict) -> str:
    lines = ["🆘 已收到您的求助！"]
    if result["contacts"]:
        lines.append(f"已通知您的 {result['contacts']} 位照護聯絡人。")
    else:
        lines.append("目前沒有登記可以通知的家屬或志工。")
    if result["admins"]:
        lines.append("並已通知社區管理員。")
    else:
        lines.append("求助單已送到管理後台，管理員上線就會看到。")
    lines.append(EMERGENCY_TIP)
    return "\n".join(lines)


# ──────────────────────────────────────────────
# 志工申請（一次問一題）
# ──────────────────────────────────────────────
APPLY_PREFIXES = ["志工申請", "申請志工", "我要當志工"]


def _finish_application(event, db, user, name, phone, area) -> None:
    from app.services.volunteer_application import submit
    application = submit(db, line_uid=user.line_uid, name=name, phone=phone, service_area=area)
    conversation.clear(db, user.line_uid)
    _say(event,
         f"📋 已收到您的志工申請，{name}！\n"
         "管理員審核後會透過 LINE 通知您結果，不用再重複申請。\n"
         f"申請編號：{str(application.id)[:8]}")


def _continue_flow(event, db, user, text, state) -> bool:
    step = state.get("step")
    if state.get("flow") != "volunteer_apply":
        conversation.clear(db, user.line_uid)
        return False
    if step == "name":
        if len(text) > 20:
            _say(event, "姓名有點長，請再輸入一次（20 字以內）。傳「取消」可以中止申請。")
            return True
        conversation.advance(db, user.line_uid, state, "phone", name=text)
        _say(event, f"好的，{text}。請問聯絡電話？（不想留可以傳「略過」）")
        return True
    if step == "phone":
        phone = None
        if text not in ("略過", "跳過", "不留"):
            digits = re.sub(r"[\s\-]", "", text)
            if not (digits.isdigit() and 8 <= len(digits) <= 12):
                _say(event, "電話格式好像不太對（請輸入 8 到 12 位數字），或傳「略過」。")
                return True
            phone = digits
        conversation.advance(db, user.line_uid, state, "area", phone=phone)
        _say(event, "最後一題：您方便服務的區域？（例如「台中市南區」，或傳「略過」）")
        return True
    if step == "area":
        area = None if text in ("略過", "跳過") else text
        data = state.get("data", {})
        _finish_application(event, db, user, data.get("name", user.name), data.get("phone"), area)
        return True
    conversation.clear(db, user.line_uid)
    return False


def _handle_apply(event, db, user, text) -> bool:
    prefix = next((p for p in APPLY_PREFIXES if text.startswith(p)), None)
    if prefix is None:
        return False
    if _is_staff(user):
        _say(event, "您已經是志工／家屬／管理員了，不用重新申請。")
        return True
    rest = text[len(prefix):].strip()
    if not rest:
        from app.services.form_token import form_url
        from app.services.line_forms import LINK_TITLES, link_card
        from app.services.line_notify import reply_flex_message
        reply_flex_message(event.reply_token, LINK_TITLES["apply"],
                           link_card("apply", form_url("apply", user.line_uid)))
        return True
    parts = rest.split()
    phone = parts[1] if len(parts) > 1 else None
    area = " ".join(parts[2:]) if len(parts) > 2 else None
    _finish_application(event, db, user, parts[0], phone, area)
    return True


# ──────────────────────────────────────────────
# 志工指令
# ──────────────────────────────────────────────
def _handle_volunteer_commands(event, db, user, text) -> bool:
    from app.models.resource import CommunityResource
    staff = _is_staff(user)

    if text in ["登記物資", "物資登記", "登記"]:
        if not staff:
            _say(event, "此功能僅限志工使用。想當志工請傳「我要當志工」。")
            return True
        _open_form(event, db, user, "res")
        return True

    if text in ("接單", "可接任務", "找任務"):
        _list_claimable(event, db, user)
        return True

    if text in ["我的物資"]:
        my_res = db.query(CommunityResource).filter(
            CommunityResource.owner_id == user.id
        ).order_by(CommunityResource.created_at.desc()).limit(5).all()
        if not my_res:
            _say(event, "您目前沒有已登記的物資。\n傳「登記物資」開始登記。")
        else:
            lines = ["📦 您已登記的物資（最近5項）：\n"]
            for r in my_res:
                status = "✅ 可用" if r.is_available else "❌ 已媒合"
                lines.append(f"・{RES_TYPE_ZH.get(r.resource_type, r.resource_type)} — {r.name}"
                             f"（{r.quantity or '數量未填'}）{status}")
            _say(event, "\n".join(lines))
        return True

    if text in ["取消物資", "撤回物資", "刪除物資"]:
        mine = db.query(CommunityResource).filter(
            CommunityResource.owner_id == user.id, CommunityResource.is_available == True
        ).all()
        for r in mine:
            db.delete(r)
        db.commit()
        _say(event, f"已撤回 {len(mine)} 份還沒被媒合的物資。已被媒合的不能撤回，請直接聯絡管理員。"
             if mine else "您目前沒有可撤回的物資（已被媒合的無法撤回）。")
        return True

    prefix = next((p for p in ["新增長者", "幫長者登記", "代辦長者", "登記長者"] if text.startswith(p)), None)
    if staff and prefix:
        rest = text[len(prefix):].strip()
        if not rest:
            _say(event, "請用以下格式：\n新增長者 [姓名] [地址]\n\n範例：\n新增長者 王奶奶 台中市南區崇倫街88號")
            return True
        from app.models.care_relation import CareRelation
        parts = rest.split(None, 1)
        elder_name = parts[0]
        elder_address = parts[1] if len(parts) > 1 else None
        existing_elder = db.query(User).filter(User.name == elder_name, User.role_filter("elderly")).first()
        if existing_elder:
            elder, created = existing_elder, False
        else:
            elder = User(name=elder_name, roles=["elderly"], address=elder_address, is_active=True)
            if elder_address:
                from app.services import places
                found = places.geocode_address(elder_address)
                if found:
                    elder.lat, elder.lng = found
            db.add(elder)
            db.commit()
            db.refresh(elder)
            created = True
        if not db.query(CareRelation).filter(
            CareRelation.elderly_id == elder.id, CareRelation.contact_id == user.id
        ).first():
            db.add(CareRelation(elderly_id=elder.id, contact_id=user.id,
                                relation="家屬代理登記", notify_order=1, is_active=True))
            db.commit()
        _say(event,
             f"✅ 已{'建立' if created else '找到'}長者資料：{elder_name}\n"
             f"地址：{elder_address or '未填，請後續補充'}\n\n"
             "已將您設為此長者的照護聯絡人，未回應打卡時會優先通知您。\n"
             "若長者本人有 LINE，請他加入官方帳號並傳「我很好」即可完成綁定；\n"
             "若沒有智慧型手機，請聯絡管理員另行安排追蹤方式。")
        return True

    if staff and text.startswith("我有"):
        _register_resource(event, db, user, text[2:].strip())
        return True
    return False


RES_KEYWORDS = {
    "water":     ["水", "飲水", "礦泉水", "飲用水", "桶裝水"],
    "food":      ["食物", "食品", "便當", "乾糧", "泡麵", "罐頭"],
    "first_aid": ["藥", "急救", "藥品", "醫療", "繃帶", "消毒"],
    "vehicle":   ["車", "汽車", "機車", "貨車", "接送"],
    "shelter":   ["空間", "房間", "庇護", "地方", "場地"],
    "tool":      ["工具", "電鋸", "發電機", "手電筒", "鏟子"],
}


def _list_claimable(event, db, user) -> None:
    from app.services import dispatch, line_forms
    from app.services.line_notify import reply_flex_message
    if not _is_staff(user):
        _say(event, "此功能僅限志工使用。想當志工請傳「我要當志工」。")
        return
    found = dispatch.list_claimable(user, db)
    if not found["items"]:
        if not found["has_resources"] and found["has_registered"]:
            _say(event, "您登記的物資目前都已派出或保留中，沒有可接的單。物資補齊後請再點選單的「登記表單」登記。")
        elif not found["has_resources"]:
            _say(event, "您目前沒有登記可提供的物資，所以沒有可接的單。請先點選單的「登記表單」登記物資。")
        elif found["open_total"] == 0:
            _say(event, "目前沒有待處理的需求，辛苦了 🙏 有新需求或管理員派單時會直接通知您。")
        else:
            _say(event, "目前待處理的需求都不是您登記的物資種類。管理員派單時會直接通知您。")
        return
    cards = [{"need_id": str(i["need"].id), "type": NEED_ZH.get(i["need"].need_type, i["need"].need_type),
              "urgency": i["need"].urgency, "address": i["need"].address, "distance_km": i["dist_km"],
              "description": i["need"].description} for i in found["items"]]
    reply_flex_message(event.reply_token, "可接的任務", line_forms.claim_carousel(cards))


def _handle_claim(event, db, user, need_id: str) -> None:
    from app.services import dispatch
    if not _is_staff(user):
        _say(event, "此功能僅限志工使用。")
        return
    result = dispatch.claim_need(need_id, user, db)
    if result.get("error"):
        _say(event, result["error"])
        return
    _say(event, f"✅ 接單成功！已保留您的「{result['resource_name']}」，任務卡稍後會傳給您，"
                "上面有地圖導航與回報按鈕。求助的人已收到通知。")


def _register_resource(event, db, user, rest: str) -> None:
    from app.models.resource import CommunityResource
    detected_type = "other"
    for rtype, keywords in RES_KEYWORDS.items():
        if any(kw in rest for kw in keywords):
            detected_type = rtype
            break
    remain = rest
    for kw_list in RES_KEYWORDS.values():
        for kw in kw_list:
            remain = remain.replace(kw, "", 1).strip()
    parts = remain.split(None, 1)
    quantity = parts[0] if parts else None
    explicit_address = parts[1] if len(parts) > 1 else None
    text, ask = save_resource(db, user, detected_type, quantity, explicit_address)
    _say(event, text, ask_location=ask)


def save_resource(db, user, detected_type: str, quantity, explicit_address, resource_name=None) -> tuple[str, bool]:
    """Create or update this person's resource. Returns (reply text, whether to ask for a location)."""
    from app.models.resource import CommunityResource
    from app.models.need import CommunityNeed
    from app.services.inventory import set_quantity_fields
    from app.services.zones import resolve_zone_for_point
    address = explicit_address or (user.address or None)

    coords = _resolve_coordinates(user, explicit_address)
    lat, lng = (coords if coords else (None, None))

    # 同一位志工、同一種物資只保留一筆：「我有水」再傳一次是更新，不是
    # 又多一筆數量未填的重複紀錄。
    existing = db.query(CommunityResource).filter(
        CommunityResource.owner_id == user.id,
        CommunityResource.resource_type == detected_type,
        CommunityResource.is_available == True,
    ).first()
    if existing and db.query(CommunityNeed.id).filter(
        CommunityNeed.matched_resource_id == existing.id,
        CommunityNeed.status.in_(("suggested", "matched")),
    ).first():
        existing = None
    label = RES_TYPE_ZH.get(detected_type, "物資")
    if existing:
        if quantity:
            set_quantity_fields(existing, quantity)
            existing.inventory_version = int(existing.inventory_version or 1) + 1
        if address:
            existing.address = address
        if resource_name:
            existing.name = resource_name
        if lat is not None:
            existing.lat, existing.lng = lat, lng
            existing.zone_id = resolve_zone_for_point(db, lat, lng)
        existing.last_updated = now_utc()
        verb = "已更新"
    else:
        resource = CommunityResource(
            owner_id=user.id, resource_type=detected_type,
            name=resource_name or f"{user.name}提供的{label}", address=address,
            lat=lat, lng=lng, is_available=True, zone_id=resolve_zone_for_point(db, lat, lng),
        )
        set_quantity_fields(resource, quantity)
        db.add(resource)
        verb = "登記成功"
    db.commit()

    text = (f"✅ 物資{verb}！\n\n類型：{NEED_ZH.get(detected_type, label)}\n"
            f"數量：{quantity or '未填'}\n地址：{address or '未填'}\n\n"
            "緊急模式啟動後系統會自動媒合，或管理員手動指派給您。\n"
            "傳「我的物資」查看已登記項目，傳「取消物資」可以撤回。")
    if lat is None:
        return text + "\n\n⚠️ 我們還不知道這份物資在哪裡，在您分享位置之前它不會被媒合。請點下面按鈕分享位置。", True
    return text, False


# ──────────────────────────────────────────────
# 需求：登記、查詢、取消
# ──────────────────────────────────────────────
def _handle_needs(event, db, user, text, intent) -> bool:
    from app.models.need import CommunityNeed
    from app.services.dispatch import cancel_need

    if text in CANCEL_NEED_WORDS:
        active = (db.query(CommunityNeed)
                  .filter(CommunityNeed.requester_id == user.id,
                          CommunityNeed.status.in_(["open", "suggested", "matched"])).all())
        for n in active:
            cancel_need(str(n.id), db)
        _say(event, f"已幫您取消 {len(active)} 筆進行中的需求。之後有需要再傳「需要水」等就可以。"
             if active else "您目前沒有進行中的需求。")
        return True

    if text in ("申請物資", "物資申請", "需要物資", "申請表單"):
        _open_form(event, db, user, "need")
        return True

    if intent["needs"]:
        reply, ask = submit_needs(db, user, intent["needs"], text)
        _say(event, reply, ask_location=ask)
        return True

    if intent["negated"]:
        _say(event, "了解，先不用登記 😊 之後有需要再傳「需要水」「需要食物」等就可以。")
        return True
    return False


def submit_needs(db, user, ntypes, description, *, urgent=False, address=None) -> tuple[str, bool]:
    """Create needs for this person. Returns (reply text, whether to ask for a location)."""
    from app.models.need import CommunityNeed
    from app.services.zones import resolve_zone_for_point
    existing_types = {n.need_type for n in db.query(CommunityNeed).filter(
        CommunityNeed.requester_id == user.id, CommunityNeed.status.in_(["open", "suggested", "matched"])).all()}
    coords = _resolve_coordinates(user, address)
    if address and coords and (user.lat is None or user.lng is None):
        user.lat, user.lng = coords
    if address and not user.address:
        user.address = address
    zone_id = resolve_zone_for_point(db, coords[0] if coords else None, coords[1] if coords else None)
    created, duplicates = [], []
    for ntype in ntypes:
        if ntype in existing_types:
            duplicates.append(ntype)
            continue
        base = URGENCY_BY_TYPE.get(ntype, 3)
        db.add(CommunityNeed(
            requester_id=user.id, need_type=ntype, description=description, address=address or user.address,
            lat=coords[0] if coords else None, lng=coords[1] if coords else None,
            urgency=max(base, 4) if urgent else base, zone_id=zone_id,
        ))
        created.append(ntype)
    db.commit()

    lines = []
    if created:
        lines.append("📋 已登記您的需求：" + "、".join(NEED_ZH.get(t, t) for t in created))
        lines.append("系統正在協調物資，志工確認後會盡快送達；有進展會直接通知您。")
    if duplicates:
        lines.append("您稍早已提出過：" + "、".join(NEED_ZH.get(t, t) for t in duplicates)
                     + "，志工正在處理中，不用重新提出。")
    lines.append("傳「我的需求」可以查看進度；情況緊急請傳「需要幫忙」。")
    if "first_aid" in created:
        lines.append(EMERGENCY_TIP)
    if not coords:
        lines.append(LOCATION_HINT)
    return "\n\n".join(lines), not coords


# ──────────────────────────────────────────────
# 卡片表單（點選式，不用打字）
# ──────────────────────────────────────────────
def _form_card(kind: str, data: dict) -> dict:
    from app.services import line_forms
    return line_forms.need_card(data) if kind == "need" else line_forms.resource_card(data)


def _send_form(event, kind: str, data: dict) -> None:
    from app.services.line_notify import reply_flex_message
    alt = "申請物資表單" if kind == "need" else "登記物資表單"
    reply_flex_message(event.reply_token, alt, _form_card(kind, data))


def _open_form(event, db, user, kind: str) -> None:
    """Send the link to the web form (real text boxes) with a tap-only fallback underneath."""
    from app.services import line_forms
    from app.services.form_token import form_url
    from app.services.line_notify import reply_flex_message
    conversation.start(db, user.line_uid, f"form_{kind}", "edit",
                       {"types": [], "urgent": False} if kind == "need" else {}, ns=line_forms.FORM_NS)
    reply_flex_message(event.reply_token, line_forms.LINK_TITLES[kind],
                       line_forms.link_card(kind, form_url(kind, user.line_uid)))


def _handle_form_text(event, db, user, text: str) -> bool:
    """「✏️」按鈕會彈出鍵盤並預填「補充：」「地址：」，這裡接住使用者打的那一句。"""
    from app.services import line_forms
    prefixes = {line_forms.NOTE_PREFIX: ("need", "note"), line_forms.ADDRESS_PREFIX: ("res", "address")}
    normalized = text.replace(":", "：", 1)
    for prefix, (kind, field) in prefixes.items():
        if not normalized.startswith(prefix):
            continue
        state = conversation.get(db, user.line_uid, line_forms.FORM_NS)
        if not state or state.get("flow") != f"form_{kind}":
            return False
        value = normalized[len(prefix):].strip()[:line_forms.MAX_TEXT]
        if not value:
            _say(event, "內容是空的，請在「" + prefix + "」後面接著打字。")
            return True
        form = {**state.get("data", {}), field: value}
        conversation.advance(db, user.line_uid, state, "edit", ns=line_forms.FORM_NS, **form)
        _send_form(event, kind, form)
        return True
    return False


def _handle_form_postback(event, db, user, data: dict) -> None:
    from app.services import line_forms
    ns = line_forms.FORM_NS
    op, kind = data.get("op"), data.get("f")
    state = conversation.get(db, user.line_uid, ns)

    if op == "noop":
        return
    if op == "wizard":
        conversation.start(db, user.line_uid, "volunteer_apply", "name")
        _say(event, "好的，我一題一題問您，隨時傳「取消」可以中止。\n\n請問您的姓名？")
        return
    if op == "cancel":
        conversation.clear(db, user.line_uid, ns)
        _say(event, "好的，已取消表單。")
        return
    if op == "open" and kind in ("need", "res"):
        if kind == "res" and not _is_staff(user):
            _say(event, "此功能僅限志工使用。")
            return
        conversation.start(db, user.line_uid, f"form_{kind}", "edit",
                           {"types": [], "urgent": False} if kind == "need" else {}, ns=ns)
        _send_form(event, kind, conversation.get(db, user.line_uid, ns)["data"])
        return
    if not state or state.get("flow") != f"form_{kind}":
        _say(event, "這張表單已經失效了，請重新點選單的「申請物資」或傳「登記物資」。")
        return
    if kind == "res" and not _is_staff(user):
        _say(event, "此功能僅限志工使用。")
        return

    form = dict(state.get("data", {}))
    value = data.get("v", "")

    if op == "go":
        if kind == "need":
            if not form.get("types"):
                _say(event, "請先點選至少一項需要的物資。")
                return
            people = form.get("people")
            desc = "卡片表單申請" + (f"（{people}人{'以上' if people == '4' else ''}）" if people else "")
            if form.get("note"):
                desc += f"｜補充：{form['note']}"
            conversation.clear(db, user.line_uid, ns)
            reply, ask = submit_needs(db, user, form["types"], desc, urgent=bool(form.get("urgent")))
            _say(event, reply, ask_location=ask)
        else:
            if not form.get("rtype") or not form.get("qty"):
                _say(event, "請先選擇物資種類和數量。")
                return
            conversation.clear(db, user.line_uid, ns)
            reply, ask = save_resource(db, user, form["rtype"], form["qty"], form.get("address"))
            _say(event, reply, ask_location=ask)
        return

    if kind == "need":
        if op == "type" and value in dict(line_forms.NEED_TYPES):
            types = list(form.get("types", []))
            types.remove(value) if value in types else types.append(value)
            form["types"] = types
        elif op == "people" and value in line_forms.PEOPLE:
            form["people"] = None if form.get("people") == value else value
        elif op == "urgent":
            form["urgent"] = value == "1"
    else:
        if op == "type" and value in {k for k, _l, _kw in line_forms.RES_TYPES}:
            if form.get("rtype") != value:
                form["qty"] = None
            form["rtype"] = value
        elif op == "qty" and value in line_forms.qty_options(form.get("rtype")):
            form["qty"] = value

    conversation.advance(db, user.line_uid, state, "edit", ns=ns, **form)
    _send_form(event, kind, form)


# ──────────────────────────────────────────────
# AI 助手
# ──────────────────────────────────────────────
def _handle_question(event, text) -> bool:
    if not _looks_like_question(text):
        return False
    try:
        from app.services import rag as rag_svc
        result = rag_svc.query(text)
    except Exception:
        logger.exception("[linebot] AI 助手查詢失敗")
        _say(event, "🤖 AI 助手暫時無法使用，請稍後再試。\n" + EMERGENCY_TIP)
        return True
    if result.get("has_answer"):
        sources = result.get("sources", [])
        src_line = f"\n\n📚 來源：{' | '.join(sources[:2])}" if sources else ""
        _say(event, f"🤖 AI 助手回答：\n\n{result['answer'][:1000]}{src_line}")
        return True
    _say(event, "🤖 知識庫裡目前沒有這個問題的資料，抱歉。\n" + EMERGENCY_TIP)
    return True


# ──────────────────────────────────────────────
# 文字訊息
# ──────────────────────────────────────────────
HELP_BASE = (
    "📖 可用指令：\n"
    "・「居民中心」— 家屬、紀錄與完整居民服務\n"
    "・「我很好」— 回覆今日打卡\n"
    "・「狀態」— 查看系統模式\n"
    "・「需要水／需要食物／需要藥」— 提出物資需求\n"
    "・「我的需求」— 查看求助進度；「取消需求」— 撤回\n"
    "・點選 LINE 的「＋」→「位置資訊」分享目前位置 — 更新您的座標\n"
    "・「需要幫忙」— 觸發緊急求助（會先跟您確認一次）"
)


FIXED_COMMANDS = {"我很好", "好", "OK", "ok", "沒事", "沒事了", "平安", "狀態", "status", "幫助", "help", "?", "？",
                  "我的需求", "進度", "求助進度", "登記物資", "物資登記", "登記", "我的物資",
                  "取消物資", "撤回物資", "刪除物資", "分享位置", "傳位置", "更新位置",
                  "申請物資", "物資申請", "需要物資", "申請表單", "接單", "可接任務", "找任務"}
UNWELL_WORDS = ("身體不舒服", "我不舒服", "不舒服")
FIXED_COMMANDS |= line_ops.COMMAND_WORDS | set(UNWELL_WORDS)


def _is_known_command(text: str, intent: dict) -> bool:
    return (
        text in FIXED_COMMANDS or text in CANCEL_NEED_WORDS
        or bool(intent["needs"]) or bool(line_ops.BIND_RE.match(text)) or bool(line_ops.JOIN_RE.match(text))
        or any(text.startswith(p) for p in APPLY_PREFIXES + ["新增長者", "幫長者登記", "代辦長者", "登記長者", "我有"])
    )


def _process_text(event, db, user, text) -> bool:
    """Handle one text message. Returns True when a specific reply was sent."""
    intent = parse_intent(text)

    # 進行中的多步驟對話（例如志工申請）
    state = conversation.get(db, user.line_uid)
    if state:
        if text in ("取消", "中止", "算了"):
            conversation.clear(db, user.line_uid)
            _say(event, "好的，已取消。想再申請時傳「我要當志工」就可以。")
            return True
        if intent["sos"] or _is_known_command(text, intent):
            # 求救永遠優先於問卷；使用者改傳別的指令（「幫助」「我的需求」「需要水」…）
            # 也代表他不想繼續填了，不能把這些指令當成「姓名」「電話」吞掉，
            # 不然接下來 30 分鐘他傳什麼都會被問卷劫持。
            conversation.clear(db, user.line_uid)
        elif _continue_flow(event, db, user, text, state):
            return True

    if _handle_form_text(event, db, user, text):
        return True

    if line_ops.handle_text(event, db, user, text):
        return True

    if text in CHECKIN_OK_WORDS:
        from app.models.checkin import DailyCheckin
        checkin = (db.query(DailyCheckin)
                   .filter(DailyCheckin.elderly_id == user.id, DailyCheckin.date == today_tw(),
                           DailyCheckin.status.in_(["pending", "no_response"])).first())
        if checkin:
            checkin_svc.mark_checkin(str(checkin.id), "ok", db)
        _say(event, "✅ 收到，今天也要保重喔！")
        return True

    if text in UNWELL_WORDS:
        _say(event, _report_unwell(user, db))
        return True

    if text in ("分享位置", "傳位置", "更新位置"):
        _say(event, "📍 請點下面的「分享我的位置」按鈕，志工才找得到您。\n（也可以點 LINE 對話框的「＋」→「位置資訊」）", ask_location=True)
        return True

    if text in ["狀態", "status"]:
        _say(event, f"目前模式：{'🚨 緊急模式' if _get_mode(db) == 'emergency' else '🟢 日常模式'}")
        return True

    if text in ["幫助", "help", "?", "？"]:
        parts = [HELP_BASE, "・「我的紀錄」— 需求紀錄、取消需求、家人綁定\n・「邀請家人」— 取得綁定碼，讓家人收到您的狀況通知"]
        roles = user.roles or []
        if "volunteer" in roles or "admin" in roles:
            parts.append("📦 志工指令：\n・「志工中心」— 任務、物資與位置的完整入口\n・「接單」— 挑選附近的需求\n・「我的任務」— 進行中的任務與回報\n"
                         "・「登記物資」「我的物資」「取消物資」\n・「新增長者 [姓名] [地址]」— 幫長者代辦註冊\n"
                         "・直接輸入問題 — AI 急救 / 照護知識查詢 🤖")
        else:
            parts.append("🙋 想幫忙嗎？\n・「我要當志工」— 申請成為志工")
        if "family" in roles:
            parts.append("👨‍👩‍👧 家屬指令：\n・「長輩狀況」— 查看長輩今天平安嗎\n・「綁定 123456」— 用長輩給的綁定碼綁定")
        else:
            parts.append("👨‍👩‍👧 家人想關心您？請他傳「綁定 碼」（碼由您傳「邀請家人」取得）。")
        if "admin" in roles:
            parts.append("🛠 決策者指令：\n・「決策中心」— 待處理量、風險與下一步\n"
                         "・「總覽」「待派」「待審」「求救單」\n・「後台」— 取得後台登入連結")
        _say(event, "\n\n".join(parts))
        return True

    if text in ("取消",):
        _say(event, "要取消什麼呢？\n・傳「取消需求」— 撤回您的求助\n・傳「取消物資」— 撤回您登記的物資（志工）")
        return True

    if _handle_apply(event, db, user, text):
        return True
    if _handle_volunteer_commands(event, db, user, text):
        return True

    # 求救永遠優先於一般需求：同一句「救命我沒水了」要先確認安危
    if intent["sos"]:
        from app.services.line_notify import reply_sos_confirmation
        reply_sos_confirmation(event.reply_token)
        return True

    if _handle_needs(event, db, user, text, intent):
        return True
    return _handle_question(event, text)


@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event: MessageEvent):
    text     = event.message.text.strip()
    line_uid = event.source.user_id
    db       = next(get_db())

    user = db.query(User).filter(User.line_uid == line_uid).first()
    first_contact = user is None
    if first_contact:
        user = _register_user(db, line_uid)
    elif not user.is_active:
        _say(event, "您的帳號目前已停用，如有疑問請直接聯絡社區管理員。")
        return
    placeholder_id = user.id

    handled = _process_text(event, db, user, text)

    if first_contact:
        # 第一句話是「加入 碼」時，臨時居民帳號已經被換成管理員預先建好的成員，不需要再歡迎。
        current = db.query(User).filter(User.line_uid == line_uid).first()
        if current is None or current.id != placeholder_id:
            return
        # 之前新用戶第一句話（哪怕是「救命」）只會得到歡迎詞，內容整個被吞掉。
        # 現在照常處理完，再補送歡迎與使用說明。
        if handled:
            from app.services.line_notify import send_text
            try:
                send_text(line_uid, _welcome_text(user.name))
            except Exception:
                logger.exception("[linebot] 歡迎訊息推播失敗")
        else:
            _say(event, _welcome_text(user.name), ask_location=True)
        return

    if not handled:
        _say(event, "收到您的訊息了 😊\n如需幫忙請傳「需要幫忙」，物資不夠請傳「需要水」等。\n傳「幫助」可查看可用指令。")


# ──────────────────────────────────────────────
# 加好友、位置、其他訊息類型
# ──────────────────────────────────────────────
@handler.add(FollowEvent)
def handle_follow(event: FollowEvent):
    line_uid = event.source.user_id
    db = next(get_db())
    user = db.query(User).filter(User.line_uid == line_uid).first()
    if not user:
        user = _register_user(db, line_uid)
    _say(event, _welcome_text(user.name), ask_location=user.lat is None)


@handler.add(MessageEvent, message=LocationMessageContent)
def handle_location(event: MessageEvent):
    """LINE 原生「分享位置」：點一下就拿到真實 GPS，不需要長者記得地址。"""
    line_uid = event.source.user_id
    db = next(get_db())
    user = db.query(User).filter(User.line_uid == line_uid).first()
    first_contact = user is None
    if first_contact:
        user = _register_user(db, line_uid)

    user.lat = event.message.latitude
    user.lng = event.message.longitude
    if event.message.address:
        user.address = event.message.address

    from app.models.need import CommunityNeed
    from app.models.resource import CommunityResource
    # 之前缺座標而永遠配不到的需求與物資，順手一起補上。
    needs_fixed = (db.query(CommunityNeed)
                   .filter(CommunityNeed.requester_id == user.id,
                           CommunityNeed.status.in_(["open", "suggested"]), CommunityNeed.lat.is_(None))
                   .update({"lat": user.lat, "lng": user.lng}, synchronize_session=False))
    res_fixed = (db.query(CommunityResource)
                 .filter(CommunityResource.owner_id == user.id, CommunityResource.lat.is_(None))
                 .update({"lat": user.lat, "lng": user.lng}, synchronize_session=False))
    db.commit()

    notes = []
    if needs_fixed:
        notes.append(f"已一併補上 {needs_fixed} 筆先前缺座標的求助，現在可以正常配對了。")
    if res_fixed:
        notes.append(f"已一併補上 {res_fixed} 份物資的位置，現在可以被媒合了。")
    note = ("\n\n" + "\n".join(notes)) if notes else ""
    _say(event, f"✅ 已更新您的位置{'：' + event.message.address if event.message.address else ''}\n"
                f"之後的求助跟派遣都會用這個位置計算距離。{note}")
    if first_contact:
        from app.services.line_notify import send_text
        try:
            send_text(line_uid, _welcome_text(user.name))
        except Exception:
            pass


@handler.add(MessageEvent)
def handle_other_message(event: MessageEvent):
    """圖片、貼圖、語音、影片：之前機器人完全沒有反應，長輩會以為壞了。"""
    _say(event, "目前我只看得懂文字和「位置」訊息 😊\n"
                "想求助請傳「需要水」「需要幫忙」等，傳「幫助」可查看全部指令。\n"
                + EMERGENCY_TIP)


# ──────────────────────────────────────────────
# Postback 按鈕
# ──────────────────────────────────────────────
def _own_checkin(db, user, checkin_id):
    from app.models.checkin import DailyCheckin
    if not checkin_id:
        return None
    try:
        checkin = db.query(DailyCheckin).filter(DailyCheckin.id == checkin_id).first()
    except Exception:
        return None
    return checkin if checkin and checkin.elderly_id == user.id else None


def _handle_task_button(event, db, user, action, need_id) -> None:
    from app.models.need import CommunityNeed
    from app.services import dispatch
    need = None
    if need_id:
        try:
            need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
        except Exception:
            need = None
    if need is None:
        _say(event, "找不到這筆任務，可能已被取消或刪除。請以最新的任務訊息為準。")
        return
    assignee = dispatch.assignee_user_id(need)
    is_admin = bool(user.roles) and "admin" in user.roles
    if not is_admin and assignee != str(user.id):
        # 之前任何人（包含需求者本人）都能按別人的任務卡，把單退回或標成完成。
        logger.warning("[task] 非受派者操作任務卡 user=%s need=%s action=%s", user.id, need_id, action)
        _say(event, "您不是這筆任務的受派志工，無法操作。")
        return

    if action == "task_accept":
        result = dispatch.accept_task(need_id, db, actor_id=str(user.id))
        if result.get("already_accepted"):
            _say(event, "您已經確認接單了，路上小心。完成後請按「✅ 已送達」或用「📝 回報現況」。")
        elif result.get("error"):
            _say(event, "這筆任務已經不在您手上了，請以最新的任務訊息為準。")
        else:
            _say(event, "🙋 已確認接單，謝謝您！求助的人已收到通知。路上小心，完成後請回報。")
        return

    if action == "task_delivered":
        result = dispatch.mark_task_delivered(need_id, db, actor_id=str(user.id))
        if result.get("already_fulfilled"):
            _say(event, "這筆任務已經回報完成了，不用再按 ✅")
        elif result.get("error"):
            _say(event, f"這筆任務目前狀態是「{STATUS_ZH.get(result.get('need_status'), result.get('need_status'))}」，"
                        "已經不需要回報送達。請以最新的任務訊息為準。")
        else:
            _say(event, "✅ 感謝您完成送達！已記錄在案，辛苦了 🙏")
    else:
        result = dispatch.decline_task_assignment(need_id, db, actor_id=str(user.id))
        if result.get("already_open") or result.get("error"):
            _say(event, "這筆任務已經不在您手上了，不需要再處理。")
        else:
            _say(event, "沒關係，我們會尋找其他志工。感謝您的回覆。")


@handler.add(PostbackEvent)
def handle_postback(event: PostbackEvent):
    raw      = event.postback.data or ""
    data     = dict(p.split("=", 1) for p in raw.split("&") if "=" in p)
    action   = data.get("action")
    line_uid = event.source.user_id
    db       = next(get_db())

    if action == "task_v2":
        try:
            result = TaskLineExecutionService(db).execute(raw, line_uid)
            reply_task_progress_message(
                event.reply_token,
                result.task,
                result.assignment,
                result.need,
            )
        except (TaskWorkflowError, TaskLineSecurityError) as exc:
            if exc.status_code == 403:
                message = "你不是目前受派志工，無法操作這項任務。"
            elif exc.status_code == 409:
                message = "任務狀態已更新，請使用最新的任務訊息。"
            else:
                message = "任務指令無效，請聯絡管理員。"
            reply_text(event.reply_token, message)
        return

    user = db.query(User).filter(User.line_uid == line_uid).first()
    if not user:
        _say(event, "請先傳一句話讓系統幫您註冊帳號，再使用按鈕。")
        return

    checkin_id = data.get("checkin_id")

    if action == "ok":
        if _own_checkin(db, user, checkin_id) is None:
            _say(event, "這張打卡卡片不是您的，或已失效。")
            return
        checkin_svc.mark_checkin(checkin_id, "ok", db)
        _say(event, "✅ 太好了！今天也要照顧好自己 🌟")

    elif action in ("confirm_sos", "help"):
        # 打卡卡片上的「需要幫忙」之前只改狀態、完全沒發警報；現在兩條路徑一致。
        result = _trigger_sos(user, db)
        _say(event, _sos_reply_text(result), ask_location=user.lat is None)

    elif action == "form":
        _handle_form_postback(event, db, user, data)

    elif action == "unwell":
        checkin = _own_checkin(db, user, checkin_id)
        if checkin is None:
            _say(event, "這張打卡卡片不是您的，或已失效。")
        else:
            _say(event, _report_unwell(user, db, checkin))

    elif action == "dismiss_sos":
        _say(event, "好的，沒事就好 😊")

    elif action == "confirm_safe":
        from app.models.care_relation import CareRelation
        from app.models.checkin import DailyCheckin
        try:
            target = db.query(DailyCheckin).filter(DailyCheckin.id == checkin_id).first() if checkin_id else None
        except Exception:
            target = None
        allowed = target is not None and (
            (user.roles and "admin" in user.roles)
            or db.query(CareRelation).filter(CareRelation.elderly_id == target.elderly_id,
                                             CareRelation.contact_id == user.id).first()
        )
        if not allowed:
            _say(event, "只有這位長者的照護聯絡人或管理員可以確認平安。")
            return
        checkin_svc.confirm_safe(checkin_id, str(user.id), db)
        _say(event, "✅ 感謝您的確認，已更新紀錄。")

    elif action == "claim":
        _handle_claim(event, db, user, data.get("need_id", ""))

    elif line_ops.handle_postback(event, db, user, action or "", data):
        pass

    elif action in ("task_delivered", "task_decline", "task_accept"):
        _handle_task_button(event, db, user, action, data.get("need_id", ""))


def _get_mode(db) -> str:
    from app.models.config import SystemConfig
    cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    return cfg.value if cfg else "normal"
