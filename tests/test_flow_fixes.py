"""看得到的按鈕不能通到死路。"""
from app.services.line_forms import LINK_TITLES
from tests.test_line_hardening import mk, replies, say


def test_family_members_can_apply_to_volunteer_from_their_card(db, line_outbox):
    """家屬卡片上有「志工申請」，按了卻被說「已經是家屬不用申請」：家屬要能申請。"""
    mk(db, "女兒", ["family"], "U-fam")
    say("U-fam", "我要當志工")
    message = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    assert getattr(message, "alt_text", "") == LINK_TITLES["apply"]

    mk(db, "老志工", ["volunteer"], "U-vet")
    say("U-vet", "我要當志工")
    assert "已經是志工" in replies(line_outbox)[-1]


def _quick_commands(line_outbox):
    message = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    items = message.quick_reply.items if getattr(message, "quick_reply", None) else []
    return [(i.action.label, getattr(i.action, "data", "") or getattr(i.action, "text", "")) for i in items]


def test_replies_that_point_somewhere_come_with_the_button(db, line_outbox):
    """回覆說「按下面的…」就一定要有那顆按鈕，不能叫人打字，也不能指向不存在的選單。"""
    mk(db, "居民", ["elderly"], "U-res")
    mk(db, "志工", ["volunteer"], "U-vol", lat=23.66, lng=121.42)
    cases = [("U-res", "我的需求", "申請物資"), ("U-res", "接單", "志工申請"), ("U-res", "附近點名", "志工申請"),
             ("U-vol", "我的任務", "接單"), ("U-vol", "我的物資", "登記物資"), ("U-vol", "接單", "登記物資"),
             ("U-vol", "暫停支援", "恢復支援"), ("U-vol", "恢復支援", "更新我的位置")]
    for uid, pressed, button in cases:
        say(uid, pressed)
        labels = [label for label, _cmd in _quick_commands(line_outbox)]
        assert button in labels, (pressed, labels, replies(line_outbox)[-1])
        assert "傳「" not in replies(line_outbox)[-1], (pressed, replies(line_outbox)[-1])


def test_elder_status_clears_once_the_sos_is_closed(db, line_outbox):
    """早上打卡按過需要幫忙：求救結案或取消後，長者狀態、總覽、未解警報都不能整天掛著紅色緊急求助。"""
    from fastapi.testclient import TestClient
    from app.main import app
    from app.models.checkin import DailyCheckin
    from app.models.need import CommunityNeed
    from app.timeutil import today_tw
    from tests.test_line_hardening import press
    c = TestClient(app)
    elder = mk(db, "王奶奶", ["elderly"], "U-gma", lat=23.66, lng=121.42)
    checkin = DailyCheckin(elderly_id=elder.id, date=today_tw(), status="pending")
    db.add(checkin); db.commit()
    press("U-gma", f"action=help&checkin_id={checkin.id}")

    def status():
        return next(r["today_status"] for r in c.get("/api/dashboard/elderly").json() if r["name"] == "王奶奶")

    def help_alerts():
        return [a for a in c.get("/api/dashboard/alerts").json() if a.get("alert_type") == "help_needed"]
    assert status() == "help_needed" and help_alerts()
    need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "sos").one()
    assert c.post(f"/api/resources/needs/{need.id}/resolve_sos").status_code == 200
    assert status() == "help_resolved", "求救結案後不再是緊急求助"
    assert not help_alerts(), "對應的求救警報也要一起標成已處理"
    counts = c.get("/api/dashboard/summary").json()["checkin_summary"]
    assert counts.get("help_needed", 0) == 0 and counts.get("help_resolved") == 1
