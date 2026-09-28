"""LINE「查詢物資」：列出最近的公開據點，不外洩個人物資位置。"""
from app.models.config import SystemConfig
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from tests.test_line_hardening import mk, replies, say


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
    text = replies(line_outbox)[-1]
    assert text.index("近的超商") < text.index("遠的避難所")
    assert "停用倉庫" not in text
    assert "飲用水充足" in text and "收容 12/80 人" in text
    assert "google.com/maps" in text


def test_emergency_mode_puts_emergency_points_first(db, line_outbox):
    _points(db)
    db.add(SystemConfig(key="mode", value="emergency")); db.commit()
    mk(db, "居民", ["elderly"], uid="U-emg", lat=23.9, lng=121.6)
    say("U-emg", "查詢物資")
    text = replies(line_outbox)[-1]
    assert "緊急模式" in text
    assert text.index("遠的避難所") < text.index("近的超商")


def test_personal_resources_are_never_listed(db, line_outbox):
    owner = mk(db, "志工", ["volunteer"], uid="U-own", lat=23.9, lng=121.6)
    db.add(CommunityResource(owner_id=owner.id, resource_type="water", name="志工家的水",
                             lat=23.9001, lng=121.6001)); db.commit()
    mk(db, "居民", ["elderly"], uid="U-priv", lat=23.9, lng=121.6)
    say("U-priv", "查詢物資")
    assert "志工家的水" not in replies(line_outbox)[-1]


def test_without_location_asks_to_share(db, line_outbox):
    mk(db, "居民", ["elderly"], uid="U-noloc")
    say("U-noloc", "查詢物資")
    assert "請先分享位置" in replies(line_outbox)[-1]
