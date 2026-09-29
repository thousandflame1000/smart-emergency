# -*- coding: utf-8 -*-
"""
驗證分級通報邏輯：1 小時未回應優先通知家屬，3 小時通知志工上門，
對應計畫書「可靠性設計」承諾。
"""
from app.services.alert import _relations_for_alert


class _FakeUser:
    def __init__(self, roles):
        self.roles = roles


class _FakeRel:
    def __init__(self, roles):
        self.contact = _FakeUser(roles)


def test_1h_tier_prefers_family_excludes_pure_volunteer():
    family = _FakeRel(["family"])
    volunteer = _FakeRel(["volunteer"])
    unlabeled = _FakeRel([])

    tier = _relations_for_alert([family, volunteer, unlabeled], "no_response_1h")
    assert family in tier
    assert unlabeled in tier
    assert volunteer not in tier


def test_3h_tier_only_volunteers():
    family = _FakeRel(["family"])
    volunteer = _FakeRel(["volunteer"])
    unlabeled = _FakeRel([])

    tier = _relations_for_alert([family, volunteer, unlabeled], "no_response_3h")
    assert tier == [volunteer]


def test_3h_tier_falls_back_to_everyone_if_no_volunteer():
    family = _FakeRel(["family"])
    unlabeled = _FakeRel([])
    tier = _relations_for_alert([family, unlabeled], "no_response_3h")
    assert tier == [family, unlabeled]


def test_help_needed_is_not_tiered():
    rels = [_FakeRel(["family"]), _FakeRel(["volunteer"]), _FakeRel([])]
    assert _relations_for_alert(rels, "help_needed") == rels


def test_family_alert_card_can_call_and_navigate_to_the_elder(db, line_outbox):
    import json
    from app.services.line_notify import send_alert_message
    from app.models.user import User
    elder = User(name="王奶奶", roles=["elderly"], phone="03-870-1234", lat=23.67, lng=121.42)
    send_alert_message("U-fam", "王奶奶", "no_response_3h", "c1", elderly=elder)
    card = json.dumps(line_outbox.sent[-1][2].contents.to_dict(), ensure_ascii=False)
    assert "tel:038701234" in card and "destination=23.67,121.42" in card

    send_alert_message("U-fam", "王奶奶", "unwell", "c1", elderly=elder)
    card = json.dumps(line_outbox.sent[-1][2].contents.to_dict(), ensure_ascii=False)
    assert "tel:" in card and "destination=" not in card, "身體不舒服只需要打電話，不用導航"


def test_elder_with_nobody_to_tell_pages_admins_once_after_three_hours(db, line_outbox):
    from datetime import timedelta
    from app.models.care_relation import CareRelation
    from app.models.checkin import DailyCheckin
    from app.models.user import User
    from app.services import alert as alert_svc
    from app.timeutil import now_utc, today_tw
    admin = User(name="管理員", roles=["admin"], line_uid="U-adm3")
    lonely = User(name="獨居阿伯", roles=["elderly"], line_uid="U-lonely", phone="0911222333")
    cared = User(name="有家人", roles=["elderly"], line_uid="U-cared")
    fam = User(name="女兒", roles=["family"], line_uid="U-fam3")
    db.add_all([admin, lonely, cared, fam]); db.commit()
    db.add(CareRelation(elderly_id=cared.id, contact_id=fam.id, relation="family"))
    sent = (now_utc() - timedelta(hours=4)).replace(tzinfo=None)
    for elder in (lonely, cared):
        db.add(DailyCheckin(elderly_id=elder.id, date=today_tw(), status="pending", prompt_sent_at=sent))
    db.commit()

    alert_svc.check_no_response()
    alert_svc.check_no_response()  # 排程每 15 分鐘跑一次，不能每次都再吵管理員
    to_admin = [m for kind, to, m in line_outbox.sent if to == "U-adm3"]
    assert len(to_admin) == 1 and "獨居阿伯" in to_admin[0].alt_text
    assert not any("有家人" in getattr(m, "alt_text", "") for m in to_admin)
