"""Skipping while a collection's first track loads must keep its tail (A6).

``_play_playlist`` queues a long playlist's first batch and fetches the rest
in the background; ``_play_artist_top_songs`` does the same with an artist's
top songs and full song list. The rest belongs to the queue it was fetched
for, not to the request that started it: an ordinary Next keeps it, Stop or a
replaced queue drops it. It used to be scheduled only after the first stream
had resolved, so a Next during that wait left the queue cut at the first
batch.

Synthetic queues; no native audio or network.
"""

from __future__ import annotations

import asyncio
from collections.abc import Coroutine
from typing import Any
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_app.test_playback import _fresh_playback_host, _stream_info
from ytm_player.app._mpris import MPRISMixin
from ytm_player.app._playback import PlaybackMixin
from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.queue import QueueManager

_SITES = ["playlist", "artist"]


class _Host(TrackActionsMixin, PlaybackMixin):
    pass


def _raw(video_id: str) -> dict:
    return {"videoId": video_id, "title": video_id}


def _track(video_id: str) -> dict:
    return {"video_id": video_id, "title": video_id, "duration": 180}


def _host() -> _Host:
    host = _Host()
    host.__dict__.update(vars(_fresh_playback_host()))
    host.player.stop = AsyncMock()
    host.queue = QueueManager()
    host.settings.playback.autoplay = False
    host._refresh_queue_page = MagicMock()
    host._sync_shuffle_bar = MagicMock()
    host._log_listen_for = AsyncMock()
    host._log_current_listen = AsyncMock()
    host.shuffle_prefs = {}
    host.ytmusic = MagicMock()
    return host


class _Scenario:
    """Five tracks served three first (A, B, C), whose first stream is held open.

    *site* picks the collection: a playlist with more tracks than its first
    batch, or an artist whose top songs are followed by the full song list.
    """

    def __init__(self, site: str) -> None:
        host = _host()
        if site == "playlist":
            host.ytmusic.get_playlist = AsyncMock(
                return_value={"tracks": [_raw(v) for v in "ABC"], "trackCount": 5}
            )
            self.tail_fetch = AsyncMock(return_value=[_raw(v) for v in "DE"])
            host.ytmusic.get_playlist_remaining = self.tail_fetch
            self._start = lambda: host._play_playlist("PL", "Long")
            self._tail_worker = "fetch-remaining-playlist"
            self.toast = "Playing: Long"
        else:
            host.ytmusic.get_artist = AsyncMock(
                return_value={
                    "name": "Long",
                    "songs": {"browseId": "SONGS", "results": [_raw(v) for v in "ABC"]},
                }
            )
            self.tail_fetch = AsyncMock(return_value={"tracks": [_raw(v) for v in "ABCDE"]})
            host.ytmusic.get_playlist = self.tail_fetch
            self._start = lambda: host._play_artist_top_songs("ARTIST")
            self._tail_worker = "fetch-artist-songs"
            self.toast = "Playing top songs from Long"
        # Whatever was queued before: the tail must never land on it.
        host.queue.add(_track("OLD"))
        host.queue.jump_to(0)

        self.first_stream_wanted = asyncio.Event()
        self.first_stream_ready = asyncio.Event()

        async def resolve(video_id: str) -> Any:
            if video_id == "A":
                self.first_stream_wanted.set()
                await self.first_stream_ready.wait()
            return _stream_info(video_id)

        host.stream_resolver.resolve = AsyncMock(side_effect=resolve)

        self.tails: list[Coroutine[Any, Any, None]] = []

        def run_worker(coro: Coroutine[Any, Any, None], **kwargs: Any) -> None:
            if kwargs.get("name") == self._tail_worker:
                self.tails.append(coro)
            else:
                coro.close()

        host.run_worker.side_effect = run_worker
        self.host = host
        self.playing: asyncio.Task[None] | None = None

    async def start_and_wait_for_first_stream(self) -> None:
        self.playing = asyncio.create_task(self._start())
        await asyncio.wait_for(self.first_stream_wanted.wait(), 3)

    async def finish(self) -> None:
        """Let the held stream through, then run whatever tail was scheduled."""
        self.first_stream_ready.set()
        assert self.playing is not None
        await asyncio.wait_for(self.playing, 3)
        for tail in self.tails:
            await tail

    def queue_ids(self) -> list[str]:
        return [t["video_id"] for t in self.host.queue.tracks]

    def loaded(self) -> list[str]:
        return [call.args[1]["video_id"] for call in self.host.player.play.await_args_list]

    def toasts(self) -> list[str]:
        """Toasts shown once the collection is queued (not the "Loading ..." one)."""
        return [
            call.args[0]
            for call in self.host.notify.call_args_list
            if not call.args[0].startswith("Loading")
        ]


