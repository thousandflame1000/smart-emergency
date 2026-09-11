# -*- coding: utf-8 -*-
"""
驗證 User.role_filter()：這裡曾經有一個嚴重的既有 bug——
User.roles.contains(["elderly"]) 這種寫法在 SQL 層面會把 ["elderly"]
也編碼成 JSON 字串再做 LIKE 比對，結果變成只比對「唯一角色剛好等於
elderly」的使用者，任何同時擁有第二個角色（例如同時是 elderly 又是
volunteer）的使用者會從查詢結果消失。

這個 bug 曾經存在於 dashboard.py（/api/dashboard/elderly、
trigger_checkin）、checkin.py（send_daily_checkins 每日打卡排程）、
linebot.py（simulate_checkin、家屬代辦登記查重）共 5 個呼叫點——
最嚴重的是每日打卡排程，代表多重角色的長者永遠不會收到打卡訊息。
"""
from app.models.user import User


def test_role_filter_matches_single_role_user(db):
    u = User(name="單一角色長者", roles=["elderly"])
    db.add(u); db.commit()

    result = db.query(User).filter(User.role_filter("elderly")).all()
    assert [r.name for r in result] == ["單一角色長者"]


def test_role_filter_matches_multi_role_user(db):
    u = User(name="多重角色長者", roles=["elderly", "volunteer"])
    db.add(u); db.commit()

    result = db.query(User).filter(User.role_filter("elderly")).all()
    assert [r.name for r in result] == ["多重角色長者"], (
        "多重角色的使用者不該從角色篩選結果中消失"
    )


def test_role_filter_does_not_false_positive_on_substring_roles(db):
    # "family" 不應該因為字串比對誤配到別的角色
    u1 = User(name="家屬", roles=["family"])
    u2 = User(name="志工", roles=["volunteer"])
    db.add_all([u1, u2]); db.commit()

    result = {r.name for r in db.query(User).filter(User.role_filter("family")).all()}
    assert result == {"家屬"}


def test_role_filter_excludes_users_without_that_role(db):
    u = User(name="純志工", roles=["volunteer"])
    db.add(u); db.commit()

    result = db.query(User).filter(User.role_filter("elderly")).all()
    assert result == []
