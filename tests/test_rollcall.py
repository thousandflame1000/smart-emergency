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
    assert board["counts"] == {"ok": 2, "unwell": 1, "help": 1, "helped": 0, "pending": 1} and board["total"] == 5
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


def test_bulk_vulnerability_matches_the_per_person_score(db):
    """點名看板一次算全部人的脆弱度，結果要跟派遣用的逐人計算一樣。"""
    from datetime import timedelta
    from app.models.alert import Alert
    from app.services.dispatch import _vulnerability_pts, vulnerability_scorer
    lonely = mk(db, "獨居", ["elderly"], "U-lonely")
    cared = mk(db, "有人顧", ["elderly"], "U-cared")
    kid1, kid2 = mk(db, "子一", ["family"], "U-k1"), mk(db, "子二", ["family"], "U-k2")
    db.add_all([CareRelation(elderly_id=cared.id, contact_id=kid1.id, relation="family"),
                CareRelation(elderly_id=cared.id, contact_id=kid2.id, relation="family")])
    for days in (1, 2, 20):
        db.add(DailyCheckin(elderly_id=lonely.id, date=today_tw() - timedelta(days=days), status="no_response"))
    db.commit()
    checkin = db.query(DailyCheckin).filter(DailyCheckin.elderly_id == lonely.id).first()
    db.add(Alert(elderly_id=lonely.id, checkin_id=checkin.id, alert_type="no_response_3h", status="sent")); db.commit()
    score = vulnerability_scorer(db)
    for person in (lonely, cared, kid1):
        assert score(person.id) == _vulnerability_pts(person.id, db), person.name
    assert score(lonely.id) > score(cared.id)


def test_volunteers_check_on_unanswered_elders_near_them(db, line_outbox):
    """災時志工傳「附近點名」：列出附近還沒回報的長者，上門確認後直接回報。"""
    from app.models.need import CommunityNeed
    mk(db, "志工", ["volunteer"], "U-helper", lat=23.6650, lng=121.4180)
    mk(db, "管理員", ["admin"], "U-boss")
    mk(db, "隔壁阿嬤", ["elderly"], "U-next", lat=23.6660, lng=121.4185, address="大進村 1 號")
    far = mk(db, "遠方阿公", ["elderly"], "U-far", lat=23.7500, lng=121.4185)
    mk(db, "回過了", ["elderly"], "U-done", lat=23.6655, lng=121.4181)
    mk(db, "居民", ["elderly"], "U-res", lat=23.6650, lng=121.4180)
    say("U-helper", "附近點名")
    assert "日常模式" in replies(line_outbox)[-1]
    _emergency(db)
    press("U-done", "action=safe")
    say("U-res", "附近點名")
    assert "給已核准志工" in replies(line_outbox)[-1]
    say("U-helper", "附近點名")
    message = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    import json
    card = json.dumps(message.contents.to_dict(), ensure_ascii=False)
    assert "隔壁阿嬤" in card and "遠方阿公" not in card and "回過了" not in card
    elder = next(p for p in rollcall.board(db)["people"] if p["name"] == "隔壁阿嬤")
    press("U-helper", f"action=rc_mark&user_id={elder['id']}&s=ok")
    person = next(p for p in rollcall.board(db)["people"] if p["name"] == "隔壁阿嬤")
    assert person["status"] == "ok" and person["marked_by"] == "志工 志工"
    press("U-helper", f"action=rc_mark&user_id={far.id}&s=help")
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    db.refresh(need)
    assert need.requester_id == far.id and "上門確認" in need.description and need.responder.name == "志工"
    assert any("上門確認 遠方阿公 需要協助" in t for t in sent_to(line_outbox, "U-boss"))


def test_volunteers_are_told_about_nearby_roll_call_in_the_emergency_broadcast(db, line_outbox):
    mk(db, "志工", ["volunteer"], "U-v2")
    _emergency(db)
    dashboard.broadcast_mode_change("emergency")
    assert any("附近點名" in t for t in sent_to(line_outbox, "U-v2"))


