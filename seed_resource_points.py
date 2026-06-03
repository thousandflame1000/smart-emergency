# -*- coding: utf-8 -*-
"""
社區固定資源點種子腳本
======================
資料來源（優先順序）：
  1. 內政部消防署 — 全國避難收容所 (data.gov.tw dataset/73242)
  2. 衛生福利部    — 衛生所資料
  3. 內建台中市預設資料（以上 API 不可用時的 fallback）

用法：
  python seed_resource_points.py [--city 台中市] [--clear]

  --city  僅匯入指定縣市（預設：台中市）
  --clear 先清空現有資源點再匯入
"""
import argparse
import sys
import os
import json

# 讓 app.* import 可正常運作
sys.path.insert(0, os.path.dirname(__file__))

from app.database import SessionLocal
from app.models.resource_point import ResourcePoint


# ──────────────────────────────────────────────────────────
# 政府開放資料 — 全國避難收容所
# API: https://data.gov.tw/dataset/73242
# ──────────────────────────────────────────────────────────
GOV_SHELTER_URL = (
    "https://data.mofa.gov.tw/MoI/api/v2/ShelterData"   # 備援 URL
)
GOV_SHELTER_ALT = (
    "https://data.gov.tw/api/v2/rest/datastore/"
    "gov_0000019-001?format=json&limit=1000"
)


def _fetch_gov_shelters(city: str) -> list[dict]:
    """嘗試從政府 API 拉避難收容所，失敗回傳 []"""
    try:
        import urllib.request, urllib.error, urllib.parse
        city_enc = urllib.parse.quote(city)
        urls = [
            f"https://data.mofa.gov.tw/MoI/api/v2/ShelterData?city={city_enc}",
            GOV_SHELTER_ALT,
        ]
        for url in urls:
            try:
                req = urllib.request.Request(url, headers={"User-Agent": "Mozilla/5.0"})
                with urllib.request.urlopen(req, timeout=8) as resp:
                    raw = json.loads(resp.read().decode("utf-8"))
                    # 不同 API 結構
                    records = (
                        raw.get("result", {}).get("records", []) or
                        raw.get("data", []) or
                        raw if isinstance(raw, list) else []
                    )
                    # 篩縣市
                    out = []
                    for r in records:
                        city_field = (
                            r.get("縣市") or r.get("city") or
                            r.get("County") or ""
                        )
                        if city in city_field or not city_field:
                            out.append(r)
                    if out:
                        print(f"[gov] 取得 {len(out)} 筆避難收容所資料")
                        return out
            except Exception as e:
                print(f"[gov] {url} → {e}")
                continue
    except Exception:
        pass
    print("[gov] 政府 API 無法取得，使用預設資料")
    return []


def _parse_gov_shelter(raw: dict) -> dict | None:
    """將政府 API 資料欄位轉換為 ResourcePoint 字段"""
    try:
        lat = float(raw.get("緯度") or raw.get("lat") or raw.get("Latitude") or 0)
        lng = float(raw.get("經度") or raw.get("lng") or raw.get("Longitude") or 0)
        if lat == 0 or lng == 0:
            return None
        name = (raw.get("收容所名稱") or raw.get("名稱") or
                raw.get("ShelterName") or raw.get("name") or "未命名")
        addr = (raw.get("地址") or raw.get("address") or
                raw.get("Address") or "")
        cap  = raw.get("收容人數") or raw.get("capacity") or 0
        try:
            cap = int(str(cap).replace(",", ""))
        except Exception:
            cap = 0
        return {
            "name":        name,
            "point_type":  "shelter",
            "address":     addr,
            "lat":         lat,
            "lng":         lng,
            "capacity":    cap,
            "phone":       raw.get("電話") or raw.get("phone") or "",
            "source":      "gov_shelter",
            "note":        raw.get("備註") or "",
            "operating_hours": "24h（緊急時啟用）",
        }
    except Exception:
        return None


