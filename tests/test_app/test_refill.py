"""Autoplay refill: stale refill workers and the prefetch after an append."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

from tests.test_app.test_playback import _accept_playback, _fresh_playback_host, _stream_info
from ytm_player.app._ownership import invalidate_requests
from ytm_player.app._playback import PlaybackMixin
from ytm_player.services.queue import QueueManager
from ytm_player.services.stream import StreamResolver


def _tracks(prefix: str, n: int) -> list[dict]:
    return [{"video_id": f"{prefix}{i}", "title": f"{prefix}{i}"} for i in range(n)]


class _Workers:
    """run_worker with Textual's group rule: an exclusive worker cancels its group."""

    def __init__(self) -> None:
        self.tasks: list[tuple[str | None, asyncio.Task]] = []
        self.calls: list[dict] = []

    def run(self, coro, *, group=None, exclusive=False, **_kwargs):
        self.calls.append({"group": group, "exclusive": exclusive})
        if exclusive:
            for task_group, task in self.tasks:
                if task_group == group and not task.done():
                    task.cancel()
        task = asyncio.ensure_future(coro)
        self.tasks.append((group, task))
        return task

    def __iter__(self):
        return iter(
            SimpleNamespace(group=group, is_running=not task.done()) for group, task in self.tasks
        )

    def started(self, group: str) -> int:
        return sum(1 for call in self.calls if call["group"] == group)

    async def settle(self) -> None:
        await asyncio.gather(*(task for _, task in self.tasks), return_exceptions=True)


def _host(workers: _Workers) -> PlaybackMixin:
    h = PlaybackMixin()
    h.queue = QueueManager()
    h.settings = MagicMock()
    h.settings.playback.autoplay = True
    h.player = MagicMock()
    h.player.current_track = None
    h.workers = workers
    h.run_worker = workers.run
    h._refresh_queue_page = MagicMock()
    h.notify = MagicMock()
    h.stream_resolver = MagicMock()
    h.stream_resolver.prefetch = AsyncMock()
    h.ytmusic = MagicMock()
    h._play_request = 0
    h._queue_stop_generation = 0
    h._refill_owner = None
    return h


def _load(h: PlaybackMixin, prefix: str, n: int) -> None:
    h.queue.add_multiple(_tracks(prefix, n))
    h.queue.jump_to(0)


def _held_radio(release: asyncio.Event, prefix: str):
    async def get_radio(_video_ids):
        await release.wait()
        return _tracks(prefix, 3)

    return AsyncMock(side_effect=get_radio)


def _queue_ids(h: PlaybackMixin) -> list[str]:
    return [t["video_id"] for t in h.queue.tracks]


class TestStaleRefill:
    async def test_replaced_queue_gets_its_own_refill(self):
        workers = _Workers()
        h = _host(workers)
        release = asyncio.Event()
        h.ytmusic.get_radio = _held_radio(release, "oldradio")
        _load(h, "old", 2)
        h._refill_queue()
        await asyncio.sleep(0)
        old_refill = workers.tasks[-1][1]

        h.queue.clear()
        _load(h, "new", 2)
        h.ytmusic.get_radio = AsyncMock(return_value=_tracks("newradio", 3))
        h._refill_queue()
        release.set()
        await workers.settle()

        assert workers.started("queue_extend") == 2
        assert old_refill.cancelled()
        ids = _queue_ids(h)
        assert "newradio0" in ids
        assert not any(i.startswith("oldradio") for i in ids)

    async def test_running_refill_for_the_same_queue_blocks_a_second(self):
        workers = _Workers()
        h = _host(workers)
        release = asyncio.Event()
        h.ytmusic.get_radio = _held_radio(release, "radio")
        _load(h, "q", 2)
        h._refill_queue()
        await asyncio.sleep(0)
        h._refill_queue()
        release.set()
        await workers.settle()

        assert workers.started("queue_extend") == 1
        assert _queue_ids(h).count("radio0") == 1

    async def test_refill_after_stop_and_replay_is_not_blocked(self):
        workers = _Workers()
        h = _host(workers)
        release = asyncio.Event()
        h.ytmusic.get_radio = _held_radio(release, "radio")
        _load(h, "q", 2)
        h._refill_queue()
        await asyncio.sleep(0)

        invalidate_requests(h)  # Stop: the pending refill's result will be dropped
        h._refill_queue()  # replay of the same queue
        release.set()
        await workers.settle()

        assert workers.started("queue_extend") == 2
        assert _queue_ids(h).count("radio0") == 1

    async def test_stale_refill_result_is_not_appended_to_a_new_queue(self):
        workers = _Workers()
        h = _host(workers)
        release = asyncio.Event()
        h.ytmusic.get_radio = _held_radio(release, "oldradio")
        _load(h, "old", 2)
        h._refill_queue()
        await asyncio.sleep(0)

        h.queue.clear()
        _load(h, "new", 10)  # long enough that no new refill starts
        release.set()
        await workers.settle()

        assert workers.started("queue_extend") == 1
        assert _queue_ids(h) == [f"new{i}" for i in range(10)]


