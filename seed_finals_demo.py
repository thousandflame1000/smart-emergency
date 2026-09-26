# -*- coding: utf-8 -*-
"""
決賽示範資料（花蓮光復／瑞穗）
================================================
線上資料庫先前混了三代資料：`[TYPHOON_SIM]` 那批的「姓名」其實是英文地名
（ruisui居民1（情境模擬）），另一批是台中的測試資料，兩地相距約 120 公里，
距離評分算出來沒有意義；再加上長期累積、無人能回應的打卡警報。這支腳本
把營運資料清乾淨，換成單一地區、彼此距離合理的示範資料。

**保留不動**（這些不是示範雜訊，刪掉會弄壞功能）：
  topology_workspaces  工作區圖資，含兩千多個道路節點 → 道路搜尋靠它
  knowledge_base       知識庫內容
  zones                分區定義
  system_config        系統設定

用法：
  python seed_finals_demo.py --plan     只印出會做什麼，不碰資料庫
  python seed_finals_demo.py --reset    清空營運資料（正式資料庫要再加 --force）
  python seed_finals_demo.py            建立示範資料
  python seed_finals_demo.py --reset --force && python seed_finals_demo.py

資料真實性說明：
  鄉鎮、村里與機構名稱（鄉公所、國小、衛生所、消防分隊）是真實存在的地名，
  但**門牌號碼與座標是鄉鎮中心附近的概略值**，無法離線查證到街廓精度。
  資源點帶 source="demo_seed" 與一句白話備註，不可當成查核過的設施名錄使用。

  標記不寫進地址或描述。線上先前那批資料把 `（[TYPHOON_SIM]）` 接在地址後面，
  於是後台名單、派遣卡與 LINE 訊息全都印著這串內部代碼給使用者看。
  這裡改用 system_config 的一列當作「已建立過」的判斷依據。
"""
import argparse
import json
import os
import random
import sys
from datetime import date, datetime, timedelta

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
try:
    sys.stdout.reconfigure(encoding="utf-8")
except Exception:
    pass

from app.database import Base, SessionLocal, _is_sqlite, engine
from app.labels import need_type
from app.models.alert import Alert
from app.models.care_relation import CareRelation
from app.models.checkin import DailyCheckin
from app.models.config import SystemConfig
from app.models.dispatch_event import DispatchEvent
from app.models.need import CommunityNeed
from app.models.resource import CommunityResource
from app.models.resource_point import ResourcePoint
from app.models.user import User
from app.models.volunteer_application import VolunteerApplication

SEED_MARKER_KEY = "finals_demo_seeded_at"
random.seed(20261003)  # 決賽日期當種子：每次重跑結果一致，方便彩排前反覆驗證

# 主角家的位置（光復鄉大進村）。媒合的距離上限依緊急度而定，
# 緊急度 5 只看 2 公里內，所以主角與附近志工的相對位置要固定，不能隨機。
PROTAGONIST_AT = (23.66930, 121.42310)

# ──────────────────────────────────────────────────────────
# 花蓮縣光復鄉、瑞穗鄉、鳳林鎮
# 村里與路名為真實地名；座標是各村里中心附近的概略值。
# ──────────────────────────────────────────────────────────
DISTRICTS = [
    ("光復鄉", "大進村", 23.6693, 121.4231, ["中正路一段", "林森路", "大進街"]),
    ("光復鄉", "大同村", 23.6721, 121.4265, ["中山路三段", "民權街", "糖廠街"]),
    ("光復鄉", "大華村", 23.6650, 121.4188, ["中正路二段", "忠孝路"]),
    ("光復鄉", "大富村", 23.6300, 121.4180, ["大富街", "明德路"]),
    ("光復鄉", "大興村", 23.6820, 121.4300, ["大興街", "復興路"]),
    ("瑞穗鄉", "瑞穗村", 23.4966, 121.3775, ["中山路二段", "成功路", "民生街"]),
    ("瑞穗鄉", "瑞美村", 23.5030, 121.3740, ["中山路三段", "瑞美街"]),
    ("瑞穗鄉", "富源村", 23.5590, 121.3720, ["富源街", "中正路"]),
    ("瑞穗鄉", "舞鶴村", 23.4790, 121.3560, ["舞鶴街", "溫泉路"]),
    ("鳳林鎮", "鳳仁里", 23.7450, 121.4520, ["中正路一段", "光復路"]),
]