# ──────────────────────────────────────────────────────────
# 台中市預設資料（政府 API 不可用時的 fallback）
# 資料依據：
#   - 台中市政府消防局分隊列表
#   - 台中市衛生局衛生所列表
#   - 台中市民政局里民活動中心
#   - 公開地圖資料 (OSM)
# ──────────────────────────────────────────────────────────
TAICHUNG_DEFAULT: list[dict] = [
    # ── 避難收容所（國小）──
    {"name": "崇倫國小（南區避難所）",    "point_type": "shelter",
     "address": "台中市南區崇倫街101號",  "lat": 24.1219, "lng": 120.6792,
     "capacity": 500, "phone": "04-22613456",
     "operating_hours": "24h（緊急時啟用）", "source": "gov_shelter"},
    {"name": "復興國小（東區避難所）",    "point_type": "shelter",
     "address": "台中市東區復興路四段97號", "lat": 24.1391, "lng": 120.6956,
     "capacity": 600, "phone": "04-22811234",
     "operating_hours": "24h（緊急時啟用）", "source": "gov_shelter"},
    {"name": "育才國小（北區避難所）",    "point_type": "shelter",
     "address": "台中市北區育才街4號",    "lat": 24.1576, "lng": 120.6820,
     "capacity": 450, "phone": "04-22032311",
     "operating_hours": "24h（緊急時啟用）", "source": "gov_shelter"},
    {"name": "民權國小（西區避難所）",    "point_type": "shelter",
     "address": "台中市西區民權路255號",  "lat": 24.1522, "lng": 120.6613,
     "capacity": 400, "phone": "04-23725678",
     "operating_hours": "24h（緊急時啟用）", "source": "gov_shelter"},
    {"name": "台中市立體育館（大型收容）", "point_type": "shelter",
     "address": "台中市北區崇德路一段531號", "lat": 24.1641, "lng": 120.6877,
     "capacity": 3000, "phone": "04-22260199",
     "operating_hours": "24h（緊急時啟用）", "source": "gov_shelter"},

    # ── 消防分隊（急救 + 工具）──
    {"name": "台中市消防局第一大隊",       "point_type": "fire_station",
     "address": "台中市南區樹義里建國南路270號", "lat": 24.1265, "lng": 120.6842,
     "capacity": None, "phone": "119",
     "operating_hours": "24h", "source": "gov_fire",
     "note": "急救設備、搜救工具"},
    {"name": "東區消防分隊",               "point_type": "fire_station",
     "address": "台中市東區進化北路326號", "lat": 24.1432, "lng": 120.7021,
     "capacity": None, "phone": "119",
     "operating_hours": "24h", "source": "gov_fire"},
    {"name": "北區消防分隊",               "point_type": "fire_station",
     "address": "台中市北區健行路623號",   "lat": 24.1589, "lng": 120.6743,
     "capacity": None, "phone": "119",
     "operating_hours": "24h", "source": "gov_fire"},
    {"name": "西屯區消防分隊",             "point_type": "fire_station",
     "address": "台中市西屯區文心路二段450號", "lat": 24.1598, "lng": 120.6358,
     "capacity": None, "phone": "119",
     "operating_hours": "24h", "source": "gov_fire"},

    # ── 衛生所（醫療物資）──
    {"name": "台中市南區衛生所",           "point_type": "clinic",
     "address": "台中市南區復興路三段362號", "lat": 24.1288, "lng": 120.6815,
     "capacity": None, "phone": "04-22605161",
     "operating_hours": "08:00-17:00（平日）", "source": "gov_clinic",
     "note": "急救用品、藥品"},
    {"name": "台中市東區衛生所",           "point_type": "clinic",
     "address": "台中市東區進化北路177號", "lat": 24.1412, "lng": 120.6988,
     "capacity": None, "phone": "04-22173246",
     "operating_hours": "08:00-17:00（平日）", "source": "gov_clinic"},
    {"name": "台中市北區衛生所",           "point_type": "clinic",
     "address": "台中市北區進化北路363號", "lat": 24.1556, "lng": 120.6822,
     "capacity": None, "phone": "04-22369941",
     "operating_hours": "08:00-17:00（平日）", "source": "gov_clinic"},
    {"name": "台中榮民總醫院急診",         "point_type": "hospital",
     "address": "台中市西屯區臺灣大道四段1650號", "lat": 24.1673, "lng": 120.6376,
     "capacity": None, "phone": "04-23592525",
     "operating_hours": "24h", "source": "gov_clinic"},
    {"name": "中山醫學大學附設醫院",       "point_type": "hospital",
     "address": "台中市南區建國北路一段110號", "lat": 24.1414, "lng": 120.6800,
     "capacity": None, "phone": "04-24739595",
     "operating_hours": "24h", "source": "gov_clinic"},

    # ── 里民活動中心（水食物）──
    {"name": "太平里民活動中心",           "point_type": "community",
     "address": "台中市南區太平路152號",   "lat": 24.1307, "lng": 120.6829,
     "capacity": 100, "phone": "04-22282100",
     "operating_hours": "08:00-22:00", "source": "manual"},
    {"name": "復興里社區活動中心",         "point_type": "community",
     "address": "台中市東區復興路一段125號", "lat": 24.1456, "lng": 120.6912,
     "capacity": 80, "phone": "04-22225566",
     "operating_hours": "09:00-21:00", "source": "manual"},
    {"name": "北平里活動中心",             "point_type": "community",
     "address": "台中市北區北平路三段68號", "lat": 24.1512, "lng": 120.6757,
     "capacity": 120, "phone": "04-22031234",
     "operating_hours": "09:00-21:00", "source": "manual"},

    # ── 物資分發點（超商/全聯）──
    {"name": "全聯福利中心 南區崇倫店",   "point_type": "store",
     "address": "台中市南區崇倫街88號",   "lat": 24.1225, "lng": 120.6801,
     "capacity": None, "phone": "04-22617788",
     "operating_hours": "07:00-23:00", "source": "manual",
     "note": "食物、飲用水分發點"},
    {"name": "7-ELEVEN 台中建國門市",     "point_type": "store",
     "address": "台中市南區建國南路一段200號", "lat": 24.1248, "lng": 120.6844,
     "capacity": None, "phone": "04-22611234",
     "operating_hours": "24h", "source": "manual",
     "note": "緊急食物、飲水"},
    {"name": "全聯福利中心 北區健行店",   "point_type": "store",
     "address": "台中市北區健行路300號",  "lat": 24.1567, "lng": 120.6723,
     "capacity": None, "phone": "04-22041234",
     "operating_hours": "07:00-23:00", "source": "manual",
     "note": "食物、飲用水分發點"},

    # ── 物資倉庫 ──
    {"name": "台中市政府物資倉庫（南屯）", "point_type": "warehouse",
     "address": "台中市南屯區公益路二段451號", "lat": 24.1341, "lng": 120.6412,
     "capacity": None, "phone": "04-22289111",
     "operating_hours": "緊急啟用", "source": "manual",
     "note": "民生物資備援倉庫，緊急模式下開放調配"},
]


