# -*- coding: utf-8 -*-
"""下載內政部消防署「避難收容處所點位檔」（data.gov.tw 73242），整理成 app/data/open_shelters.csv。

「查詢物資」除了自己登記的據點，也會列出全國的公告收容所，人在哪裡都找得到最近的。
資料更新時在本機跑一次再 commit：python make_open_shelters.py
不收管理人姓名與手機號碼（個人資料），只留市話。
"""
import csv
import io
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from app.services.nearby import OPEN_SHELTER_FILE  # noqa: E402
from app.validation import tel_uri  # noqa: E402

SOURCE = ("https://opdadm.moi.gov.tw/api/v1/no-auth/resource/api/dataset/ED6CF735-6C03-4573-A882-72C1BEC799CB"
          "/resource/54550E2F-4567-4C8F-BD2E-E54E9D0386B8/download")
FIELDS = ["name", "area", "lat", "lng", "capacity", "phone", "vulnerable"]


def in_taiwan(lat, lng):
    return 21.5 < lat < 26.5 and 118 < lng < 122.5


def clean(row):
    try:
        lng, lat = float(row["經度"]), float(row["緯度"])
    except ValueError:
        return None
    if not in_taiwan(lat, lng) and in_taiwan(lng, lat):
        lat, lng = lng, lat  # 經緯度填反
    if not in_taiwan(lat, lng) or not row["避難收容處所名稱"].strip():
        return None
    phone = row["管理人電話"].strip()
    if phone.startswith("09") or not tel_uri(phone):
        phone = ""
    try:
        capacity = int(float(row["預計收容人數"]))
    except ValueError:
        capacity = 0
    return {"name": row["避難收容處所名稱"].strip(), "area": (row["縣市及鄉鎮市區"] + row["村里"]).strip(),
            "lat": round(lat, 5), "lng": round(lng, 5), "capacity": capacity or "", "phone": phone,
            "vulnerable": "1" if row["適合避難弱者安置"].strip() == "是" else ""}


def main(path=None):
    raw = open(path, encoding="utf-8-sig").read() if path else \
        urllib.request.urlopen(SOURCE, timeout=120).read().decode("utf-8-sig")
    seen, out = set(), []
    for row in csv.DictReader(io.StringIO(raw)):
        item = clean(row)
        key = item and (item["name"], item["lat"], item["lng"])
        if item and key not in seen:
            seen.add(key)
            out.append(item)
    os.makedirs(os.path.dirname(OPEN_SHELTER_FILE), exist_ok=True)
    with open(OPEN_SHELTER_FILE, "w", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(out)
    print(f"saved {len(out)} shelters to {OPEN_SHELTER_FILE}")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
