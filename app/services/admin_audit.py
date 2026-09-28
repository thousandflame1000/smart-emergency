"""Record admin write requests. A failed audit write never fails the request itself."""
import logging
from datetime import UTC

from app.database import SessionLocal
from app.models.admin_audit import AdminAudit

log = logging.getLogger(__name__)
WRITE_METHODS = {"POST", "PUT", "PATCH", "DELETE"}


def should_record(method: str, path: str) -> bool:
    return method in WRITE_METHODS and path.startswith("/api/")


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
    return [{"at": at(r.created_at), "actor": r.actor_label,
             "method": r.method, "path": r.path, "status": r.status_code} for r in rows]
