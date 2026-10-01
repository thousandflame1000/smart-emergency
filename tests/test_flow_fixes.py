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
