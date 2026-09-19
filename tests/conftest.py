# -*- coding: utf-8 -*-
"""
Pytest 共用設定。用一個獨立的 sqlite 檔案跑測試，每個測試前重建乾淨的表，
測試之間互不干擾，也完全不會碰到正式資料庫。
"""
import os
import tempfile

# 必須在任何 app.* 被 import 之前設定好環境變數，
# 因為 app.config.Settings() 是在模組載入當下就讀取 .env / 環境變數。
_TEST_DB = os.path.join(tempfile.gettempdir(), "smart_emergency_pytest.db")
os.environ["DATABASE_URL"] = f"sqlite:///{_TEST_DB}"
os.environ.setdefault("LINE_CHANNEL_SECRET", "dummy_secret")
os.environ.setdefault("LINE_CHANNEL_ACCESS_TOKEN", "dummy_token")
os.environ.setdefault("GEMINI_API_KEY", "dummy_key")

import pytest

from app.database import engine, Base, SessionLocal
import app.models.resource_point  # noqa: F401 確保所有 model 都被註冊


@pytest.fixture(autouse=True)
def clean_db():
    Base.metadata.drop_all(bind=engine)
    Base.metadata.create_all(bind=engine)
    yield


@pytest.fixture
def db():
    session = SessionLocal()
    try:
        yield session
    finally:
        session.close()


class LineOutbox:
    """Stands in for the LINE Messaging API in every test.

    Without this, any code path that pushes a LINE message (notifying a requester,
    a volunteer, a family contact...) would try to reach api.line.me with a dummy
    token — slow, flaky, and it would hide whether the right people were told.
    Tests can assert on ``line_outbox.sent`` (list of (kind, to, message))."""

    def __init__(self):
        self.sent = []

    def push_message(self, request):
        for message in request.messages:
            self.sent.append(("push", request.to, message))

    def reply_message(self, request):
        for message in request.messages:
            self.sent.append(("reply", None, message))

    def get_profile(self, uid):
        class _Profile:
            display_name = "測試用戶"
        return _Profile()

    def texts(self, to=None):
        return [getattr(m, "text", "") for kind, target, m in self.sent
                if hasattr(m, "text") and (to is None or target == to)]


@pytest.fixture(autouse=True)
def line_outbox(monkeypatch):
    from app.services import line_notify
    box = LineOutbox()
    monkeypatch.setattr(line_notify, "_get_api", lambda: box)
    return box


@pytest.fixture(autouse=True)
def no_real_geocoding(monkeypatch):
    """Nominatim is a real network call with a 1s throttle; tests must never hit it.
    Individual tests can override with monkeypatch.setattr(places, "search_places", ...)."""
    from app.services import places
    monkeypatch.setattr(places, "search_places", lambda query: [])
