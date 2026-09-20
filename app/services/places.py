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


_failed: dict[str, float] = {}
FAILURE_MEMORY_SECONDS = 300


def search_places(query: str, *, timeout: float = 15) -> list[dict]:
    global _last_request
    query = query.strip()
    if len(query) < 2:
        raise ValueError("請輸入至少兩個字的地區或地標名稱")
    with _lock:
        cached = _cache.get(query)
        if cached and time.monotonic() - cached[0] < 21600:
            _cache.move_to_end(query)
            return cached[1]
        failed_at = _failed.get(query)
        if failed_at and time.monotonic() - failed_at < FAILURE_MEMORY_SECONDS:
            raise RuntimeError("地址查詢剛剛失敗過，稍後再試")
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
        try:
            with urlopen(request, timeout=timeout) as response:
                data = json.loads(response.read(500_000))
        except Exception:
            _failed[query] = time.monotonic()
            raise
        if not isinstance(data, list):
            raise ValueError("地區搜尋服務回傳格式不正確")
        result = [{"name": p["display_name"], "lat": float(p["lat"]), "lng": float(p["lon"])} for p in data[:6]]
        _cache[query] = (time.monotonic(), result)
        while len(_cache) > 128:
            _cache.popitem(last=False)
        return result


def geocode_address(address: str) -> tuple[float, float] | None:
    """Best-effort address -> (lat, lng). Never raises: callers use it to fill in
    coordinates opportunistically and must keep working when the lookup fails."""
    try:
        # Runs while someone waits for a chat reply, so give up quickly instead of holding the
        # whole conversation (and the shared throttle lock) for the full 15 seconds.
        results = search_places(address, timeout=5)
    except Exception:
        return None
    if not results:
        return None
    return results[0]["lat"], results[0]["lng"]
