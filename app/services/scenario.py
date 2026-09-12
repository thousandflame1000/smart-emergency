# -*- coding: utf-8 -*-
"""
決賽現場展演用：颱風夜情境模擬引擎
========================================
現有的「王秀霞脆弱度分數決定派遣優先權」這個核心賣點，之前只能靠
管理員手動一步步在後台點按鈕做出來——評審看到的是「操作員在操作
系統」，不是「系統自己在應對一場正在發生的災害」。這支服務把同一件
事包成一個有時間感、會自己往前走的劇本，讓現場可以按「開始」之後
系統自己演進，管理員只在最後「確認派遣」這個關卡介入（刻意保留這
一關，呼應計畫書「所有自動建議仍須管理員確認」的設計）。

刻意的設計決定
----------------
1. 情境模式切換緊急模式只改資料庫欄位，**不會**觸發 app/routers/
   dashboard.py set_mode() 那個會廣播 LINE 訊息給所有真人的邏輯——
   決賽前這支腳本會被反覆重跑測試，不能每次測試都騷擾到真的綁定
   LINE 的使用者（例如已重新分類為 admin 的真實測試帳號）。正式
   要廣播緊急模式，管理員仍然要在儀表板上另外按「切換緊急模式」。
2. 情境會呼叫「真正在生產環境運作」的同一個 dispatch.auto_dispatch()，
   不是另外寫一套簡化版假邏輯——賣點是「這是系統真的在跑」，不是
   一個看起來像但其實是演的動畫。
3. 為了讓「資源只有一份、王秀霞該贏」這個示範在任何時候重跑都
   deterministic（不會被當下資料庫裡剛好還開著的其他真實/示範需求
   干擾到資源池），情境自己建立的資源/需求用一個系統既有下拉選單
   不會出現、TYPE_AFFINITY 矩陣裡也没有的物資類型
   （`SCENARIO_NEED_TYPE`），跟系統裡任何其他資料完全不會互相匹配，
   確保「唯一一份物資」在情境範圍內是真的唯一。
4. 王秀霞優先使用 seed_rich_demo.py 建立的真實主角（如果存在），
   讓分數是「平時資料算出來的」而不是硬編；只有在完全找不到她時
   （例如全新環境還沒 seed）才臨時建立一個帶同樣關懷史的替身，並
   標記起來讓 reset() 之後能精準清掉，不影響任何既有資料。
"""
import json
from datetime import date, datetime, timedelta

from sqlalchemy.orm import Session

from app.models.user import User
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.config import SystemConfig
from app.services import dispatch

SCENARIO_TAG = "[TYPHOON_SIM]"
# 刻意不用 "water"——見上方設計決定 3，確保情境的資源池不會被系統
# 裡其他既有 water/food 需求或物資污染，示範結果才會每次都一樣。
SCENARIO_NEED_TYPE = "demo_water"


# ──────────────────────────────────────────────────────────
# SystemConfig 存取小工具
# ──────────────────────────────────────────────────────────
def _cfg_get(db: Session, key: str, default: str) -> str:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    return row.value if row else default


def _cfg_set(db: Session, key: str, value: str) -> None:
    row = db.query(SystemConfig).filter(SystemConfig.key == key).first()
    if row:
        row.value = value
    else:
        db.add(SystemConfig(key=key, value=value))


def _load(db: Session):
    step = int(_cfg_get(db, "scenario_step", "-1"))
    log = json.loads(_cfg_get(db, "scenario_log", "[]"))
    entities = json.loads(_cfg_get(db, "scenario_entities", "{}"))
    return step, log, entities


def _save(db: Session, step: int, log: list, entities: dict) -> None:
    _cfg_set(db, "scenario_step", str(step))
    _cfg_set(db, "scenario_log", json.dumps(log, ensure_ascii=False))
    _cfg_set(db, "scenario_entities", json.dumps(entities, ensure_ascii=False))
    db.commit()


# ──────────────────────────────────────────────────────────
# 找/建 情境角色
# ──────────────────────────────────────────────────────────
def _get_or_create_protagonist(db: Session):
    """優先用 seed_rich_demo.py 建立的真主角，找不到才臨時建一個替身。"""
    p = db.query(User).filter(User.name == "王秀霞").first()
    if p:
        return p, False

    p = User(
        name="王秀霞", roles=["elderly"],
        address=f"情境模擬地址（{SCENARIO_TAG}）",
        lat=24.151, lng=120.681, is_active=True,
    )
    db.add(p)
    db.commit()

    today = date.today()
    for days_ago in range(1, 6):
        db.add(DailyCheckin(
            elderly_id=p.id, date=today - timedelta(days=days_ago),
            status="no_response",
        ))
    db.commit()
    db.add(Alert(elderly_id=p.id, alert_type="no_response_3h",
                 notified_users=[], status="sent"))
    db.commit()
    return p, True


