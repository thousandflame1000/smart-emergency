import json
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    PushMessageRequest, ReplyMessageRequest,
    FlexMessage, TextMessage,
)
from app.config import settings

_line_config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)


def _get_api() -> MessagingApi:
    return MessagingApi(ApiClient(_line_config))


def push_flex_message(line_uid: str, alt_text: str, contents: dict) -> None:
    _get_api().push_message(PushMessageRequest(
        to=line_uid,
        messages=[FlexMessage(alt_text=alt_text, contents=contents)],
    ))


def reply_flex_message(reply_token: str, alt_text: str, contents: dict) -> None:
    _get_api().reply_message(ReplyMessageRequest(
        reply_token=reply_token,
        messages=[FlexMessage(alt_text=alt_text, contents=contents)],
    ))


# ──────────────────────────────────────────────
# 緊急求助二次確認
# ──────────────────────────────────────────────
# 「需要幫忙」「救命」「緊急」這幾個詞在自由文字比對下太寬鬆——日常
# 聊天講到「這件事很緊急」也會誤觸最高等級警報＋自動建立需求。改成
# 先跳這張卡二次確認，真的按下「對」才觸發，按鈕本身就帶著確認結果
# （postback），不需要額外的對話狀態追蹤。
def reply_sos_confirmation(reply_token: str) -> None:
    flex = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#DC2626",
            "contents": [
                {"type": "text", "text": "鄰里守望 · 緊急求助確認", "color": "#ffffff",
                 "size": "sm", "weight": "bold"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "您是說需要立即救援嗎？", "size": "xl",
                 "weight": "bold", "wrap": True},
                {"type": "text", "text": "如果只是聊天講到這幾個字，請按「沒事」，不會通知任何人。",
                 "size": "sm", "color": "#555555", "wrap": True},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#DC2626",
                    "action": {
                        "type": "postback",
                        "label": "🆘 對，我需要立即救援",
                        "data": "action=confirm_sos",
                        "displayText": "對，我需要立即救援",
                    },
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "action": {
                        "type": "postback",
                        "label": "沒事，我在聊別的",
                        "data": "action=dismiss_sos",
                        "displayText": "沒事，我在聊別的",
                    },
                },
            ],
        },
    }
    reply_flex_message(reply_token, "緊急求助確認", flex)


# ──────────────────────────────────────────────
# 打卡訊息
# ──────────────────────────────────────────────
def send_checkin_message(line_uid: str, checkin_id: str) -> None:
    flex = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#27ACB2",
            "contents": [
                {"type": "text", "text": "鄰里守望", "color": "#ffffff",
                 "size": "sm", "weight": "bold"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "md",
            "contents": [
                {"type": "text", "text": "早安 🌞", "size": "xxl", "weight": "bold"},
                {"type": "text", "text": "今天身體還好嗎？", "size": "lg",
                 "color": "#555555", "wrap": True},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "horizontal",
            "spacing": "sm",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#27ACB2",
                    "action": {
                        "type": "postback",
                        "label": "✅ 我很好",
                        "data": f"action=ok&checkin_id={checkin_id}",
                        "displayText": "我今天很好！",
                    },
                },
                {
                    "type": "button",
                    "style": "secondary",
                    "action": {
                        "type": "postback",
                        "label": "🆘 需要幫忙",
                        "data": f"action=help&checkin_id={checkin_id}",
                        "displayText": "我需要幫忙",
                    },
                },
            ],
        },
    }
    _get_api().push_message(PushMessageRequest(
        to=line_uid,
        messages=[FlexMessage(alt_text="今日關懷打卡", contents=flex)],
    ))


