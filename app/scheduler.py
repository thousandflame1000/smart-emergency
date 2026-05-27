from apscheduler.schedulers.background import BackgroundScheduler
from apscheduler.triggers.cron import CronTrigger
from apscheduler.triggers.interval import IntervalTrigger
import logging

logger = logging.getLogger(__name__)
_scheduler = BackgroundScheduler(timezone="Asia/Taipei")


def start_scheduler() -> None:
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
        send_daily_checkins,
        CronTrigger(hour=checkin_hour, minute=checkin_minute, timezone="Asia/Taipei"),
        id="daily_checkin",
        replace_existing=True,
    )

    # 每 15 分鐘檢查未回應
    _scheduler.add_job(
        check_no_response,
        IntervalTrigger(minutes=15),
        id="check_no_response",
        replace_existing=True,
    )

    # 緊急模式：每 30 分鐘自動媒合物資
    from app.services.dispatch import auto_dispatch
    _scheduler.add_job(
        auto_dispatch,
        IntervalTrigger(minutes=30),
        id="auto_dispatch",
        replace_existing=True,
    )

    _scheduler.start()
    logger.info(f"Scheduler started. Checkin at {checkin_hour:02d}:{checkin_minute:02d}")


def shutdown_scheduler() -> None:
    if _scheduler.running:
        _scheduler.shutdown(wait=False)
        logger.info("Scheduler stopped.")
