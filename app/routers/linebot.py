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
        vol_extra = (
            "\n\n📦 志工指令：\n"
            "・「登記物資」— 登記您可提供的物資\n"
            "・「我的物資」— 查看已登記項目\n"
            "・直接輸入問題 — AI 急救 / 照護知識查詢 🤖"
        ) if is_vol else ""
        reply_text(event.reply_token,
                   "📖 可用指令：\n"
                   "・「我很好」— 回覆今日打卡\n"
                   "・「狀態」— 查看系統模式\n"
                   f"・「需要幫忙」— 觸發緊急通知{vol_extra}")
        return

    # ── 志工物資登記 ────────────────────────────
    is_vol = user.roles and any(r in user.roles for r in ["volunteer", "family", "admin"])

    if text in ["登記物資", "物資登記", "登記"]:
        if not is_vol:
            reply_text(event.reply_token, "此功能僅限志工使用。請聯絡管理員確認您的角色。")
            return
        from app.services.line_notify import send_resource_register_menu
        try:
            send_resource_register_menu(line_uid, event.reply_token)
        except Exception:
            reply_text(event.reply_token,
                       "📦 請用以下格式登記物資：\n\n"
                       "我有 [類型] [數量] [地址]\n\n"
                       "類型可填：水/食物/藥品/工具/車/庇護所/其他\n\n"
                       "範例：\n"
                       "我有 水 20箱 台中市南區崇倫街88號\n"
                       "我有 食物 50份 自家（台中市東區）")
        return

    if text in ["我的物資"]:
        from app.models.resource import CommunityResource
        my_res = db.query(CommunityResource).filter(
            CommunityResource.owner_id == user.id
        ).order_by(CommunityResource.created_at.desc()).limit(5).all()
        if not my_res:
            reply_text(event.reply_token, "您目前沒有已登記的物資。\n傳「登記物資」開始登記。")
        else:
            RES_TYPE_ZH = {"water":"飲用水","food":"食物","first_aid":"急救用品",
                           "shelter":"庇護所","vehicle":"交通工具","tool":"工具","other":"其他"}
            lines = [f"📦 您已登記的物資（最近5項）：\n"]
            for r in my_res:
                status = "✅ 可用" if r.is_available else "❌ 已媒合"
                lines.append(f"・{RES_TYPE_ZH.get(r.resource_type,r.resource_type)} — {r.name}"
                              f"（{r.quantity or '數量未填'}）{status}")
            reply_text(event.reply_token, "\n".join(lines))
        return

    # ── 自然語言物資登記：「我有水/食物/藥/車 數量 地址」 ──
    if is_vol and text.startswith("我有"):
        rest = text[2:].strip()
        # 解析類型
        TYPE_KEYWORDS = {
            "water":     ["水", "飲水", "礦泉水", "飲用水", "桶裝水"],
            "food":      ["食物", "食品", "便當", "乾糧", "泡麵", "罐頭"],
            "first_aid": ["藥", "急救", "藥品", "醫療", "繃帶", "消毒"],
            "vehicle":   ["車", "汽車", "機車", "貨車", "接送"],
            "shelter":   ["空間", "房間", "庇護", "地方", "場地"],
            "tool":      ["工具", "電鋸", "發電機", "手電筒", "鏟子"],
        }
        detected_type = "other"
        for rtype, keywords in TYPE_KEYWORDS.items():
            if any(kw in rest for kw in keywords):
                detected_type = rtype
                break

        # 移除類型關鍵字，剩下是數量+地址
        remain = rest
        for kw_list in TYPE_KEYWORDS.values():
            for kw in kw_list:
                remain = remain.replace(kw, "", 1).strip()

        # 嘗試分割數量和地址（用空格分隔）
        parts = remain.split(None, 1)
        quantity = parts[0] if parts else None
        address  = parts[1] if len(parts) > 1 else (user.address or None)

        # 用使用者地址作 fallback
        if not address and user.address:
            address = user.address

        from app.models.resource import CommunityResource
        RES_NAME = {"water":"飲用水","food":"食物","first_aid":"急救用品",
                    "shelter":"庇護所","vehicle":"交通工具","tool":"工具","other":"物資"}
        new_res = CommunityResource(
            owner_id=user.id,
            resource_type=detected_type,
            name=f"{user.name}提供的{RES_NAME.get(detected_type,'物資')}",
            quantity=quantity,
            address=address,
            lat=user.lat,
            lng=user.lng,
            is_available=True,
        )
        db.add(new_res)
        db.commit()

        TYPE_ZH = {"water":"💧飲用水","food":"🍱食物","first_aid":"🩹急救用品",
                   "shelter":"🏠庇護所","vehicle":"🚗交通工具","tool":"🔧工具","other":"📦其他物資"}
        reply_text(event.reply_token,
                   f"✅ 物資登記成功！\n\n"
                   f"類型：{TYPE_ZH.get(detected_type, detected_type)}\n"
                   f"數量：{quantity or '未填'}\n"
                   f"地址：{address or '未填'}\n\n"
                   f"緊急模式啟動後系統會自動媒合，\n"
                   f"或管理員手動指派給您。\n\n"
                   f"傳「我的物資」可查看已登記項目。")
        return

    # ── 自然語言需求回報：「需要水/食物/藥」 ──
    NEED_KEYWORDS = {
        "water":     ["需要水", "缺水", "沒水", "要水"],
        "food":      ["需要食物", "需要食", "缺食", "沒食物", "要食物", "沒吃"],
        "first_aid": ["需要藥", "需要醫療", "受傷", "需要急救"],
        "shelter":   ["需要庇護", "無家可歸", "房子損壞", "沒地方住"],
        "vehicle":   ["需要車", "需要接送", "出不去"],
    }
    detected_need = None
    for ntype, nkws in NEED_KEYWORDS.items():
        if any(kw in text for kw in nkws):
            detected_need = ntype
            break

    if detected_need:
        from app.models.need import CommunityNeed
        existing = db.query(CommunityNeed).filter(
            CommunityNeed.requester_id == user.id,
            CommunityNeed.status == "open",
            CommunityNeed.need_type == detected_need,
        ).first()
        if not existing:
            need = CommunityNeed(
                requester_id=user.id,
                need_type=detected_need,
                description=text,
                address=user.address,
                lat=user.lat,
                lng=user.lng,
                urgency=3,
            )
            db.add(need)
            db.commit()
        NEED_ZH = {"water":"💧飲用水","food":"🍱食物","first_aid":"🩹急救用品",
                   "shelter":"🏠庇護所","vehicle":"🚗交通工具"}
        reply_text(event.reply_token,
                   f"📋 已登記您的需求：{NEED_ZH.get(detected_need, detected_need)}\n\n"
                   f"系統正在協調物資，\n"
                   f"志工確認後會盡快送達。\n\n"
                   f"如情況緊急請傳「需要幫忙」。")
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

    elif action == "task_delivered":
        need_id = data.get("need_id", "")
        if need_id:
            from app.models.need import CommunityNeed
            need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
            if need:
                need.status = "fulfilled"
                db.commit()
        reply_text(event.reply_token,
                   "✅ 感謝您完成送達！已記錄在案，辛苦了 🙏")

    elif action == "task_decline":
        need_id = data.get("need_id", "")
        if need_id:
            # 重新開放需求等待下一位志工
            from app.models.need import CommunityNeed
            need = db.query(CommunityNeed).filter(CommunityNeed.id == need_id).first()
            if need:
                need.status = "open"
                need.matched_resource_id = None
                # 把物資重新開放
                if need.matched_resource_id:
                    from app.models.resource import CommunityResource
                    res = db.query(CommunityResource).filter(
                        CommunityResource.id == need.matched_resource_id
                    ).first()
                    if res:
                        res.is_available = True
                db.commit()
        reply_text(event.reply_token,
                   "沒關係，我們會尋找其他志工。感謝您的回覆。")


def _get_mode(db) -> str:
    from app.models.config import SystemConfig
    cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    return cfg.value if cfg else "normal"
