# -*- coding: utf-8 -*-
"""
豐富示範資料（決賽 demo 用）
========================================
跟 seed.py 不一樣：這支腳本是「附加」的，不會清空任何既有資料——
可以安全地在正式環境上跑，不會動到已經存在的真實使用者或既有 demo
資料。用一個固定的識別碼（SOURCE_TAG）標記自己建立的資料，重複執行
會先偵測、不重複塞入。

目的：
  1. 建立數量夠多、地址/角色夠多樣的長者/志工/家屬，讓地圖跟列表
     看起來像真的在運作的社區，而不是 3 個假名字。
  2. 建立過去 14 天的打卡歷史（有正常也有異常），讓「歷史趨勢分析」
     頁面有真實資料可以畫，也讓脆弱度評分公式（app/services/dispatch.py
     的 _vulnerability_pts）在 demo 現場能算出有意義、非零的分數。
  3. 特別設計一位「王秀霞」——對應計畫書敘事裡的「王奶奶」原型：
     近 7 天有多次打卡異常、目前有未解決警報、身邊只有 1 位照顧
     聯絡人——讓她在 demo 現場的自動媒合裡「真的」因為脆弱度評分
     被排到最前面，不是喊假的。

用法：
  python seed_rich_demo.py           # 附加豐富示範資料
  python seed_rich_demo.py --wipe    # 只清掉這支腳本自己建立過的資料，不動其他人
"""
import sys
import os
import random
import argparse
from datetime import date, timedelta, datetime

sys.path.insert(0, os.path.dirname(__file__))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.database import SessionLocal, engine, Base
from app.models.user import User
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.alert import Alert
from app.models.resource import CommunityResource
from app.models.need import CommunityNeed
import app.models.resource_point  # noqa: F401 確保建表時包含這張表

SOURCE_TAG = "[RICH_DEMO]"  # 附加在 note/quantity 這類自由文字欄位，方便之後辨識與清除
random.seed(42)  # 每次跑產生一樣的資料，方便 demo 前重跑驗證

# ──────────────────────────────────────────────────────────
# 台中市各行政區的大致座標範圍（跟 seed_resource_points.py 的資源點
# 座標同一個城市，讓距離評分看起來合理）
# ──────────────────────────────────────────────────────────
DISTRICTS = [
    ("南區",  24.118, 120.679, ["建國南路", "五權南路", "崇倫街", "工學路", "美村路"]),
    ("東區",  24.140, 120.698, ["復興路四段", "自由路三段", "進化路", "東光路"]),
    ("北區",  24.156, 120.682, ["健行路", "進化北路", "太原路", "育才街"]),
    ("西區",  24.148, 120.665, ["民權路", "五權路", "精誠路", "美村路一段"]),
    ("西屯區", 24.163, 120.644, ["文心路二段", "台灣大道四段", "西屯路二段", "福星路"]),
    ("南屯區", 24.138, 120.643, ["公益路二段", "文心南路", "永春東路"]),
    ("北屯區", 24.175, 120.706, ["崇德路三段", "松竹路二段", "太原路三段"]),
]

SURNAMES = ["陳","林","黃","張","李","王","吳","劉","蔡","楊","許","鄭","謝","洪","邱","曾","廖","賴","徐","周"]
ELDER_GIVEN = ["秀琴","秀娟","秀霞","阿枝","阿珠","金蓮","月娥","素貞","玉蘭","阿嬤","文雄","進財","再興","水金","金龍","石頭","阿田","來發","有志","阿榮"]
VOL_GIVEN   = ["志明","建宏","冠廷","佳蓉","怡君","雅婷","家豪","彥廷","詩涵","柏翰","筱雯","宗翰"]
FAMILY_GIVEN = ["小明","小華","美玲","淑芬","俊傑","雅雯","家瑋","惠如"]


def _rand_addr():
    dist, lat0, lng0, roads = random.choice(DISTRICTS)
    road = random.choice(roads)
    num = random.randint(1, 300)
    lat = lat0 + random.uniform(-0.012, 0.012)
    lng = lng0 + random.uniform(-0.012, 0.012)
    return f"台中市{dist}{road}{num}號", round(lat, 5), round(lng, 5)


