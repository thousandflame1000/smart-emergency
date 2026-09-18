from fastapi import APIRouter, Depends, HTTPException
from sqlalchemy.orm import Session

from app.database import get_db
from app.models.task_workflow import Task, TaskEvent
from app.services.outbox import notification_delivery_for_task


router = APIRouter()


@router.get("/{task_id}/notifications")
def task_notifications(task_id: str, db: Session = Depends(get_db)):
    try:
        return notification_delivery_for_task(db, task_id)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc


@router.get("/{task_id}/events")
def task_events(task_id: str, db: Session = Depends(get_db)):
    task = db.get(Task, task_id)
    if not task:
        raise HTTPException(status_code=404, detail="Task not found")
    events = (
        db.query(TaskEvent)
        .filter(TaskEvent.task_id == task.id)
        .order_by(TaskEvent.occurred_at, TaskEvent.id)
        .all()
    )
    return {
        "task_id": str(task.id),
        "status": task.status,
        "events": [
            {
                "id": str(event.id),
                "event_type": event.event_type,
                "task_version": event.task_version,
                "occurred_at": event.occurred_at.isoformat() if event.occurred_at else None,
                "metadata": event.metadata_json,
            }
            for event in events
        ],
    }