def _get_or_create_other_elder(db: Session):
    """挑一位脆弱度最低的既有長者當對照組，襯托王秀霞是「真的比較急」。"""
    candidates = (
        db.query(User)
        .filter(User.role_filter("elderly"), User.name != "王秀霞",
                User.is_active == True)
        .limit(30)
        .all()
    )
    best, best_vuln = None, None
    for c in candidates:
        v = dispatch._vulnerability_pts(c.id, db)
        if best_vuln is None or v < best_vuln:
            best, best_vuln = c, v
    if best is not None:
        return best, False

    other = User(
        name="社區長者（情境模擬）", roles=["elderly"],
        address=f"情境模擬地址（{SCENARIO_TAG}）",
        lat=24.150, lng=120.680, is_active=True,
    )
    db.add(other)
    db.commit()
    return other, True


# ──────────────────────────────────────────────────────────
# 劇本步驟
# ──────────────────────────────────────────────────────────
def _step_broadcast(db: Session, entities: dict):
    _cfg_set(db, "mode", "emergency")
    return "氣象局發布颱風警報，系統切換為「緊急模式」。", {}


def _step_report_other(db: Session, entities: dict):
    other, created = _get_or_create_other_elder(db)
    need = CommunityNeed(
        requester_id=other.id, need_type=SCENARIO_NEED_TYPE,
        description=f"{SCENARIO_TAG} 情境模擬：一般通報，需要飲用水",
        address=other.address, lat=other.lat or 24.150, lng=other.lng or 120.680,
        urgency=3, status="open",
    )
    db.add(need)
    db.commit()
    add = {"need_other": str(need.id), "other_elder": str(other.id)}
    if created:
        add["other_elder_created"] = "1"
    return f"「{other.name}」通報需要飲用水（緊急度 3）。", add


def _step_report_protagonist(db: Session, entities: dict):
    protagonist, created = _get_or_create_protagonist(db)
    need = CommunityNeed(
        requester_id=protagonist.id, need_type=SCENARIO_NEED_TYPE,
        description=f"{SCENARIO_TAG} 情境模擬：王秀霞通報，需要飲用水",
        address=protagonist.address, lat=protagonist.lat or 24.151,
        lng=protagonist.lng or 120.681, urgency=3, status="open",
    )
    db.add(need)
    db.commit()
    add = {"need_protagonist": str(need.id), "protagonist": str(protagonist.id)}
    if created:
        add["protagonist_created"] = "1"
    return f"「{protagonist.name}」稍晚也通報需要飲用水（緊急度同為 3）。", add


def _step_scarce_resource(db: Session, entities: dict):
    vol = User(
        name="颱風夜支援志工（情境模擬）", roles=["volunteer"],
        address=f"情境模擬地址（{SCENARIO_TAG}）",
        lat=24.150, lng=120.680, is_active=True,
    )
    db.add(vol)
    db.commit()
    res = CommunityResource(
        owner_id=vol.id, resource_type=SCENARIO_NEED_TYPE,
        name="颱風夜僅存的飲用水", quantity="1箱",
        lat=24.150, lng=120.680, is_available=True, note=SCENARIO_TAG,
    )
    db.add(res)
    db.commit()
    return "現場目前只湊到 1 份可調度的飲用水——資源稀缺。", {
        "volunteer": str(vol.id), "resource": str(res.id),
    }


def _step_auto_dispatch(db: Session, entities: dict):
    result = dispatch.auto_dispatch()

    need_p = entities.get("need_protagonist")
    need_o = entities.get("need_other")
    n_p = db.query(CommunityNeed).filter(CommunityNeed.id == need_p).first() if need_p else None
    n_o = db.query(CommunityNeed).filter(CommunityNeed.id == need_o).first() if need_o else None

    lines = ["系統執行自動媒合演算法（依緊急度 → 脆弱度分數排序處理需求）。"]
    if n_p is not None:
        tag = "✅ 獲得建議媒合" if n_p.status == "suggested" else f"（狀態：{n_p.status}）"
        lines.append(f"王秀霞的需求 {tag}")
    if n_o is not None:
        tag = "⏳ 資源不足，暫無媒合" if n_o.status == "open" else f"（狀態：{n_o.status}）"
        lines.append(f"另一筆一般通報 {tag}")
    return "\n".join(lines), {"dispatch_result": result}