# ──────────────────────────────────────────────
# 警報通知（家屬 / 志工）
# ──────────────────────────────────────────────
def send_alert_message(
    line_uid: str,
    elderly_name: str,
    alert_type: str,
    checkin_id: str,
) -> None:
    messages = {
        "no_response_1h": (
            f"⚠️ 關懷提醒",
            f"{elderly_name} 今天打卡超過 1 小時未回應，請確認是否安好。",
            "#FF6B35",
        ),
        "no_response_3h": (
            f"🚨 緊急關懷",
            f"{elderly_name} 已超過 3 小時未回應！請立即確認或前往探視。",
            "#D32F2F",
        ),
        "help_needed": (
            f"🆘 需要幫忙",
            f"{elderly_name} 按下了「需要幫忙」，請盡快聯繫。",
            "#FF6B35",
        ),
    }

    title, body_text, color = messages.get(
        alert_type, ("📢 通知", f"請確認 {elderly_name} 的狀況", "#27ACB2")
    )

    flex = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": color,
            "contents": [
                {"type": "text", "text": title, "color": "#ffffff",
                 "size": "md", "weight": "bold"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {"type": "text", "text": body_text, "wrap": True, "size": "md"},
            ],
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "contents": [
                {
                    "type": "button",
                    "style": "primary",
                    "color": "#27ACB2",
                    "action": {
                        "type": "postback",
                        "label": "✅ 已確認安全",
                        "data": f"action=confirm_safe&checkin_id={checkin_id}",
                        "displayText": "已確認長者安全",
                    },
                },
            ],
        },
    }
    _get_api().push_message(PushMessageRequest(
        to=line_uid,
        messages=[FlexMessage(alt_text=body_text, contents=flex)],
    ))


# ──────────────────────────────────────────────
# 派工通知（緊急模式）
# ──────────────────────────────────────────────
def send_task_message(
    line_uid: str,
    need_description: str,
    address: str,
    resource_name: str,
    need_id: str = "",
    distance_km: float | None = None,
    dest_lat: float | None = None,
    dest_lng: float | None = None,
) -> None:
    # 任務卡之前只有地址文字，志工接不接單之前完全不知道要跑多遠、
    # 也沒有地圖連結——只能自己另外查。距離跟地圖連結都是既有資料
    # （派遣當下 need/resource 都有座標），順手帶進卡片，不用志工
    # 自己再查一次。
    body_contents = [
        {"type": "text", "text": f"需求：{need_description}", "wrap": True},
        {"type": "text", "text": f"地點：{address}", "wrap": True},
    ]
    if distance_km is not None:
        body_contents.append({"type": "text", "text": f"距離約 {distance_km:.1f} 公里",
                              "size": "sm", "color": "#888888"})
    body_contents.append({"type": "text", "text": f"您可提供：{resource_name}",
                          "wrap": True, "color": "#27ACB2"})
    footer_contents = []
    if dest_lat is not None and dest_lng is not None:
        footer_contents.append({
            "type": "button",
            "style": "link",
            "height": "sm",
            "action": {
                "type": "uri",
                "label": "🗺 開啟地圖導航",
                "uri": f"https://www.google.com/maps/dir/?api=1&destination={dest_lat},{dest_lng}",
            },
        })
    flex = {
        "type": "bubble",
        "header": {
            "type": "box",
            "layout": "vertical",
            "backgroundColor": "#FF6B35",
            "contents": [
                {"type": "text", "text": "🚛 社區支援任務", "color": "#ffffff",
                 "size": "md", "weight": "bold"},
            ],
        },
        "body": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": body_contents,
        },
        "footer": {
            "type": "box",
            "layout": "vertical",
            "spacing": "sm",
            "contents": footer_contents + [{
                "type": "box",
                "layout": "horizontal",
                "spacing": "sm",
                "contents": [
                    {
                        "type": "button",
                        "style": "primary",
                        "color": "#27ACB2",
                        "action": {
                            "type": "postback",
                            "label": "✅ 已送達",
                            "data": f"action=task_delivered&need_id={need_id}",
                            "displayText": "已完成送達！",
                        },
                    },
                    {
                        "type": "button",
                        "style": "secondary",
                        "action": {
                            "type": "postback",
                            "label": "❌ 無法前往",
                            "data": f"action=task_decline&need_id={need_id}",
                        },
                    },
                ],
            }],
        },
    }
    _get_api().push_message(PushMessageRequest(
        to=line_uid,
        messages=[FlexMessage(alt_text="社區支援任務", contents=flex)],
    ))