def test_volunteer_reporting_help_for_an_elder_with_an_open_sos(db, line_outbox):
    from app.models.need import CommunityNeed
    from app.routers import linebot as lb
    mk(db, "志工甲", ["volunteer"], "U-va", lat=23.665, lng=121.418)
    mk(db, "志工乙", ["volunteer"], "U-vb", lat=23.665, lng=121.418)
    elder = mk(db, "阿公", ["elderly"], "U-ag", lat=23.6652, lng=121.4182)
    _emergency(db)
    lb._trigger_sos(elder, db)
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    press("U-va", f"action=rc_mark&user_id={elder.id}&s=help")
    db.refresh(need)
    assert need.responder.name == "志工甲", "還沒人受理的既有求救，由上門的志工接手"
    assert db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").count() == 1, "不開第二張"
    press("U-vb", f"action=rc_mark&user_id={elder.id}&s=help")
    assert "已由 志工甲 處理" in replies(line_outbox)[-1]


def test_double_tap_on_the_roll_call_does_not_fail(db):
    elder = mk(db, "阿嬤", ["elderly"], "U-tap")
    _emergency(db)
    assert rollcall.note(db, elder, "ok") and rollcall.note(db, elder, "ok")
    assert _status(db) == {"阿嬤": "ok"}


def test_roll_call_follows_up_automatically_and_reports_to_admins(db, line_outbox):
    from datetime import timedelta
    from app.timeutil import now_utc
    mk(db, "管理員", ["admin"], "U-boss2")
    mk(db, "回了", ["elderly"], "U-yes2")
    mk(db, "沒回", ["elderly"], "U-no2")
    db.add(SystemConfig(key="mode", value="emergency")); db.commit()
    start = now_utc() - timedelta(minutes=35)
    db.add(SystemConfig(key=rollcall.ROUND_KEY, value=start.isoformat())); db.commit()
    press("U-yes2", "action=safe")
    line_outbox.sent.clear()
    assert rollcall.auto_follow_up(db) == 1, "30 分鐘的追蹤"
    assert sent_to(line_outbox, "U-no2") == ["🚨 請回報是否平安"] and not sent_to(line_outbox, "U-yes2")
    assert any("點名 30 分鐘" in t and "還沒回 1" in t for t in sent_to(line_outbox, "U-boss2"))
    assert rollcall.auto_follow_up(db) == 0, "同一個時間點只做一次"
    row = db.query(SystemConfig).filter(SystemConfig.key == rollcall.ROUND_KEY).one()
    row.value = (now_utc() - timedelta(minutes=95)).isoformat(); db.commit()
    line_outbox.sent.clear()
    assert rollcall.auto_follow_up(db) == 1, "新的一輪兩個時間點同時到期：只跑最後一個"
    assert sent_to(line_outbox, "U-no2") == ["🚨 請回報是否平安"], "長者不會連收兩張一樣的卡"
    assert rollcall.auto_follow_up(db) == 0


def test_no_follow_up_outside_an_emergency(db):
    assert rollcall.auto_follow_up(db) == 0