def _step_wait_confirm(db: Session, entities: dict):
    return ("情境演進結束——請至「緊急調度中心」查看待確認的媒合建議，"
            "系統不會自動通知志工，需管理員手動確認派遣。"), {}


STEPS = [
    {"key": "broadcast", "title": "🌀 颱風警報廣播",
     "desc": "氣象局發布警報，系統切換為緊急模式（示範用，不廣播真實 LINE 訊息）。",
     "run": _step_broadcast},
    {"key": "report_other", "title": "📞 第一筆通報",
     "desc": "一般社區長者通報需要飲用水。",
     "run": _step_report_other},
    {"key": "report_protagonist", "title": "📞 王秀霞通報",
     "desc": "近期打卡異常、警報未解決的王秀霞稍晚也通報同樣需求。",
     "run": _step_report_protagonist},
    {"key": "scarce_resource", "title": "📦 物資有限",
     "desc": "現場只湊到 1 份可調度的飲用水，資源稀缺情境成立。",
     "run": _step_scarce_resource},
    {"key": "auto_dispatch", "title": "🤖 自動媒合",
     "desc": "系統執行真正的自動媒合演算法，決定唯一一份物資該給誰。",
     "run": _step_auto_dispatch},
    {"key": "wait_confirm", "title": "✅ 等待管理員確認",
     "desc": "媒合結果僅為建議，需管理員手動確認才會通知志工。",
     "run": _step_wait_confirm},
]


# ──────────────────────────────────────────────────────────
# 對外 API
# ──────────────────────────────────────────────────────────
def status(db: Session) -> dict:
    step, log, entities = _load(db)
    autoplay = _cfg_get(db, "scenario_autoplay", "0") == "1"
    return {
        "step": step,
        "total": len(STEPS),
        "finished": step >= len(STEPS) - 1,
        "autoplay": autoplay,
        "log": log,
        "steps": [
            {"title": s["title"], "desc": s["desc"], "done": i <= step}
            for i, s in enumerate(STEPS)
        ],
    }


def start(db: Session) -> dict:
    """重置後從頭開始（確保每次「開始」都是乾淨的情境）。"""
    reset(db)
    return advance(db)


def advance(db: Session) -> dict:
    step, log, entities = _load(db)
    next_step = step + 1
    if next_step >= len(STEPS):
        _cfg_set(db, "scenario_autoplay", "0")
        db.commit()
        return status(db)

    spec = STEPS[next_step]
    message, entities_add = spec["run"](db, entities)
    entities.update(entities_add)
    db.commit()

    log.append({
        "step": next_step,
        "title": spec["title"],
        "message": message,
        "at": datetime.now().strftime("%H:%M:%S"),
    })
    if next_step >= len(STEPS) - 1:
        _cfg_set(db, "scenario_autoplay", "0")
    _save(db, next_step, log, entities)
    return status(db)


def reset(db: Session) -> dict:
    """只清掉情境自己建立的資料，完全不動任何既有/真實資料。"""
    step, log, entities = _load(db)

    for key in ("need_other", "need_protagonist"):
        nid = entities.get(key)
        if nid:
            n = db.query(CommunityNeed).filter(CommunityNeed.id == nid).first()
            if n:
                db.delete(n)

    res_id = entities.get("resource")
    if res_id:
        r = db.query(CommunityResource).filter(CommunityResource.id == res_id).first()
        if r:
            db.delete(r)

    vol_id = entities.get("volunteer")
    if vol_id:
        v = db.query(User).filter(User.id == vol_id).first()
        if v:
            db.delete(v)

    if entities.get("protagonist_created") == "1":
        pid = entities.get("protagonist")
        if pid:
            db.query(Alert).filter(Alert.elderly_id == pid).delete(synchronize_session=False)
            db.query(DailyCheckin).filter(DailyCheckin.elderly_id == pid).delete(synchronize_session=False)
            p = db.query(User).filter(User.id == pid).first()
            if p:
                db.delete(p)

    if entities.get("other_elder_created") == "1":
        oid = entities.get("other_elder")
        if oid:
            o = db.query(User).filter(User.id == oid).first()
            if o:
                db.delete(o)

    db.commit()
    _cfg_set(db, "scenario_autoplay", "0")
    _save(db, -1, [], {})
    return status(db)


def set_autoplay(db: Session, enabled: bool) -> dict:
    _cfg_set(db, "scenario_autoplay", "1" if enabled else "0")
    db.commit()
    return status(db)
