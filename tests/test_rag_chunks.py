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


@pytest.fixture
def client(monkeypatch):
    monkeypatch.setattr(rag_svc, "_embed", lambda text: [0.9, 0.9, 0.9])
    app = FastAPI()
    app.include_router(rag_router.router, prefix="/api/rag")
    return TestClient(app)


def _seed_chunk(db):
    chunk = KnowledgeChunk(
        content="CPR 步驟：胸外按壓 30 下，人工呼吸 2 下，反覆進行。",
        embedding=json.dumps([0.1, 0.2, 0.3]),
        source="測試手冊 v1.0", category="first_aid",
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
    assert r.json()["source"] == "測試手冊 v1.0"


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


def test_reload_all_refuses_without_ai_and_keeps_the_knowledge_base(db, monkeypatch):
    from fastapi.testclient import TestClient
    from app.config import settings
    from app.main import app
    from app.models.knowledge import KnowledgeChunk
    db.add(KnowledgeChunk(content="CPR 步驟", source="AHA", category="first_aid", embedding="[1.0]")); db.commit()
    monkeypatch.setattr(settings, "EXTERNAL_AI_ENABLED", False)
    r = TestClient(app).post("/api/rag/ingest_all")
    assert r.status_code == 409 and "保持不變" in r.json()["detail"]
    assert db.query(KnowledgeChunk).count() == 1


def test_rebuild_failing_halfway_keeps_old_content(db, monkeypatch):
    import pytest
    from app.models.knowledge import KnowledgeChunk
    from app.services import rag
    db.add(KnowledgeChunk(content="舊內容", source="舊", category="first_aid", embedding="[1.0]")); db.commit()
    calls = {"n": 0}

    def flaky_embed(text):
        calls["n"] += 1
        if calls["n"] > 3:
            raise RuntimeError("quota")
        return [0.1]
    monkeypatch.setattr(rag, "_embed", flaky_embed)
    with pytest.raises(RuntimeError):
        rag.rebuild_builtin_documents()
    db.expire_all()
    assert [c.content for c in db.query(KnowledgeChunk).all()] == ["舊內容"]

    monkeypatch.setattr(rag, "_embed", lambda text: [0.1])
    assert rag.rebuild_builtin_documents() > 1
    db.expire_all()
    assert "舊內容" not in {c.content for c in db.query(KnowledgeChunk).all()}


def _seed_kb(db):
    from app.models.knowledge import KnowledgeChunk
    db.add_all([
        KnowledgeChunk(content="【中風辨識（FAST 法則）】\nF – Face（臉）：請患者微笑，一側臉歪斜。", source="急救指引", category="first_aid"),
        KnowledgeChunk(content="【長者跌倒預防與跌倒後處置】\n先不要急著扶起，確認意識與疼痛部位。", source="長照指引", category="eldercare"),
        KnowledgeChunk(content="【長者日常打卡與關懷流程】\n長者回覆「我很好」即完成打卡。", source="照護 SOP", category="eldercare"),
    ])
    db.commit()


def test_sop_lookup_works_without_external_ai(db, monkeypatch):
    from app.config import settings
    from app.services import rag
    _seed_kb(db)
    monkeypatch.setattr(settings, "EXTERNAL_AI_ENABLED", False)
    stroke = rag.query("中風怎麼辦")
    assert stroke["has_answer"] and stroke["mode"] == "keyword" and "FAST" in stroke["answer"]
    assert "跌倒" in rag.query("阿嬤跌倒了怎麼辦")["answer"]
    assert rag.query("我很無聊")["has_answer"] is False, "一個常見詞不該撈出打卡 SOP"


def test_embedding_failure_falls_back_to_keyword_search(db, monkeypatch):
    from app.config import settings
    from app.services import rag
    _seed_kb(db)
    monkeypatch.setattr(settings, "EXTERNAL_AI_ENABLED", True)
    monkeypatch.setattr(rag, "_embed", lambda text: (_ for _ in ()).throw(RuntimeError("quota")))
    result = rag.query("中風怎麼辦")
    assert result["has_answer"] and result["mode"] == "keyword"


def test_line_labels_keyword_answers_as_knowledge_base(db, line_outbox, monkeypatch):
    from app.config import settings
    from tests.test_line_hardening import mk, replies, say
    _seed_kb(db)
    monkeypatch.setattr(settings, "EXTERNAL_AI_ENABLED", False)
    mk(db, "志工", ["volunteer"], "U-kb")
    say("U-kb", "中風怎麼辦？")
    reply = replies(line_outbox)[-1]
    assert reply.startswith("📚 知識庫") and "FAST" in reply
