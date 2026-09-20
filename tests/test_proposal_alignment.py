# -*- coding: utf-8 -*-
"""計畫書寫的功能，系統要真的做到：知識庫涵蓋表 3 的主題、有版本欄位，
打卡卡片有「身體不舒服」，自行接單可被管理員撤銷。"""
import pytest

from app.models.checkin import DailyCheckin
from app.models.knowledge import KnowledgeChunk
from app.models.need import CommunityNeed
from app.services import dispatch, rag
from app.timeutil import today_tw
from tests.test_line_hardening import mk, press, replies, say, sent_to
from tests.test_role_interfaces import card_text, world


# ═══════════════ 知識庫 ═══════════════
def _docs():
    import importlib
    import ingest_kb
    return importlib.reload(ingest_kb).DOCUMENTS


PROPOSAL_TOPICS = {
    "CPR": "CPR", "哈姆立克": "哈姆立克", "中風辨識": "FAST", "低血糖": "低血糖", "失溫": "失溫",
    "跌倒": "跌倒", "壓瘡": "壓瘡", "失智": "失智", "地震": "地震", "颱風": "颱風", "洪水": "淹水",
}


def test_knowledge_base_covers_every_topic_the_proposal_promises():
    text = " ".join(d["content"] for d in _docs())
    missing = [name for name, kw in PROPOSAL_TOPICS.items() if kw not in text]
    assert not missing, f"計畫書表 3 提到但知識庫沒有：{missing}"


def test_knowledge_base_has_real_depth_and_every_entry_is_one_clean_chunk():
    docs = _docs()
    assert len(docs) >= 30
    assert sum(len(d["content"]) for d in docs) >= 7000
    for d in docs:
        assert d["source"] and d["category"] in {"first_aid", "eldercare", "disaster", "volunteer"}
    from kb_more import MORE
    for d in MORE:
        chunks = rag._chunk_text(d["content"])
        assert len(chunks) == 1, f"{d['source']} 被切成 {len(chunks)} 段，回答會斷章取義"


def test_every_first_aid_entry_says_when_to_call_119_or_seek_care():
    from kb_more import MORE
    for d in MORE:
        if d["category"] == "first_aid":
            assert "119" in d["content"], f"{d['source']} 沒有寫何時要撥 119"


def test_every_new_entry_carries_a_version():
    from kb_more import MORE
    assert all(d.get("version") for d in MORE)


@pytest.fixture()
def fake_embedding(monkeypatch):
    monkeypatch.setattr(rag, "_embed", lambda text: [0.1, 0.2, 0.3])


def test_sync_fills_gaps_without_deleting_or_overwriting(db, fake_embedding):
    db.add(KnowledgeChunk(content="管理員自己改過的內容", source="急救指引：外傷止血 v1.0",
                          category="first_aid", version="9.9"))
    db.commit()
    first = rag.sync_builtin_documents()
    assert first["added"] > 20 and first["failed"] == 0
    db.expire_all()
    assert db.query(KnowledgeChunk).filter(KnowledgeChunk.content == "管理員自己改過的內容").count() == 1
    second = rag.sync_builtin_documents()
    assert second["added"] <= 1, "第二次不能再重複新增（被改過的那份最多補回原文一次）"
    total = db.query(KnowledgeChunk).count()
    assert rag.sync_builtin_documents()["added"] == 0
    assert db.query(KnowledgeChunk).count() == total


def test_ingest_stores_version(db, fake_embedding):
    rag.ingest_document("【測試】" + "內容" * 30, "測試來源 v2", "first_aid", version="2.0")
    assert db.query(KnowledgeChunk).one().version == "2.0"


def test_version_column_is_added_to_an_existing_database(tmp_path):
    from sqlalchemy import create_engine, inspect, text
    from app.schema_migrations import ensure_additive_schema
    engine = create_engine(f"sqlite:///{tmp_path / 'old.db'}")
    with engine.begin() as c:
        c.exec_driver_sql("CREATE TABLE knowledge_base (id TEXT PRIMARY KEY, content TEXT NOT NULL, embedding TEXT, "
                          "source TEXT NOT NULL, category TEXT NOT NULL, created_at TIMESTAMP)")
    ensure_additive_schema(engine)
    assert "version" in {c["name"] for c in inspect(engine).get_columns("knowledge_base")}


def test_kb_listing_shows_version(db, fake_embedding):
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    from app.routers import rag as rag_router
    rag.ingest_document("【版本測試】" + "內容" * 30, "版本來源 v3", "eldercare", version="3.1")
    api = FastAPI(); api.include_router(rag_router.router, prefix="/api/rag")
    chunks = TestClient(api).get("/api/rag/chunks").json()["chunks"]
    assert chunks[0]["version"] == "3.1"


def test_line_routes_new_health_topics_to_the_knowledge_assistant(db, line_outbox, monkeypatch):
    asked = []
    monkeypatch.setattr(rag, "query", lambda q: asked.append(q) or {"answer": "已回答", "sources": [], "has_answer": True})
    mk(db, "志工", ["volunteer"], "U-kb")
    for q in ("壓瘡怎麼預防", "失智的長輩走失了怎麼辦", "有人抽搐該怎麼做"):
        say("U-kb", q)
    assert len(asked) == 3


