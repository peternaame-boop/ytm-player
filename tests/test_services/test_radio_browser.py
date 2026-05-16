"""Unit tests for the radio-browser.info HTTP client.

urlopen is patched at the module-import boundary so we never hit the
real network. We assert on URL composition, JSON parsing, fallback
behaviour, and the Station ↔ track-dict adapter.
"""

from __future__ import annotations

import json
from unittest.mock import patch

import pytest

from ytm_player.services import radio_browser as rb_module
from ytm_player.services.radio_browser import RadioBrowser, RadioBrowserError, Station


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Reset the singleton between tests so settings overrides take effect."""
    RadioBrowser._instance = None
    yield
    RadioBrowser._instance = None


def _fake_urlopen(payload, status=200):
    """Build a context-manager that mimics urllib's urlopen return value."""

    class _FakeResp:
        def __init__(self, body: bytes):
            self._body = body
            self.status = status

        def read(self) -> bytes:
            return self._body

        def __enter__(self):
            return self

        def __exit__(self, *_args):
            return False

    if isinstance(payload, (dict, list)):
        body = json.dumps(payload).encode("utf-8")
    else:
        body = payload if isinstance(payload, bytes) else str(payload).encode("utf-8")
    return lambda _req, timeout=None: _FakeResp(body)


# ── Station ────────────────────────────────────────────────────────────


def test_station_from_api_normalizes_tags_and_url():
    data = {
        "stationuuid": "abc-123",
        "name": "  Test Radio  ",
        "url": "http://orig.example.com/live",
        "url_resolved": "http://cdn.example.com/live.mp3",
        "tags": "jazz, lofi, ambient,  ",
        "country": "Germany",
        "countrycode": "DE",
        "codec": "MP3",
        "bitrate": "128",  # API returns strings — must coerce
        "votes": 1234,
        "clickcount": 5678,
        "lastcheckok": 1,
    }
    s = Station.from_api(data)

    assert s.uuid == "abc-123"
    assert s.name == "Test Radio"  # stripped
    assert s.url == "http://cdn.example.com/live.mp3"  # url_resolved wins
    assert s.tags == ["jazz", "lofi", "ambient"]
    assert s.country_code == "DE"
    assert s.bitrate == 128
    assert s.votes == 1234
    assert s.click_count == 5678
    assert s.last_check_ok is True


def test_station_to_track_dict_marks_is_station():
    s = Station(
        uuid="u",
        name="N",
        url="http://x",
        homepage="",
        favicon="",
        country="",
        country_code="",
        language="",
        tags=["lofi"],
        codec="MP3",
        bitrate=128,
        votes=0,
        click_count=0,
        last_check_ok=True,
    )
    d = s.to_track_dict()
    assert d["is_station"] is True
    assert d["video_id"] == "station:u"
    assert d["duration"] is None  # live stream
    assert d["station_url"] == "http://x"


def test_from_api_url_resolved_fallback_to_url():
    """When url_resolved is missing/empty, use url."""
    data = {"stationuuid": "u", "name": "n", "url": "http://only.example.com"}
    assert Station.from_api(data).url == "http://only.example.com"


# ── RadioBrowser HTTP plumbing ────────────────────────────────────────


def test_top_voted_strips_entries_with_empty_url():
    payload = [
        {"stationuuid": "a", "name": "Good", "url": "http://a"},
        {"stationuuid": "b", "name": "Empty", "url": ""},  # filtered out
    ]
    with patch.object(rb_module, "urlopen", _fake_urlopen(payload)):
        rb = RadioBrowser()
        rb._base_url = "https://test.example.com"
        rb._base_url_at = 9e9  # cache the server forever
        result = rb.top_voted(limit=2)
    assert [s.name for s in result] == ["Good"]


def test_search_includes_filters_in_query_string():
    captured: dict = {}

    def _capture(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        return _fake_urlopen([])(req, timeout=timeout)

    with patch.object(rb_module, "urlopen", _capture):
        rb = RadioBrowser()
        rb._base_url = "https://test.example.com"
        rb._base_url_at = 9e9
        rb.search(name="jazz cafe", country_code="DE", tag="lofi", limit=10)

    assert "stations/search" in captured["url"]
    assert "name=jazz+cafe" in captured["url"]
    assert "countrycode=DE" in captured["url"]
    assert "tag=lofi" in captured["url"]
    assert "limit=10" in captured["url"]
    # User-Agent etiquette: identify the client.
    assert captured["headers"]["User-agent"].startswith("ytm-player/")


def test_cache_is_used_for_repeat_requests():
    """Two calls in a row should only fire urlopen once."""
    payload = [{"stationuuid": "u", "name": "x", "url": "http://x"}]
    call_count = {"n": 0}

    def _counter(req, timeout=None):
        call_count["n"] += 1
        return _fake_urlopen(payload)(req, timeout=timeout)

    with patch.object(rb_module, "urlopen", _counter):
        rb = RadioBrowser()
        rb._base_url = "https://test.example.com"
        rb._base_url_at = 9e9
        rb.top_voted(limit=10)
        rb.top_voted(limit=10)  # cache hit — no new request
    assert call_count["n"] == 1


def test_listing_returns_empty_on_http_error():
    """Network errors surface as an empty list, not an exception."""

    def _boom(_req, timeout=None):
        raise OSError("simulated DNS death")

    with patch.object(rb_module, "urlopen", _boom):
        rb = RadioBrowser()
        rb._base_url = "https://test.example.com"
        rb._base_url_at = 9e9
        assert rb.top_voted(limit=5) == []


def test_non_json_body_raises_radiobrowser_error():
    """Internal _get surfaces a typed error for non-JSON bodies."""
    with patch.object(rb_module, "urlopen", _fake_urlopen(b"<html>oops</html>")):
        rb = RadioBrowser()
        rb._base_url = "https://test.example.com"
        rb._base_url_at = 9e9
        with pytest.raises(RadioBrowserError):
            rb._get("/json/stations/topvote/10")


def test_log_click_swallows_errors():
    """click-log is fire-and-forget — must not raise on failure."""

    def _boom(_req, timeout=None):
        raise OSError("post failed")

    with patch.object(rb_module, "urlopen", _boom):
        rb = RadioBrowser()
        rb._base_url = "https://test.example.com"
        rb._base_url_at = 9e9
        rb.log_click("some-uuid")  # must not raise
        rb.vote("some-uuid")  # ditto


def test_clear_cache_flushes_stored_entries():
    payload = [{"stationuuid": "u", "name": "x", "url": "http://x"}]
    call_count = {"n": 0}

    def _counter(req, timeout=None):
        call_count["n"] += 1
        return _fake_urlopen(payload)(req, timeout=timeout)

    with patch.object(rb_module, "urlopen", _counter):
        rb = RadioBrowser()
        rb._base_url = "https://test.example.com"
        rb._base_url_at = 9e9
        rb.top_voted(limit=10)
        rb.clear_cache()
        rb.top_voted(limit=10)  # re-fetches
    assert call_count["n"] == 2
