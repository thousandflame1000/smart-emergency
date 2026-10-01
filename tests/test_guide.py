"""使用手冊：不用登入就看得到，而且跟 LINE 選單上的按鈕一致。"""
from fastapi.testclient import TestClient

from app.config import settings
from app.services import rich_menu
from tests.test_line_hardening import mk, replies, say


def _guide():
    from app.main import app
    return TestClient(app).get("/guide")


def test_guide_is_public_even_when_the_console_is_locked(monkeypatch):
    from app import demo_auth
    monkeypatch.setattr(settings, "DEMO_PASSWORD", "pw")
    demo_auth.reset_cache()
    try:
        r = _guide()
        assert r.status_code == 200 and "鄰里守望 使用手冊" in r.text
    finally:
        demo_auth.reset_cache()


def test_every_rich_menu_button_is_in_the_guide():
    """選單改了手冊沒跟上，使用者就找不到按鈕：選單上每一顆按鈕的名稱都要在手冊裡。"""
    page = _guide().text
    labels = {cell[0] for row in rich_menu.RESIDENT_ROWS + rich_menu.ADMIN_ROWS for cell in row}
    missing = sorted(label for label in labels if label not in page)
    assert not missing, missing


def test_help_reply_links_to_the_guide(db, line_outbox):
    mk(db, "長者", ["elderly"], "U-help")
    say("U-help", "操作說明")
    assert settings.PUBLIC_BASE_URL.rstrip("/") + "/guide" in replies(line_outbox)[-1]


def test_account_deletion_has_a_visible_button(db, line_outbox):
    """刪除帳號是個資權利，不能只靠打字：「我的資料」卡片上要有按鈕，按了先確認。"""
    import json
    mk(db, "長者", ["elderly"], "U-del")
    say("U-del", "我的資料")
    card = json.dumps([m for kind, _to, m in line_outbox.sent if kind == "reply"][-1].contents.to_dict(), ensure_ascii=False)
    assert '"label": "刪除我的帳號"' in card
    say("U-del", "刪除我的帳號")
    confirm = json.dumps([m for kind, _to, m in line_outbox.sent if kind == "reply"][-1].contents.to_dict(), ensure_ascii=False)
    assert "action=delete_me" in confirm and "action=keep_me" in confirm
