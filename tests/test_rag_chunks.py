# -*- coding: utf-8 -*-
"""
驗證知識庫查閱/更新/刪除端點——對應計畫書「管理員可在後台查閱與更新」的承諾。
用 monkeypatch 換掉 _embed，完全不會打真的 Gemini API。
"""
import json
import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from app.models.knowledge import KnowledgeChunk
from app.services import rag as rag_svc
from app.routers import rag as rag_router
from app.security import require_admin


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(rag_svc, "_embed", lambda text: [0.9, 0.9, 0.9])
    app = FastAPI()
    app.include_router(rag_router.router, prefix="/api/rag")
    app.dependency_overrides[require_admin] = lambda: {"id": "test-admin", "name": "Test admin"}
    return TestClient(app)


def _seed_chunk(db):
    chunk = KnowledgeChunk(
        content="CPR 步驟：胸外按壓 30 下，人工呼吸 2 下，反覆進行。",
        embedding=json.dumps([0.1, 0.2, 0.3]),
        source="測試手冊", category="first_aid", version="1.0",
    )
    db.add(chunk); db.commit(); db.refresh(chunk)
    return str(chunk.id)


def test_list_chunks(db, client):
    _seed_chunk(db)
    db.close()
    r = client.get("/api/rag/chunks")
    assert r.status_code == 200
    assert r.json()["total"] == 1


def test_get_chunk(db, client):
    chunk_id = _seed_chunk(db)
    db.close()
    r = client.get(f"/api/rag/chunks/{chunk_id}")
    assert r.status_code == 200
    assert r.json()["source"] == "測試手冊"
    assert r.json()["version"] == "1.0"


def test_update_content_reembeds(db, client):
    chunk_id = _seed_chunk(db)
    db.close()

    r = client.put(f"/api/rag/chunks/{chunk_id}", json={"content": "CPR 更新版：先確認意識與呼吸。"})
    assert r.status_code == 200
    assert r.json()["re_embedded"] is True

    from app.database import SessionLocal
    db2 = SessionLocal()
    c = db2.query(KnowledgeChunk).filter(KnowledgeChunk.id == chunk_id).first()
    assert c.content == "CPR 更新版：先確認意識與呼吸。"
    assert json.loads(c.embedding) == [0.9, 0.9, 0.9]
    db2.close()


def test_update_source_only_does_not_reembed(db, client):
    chunk_id = _seed_chunk(db)
    db.close()

    r = client.put(f"/api/rag/chunks/{chunk_id}", json={"source": "測試手冊 v2.0"})
    assert r.json()["re_embedded"] is False

    from app.database import SessionLocal
    db2 = SessionLocal()
    c = db2.query(KnowledgeChunk).filter(KnowledgeChunk.id == chunk_id).first()
    assert c.source == "測試手冊 v2.0"
    db2.close()


def test_delete_chunk(db, client):
    chunk_id = _seed_chunk(db)
    db.close()

    r = client.delete(f"/api/rag/chunks/{chunk_id}")
    assert r.status_code == 200

    from app.database import SessionLocal
    db2 = SessionLocal()
    assert db2.query(KnowledgeChunk).count() == 0
    db2.close()


def test_get_missing_chunk_404(client):
    r = client.get("/api/rag/chunks/does-not-exist")
    assert r.status_code == 404


def test_mutations_require_an_admin(monkeypatch):
    monkeypatch.setattr(rag_svc, "_embed", lambda text: [0.9, 0.9, 0.9])
    app = FastAPI()
    app.include_router(rag_router.router, prefix="/api/rag")
    anonymous = TestClient(app)
    response = anonymous.post(
        "/api/rag/ingest",
        json={"content": "A sufficiently long approved test document.", "source": "test", "category": "test"},
    )
    assert response.status_code == 403


def test_query_returns_versioned_references(monkeypatch):
    monkeypatch.setattr(rag_svc, "_embed", lambda text: [1.0])
    monkeypatch.setattr(
        rag_svc,
        "_search",
        lambda embedding: [{
            "id": "chunk-1",
            "content": "Call emergency services when life is in danger.",
            "source": "Approved manual",
            "version": "2026.1",
            "category": "first_aid",
            "similarity": 0.91234,
        }],
    )

    class _Response:
        text = "Call emergency services."

    class _Model:
        def generate_content(self, prompt):
            return _Response()

    monkeypatch.setattr(rag_svc.genai, "GenerativeModel", lambda model: _Model())
    result = rag_svc.query("What should I do?")
    assert result["sources"] == ["Approved manual"]
    assert result["retrieval_confidence"] == 0.9123
    assert result["references"] == [{
        "chunk_id": "chunk-1",
        "source": "Approved manual",
        "version": "2026.1",
        "category": "first_aid",
        "similarity": 0.9123,
    }]
