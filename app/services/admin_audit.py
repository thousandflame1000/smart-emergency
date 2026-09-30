"""Record admin write requests. A failed audit write never fails the request itself."""
import logging
from datetime import UTC

from app.database import SessionLocal
from app.models.admin_audit import AdminAudit

log = logging.getLogger(__name__)
import re

WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}
# 用 POST 傳資料、但只做計算不改任何東西的端點；記下來只會把真正的操作淹掉。
READ_ONLY_POSTS = {"/api/workspaces/import-preview", "/api/workspaces/analyze", "/api/workspaces/area-facilities",
                   "/api/workspaces/allocate", "/api/workspaces/database-merge", "/api/workspaces/database-diff",
                   "/api/rag/query"}

_ID = r"[^/?]+"
_ACTIONS = [
    ("POST", r"/api/dashboard/mode\?mode=emergency", "啟動緊急模式"),
    ("POST", r"/api/dashboard/mode\?mode=normal", "解除緊急模式"),
    ("POST", r"/api/dashboard/users", "新增人員"),
    ("PUT", rf"/api/dashboard/users/{_ID}", "修改人員資料"),
    ("DELETE", rf"/api/dashboard/users/{_ID}", "刪除人員"),
    ("POST", rf"/api/dashboard/users/{_ID}/resolve_alerts", "解除警報"),
    ("POST", rf"/api/dashboard/users/{_ID}/join_code", "產生加入碼"),
    ("POST", rf"/api/dashboard/users/{_ID}/unlink_line", "解除 LINE 綁定"),
    ("POST", r"/api/dashboard/trigger_checkin", "立即發送打卡"),
    ("POST", r"/api/dashboard/relations", "新增照護關係"),
    ("POST", rf"/api/dashboard/relations/{_ID}/move", "調整通知順序"),
    ("DELETE", rf"/api/dashboard/relations/{_ID}", "刪除照護關係"),
    ("POST", rf"/api/dashboard/volunteer-applications/{_ID}/decision", "審核志工"),
    ("POST", r"/api/dashboard/rehearsal/.+", "演練工具"),
    ("POST", r"/api/resources/dispatch", "自動派遣"),
    ("POST", rf"/api/resources/needs/{_ID}/confirm_dispatch", "核准派遣"),
    ("POST", rf"/api/resources/needs/{_ID}/decline_suggestion", "退回建議"),
    ("POST", rf"/api/resources/needs/{_ID}/propose_dispatch", "提出派遣建議"),
    ("POST", rf"/api/resources/needs/{_ID}/match", "手動派遣"),
    ("POST", rf"/api/resources/needs/{_ID}/message_assignee", "傳訊息給志工"),
    ("POST", rf"/api/resources/needs/{_ID}/resolve_sos", "求救已處理"),
    ("POST", rf"/api/resources/needs/{_ID}/assign_sos\?(.*&)?replace=true.*", "改派求救處理人"),
    ("POST", rf"/api/resources/needs/{_ID}/assign_sos", "指派求救處理人"),
    ("POST", rf"/api/resources/needs/{_ID}/reported_119", "轉報 119"),
    ("POST", r"/api/rollcall/remind", "點名：再問還沒回的人"),
    ("POST", rf"/api/rollcall/{_ID}", "點名：代為標記"),
    ("POST", r"/api/resources/needs", "新增需求"),
    ("PUT", rf"/api/resources/needs/{_ID}", "修改需求"),
    ("DELETE", rf"/api/resources/needs/{_ID}", "刪除需求"),
    ("POST", r"/api/resources/points/seed", "匯入資源點"),
    ("POST", rf"/api/resources/points/{_ID}/checkin", "資源點入住"),
    ("POST", rf"/api/resources/points/{_ID}/checkout", "資源點離開"),
    ("POST", r"/api/resources/points", "新增資源點"),
    ("PUT", rf"/api/resources/points/{_ID}", "修改資源點"),
    ("DELETE", rf"/api/resources/points/{_ID}", "刪除資源點"),
    ("PATCH", rf"/api/resources/{_ID}/toggle", "切換物資可用"),
    ("POST", r"/api/resources/?", "新增物資"),
    ("PUT", rf"/api/resources/{_ID}", "修改物資"),
    ("DELETE", rf"/api/resources/{_ID}", "刪除物資"),
    ("POST", r"/api/workspaces/database-push", "寫回物資資料"),
    ("POST", r"/api/workspaces/apply-allocation", "送出分配建議"),
    ("POST", r"/api/workspaces", "另存工作區"),
    ("PUT", rf"/api/workspaces/{_ID}", "儲存工作區"),
    ("DELETE", rf"/api/workspaces/{_ID}", "刪除工作區"),
    ("POST", r"/api/zones", "新增分區"),
    ("DELETE", rf"/api/zones/{_ID}", "刪除分區"),
    ("PUT", rf"/api/zones/(resources|needs)/{_ID}", "調整分區"),
    ("POST", r"/api/rag/(ingest|sync|ingest_all)", "更新知識庫"),
    ("PUT", rf"/api/rag/chunks/{_ID}", "修改知識庫段落"),
    ("DELETE", rf"/api/rag/chunks/{_ID}", "刪除知識庫段落"),
    ("POST", r"/api/system/rich-menu/install", "重裝 LINE 選單"),
    ("PUT", r"/api/rehearsal", "儲存演練紀錄"),
]


def should_record(method: str, path: str) -> bool:
    return method in WRITE_METHODS and path.startswith("/api/") and path not in READ_ONLY_POSTS


def describe(method: str, path: str) -> str:
    """把 API 路徑翻成管理員看得懂的動作名稱；LINE 紀錄本來就是中文。"""
    if method == "LINE":
        return path.split(" ")[0]
    for m, pattern, label in _ACTIONS:
        if m == method and re.fullmatch(pattern + r"(\?.*)?", path):
            return label
    return path


def record(admin: dict | None, method: str, path: str, status_code: int) -> None:
    try:
        db = SessionLocal()
    except Exception:
        log.warning("admin audit unavailable for %s %s", method, path, exc_info=True)
        return
    try:
        db.add(AdminAudit(
            actor_id=str(admin["id"]) if admin and admin.get("id") else None,
            actor_label=admin["name"] if admin else "未登入（開放模式）",
            method=method, path=path[:500], status_code=status_code,
        ))
        db.commit()
    except Exception:
        db.rollback()
        log.warning("admin audit write failed for %s %s", method, path, exc_info=True)
    finally:
        db.close()


def recent(db, limit: int = 100) -> list[dict]:
    rows = db.query(AdminAudit).order_by(AdminAudit.created_at.desc()).limit(limit).all()
    # server_default now() 存的是不帶時區的 UTC（SQLite 與 Railway Postgres 皆然）
    def at(value):
        return (value if value.tzinfo else value.replace(tzinfo=UTC)).isoformat() if value else None
    return [{"at": at(r.created_at), "actor": r.actor_label, "action": describe(r.method, r.path),
             "method": r.method, "path": r.path, "status": r.status_code} for r in rows]
