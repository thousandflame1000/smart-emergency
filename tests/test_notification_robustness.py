# -*- coding: utf-8 -*-
"""
驗證單一使用者的 LINE 推播失敗不會讓整批排程中斷。

背景：send_daily_checkins() 跟 alert._escalate() 原本都是迴圈裡直接
呼叫 LINE API，沒有包 try/except。只要其中一個使用者的 LINE ID 失效
或 API 暫時性錯誤丟出例外，整個迴圈就會中斷——後面排隊的其他長者
當天完全收不到打卡訊息，_escalate() 甚至因為 Alert 記錄建立在迴圈
之後、永遠執行不到，導致同一筆警報每 15 分鐘被重新觸發一次。
"""
from datetime import date, timedelta

from app.database import SessionLocal
from app.models.user import User
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.outbox import OutboxMessage
from app.models.care_relation import CareRelation
from app.services import checkin as checkin_svc
from app.services import alert as alert_svc
from app.services import dispatch as dispatch_svc
from app.timeutil import now_utc


def test_send_daily_checkins_continues_after_one_failure(db, monkeypatch):
    bad = User(name="失效LINE長者", roles=["elderly"], line_uid="Ubad", is_active=True)
    good = User(name="正常長者", roles=["elderly"], line_uid="Ugood", is_active=True)
    db.add_all([bad, good])
    db.commit()

    sent_to = []

    def fake_send(line_uid, checkin_id):
        if line_uid == "Ubad":
            raise Exception("LINE API 錯誤：無效的使用者")
        sent_to.append(line_uid)

    monkeypatch.setattr(checkin_svc, "send_checkin_message", fake_send)
    checkin_svc.send_daily_checkins()

    assert "Ugood" in sent_to, "壞掉的那個使用者不該擋住後面正常的使用者"

    today = date.today()
    checkins = db.query(DailyCheckin).filter(DailyCheckin.date == today).all()
    assert len(checkins) == 2, "兩位長者的打卡記錄都該建立，即使推播失敗"
    bad_checkin = next(row for row in checkins if row.elderly_id == bad.id)
    good_checkin = next(row for row in checkins if row.elderly_id == good.id)
    assert bad_checkin.prompt_sent_at is None
    assert good_checkin.prompt_sent_at is not None
    failed_delivery = db.query(OutboxMessage).filter(
        OutboxMessage.aggregate_id == str(bad_checkin.id)
    ).one()
    assert failed_delivery.status == "FAILED"
    bad_checkin.created_at = now_utc() - timedelta(hours=4)
    db.commit()
    alert_svc.check_no_response()
    assert db.query(Alert).filter(Alert.checkin_id == bad_checkin.id).count() == 0


def test_escalate_continues_after_one_contact_send_fails(db, monkeypatch):
    elder = User(name="長者", roles=["elderly"])
    bad_contact = User(name="失效LINE聯絡人", roles=["family"], line_uid="Ubad")
    good_contact = User(name="正常聯絡人", roles=["family"], line_uid="Ugood")
    db.add_all([elder, bad_contact, good_contact])
    db.commit()

    db.add_all([
        CareRelation(elderly_id=elder.id, contact_id=bad_contact.id, relation="family", notify_order=1),
        CareRelation(elderly_id=elder.id, contact_id=good_contact.id, relation="family", notify_order=2),
    ])
    checkin = DailyCheckin(elderly_id=elder.id, date=date.today(), status="pending")
    db.add(checkin)
    db.commit()
    db.refresh(checkin)

    sent_to = []

    def fake_send(line_uid, elderly_name, alert_type, checkin_id):
        if line_uid == "Ubad":
            raise Exception("LINE API 錯誤：無效的使用者")
        sent_to.append(line_uid)

    monkeypatch.setattr(alert_svc, "send_alert_message", fake_send)
    from datetime import datetime
    alert_svc._escalate(db, checkin, "no_response_1h", datetime.now())

    assert "Ugood" in sent_to, "壞掉的那個聯絡人不該擋住後面正常的聯絡人"

    alert = db.query(Alert).filter(Alert.checkin_id == checkin.id).first()
    assert alert is not None, "即使有聯絡人發送失敗，Alert 記錄還是該被建立，不然同一筆警報會被重複觸發"
    assert good_contact.id in alert.notified_users
    assert bad_contact.id not in alert.notified_users


def test_requester_message_failure_is_left_for_outbox_retry(db, monkeypatch):
    from app.models.need import CommunityNeed
    from app.services import line_notify

    requester = User(name="需求者", roles=["elderly"], line_uid="U-requester")
    db.add(requester); db.commit(); db.refresh(requester)
    need = CommunityNeed(requester_id=requester.id, need_type="water", status="matched")
    db.add(need); db.commit(); db.refresh(need)
    monkeypatch.setattr(
        line_notify,
        "send_text",
        lambda *args, **kwargs: (_ for _ in ()).throw(RuntimeError("LINE unavailable")),
    )

    assert dispatch_svc.notify_requester(need, "志工正在前往") is False
    delivery = db.query(OutboxMessage).filter(
        OutboxMessage.aggregate_id == str(need.id),
        OutboxMessage.message_type == "TEXT",
    ).one()
    assert delivery.status == "FAILED" and "LINE unavailable" in delivery.last_error
