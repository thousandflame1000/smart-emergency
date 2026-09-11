# -*- coding: utf-8 -*-
"""
共用的 rate limiter 實例。

系統目前沒有登入驗證，任何知道網址的人都能直接呼叫 API——最高風險的
是 /api/dashboard/mode，切換緊急模式會廣播 LINE 訊息給所有真實用戶，
被亂打會直接騷擾到真人。在決賽前這麼近的時間點加完整登入系統風險
太高（牽動所有路由跟前端，一旦哪裡沒接好就會在現場打不開後台），
所以先用限流頂著：擋掉「短時間內狂打同一個 IP」的濫用情境，
不影響正常操作（人手動點按鈕不可能在一分鐘內按到超過限制次數）。

放在獨立模組是為了避免 app/main.py 和 app/routers/dashboard.py
互相 import 造成循環依賴。
"""
from slowapi import Limiter
from slowapi.util import get_remote_address

limiter = Limiter(key_func=get_remote_address, default_limits=["60/minute"])