# ──────────────────────────────────────────────
# 取得用戶 LINE 顯示名稱
# ──────────────────────────────────────────────
def get_display_name(line_uid: str) -> str:
    try:
        profile = _get_api().get_profile(line_uid)
        return profile.display_name
    except Exception:
        return f"用戶_{line_uid[-6:]}"


# ──────────────────────────────────────────────
# 純文字訊息（fallback）
# ──────────────────────────────────────────────
def send_text(line_uid: str, text: str) -> None:
    _get_api().push_message(PushMessageRequest(
        to=line_uid,
        messages=[TextMessage(text=text)],
    ))


def reply_text(reply_token: str, text: str) -> None:
    _get_api().reply_message(ReplyMessageRequest(
        reply_token=reply_token,
        messages=[TextMessage(text=text)],
    ))


# ──────────────────────────────────────────────
# 志工物資登記選單
# ──────────────────────────────────────────────
def send_resource_register_menu(line_uid: str, reply_token: str) -> None:
    """發送物資類型選單給志工，讓他快速登記"""
    flex = {
        "type": "bubble",
        "header": {
            "type": "box", "layout": "vertical",
            "backgroundColor": "#27ACB2",
            "contents": [{"type": "text", "text": "📦 登記可提供物資",
                          "color": "#ffffff", "size": "md", "weight": "bold"}],
        },
        "body": {
            "type": "box", "layout": "vertical", "spacing": "sm",
            "contents": [
                {"type": "text", "text": "請選擇物資類型，或直接輸入：",
                 "size": "sm", "color": "#555555", "wrap": True},
                {"type": "text",
                 "text": "我有 [類型] [數量] [地址]",
                 "size": "sm", "color": "#27ACB2", "weight": "bold"},
                {"type": "text",
                 "text": "例：我有 水 20箱 台中市南區",
                 "size": "xs", "color": "#888888"},
            ],
        },
        "footer": {
            "type": "box", "layout": "vertical", "spacing": "xs",
            "contents": [
                {
                    "type": "box", "layout": "horizontal", "spacing": "xs",
                    "contents": [
                        {"type": "button", "style": "primary", "color": "#27ACB2",
                         "flex": 1, "height": "sm",
                         "action": {"type": "message", "label": "💧 飲用水",
                                    "text": "我有 水"}},
                        {"type": "button", "style": "primary", "color": "#27ACB2",
                         "flex": 1, "height": "sm",
                         "action": {"type": "message", "label": "🍱 食物",
                                    "text": "我有 食物"}},
                    ],
                },
                {
                    "type": "box", "layout": "horizontal", "spacing": "xs",
                    "contents": [
                        {"type": "button", "style": "primary", "color": "#FF6B35",
                         "flex": 1, "height": "sm",
                         "action": {"type": "message", "label": "🩹 急救用品",
                                    "text": "我有 藥品"}},
                        {"type": "button", "style": "primary", "color": "#FF6B35",
                         "flex": 1, "height": "sm",
                         "action": {"type": "message", "label": "🚗 交通工具",
                                    "text": "我有 車"}},
                    ],
                },
                {
                    "type": "box", "layout": "horizontal", "spacing": "xs",
                    "contents": [
                        {"type": "button", "style": "secondary",
                         "flex": 1, "height": "sm",
                         "action": {"type": "message", "label": "🔧 工具",
                                    "text": "我有 工具"}},
                        {"type": "button", "style": "secondary",
                         "flex": 1, "height": "sm",
                         "action": {"type": "message", "label": "🏠 庇護空間",
                                    "text": "我有 空間"}},
                    ],
                },
            ],
        },
    }
    _get_api().reply_message(ReplyMessageRequest(
        reply_token=reply_token,
        messages=[FlexMessage(alt_text="登記可提供物資", contents=flex)],
    ))