def _rand_phone():
    return f"09{random.randint(10,99)}{random.randint(100000,999999)}"


def wipe():
    db = SessionLocal()
    try:
        tagged_resources = db.query(CommunityResource).filter(CommunityResource.note == SOURCE_TAG).all()
        tagged_needs = db.query(CommunityNeed).filter(CommunityNeed.description.like(f"{SOURCE_TAG}%")).all()
        tagged_users = db.query(User).filter(User.address.like(f"%{SOURCE_TAG}%")).all()

        for r in tagged_resources:
            db.delete(r)
        for n in tagged_needs:
            db.delete(n)
        for u in tagged_users:
            db.query(CareRelation).filter(
                (CareRelation.elderly_id == u.id) | (CareRelation.contact_id == u.id)
            ).delete(synchronize_session=False)
            db.query(Alert).filter(Alert.elderly_id == u.id).delete(synchronize_session=False)
            db.query(DailyCheckin).filter(DailyCheckin.elderly_id == u.id).delete(synchronize_session=False)
            db.delete(u)
        db.commit()
        print(f"[wipe] 已清除 {len(tagged_users)} 位示範使用者、{len(tagged_resources)} 筆物資、{len(tagged_needs)} 筆需求")
    finally:
        db.close()


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        existing_marker = db.query(User).filter(User.address.like(f"%{SOURCE_TAG}%")).first()
        if existing_marker:
            print("[seed_rich_demo] 偵測到已經跑過，略過（如需重跑請先執行 --wipe）")
            return

        today = date.today()

        # ── 長者（含王秀霞主角）──────────────────────────
        elderly = []
        for i in range(22):
            name = random.choice(SURNAMES) + random.choice(ELDER_GIVEN)
            addr, lat, lng = _rand_addr()
            u = User(
                name=name, roles=["elderly"], phone=_rand_phone(),
                address=f"{addr}（{SOURCE_TAG}）", lat=lat, lng=lng, is_active=True,
            )
            db.add(u)
            elderly.append(u)
        db.commit()

        # 主角：王秀霞——近 7 天多次異常、只有 1 位照顧聯絡人、目前有未解決警報
        protagonist = elderly[0]
        protagonist.name = "王秀霞"
        db.commit()

        # ── 志工 ──────────────────────────────────────
        volunteers = []
        for i in range(12):
            name = random.choice(SURNAMES) + random.choice(VOL_GIVEN)
            addr, lat, lng = _rand_addr()
            u = User(
                name=name, roles=["volunteer"], phone=_rand_phone(),
                address=f"{addr}（{SOURCE_TAG}）", lat=lat, lng=lng, is_active=True,
            )
            db.add(u)
            volunteers.append(u)
        db.commit()

        # ── 家屬 ──────────────────────────────────────
        family = []
        for i in range(8):
            name = random.choice(SURNAMES) + random.choice(FAMILY_GIVEN)
            addr, lat, lng = _rand_addr()
            u = User(
                name=name, roles=["family"], phone=_rand_phone(),
                address=f"{addr}（{SOURCE_TAG}）", lat=lat, lng=lng, is_active=True,
            )
            db.add(u)
            family.append(u)
        db.commit()

        # ── 照護關係：大部分長者配 1-2 位聯絡人，主角只配 1 位 ──
        db.add(CareRelation(elderly_id=protagonist.id, contact_id=family[0].id,
                             relation="女兒", notify_order=1, is_active=True))
        for e in elderly[1:]:
            n_contacts = random.choice([1, 1, 2, 2, 0])  # 少數獨居無聯絡人，凸顯對比
            pool = family + volunteers
            random.shuffle(pool)
            for order, contact in enumerate(pool[:n_contacts], start=1):
                db.add(CareRelation(elderly_id=e.id, contact_id=contact.id,
                                     relation="家屬" if contact in family else "志工",
                                     notify_order=order, is_active=True))
        db.commit()

        # ── 過去 14 天打卡歷史 ──────────────────────────
        for e in elderly:
            is_protagonist = (e.id == protagonist.id)
            for days_ago in range(14, 0, -1):
                d = today - timedelta(days=days_ago)
                if is_protagonist and days_ago <= 6:
                    # 近 7 天：3 次沒回應、其餘正常，塑造真實的高脆弱度分數
                    status = random.choice(["no_response", "no_response", "ok", "help_needed"])
                elif is_protagonist:
                    status = "ok"
                else:
                    # 一般長者：九成正常，偶爾漏一次
                    status = random.choices(["ok", "no_response", "pending"], weights=[85, 10, 5])[0]

                responded_at = (
                    datetime.combine(d, datetime.min.time()).replace(hour=8)
                    + timedelta(minutes=random.randint(1, 90))
                    if status == "ok" else None
                )
                db.add(DailyCheckin(
                    elderly_id=e.id, date=d, status=status,
                    responded_at=responded_at,
                    created_at=datetime.combine(d, datetime.min.time()).replace(hour=8),
                ))
        db.commit()

        # ── 對應打卡異常的警報（部分已解決、少數還在燒）──
        checkins = db.query(DailyCheckin).filter(
            DailyCheckin.status.in_(["no_response", "help_needed"])
        ).all()
        for i, c in enumerate(checkins):
            is_recent = (today - c.date).days <= 1
            db.add(Alert(
                elderly_id=c.elderly_id, checkin_id=c.id,
                alert_type="help_needed" if c.status == "help_needed" else "no_response_3h",
                notified_users=[], status="sent" if is_recent else "resolved",
                created_at=datetime.combine(c.date, datetime.min.time()).replace(hour=11),
                resolved_at=None if is_recent else datetime.combine(c.date, datetime.min.time()).replace(hour=14),
            ))
        db.commit()

        # ── 個人物資（志工登記，類型/數量多樣）──────────
        RES_TYPES = ["water", "food", "first_aid", "shelter", "vehicle", "tool", "other"]
        RES_NAMES = {
            "water": ["礦泉水（整箱）", "桶裝飲用水", "備用飲水20公升"],
            "food": ["乾糧餅乾", "泡麵一箱", "白米10公斤", "罐頭食品"],
            "first_aid": ["急救箱", "常備藥品", "OK繃/消毒用品"],
            "shelter": ["空房可借宿", "車庫可暫住", "客廳沙發空間"],
            "vehicle": ["自用小客車", "機車可接送", "小貨車"],
            "tool": ["發電機", "手電筒+電池", "鏈鋸"],
            "other": ["禦寒毛毯", "行動電源", "尿布/成人紙尿褲"],
        }
        for v in volunteers:
            for _ in range(random.randint(1, 3)):
                rtype = random.choice(RES_TYPES)
                addr, lat, lng = _rand_addr()
                db.add(CommunityResource(
                    owner_id=v.id, resource_type=rtype,
                    name=f"{v.name}提供的{random.choice(RES_NAMES[rtype])}",
                    quantity=random.choice(["1份", "2箱", "約10人份", "1組", "數量充足"]),
                    address=addr, lat=lat, lng=lng,
                    is_available=random.random() > 0.15,
                    note=SOURCE_TAG,
                ))
        db.commit()

        # ── 幾筆歷史已完成需求（讓歷史紀錄看起來有真的運作過）──
        NEED_TYPES = ["water", "food", "first_aid", "shelter"]
        for e in random.sample(elderly, 6):
            ntype = random.choice(NEED_TYPES)
            db.add(CommunityNeed(
                requester_id=e.id, need_type=ntype,
                description=f"{SOURCE_TAG} 過去需求範例：需要{ntype}",
                address=e.address, lat=e.lat, lng=e.lng,
                urgency=random.randint(1, 3), status="fulfilled",
                created_at=datetime.now() - timedelta(days=random.randint(2, 12)),
            ))
        db.commit()

        print(f"[seed_rich_demo] 完成：長者 {len(elderly)} 位（含主角「{protagonist.name}」）、"
              f"志工 {len(volunteers)} 位、家屬 {len(family)} 位、"
              f"14 天打卡歷史、{len(checkins)} 筆警報、物資與歷史需求資料已建立")
        print(f"[seed_rich_demo] 主角 elderly_id = {protagonist.id}（demo 時可用這個 id 驗證脆弱度分數）")

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--wipe", action="store_true")
    args = parser.parse_args()
    if args.wipe:
        wipe()
    else:
        run()