# ═══════════════ 打卡：身體不舒服 ═══════════════
def _checkin(db, elder, status="pending"):
    row = DailyCheckin(elderly_id=elder.id, date=today_tw(), status=status)
    db.add(row); db.commit(); db.refresh(row)
    return row


def _with_family(db):
    from app.models.care_relation import CareRelation
    vol, req, adm, res, need = world(db)
    fam = mk(db, "女兒", ["family"], "U-fam")
    db.add(CareRelation(elderly_id=req.id, contact_id=fam.id, relation="family", notify_order=1)); db.commit()
    return req, fam, adm


def test_checkin_card_offers_three_choices():
    from app.services.line_notify import send_checkin_message
    from tests.conftest import LineOutbox  # noqa: F401
    import app.services.line_notify as ln
    box = LineOutbox()
    ln._get_api = lambda: box
    send_checkin_message("U-x", "checkin-1")
    card = str(box.sent[-1][2].contents.to_dict())
    for data in ("action=ok", "action=unwell", "action=help"):
        assert data in card
    assert "身體不舒服" in card


def test_unwell_button_notifies_family_but_is_not_an_emergency(db, line_outbox):
    req, fam, adm = _with_family(db)
    checkin = _checkin(db, req)
    press("U-req", f"action=unwell&checkin_id={checkin.id}")
    db.expire_all()
    assert db.query(DailyCheckin).one().status == "unwell"
    assert "身體不舒服" in card_text(line_outbox, "U-fam")
    assert not sent_to(line_outbox, "U-adm"), "身體不舒服不是緊急事件，不打擾管理員"
    assert db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").count() == 0
    reply = replies(line_outbox)[-1]
    assert "已通知您的家人" in reply and "119" in reply


def test_unwell_is_idempotent_and_does_not_downgrade_a_help_request(db, line_outbox):
    req, fam, adm = _with_family(db)
    checkin = _checkin(db, req)
    press("U-req", f"action=unwell&checkin_id={checkin.id}")
    n = len([m for m in line_outbox.sent if m[1] == "U-fam"])
    press("U-req", f"action=unwell&checkin_id={checkin.id}")
    assert len([m for m in line_outbox.sent if m[1] == "U-fam"]) == n, "重複按不能重複通知家人"
    assert "已經回報過" in replies(line_outbox)[-1]
    press("U-req", f"action=help&checkin_id={checkin.id}")
    press("U-req", f"action=unwell&checkin_id={checkin.id}")
    db.expire_all()
    assert db.query(DailyCheckin).one().status == "help_needed"


def test_unwell_tells_the_elder_when_nobody_can_be_notified(db, line_outbox):
    vol, req, adm, res, need = world(db)
    say("U-req", "身體不舒服")
    assert any("邀請家人" in t for t in replies(line_outbox))
    db.expire_all()
    assert db.query(DailyCheckin).one().status == "unwell"


def test_unwell_button_only_works_on_your_own_card(db, line_outbox):
    req, fam, adm = _with_family(db)
    checkin = _checkin(db, req)
    press("U-fam", f"action=unwell&checkin_id={checkin.id}")
    assert any("不是您的" in t for t in replies(line_outbox))
    db.expire_all()
    assert db.query(DailyCheckin).one().status == "pending"


def test_saying_fine_afterwards_clears_the_concern(db, line_outbox):
    from app.models.alert import Alert
    req, fam, adm = _with_family(db)
    checkin = _checkin(db, req)
    press("U-req", f"action=unwell&checkin_id={checkin.id}")
    press("U-req", f"action=ok&checkin_id={checkin.id}")
    db.expire_all()
    assert db.query(DailyCheckin).one().status == "ok"
    assert all(a.status == "resolved" for a in db.query(Alert).all())


def test_unwell_shows_in_admin_overview(db, line_outbox):
    req, fam, adm = _with_family(db)
    _ = _checkin(db, req)
    say("U-req", "不舒服")
    say("U-adm", "總覽")
    assert "不舒服 1" in replies(line_outbox)[-1]


# ═══════════════ 自行接單可被管理員撤銷 ═══════════════
def test_admin_can_revoke_a_volunteer_self_claim(db, line_outbox):
    vol, req, adm, res, need = world(db)
    press("U-vol", f"action=claim&need_id={need.id}")
    assert f"action=admin_revoke&need_id={need.id}" in card_text(line_outbox, "U-adm")
    press("U-adm", f"action=admin_revoke&need_id={need.id}")
    db.expire_all()
    n = db.query(CommunityNeed).filter(CommunityNeed.id == need.id).one()
    assert n.status == "open" and n.matched_resource_id is None
    assert any("撤銷" in t for t in sent_to(line_outbox, "U-vol"))
    assert any("已撤銷" in t for t in replies(line_outbox))
    press("U-adm", f"action=admin_revoke&need_id={need.id}")
    assert any("已經不在進行中" in t for t in replies(line_outbox)), "重複按不能出錯"


def test_only_admins_can_revoke(db, line_outbox):
    vol, req, adm, res, need = world(db)
    press("U-vol", f"action=claim&need_id={need.id}")
    press("U-vol", f"action=admin_revoke&need_id={need.id}")
    db.expire_all()
    assert db.query(CommunityNeed).one().status == "matched"
