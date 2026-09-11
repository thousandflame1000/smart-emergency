# -*- coding: utf-8 -*-
"""
驗證 seed.py 的安全防呆：偵測到非 SQLite（可能是正式環境）且已有資料時，
沒加 --force 應該直接拒絕執行，不能把資料清空。

背景：seed.py 每次執行都會清空 users/care_relations/community_resources/
daily_checkins/system_config 五張表。startup.py 只在資料庫是空的時候
才會呼叫它，天生安全；但這個腳本也可以直接用 `python seed.py` 執行——
如果那時候 .env 的 DATABASE_URL 不小心指到正式環境的 Postgres，會在
沒有任何提示的情況下把正式資料全部刪光。
"""
import importlib
import pytest

import app.database as dbmod
from app.models.user import User


@pytest.fixture
def seed_module():
    import seed as seed_mod
    importlib.reload(seed_mod)
    return seed_mod


def test_refuses_to_run_against_non_sqlite_with_existing_data(db, seed_module, monkeypatch):
    db.add(User(name="既有使用者", roles=["elderly"]))
    db.commit()

    monkeypatch.setattr(dbmod, "_is_sqlite", False)
    monkeypatch.setattr(seed_module, "_is_sqlite", False)

    with pytest.raises(SystemExit):
        seed_module.run(force=False)

    remaining = db.query(User).count()
    assert remaining == 1, "沒加 --force 時不應該真的清空資料"


def test_force_flag_allows_wiping_non_sqlite(db, seed_module, monkeypatch):
    db.add(User(name="既有使用者", roles=["elderly"]))
    db.commit()

    monkeypatch.setattr(dbmod, "_is_sqlite", False)
    monkeypatch.setattr(seed_module, "_is_sqlite", False)

    seed_module.run(force=True)  # 明確要求才允許清空

    names = {u.name for u in db.query(User).all()}
    assert "既有使用者" not in names
    assert "陳月英" in names  # seed 資料確實塞進去了


def test_sqlite_dev_workflow_is_unaffected(db, seed_module):
    """本機 sqlite 開發流程（README 記載的標準流程）不該被這個防呆擋住。"""
    seed_module.run()
    assert db.query(User).count() > 0
