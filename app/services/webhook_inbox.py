from __future__ import annotations

import hashlib
import json
import os
import socket
import uuid
from datetime import UTC, datetime, timedelta
from typing import Callable

from sqlalchemy import or_
from sqlalchemy.exc import IntegrityError

from app.config import settings
from app.database import SessionLocal
from app.models.webhook_event import WebhookEvent


Dispatch = Callable[[str, dict], None]


def _now() -> datetime:
    return datetime.now(UTC).replace(tzinfo=None)


def _worker_id() -> str:
    return f"{socket.gethostname()}:{os.getpid()}:{uuid.uuid4().hex[:8]}"


def event_id(raw_event: dict) -> str:
    supplied = str(raw_event.get("webhookEventId") or "").strip()
    if supplied:
        return supplied
    canonical = json.dumps(raw_event, ensure_ascii=False, sort_keys=True, separators=(",", ":"))
    return "sha256:" + hashlib.sha256(canonical.encode("utf-8")).hexdigest()


def claim_event(destination: str, raw_event: dict) -> tuple[str, str | None]:
    """Persist and claim one event. Returns (event_id, lease_owner)."""
    identifier = event_id(raw_event)
    payload = json.dumps(
        {"destination": destination, "event": raw_event},
        ensure_ascii=False,
        separators=(",", ":"),
    )
    now = _now()
    db = SessionLocal()
    try:
        owner = _worker_id()
        row = WebhookEvent(
            id=identifier,
            event_type=str(raw_event.get("type") or "unknown"),
            payload=payload,
            status="PROCESSING",
            attempt_count=1,
            available_at=now,
            locked_at=now,
            locked_by=owner,
        )
        db.add(row)
        try:
            db.commit()
            return identifier, owner
        except IntegrityError:
            db.rollback()
        return identifier, _claim_existing(db, identifier, now)
    finally:
        db.close()


def _claim_existing(db, identifier: str, now: datetime | None = None) -> str | None:
    now = now or _now()
    stale_before = now - timedelta(seconds=settings.WEBHOOK_LEASE_SECONDS)
    query = db.query(WebhookEvent).filter(WebhookEvent.id == identifier)
    if db.get_bind().dialect.name == "postgresql":
        query = query.with_for_update(skip_locked=True)
    row = query.first()
    if not row or row.status in ("PROCESSED", "DEAD"):
        db.rollback()
        return None
    if row.status == "PROCESSING" and row.locked_at and row.locked_at > stale_before:
        db.rollback()
        return None
    if row.status == "FAILED" and row.available_at and row.available_at > now:
        db.rollback()
        return None
    if int(row.attempt_count or 0) >= settings.WEBHOOK_MAX_ATTEMPTS:
        row.status = "DEAD"
        row.locked_at = None
        row.locked_by = None
        db.commit()
        return None
    owner = _worker_id()
    row.status = "PROCESSING"
    row.attempt_count = int(row.attempt_count or 0) + 1
    row.locked_at = now
    row.locked_by = owner
    row.last_error = None
    db.commit()
    return owner


def mark_processed(identifier: str, owner: str) -> bool:
    db = SessionLocal()
    try:
        row = db.query(WebhookEvent).filter(
            WebhookEvent.id == identifier,
            WebhookEvent.status == "PROCESSING",
            WebhookEvent.locked_by == owner,
        ).first()
        if not row:
            return False
        row.status = "PROCESSED"
        row.processed_at = _now()
        row.locked_at = None
        row.locked_by = None
        row.last_error = None
        db.commit()
        return True
    finally:
        db.close()


def mark_failed(identifier: str, owner: str, exc: Exception) -> bool:
    db = SessionLocal()
    try:
        row = db.query(WebhookEvent).filter(
            WebhookEvent.id == identifier,
            WebhookEvent.status == "PROCESSING",
            WebhookEvent.locked_by == owner,
        ).first()
        if not row:
            return False
        attempts = int(row.attempt_count or 1)
        row.status = "DEAD" if attempts >= settings.WEBHOOK_MAX_ATTEMPTS else "FAILED"
        delay = min(
            settings.WEBHOOK_BASE_DELAY_SECONDS * (2 ** max(0, attempts - 1)),
            settings.WEBHOOK_MAX_DELAY_SECONDS,
        )
        row.available_at = _now() + timedelta(seconds=delay)
        row.locked_at = None
        row.locked_by = None
        row.last_error = str(exc)[:2000]
        db.commit()
        return True
    finally:
        db.close()


def process_ready_batch(dispatch: Dispatch, limit: int = 25) -> int:
    now = _now()
    stale_before = now - timedelta(seconds=settings.WEBHOOK_LEASE_SECONDS)
    db = SessionLocal()
    try:
        identifiers = [
            row[0]
            for row in (
                db.query(WebhookEvent.id)
                .filter(
                    or_(
                        (WebhookEvent.status == "FAILED") & (WebhookEvent.available_at <= now),
                        (WebhookEvent.status == "PROCESSING") & (WebhookEvent.locked_at <= stale_before),
                    )
                )
                .order_by(WebhookEvent.available_at, WebhookEvent.received_at)
                .limit(limit)
                .all()
            )
        ]
    finally:
        db.close()

    processed = 0
    for identifier in identifiers:
        claim_db = SessionLocal()
        try:
            owner = _claim_existing(claim_db, identifier)
            if not owner:
                continue
            row = claim_db.get(WebhookEvent, identifier)
            envelope = json.loads(row.payload)
        finally:
            claim_db.close()
        try:
            dispatch(envelope.get("destination") or "", envelope["event"])
        except Exception as exc:
            mark_failed(identifier, owner, exc)
        else:
            if mark_processed(identifier, owner):
                processed += 1
    return processed
