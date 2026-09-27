from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
from functools import wraps
import hashlib
import logging
from sqlalchemy import text

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="Asia/Taipei")


def _lock_key(job_id: str) -> int:
    return int.from_bytes(
        hashlib.blake2b(job_id.encode("utf-8"), digest_size=8).digest(),
        byteorder="big",
        signed=True,
    )


def single_instance_job(job_id: str, func):
    """Run once across all PostgreSQL web processes, not once per process."""
    @wraps(func)
    def wrapped():
        from app.database import SessionLocal

        lock_db = SessionLocal()
        acquired = True
        try:
            if lock_db.get_bind().dialect.name == "postgresql":
                acquired = bool(
                    lock_db.execute(
                        text("SELECT pg_try_advisory_lock(:key)"),
                        {"key": _lock_key(job_id)},
                    ).scalar()
                )
            if not acquired:
                logger.debug("Scheduler job %s is already running on another process", job_id)
                return None
            return func()
        finally:
            if acquired and lock_db.get_bind().dialect.name == "postgresql":
                lock_db.execute(
                    text("SELECT pg_advisory_unlock(:key)"),
                    {"key": _lock_key(job_id)},
                )
                lock_db.commit()
            lock_db.close()

    return wrapped


def start_scheduler() -> None:
    if _scheduler.running:
        return
    from app.services.checkin import send_daily_checkins
    from app.services.alert import check_no_response
    from app.database import SessionLocal
    from app.models.config import SystemConfig

    db = SessionLocal()
    cfg = {
        row.key: row.value
        for row in db.query(SystemConfig).all()
    }
    db.close()

    checkin_hour   = int(cfg.get("checkin_hour", "8"))
    checkin_minute = int(cfg.get("checkin_minute", "0"))

    # 每天早上發打卡
    _scheduler.add_job(
        single_instance_job("daily_checkin", send_daily_checkins),
        CronTrigger(hour=checkin_hour, minute=checkin_minute, timezone="Asia/Taipei"),
        id="daily_checkin",
        replace_existing=True,
    )

    # 每 15 分鐘檢查未回應
    _scheduler.add_job(
        single_instance_job("check_no_response", check_no_response),
        IntervalTrigger(minutes=15),
        id="check_no_response",
        replace_existing=True,
    )

    # 緊急模式：每 30 分鐘自動媒合物資
    from app.services.dispatch import auto_dispatch
    _scheduler.add_job(
        single_instance_job("auto_dispatch", auto_dispatch),
        IntervalTrigger(minutes=30),
        id="auto_dispatch",
        replace_existing=True,
    )

    from app.services.outbox import process_outbox_batch
    _scheduler.add_job(
        single_instance_job("notification_outbox", process_outbox_batch),
        IntervalTrigger(seconds=5),
        id="notification_outbox",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    from app.routers.linebot import retry_failed_webhooks
    _scheduler.add_job(
        single_instance_job("webhook_inbox", retry_failed_webhooks),
        IntervalTrigger(seconds=5),
        id="webhook_inbox",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    from app.services.privacy import purge_expired_technical_records
    _scheduler.add_job(
        single_instance_job("privacy_retention", purge_expired_technical_records),
        CronTrigger(hour=3, minute=30, timezone="Asia/Taipei"),
        id="privacy_retention",
        replace_existing=True,
        max_instances=1,
        coalesce=True,
    )

    _scheduler.start()
    logger.info(f"Scheduler started. Checkin at {checkin_hour:02d}:{checkin_minute:02d}")

def shutdown_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")


def scheduler_running() -> bool:
    return _scheduler.running
