"""整場災害演練：全部走真的 LINE webhook（HMAC 簽章），不直接呼叫處理函式。

啟動緊急模式 → 長者點名 → 一鍵求救 → 附近志工「我過去」→ 處理完成 → 家屬請人探視 →
志工附近點名上門 → 後台點名看板與災情摘要的數字要對得上。任何一段的路由、按鈕資料、
權限檢查接錯，這裡都會斷。
"""
import base64
import hashlib
import hmac
import json
import uuid

import pytest
from fastapi.testclient import TestClient

from app.config import settings
from app.main import app
from app.models.care_relation import CareRelation
from app.models.need import CommunityNeed
from app.routers import linebot
from app.services import rollcall, sitrep
from tests.test_line_hardening import mk, sent_to


def _uid(n: int) -> str:
    return "U" + f"{n:032x}"


def _event(uid: str, **kind) -> dict:
    return {"mode": "active", "timestamp": 1710000000000, "source": {"type": "user", "userId": uid},
            "webhookEventId": "evt-" + uuid.uuid4().hex, "deliveryContext": {"isRedelivery": False},
            "replyToken": "reply-" + uuid.uuid4().hex, **kind}


def _send(client: TestClient, event: dict) -> None:
    body = json.dumps({"destination": "U" + "0" * 32, "events": [event]}, ensure_ascii=False,
                      separators=(",", ":")).encode("utf-8")
    signature = base64.b64encode(hmac.new(settings.LINE_CHANNEL_SECRET.encode(), body, hashlib.sha256).digest()).decode()
    response = client.post("/webhook/line", content=body,
                           headers={"X-Line-Signature": signature, "Content-Type": "application/json"})
    assert response.status_code == 200, response.text


@pytest.fixture
def line(monkeypatch):
    monkeypatch.setattr(linebot, "_DEV_MODE", False)
    client = TestClient(app)

    def text(uid, words):
        _send(client, _event(uid, type="message", message={"id": uuid.uuid4().hex[:10], "type": "text",
                                                            "quoteToken": "q", "text": words}))

    def press(uid, data):
        _send(client, _event(uid, type="postback", postback={"data": data}))
    return text, press, client


def _wait_for(condition, seconds: float = 5.0) -> None:
    import time
    deadline = time.monotonic() + seconds
    while not condition():
        assert time.monotonic() < deadline, "等不到背景廣播"
        time.sleep(0.05)


def _replies(outbox):
    return [getattr(m, "text", None) or getattr(m, "alt_text", "") for kind, _to, m in outbox.sent if kind == "reply"]


def test_full_disaster_drill_over_the_real_webhook(db, line_outbox, line):
    text, press, client = line
    admin, helper, far_helper = _uid(1), _uid(2), _uid(3)
    safe_elder, sos_elder, kid = _uid(10), _uid(11), _uid(20)
    mk(db, "管理員", ["admin"], admin)
    mk(db, "巷口志工", ["volunteer"], helper, lat=23.6650, lng=121.4180)
    mk(db, "遠方志工", ["volunteer"], far_helper, lat=23.8000, lng=121.4180)
    mk(db, "平安阿嬤", ["elderly"], safe_elder, lat=23.6655, lng=121.4185)
    sos = mk(db, "求救阿公", ["elderly"], sos_elder, lat=23.6660, lng=121.4190, phone="0911222333", address="大進村 3 號")
    silent = mk(db, "沒手機阿伯", ["elderly"], None, lat=23.6645, lng=121.4175, address="大進村 9 號")
    family = mk(db, "阿伯的女兒", ["family"], kid)
    db.add(CareRelation(elderly_id=silent.id, contact_id=family.id, relation="family")); db.commit()

    # 1. 啟動緊急模式：長者收點名卡，志工收提醒
    assert client.post("/api/dashboard/mode?mode=emergency").status_code == 200
    _wait_for(lambda: sent_to(line_outbox, safe_elder) and sent_to(line_outbox, helper))  # 廣播在背景執行緒送
    assert sent_to(line_outbox, safe_elder) == ["🚨 請回報是否平安"]
    assert any("附近點名" in t for t in sent_to(line_outbox, helper))

    # 2. 長者回報：按點名卡「我平安」
    press(safe_elder, "action=safe")
    assert "已回報平安" in _replies(line_outbox)[-1]

    # 3. 一鍵求救（先確認）→ 附近志工收「附近有人需要幫忙」，遠的沒收到
    text(sos_elder, "需要幫忙")
    press(sos_elder, "action=confirm_sos")
    assert "也通知了附近 1 位志工" in _replies(line_outbox)[-1]
    assert "🆘 附近有人需要幫忙" in sent_to(line_outbox, helper)
    assert "🆘 附近有人需要幫忙" not in sent_to(line_outbox, far_helper)
    need = db.query(CommunityNeed).filter(CommunityNeed.requester_id == sos.id).one()

    # 4. 志工「我過去」→ 受理；「處理完成」→ 結案
    press(helper, f"action=sos_go&need_id={need.id}")
    assert _replies(line_outbox)[-1] == "由您處理這筆求救"
    press(far_helper, f"action=sos_go&need_id={need.id}")
    assert "已經有 巷口志工 在處理了" in _replies(line_outbox)[-1]
    press(helper, f"action=sos_done&need_id={need.id}")
    db.expire_all()
    assert need.status == "fulfilled"

    # 5. 家屬聯絡不到沒手機的阿伯 → 請附近志工去看看
    press(kid, f"action=family_check&elder_id={silent.id}")
    assert "去看看 沒手機阿伯" in _replies(line_outbox)[-1]
    check = db.query(CommunityNeed).filter(CommunityNeed.requester_id == silent.id).one()

    # 6. 志工「附近點名」→ 上門，回報阿伯平安
    text(helper, "附近點名")
    assert _replies(line_outbox)[-1].startswith("附近還沒回報的長者")
    press(helper, f"action=rc_mark&user_id={silent.id}&s=ok")
    assert "已回報 沒手機阿伯 平安" in _replies(line_outbox)[-1]
    press(helper, f"action=sos_go&need_id={check.id}")
    press(helper, f"action=sos_done&need_id={check.id}")
    assert any("處理完成" in t for t in sent_to(line_outbox, kid)), "家屬要知道結果"

    # 7. 後台看板與災情摘要對得上
    board = client.get("/api/rollcall").json()
    by_name = {p["name"]: p["status"] for p in board["people"]}
    assert by_name == {"平安阿嬤": "ok", "求救阿公": "help", "沒手機阿伯": "ok"}
    db.expire_all()  # 按鈕是 webhook 在另一個 session 處理的
    report = sitrep.build(db)
    assert report["sos"]["total"] == 2 and report["sos"]["closed"] == 2 and report["sos"]["median_ack_min"] is not None
    whats = {t["what"] for t in report["timeline"]}
    assert {"通報求救", "受理求救", "求救結案", "家屬請人探視", "點名回報：需要協助"} <= whats

    # 8. 解除緊急模式：點名結束
    assert client.post("/api/dashboard/mode?mode=normal").status_code == 200
    assert rollcall.board(db) == {"active": False}
