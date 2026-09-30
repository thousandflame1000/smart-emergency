"""一鍵求救的處理流程：通知附近志工、第一個人受理、逾時升級、結案、後台指派。"""
import json
from datetime import timedelta

from fastapi.testclient import TestClient

from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.routers import linebot as lb
from app.services import sos
from app.timeutil import now_utc
from tests.test_line_hardening import mk, press, replies, sent_to

ALERT = "🆘 附近有人需要幫忙"


def _world(db):
    elder = mk(db, "陳阿公", ["elderly"], "U-elder", lat=23.6650, lng=121.4180,
               address="光復鄉大進村 12 號", phone="0911222333")
    near = mk(db, "近志工", ["volunteer"], "U-near", lat=23.6690, lng=121.4180)   # 約 445 公尺
    mid = mk(db, "中志工", ["volunteer"], "U-mid", lat=23.6800, lng=121.4180)     # 約 1.7 公里
    far = mk(db, "遠志工", ["volunteer"], "U-far", lat=23.7200, lng=121.4180)     # 約 6 公里
    mk(db, "鄰居", ["elderly"], "U-res", lat=23.6651, lng=121.4181)    # 不是志工
    admin = mk(db, "管理員", ["admin"], "U-admin")
    lb._trigger_sos(elder, db)
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    return need, elder, near, mid, far, admin


def _card(outbox, uid):
    message = [m for kind, to, m in outbox.sent if kind == "push" and to == uid][-1]
    return json.dumps(message.contents.to_dict(), ensure_ascii=False)


def test_sos_alerts_only_volunteers_within_reach(db, line_outbox):
    need, *_ = _world(db)
    assert sent_to(line_outbox, "U-near") == [ALERT] and sent_to(line_outbox, "U-mid") == [ALERT]
    assert not sent_to(line_outbox, "U-far") and not sent_to(line_outbox, "U-res"), "太遠的志工與一般居民不叫"
    card = _card(line_outbox, "U-near")
    assert f"action=sos_go&need_id={need.id}" in card and "公尺）" in card
    assert "0911222333" not in card, "還沒受理前不給電話"
    assert "也通知了附近 2 位志工" in lb._sos_reply_text({"contacts": 0, "admins": 1, "nearby": 2})


def test_first_volunteer_to_press_takes_it_and_later_ones_are_told(db, line_outbox):
    need, elder, near, mid, far, admin = _world(db)
    press("U-mid", f"action=sos_go&need_id={need.id}")
    press("U-near", f"action=sos_go&need_id={need.id}")
    db.expire_all()
    assert str(need.responder_id) == str(mid.id) and need.acknowledged_at is not None and need.status == "open"
    assert replies(line_outbox)[-2] == "由您處理這筆求救"
    assert "已經有 中志工 在處理了" in replies(line_outbox)[-1]
    reply_card = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-2]
    assert "tel:0911222333" in json.dumps(reply_card.contents.to_dict(), ensure_ascii=False), "受理後才給電話"
    assert any("中志工 已受理 陳阿公 的求救" in t for t in sent_to(line_outbox, "U-admin"))
    assert db.query(DispatchEvent).filter(DispatchEvent.action == "sos_acknowledged").count() == 1


def test_only_volunteers_and_admins_can_take(db, line_outbox):
    need, *_ = _world(db)
    press("U-res", f"action=sos_go&need_id={need.id}")
    assert "只有已核准的志工" in replies(line_outbox)[-1]
    press("U-near", f"action=sos_take&need_id={need.id}")
    assert "僅限管理員" in replies(line_outbox)[-1]
    db.expire_all()
    assert need.responder_id is None


def test_only_the_responder_or_an_admin_can_close(db, line_outbox):
    need, *_ = _world(db)
    press("U-mid", f"action=sos_go&need_id={need.id}")
    press("U-near", f"action=sos_done&need_id={need.id}")
    assert "只有受理這筆求救的人或管理員可以結案" in replies(line_outbox)[-1]
    press("U-mid", f"action=sos_done&need_id={need.id}")
    db.expire_all()
    assert need.status == "fulfilled"
    assert any("中志工 回報 陳阿公 的求救已處理完成" in t for t in sent_to(line_outbox, "U-admin"))
    press("U-near", f"action=sos_go&need_id={need.id}")
    assert "已經處理完成" in replies(line_outbox)[-1]


def test_admin_takes_from_the_line_card(db, line_outbox):
    need, *_ = _world(db)
    press("U-admin", f"action=sos_take&need_id={need.id}")
    db.expire_all()
    assert need.responder.name == "管理員" and replies(line_outbox)[-1] == "由您處理這筆求救"


