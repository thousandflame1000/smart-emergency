# -*- coding: utf-8 -*-
"""
Demo 資料 seed
直接執行：python seed.py
被 startup.py import 時也會執行 run()
"""
import sys, os
sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.database import SessionLocal
from app.models.user import User
from app.models.care_relation import CareRelation
from app.models.resource import CommunityResource
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from datetime import date, datetime


def run():
    db = SessionLocal()

    print("=" * 50)
    print("開始塞入假資料")
    print("=" * 50)

    # ── 清空舊資料（重跑用）────────────────────────
    for model in [DailyCheckin, CareRelation, CommunityResource, User, SystemConfig]:
        db.query(model).delete()
    db.commit()

    # ── 長者 ──────────────────────────────────────
    elderly = [
        User(name="陳月英", roles=["elderly"],
             phone="0912345678", address="台中市南區建國路 12 號",
             lat=24.1234, lng=120.6789, is_active=True),
        User(name="李明德", roles=["elderly"],
             phone="0923456789", address="台中市南區忠明路 88 號",
             lat=24.1300, lng=120.6820, is_active=True),
        User(name="王阿花", roles=["elderly"],
             phone="0934567890", address="台中市南區復興路 5 號",
             lat=24.1180, lng=120.6750, is_active=True),
    ]
    for e in elderly:
        db.add(e)

    # ── 家屬 ──────────────────────────────────────
    family = [
        User(name="陳小明", roles=["family"], phone="0956789012"),
        User(name="李美華", roles=["family"], phone="0967890123"),
    ]
    for f in family:
        db.add(f)

    # ── 志工 ──────────────────────────────────────
    volunteers = [
        User(name="張志工甲", roles=["volunteer"],
             phone="0978901234", address="台中市南區五權路 20 號",
             lat=24.1260, lng=120.6800),
        User(name="林志工乙", roles=["volunteer"],
             phone="0989012345", address="台中市南區建成路 33 號",
             lat=24.1220, lng=120.6770),
    ]
    for v in volunteers:
        db.add(v)

    db.commit()
    print("使用者建立完成：長者 3、家屬 2、志工 2")

    # ── 關係 ──────────────────────────────────────
    relations = [
        CareRelation(elderly_id=elderly[0].id, contact_id=family[0].id,
                     relation="family", notify_order=1),
        CareRelation(elderly_id=elderly[0].id, contact_id=volunteers[0].id,
                     relation="volunteer", notify_order=2),
        CareRelation(elderly_id=elderly[1].id, contact_id=family[1].id,
                     relation="family", notify_order=1),
        CareRelation(elderly_id=elderly[1].id, contact_id=volunteers[0].id,
                     relation="volunteer", notify_order=2),
        CareRelation(elderly_id=elderly[2].id, contact_id=volunteers[1].id,
                     relation="volunteer", notify_order=1),
    ]
    for r in relations:
        db.add(r)
    db.commit()
    print("關係建立完成")

    # ── 物資 ──────────────────────────────────────
    resources = [
        CommunityResource(owner_id=volunteers[0].id, resource_type="water",
                          name="備用飲用水", quantity="約 50 公升",
                          address="台中市南區五權路 20 號",
                          lat=24.1260, lng=120.6800, is_available=True),
        CommunityResource(owner_id=volunteers[0].id, resource_type="first_aid",
                          name="急救箱", quantity="1 組",
                          address="台中市南區五權路 20 號",
                          lat=24.1260, lng=120.6800, is_available=True),
        CommunityResource(owner_id=volunteers[1].id, resource_type="food",
                          name="乾糧（餅乾、罐頭）", quantity="約 30 人份",
                          address="台中市南區建成路 33 號",
                          lat=24.1220, lng=120.6770, is_available=True),
        CommunityResource(owner_id=volunteers[1].id, resource_type="shelter",
                          name="可借宿空間", quantity="可容納 4 人",
                          address="台中市南區建成路 33 號",
                          lat=24.1220, lng=120.6770, is_available=True),
    ]
    for r in resources:
        db.add(r)
    db.commit()
    print("物資登記完成：4 筆")

    # ── 今日打卡（模擬三種狀態）──────────────────
    statuses = ["ok", "no_response", "pending"]
    for i, e in enumerate(elderly):
        status = statuses[i]
        c = DailyCheckin(
            elderly_id=e.id,
            date=date.today(),
            status=status,
            responded_at=datetime.now() if status == "ok" else None,
            created_at=datetime.now(),
        )
        db.add(c)
        print(f"  {e.name} 今日打卡：{status}")
    db.commit()

    # ── 系統設定 ──────────────────────────────────
    configs = [
        SystemConfig(key="mode",                  value="normal"),
        SystemConfig(key="checkin_hour",          value="8"),
        SystemConfig(key="checkin_minute",        value="0"),
        SystemConfig(key="alert_threshold_1_min", value="60"),
        SystemConfig(key="alert_threshold_2_min", value="180"),
    ]
    for c in configs:
        db.add(c)
    db.commit()
    db.close()

    print()
    print("=" * 50)
    print("假資料塞入完成！")
    print("  長者：3 位  家屬：2 位  志工：2 位")
    print("  物資：4 筆  今日打卡：3 筆")
    print("=" * 50)


if __name__ == "__main__":
    run()
