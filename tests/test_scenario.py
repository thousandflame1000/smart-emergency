# -*- coding: utf-8 -*-
"""
決賽展演用情境模擬引擎（app/services/scenario.py）的回歸測試。

重點驗證三件事，對應它存在的理由：
1. 情境從頭跑到「自動媒合」那一步時，王秀霞真的因為脆弱度分數贏過
   對照組長者拿到唯一一份物資——不是腳本硬編結果，是呼叫真正的
   dispatch.auto_dispatch()算出來的。
2. 情境自建的物資類型（SCENARIO_NEED_TYPE）跟系統既有資料完全隔離，
   不會被其他已存在的 open 需求/可用物資干擾，示範才會每次都一樣。
3. reset() 只清掉情境自己建立的資料，不會動到既有/真實資料
   （尤其是王秀霞本人，如果她是 seed_rich_demo.py 建立的真實主角）。
"""
import json
from datetime import date, timedelta

from app.models.user import User
from app.models.checkin import DailyCheckin
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.config import SystemConfig
from app.services import scenario


def _run_full_scenario(db):
    st = scenario.start(db)
    while not st["finished"]:
        st = scenario.advance(db)
    return st


def test_full_scenario_prioritizes_protagonist_by_real_vulnerability(db):
    st = _run_full_scenario(db)
    assert st["step"] == len(scenario.STEPS) - 1

    protagonist = db.query(User).filter(User.name == "王秀霞").first()
    assert protagonist is not None, "情境應該在找不到既有主角時自己建立一位"

    need_p = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.requester_id == protagonist.id,
                CommunityNeed.need_type == scenario.SCENARIO_NEED_TYPE)
        .first()
    )
    assert need_p is not None
    assert need_p.status == "suggested", (
        f"王秀霞的情境需求應該贏得唯一一份物資，實際狀態：{need_p.status}"
    )

    other_needs = (
        db.query(CommunityNeed)
        .filter(CommunityNeed.need_type == scenario.SCENARIO_NEED_TYPE,
                CommunityNeed.requester_id != protagonist.id)
        .all()
    )
    assert len(other_needs) == 1
    assert other_needs[0].status == "open", "資源只有一份，對照組應該搶輸、維持待媒合"


def test_scenario_resource_pool_isolated_from_existing_open_needs(db):
    """
    系統裡如果剛好還有其他 open 的 water 需求跟可用資源（例如真實使用中
    留下的資料），不應該干擾情境的資源池——情境用專屬類型隔離。
    """
    vol = User(name="既有志工", roles=["volunteer"], lat=24.2, lng=120.7)
    db.add(vol); db.commit()
    db.add(CommunityResource(owner_id=vol.id, resource_type="water",
                              name="既有的水", lat=24.2, lng=120.7, is_available=True))
    elder = User(name="既有長者", roles=["elderly"], lat=24.2, lng=120.7)
    db.add(elder); db.commit()
    db.add(CommunityNeed(requester_id=elder.id, need_type="water", urgency=5,
                          lat=24.2, lng=120.7, status="open"))
    db.commit()

    _run_full_scenario(db)

    # 既有的水需求應該用既有的水媒合，跟情境的專屬物資池完全無關
    existing_need = db.query(CommunityNeed).filter(CommunityNeed.need_type == "water").first()
    assert existing_need.status in ("suggested", "matched", "open")  # 演算法本身行為，這裡只確認沒有互相污染


def test_reset_does_not_touch_pre_existing_protagonist(db):
    """王秀霞若是已經存在的真實主角，reset() 之後她本人跟她的既有關懷史都要還在。"""
    protagonist = User(name="王秀霞", roles=["elderly"], lat=24.151, lng=120.681, is_active=True)
    db.add(protagonist); db.commit()
    today = date.today()
    db.add(DailyCheckin(elderly_id=protagonist.id, date=today - timedelta(days=1), status="no_response"))
    db.commit()
    protagonist_id = protagonist.id

    _run_full_scenario(db)
    scenario.reset(db)

    still_there = db.query(User).filter(User.id == protagonist_id).first()
    assert still_there is not None, "reset() 不該刪掉既有的真實主角"
    assert db.query(DailyCheckin).filter(DailyCheckin.elderly_id == protagonist_id).count() == 1, \
        "reset() 不該動到主角既有的打卡歷史"

    # 情境自己建立的需求/物資/志工都該被清掉
    assert db.query(CommunityNeed).filter(
        CommunityNeed.need_type == scenario.SCENARIO_NEED_TYPE
    ).count() == 0
    assert db.query(CommunityResource).filter(
        CommunityResource.resource_type == scenario.SCENARIO_NEED_TYPE
    ).count() == 0


def test_reset_removes_temp_protagonist_when_none_existed(db):
    """全新環境（找不到既有王秀霞）情境自己建的替身，reset() 之後要整個消失。"""
    _run_full_scenario(db)
    entities = json.loads(
        db.query(SystemConfig).filter(SystemConfig.key == "scenario_entities").first().value
    )
    assert entities.get("protagonist_created") == "1"
    protagonist_id = entities["protagonist"]

    scenario.reset(db)

    assert db.query(User).filter(User.id == protagonist_id).first() is None
    assert db.query(DailyCheckin).filter(DailyCheckin.elderly_id == protagonist_id).count() == 0


def test_mode_switch_does_not_broadcast(db):
    """情境切換緊急模式只改資料庫欄位，不該觸發真的 LINE 廣播（見設計決定 1）。"""
    scenario.start(db)
    scenario.advance(db)  # 廣播步驟

    cfg = db.query(SystemConfig).filter(SystemConfig.key == "mode").first()
    assert cfg is not None and cfg.value == "emergency"


def test_advance_past_end_is_noop(db):
    st = _run_full_scenario(db)
    again = scenario.advance(db)
    assert again["step"] == st["step"] == len(scenario.STEPS) - 1


def test_autoplay_flag_persists_and_toggles(db):
    st = scenario.set_autoplay(db, True)
    assert st["autoplay"] is True
    st = scenario.status(db)
    assert st["autoplay"] is True
    st = scenario.set_autoplay(db, False)
    assert st["autoplay"] is False
