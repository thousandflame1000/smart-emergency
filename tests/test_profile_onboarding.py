# -*- coding: utf-8 -*-
"""新加入的人要能自己填資料，不必等管理員代打，也不必先開口要東西。

先前唯一會寫入姓名／電話／地址的路徑是送出「申請物資」表單。於是一個剛
加好友的長輩，系統只知道他的 LINE 暱稱——沒有電話可以打，沒有地址可以
轉座標，派遣演算法看不到他。而這套系統的主張正是「平時就累積資料」。
"""
from fastapi.testclient import TestClient

from app.main import app as real_app
from app.models.user import User
from app.routers import linebot as lb
from app.services.form_token import make_token

from tests.test_line_hardening import _Ev, mk, readable, say

client = TestClient(real_app)


def _cards(outbox):
    return [readable(m) for kind, to, m in outbox.sent]


def test_welcome_hands_the_newcomer_a_form(db, line_outbox):
    lb.handle_follow(_Ev("U-新人"))
    assert any("歡迎" in t for t in _cards(line_outbox)), _cards(line_outbox)
    user = db.query(User).filter(User.line_uid == "U-新人").first()
    assert user is not None
    # 剛加入就是這個狀態：只有暱稱，其餘一片空白。
    assert user.phone is None and user.address is None and user.lat is None


def test_a_resident_can_fill_in_their_own_details(db):
    user = mk(db, "測試長輩", ["elderly"], uid="U-填資料")
    token = make_token("U-填資料")

    res = client.post("/f/api/profile", json={
        "t": token, "name": "王秀霞", "phone": "0912345678",
        "address": "花蓮縣光復鄉大進村大進街48號"})
    assert res.status_code == 200, res.text
    assert res.json()["ok"] is True

    db.expire_all()
    user = db.query(User).filter(User.line_uid == "U-填資料").first()
    assert user.name == "王秀霞"
    assert user.phone == "0912345678"
    assert user.address == "花蓮縣光復鄉大進村大進街48號"


def test_saving_details_without_a_location_says_so(db):
    mk(db, "沒座標", ["elderly"], uid="U-無座標")
    res = client.post("/f/api/profile", json={
        "t": make_token("U-無座標"), "name": "陳阿姨", "address": "花蓮縣瑞穗鄉中山路二段1號"})
    body = res.json()
    # 沒有座標就配不出志工，回覆要講清楚下一步，不要只說「已儲存」。
    assert body["need_location"] is True
    assert "分享位置" in body["message"]


def test_the_form_page_serves_the_profile_kind(db):
    assert client.get("/f/profile?t=" + make_token("U-任意")).status_code == 200


def test_a_bad_phone_is_refused_with_a_reason(db):
    mk(db, "電話怪", ["elderly"], uid="U-壞電話")
    res = client.post("/f/api/profile", json={
        "t": make_token("U-壞電話"), "name": "李先生", "address": "花蓮縣光復鄉1號",
        "phone": "打給我就對了"})
    assert res.status_code == 422
    assert "電話" in res.text


def test_resident_can_reach_the_form_again_from_line(db, line_outbox):
    """搬家、換電話都會發生，填過一次不能就此鎖死。"""
    mk(db, "已註冊", ["elderly"], uid="U-再填")
    say("U-再填", "我的資料")
    assert any("我的資料" in t for t in _cards(line_outbox)), _cards(line_outbox)
