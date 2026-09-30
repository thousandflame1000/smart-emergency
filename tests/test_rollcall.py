"""災時點名：緊急模式開新的一輪，長者的各種回報都算，後台看得到誰還沒回。"""
from fastapi.testclient import TestClient

from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from app.routers import dashboard
from app.services import checkin as checkin_svc
from app.services import rollcall
from app.timeutil import today_tw
from tests.test_line_hardening import mk, press, replies, say, sent_to


def _emergency(db):
    db.add(SystemConfig(key="mode", value="emergency")); db.commit()
    return rollcall.start(db)


def _status(db):
    return {p["name"]: p["status"] for p in rollcall.board(db)["people"]}


def test_no_roll_call_in_normal_mode(db):
    elder = mk(db, "阿嬤", ["elderly"], "U-a")
    assert rollcall.board(db) == {"active": False}
    assert rollcall.note(db, elder, "ok") is False


def test_emergency_asks_elders_and_others_get_the_usual_broadcast(db, line_outbox):
    mk(db, "阿嬤", ["elderly"], "U-a")
    mk(db, "志工", ["volunteer"], "U-v")
    _emergency(db)
    dashboard.broadcast_mode_change("emergency")
    assert sent_to(line_outbox, "U-a") == ["🚨 請回報是否平安"], "長者收點名卡，不重複收一般廣播"
    assert any("緊急模式啟動" in t for t in sent_to(line_outbox, "U-v"))


def test_every_way_of_answering_counts(db, line_outbox):
    mk(db, "按平安", ["elderly"], "U-ok")
    mk(db, "打字平安", ["elderly"], "U-typed")
    mk(db, "求救", ["elderly"], "U-help", lat=24.0, lng=120.6)
    mk(db, "不舒服", ["elderly"], "U-unwell")
    mk(db, "沒回", ["elderly"], "U-silent")
    _emergency(db)
    press("U-ok", "action=safe")
    assert "已回報平安" in replies(line_outbox)[-1]
    say("U-typed", "我平安")
    press("U-help", "action=confirm_sos")
    say("U-unwell", "不舒服")
    assert _status(db) == {"按平安": "ok", "打字平安": "ok", "求救": "help", "不舒服": "unwell", "沒回": "pending"}
    board = rollcall.board(db)
    assert board["counts"] == {"ok": 2, "unwell": 1, "help": 1, "pending": 1} and board["total"] == 5
    assert [p["name"] for p in board["people"]][:2] == ["求救", "沒回"], "需要協助的最前面，接著是還沒回的"


def test_a_morning_check_in_does_not_count_after_the_disaster(db, line_outbox):
    elder = mk(db, "早起阿公", ["elderly"], "U-early")
    checkin_svc.record_ok(db, elder)
    assert db.query(DailyCheckin).filter(DailyCheckin.date == today_tw()).one().status == "ok"
    _emergency(db)
    assert _status(db) == {"早起阿公": "pending"}


def test_family_confirmation_counts_for_the_elder(db, line_outbox):
    elder = mk(db, "阿公", ["elderly"], "U-elder")
    kid = mk(db, "女兒", ["family"], "U-kid")
    db.add(CareRelation(elderly_id=elder.id, contact_id=kid.id, relation="family"))
    checkin = DailyCheckin(elderly_id=elder.id, date=today_tw(), status="no_response")
    db.add(checkin); db.commit()
    _emergency(db)
    press("U-kid", f"action=confirm_safe&checkin_id={checkin.id}")
    person = rollcall.board(db)["people"][0]
    assert person["status"] == "ok" and person["via"] == "family" and person["marked_by"] == "女兒"


def test_console_marks_and_reminds_only_those_who_have_not_answered(db, line_outbox):
    from app.main import app
    answered = mk(db, "回了", ["elderly"], "U-yes")
    mk(db, "沒回", ["elderly"], "U-no")
    no_line = mk(db, "沒 LINE", ["elderly"])
    client = TestClient(app)
    assert client.post("/api/rollcall/remind").status_code == 409, "日常模式不能點名"
    _emergency(db)
    press("U-yes", "action=safe")
    line_outbox.sent.clear()
    r = client.post("/api/rollcall/remind")
    assert r.json()["sent"] == 1 and sent_to(line_outbox, "U-no") == ["🚨 請回報是否平安"] and not sent_to(line_outbox, "U-yes")
    assert client.post(f"/api/rollcall/{no_line.id}?status=ok").status_code == 200
    person = next(p for p in client.get("/api/rollcall").json()["people"] if p["name"] == "沒 LINE")
    assert person["status"] == "ok" and person["via"] == "console"
    assert client.post(f"/api/rollcall/{answered.id}?status=bad").status_code == 422


def test_a_new_emergency_starts_a_fresh_round(db, line_outbox):
    mk(db, "阿嬤", ["elderly"], "U-a")
    _emergency(db)
    press("U-a", "action=safe")
    assert _status(db) == {"阿嬤": "ok"}
    rollcall.start(db)
    assert _status(db) == {"阿嬤": "pending"}


def test_admins_see_the_roll_call_on_line(db, line_outbox):
    mk(db, "管理員", ["admin"], "U-boss")
    mk(db, "沒回的阿公", ["elderly"], "U-quiet", phone="0911000222")
    say("U-boss", "點名")
    assert "日常模式" in replies(line_outbox)[-1]
    _emergency(db)
    say("U-boss", "點名")
    text = replies(line_outbox)[-1]
    assert "還沒回 1" in text and "沒回的阿公" in text and "0911000222" in text
    say("U-boss", "決策中心")
    assert replies(line_outbox)[-1] == "決策中心"
    card = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    import json
    assert "災時點名" in json.dumps(card.contents.to_dict(), ensure_ascii=False)
