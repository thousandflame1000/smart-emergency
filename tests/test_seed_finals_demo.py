# -*- coding: utf-8 -*-
"""決賽示範資料：驗證「現場一定會被按到」的那兩件事真的成立。

這支測試不是在檢查筆數對不對，而是釘住兩個會讓現場開天窗的失效模式：

1. 「待派」卡只認志工的個人物資（line_ops._top_candidates 過濾 source=="resource"），
   設施不算。第一版種子把志工隨機撒在三個鄉鎮、31 公里範圍內，主角緊急度 5
   的距離上限只有 2 公里，於是候選全是設施、卡片顯示「目前沒有可用的志工物資」。
2. 主角的脆弱度要靠真實塞入的打卡與警報算出來且明顯居首。第一版交給隨機，
   結果有人同分，主角掉到第 2 名——「平時關懷資料決定災時優先權」這個主張
   在現場就會被自己的畫面打臉。
"""
import importlib

from app.models.need import CommunityNeed
from app.models.user import User
from app.services import line_ops
from app.services.dispatch import _vulnerability_pts


def _seed(db):
    import seed_finals_demo
    importlib.reload(seed_finals_demo)
    seed_finals_demo.run()
    return seed_finals_demo


def _protagonist(db):
    return db.query(User).filter(User.name == "王秀霞").first()


def test_protagonist_is_the_single_most_vulnerable_person(db):
    _seed(db)
    elders = [u for u in db.query(User).all() if "elderly" in (u.roles or [])]
    scored = sorted(((_vulnerability_pts(e.id, db), e.name) for e in elders), reverse=True)
    top_score, top_name = scored[0]
    runner_up = scored[1][0]
    assert top_name == "王秀霞", f"脆弱度最高的是「{top_name}」，不是主角"
    assert top_score > runner_up, f"主角 {top_score} 跟第二名 {runner_up} 同分，排序會變成擲骰子"


def test_dispatch_card_finds_a_volunteer_not_only_facilities(db):
    """最關鍵的一張卡：待派需求必須算得出「派給某位志工」。"""
    _seed(db)
    lead = _protagonist(db)
    need = CommunityNeed(
        requester_id=lead.id, need_type="water", description="停水",
        address=lead.address, lat=lead.lat, lng=lead.lng, urgency=5, status="open")
    db.add(need)
    db.commit()

    top = line_ops._top_candidates(need, db, 1)
    assert top, "「待派」卡拿不到志工候選，現場只會看到「目前沒有可用的志工物資」"
    best = top[0]
    assert best.get("vol"), "候選沒有志工姓名，卡片上的「派給 ○○○」會是空的"
    assert best.get("dist_km") is not None and best["dist_km"] <= 2.0, (
        f"最佳候選距離 {best.get('dist_km')} km，超過緊急度 5 的 2 公里上限")


def test_several_volunteers_with_water_live_within_walking_distance(db):
    """釘的是規則，不是某一次抽樣的結果。

    只斷言「這次剛好找得到候選」會漏掉真正的退化：志工若改回隨機撒在十個村里，
    四位帶水的志工大約只有三分之一機率有人落在主角 2 公里內——測試會時過時不過，
    而決賽當天是哪一種沒人知道。這裡直接要求附近就有好幾位，餘裕不能只有一位。
    """
    from app.models.resource import CommunityResource
    from app.services.geo import haversine_km

    _seed(db)
    lead = _protagonist(db)
    near = 0
    for res in db.query(CommunityResource).filter(
            CommunityResource.resource_type == "water",
            CommunityResource.is_available == True).all():  # noqa: E712
        if res.lat and haversine_km(lead.lat, lead.lng, res.lat, res.lng) <= 2.0:
            near += 1
    assert near >= 3, (
        f"主角 2 公里內只有 {near} 份可用的飲用水；緊急度 5 的媒合半徑就是 2 公里，"
        "少於三份代表這條演示路徑是靠運氣成立的")


def test_no_internal_marker_leaks_into_anything_a_person_reads(db):
    """線上前一批資料把 `（[TYPHOON_SIM]）` 接在地址後面，於是後台名單、
    派遣卡與 LINE 訊息全都印著這串內部代碼。標記要放在 system_config，不是地址。"""
    from app.models.resource import CommunityResource
    from app.models.resource_point import ResourcePoint

    _seed(db)
    checks = [(User, "address"), (User, "name"),
              (CommunityNeed, "description"), (CommunityResource, "name"),
              (ResourcePoint, "address"), (ResourcePoint, "name")]
    for model, field in checks:
        for row in db.query(model).all():
            value = str(getattr(row, field) or "")
            assert "[" not in value, f"{model.__name__}.{field} 帶著內部標記：{value!r}"
            assert "SIM" not in value and "DEMO" not in value, (
                f"{model.__name__}.{field} 帶著內部標記：{value!r}")


def test_everyone_is_in_one_region_so_distance_scoring_means_something(db):
    """台中與花東混在一起時最遠相距約 120 公里，距離懲罰算出來沒有意義。"""
    from app.services.geo import haversine_km

    _seed(db)
    pts = [(u.lat, u.lng) for u in db.query(User).all() if u.lat and u.lng]
    assert pts
    span = haversine_km(min(p[0] for p in pts), min(p[1] for p in pts),
                        max(p[0] for p in pts), max(p[1] for p in pts))
    assert span < 60, f"最遠相距 {span:.0f} km，距離評分會失去意義"


def test_reset_keeps_the_road_graph(db):
    """工作區圖資有兩千多個道路節點，道路搜尋靠它。清示範資料不能連它一起刪。"""
    import seed_finals_demo
    importlib.reload(seed_finals_demo)
    from app.models.workspace import TopologyWorkspace

    assert TopologyWorkspace not in seed_finals_demo.WIPE_MODELS
    names = {m.__tablename__ for m in seed_finals_demo.WIPE_MODELS}
    assert "topology_workspaces" not in names
    assert "knowledge_base" not in names
    assert "zones" not in names
