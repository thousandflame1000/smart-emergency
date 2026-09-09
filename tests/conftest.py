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
