"""Explicit place searches with shared throttling and bounded caching."""

import json
import os
import time
from collections import OrderedDict
from threading import Lock
from urllib.parse import urlencode
from urllib.request import Request, urlopen

_lock = Lock()
_cache = OrderedDict()
_last_request = 0.0


def search_places(query: str) -> list[dict]:
    global _last_request
    query = query.strip()
    if len(query) < 2:
        raise ValueError("請輸入至少兩個字的地區或地標名稱")
    with _lock:
        cached = _cache.get(query)
        if cached and time.monotonic() - cached[0] < 21600:
            _cache.move_to_end(query)
            return cached[1]
        delay = 1.1 - (time.monotonic() - _last_request)
        if delay > 0:
            time.sleep(delay)
        params = urlencode({"q": query, "format": "jsonv2", "limit": 6, "accept-language": "zh-TW"})
        endpoint = os.getenv("NOMINATIM_SEARCH_URL", "https://nominatim.openstreetmap.org/search")
        request = Request(endpoint + "?" + params, headers={
            "User-Agent": "SmartEmergency/1.0 (https://smart-emergency-production-d744.up.railway.app)",
            "Accept": "application/json",
        })
        _last_request = time.monotonic()
        with urlopen(request, timeout=15) as response:
            data = json.loads(response.read(500_000))
        if not isinstance(data, list):
            raise ValueError("地區搜尋服務回傳格式不正確")
        result = [{"name": p["display_name"], "lat": float(p["lat"]), "lng": float(p["lon"])} for p in data[:6]]
        _cache[query] = (time.monotonic(), result)
        while len(_cache) > 128:
            _cache.popitem(last=False)
        return result