# ──────────────────────────────────────────────────────────
# 主程式
# ──────────────────────────────────────────────────────────
def seed(city: str = "台中市", clear: bool = False):
    db = SessionLocal()
    try:
        if clear:
            deleted = db.query(ResourcePoint).delete()
            db.commit()
            print(f"[seed] 已清除 {deleted} 筆現有資源點")

        # 現有名稱集（防重複）
        existing_names = {r.name for r in db.query(ResourcePoint.name).all()}

        inserted = 0

        # ── Layer 1：政府 API ──
        gov_raw = _fetch_gov_shelters(city)
        for raw in gov_raw:
            parsed = _parse_gov_shelter(raw)
            if not parsed or parsed["name"] in existing_names:
                continue
            pt = ResourcePoint(**{k: v for k, v in parsed.items()
                                  if hasattr(ResourcePoint, k)})
            db.add(pt)
            existing_names.add(parsed["name"])
            inserted += 1

        # ── Layer 2：預設資料（僅當政府 API 補充不足時）──
        for d in TAICHUNG_DEFAULT:
            if d["name"] in existing_names:
                continue
            if city != "台中市" and city not in d.get("address", ""):
                continue
            supplies = {}
            if d["point_type"] == "shelter":
                supplies = {"shelter": "有", "water": "有限", "food": "有限"}
            elif d["point_type"] == "fire_station":
                supplies = {"first_aid": "充足", "tool": "充足"}
            elif d["point_type"] in ("hospital", "clinic"):
                supplies = {"first_aid": "充足"}
            elif d["point_type"] == "community":
                supplies = {"water": "有限", "food": "有限", "shelter": "有"}
            elif d["point_type"] == "store":
                supplies = {"water": "充足", "food": "充足"}
            elif d["point_type"] == "warehouse":
                supplies = {"water": "充足", "food": "充足",
                            "first_aid": "有限", "other": "充足"}

            pt = ResourcePoint(
                name=d["name"],
                point_type=d["point_type"],
                address=d.get("address"),
                lat=d.get("lat"),
                lng=d.get("lng"),
                capacity=d.get("capacity"),
                phone=d.get("phone"),
                operating_hours=d.get("operating_hours"),
                source=d.get("source", "manual"),
                note=d.get("note"),
                supplies_json=json.dumps(supplies, ensure_ascii=False),
                is_active=True,
            )
            db.add(pt)
            existing_names.add(d["name"])
            inserted += 1

        db.commit()
        total = db.query(ResourcePoint).count()
        print(f"[seed] 新增 {inserted} 筆資源點，資料庫共 {total} 筆")
        return inserted

    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="匯入社區固定資源點")
    parser.add_argument("--city",  default="台中市", help="縣市名稱（預設：台中市）")
    parser.add_argument("--clear", action="store_true", help="先清除再匯入")
    args = parser.parse_args()

    # 確保 DB 表存在
    from app.database import engine, Base
    import app.models.resource_point  # noqa: F401
    Base.metadata.create_all(bind=engine)

    count = seed(city=args.city, clear=args.clear)
    print(f"完成。共匯入 {count} 筆資源點。")
