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
from fastapi import Request
from slowapi import Limiter
from slowapi.util import get_remote_address


def _client_ip_key(request: Request) -> str:
    """
    slowapi 預設的 get_remote_address() 是讀 request.client.host——
    也就是跟 ASGI server 直接建立 TCP 連線的那一端。這在本機測試時
    沒問題（直接連線，client.host 就是真實來源），但部署到 Railway
    後實測發現：Railway 的 edge 網路是透過內部 CGNAT 位址
    （100.64.0.0/10 網段）把請求轉發給 app，而且**每一個請求轉發用
    的內部位址都不一樣**（100.64.0.4、.5、.6、.7...逐次遞增）。這代表
    直接用 request.client.host 當限流的 key，會讓限流器把每一個請求
    都當成不同來源，計數永遠不會累積，限流形同虛設——這是真的用
    診斷端點在正式環境撈出來的實際觀察，不是理論推測。

    真正穩定的來源 IP 在 Railway 自己加上的 X-Real-IP header 裡
    （X-Forwarded-For 的第一段也是同一個值）。因為這個 app 只能透過
    Railway 的 edge 存取、沒有其他直接對外的路徑，這個 header 是
    Railway 的 edge 依照它自己觀察到的真實來源設定的，不是使用者端
    可以偽造後直接繞過的欄位，可以信任。

    本機開發（沒有這些 header）會自然 fallback 回
    get_remote_address()，行為跟原本一樣。
    """
    real_ip = request.headers.get("x-real-ip")
    if real_ip:
        return real_ip
    forwarded_for = request.headers.get("x-forwarded-for")
    if forwarded_for:
        return forwarded_for.split(",")[0].strip()
    return get_remote_address(request)


limiter = Limiter(key_func=_client_ip_key, default_limits=["60/minute"])