SURNAMES = ["陳", "林", "黃", "張", "李", "王", "吳", "劉", "蔡", "楊",
            "許", "鄭", "謝", "洪", "邱", "曾", "廖", "賴", "徐", "周"]
ELDER_GIVEN = ["秀琴", "秀娟", "阿枝", "阿珠", "金蓮", "月娥", "素貞", "玉蘭",
               "文雄", "進財", "再興", "水金", "金龍", "阿田", "來發", "有志", "阿榮"]
VOL_GIVEN = ["志明", "建宏", "冠廷", "佳蓉", "怡君", "雅婷", "家豪", "彥廷",
             "詩涵", "柏翰", "筱雯", "宗翰"]
FAMILY_GIVEN = ["小明", "小華", "美玲", "淑芬", "俊傑", "雅雯", "家瑋", "惠如"]

# 機構名稱真實；門牌與座標為概略值，故一律帶標記。
FACILITIES = [
    ("光復鄉公所", "government", 23.6695, 121.4224, "花蓮縣光復鄉中正路一段"),
    ("光復鄉衛生所", "clinic", 23.6688, 121.4240, "花蓮縣光復鄉中正路一段"),
    ("光復國小", "shelter", 23.6710, 121.4250, "花蓮縣光復鄉中山路三段"),
    ("光復國中", "shelter", 23.6672, 121.4205, "花蓮縣光復鄉林森路"),
    ("大進國小", "shelter", 23.6640, 121.4170, "花蓮縣光復鄉大進村"),
    ("花蓮縣消防局光復分隊", "fire_station", 23.6702, 121.4218, "花蓮縣光復鄉中正路一段"),
    ("光復糖廠", "community", 23.6738, 121.4260, "花蓮縣光復鄉糖廠街"),
    ("大富社區活動中心", "community", 23.6305, 121.4175, "花蓮縣光復鄉大富村"),
    ("瑞穗鄉公所", "government", 23.4962, 121.3778, "花蓮縣瑞穗鄉中山路二段"),
    ("瑞穗鄉衛生所", "clinic", 23.4970, 121.3762, "花蓮縣瑞穗鄉中山路二段"),
    ("瑞穗國小", "shelter", 23.4980, 121.3790, "花蓮縣瑞穗鄉中山路三段"),
    ("瑞穗國中", "shelter", 23.4941, 121.3752, "花蓮縣瑞穗鄉中正南路"),
    ("花蓮縣消防局瑞穗分隊", "fire_station", 23.4958, 121.3770, "花蓮縣瑞穗鄉中山路二段"),
    ("富源國小", "shelter", 23.5585, 121.3715, "花蓮縣瑞穗鄉富源村"),
    ("富源社區活動中心", "community", 23.5600, 121.3730, "花蓮縣瑞穗鄉富源村"),
    ("鳳林鎮公所", "government", 23.7455, 121.4518, "花蓮縣鳳林鎮中正路一段"),
    ("衛生福利部玉里醫院鳳林分院", "clinic", 23.7430, 121.4540, "花蓮縣鳳林鎮"),
    ("鳳林國中", "shelter", 23.7470, 121.4505, "花蓮縣鳳林鎮中正路"),
]

POINT_SUPPLIES = {
    "shelter": {"shelter": "有", "water": "有限", "food": "有限"},
    "fire_station": {"first_aid": "有", "tool": "有", "vehicle": "有"},
    "clinic": {"first_aid": "有"},
    "government": {"water": "有限", "other": "有限"},
    "community": {"shelter": "有限", "water": "有限"},
}

