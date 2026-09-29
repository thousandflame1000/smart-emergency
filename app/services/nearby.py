"""LINE「查詢物資」：依距離列出最近的社區資源點。

只列公開的固定據點（ResourcePoint），不列個人登記的物資，避免把志工住家座標
發給所有人。緊急模式時先列應變據點（庇護所、消防分隊、衛生所、醫院）。
"""
import json
import re

from sqlalchemy.orm import Session

from app.models.resource_point import EMERGENCY_POINT_TYPES, POINT_TYPES, ResourcePoint
from app.services.geo import haversine_km

SUPPLY_ZH = {"water": "飲用水", "food": "食物", "first_aid": "急救用品", "shelter": "收容",
             "vehicle": "交通", "tool": "工具", "other": "其他"}
EMERGENCY_COLOR, SUPPLY_COLOR = "#c0392b", "#148f77"


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


def _details(p: ResourcePoint, km: float) -> list[str]:
    lines = [f"{POINT_TYPES.get(p.point_type, p.point_type)}・約 {km:.1f} 公里（直線）"]
    supplies = _supplies(p)
    if supplies:
        lines.append(supplies)
    if p.capacity:
        lines.append(f"收容 {p.current_load or 0}/{p.capacity} 人")
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
        if p.phone and re.fullmatch(r"[0-9+\-()\s]{7,20}", p.phone):
            buttons.append({"label": f"📞 {p.phone}"[:20], "uri": "tel:" + re.sub(r"[^0-9+]", "", p.phone)})
        cards.append(bubble(f"{i}. {p.name}", color, _details(p, row["km"]), buttons))
    alt = ("🚨 最近的應變據點：" if emergency else "📦 最近的物資據點：") + "、".join(r["point"].name for r in rows)
    return alt[:400], carousel(cards)


NO_POINTS_TEXT = "附近還沒有登記的物資或避難據點。緊急時請撥 119。"
