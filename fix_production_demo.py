# -*- coding: utf-8 -*-
"""把線上示範資料修到可以上台，透過 HTTP API，不需要資料庫連線。

為什麼不直接跑 seed_finals_demo.py：那支要連資料庫（railway run）。這支只用
公開的管理 API，所以在哪裡都能跑。代價是它**改不了打卡與警報的歷史**，因此
刻意保留線上既有的那份歷史——王秀霞現在的脆弱度就是從真實累積的紀錄算出來的。

修三件會在評審面前出事的事：

1. 後台長者名單印著 `ruisui居民1（情境模擬）` 與 `ruisui1號（[TYPHOON_SIM]）`，
   英文地名當人名、內部標記當地址。
2. 七筆需求全部 0 候選：可用物資都在台中 (24.12, 120.68)，需求都在花東
   (23.0–23.5)，相距約 120 公里，而緊急度 5 的媒合半徑只有 2 公里。
   「待派」卡與「查看詳情」都會是空的。
3. admin 角色 0 人，所以 LINE 的決策中心選單沒有人拿得到。

用法：
  python fix_production_demo.py --plan    只印出會改什麼
  python fix_production_demo.py           實際送出
"""
import argparse
import json
import sys
import urllib.error
import urllib.parse
import urllib.request

BASE = "https://smart-emergency-production-d744.up.railway.app"

# 主角。她的座標落在玉里鎮，脆弱度來自線上既有的打卡與警報紀錄。
HERO_NAME = "王秀霞"
HERO_AT = (23.3343, 121.3177)

# 座標 → 真實行政區。原本那批用英文地名當姓名，這裡換回中文，
# 並且分清楚花蓮縣與台東縣——花東縱谷跨兩縣，寫錯評審看得出來。
PLACES = [
    (23.50, "花蓮縣瑞穗鄉", ["中山路二段", "成功路", "民生街"]),
    (23.33, "花蓮縣玉里鎮", ["中山路一段", "中正路", "民權街"]),
    (23.16, "花蓮縣富里鄉", ["中山路", "永安街"]),
    (23.28, "臺東縣長濱鄉", ["長濱街", "三民路"]),
    (23.11, "臺東縣池上鄉", ["中山路", "新興街"]),
    (23.09, "臺東縣成功鎮", ["中山路", "中華路"]),
    (23.05, "臺東縣關山鎮", ["中正路", "和平路"]),
]

NEW_NAMES = ["林阿枝", "陳金蓮", "黃水金", "張月娥", "李進財", "吳素貞", "劉來發",
             "蔡玉蘭", "楊再興", "許阿珠"]
VOL_NAMES = ["鄭建宏", "謝佳蓉", "洪志明", "邱雅婷"]


def call(method, path, params=None):
    url = BASE + path + ("?" + urllib.parse.urlencode(params, doseq=True) if params else "")
    req = urllib.request.Request(url, method=method)
    try:
        with urllib.request.urlopen(req, timeout=40) as r:
            return json.loads(r.read().decode("utf-8") or "{}")
    except urllib.error.HTTPError as e:
        return {"__error__": f"{e.code} {e.read().decode('utf-8')[:160]}"}
    except Exception as e:
        return {"__error__": str(e)[:160]}


def get(path):
    return call("GET", path)


def rows(d, *keys):
    if isinstance(d, list):
        return d
    for k in keys:
        if isinstance(d.get(k), list):
            return d[k]
    return []


def place_for(lat):
    """離這個緯度最近的行政區。"""
    return min(PLACES, key=lambda p: abs(p[0] - (lat or 0)))


