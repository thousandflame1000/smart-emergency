import base64
import hashlib
import hmac
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.webhook_event import WebhookEvent
from app.routers import linebot
from app.services import webhook_inbox
from app.timeutil import now_utc


def _envelope(event_id: str | None = None) -> bytes:
    events = []
    if event_id:
        events.append({
            "type": "message",
            "mode": "active",
            "timestamp": 1710000000000,
            "source": {"type": "user", "userId": "U" + "1" * 32},
            "webhookEventId": event_id,
            "deliveryContext": {"isRedelivery": False},
            "replyToken": "reply-token",
            "message": {"id": "123456789", "type": "text", "quoteToken": "quote", "text": "平安"},
        })
    return json.dumps(
        {"destination": "U" + "0" * 32, "events": events},
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")


def _signature(body: bytes) -> str:
    digest = hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()
    return base64.b64encode(digest).decode()


def _post(client: TestClient, body: bytes):
    return client.post(
        "/webhook/line",
        content=body,
        headers={"X-Line-Signature": _signature(body), "Content-Type": "application/json"},
    )


def test_signed_event_is_deduplicated(db, monkeypatch):
    monkeypatch.setattr(linebot, "_DEV_MODE", False)
    calls = []
    monkeypatch.setattr(linebot.handler, "handle", lambda body, signature: calls.append(body))
    client = TestClient(app)
    body = _envelope("evt-deduplicated")

    assert _post(client, body).status_code == 200
    assert _post(client, body).status_code == 200
    assert len(calls) == 1
    db.expire_all()
    row = db.get(WebhookEvent, "evt-deduplicated")
    assert row.status == "PROCESSED" and row.attempt_count == 1


def test_failed_event_is_persisted_and_retried(db, monkeypatch):
    monkeypatch.setattr(linebot, "_DEV_MODE", False)
    monkeypatch.setattr(
        linebot.handler,
        "handle",
        lambda body, signature: (_ for _ in ()).throw(RuntimeError("temporary LINE failure")),
    )
    client = TestClient(app)
    body = _envelope("evt-retry")

    assert _post(client, body).status_code == 503
    db.expire_all()
    row = db.get(WebhookEvent, "evt-retry")
    assert row.status == "FAILED" and "temporary LINE failure" in row.last_error

    row.available_at = now_utc() - timedelta(seconds=1)
    db.commit()
    calls = []
    monkeypatch.setattr(linebot.handler, "handle", lambda body, signature: calls.append(body))
    assert linebot.retry_failed_webhooks() == 1
    db.expire_all()
    assert db.get(WebhookEvent, "evt-retry").status == "PROCESSED"
    assert len(calls) == 1


def test_webhook_is_exempt_from_global_rate_limit(monkeypatch):
    monkeypatch.setattr(linebot, "_DEV_MODE", False)
    client = TestClient(app)
    body = _envelope()
    statuses = [_post(client, body).status_code for _ in range(65)]
    assert statuses == [200] * 65


def test_expired_lease_cannot_be_completed_by_old_worker(db):
    row = WebhookEvent(
        id="evt-lease",
        event_type="message",
        payload=json.dumps({"destination": "U", "event": {"type": "message"}}),
        status="PROCESSING",
        attempt_count=1,
        available_at=now_utc() - timedelta(minutes=5),
        locked_at=now_utc() - timedelta(minutes=5),
        locked_by="old-worker",
    )
    db.add(row); db.commit()

    new_owner = webhook_inbox._claim_existing(db, row.id)
    assert new_owner and new_owner != "old-worker"
    assert webhook_inbox.mark_processed(row.id, "old-worker") is False
    db.expire_all()
    current = db.get(WebhookEvent, row.id)
    assert current.status == "PROCESSING" and current.locked_by == new_owner
