"""後台「系統狀態」：一頁看完正式站有沒有設定好、哪裡壞了、要怎麼修。

每一項回 {key, label, level, detail, hint}：level 是 ok / warn / error，hint 只在
不是 ok 時給，寫的是營運人員能照著做的一句話。
"""
import os
import time
from datetime import datetime, timedelta

from sqlalchemy.orm import Session

from app.config import settings
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from app.models.knowledge import KnowledgeChunk
from app.models.outbox import OutboxMessage
from app.models.resource_point import EMERGENCY_POINT_TYPES, ResourcePoint
from app.models.user import User
from app.models.webhook_event import WebhookEvent
from app.timeutil import TAIWAN, now_utc, today_tw

_line_cache: dict = {"at": 0.0, "result": None}
LINE_CACHE_SECONDS = 300


def _item(key, label, level, detail, hint=None):
    return {"key": key, "label": label, "level": level, "detail": detail, "hint": hint if level != "ok" else None}


def _line_check() -> dict:
    """真的呼叫 LINE 取官方帳號資訊；權杖失效時通知與回覆全部會失敗。成功結果快取 5 分鐘。"""
    if _line_cache["result"] and time.time() - _line_cache["at"] < LINE_CACHE_SECONDS:
        return _line_cache["result"]
    try:
        from app.services.line_notify import _get_api
        info = _get_api().get_bot_info()
        result = _item("line", "LINE 官方帳號", "ok", f"已連線：{info.display_name}（{info.basic_id}）")
        _line_cache.update(at=time.time(), result=result)
        return result
    except Exception as exc:
        status = getattr(exc, "status", None)
        reason = ("LINE 拒絕權杖（401 未授權）" if status == 401 else
                  "權杖沒有權限（403）" if status == 403 else
                  f"LINE 回應錯誤（{status}）" if status else "連不上 LINE 伺服器")
        return _item("line", "LINE 官方帳號", "error", reason,
                     "檢查 Railway 的 LINE_CHANNEL_ACCESS_TOKEN 是否正確、未過期。")


def _menu_check() -> dict:
    """LINE 上的圖文選單是不是目前這一版；舊版時居民看不到新按鈕（例如「查詢物資」）。"""
    from app.services.rich_menu import MENUS, _apis, _request
    label = "LINE 圖文選單"
    try:
        api, _ = _apis()
        live = {m.name: m for m in api.get_rich_menu_list().richmenus or []}
    except Exception:
        return _item("richmenu", label, "warn", "讀不到 LINE 上的選單", "確認上方 LINE 官方帳號是綠燈。")
    stale = []
    for name, spec in MENUS.items():
        key = lambda action: getattr(action, "data", None) or getattr(action, "uri", None)  # noqa: E731
        expected = [key(area.action) for area in _request(name).areas]
        menu = live.get(name)
        # 舊版是「代打一句話」的按鈕（沒有 data 也沒有 App 連結），也算舊版
        actual = [key(area.action) for area in (menu.areas if menu else [])]
        if sorted(map(str, actual)) != sorted(map(str, expected)):
            stale.append(name.split("-")[-1])
    return _item("richmenu", label, "ok" if not stale else "warn",
                 "已是最新版" if not stale else f"{'、'.join(stale)}選單是舊版",
                 "按下方「重裝 LINE 選單」，約 10 秒完成。")


def _coverage(db: Session) -> list[dict]:
    """應變據點有沒有落在長者附近：據點全在別的縣市時，數量再多，查詢物資也只會列出幾十公里外的地方。"""
    from statistics import median
    from app.services.nearby import nearest_shelter_km
    shelters = [(p.lat, p.lng) for p in db.query(ResourcePoint).filter(
        ResourcePoint.is_active.is_(True), ResourcePoint.lat.isnot(None),
        ResourcePoint.point_type.in_(EMERGENCY_POINT_TYPES)).all()]
    homes = [(u.lat, u.lng) for u in db.query(User).filter(
        User.role_filter("elderly"), User.is_active.is_(True), User.lat.isnot(None)).all()]
    # 內政部公告的收容所也算：長者住在服務區外時，查詢物資仍會列出這些
    distances = [km for km in (nearest_shelter_km(h[0], h[1], shelters) for h in homes) if km is not None]
    if not distances:
        return []
    km = median(distances)
    return [_item("coverage", "長者到最近應變據點", "ok" if km <= 20 else "warn", f"中位數約 {km:.1f} 公里" if km < 10 else f"中位數約 {km:.0f} 公里",
                  "應變據點離長者太遠：補上長者所在鄉鎮的避難所、消防分隊與衛生所。")]


