"""最近的 AED（衛福部全國 AED 位置資訊，make_aed.py 產生）。

參考 PulsePoint AED：心跳停止時每一分鐘都算，求救卡片、急救問答與 LINE「AED」都會列出
最近的 AED、放在哪裡、現在有沒有開放（依台灣時間與登記的開放時段）。
"""
import csv
import gzip
import os
from dataclasses import dataclass
from datetime import datetime, timedelta
from functools import lru_cache

from app.services.geo import haversine_km
from app.timeutil import now_utc

AED_FILE = os.path.join(os.path.dirname(os.path.dirname(__file__)), "data", "aed.csv.gz")
TAIPEI = timedelta(hours=8)
CPR_HINTS = ("CPR", "cpr", "心肺復甦", "心跳停止", "沒有心跳", "沒心跳", "沒呼吸", "沒有呼吸", "昏倒", "倒地",
             "叫不醒", "失去意識", "AED", "aed", "電擊")


@dataclass(frozen=True)
class Aed:
    name: str
    place: str
    lat: float
    lng: float
    weekday: str
    saturday: str
    sunday: str
    note: str
    phone: str | None


@lru_cache(maxsize=1)
def aeds() -> tuple[Aed, ...]:
    try:
        with gzip.open(AED_FILE, "rt", encoding="utf-8") as f:
            return tuple(Aed(r["name"], r["place"], float(r["lat"]), float(r["lng"]), r["weekday"], r["saturday"],
                             r["sunday"], r["note"], r["phone"] or None) for r in csv.DictReader(f))
    except OSError:
        return ()


def _slot(aed: Aed, local: datetime) -> str:
    return aed.weekday if local.weekday() < 5 else aed.saturday if local.weekday() == 5 else aed.sunday


def open_now(aed: Aed, local: datetime | None = None) -> bool | None:
    """True 開放中、False 這個時段沒開、None 沒登記開放時間。"""
    if not (aed.weekday or aed.saturday or aed.sunday):
        return None
    local = local or (now_utc().replace(tzinfo=None) + TAIPEI)
    slot = _slot(aed, local)
    if not slot:
        return False
    start, end = slot.split("-")
    return start <= local.strftime("%H:%M") <= end


def hours_text(aed: Aed, local: datetime | None = None) -> str:
    state = open_now(aed, local)
    local = local or (now_utc().replace(tzinfo=None) + TAIPEI)
    slot = _slot(aed, local)
    if state is None:
        return "開放時間未登記"
    if state:
        return "現在開放" + ("（24 小時）" if slot in ("00:00-23:59", "00:00-24:00") else f"（今天 {slot}）")
    return f"現在可能沒開（今天 {slot}）" if slot else "今天沒有開放"


def nearest(lat: float, lng: float, limit: int = 3, local: datetime | None = None) -> list[dict]:
    """最近的幾個地點（同一個場所有好幾台只列最近那台）；距離差不多時（300 公尺內）先列現在有開的。"""
    box = [a for a in aeds() if abs(a.lat - lat) < 0.2 and abs(a.lng - lng) < 0.2] or list(aeds())
    seen, rows = set(), []
    for km, a in sorted(((haversine_km(lat, lng, a.lat, a.lng), a) for a in box), key=lambda r: r[0]):
        if a.name in seen:
            continue
        seen.add(a.name)
        rows.append((km, a))
        if len(rows) == limit + 4:
            break
    rows.sort(key=lambda r: (round(r[0] / 0.3), open_now(r[1], local) is not True, r[0]))
    return [{"aed": a, "km": km, "open": open_now(a, local), "hours": hours_text(a, local)} for km, a in rows[:limit]]


def distance_text(km: float) -> str:
    return f"{km * 1000:.0f} 公尺" if km < 1 else f"{km:.1f} 公里"


def one_line(lat: float | None, lng: float | None) -> str | None:
    """給求救卡片與急救問答用的一行：最近的 AED 在哪。"""
    if lat is None or lng is None:
        return None
    found = nearest(lat, lng, limit=1)
    if not found:
        return None
    row = found[0]
    a = row["aed"]
    return f"最近的 AED：{a.name}（{a.place}，約 {distance_text(row['km'])}，{row['hours']}）"


def about_cpr(text: str) -> bool:
    return any(h in (text or "") for h in CPR_HINTS)
