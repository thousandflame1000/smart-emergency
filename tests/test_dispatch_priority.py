# -*- coding: utf-8 -*-
"""
兩個在深入測試 dispatch 演算法時才抓到的嚴重 bug 的回歸測試——都是
核心派遣演算法本身的正確性問題，不是邊角案例：

1. 資源重複配對（double-booking）：SessionLocal 設定 autoflush=False
   （app/database.py），auto_dispatch() 在同一批次的迴圈裡把資源標記
   is_available=False 後，如果不主動 db.flush()，這個變更只存在
   Python 物件狀態、還沒真的寫進資料庫。處理下一筆需求時
   _collect_from_resources() 重新對資料庫查詢，撈到的還是舊值，導致
   同一份實際只有一份的物資被「建議」給兩筆不同需求——如果兩筆建議
   都被管理員確認，同一位志工會收到兩個要送同一份物資到不同地址的
   任務通知。

2. 脆弱度沒有真的影響「先處理誰」：原本的處理順序只用
   urgency.desc() + created_at 排序，脆弱度只在同一筆需求「內部」
   比較候選資源時加分，資源不足、兩筆需求緊急度相同、要搶同一份
   物資時，完全沒有反映「平時打卡異常/警報未解決/照顧網絡孤立」
   這些關懷資料——跟計畫書「脆弱度偏高會被自動排入優先派遣名單」
   的核心敘述對不起來。

3. 貪婪法不保證全域最優：處理順序在前的需求會搶走「對它自己來說
   最好、但對後面的需求來說是唯一選項」的資源，即使把該資源讓給
   後面的需求、自己退而求其次仍然能被滿足，貪婪法也看不到這個
   更好的整體解——這是 auto_dispatch() 從貪婪法改成批次匈牙利演算法
   （scipy.optimize.linear_sum_assignment）要解決的問題。
"""
from datetime import datetime, timedelta, date

from app.database import SessionLocal
from app.models.user import User
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from app.services import dispatch


def _make_scarce_resource_scenario(db, high_vuln_created_later=True):
    vol = User(name="志工", roles=["volunteer"], line_uid="Uvol", lat=24.15, lng=120.68)
    db.add(vol); db.commit()

    res = CommunityResource(owner_id=vol.id, resource_type="water", name="唯一的水",
                             lat=24.15, lng=120.68, is_available=True)
    db.add(res); db.commit()

    low_vuln = User(name="低脆弱度長者", roles=["elderly"], lat=24.151, lng=120.681)
    high_vuln = User(name="高脆弱度長者", roles=["elderly"], lat=24.151, lng=120.681)
    db.add_all([low_vuln, high_vuln]); db.commit()

    # 高脆弱度：近 7 天多次 no_response
    for days_ago in range(1, 6):
        db.add(DailyCheckin(elderly_id=high_vuln.id, date=date.today() - timedelta(days=days_ago),
                             status="no_response"))
    db.commit()

    now = datetime.now()
    t_low = now - timedelta(minutes=5) if high_vuln_created_later else now
    t_high = now if high_vuln_created_later else now - timedelta(minutes=5)

    need_low = CommunityNeed(requester_id=low_vuln.id, need_type="water", urgency=3,
                              lat=24.151, lng=120.681, created_at=t_low)
    need_high = CommunityNeed(requester_id=high_vuln.id, need_type="water", urgency=3,
                               lat=24.151, lng=120.681, created_at=t_high)
    db.add_all([need_low, need_high])
    db.commit()

    db.add(SystemConfig(key="mode", value="emergency"))
    db.commit()
    return str(need_low.id), str(need_high.id), str(res.id)


def test_scarce_resource_is_not_suggested_to_two_needs_at_once(db):
    need_low_id, need_high_id, res_id = _make_scarce_resource_scenario(db)
    db.close()

    result = dispatch.auto_dispatch()
    assert result["suggested"] == 1, f"只有 1 份資源，最多只能建議出去 1 次：{result}"
    assert result["skipped"] == 1, f"另一筆搶不到資源的需求應該被跳過：{result}"

    db2 = SessionLocal()
    n_low = db2.query(CommunityNeed).filter(CommunityNeed.id == need_low_id).first()
    n_high = db2.query(CommunityNeed).filter(CommunityNeed.id == need_high_id).first()
    claimed = [n for n in (n_low, n_high) if str(n.matched_resource_id) == res_id]
    assert len(claimed) == 1, (
        f"同一份資源不該同時被兩筆需求宣稱擁有，實際: "
        f"low.matched={n_low.matched_resource_id}, high.matched={n_high.matched_resource_id}"
    )
    db2.close()


def test_higher_vulnerability_wins_scarce_resource_even_if_created_later(db):
    """
    高脆弱度長者的需求「較晚」建立，但因為脆弱度高，資源不足時應該
    優先分配給她——不是單純按建立時間 FIFO。
    """
    need_low_id, need_high_id, res_id = _make_scarce_resource_scenario(
        db, high_vuln_created_later=True
    )
    db.close()

    dispatch.auto_dispatch()

    db2 = SessionLocal()
    n_high = db2.query(CommunityNeed).filter(CommunityNeed.id == need_high_id).first()
    n_low = db2.query(CommunityNeed).filter(CommunityNeed.id == need_low_id).first()
    assert str(n_high.matched_resource_id) == res_id, (
        "高脆弱度的需求即使較晚建立，資源不足時也應該優先分配給她，"
        f"實際 high.status={n_high.status}（matched_resource_id={n_high.matched_resource_id}）, "
        f"low.status={n_low.status}"
    )
    assert n_low.status == "open"
    db2.close()


