from fastapi import APIRouter, Request, HTTPException
from linebot.v3 import WebhookHandler
from linebot.v3.exceptions import InvalidSignatureError
from linebot.v3.webhooks import (
    MessageEvent, PostbackEvent,
    TextMessageContent,
)

from app.config import settings
from app.database import get_db
from app.services import checkin as checkin_svc
from app.services.line_notify import reply_text
from app.models.user import User

router  = APIRouter()
handler = WebhookHandler(settings.LINE_CHANNEL_SECRET)

_DEV_MODE = settings.LINE_CHANNEL_SECRET in ("dummy_secret", "test", "dev")


# ──────────────────────────────────────────────
# Webhook 入口
# ──────────────────────────────────────────────
@router.post("/line")
async def line_webhook(request: Request):
    signature = request.headers.get("X-Line-Signature", "")
    body      = await request.body()

    try:
        handler.handle(body.decode(), signature)
    except InvalidSignatureError:
        if _DEV_MODE:
            pass   # dev 模式略過驗證
        else:
            raise HTTPException(status_code=400, detail="Invalid signature")

    return "OK"


# ── 模擬打卡（dev 測試用，正式環境移除）────────
@router.post("/line/simulate_checkin")
async def simulate_checkin(elderly_name: str):
    """
    Dev only: 手動觸發打卡，不需要真實 LINE 帳號
    POST /webhook/line/simulate_checkin?elderly_name=陳月英
    """
    if not _DEV_MODE:
        raise HTTPException(status_code=403, detail="只在開發模式下開放")

    from app.services.checkin import send_daily_checkins
    from app.database import SessionLocal
    from app.models.checkin import DailyCheckin
    from app.models.user import User as U
    from datetime import date

    db = SessionLocal()
    elderly = db.query(U).filter(
        U.name == elderly_name,
        U.roles.contains('elderly'),
    ).first()

    if not elderly:
        db.close()
        return {"error": f"找不到長者：{elderly_name}"}

    # 如果今天已有打卡記錄就回傳
    existing = db.query(DailyCheckin).filter(
        DailyCheckin.elderly_id == elderly.id,
        DailyCheckin.date == date.today()
    ).first()

    db.close()

    if existing:
        return {
            "message": f"{elderly_name} 今日已有打卡記錄",
            "checkin_id": str(existing.id),
            "status": existing.status,
            "note": "如需重新測試請先執行 seed.py"
        }

    return {
        "message": f"此長者沒有綁定 LINE，無法發送（正式環境需先綁定）",
        "elderly": elderly_name,
        "tip": "用 /api/dashboard/elderly 看目前狀態"
    }