def test_admin_can_switch_emergency_mode_from_line_with_confirmation(db, line_outbox):
    """災害時管理員可能只有手機：LINE 上也能切模式，但要先確認，而且會記進操作紀錄與時間軸。"""
    from app.models.admin_audit import AdminAudit
    from app.routers.linebot import _get_mode
    mk(db, "管理員", ["admin"], "U-boss3")
    mk(db, "阿嬤", ["elderly"], "U-granny3")
    mk(db, "居民", ["elderly"], "U-plain3")
    say("U-plain3", "緊急模式")
    assert "僅限管理員" in replies(line_outbox)[-1]
    say("U-boss3", "緊急模式")
    assert replies(line_outbox)[-1] == "啟動緊急模式？"
    press("U-boss3", "action=admin_mode&m=cancel")
    assert "已取消" in replies(line_outbox)[-1] and _get_mode(db) == "normal"
    press("U-boss3", "action=admin_mode&m=emergency")
    assert "已啟動緊急模式" in replies(line_outbox)[-1]
    db.expire_all()
    assert _get_mode(db) == "emergency" and rollcall.current_round(db)
    press("U-boss3", "action=admin_mode&m=emergency")
    assert "已經是緊急模式" in replies(line_outbox)[-1]
    say("U-boss3", "解除緊急模式")
    assert replies(line_outbox)[-1] == "解除緊急模式？"
    press("U-boss3", "action=admin_mode&m=normal")
    db.expire_all()
    assert _get_mode(db) == "normal"
    logged = [a.path for a in db.query(AdminAudit).filter(AdminAudit.method == "LINE").all()]
    assert logged == ["啟動緊急模式", "解除緊急模式"], "取消與重複按的不記"


def test_field_staff_report_shelter_headcount_from_line(db, line_outbox):
    import json
    from app.models.admin_audit import AdminAudit
    from app.models.resource_point import ResourcePoint
    mk(db, "收容所志工", ["field_staff"], "U-staff", lat=23.665, lng=121.418)
    mk(db, "居民", ["elderly"], "U-res2")
    school = ResourcePoint(name="光復國小", point_type="shelter", lat=23.671, lng=121.425, capacity=6, current_load=0)
    db.add(school); db.commit()
    say("U-res2", "收容")
    assert "查詢物資" in replies(line_outbox)[-1]
    say("U-staff", "收容")
    card = json.dumps([m for kind, _to, m in line_outbox.sent if kind == "reply"][-1].contents.to_dict(), ensure_ascii=False)
    assert "光復國小" in card and f"action=shelter&id={school.id}&d=p5" in card
    press("U-staff", f"action=shelter&id={school.id}&d=p5")
    press("U-staff", f"action=shelter&id={school.id}&d=p1")
    assert replies(line_outbox)[-1] == "光復國小 現在 6 人"
    last = json.dumps([m for kind, _to, m in line_outbox.sent if kind == "reply"][-1].contents.to_dict(), ensure_ascii=False)
    assert "已滿" in last
    press("U-staff", f"action=shelter&id={school.id}&d=m5")
    press("U-staff", f"action=shelter&id={school.id}&d=m5")
    db.refresh(school)
    assert school.current_load == 0, "不會變成負數"
    press("U-res2", f"action=shelter&id={school.id}&d=p5")
    db.refresh(school)
    assert school.current_load == 0, "居民不能改"
    assert db.query(AdminAudit).filter(AdminAudit.path.like("收容人數 光復國小%")).count() == 4


def test_roll_call_exports_as_csv_that_excel_opens(db, line_outbox):
    from app.main import app
    from fastapi.testclient import TestClient
    mk(db, "回了", ["elderly"], "U-csv-yes", phone="0911000111", address="大進村 1 號")
    mk(db, "沒回", ["elderly"], "U-csv-no")
    _emergency(db)
    press("U-csv-yes", "action=safe")
    r = TestClient(app).get("/api/rollcall/export.csv")
    assert r.status_code == 200 and r.headers["content-type"].startswith("text/csv")
    text = r.content.decode("utf-8")
    assert text.startswith("\ufeff姓名,狀態"), "Excel 要有 BOM 才不會亂碼"
    assert "回了,平安" in text and "0911000111" in text and "沒回,還沒回" in text
    import re
    assert re.search(r"LINE,,20\d\d-\d\d-\d\d \d\d:\d\d", text), "回報時間用台灣時間、到分鐘"


