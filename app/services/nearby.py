"""LINE「查詢物資」：依距離列出最近的社區資源點。

只列公開的固定據點（ResourcePoint），不列個人登記的物資，避免把志工住家座標
發給所有人。緊急模式時先列應變據點（庇護所、消防分隊、衛生所、醫院）。
"""
import json

from sqlalchemy.orm import Session

from app.models.resource_point import EMERGENCY_POINT_TYPES, POINT_TYPES, ResourcePoint
from app.services.geo import haversine_km

SUPPLY_ZH = {"water": "飲用水", "food": "食物", "first_aid": "急救用品", "shelter": "收容",
             "vehicle": "交通", "tool": "工具", "other": "其他"}


def nearest_points(db: Session, lat: float, lng: float, emergency: bool, limit: int = 5) -> list[dict]:
    points = (db.query(ResourcePoint)
              .filter(ResourcePoint.is_active == True,  # noqa: E712
                      ResourcePoint.lat.isnot(None), ResourcePoint.lng.isnot(None))
              .all())
    rows = [(p.point_type not in EMERGENCY_POINT_TYPES if emergency else False,
             haversine_km(lat, lng, p.lat, p.lng), p) for p in points]
    rows.sort(key=lambda r: (r[0], r[1]))
    return [{"point": p, "km": km} for _, km, p in rows[:limit]]


def _supplies(p: ResourcePoint) -> str:
    try:
        data = json.loads(p.supplies_json) if p.supplies_json else {}
    except (TypeError, ValueError):
        data = {}
    return "、".join(f"{SUPPLY_ZH.get(k, k)}{v}" for k, v in data.items() if v)


def nearby_text(db: Session, user, emergency: bool) -> str:
    if user.lat is None or user.lng is None:
        return "📍 請先分享位置，才能找離您最近的物資與避難據點。"
    rows = nearest_points(db, user.lat, user.lng, emergency)
    if not rows:
        return "附近還沒有登記的物資或避難據點。緊急時請撥 119。"
    lines = ["🚨 緊急模式・最近的應變據點" if emergency else "📦 離您最近的物資據點"]
    for i, row in enumerate(rows, 1):
        p = row["point"]
        lines.append(f"\n{i}. {p.name}（{POINT_TYPES.get(p.point_type, p.point_type)}）{row['km']:.1f} 公里")
        supplies = _supplies(p)
        if supplies:
            lines.append(f"   {supplies}")
        if p.capacity:
            lines.append(f"   收容 {p.current_load or 0}/{p.capacity} 人")
        if p.phone:
            lines.append(f"   ☎ {p.phone}")
        lines.append(f"   https://www.google.com/maps/search/?api=1&query={p.lat},{p.lng}")
    lines.append("\n距離為直線估算；實際路況以現場為準。")
    return "\n".join(lines)
