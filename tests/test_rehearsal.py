# -*- coding: utf-8 -*-
import json

import pytest
from fastapi.testclient import TestClient


@pytest.fixture()
def web():
    from app.main import app
    return TestClient(app)


def _submission():
    return {
        "version": "competition-rehearsal-v1",
        "participant_name": "參賽者",
        "session_name": "第一次全流程",
        "rehearsal_date": "2026-09-27",
        "scenario": "光復鄉馬太鞍溪洪災",
        "route": [{"id": "r-checkin", "status": "blocked", "observation": "按了平安",
                   "blocker": "管理畫面更新慢"}],
        "timing": [{"id": "t-open", "actual_seconds": 41, "script_notes": "開場太長"}],
        "rubric": [{"id": "d-story", "score": 3, "evidence": "走完三角色", "improvement": "縮短切頁"}],
        "questions": [{"id": "q-privacy", "answer": "先取得同意並限制用途。"}],
        "overall_notes": "需要再練管理者流程",
        "next_action": "重練派遣兩次",
    }


def test_rehearsal_page_has_inputs_for_roles_and_official_rubric(web, monkeypatch, tmp_path):
    from app.config import settings
    from app.routers import rehearsal
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(rehearsal, "RESPONSE_PATH", tmp_path / "responses.json")
    page = web.get("/rehearsal")
    assert page.status_code == 200
    for label in ("居民路線", "志工路線", "管理者路線", "作品展演 60 分", "展位準備 20 分",
                  "整體表現 20 分", "送出給 Codex"):
        assert label in page.text
    assert 'data-field="observation"' in page.text
    assert 'data-field="answer"' in page.text


def test_rehearsal_submission_round_trip_writes_codex_handoff_file(web, monkeypatch, tmp_path):
    from app.config import settings
    from app.routers import rehearsal
    path = tmp_path / "responses.json"
    monkeypatch.setattr(settings, "APP_ENV", "development")
    monkeypatch.setattr(rehearsal, "RESPONSE_PATH", path)

    saved = web.put("/api/rehearsal", json=_submission())
    assert saved.status_code == 200 and saved.json()["path"] == "responses.json"
    assert path.exists()
    on_disk = json.loads(path.read_text(encoding="utf-8"))
    assert on_disk["submission"]["route"][0]["status"] == "blocked"
    assert on_disk["submission"]["next_action"] == "重練派遣兩次"

    loaded = web.get("/api/rehearsal")
    assert loaded.status_code == 200
    assert loaded.json()["submission"]["participant_name"] == "參賽者"


def test_rehearsal_is_not_exposed_in_production(web, monkeypatch):
    from app.config import settings
    monkeypatch.setattr(settings, "APP_ENV", "production")
    assert web.get("/rehearsal").status_code == 404
    assert web.get("/api/rehearsal").status_code == 404