def test_console_marking_help_opens_an_sos(db, line_outbox):
    from app.main import app
    from app.models.need import CommunityNeed
    from fastapi.testclient import TestClient
    elder = mk(db, "電話確認的阿公", ["elderly"], None, lat=23.665, lng=121.418, address="大進村 7 號")
    mk(db, "附近志工", ["volunteer"], "U-help-vol", lat=23.666, lng=121.418)
    _emergency(db)
    client = TestClient(app)
    r = client.post(f"/api/rollcall/{elder.id}?status=help")
    assert r.status_code == 200 and "開了求救單" in r.json()["message"] and "附近 1 位志工" in r.json()["message"]
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    assert need.requester_id == elder.id and "電話確認" in need.description
    assert sent_to(line_outbox, "U-help-vol") == ["🆘 附近有人需要幫忙"]
    again = client.post(f"/api/rollcall/{elder.id}?status=help").json()["message"]
    assert "已經有求救單" in again and db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").count() == 1
    person = next(p for p in client.get("/api/rollcall").json()["people"] if p["name"] == "電話確認的阿公")
    assert person["status"] == "help"


def test_elder_whose_sos_was_closed_shows_as_helped_not_still_needing_help(db, line_outbox):
    """需要協助的人求救結案後改列「已協助」：附近點名不再叫志工去，進度報告也不再列他；再求救就回到需要協助。"""
    from app.models.need import CommunityNeed
    mk(db, "跌倒的阿嬤", ["elderly"], "U-fell", lat=23.665, lng=121.418, address="大進村 9 號")
    near = mk(db, "附近志工", ["volunteer"], "U-near", lat=23.666, lng=121.418)
    mk(db, "管理員", ["admin"], "U-boss")
    _emergency(db)
    press("U-fell", "action=confirm_sos")
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    assert _status(db)["跌倒的阿嬤"] == "help"
    press("U-near", f"action=sos_go&need_id={need.id}")
    assert _status(db)["跌倒的阿嬤"] == "help", "受理了還沒結案，仍然算需要協助"
    press("U-near", f"action=sos_done&need_id={need.id}")

    board = rollcall.board(db)
    person = next(p for p in board["people"] if p["name"] == "跌倒的阿嬤")
    assert (person["status"], person["status_label"]) == ("helped", "已協助")
    assert board["counts"]["help"] == 0 and board["counts"]["helped"] == 1
    assert rollcall.nearby_unanswered(db, near) == []
    assert rollcall.counts_line(board["counts"]).endswith("已協助 1")

    press("U-fell", "action=confirm_sos")                              # 又需要幫忙
    assert _status(db)["跌倒的阿嬤"] == "help"
    assert db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos", CommunityNeed.status == "open").count() == 1


def test_sos_opened_before_the_emergency_and_closed_during_it_counts_as_helped(db, line_outbox):
    """點名前就開著的求救，後台標需要協助會沿用那一張；在這一輪結案也算已協助。"""
    from app.main import app
    from app.models.need import CommunityNeed
    elder = mk(db, "早上就求救的阿公", ["elderly"], None, lat=23.665, lng=121.418)
    db.add(CommunityNeed(requester_id=elder.id, need_type="sos", status="open", lat=23.665, lng=121.418))
    db.commit()
    _emergency(db)
    client = TestClient(app)
    assert "已經有求救單" in client.post(f"/api/rollcall/{elder.id}?status=help").json()["message"]
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    assert client.post(f"/api/resources/needs/{need.id}/resolve_sos").status_code == 200
    db.expire_all()
    assert _status(db)["早上就求救的阿公"] == "helped"


