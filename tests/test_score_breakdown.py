# -*- coding: utf-8 -*-
"""
等待時間懲罰（deprivation-cost 簡化版）與候選分數分解攤開，
兩者都是研究背景任務找到的低成本、有文獻依據的改動
（見 RESEARCH_disaster_logistics.md 第 7 節）。
"""
from datetime import datetime, timedelta

from app.database import SessionLocal
from app.models.user import User
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.services import dispatch


def _make_pair(db, need_created_at):
    vol = User(name="志工", roles=["volunteer"], lat=24.150, lng=120.670)
    db.add(vol); db.commit()
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="水",
                             lat=24.150, lng=120.670, is_available=True)
    db.add(res); db.commit()

    elder = User(name="長者", roles=["elderly"], lat=24.150, lng=120.670)
    db.add(elder); db.commit()

    need = CommunityNeed(requester_id=elder.id, need_type="water", urgency=3,
                          lat=24.150, lng=120.670, status="open", created_at=need_created_at)
    db.add(need); db.commit()
    return need


def test_longer_wait_increases_score(db):
    now = datetime.utcnow()
    need_fresh = _make_pair(db, now)
    need_old = _make_pair(db, now - timedelta(hours=10))

    fresh_result = dispatch.preview_candidates(str(need_fresh.id), db)
    old_result = dispatch.preview_candidates(str(need_old.id), db)

    assert old_result["candidates"][0]["score"] > fresh_result["candidates"][0]["score"], (
        "同樣條件下，開得更久的需求分數應該更高（deprivation cost 概念）"
    )
    assert old_result["candidates"][0]["breakdown"]["wait_pts"] > 0


def test_wait_pts_is_capped(db):
    now = datetime.utcnow()
    need = _make_pair(db, now - timedelta(hours=1000))  # 遠超上限
    result = dispatch.preview_candidates(str(need.id), db)
    assert result["candidates"][0]["breakdown"]["wait_pts"] == dispatch.WAIT_PTS_CAP


def test_preview_candidates_exposes_score_breakdown(db):
    need = _make_pair(db, datetime.utcnow())
    result = dispatch.preview_candidates(str(need.id), db)
    breakdown = result["candidates"][0]["breakdown"]
    for key in ("urgency_pts", "vulnerability_pts", "affinity_pts", "wait_pts",
                "dist_penalty", "load_penalty", "total"):
        assert key in breakdown, f"候選分數分解應該包含 {key}，管理員才能稽核判斷依據"