@pytest.mark.parametrize("site", _SITES)
async def test_next_while_the_first_track_loads_keeps_the_tail(site):
    s = _Scenario(site)
    try:
        await s.start_and_wait_for_first_stream()
        await s.host._play_next()  # ordinary Next supersedes the request, not the queue
    finally:
        await s.finish()

    # Master never scheduled the tail here: the queue stayed A, B, C.
    assert len(s.tails) == 1
    assert s.queue_ids() == ["A", "B", "C", "D", "E"]
    assert s.host.queue.current_track["video_id"] == "B"
    assert s.loaded() == ["B"]


@pytest.mark.parametrize("site", _SITES)
async def test_stop_while_the_first_track_loads_drops_the_tail(site):
    s = _Scenario(site)
    try:
        await s.start_and_wait_for_first_stream()
        await MPRISMixin._mpris_stop(s.host)
    finally:
        await s.finish()

    # The tail was scheduled before the stream resolved, and Stop still ended it.
    assert len(s.tails) == 1
    s.tail_fetch.assert_not_awaited()
    assert s.queue_ids() == ["A", "B", "C"]
    assert s.loaded() == []


@pytest.mark.parametrize("site", _SITES)
async def test_replacing_the_queue_while_the_first_track_loads_drops_the_tail(site):
    s = _Scenario(site)
    try:
        await s.start_and_wait_for_first_stream()
        await s.host._replace_queue_and_play([_track("X"), _track("Y")])
    finally:
        await s.finish()

    assert len(s.tails) == 1
    s.tail_fetch.assert_not_awaited()
    assert s.queue_ids() == ["X", "Y"]
    assert s.loaded() == ["X"]


@pytest.mark.parametrize("site", _SITES)
async def test_uninterrupted_play_still_fetches_the_tail_once_and_confirms(site):
    """Control: nothing skipped. One tail, appended, and the toast after the load."""
    s = _Scenario(site)
    try:
        await s.start_and_wait_for_first_stream()
        assert s.toasts() == []  # the confirmation still follows the first load
    finally:
        await s.finish()

    assert len(s.tails) == 1
    assert s.queue_ids() == ["A", "B", "C", "D", "E"]
    assert s.loaded() == ["A"]
    assert s.toasts() == [s.toast]


@pytest.mark.parametrize("site", _SITES)
async def test_queue_left_without_a_current_track_plays_nothing(site):
    """Control: as before the reorder, no current track means no play, no crash."""
    host = _host()
    host.queue = MagicMock(current_track=None, generation=0, shuffle_enabled=False)
    host._replace_queue_and_play = AsyncMock()
    host.play_track = AsyncMock()
    if site == "playlist":
        host.ytmusic.get_playlist = AsyncMock(return_value={"tracks": [_raw("A")]})
        await host._play_playlist("PL", "Long")
        toast = "Playing: Long"
    else:
        host.ytmusic.get_artist = AsyncMock(
            return_value={"name": "Long", "songs": {"results": [_raw("A")]}}
        )
        await host._play_artist_top_songs("ARTIST")
        toast = "Playing top songs from Long"

    host._replace_queue_and_play.assert_awaited_once()
    host.play_track.assert_not_called()
    assert host.notify.call_args.args[0] == toast
