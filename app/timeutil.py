# -*- coding: utf-8 -*-
"""時間的單一來源。

資料庫時間戳記（created_at 的 server_default、各種 responded_at）一律是 UTC；
「今天」的日期邊界則是台灣日期（打卡排程在 Asia/Taipei 早上發出）。之前這兩件事
都直接呼叫 datetime.now() / date.today()，結果取決於伺服器所在時區：
- 在台灣時區的機器（示範用筆電）上，datetime.now() 比 UTC 的 created_at 快 8 小時，
  一建立打卡就同時觸發「1 小時」與「3 小時」未回應警報；
- 在 UTC 的伺服器（Railway）上，date.today() 每天有 8 小時（台灣 00:00–08:00）
  還停在前一天，早上 7 點按「我很好」會對不到當天的打卡。
台灣沒有日光節約時間，固定 +8 就是精確的，不需要 tzdata。
"""
from datetime import date, datetime, timedelta, timezone

TAIWAN = timezone(timedelta(hours=8))


def now_utc() -> datetime:
    """Naive UTC now — same shape as the TIMESTAMP columns in this project."""
    return datetime.now(timezone.utc).replace(tzinfo=None)


def today_tw() -> date:
    """Today's calendar date in Taiwan."""
    return datetime.now(TAIWAN).date()
