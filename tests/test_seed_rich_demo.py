# -*- coding: utf-8 -*-
"""
驗證 seed_rich_demo.py：跟 seed.py 不一樣，這支腳本是附加式的，
必須確保：
1. 不會動到任何既有、沒有標記過的使用者資料（正式環境上真的有一位
   真人使用者「博柳喧」，這支腳本絕對不能動到他）
2. 主角「王秀霞」的脆弱度分數必須是根據真實塞入的歷史打卡/警報資料
   算出來的，不是隨便給一個數字充數
3. 重複執行不會產生重複資料
4. --wipe 只清掉自己建立的資料
"""
import importlib

from app.models.user import User
from app.services.dispatch import _vulnerability_pts


def _reload_seed_module():
    import seed_rich_demo
    importlib.reload(seed_rich_demo)
    return seed_rich_demo


def test_does_not_touch_pre_existing_untagged_users(db):
    real_user = User(name="不該被清掉的真人", roles=["elderly"], address="真實地址沒有標記")
    db.add(real_user)
    db.commit()

    seed_mod = _reload_seed_module()
    seed_mod.run()

    remaining_real = db.query(User).filter(User.name == "不該被清掉的真人").first()
    assert remaining_real is not None

    seed_mod.wipe()

    still_there = db.query(User).filter(User.name == "不該被清掉的真人").first()
    assert still_there is not None, "wipe 不該動到沒有標記的既有使用者"


def test_protagonist_has_genuinely_high_vulnerability_score(db):
    seed_mod = _reload_seed_module()
    seed_mod.run()

    protagonist = db.query(User).filter(User.name == "王秀霞").first()
    assert protagonist is not None

    score = _vulnerability_pts(protagonist.id, db)
    assert score >= 10, f"主角的脆弱度分數應該根據真實塞入的歷史資料算出偏高分數，實際 {score}"


def test_rerun_does_not_duplicate_data(db):
    seed_mod = _reload_seed_module()
    seed_mod.run()
    count_after_first = db.query(User).count()

    seed_mod.run()
    count_after_second = db.query(User).count()

    assert count_after_first == count_after_second, "重複執行不該產生重複的示範資料"


def test_wipe_removes_all_tagged_data(db):
    seed_mod = _reload_seed_module()
    seed_mod.run()
    assert db.query(User).filter(User.name == "王秀霞").first() is not None

    seed_mod.wipe()
    assert db.query(User).filter(User.name == "王秀霞").first() is None
    assert db.query(User).count() == 0
