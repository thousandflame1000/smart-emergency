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