class TestPrefetchAfterAppend:
    async def test_append_prefetches_the_new_next_track(self):
        workers = _Workers()
        h = _host(workers)
        _load(h, "q", 1)
        h.ytmusic.get_radio = AsyncMock(return_value=_tracks("radio", 3))

        await h._fetch_and_play_radio(h.queue.tracks[0], append=True)
        await workers.settle()

        h.stream_resolver.prefetch.assert_called_once_with("radio0")
        assert workers.calls == [{"group": "prefetch", "exclusive": False}]

    async def test_track_change_prefetch_stays_exclusive(self):
        workers = _Workers()
        h = _host(workers)
        _load(h, "q", 2)

        h._prefetch_next_track()
        await workers.settle()

        assert workers.calls == [{"group": "prefetch", "exclusive": True}]

    async def test_empty_radio_result_does_not_prefetch(self):
        workers = _Workers()
        h = _host(workers)
        _load(h, "q", 1)
        h.ytmusic.get_radio = AsyncMock(return_value=[])

        await h._fetch_and_play_radio(h.queue.tracks[0], append=True)

        assert workers.calls == []

    async def test_dropped_append_does_not_prefetch(self):
        workers = _Workers()
        h = _host(workers)
        _load(h, "q", 1)

        async def replaced_meanwhile(_video_ids):
            h.queue.clear()
            _load(h, "new", 1)
            return _tracks("radio", 3)

        h.ytmusic.get_radio = AsyncMock(side_effect=replaced_meanwhile)

        await h._fetch_and_play_radio(h.queue.tracks[0], append=True)

        assert workers.calls == []
        assert _queue_ids(h) == ["new0"]

    async def test_radio_that_replaces_the_queue_does_not_prefetch_here(self):
        workers = _Workers()
        h = _host(workers)
        _load(h, "q", 1)
        h.ytmusic.get_radio = AsyncMock(return_value=_tracks("radio", 3))
        h.play_track = AsyncMock()

        await h._fetch_and_play_radio(h.queue.tracks[0])

        assert workers.calls == []
        h.play_track.assert_awaited_once()

    async def test_play_waiting_on_a_prefetch_survives_the_refill_prefetch(self):
        # The refill's prefetch must not cancel a running prefetch that a play
        # has joined: cancelling its owner cancels the shared resolve.
        workers = _Workers()
        h = _host(workers)
        _load(h, "q", 1)
        resolver = StreamResolver()
        gate = threading.Event()
        extractions: list[str] = []
        info = _stream_info("radio0")

        def slow_extract(video_id: str):
            extractions.append(video_id)
            gate.wait(5)
            return info

        resolver._resolve_sync = slow_extract
        h.stream_resolver = resolver
        workers.run(resolver.prefetch("radio0"), group="prefetch", exclusive=True)
        await asyncio.sleep(0.05)
        play = asyncio.ensure_future(resolver.resolve("radio0"))
        await asyncio.sleep(0.05)

        h.ytmusic.get_radio = AsyncMock(return_value=_tracks("radio", 3))
        await h._fetch_and_play_radio(h.queue.tracks[0], append=True)
        gate.set()

        assert await asyncio.wait_for(play, timeout=5) is info
        await workers.settle()
        assert extractions == ["radio0"]

    async def test_autoplay_at_queue_end_resolves_the_first_radio_track_once(self):
        workers = _Workers()
        h = _fresh_playback_host()
        h.queue = QueueManager()
        h.queue.add_multiple(_tracks("q", 1))
        h.queue.jump_to(0)
        h.workers = workers
        h.run_worker = workers.run
        h._refresh_queue_page = MagicMock()
        h.settings.playback.autoplay = True
        h.player.current_track = h.queue.tracks[0]
        h.ytmusic = MagicMock()
        h.ytmusic.get_radio = AsyncMock(return_value=_tracks("radio", 3))
        _accept_playback(h)
        resolver = StreamResolver()
        extractions: list[str] = []

        def extract(video_id: str):
            extractions.append(video_id)
            return _stream_info(video_id)

        resolver._resolve_sync = extract
        h.stream_resolver = resolver

        await h._play_next()
        await workers.settle()

        assert h.player.play.await_args.args[0] == "http://stream/radio0"
        assert extractions == ["radio0"]
