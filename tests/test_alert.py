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