def test_urgency_still_takes_priority_over_vulnerability(db):
    """脆弱度是同一緊急度內的排序依據，不該蓋過緊急度本身——
    緊急度 5 的低脆弱度需求，還是該排在緊急度 3 的高脆弱度需求前面。"""
    vol = User(name="志工", roles=["volunteer"], line_uid="Uvol2", lat=24.15, lng=120.68)
    db.add(vol); db.commit()
    res = CommunityResource(owner_id=vol.id, resource_type="water", name="唯一的水2",
                             lat=24.15, lng=120.68, is_available=True)
    db.add(res); db.commit()
    res_id = str(res.id)

    urgent_low_vuln = User(name="緊急但低脆弱度", roles=["elderly"], lat=24.151, lng=120.681)
    calm_high_vuln = User(name="不急但高脆弱度", roles=["elderly"], lat=24.151, lng=120.681)
    db.add_all([urgent_low_vuln, calm_high_vuln]); db.commit()

    for days_ago in range(1, 7):
        db.add(DailyCheckin(elderly_id=calm_high_vuln.id, date=date.today() - timedelta(days=days_ago),
                             status="no_response"))
    db.commit()

    need_urgent = CommunityNeed(requester_id=urgent_low_vuln.id, need_type="water", urgency=5,
                                 lat=24.151, lng=120.681)
    need_calm = CommunityNeed(requester_id=calm_high_vuln.id, need_type="water", urgency=2,
                               lat=24.151, lng=120.681)
    db.add_all([need_urgent, need_calm])
    db.commit()
    need_urgent_id, need_calm_id = str(need_urgent.id), str(need_calm.id)

    db.add(SystemConfig(key="mode", value="emergency"))
    db.commit()
    db.close()

    dispatch.auto_dispatch()

    db2 = SessionLocal()
    n_urgent = db2.query(CommunityNeed).filter(CommunityNeed.id == need_urgent_id).first()
    assert str(n_urgent.matched_resource_id) == res_id, "緊急度 5 應該優先於脆弱度分數，先搶到資源"
    db2.close()


def test_batch_assignment_beats_greedy_first_come(db):
    """
    經典指派問題反例：A 兩個候選都能用（X 較近、Y 略遠但仍在範圍內），
    B 只有一個候選能用（X，Y 已超出 B 的緊急度距離上限）。

    貪婪法依 (urgency, vulnerability) 排序先處理 A（脆弱度較高），A
    會搶走「對自己來說分數最高」的 X，導致 B 完全沒有資源可用——即使
    把 X 讓給 B、A 退而求其次改用 Y，兩筆需求明明都能被滿足。

    批次最佳指派（匈牙利演算法）看的是全域總分數最大化，會找到
    A→Y、B→X 這個兩筆需求都被滿足的解，即使 A 單筆的分數因此變低。
    """
    vol1 = User(name="志工1", roles=["volunteer"], line_uid="Uvol_x", lat=24.150, lng=120.670)
    vol2 = User(name="志工2", roles=["volunteer"], line_uid="Uvol_y", lat=24.150, lng=120.670)
    db.add_all([vol1, vol2]); db.commit()

    res_x = CommunityResource(owner_id=vol1.id, resource_type="water", name="X",
                               lat=24.150, lng=120.670, is_available=True)
    res_y = CommunityResource(owner_id=vol2.id, resource_type="water", name="Y",
                               lat=24.204, lng=120.670, is_available=True)
    db.add_all([res_x, res_y]); db.commit()

    elder_a = User(name="A", roles=["elderly"], lat=24.159, lng=120.670)  # ~1km 自 X，~5km 自 Y
    elder_b = User(name="B", roles=["elderly"], lat=24.105, lng=120.670)  # ~5km 自 X，~11km 自 Y（超出上限）
    db.add_all([elder_a, elder_b]); db.commit()

    # A 脆弱度較高，貪婪排序會先處理 A
    for d in range(1, 4):
        db.add(DailyCheckin(elderly_id=elder_a.id, date=date.today() - timedelta(days=d), status="no_response"))
    db.commit()

    need_a = CommunityNeed(requester_id=elder_a.id, need_type="water", urgency=3, lat=24.159, lng=120.670)
    need_b = CommunityNeed(requester_id=elder_b.id, need_type="water", urgency=3, lat=24.105, lng=120.670)
    db.add_all([need_a, need_b])
    db.commit()
    need_a_id, need_b_id = str(need_a.id), str(need_b.id)

    db.add(SystemConfig(key="mode", value="emergency"))
    db.commit()
    db.close()

    result = dispatch.auto_dispatch()
    assert result["suggested"] == 2, f"兩筆需求都該被滿足，實際：{result}"

    db2 = SessionLocal()
    n_a = db2.query(CommunityNeed).filter(CommunityNeed.id == need_a_id).first()
    n_b = db2.query(CommunityNeed).filter(CommunityNeed.id == need_b_id).first()
    assert n_a.status == "suggested" and n_b.status == "suggested"
    assert n_a.matched_resource_id != n_b.matched_resource_id
    db2.close()