def test_unacknowledged_sos_is_escalated_once(db, line_outbox):
    need, *_ = _world(db)
    assert sos.escalate_unacknowledged(db) == 0, "剛通報的不升級"
    need.created_at = now_utc().replace(tzinfo=None) - timedelta(minutes=sos.ESCALATE_MINUTES + 1)
    db.commit()
    assert sos.escalate_unacknowledged(db) == 1
    assert sos.escalate_unacknowledged(db) == 0, "每筆只再叫一次"
    assert any("超過 10 分鐘沒有人受理" in t for t in sent_to(line_outbox, "U-admin"))


def test_acknowledged_sos_is_not_escalated(db, line_outbox):
    need, *_ = _world(db)
    press("U-near", f"action=sos_go&need_id={need.id}")
    db.expire_all()
    need.created_at = now_utc().replace(tzinfo=None) - timedelta(minutes=30)
    db.commit()
    assert sos.escalate_unacknowledged(db) == 0


def test_console_assigns_a_responder_who_gets_the_card(db, line_outbox):
    from app.main import app
    from app.services.workspace_bridge import operational_snapshot
    need, elder, near, mid, far, admin = _world(db)
    client = TestClient(app)
    people = client.get(f"/api/resources/needs/{need.id}/sos_candidates").json()["candidates"]
    assert [p["name"] for p in people] == ["近志工", "中志工", "遠志工", "管理員"], "志工由近到遠，管理員最後"
    r = client.post(f"/api/resources/needs/{need.id}/assign_sos?user_id={far.id}")
    assert r.status_code == 200 and "已傳 LINE 任務卡" in r.json()["message"]
    assert sent_to(line_outbox, "U-far")[-1] == "由您處理這筆求救"
    again = client.post(f"/api/resources/needs/{need.id}/assign_sos?user_id={near.id}")
    assert again.status_code == 409 and "遠志工" in again.json()["detail"]
    db.expire_all()
    node = next(n for n in operational_snapshot(db)["graph"]["nodes"] if n["id"] == f"db:need:{need.id}")
    assert node["properties"]["responder"] == "遠志工" and node["properties"]["acknowledged_at"].endswith("+00:00")
    assert client.get("/api/dashboard/summary").json()["open_sos"][0]["responder"] == "遠志工"
    bad = client.post(f"/api/resources/needs/{need.id}/assign_sos?user_id={elder.id}")
    assert bad.status_code == 400


def test_family_can_ask_nearby_volunteers_to_check_on_an_elder(db, line_outbox):
    """長輩沒有手機或昏倒按不了求救：家屬按「請附近志工去看看」，走同一條求救流程。"""
    from app.models.care_relation import CareRelation
    elder = mk(db, "獨居阿公", ["elderly"], None, lat=23.6650, lng=121.4180, address="大進村 5 號", phone="038701111")
    kid = mk(db, "兒子", ["family"], "U-kid")
    mk(db, "路人", ["elderly"], "U-stranger")
    mk(db, "近志工", ["volunteer"], "U-near", lat=23.6690, lng=121.4180)
    mk(db, "管理員", ["admin"], "U-admin")
    db.add(CareRelation(elderly_id=elder.id, contact_id=kid.id, relation="family")); db.commit()
    press("U-stranger", f"action=family_check&elder_id={elder.id}")
    assert "只有這位長輩的照護聯絡人" in replies(line_outbox)[-1]
    press("U-kid", f"action=family_check&elder_id={elder.id}")
    assert "已請附近 1 位志工與社區管理員去看看 獨居阿公" in replies(line_outbox)[-1]
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    assert "家屬 兒子 聯絡不到" in need.description and need.lat == 23.6650
    assert sent_to(line_outbox, "U-near") == [ALERT]
    assert any("聯絡不到 獨居阿公" in t for t in sent_to(line_outbox, "U-admin"))
    press("U-kid", f"action=family_check&elder_id={elder.id}")
    assert "已經有人在處理" in replies(line_outbox)[-1], "重複按不會開第二張單"
    press("U-near", f"action=sos_go&need_id={need.id}")
    assert any("近志工 已經要去看 獨居阿公" in t for t in sent_to(line_outbox, "U-kid"))
    press("U-near", f"action=sos_done&need_id={need.id}")
    assert any("獨居阿公 的狀況已處理完成" in t for t in sent_to(line_outbox, "U-kid"))


def test_the_check_on_button_is_on_family_cards(db, line_outbox):
    from app.models.care_relation import CareRelation
    from app.services.line_notify import send_alert_message
    elder = mk(db, "阿嬤", ["elderly"], None)
    kid = mk(db, "女兒", ["family"], "U-dau")
    db.add(CareRelation(elderly_id=elder.id, contact_id=kid.id, relation="family")); db.commit()
    from tests.test_line_hardening import say
    say("U-dau", "長輩狀況")
    card = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    assert f"action=family_check&elder_id={elder.id}" in json.dumps(card.contents.to_dict(), ensure_ascii=False)
    send_alert_message("U-dau", "阿嬤", "no_response_3h", "c1", elderly=elder)
    pushed = [m for kind, to, m in line_outbox.sent if kind == "push" and to == "U-dau"][-1]
    assert "family_check" in json.dumps(pushed.contents.to_dict(), ensure_ascii=False)