def plan_changes():
    users = rows(get("/api/dashboard/users"), "users", "items", "data")
    resources = rows(get("/api/resources/"), "resources", "items", "data")
    changes = []

    hero = next((u for u in users if u.get("name") == HERO_NAME), None)
    elder_i, vol_i = 0, 0

    for u in users:
        name, addr = str(u.get("name") or ""), str(u.get("address") or "")
        dirty = "情境模擬" in name or "TYPHOON_SIM" in addr or "[" in addr
        if not dirty or u is hero:
            continue
        _, town, roads = place_for(u.get("lat"))
        is_vol = "volunteer" in (u.get("roles") or [])
        if is_vol:
            new_name = VOL_NAMES[vol_i % len(VOL_NAMES)]
            vol_i += 1
        else:
            new_name = NEW_NAMES[elder_i % len(NEW_NAMES)]
            elder_i += 1
        changes.append(("user", u["id"], u.get("name"), {
            "name": new_name,
            "address": f"{town}{roads[elder_i % len(roads)]}{11 + elder_i * 7}號"}))

    # 主角的地址也帶著標記，但姓名是對的，只換地址。
    if hero and "[" in str(hero.get("address") or ""):
        changes.append(("user", hero["id"], hero.get("name"),
                        {"address": "花蓮縣玉里鎮中山路一段48號"}))

    # 志工搬到主角步行距離內，否則緊急度 5 的 2 公里半徑內永遠沒有人。
    vols = [u for u in users if "volunteer" in (u.get("roles") or [])]
    for i, v in enumerate(vols[:4]):
        changes.append(("user", v["id"], v.get("name"), {
            "lat": round(HERO_AT[0] + (i - 1.5) * 0.004, 5),
            "lng": round(HERO_AT[1] + (i % 2 - 0.5) * 0.005, 5),
            "address": f"花蓮縣玉里鎮中正路{20 + i * 13}號"}))

    # 物資跟著搬，並確認有可用的飲用水——demo 的需求就是水。
    for i, r in enumerate(resources[:4]):
        changes.append(("resource", r["id"], r.get("name"), {
            "lat": round(HERO_AT[0] + (i - 1.5) * 0.004, 5),
            "lng": round(HERO_AT[1] + (i % 2 - 0.5) * 0.005, 5),
            "address": f"花蓮縣玉里鎮中正路{20 + i * 13}號",
            "is_available": "true"}))

    # 線上四筆物資裡只有一筆是飲用水，而 PUT 改不了物資種類。示範的需求就是水，
    # 候選清單只有一列會顯得很單薄，所以在主角附近補幾筆可用的水。
    water_near = [r for r in resources
                  if r.get("resource_type") == "water" and r.get("is_available")]
    for i, v in enumerate(vols[:3]):
        if len(water_near) + i >= 3:
            break
        changes.append(("new_resource", v["id"], v.get("name"), {
            "resource_type": "water",
            "name": f"{VOL_NAMES[i % len(VOL_NAMES)]}提供的桶裝飲用水",
            "owner_id": v["id"],
            "quantity": ["2箱", "約10人份", "20公升"][i % 3],
            "address": f"花蓮縣玉里鎮中正路{20 + i * 13}號",
            "lat": round(HERO_AT[0] + (i - 1) * 0.004, 5),
            "lng": round(HERO_AT[1] + (i % 2 - 0.5) * 0.005, 5)}))

    # 一位管理員，LINE 的決策中心選單才有人拿得到。
    if not any("admin" in (u.get("roles") or []) for u in users):
        staff = [u for u in users if "volunteer" in (u.get("roles") or [])]
        if staff:
            changes.append(("user", staff[0]["id"], staff[0].get("name"),
                            {"roles": ["volunteer", "admin"]}))
    return changes


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--plan", action="store_true")
    args = ap.parse_args()

    changes = plan_changes()
    if not changes:
        print("沒有需要修改的項目。")
        return

    print(f"共 {len(changes)} 項變更：\n")
    for kind, ident, old, fields in changes:
        shown = ", ".join(f"{k}={v}" for k, v in fields.items())
        print(f"  [{kind}] {str(old)[:22]:<24} → {shown}")

    if args.plan:
        print("\n(--plan：沒有送出任何請求)")
        return

    print("\n送出中…")
    ok = fail = 0
    for kind, ident, old, fields in changes:
        if kind == "new_resource":
            res = call("POST", "/api/resources/", fields)
        else:
            path = (f"/api/dashboard/users/{ident}" if kind == "user"
                    else f"/api/resources/{ident}")
            res = call("PUT", path, fields)
        if res.get("__error__"):
            fail += 1
            print(f"  ✗ {str(old)[:20]}: {res['__error__']}")
        else:
            ok += 1
    print(f"\n成功 {ok} 項，失敗 {fail} 項。")
    if fail:
        sys.exit(1)


if __name__ == "__main__":
    main()
