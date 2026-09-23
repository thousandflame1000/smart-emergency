"""把資料庫存的代碼翻成使用者看得懂的字。

這些對照表原本住在 `app/routers/linebot.py`，於是 service 層要用就得反過來
匯入 router——方向不對，結果 `dispatch.py` 乾脆把原始值直接內插進中文句子，
使用者會看到「需求目前狀態為「suggested」」這種訊息。放在這裡讓任何一層都能用。
"""

NEED_STATUS_ZH = {
    "open": "待媒合",
    "suggested": "系統已建議，等待管理員核准",
    "matched": "已派遣，志工正在處理",
    "fulfilled": "已完成",
    "cancelled": "已取消",
}


def need_status(value: str | None) -> str:
    """狀態的中文說法；遇到沒收錄的值就照原樣回傳，不要讓畫面變成空白。"""
    return NEED_STATUS_ZH.get(value or "", value or "未知")


# 取不到 LINE 顯示名稱時的佔位字。先前用的是「用戶_」加 LINE UID 末六碼，
# 那串東西會被當成姓名存進資料庫，然後出現在歡迎詞、後台名單、派遣卡片與
# 家屬通知裡——調度者看到「用戶_4f8a2c」根本不知道那是誰。
UNNAMED_RESIDENT = "未命名住戶"


def is_placeholder_name(name: str | None) -> bool:
    """這個名字是系統自動給的佔位字，不是本人提供的稱呼。"""
    return not name or name == UNNAMED_RESIDENT or name.startswith("用戶_")
