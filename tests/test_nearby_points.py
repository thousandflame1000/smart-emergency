"""LINE「查詢物資」：列出最近的公開據點，不外洩個人物資位置。"""
from app.models.config import SystemConfig
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
import json

from tests.test_line_hardening import mk, replies, say


def last_card_text(outbox) -> str:
    """最後一則回覆若是卡片，攤平成文字方便比對；否則回文字本身。"""
    message = [m for kind, _to, m in outbox.sent if kind == "reply"][-1]
    if type(message).__name__ == "FlexMessage":
        return json.dumps(message.contents.to_dict(), ensure_ascii=False)
    return message.text


def _points(db):
    db.add_all([
        ResourcePoint(name="近的超商", point_type="store", lat=23.901, lng=121.601,
                      supplies_json='{"water": "充足"}'),
        ResourcePoint(name="遠的避難所", point_type="shelter", lat=23.95, lng=121.65, capacity=80, current_load=12),
        ResourcePoint(name="停用倉庫", point_type="warehouse", lat=23.9, lng=121.6, is_active=False),
    ])
    db.commit()


def test_lists_nearest_active_points_with_map_links(db, line_outbox):
    _points(db)
    mk(db, "居民", ["elderly"], uid="U-near", lat=23.9, lng=121.6)
    say("U-near", "查詢物資")
    text = last_card_text(line_outbox)
    assert text.index("近的超商") < text.index("遠的避難所")
    assert "停用倉庫" not in text
    assert "飲用水充足" in text and "收容 12/80 人" in text
    assert "google.com/maps/dir/?api=1&destination=23.901,121.601" in text
    message = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    assert message.quick_reply.items[0].action.type == "location", "卡片下方仍要能更新位置"


def test_emergency_mode_puts_emergency_points_first(db, line_outbox):
    _points(db)
    db.add(SystemConfig(key="mode", value="emergency")); db.commit()
    mk(db, "居民", ["elderly"], uid="U-emg", lat=23.9, lng=121.6)
    say("U-emg", "查詢物資")
    assert "應變據點" in replies(line_outbox)[-1]  # alt text
    text = last_card_text(line_outbox)
    assert text.index("遠的避難所") < text.index("近的超商")


def test_personal_resources_are_never_listed(db, line_outbox):
    owner = mk(db, "志工", ["volunteer"], uid="U-own", lat=23.9, lng=121.6)
    db.add(CommunityResource(owner_id=owner.id, resource_type="water", name="志工家的水",
                             lat=23.9001, lng=121.6001)); db.commit()
    mk(db, "居民", ["elderly"], uid="U-priv", lat=23.9, lng=121.6)
    say("U-priv", "查詢物資")
    assert "志工家的水" not in last_card_text(line_outbox)


def test_without_location_asks_to_share(db, line_outbox):
    mk(db, "居民", ["elderly"], uid="U-noloc")
    say("U-noloc", "查詢物資")
    assert "請先分享位置" in replies(line_outbox)[-1]


def test_phone_button_only_for_valid_numbers(db, line_outbox):
    db.add_all([ResourcePoint(name="有電話", point_type="clinic", lat=23.9, lng=121.6, phone="03-870-1234"),
                ResourcePoint(name="亂填", point_type="clinic", lat=23.9, lng=121.6, phone="問櫃台")])
    db.commit()
    mk(db, "居民", ["elderly"], uid="U-tel", lat=23.9, lng=121.6)
    say("U-tel", "查詢物資")
    text = last_card_text(line_outbox)
    assert "tel:038701234" in text and "問櫃台" not in text


def test_every_demo_point_type_has_a_chinese_label():
    """示範資料裡的類型若沒有中文名稱，LINE 與地圖會直接露出英文代碼。"""
    import seed_finals_demo
    from app.models.resource_point import POINT_TYPES
    assert {ptype for _, ptype, *_ in seed_finals_demo.FACILITIES} <= POINT_TYPES.keys()


def test_sharing_location_offers_a_one_tap_supply_lookup(db, line_outbox):
    """從「查詢物資」去分享位置後，不必再回選單找按鈕。"""
    from app.routers import linebot as lb
    from tests.test_line_hardening import _Ev, _Msg
    mk(db, "居民", ["elderly"], uid="U-tap")
    lb.handle_location(_Ev("U-tap", msg=_Msg(latitude=23.9, longitude=121.6)))
    message = [m for kind, _to, m in line_outbox.sent if kind == "reply"][-1]
    assert [item.action.data for item in message.quick_reply.items] == ["cmd=查詢物資"]
