"""選擇地區：從 OpenStreetMap 取出某地點周邊的公開設施，放進工作區當設施物件。

只取設施，不取道路；道路節點動輒上千個，會淹沒事件與需求。
"""
from __future__ import annotations

import json
import time
from urllib.error import URLError
from urllib.parse import urlencode
from urllib.request import Request, urlopen

from app.services.workspace import Node

OVERPASS_SERVICES = (
    ("FOSSGIS", "https://overpass-api.de/api/interpreter"),
    ("VK Maps", "https://maps.mail.ru/osm/tools/overpass/api/interpreter"),
)
KIND_ZH = {
    "hospital": "醫院", "clinic": "診所", "pharmacy": "藥局", "fire_station": "消防",
    "police": "警察", "school": "學校", "community_centre": "活動中心",
    "social_facility": "社福機構", "shelter": "避難處", "supermarket": "超市", "convenience": "便利商店",
}
MAX_RADIUS_M = 3000
MAX_FACILITIES = 300
CACHE_SECONDS = 3600
_cache: dict[tuple, tuple[float, dict]] = {}


def facilities_around(lat: float, lng: float, radius_m: int) -> dict:
    if not (-85 <= lat <= 85 and -180 <= lng <= 180):
        raise ValueError("座標超出範圍")
    if not (100 <= radius_m <= MAX_RADIUS_M):
        raise ValueError(f"範圍需在 100–{MAX_RADIUS_M} 公尺")
    key = (round(lat, 4), round(lng, 4), int(radius_m))
    hit = _cache.get(key)
    if hit and time.monotonic() - hit[0] < CACHE_SECONDS:
        return hit[1]
    around = f"(around:{int(radius_m)},{lat},{lng})"
    query = (f'[out:json][timeout:20];('
             f'nwr["amenity"~"^(hospital|clinic|pharmacy|fire_station|police|school|community_centre|social_facility|shelter)$"]{around};'
             f'nwr["shop"~"^(supermarket|convenience)$"]{around};);out center {MAX_FACILITIES};')
    data, provider = None, None
    # 公用 Overpass 對連續請求會回 429／逾時；每個鏡像失敗後換下一個，全部失敗再等一下重試一輪。
    for attempt, (name, endpoint) in enumerate(OVERPASS_SERVICES + OVERPASS_SERVICES):
        if attempt == len(OVERPASS_SERVICES):
            time.sleep(2)
        request = Request(endpoint, data=urlencode({"data": query}).encode(),
                          headers={"User-Agent": "SmartEmergency/1.0 (community disaster relief)",
                                   "Content-Type": "application/x-www-form-urlencoded", "Accept": "application/json"})
        try:
            with urlopen(request, timeout=20) as response:
                data = json.loads(response.read(5_000_001))
            provider = name
            break
        except (URLError, TimeoutError, ValueError):
            continue
    if data is None:
        raise ConnectionError("OpenStreetMap 暫時無法回應，請稍後重試")
    result = {"nodes": [n.model_dump() for n in _nodes(data, provider)], "provider": provider}
    _cache[key] = (time.monotonic(), result)
    return result


def _nodes(data: dict, provider: str) -> list[Node]:
    out = []
    for element in data.get("elements", []):
        tags = element.get("tags") or {}
        lat = element.get("lat", (element.get("center") or {}).get("lat"))
        lng = element.get("lon", (element.get("center") or {}).get("lon"))
        kind = tags.get("amenity") or tags.get("shop")
        if lat is None or lng is None or kind not in KIND_ZH:
            continue
        label = tags.get("name:zh") or tags.get("name") or KIND_ZH[kind]
        out.append(Node(
            id=f"osm:{element['type']}/{element['id']}", label=label[:120], kind="facility",
            lat=round(float(lat), 7), lng=round(float(lng), 7), quantity=0, available=True,
            source=f"OpenStreetMap contributors / ODbL / {provider}",
            properties={"facility_type": KIND_ZH[kind], "address": tags.get("addr:full") or ""},
        ))
    return out[:MAX_FACILITIES]
