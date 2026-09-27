# -*- coding: utf-8 -*-
"""選擇地區：從 Overpass 取周邊設施；公用伺服器會拒絕連續請求，要換鏡像、重試並快取。"""
import io
import json
from urllib.error import URLError

from app.services import area_facilities as af

SAMPLE = {"elements": [
    {"type": "node", "id": 1, "lat": 23.02, "lon": 120.25, "tags": {"amenity": "fire_station", "name": "永康消防分隊"}},
    {"type": "way", "id": 2, "center": {"lat": 23.03, "lon": 120.26}, "tags": {"amenity": "school"}},
    {"type": "node", "id": 3, "lat": 23.04, "lon": 120.27, "tags": {"amenity": "bench"}},
]}


class _Resp(io.BytesIO):
    def __enter__(self):
        return self

    def __exit__(self, *a):
        return False


def test_facilities_are_parsed_retried_on_a_busy_mirror_and_cached(monkeypatch):
    af._cache.clear()
    calls = []

    def fake_urlopen(request, timeout):
        calls.append(request.full_url)
        if len(calls) == 1:
            raise URLError("429 Too Many Requests")
        return _Resp(json.dumps(SAMPLE).encode())

    monkeypatch.setattr(af, "urlopen", fake_urlopen)
    result = af.facilities_around(23.026, 120.253, 1000)
    labels = {n["id"]: (n["label"], n["kind"], n["properties"]["facility_type"]) for n in result["nodes"]}
    assert labels == {"osm:node/1": ("永康消防分隊", "facility", "消防"), "osm:way/2": ("學校", "facility", "學校")}
    assert len(calls) == 2 and result["provider"] == af.OVERPASS_SERVICES[1][0]

    af.facilities_around(23.026, 120.253, 1000)
    assert len(calls) == 2  # 同一地點與範圍一小時內不再打外部服務


def test_all_mirrors_down_is_a_clear_error(monkeypatch):
    af._cache.clear()
    monkeypatch.setattr(af, "urlopen", lambda request, timeout: (_ for _ in ()).throw(URLError("down")))
    monkeypatch.setattr(af.time, "sleep", lambda s: None)
    try:
        af.facilities_around(23.0, 120.2, 500)
    except ConnectionError as exc:
        assert "OpenStreetMap" in str(exc)
    else:
        raise AssertionError("expected ConnectionError")
