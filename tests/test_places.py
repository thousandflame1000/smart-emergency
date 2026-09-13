import io
import json
from urllib.error import URLError
from urllib.parse import parse_qs, urlparse

from fastapi.testclient import TestClient

from app.main import app
from app.services import places


def test_chinese_search_is_encoded_cached_and_separate_from_workspace_ids(monkeypatch):
    calls = []
    places._cache.clear()
    monkeypatch.setattr(places, '_last_request', 0)

    def fake_open(request, timeout):
        calls.append(request)
        return io.BytesIO(json.dumps([{"display_name":"臺北市, 臺灣", "lat":"25.03", "lon":"121.56"}]).encode())

    monkeypatch.setattr(places, 'urlopen', fake_open)
    client = TestClient(app)
    first = client.get('/api/workspaces/places', params={"q":"臺北市"})
    second = client.get('/api/workspaces/places', params={"q":"臺北市"})
    assert first.status_code == 200
    assert first.json() == second.json()
    assert first.json()["places"][0]["lat"] == 25.03
    assert len(calls) == 1
    assert parse_qs(urlparse(calls[0].full_url).query)["q"] == ["臺北市"]
    assert "SmartEmergency" in calls[0].get_header('User-agent')
    places._cache.clear()


def test_search_failure_and_short_query_are_visible(monkeypatch):
    places._cache.clear()
    monkeypatch.setattr(places, '_last_request', 0)

    def offline(*args, **kwargs):
        raise URLError('offline')

    monkeypatch.setattr(places, 'urlopen', offline)
    client = TestClient(app)
    assert client.get('/api/workspaces/places', params={"q":"x"}).status_code == 422
    result = client.get('/api/workspaces/places', params={"q":"東京"})
    assert result.status_code == 502
    assert '搜尋' in result.json()['detail']