def test_console_logs_when_an_sos_was_reported_to_119(db, line_outbox):
    from app.main import app
    need, *_ = _world(db)
    client = TestClient(app)
    assert client.post(f"/api/resources/needs/{need.id}/reported_119").status_code == 200
    events = client.get(f"/api/resources/needs/{need.id}/events").json()
    assert any(e["action"] == "sos_reported_119" for e in events)
    other = CommunityNeed(requester_id=need.requester_id, need_type="water", status="open")
    db.add(other); db.commit()
    assert client.post(f"/api/resources/needs/{other.id}/reported_119").status_code == 404


def test_admin_sos_card_carries_triage_details(db, line_outbox, monkeypatch):
    from app.services import aed
    monkeypatch.setattr(aed, "aeds", lambda: (aed.Aed("活動中心", "大廳", 23.6652, 121.4181, "00:00-23:59",
                                                     "00:00-23:59", "00:00-23:59", "", None),))
    _world(db)
    message = [m for kind, to, m in line_outbox.sent if kind == "push" and to == "U-admin"][-1]
    card = json.dumps(message.contents.to_dict(), ensure_ascii=False)
    assert "脆弱度" in card and "已通知附近 2 位志工" in card and "最近的 AED：活動中心" in card


def test_a_failed_nearby_alert_is_retried_from_the_outbox(db, line_outbox, monkeypatch):
    """LINE 暫時出錯時，附近志工的求救卡不能就這樣沒了：存在寄件佇列，排程重送。"""
    from datetime import timedelta
    from app.models.outbox import OutboxMessage
    from app.services.outbox import OutboxWorker
    real_push = line_outbox.push_message
    calls = {"n": 0}

    def flaky(request):
        calls["n"] += 1
        if request.to == "U-near" and calls.get("failed") is None:
            calls["failed"] = True
            raise RuntimeError("LINE 503")
        real_push(request)
    monkeypatch.setattr(line_outbox, "push_message", flaky)
    need, *_ = _world(db)
    assert not sent_to(line_outbox, "U-near"), "第一次推送失敗"
    row = db.query(OutboxMessage).filter(OutboxMessage.dedupe_key.like(f"sos-nearby:{need.id}:%"),
                                         OutboxMessage.destination == "U-near").one()
    assert row.status == "FAILED" and row.message_type == "FLEX"
    worker = OutboxWorker(db, worker_id="retry-test")
    sent = worker.process_next(now=row.available_at + timedelta(seconds=1))
    assert sent.status == "SENT" and sent_to(line_outbox, "U-near") == [ALERT]


def test_console_can_message_the_sos_responder(db, line_outbox):
    from app.main import app
    need, elder, near, mid, far, admin = _world(db)
    client = TestClient(app)
    early = client.post(f"/api/resources/needs/{need.id}/message_assignee", json={"text": "救護車快到了"})
    assert early.status_code == 409 and "還沒有人受理" in str(early.json()), "還沒人受理時沒有對象可以傳"
    press("U-near", f"action=sos_go&need_id={need.id}")
    r = client.post(f"/api/resources/needs/{need.id}/message_assignee", json={"text": "救護車快到了，請在巷口等"})
    assert r.status_code == 200 and "近志工" in r.json()["message"]
    assert any("陳阿公 的求救" in t and "巷口等" in t for t in line_outbox.texts("U-near"))


def test_responder_reports_arrival_once(db, line_outbox):
    from app.models.dispatch_event import DispatchEvent
    from app.services import sitrep
    need, *_ = _world(db)
    press("U-mid", f"action=sos_arrived&need_id={need.id}")
    assert "只有受理這筆求救的人" in replies(line_outbox)[-1]
    press("U-mid", f"action=sos_go&need_id={need.id}")
    card = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    assert f"action=sos_arrived&need_id={need.id}" in json.dumps(card.contents.to_dict(), ensure_ascii=False)
    press("U-mid", f"action=sos_arrived&need_id={need.id}")
    assert "已回報到場" in replies(line_outbox)[-1]
    assert any("中志工 已到 陳阿公 身邊" in t for t in sent_to(line_outbox, "U-admin"))
    press("U-mid", f"action=sos_arrived&need_id={need.id}")
    assert "已經回報過到場" in replies(line_outbox)[-1]
    assert db.query(DispatchEvent).filter(DispatchEvent.action == "sos_on_scene").count() == 1
    db.expire_all()
    assert sitrep.build(db)["sos"]["median_arrive_min"] is not None
    assert "處理人到場" in {t["what"] for t in sitrep.build(db)["timeline"]}