def checks(db: Session) -> list[dict]:
    from app.demo_auth import auth_mode
    from app.scheduler import scheduler_running
    production = settings.APP_ENV == "production"
    out = []

    commit = os.getenv("RAILWAY_GIT_COMMIT_SHA", "")[:7] or "本機"
    out.append(_item("version", "部署版本", "ok", commit))

    running = scheduler_running()
    out.append(_item("scheduler", "排程（打卡、未回應、自動派遣）", "ok" if running else ("error" if production else "warn"),
                     "執行中" if running else "未執行", "重新部署服務；排程停止時不會發打卡也不會升級通知。"))

    # 預設開放不用登入（使用者決定）；要上鎖再設 ADMIN_LINE_LOGIN=true 或 DEMO_PASSWORD
    mode = auth_mode()
    auth_detail = {"open": "開放，不用登入", "password": "展演密碼保護", "line-admin": "LINE 管理員登入"}[mode]
    out.append(_item("auth", "後台登入", "ok", auth_detail))

    line = _line_check()
    out.append(line)
    if line["level"] == "ok":
        out.append(_menu_check())

    admins = db.query(User).filter(User.role_filter("admin"), User.line_uid.isnot(None), User.is_active.is_(True)).count()
    out.append(_item("admins", "綁定 LINE 的管理員", "ok" if admins else "error", f"{admins} 位",
                     "至少一位管理員要綁定 LINE，否則求救與 3 小時未回應的升級通知沒有人收到。"))

    ai_on = settings.EXTERNAL_AI_ENABLED and bool(settings.GEMINI_API_KEY)
    out.append(_item("ai", "AI 問答", "ok" if ai_on else "warn",
                     "Gemini 產生回答" if ai_on else "未啟用：改用知識庫原文摘錄",
                     "在 Railway 設 EXTERNAL_AI_ENABLED=true 與 GEMINI_API_KEY。"))

    total = db.query(KnowledgeChunk).count()
    embedded = db.query(KnowledgeChunk).filter(KnowledgeChunk.embedding.isnot(None)).count()
    kb_level = "error" if total == 0 else ("warn" if ai_on and embedded < total else "ok")
    out.append(_item("kb", "知識庫", kb_level, f"{total} 段，其中 {embedded} 段 AI 可檢索",
                     "到「知識庫」按「補齊預設文件」。" if total == 0 else "AI 啟用後到「知識庫」按「補齊預設文件」補上檢索資料。"))

    points = db.query(ResourcePoint).filter(ResourcePoint.is_active.is_(True), ResourcePoint.lat.isnot(None))
    located = points.count()
    emergency = points.filter(ResourcePoint.point_type.in_(EMERGENCY_POINT_TYPES)).count()
    out.append(_item("points", "資源點（查詢物資、緊急圖層）", "ok" if emergency else "warn",
                     f"{located} 個有座標，其中應變據點 {emergency} 個",
                     "在事件處置工作區新增設施，或匯入縣市避難所與消防、衛生所資料。"))
    out.extend(_coverage(db))

    since = now_utc() - timedelta(hours=24)
    failed = (db.query(OutboxMessage).filter(OutboxMessage.status.in_(("FAILED", "DEAD")),
                                             OutboxMessage.created_at >= since))
    failed_count = failed.count()
    last = failed.order_by(OutboxMessage.created_at.desc()).first()
    out.append(_item("outbox", "通知送達（24 小時）", "ok" if failed_count == 0 else "error",
                     "全部送出" if failed_count == 0 else f"{failed_count} 則失敗：{(last.last_error or '')[:100]}",
                     "多半是 LINE 權杖失效或對方封鎖官方帳號；先看上方 LINE 狀態。"))

    webhook_failed = (db.query(WebhookEvent).filter(WebhookEvent.status.in_(("FAILED", "DEAD")),
                                                    WebhookEvent.received_at >= since).count())
    out.append(_item("webhook", "LINE 訊息處理（24 小時）", "ok" if webhook_failed == 0 else "error",
                     "全部處理完成" if webhook_failed == 0 else f"{webhook_failed} 則處理失敗",
                     "查看 Railway 日誌中的錯誤；失敗的訊息會自動重試。"))

    cfg = {row.key: row.value for row in db.query(SystemConfig).all()}
    hour, minute = int(cfg.get("checkin_hour", "8")), int(cfg.get("checkin_minute", "0"))
    now_tw = datetime.now(TAIWAN)
    due = now_tw.replace(hour=hour, minute=minute, second=0, microsecond=0) + timedelta(minutes=30)
    sent_today = (db.query(DailyCheckin).filter(DailyCheckin.date == today_tw(),
                                                DailyCheckin.prompt_sent_at.isnot(None)).count())
    elders = db.query(User).filter(User.role_filter("elderly"), User.line_uid.isnot(None), User.is_active.is_(True)).count()
    late = now_tw >= due and elders and not sent_today
    out.append(_item("checkin", f"今日打卡（{hour:02d}:{minute:02d} 發送）", "warn" if late else "ok",
                     f"已發送 {sent_today} 則，綁定 LINE 的長者 {elders} 位",
                     "排程可能沒跑；到「人員管理」按上方「立即發送打卡」。"))

    covered = {row[0] for row in db.query(CareRelation.elderly_id).filter(CareRelation.is_active.is_(True)).all()}
    elder_ids = [row[0] for row in db.query(User.id).filter(User.role_filter("elderly"), User.is_active.is_(True)).all()]
    uncovered = sum(1 for uid in elder_ids if uid not in covered)
    out.append(_item("contacts", "照護聯絡人", "ok" if not uncovered else "warn",
                     "每位長者都有聯絡人" if not uncovered else f"{uncovered} 位長者沒有聯絡人",
                     "到「照護關係」補上家屬或志工；沒有聯絡人時只能靠管理員收到升級通知。"))

    mode_value = cfg.get("mode", "normal")
    out.append(_item("mode", "目前模式", "ok", "緊急模式" if mode_value == "emergency" else "日常模式"))
    return out


def summary(items: list[dict]) -> dict:
    counts = {level: sum(1 for i in items if i["level"] == level) for level in ("ok", "warn", "error")}
    return {"items": items, **counts, "checked_at": now_utc().isoformat() + "Z"}

