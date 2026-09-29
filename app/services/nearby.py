"""LINE「查詢物資」：依距離列出最近的社區資源點。

只列公開的固定據點（ResourcePoint），不列個人登記的物資，避免把志工住家座標
發給所有人。緊急模式時先列應變據點（庇護所、消防分隊、衛生所、醫院）。
自己登記的據點只涵蓋服務區；再加上內政部公告的全國避難收容處所（make_open_shelters.py 產生），
人在哪個縣市都找得到最近的收容所。
"""
import csv
import json
import os
from dataclasses import dataclass
from functools import lru_cache

from sqlalchemy.orm import Session

from app.models.resource_point import EMERGENCY_POINT_TYPES, POINT_TYPES, ResourcePoint
from app.services.geo import haversine_km
from app.validation import tel_uri

SUPPLY_ZH = {"water": "飲用水", "food": "食物", "first_aid": "急救用品", "shelter": "收容",
             "vehicle": "交通", "tool": "工具", "other": "其他"}
EMERGENCY_COLOR, SUPPLY_COLOR = "#c0392b", "#148f77"
OPEN_SHELTER_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "open_shelters.csv")
SAME_PLACE_KM = 0.3  # 公告收容所跟自己登記的據點這麼近，視為同一處，只列自己登記的（有即時收容人數）


@dataclass(frozen=True)
class OpenShelter:
    """內政部公告的避難收容處所；欄位對齊 ResourcePoint，卡片與 App 可以共用。"""
    name: str
    area: str
    lat: float
    lng: float
    capacity: int | None
    phone: str | None
    vulnerable: bool
    point_type = "shelter"
    current_load = None
    operating_hours = None
    supplies_json = None


@lru_cache(maxsize=1)
def open_shelters() -> tuple[OpenShelter, ...]:
    try:
        with open(OPEN_SHELTER_FILE, encoding="utf-8") as f:
            return tuple(OpenShelter(r["name"], r["area"], float(r["lat"]), float(r["lng"]),
                                     int(r["capacity"]) if r["capacity"] else None, r["phone"] or None,
                                     r["vulnerable"] == "1") for r in csv.DictReader(f))
    except OSError:
        return ()


def _open_near(lat: float, lng: float) -> list[OpenShelter]:
    """先用經緯度方框粗篩（約 ±50 公里），才不用每次對六千處都算距離；方框內沒有就全部算。"""
    box = [s for s in open_shelters() if abs(s.lat - lat) < 0.5 and abs(s.lng - lng) < 0.5]
    return box or list(open_shelters())


def nearest_shelter_km(lat: float, lng: float, own: list[tuple[float, float]]) -> float | None:
    """到最近收容所的直線距離，自己登記的與公告的都算；系統狀態用來看長者附近有沒有地方去。"""
    spots = own + [(s.lat, s.lng) for s in _open_near(lat, lng)]
    return min((haversine_km(lat, lng, a, b) for a, b in spots), default=None)


def nearest_points(db: Session, lat: float, lng: float, emergency: bool, limit: int = 5) -> list[dict]:
    points = (db.query(ResourcePoint)
              .filter(ResourcePoint.is_active == True,  # noqa: E712
                      ResourcePoint.lat.isnot(None), ResourcePoint.lng.isnot(None))
              .all())
    rows = [(p.point_type not in EMERGENCY_POINT_TYPES if emergency else False,
             haversine_km(lat, lng, p.lat, p.lng), p) for p in points]
    rows += [(False, haversine_km(lat, lng, s.lat, s.lng), s) for s in _open_near(lat, lng)]
    rows.sort(key=lambda r: (r[0], r[1]))
    picked = []
    for _, km, p in rows:
        if isinstance(p, OpenShelter) and any(
                haversine_km(p.lat, p.lng, q.lat, q.lng) < SAME_PLACE_KM for q in points):
            continue
        picked.append({"point": p, "km": km})
        if len(picked) == limit:
            break
    return picked


def capacity_text(p) -> str | None:
    if not p.capacity:
        return None
    if isinstance(p, OpenShelter):
        return f"可收容 {p.capacity} 人" + ("・可安置長者與身障者" if p.vulnerable else "")
    return f"收容 {p.current_load or 0}/{p.capacity} 人"


def _supplies(p: ResourcePoint) -> str:
    try:
        data = json.loads(p.supplies_json) if p.supplies_json else {}
    except (TypeError, ValueError):
        data = {}
    return "、".join(f"{SUPPLY_ZH.get(k, k)}{v}" for k, v in data.items() if v)


def _details(p: ResourcePoint, km: float) -> list[str]:
    lines = [f"{POINT_TYPES.get(p.point_type, p.point_type)}・約 {km:.1f} 公里（直線）"]
    if isinstance(p, OpenShelter):
        lines.append(f"{p.area}・內政部公告")
    supplies = _supplies(p)
    if supplies:
        lines.append(supplies)
    if capacity_text(p):
        lines.append(capacity_text(p))
    if p.operating_hours:
        lines.append(f"開放 {p.operating_hours}")
    return lines


def missing_text(user) -> str | None:
    """沒位置時的提示；有位置則回 None。"""
    if user.lat is None or user.lng is None:
        return "📍 請先分享位置，才能找離您最近的物資與避難據點。"
    return None


def nearby_cards(db: Session, user, emergency: bool) -> tuple[str, dict] | None:
    """回 (alt_text, carousel)；附近沒有任何據點時回 None。"""
    from app.services.line_ops import bubble, carousel
    rows = nearest_points(db, user.lat, user.lng, emergency)
    if not rows:
        return None
    cards = []
    for i, row in enumerate(rows, 1):
        p = row["point"]
        color = EMERGENCY_COLOR if emergency and p.point_type in EMERGENCY_POINT_TYPES else SUPPLY_COLOR
        buttons = [{"label": "🧭 導航", "uri": f"https://www.google.com/maps/dir/?api=1&destination={p.lat},{p.lng}"}]
        if tel_uri(p.phone):
            buttons.append({"label": f"📞 {p.phone}"[:20], "uri": tel_uri(p.phone)})
        cards.append(bubble(f"{i}. {p.name}", color, _details(p, row["km"]), buttons))
    alt = ("🚨 最近的應變據點：" if emergency else "📦 最近的物資據點：") + "、".join(r["point"].name for r in rows)
    return alt[:400], carousel(cards)


NO_POINTS_TEXT = "附近還沒有登記的物資或避難據點。緊急時請撥 119。"
