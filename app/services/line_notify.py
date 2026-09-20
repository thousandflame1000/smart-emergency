import json
from linebot.v3.messaging import (
    ApiClient, Configuration, MessagingApi,
    PushMessageRequest, ReplyMessageRequest,
    FlexContainer, FlexMessage, TextMessage,
)
from app.config import settings

def _flex(alt_text: str, contents) -> FlexMessage:
    """Build a Flex message. A plain dict must go through FlexContainer.from_dict: handed to
    FlexMessage directly, the SDK keeps only the top-level type and serializes an empty bubble."""
    if isinstance(contents, dict):
        contents = FlexContainer.from_dict(contents)
    return FlexMessage(alt_text=alt_text, contents=contents)


_line_config = Configuration(access_token=settings.LINE_CHANNEL_ACCESS_TOKEN)


LINE_TIMEOUT = (3.0, 8.0)  # (connect, read) seconds
_api: MessagingApi | None = None


def _get_api() -> MessagingApi:
    """One shared client. Building a new one per call threw away the keep-alive connection, so
    every push paid a fresh TLS handshake; and the SDK's default is no timeout at all, so a
    stuck LINE response would hang the request (and the whole worker) forever."""
    global _api
    if _api is None:
        client = ApiClient(_line_config)
        send = client.rest_client.request

        def request(*args, _request_timeout=None, **kwargs):
            return send(*args, _request_timeout=_request_timeout or LINE_TIMEOUT, **kwargs)

        client.rest_client.request = request
        _api = MessagingApi(client)
    return _api


def push_flex_message(line_uid: str, alt_text: str, contents: dict) -> None:
    _get_api().push_message(PushMessageRequest(
        to=line_uid,
        messages=[_flex(alt_text, contents)],
    ))


def reply_flex_message(reply_token: str, alt_text: str, contents: dict) -> None:
    _get_api().reply_message(ReplyMessageRequest(
        reply_token=reply_token,
        messages=[_flex(alt_text, contents)],
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
        messages=[_flex("今日關懷打卡", flex)],
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
        messages=[_flex(body_text, flex)],
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
    report_buttons = []
    if need_id:
        from app.services.form_token import form_url
        report_buttons.append({
            "type": "button", "style": "primary", "color": "#e67e22", "height": "sm",
            "action": {"type": "postback", "label": "🙋 確認接單，我會出發",
                       "data": f"action=task_accept&need_id={need_id}"},
        })
        report_buttons.append({
            "type": "button", "style": "secondary", "height": "sm",
            "action": {"type": "uri", "label": "📝 回報現況（可打字說明）",
                       "uri": form_url("report", line_uid, need_id)},
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
            "contents": footer_contents + report_buttons + [{
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
        messages=[_flex("社區支援任務", flex)],
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


def reply_text_with_location_prompt(reply_token: str, text: str) -> None:
    """Text reply plus a one-tap "share my location" quick-reply button.

    The single most common reason a request could never be matched was that the
    person's coordinates were unknown. LINE lets the chat itself ask for a location
    with one tap, so ask right where the problem shows up instead of expecting the
    user to remember a menu path."""
    from linebot.v3.messaging import LocationAction, QuickReply, QuickReplyItem
    message = TextMessage(
        text=text,
        quick_reply=QuickReply(items=[QuickReplyItem(action=LocationAction(label="📍 分享我的位置"))]),
    )
    _get_api().reply_message(ReplyMessageRequest(reply_token=reply_token, messages=[message]))


def send_text_with_location_prompt(line_uid: str, text: str) -> None:
    """Push variant of the location quick-reply, for confirmations sent after a web form."""
    from linebot.v3.messaging import LocationAction, QuickReply, QuickReplyItem
    message = TextMessage(
        text=text,
        quick_reply=QuickReply(items=[QuickReplyItem(action=LocationAction(label="📍 分享我的位置"))]),
    )
    _get_api().push_message(PushMessageRequest(to=line_uid, messages=[message]))
