"""最近的 AED：全國資料、現在有沒有開、LINE 指令、急救問答與求救卡片。"""
import json
from datetime import datetime

from app.services import aed
from app.services.aed import aeds as real_aeds  # 收集測試時抓，conftest 會在每個測試換掉它
from tests.test_line_hardening import mk, replies, say

BOX = aed.Aed("社區活動中心", "一樓大廳", 23.6652, 121.4182, "08:00-17:00", "", "", "假日請洽警衛", "03-870-1234")
ALWAYS = aed.Aed("消防分隊", "車庫門口", 23.6700, 121.4200, "00:00-23:59", "00:00-23:59", "00:00-23:59", "", None)


def test_national_file_covers_taiwan_and_finds_the_venue():
    found = real_aeds()
    assert len(found) > 15000
    assert not [a for a in found if a.phone and a.phone.startswith("09")], "只留市話"
    import unittest.mock as mock
    with mock.patch.object(aed, "aeds", real_aeds):
        rows = aed.nearest(25.0441, 121.5296)  # 華山文創園區
    assert rows[0]["km"] < 0.3
    assert len({r["aed"].name for r in rows}) == len(rows), "同一個場所只列一次"


def test_open_hours_follow_the_day_of_week():
    monday_noon, monday_night, saturday = datetime(2026, 10, 5, 12, 0), datetime(2026, 10, 5, 21, 0), datetime(2026, 10, 3, 12, 0)
    assert aed.open_now(BOX, monday_noon) is True and aed.open_now(BOX, monday_night) is False
    assert aed.open_now(BOX, saturday) is False and aed.hours_text(BOX, saturday) == "今天沒有開放"
    assert aed.hours_text(ALWAYS, monday_night) == "現在開放（24 小時）"
    unknown = aed.Aed("某處", "", 23.6, 121.4, "", "", "", "", None)
    assert aed.open_now(unknown) is None and aed.hours_text(unknown) == "開放時間未登記"


def test_line_aed_command_lists_the_nearest_with_navigation(db, line_outbox, monkeypatch):
    monkeypatch.setattr(aed, "aeds", lambda: (BOX, ALWAYS))
    mk(db, "沒位置", ["elderly"], "U-noloc")
    say("U-noloc", "AED")
    assert "請先分享位置" in replies(line_outbox)[-1]
    mk(db, "有位置", ["elderly"], "U-loc", lat=23.6650, lng=121.4180)
    say("U-loc", "AED")
    message = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    card = json.dumps(message.contents.to_dict(), ensure_ascii=False)
    assert message.alt_text.startswith("最近的 AED：社區活動中心")
    assert "放在：一樓大廳" in card and "destination=23.6652,121.4182" in card and "tel:038701234" in card


def test_cpr_question_mentions_the_nearest_aed(db, line_outbox, monkeypatch):
    from app.services import rag
    monkeypatch.setattr(aed, "aeds", lambda: (ALWAYS,))
    monkeypatch.setattr(rag, "query", lambda q: {"answer": "先撥 119，再開始壓胸。", "sources": ["CPR 指引"], "has_answer": True})
    mk(db, "問的人", ["elderly"], "U-ask", lat=23.6650, lng=121.4180)
    say("U-ask", "CPR 怎麼做？")
    assert "最近的 AED：消防分隊" in replies(line_outbox)[-1]
    say("U-ask", "低血糖怎麼辦？")
    assert "AED" not in replies(line_outbox)[-1], "跟心跳停止無關的問題不附 AED"


def test_sos_cards_tell_the_responder_where_the_aed_is(db, monkeypatch):
    from types import SimpleNamespace
    from app.services import sos
    monkeypatch.setattr(aed, "aeds", lambda: (ALWAYS,))
    need = SimpleNamespace(id="n1", lat=23.6650, lng=121.4180, address="大進村", description="跌倒",
                           requester=SimpleNamespace(name="阿公", phone=None, lat=None, lng=None))
    assert "最近的 AED：消防分隊" in json.dumps(sos.responder_card(need), ensure_ascii=False)
