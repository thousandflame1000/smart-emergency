"""把資料庫存的代碼翻成使用者看得懂的字。

這些對照表原本住在 `app/routers/linebot.py`，於是 service 層要用就得反過來
匯入 router——方向不對，結果 `dispatch.py` 乾脆把原始值直接內插進中文句子，
使用者會看到「需求目前狀態為「suggested」」這種訊息。放在這裡讓任何一層都能用。
"""

NEED_STATUS_ZH = {
    "open": "待媒合",
    "suggested": "待核准",
    "matched": "執行中",
    "fulfilled": "已完成",
    "cancelled": "已取消",
}

# 「接下來誰要動作」。標籤只夠短到能放進徽章，這句才是使用者真正需要知道的，
# 拿來當畫面上的 tooltip 與 LINE 的說明，省掉另外寫一份沒人看的說明文件。
NEED_STATUS_WHY = {
    "open": "還沒有配對到物資，等待系統媒合或管理員手動指派。",
    "suggested": "系統已經配好物資，等管理員核准後才會通知志工。",
    "matched": "已派給志工，志工正在處理，完成後會回報。",
    "fulfilled": "志工已回報送達，這筆需求結束。",
    "cancelled": "已取消，不會再派送。",
}


def need_status(value: str | None) -> str:
    """狀態的中文說法；遇到沒收錄的值就照原樣回傳，不要讓畫面變成空白。"""
    return NEED_STATUS_ZH.get(value or "", value or "未知")


# LINE 訊息配一個圖示比較好認，但**詞要跟網頁一樣**。先前 LINE 自己留了一份表，
# 同一筆需求在 LINE 叫「已派遣」、在後台叫「執行中」、在住戶表單又叫「已派遣」，
# 住戶打電話問調度者時兩邊講的不是同一個詞。圖示是裝飾，詞不是。
NEED_STATUS_EMOJI = {
    "open": "⏳",
    "suggested": "🔎",
    "matched": "🚚",
    "fulfilled": "✅",
    "cancelled": "",
}


def need_status_line(value: str | None) -> str:
    """給 LINE 用：圖示 + 跟網頁完全相同的那個詞。"""
    label = need_status(value)
    icon = NEED_STATUS_EMOJI.get(value or "", "")
    return f"{icon} {label}".strip()


# 後端會送出四種警報，但總覽頁的對照表只收了三種，獨漏 unwell——而 unwell 正是
# 日常最常出現的那一種。缺的那一筆會走 `labels[type] || type` 的退路，於是調度者
# 在警報列上看到的是英文字串「unwell」。這種表只要分成兩份就會再漏一次。
ALERT_TYPE_ZH = {
    "help_needed": "主動求助",
    "unwell": "身體不舒服",
    "no_response_1h": "超過 1 小時未回應",
    "no_response_3h": "超過 3 小時未回應",
}


def alert_type(value: str | None) -> str:
    return ALERT_TYPE_ZH.get(value or "", value or "未知")


# 需求／物資的品項名稱。先前 linebot.py 與 dispatch.py 各有一份：dispatch 把
# other 叫「物資」、LINE 叫「其他物資」，而 tool 只有 dispatch 收錄，住戶在表單
# 選了「🔧 工具」之後，LINE 回覆裡就會出現英文的 tool。
NEED_TYPE_ZH = {
    "water": "飲用水",
    "demo_water": "飲用水",
    "food": "食物",
    "first_aid": "急救用品",
    "shelter": "庇護所",
    "vehicle": "交通工具",
    "tool": "工具",
    "other": "其他物資",
    "sos": "緊急求助",
}

NEED_TYPE_EMOJI = {
    "water": "💧", "demo_water": "💧", "food": "🍱", "first_aid": "🩹",
    "shelter": "🏠", "vehicle": "🚗", "tool": "🔧", "other": "📦", "sos": "🆘",
}


def need_type(value: str | None) -> str:
    return NEED_TYPE_ZH.get(value or "", value or "未知")


def need_type_line(value: str | None) -> str:
    """給 LINE 用：圖示 + 跟網頁完全相同的那個詞。"""
    return f"{NEED_TYPE_EMOJI.get(value or '', '')}{need_type(value)}".strip()


# 取不到 LINE 顯示名稱時的佔位字。先前用的是「用戶_」加 LINE UID 末六碼，
# 那串東西會被當成姓名存進資料庫，然後出現在歡迎詞、後台名單、派遣卡片與
# 家屬通知裡——調度者看到「用戶_4f8a2c」根本不知道那是誰。
UNNAMED_RESIDENT = "未命名住戶"


def is_placeholder_name(name: str | None) -> bool:
    """這個名字是系統自動給的佔位字，不是本人提供的稱呼。"""
    return not name or name == UNNAMED_RESIDENT or name.startswith("用戶_")
