# -*- coding: utf-8 -*-
"""下載衛福部「全國 AED 位置資訊」（公共場所 AED 急救資訊網，每日更新），整理成 app/data/aed.csv.gz。

求救卡片、急救問答與 LINE「AED」會列出最近的 AED 與現在有沒有開放。
資料更新時在本機跑一次再 commit：python make_aed.py
只留市話（開放時間緊急連絡電話），不收手機號碼。
"""
import csv
import gzip
import io
import os
import sys
import urllib.request

sys.path.insert(0, os.path.dirname(__file__))
from app.services.aed import AED_FILE  # noqa: E402
from app.validation import tel_uri  # noqa: E402

SOURCE = "https://tw-aed.mohw.gov.tw/openData?t=csv"
FIELDS = ["name", "place", "lat", "lng", "weekday", "saturday", "sunday", "note", "phone"]


def _hours(start: str, end: str) -> str:
    start, end = start.strip()[:5], end.strip()[:5]
    return f"{start}-{end}" if start and end else ""


def clean(row):
    try:
        lat, lng = float(row["地點LAT"]), float(row["地點LNG"])
    except ValueError:
        return None
    if not (21.5 < lat < 26.5 and 118 < lng < 122.5) or not row["場所名稱"].strip():
        return None
    phone = row["開放時間緊急連絡電話"].strip()
    if phone.startswith("09") or not tel_uri(phone):
        phone = ""
    return {"name": row["場所名稱"].strip(), "place": row["AED放置地點"].strip()[:40],
            "lat": round(lat, 6), "lng": round(lng, 6),
            "weekday": _hours(row["周一至周五起"], row["周一至周五迄"]),
            "saturday": _hours(row["周六起"], row["周六迄"]),
            "sunday": _hours(row["周日起"], row["周日迄"]),
            "note": " ".join(row["開放使用時間備註"].split())[:40], "phone": phone}


def main(path=None):
    raw = open(path, encoding="utf-8-sig").read() if path else \
        urllib.request.urlopen(SOURCE, timeout=180).read().decode("utf-8-sig")
    out = [item for item in map(clean, csv.DictReader(io.StringIO(raw))) if item]
    os.makedirs(os.path.dirname(AED_FILE), exist_ok=True)
    with gzip.open(AED_FILE, "wt", encoding="utf-8", newline="") as f:
        writer = csv.DictWriter(f, fieldnames=FIELDS)
        writer.writeheader()
        writer.writerows(out)
    print(f"saved {len(out)} AEDs to {AED_FILE} ({os.path.getsize(AED_FILE) // 1024} KB)")


if __name__ == "__main__":
    main(sys.argv[1] if len(sys.argv) > 1 else None)
