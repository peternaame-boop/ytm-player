"""Pages warm their first songs on a spare resolver, so a click starts from the cache.

The spare resolver holds its own yt-dlp lock: a guess in flight never delays the
resolve a click starts on the main resolver.
"""

from __future__ import annotations

import asyncio
import threading
import time
from types import SimpleNamespace

from ytm_player.app._playback import PlaybackMixin
from ytm_player.services.stream import StreamInfo, StreamResolver


def _info(video_id: str) -> StreamInfo:
    return StreamInfo(url=f"https://x/{video_id}", video_id=video_id, format="opus", bitrate=160,
                      duration=200, expires_at=time.time() + 3600)  # fmt: skip


class _Host:
    _warm = PlaybackMixin._warm
    _spare_resolvers = PlaybackMixin._spare_resolvers

    def __init__(self) -> None:
        self.stream_resolver = StreamResolver("high")


async def test_warmed_songs_land_in_the_main_cache_and_cached_ones_are_skipped(monkeypatch):
    host = _Host()
    host.stream_resolver._put_cache(_info("cached"))
    asked: list[str] = []

    def fake_resolve_sync(self, video_id):
        asked.append(video_id)
        return _info(video_id)

    monkeypatch.setattr(StreamResolver, "_resolve_sync", fake_resolve_sync)
    await host._warm(["cached", "a", "b"])
    assert sorted(asked) == ["a", "b"]
    assert host.stream_resolver._get_cached("a") is not None
    assert host.stream_resolver not in host._spare_stream_resolvers
    assert len(host._spare_stream_resolvers) == 3


async def test_a_click_does_not_wait_for_a_guess_in_flight(monkeypatch):
    host = _Host()
    release = threading.Event()

    def fake_resolve_sync(self, video_id):
        if video_id == "guess":
            release.wait(5)
        return _info(video_id)

    monkeypatch.setattr(StreamResolver, "_resolve_sync", fake_resolve_sync)
    guess = asyncio.create_task(host._warm(["guess"]))
    await asyncio.sleep(0.05)
    started = time.monotonic()
    clicked = await host.stream_resolver.resolve("clicked")
    assert clicked is not None and time.monotonic() - started < 1
    release.set()
    await guess


def test_warm_streams_dedupes_and_caps(monkeypatch):
    started: list[list[str]] = []
    host = SimpleNamespace(stream_resolver=object())
    host._warm = lambda ids: ids
    host.run_worker = lambda ids, **kwargs: started.append(ids)
    PlaybackMixin.warm_streams(host, ["a", "a", "", "b", "c", "d", "e"])
    assert started == [["a", "b", "c", "d", "e"]]


def test_warm_streams_caps_at_the_limit():
    started: list[list[str]] = []
    host = SimpleNamespace(stream_resolver=object())
    host._warm = lambda ids: ids
    host.run_worker = lambda ids, **kwargs: started.append(ids)
    PlaybackMixin.warm_streams(host, [f"v{i}" for i in range(30)])
    assert len(started[0]) == 12