def test_people_who_answered_a_roll_call_or_took_an_sos_can_still_be_deleted(db, line_outbox):
    """點名紀錄與求救處理人是後來加的外鍵；刪除使用者時要一起處理，不然正式站會擋下刪除。"""
    from app.models.need import CommunityNeed
    from app.models.safety_check import SafetyCheck
    from app.models.user import User
    from app.services.user_deletion import UserDeletionBlocked, delete_user_data
    import pytest
    mk(db, "回報過的阿嬤", ["elderly"], "U-answered")
    helper = mk(db, "處理過的志工", ["volunteer"], "U-helper2")
    other = mk(db, "另一位長者", ["elderly"], "U-other2")
    _emergency(db)
    press("U-answered", "action=safe")
    done = CommunityNeed(requester_id=other.id, need_type="sos", status="fulfilled", responder_id=helper.id)
    live = CommunityNeed(requester_id=other.id, need_type="sos", status="open", responder_id=helper.id)
    db.add_all([done, live]); db.commit()

    with pytest.raises(UserDeletionBlocked, match="正在處理 1 筆求救"):
        delete_user_data(db, helper)
    live.status = "fulfilled"; db.commit()
    delete_user_data(db, helper)
    db.expire_all()
    assert db.query(CommunityNeed).filter(CommunityNeed.responder_id.isnot(None)).count() == 0

    elder = db.query(User).filter(User.line_uid == "U-answered").one()
    delete_user_data(db, elder)
    assert db.query(SafetyCheck).count() == 0 and db.query(User).filter(User.line_uid == "U-answered").count() == 0


def _last_card(line_outbox):
    import json
    message = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    return json.dumps(message.contents.to_dict(), ensure_ascii=False)


def test_family_sees_this_disasters_roll_call_not_this_mornings_check_in(db, line_outbox):
    """早上打卡平安，災害發生後還沒回報：家屬不能看到「已回報平安」；家屬聯絡到人可以直接代為回報。"""
    from app.services import checkin as checkin_svc
    elder = mk(db, "阿公", ["elderly"], "U-gpa")
    kid = mk(db, "女兒", ["family"], "U-kid")
    mk(db, "路人", ["family"], "U-stranger")
    db.add(CareRelation(elderly_id=elder.id, contact_id=kid.id, relation="family")); db.commit()
    checkin_svc.record_ok(db, elder)
    say("U-kid", "長輩狀況")
    assert "已回報平安" in _last_card(line_outbox)

    _emergency(db)
    say("U-kid", "長輩狀況")
    card = _last_card(line_outbox)
    assert "還沒回報這次是否平安" in card and f"action=rc_family&elder_id={elder.id}" in card

    press("U-stranger", f"action=rc_family&elder_id={elder.id}")
    assert "照護聯絡人" in replies(line_outbox)[-1] and _status(db)["阿公"] == "pending"
    press("U-kid", f"action=rc_family&elder_id={elder.id}")
    assert replies(line_outbox)[-1] == "✅ 已回報 阿公 這次平安，社區看得到。"
    person = rollcall.board(db)["people"][0]
    assert (person["status"], person["via"], person["marked_by"]) == ("ok", "family", "女兒")
    say("U-kid", "長輩狀況")
    card = _last_card(line_outbox)
    assert "這次已回報平安" in card and "rc_family" not in card


def test_family_sees_a_closed_sos_as_handled(db, line_outbox):
    from app.models.need import CommunityNeed
    elder = mk(db, "阿嬤", ["elderly"], "U-gma", lat=23.66, lng=121.42)
    kid = mk(db, "兒子", ["family"], "U-son")
    db.add(CareRelation(elderly_id=elder.id, contact_id=kid.id, relation="family")); db.commit()
    db.add(DailyCheckin(elderly_id=elder.id, date=today_tw(), status="pending")); db.commit()
    press("U-gma", "action=confirm_sos")
    say("U-son", "長輩狀況")
    assert "求助中" in _last_card(line_outbox)
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    from app.services.dispatch import resolve_sos
    resolve_sos(str(need.id), db)
    say("U-son", "長輩狀況")
    assert "今天的求助已結案" in _last_card(line_outbox)

    _emergency(db)
    press("U-gma", "action=confirm_sos")
    say("U-son", "長輩狀況")
    assert "需要協助（求救處理中）" in _last_card(line_outbox)
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos", CommunityNeed.status == "open").one()
    resolve_sos(str(need.id), db)
    say("U-son", "長輩狀況")
    assert "求救已處理完成" in _last_card(line_outbox)