# 清空這些（營運資料）。不在清單上的表一律不動。
WIPE_MODELS = [
    Alert, DailyCheckin, CareRelation, DispatchEvent, CommunityNeed,
    CommunityResource, VolunteerApplication, ResourcePoint, User,
]
KEEP_NOTE = "topology_workspaces（道路圖資）、knowledge_base、zones、system_config 保持不動"


def _addr():
    town, village, lat0, lng0, roads = random.choice(DISTRICTS)
    road = random.choice(roads)
    return (f"花蓮縣{town}{village}{road}{random.randint(1, 180)}號",
            round(lat0 + random.uniform(-0.006, 0.006), 5),
            round(lng0 + random.uniform(-0.006, 0.006), 5))


def _phone():
    return f"09{random.randint(10, 99)}{random.randint(100000, 999999)}"


def _count(db, model) -> int:
    """表還沒建立時回 0——--plan 必須在任何情況下都能安全執行。"""
    try:
        return db.query(model).count()
    except Exception:
        db.rollback()
        return 0


def reset(force: bool = False):
    db = SessionLocal()
    try:
        total = sum(_count(db, m) for m in WIPE_MODELS)
        if total > 0 and not _is_sqlite and not force:
            print("這個資料庫不是 SQLite，而且已經有 "
                  f"{total} 筆營運資料——看起來是正式環境。\n"
                  "為了避免誤刪，預設拒絕執行。確定要清空請加 --force。")
            sys.exit(1)
        counts = {}
        for model in WIPE_MODELS:
            counts[model.__tablename__] = db.query(model).delete()
        # 清掉「已建立過」的標記，否則接著跑 seed 會被略過。
        db.query(SystemConfig).filter(SystemConfig.key == SEED_MARKER_KEY).delete()
        db.commit()
        for name, n in counts.items():
            print(f"  清除 {name}: {n} 筆")
        print(f"[reset] 共 {sum(counts.values())} 筆。{KEEP_NOTE}")
    finally:
        db.close()


