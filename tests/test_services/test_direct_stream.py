"""The fast stream lookup: one innertube request, with yt-dlp as the fallback."""

from __future__ import annotations

import json

import pytest

from ytm_player.services import direct_stream
from ytm_player.services.stream import StreamInfo, StreamResolver

FORMATS = [
    {"itag": 251, "mimeType": 'audio/webm; codecs="opus"', "bitrate": 160_000, "url": "u251",
     "averageBitrate": 140_000, "approxDurationMs": "222000"},
    {"itag": 140, "mimeType": 'audio/mp4; codecs="mp4a.40.2"', "bitrate": 130_000, "url": "u140",
     "averageBitrate": 128_000, "approxDurationMs": "222000"},
    {"itag": 249, "mimeType": 'audio/webm; codecs="opus"', "bitrate": 60_000, "url": "u249",
     "averageBitrate": 50_000, "approxDurationMs": "222000"},
    {"itag": 137, "mimeType": 'video/mp4; codecs="avc1"', "bitrate": 900_000, "url": "v137"},
]  # fmt: skip


@pytest.mark.parametrize(("quality", "itag"), [("high", 251), ("medium", 140), ("low", 249)])
def test_quality_picks_the_matching_audio_format(quality: str, itag: int):
    assert direct_stream.pick_audio(FORMATS, quality)["itag"] == itag


def test_formats_without_a_url_are_never_picked():
    assert direct_stream.pick_audio([dict(FORMATS[0], url=None)], "high") is None


class _Response:
    def __init__(self, body: bytes) -> None:
        self.body = body

    def read(self) -> bytes:
        return self.body

    def __enter__(self):
        return self

    def __exit__(self, *args) -> None:
        return None


@pytest.fixture
def real_lookup(monkeypatch: pytest.MonkeyPatch):
    """Undo the suite-wide stub, and serve canned YouTube answers instead of the network."""
    import importlib

    monkeypatch.setattr(direct_stream, "lookup", importlib.reload(direct_stream).lookup)
    answers: dict[str, bytes] = {
        "home": b'<script>ytcfg.set({"VISITOR_DATA":"CgtVSVNJVE9S"});</script>',
        "player": json.dumps(
            {"playabilityStatus": {"status": "OK"}, "streamingData": {"adaptiveFormats": FORMATS}}
        ).encode(),  # fmt: skip
    }
    sent: list[dict] = []

    def fake_urlopen(request, timeout=None):
        if request.full_url.startswith("https://music.youtube.com/"):
            return _Response(answers["home"])
        sent.append({"body": json.loads(request.data), "headers": dict(request.header_items())})
        return _Response(answers["player"])

    monkeypatch.setattr(direct_stream.urllib.request, "urlopen", fake_urlopen)
    direct_stream.forget_visitor()
    return answers, sent


def test_lookup_returns_the_stream_and_sends_the_visitor_id(real_lookup):
    _answers, sent = real_lookup
    found = direct_stream.lookup("vid", "high")
    assert found == {"url": "u251", "codec": "opus", "bitrate": 140, "duration": 222}
    assert sent[0]["body"]["context"]["client"]["visitorData"] == "CgtVSVNJVE9S"
    assert sent[0]["headers"]["X-goog-visitor-id"] == "CgtVSVNJVE9S"


def test_an_unplayable_answer_returns_nothing(real_lookup):
    answers, _sent = real_lookup
    answers["player"] = json.dumps({"playabilityStatus": {"status": "LOGIN_REQUIRED"}}).encode()
    assert direct_stream.lookup("vid", "high") is None


def _stub_ytdlp(monkeypatch, calls: list[str]) -> None:
    def fake_try_resolve(self, url, video_id, attempt):
        calls.append(video_id)
        return StreamInfo(url="ytdlp", video_id=video_id, format="opus", bitrate=160,
                          duration=1, expires_at=9e9)  # fmt: skip

    monkeypatch.setattr(StreamResolver, "_try_resolve", fake_try_resolve)


def test_the_resolver_uses_the_fast_lookup_first(monkeypatch):
    calls: list[str] = []
    _stub_ytdlp(monkeypatch, calls)
    monkeypatch.setattr(direct_stream, "lookup", lambda video_id, quality: {
        "url": "fast", "codec": "opus", "bitrate": 140, "duration": 200})  # fmt: skip
    info = StreamResolver("high")._resolve_sync("dQw4w9WgXcQ")
    assert info.url == "fast" and calls == []


def test_the_resolver_falls_back_to_ytdlp_when_the_fast_lookup_fails(monkeypatch):
    calls: list[str] = []
    _stub_ytdlp(monkeypatch, calls)
    info = StreamResolver("high")._resolve_sync("dQw4w9WgXcQ")
    assert info.url == "ytdlp" and calls == ["dQw4w9WgXcQ"]


def test_after_a_cache_clear_the_retry_goes_through_ytdlp(monkeypatch):
    """A stream that failed to open clears the cache, and the retry must not get the same URL again."""
    calls: list[str] = []
    _stub_ytdlp(monkeypatch, calls)
    monkeypatch.setattr(direct_stream, "lookup", lambda video_id, quality: {
        "url": "fast", "codec": "opus", "bitrate": 140, "duration": 200})  # fmt: skip
    resolver = StreamResolver("high")
    resolver.clear_cache()
    assert resolver._resolve_sync("dQw4w9WgXcQ").url == "ytdlp"
