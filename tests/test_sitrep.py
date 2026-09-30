"""災情摘要：往上回報用的一頁，數字要跟系統裡的紀錄對得上。"""
from datetime import timedelta

from fastapi.testclient import TestClient

from app.models.config import SystemConfig
from app.models.need import CommunityNeed
from app.services import rollcall, sitrep
from app.timeutil import now_utc
from tests.test_line_hardening import mk, press


def test_emergency_report_counts_roll_call_sos_and_needs(db, line_outbox):
    admin = mk(db, "管理員", ["admin"], "U-boss")
    safe = mk(db, "平安阿嬤", ["elderly"], "U-safe")
    mk(db, "沒回阿公", ["elderly"], "U-quiet", phone="0911000333", address="大進村 3 號")
    db.add(SystemConfig(key="mode", value="emergency")); db.commit()
    rollcall.start(db)
    press("U-safe", "action=safe")
    now = now_utc().replace(tzinfo=None)
    taken = CommunityNeed(requester_id=safe.id, need_type="sos", status="open", created_at=now,
                          responder_id=admin.id, acknowledged_at=now + timedelta(minutes=4))
    waiting = CommunityNeed(requester_id=safe.id, need_type="sos", status="open", created_at=now)
    water = CommunityNeed(requester_id=safe.id, need_type="water", status="fulfilled", created_at=now)
    food = CommunityNeed(requester_id=safe.id, need_type="food", status="open", created_at=now)
    db.add_all([taken, waiting, water, food]); db.commit()

    report = sitrep.build(db)
    assert report["mode"] == "緊急模式" and "緊急模式啟動" in report["period"]
    assert report["rollcall"]["counts"]["ok"] == 1 and report["rollcall"]["counts"]["pending"] == 1
    assert [p["name"] for p in report["rollcall"]["follow_up"]] == ["沒回阿公"]
    s = report["sos"]
    assert (s["total"], s["waiting"], s["in_progress"], s["closed"]) == (2, 1, 1, 0)
    assert s["median_ack_min"] == 4.0
    assert report["needs"]["by_type"]["飲用水"] == {"total": 1, "open": 0, "done": 1}
    assert report["needs"]["by_type"]["食物"] == {"total": 1, "open": 1, "done": 0}


def test_normal_day_report_has_no_roll_call(db):
    report = sitrep.build(db)
    assert report["mode"] == "日常模式" and report["rollcall"] is None and report["period"].startswith("今日")


def test_report_page_and_api(db):
    from app.main import app
    client = TestClient(app)
    assert "災情摘要" in client.get("/view/sitrep").text
    assert client.get("/api/dashboard/sitrep").json()["sos"]["total"] == 0


def test_cases_still_open_from_before_the_period_stay_in_the_report(db):
    elder = mk(db, "阿嬤", ["elderly"], "U-old")
    old = now_utc().replace(tzinfo=None) - timedelta(days=2)
    db.add_all([CommunityNeed(requester_id=elder.id, need_type="sos", status="open", created_at=old),
                CommunityNeed(requester_id=elder.id, need_type="water", status="fulfilled", created_at=old)])
    db.commit()
    report = sitrep.build(db)
    assert report["sos"]["waiting"] == 1, "兩天前的求救還沒人處理，一定要在報告上"
    assert report["needs"]["total"] == 0, "早就結案的舊需求不算進這一期"


def test_timeline_lists_what_happened_newest_first(db, line_outbox):
    from app.main import app
    from app.routers import linebot as lb
    mk(db, "管理員", ["admin"], "U-boss")
    elder = mk(db, "阿公", ["elderly"], "U-elder", lat=23.665, lng=121.418)
    client = TestClient(app)
    client.post("/api/dashboard/mode?mode=emergency")
    lb._trigger_sos(elder, db)
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    press("U-boss", f"action=sos_take&need_id={need.id}")
    client.post(f"/api/resources/needs/{need.id}/reported_119")
    events = [t["what"] for t in sitrep.build(db)["timeline"]]
    assert {"轉報 119", "受理求救", "點名回報：需要協助", "通報求救", "啟動緊急模式"} <= set(events)
    assert sitrep.actor_name("admin:王小明") == "管理員：王小明" and sitrep.actor_name("manager") == "後台"


def test_report_lists_each_shelter_fullest_first(db):
    from app.models.resource_point import ResourcePoint
    db.add_all([ResourcePoint(name="空的", point_type="shelter", capacity=100, current_load=5),
                ResourcePoint(name="快滿", point_type="shelter", capacity=10, current_load=9)])
    db.commit()
    rows = sitrep.build(db)["shelters"]["rows"]
    assert [r["name"] for r in rows] == ["快滿", "空的"]