def run():
    Base.metadata.create_all(bind=engine)
    db = SessionLocal()
    try:
        if db.query(SystemConfig).filter(SystemConfig.key == SEED_MARKER_KEY).first():
            print("[seed] 偵測到已經跑過，略過。要重建請先執行 --reset")
            return
        today = date.today()

        # 主角對應計畫書敘事裡的「王奶奶」。位置寫死在光復鄉大進村，因為
        # 緊急度 5 的距離上限是 2 公里（URGENCY_MAX_KM）：志工若隨機散在
        # 三個鄉鎮、31 公里的範圍裡，她附近很可能一個帶水的志工都沒有，
        # 「待派」卡就會顯示「目前沒有可用的志工物資」——決賽最關鍵的
        # 那張卡直接開天窗。
        protagonist = User(
            name="王秀霞", roles=["elderly"], phone=_phone(),
            address="花蓮縣光復鄉大進村大進街48號",
            lat=PROTAGONIST_AT[0], lng=PROTAGONIST_AT[1], is_active=True)
        db.add(protagonist)
        elderly = [protagonist]
        for _ in range(19):
            addr, lat, lng = _addr()
            u = User(name=random.choice(SURNAMES) + random.choice(ELDER_GIVEN),
                     roles=["elderly"], phone=_phone(),
                     address=addr, lat=lat, lng=lng, is_active=True)
            db.add(u)
            elderly.append(u)
        db.commit()

        volunteers = []
        # 先放四位在主角步行距離內，確保媒合一定算得出志工候選。
        for i in range(4):
            lat = round(PROTAGONIST_AT[0] + random.uniform(-0.008, 0.008), 5)
            lng = round(PROTAGONIST_AT[1] + random.uniform(-0.008, 0.008), 5)
            u = User(name=random.choice(SURNAMES) + random.choice(VOL_GIVEN),
                     roles=["volunteer"], phone=_phone(),
                     address=f"花蓮縣光復鄉大進村{random.choice(['中正路一段', '林森路', '大進街'])}"
                             f"{random.randint(1, 120)}號",
                     lat=lat, lng=lng, is_active=True)
            db.add(u)
            volunteers.append(u)
        for _ in range(6):
            addr, lat, lng = _addr()
            u = User(name=random.choice(SURNAMES) + random.choice(VOL_GIVEN),
                     roles=["volunteer"], phone=_phone(),
                     address=addr, lat=lat, lng=lng, is_active=True)
            db.add(u)
            volunteers.append(u)
        db.commit()

        family = []
        for _ in range(8):
            addr, lat, lng = _addr()
            u = User(name=random.choice(SURNAMES) + random.choice(FAMILY_GIVEN),
                     roles=["family"], phone=_phone(),
                     address=addr, lat=lat, lng=lng, is_active=True)
            db.add(u)
            family.append(u)
        db.commit()

        db.add(CareRelation(elderly_id=protagonist.id, contact_id=family[0].id,
                            relation="女兒", notify_order=1, is_active=True))
        for e in elderly[1:]:
            pool = family + volunteers
            random.shuffle(pool)
            for order, contact in enumerate(pool[:random.choice([1, 1, 2, 2, 0])], start=1):
                db.add(CareRelation(
                    elderly_id=e.id, contact_id=contact.id,
                    relation="家屬" if contact in family else "志工",
                    notify_order=order, is_active=True))
        db.commit()

        # 脆弱度上限是 checkin 12 + 警報 10 + 孤立 6。主角只有 1 位聯絡人（3 分），
        # 所以她的天花板是 25。她近 7 天要有 3 次以上異常才吃滿 12 分的 checkin 上限；
        # 其他長者的異常一律排在 8 天前（評分只看近 7 天），確保沒有人跟她同分。
        # 先前交給隨機決定，結果有人打平、主角掉到第 2 名。
        LEAD_RISK_DAYS = {1, 2, 4, 6}
        # 另外兩位近期也有一次未回應，警報列才不會只有主角一筆、像是假的。
        # 一次未回應 = 4 分 + 最多 5 分警報 + 最多 6 分孤立 = 15，仍遠低於主角的 25。
        secondary = {e.id for e in random.sample(elderly[1:], 2)}
        for e in elderly:
            lead = (e.id == protagonist.id)
            for days_ago in range(14, 0, -1):
                d = today - timedelta(days=days_ago)
                if lead:
                    status = ("help_needed" if days_ago == 1
                              else "no_response" if days_ago in LEAD_RISK_DAYS else "ok")
                elif e.id in secondary and days_ago == 1:
                    status = "no_response"
                elif days_ago >= 8:
                    status = random.choices(["ok", "no_response", "pending"],
                                            weights=[80, 14, 6])[0]
                else:
                    status = random.choices(["ok", "pending"], weights=[93, 7])[0]
                base = datetime.combine(d, datetime.min.time()).replace(hour=8)
                db.add(DailyCheckin(
                    elderly_id=e.id, date=d, status=status, created_at=base,
                    responded_at=base + timedelta(minutes=random.randint(1, 90))
                    if status == "ok" else None))
        db.commit()

        # 舊的打卡異常一律標成已解決，只留最近一天的還在燒。
        # 先前線上那 16 筆全是未解決，演示時警報列整片紅、看不出重點。
        risky = db.query(DailyCheckin).filter(
            DailyCheckin.status.in_(["no_response", "help_needed"])).all()
        live = 0
        for c in risky:
            # 近兩天的還沒解決，更早的都已處理。主角在第 1、2 天都有異常，
            # 於是吃滿 10 分的警報上限；其他人最多一筆。
            recent = (today - c.date).days <= 2
            live += recent
            base = datetime.combine(c.date, datetime.min.time()).replace(hour=11)
            db.add(Alert(
                elderly_id=c.elderly_id, checkin_id=c.id,
                alert_type="help_needed" if c.status == "help_needed" else "no_response_3h",
                notified_users=[], status="sent" if recent else "resolved",
                created_at=base,
                resolved_at=None if recent else base + timedelta(hours=3)))
        db.commit()

        RES_NAMES = {
            "water": ["礦泉水（整箱）", "桶裝飲用水", "備用飲水20公升"],
            "food": ["乾糧餅乾", "泡麵一箱", "白米10公斤", "罐頭食品"],
            "first_aid": ["急救箱", "常備藥品", "消毒用品"],
            "shelter": ["空房可借宿", "車庫可暫住"],
            "vehicle": ["自用小客車", "機車可接送", "小貨車"],
            "tool": ["發電機", "手電筒+電池", "鏈鋸"],
            "other": ["禦寒毛毯", "行動電源", "成人紙尿褲"],
        }
        for i, v in enumerate(volunteers):
            # 前四位是主角附近那幾位：第一項固定是「可用的飲用水」，
            # 讓「申請飲用水 → 待派卡顯示最佳志工」這條演示路徑一定走得通。
            types = ["water"] if i < 4 else []
            types += random.sample(list(RES_NAMES), random.randint(1, 2))
            for j, rtype in enumerate(types):
                db.add(CommunityResource(
                    owner_id=v.id, resource_type=rtype,
                    name=f"{v.name}提供的{random.choice(RES_NAMES[rtype])}",
                    quantity=random.choice(["1份", "2箱", "約10人份", "1組"]),
                    address=v.address, lat=v.lat, lng=v.lng,
                    is_available=True if (i < 4 and j == 0) else random.random() > 0.15))
        db.commit()

        # 幾筆已完成的歷史需求，讓趨勢頁與紀錄看起來真的運作過。
        # 說明文字走共用的中文對照表；原本的 seeder 直接內插 need_type，
        # 於是描述裡寫著「需要water」。
        for e in random.sample(elderly, 6):
            ntype = random.choice(["water", "food", "first_aid", "shelter"])
            db.add(CommunityNeed(
                requester_id=e.id, need_type=ntype,
                description=f"歷史需求：需要{need_type(ntype)}",
                address=e.address, lat=e.lat, lng=e.lng,
                urgency=random.randint(1, 3), status="fulfilled",
                created_at=datetime.now() - timedelta(days=random.randint(2, 12))))
        db.commit()

        for name, ptype, lat, lng, addr in FACILITIES:
            db.add(ResourcePoint(
                name=name, point_type=ptype, lat=lat, lng=lng,
                address=addr, source="demo_seed",
                note="示範資料：機構名稱為真實地名，座標為鄉鎮概略位置",
                supplies_json=json.dumps(POINT_SUPPLIES.get(ptype, {}), ensure_ascii=False),
                is_active=True))
        db.commit()

        db.add(SystemConfig(key=SEED_MARKER_KEY, value=datetime.now().isoformat(timespec="seconds")))
        db.commit()

        print(f"[seed] 長者 {len(elderly)} 位（主角「{protagonist.name}」）、"
              f"志工 {len(volunteers)} 位、家屬 {len(family)} 位")
        print(f"[seed] 14 天打卡、警報 {len(risky)} 筆（未解決 {live} 筆）、"
              f"資源點 {len(FACILITIES)} 處")
        print(f"[seed] 主角 elderly_id = {protagonist.id}")
    finally:
        db.close()


def plan():
    db = SessionLocal()
    try:
        print("會清空（營運資料）：")
        for m in WIPE_MODELS:
            print(f"  {m.__tablename__:<24} 目前 {_count(db, m)} 筆")
        print(f"\n保持不動：{KEEP_NOTE}")
        print(f"\n會建立：長者 20、志工 10、家屬 8、資源點 {len(FACILITIES)} 處、"
              "14 天打卡歷史、6 筆歷史需求")
        print(f"地區：{'、'.join(sorted({d[0] for d in DISTRICTS}))}")
        where = "SQLite（本機）" if _is_sqlite else "非 SQLite（正式環境，--reset 需要 --force）"
        print(f"資料庫：{where}")
    finally:
        db.close()


if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true", help="只印出會做什麼，不碰資料庫")
    ap.add_argument("--reset", action="store_true", help="清空營運資料")
    ap.add_argument("--force", action="store_true", help="允許對非 SQLite 資料庫執行 --reset")
    a = ap.parse_args()
    if a.plan:
        plan()
    elif a.reset:
        reset(force=a.force)
    else:
        run()