# ──────────────────────────────────────────────
# 文字訊息
# ──────────────────────────────────────────────
@handler.add(MessageEvent, message=TextMessageContent)
def handle_text(event: MessageEvent):
    text     = event.message.text.strip()
    line_uid = event.source.user_id
    db       = next(get_db())

    user = db.query(User).filter(User.line_uid == line_uid).first()

    if not user:
        # 自動建立帳號，抓 LINE 顯示名稱
        from app.models.user import User as U
        from app.services.line_notify import get_display_name
        display_name = get_display_name(line_uid)
        new_user = U(name=display_name, roles=["elderly"], line_uid=line_uid)
        db.add(new_user)
        db.commit()
        reply_text(event.reply_token,
                   f"👋 {display_name}，歡迎加入鄰里守望！\n\n"
                   f"您已自動註冊為系統成員 🎉\n"
                   f"管理員會協助確認您的角色。\n\n"
                   f"傳「幫助」查看可用指令 😊")
        return

    if text in ["我很好", "好", "OK", "ok", "沒事"]:
        # 更新今日打卡狀態
        from app.models.checkin import DailyCheckin
        from datetime import date, datetime
        today_checkin = (
            db.query(DailyCheckin)
            .filter(
                DailyCheckin.elderly_id == user.id,
                DailyCheckin.date == date.today(),
                DailyCheckin.status == "pending",
            )
            .first()
        )
        if today_checkin:
            today_checkin.status = "ok"
            today_checkin.responded_at = datetime.now()
            db.commit()
        reply_text(event.reply_token, "✅ 收到，今天也要保重喔！")
        return

    if text in ["狀態", "status"]:
        mode = _get_mode(db)
        reply_text(event.reply_token,
                   f"目前模式：{'🚨 緊急模式' if mode == 'emergency' else '🟢 日常模式'}")
        return

    if text in ["幫助", "help", "?"]:
        is_vol = user.roles and any(r in user.roles for r in ["volunteer", "family", "admin"])
        extra = "\n・直接輸入問題 — AI 急救 / 照護知識查詢 🤖" if is_vol else ""
        reply_text(event.reply_token,
                   "📖 可用指令：\n"
                   "・「我很好」— 回覆今日打卡\n"
                   "・「狀態」— 查看系統模式\n"
                   f"・「需要幫忙」— 觸發緊急通知{extra}")
        return

    if "需要幫忙" in text or "救命" in text or "緊急" in text:
        # 更新今日打卡狀態並觸發警報
        from app.models.checkin import DailyCheckin
        from datetime import date, datetime
        today_checkin = (
            db.query(DailyCheckin)
            .filter(
                DailyCheckin.elderly_id == user.id,
                DailyCheckin.date == date.today(),
            )
            .first()
        )
        if today_checkin:
            today_checkin.status = "help_needed"
            today_checkin.responded_at = datetime.now()
            db.commit()
            # 通知家屬 / 志工
            try:
                from app.services.alert import send_alerts_for_checkin
                send_alerts_for_checkin(today_checkin.id, "help_needed", db)
            except Exception:
                pass
        reply_text(event.reply_token,
                   "🆘 已收到您的求助！\n正在通知家屬和志工，請稍候。")
        return

    # 志工 / 家屬 → 問題導向 RAG 查詢
    if user.roles and any(r in user.roles for r in ["volunteer", "family", "admin"]):
        if len(text) >= 8:  # 足夠長的問題才送 RAG
            try:
                from app.services import rag as rag_svc
                result = rag_svc.query(text)
                if result.get("has_answer"):
                    answer = result["answer"]
                    sources = result.get("sources", [])
                    src_line = f"\n\n📚 來源：{' | '.join(sources[:2])}" if sources else ""
                    reply_text(event.reply_token, f"🤖 AI 助手回答：\n\n{answer[:1000]}{src_line}")
                    return
            except Exception:
                pass

    reply_text(event.reply_token,
               "收到您的訊息了 😊\n"
               "如需幫忙請按打卡訊息的「需要幫忙」按鈕。\n"
               "傳「幫助」可查看可用指令。")


# ──────────────────────────────────────────────
# Postback 按鈕
# ──────────────────────────────────────────────
@handler.add(PostbackEvent)
def handle_postback(event: PostbackEvent):
    raw      = event.postback.data
    data     = dict(p.split("=", 1) for p in raw.split("&") if "=" in p)
    action   = data.get("action")
    line_uid = event.source.user_id
    db       = next(get_db())

    user = db.query(User).filter(User.line_uid == line_uid).first()
    if not user:
        return

    checkin_id = data.get("checkin_id")

    if action == "ok":
        if checkin_id:
            checkin_svc.mark_checkin(checkin_id, "ok", db)
        reply_text(event.reply_token, "✅ 太好了！今天也要照顧好自己 🌟")

    elif action == "help":
        if checkin_id:
            checkin_svc.mark_checkin(checkin_id, "help_needed", db)
        reply_text(event.reply_token,
                   "🆘 已通知家屬和志工，請稍候，有人會盡快聯絡您。")

    elif action == "confirm_safe":
        if checkin_id:
            checkin_svc.confirm_safe(checkin_id, str(user.id), db)
        reply_text(event.reply_token, "✅ 感謝您的確認，已更新紀錄。")

    elif action == "task_accept":
        reply_text(event.reply_token,
                   "✅ 謝謝您願意幫忙！管理員會盡快與您聯繫詳情。")

    elif action == "task_decline":
        reply_text(event.reply_token,
                   "沒關係，我們會尋找其他志工。感謝您的回覆。")


def _get_mode(db) -> str:
    from app.models.config import SystemConfig
    cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    return cfg.value if cfg else "normal"
