"""Autoplay refill: stale refill workers and the prefetch after an append."""

from __future__ import annotations

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

from tests.test_app.test_playback import _accept_playback, _fresh_playback_host, _stream_info
from ytm_player.app._ownership import PlaybackOwnership
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


class _GatedResolver:
    """Real StreamResolver that gates the first extraction of *gated_id* and tracks concurrency."""

    def __init__(self, gated_id: str) -> None:
        self.gated_id = gated_id
        self.resolver = StreamResolver()
        self.entered = threading.Event()
        self.release = threading.Event()
        self.extractions: list[str] = []
        self.max_active = 0
        self._active = 0
        self._lock = threading.Lock()
        self.resolver._resolve_sync = self._extract

    def _extract(self, video_id: str):
        with self._lock:
            self._active += 1
            self.max_active = max(self.max_active, self._active)
            self.extractions.append(video_id)
            gate_this = video_id == self.gated_id and self.extractions.count(video_id) == 1
        try:
            if gate_this:
                self.entered.set()
                self.release.wait(timeout=5)
            return _stream_info(video_id)
        finally:
            with self._lock:
                self._active -= 1


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
    h._ownership = PlaybackOwnership()
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

        h._ownership.stop()  # Stop: the pending refill's result will be dropped
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


class TestPrefetchLookahead:
    """[cache] prefetch_next supports disabling (false/0) and sequential N-track lookahead."""

    async def test_prefetch_disabled_with_false_or_zero_starts_no_worker(self):
        for disabled_value in (False, 0):
            workers = _Workers()
            h = _host(workers)
            h.settings.cache.prefetch_next = disabled_value
            _load(h, "q", 4)

            h._prefetch_next_track()
            await workers.settle()

            assert workers.calls == []
            h.stream_resolver.prefetch.assert_not_called()

    async def test_prefetch_lookahead_runs_sequentially_and_deduplicates(self):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 4
        h.queue.add_multiple(
            [
                {"video_id": "q0", "title": "q0"},
                {"video_id": "q1", "title": "q1"},
                {"title": "no_id"},
                {"video_id": "q1", "title": "q1_dup"},
                {"video_id": "q2", "title": "q2"},
                {"video_id": "q3", "title": "q3"},
            ]
        )
        h.queue.jump_to(0)

        active = 0
        max_active = 0
        seen: list[str] = []

        async def tracked_prefetch(video_id: str) -> None:
            nonlocal active, max_active
            active += 1
            max_active = max(max_active, active)
            seen.append(video_id)
            await asyncio.sleep(0.01)
            active -= 1

        h.stream_resolver.prefetch = AsyncMock(side_effect=tracked_prefetch)

        h._prefetch_next_track()
        await workers.settle()

        assert workers.calls == [{"group": "prefetch", "exclusive": True}]
        assert max_active == 1
        assert seen == ["q1", "q2"]

    async def test_prefetch_lookahead_stops_when_queue_cleared_mid_sequence(self):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 3
        _load(h, "q", 5)

        seen: list[str] = []

        async def clear_on_first(video_id: str) -> None:
            seen.append(video_id)
            h.queue.clear()

        h.stream_resolver.prefetch = AsyncMock(side_effect=clear_on_first)

        h._prefetch_next_track()
        await workers.settle()

        assert seen == ["q1"]

    async def test_prefetch_lookahead_stops_when_playback_stopped_mid_sequence(self):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 3
        _load(h, "q", 5)

        seen: list[str] = []

        async def stop_on_first(video_id: str) -> None:
            seen.append(video_id)
            h._ownership.stop()

        h.stream_resolver.prefetch = AsyncMock(side_effect=stop_on_first)

        h._prefetch_next_track()
        await workers.settle()

        assert seen == ["q1"]

    @pytest.mark.parametrize(
        ("prefetch_next", "remaining", "expected_started"),
        [
            (False, 3, 1),
            (False, 4, 0),
            (0, 3, 1),
            (0, 4, 0),
            (True, 3, 1),
            (True, 4, 0),
            (5, 5, 1),
            (5, 6, 0),
        ],
    )
    async def test_refill_threshold_scales_with_prefetch_count(
        self, prefetch_next: bool | int, remaining: int, expected_started: int
    ):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = prefetch_next
        h.ytmusic.get_radio = AsyncMock(return_value=_tracks("radio", 3))
        _load(h, "q", remaining + 1)  # jump_to(0) leaves `remaining` upcoming tracks
        assert h.queue.remaining_tracks == remaining

        h._refill_queue()
        await workers.settle()

        assert workers.started("queue_extend") == expected_started

    async def test_overlapping_refill_prefetch_never_leapfrogs_active_extraction(self):
        """With real StreamResolver and prefetch_next=3, an exclusive=False refill
        worker whose first uncached track differs from W1's in-flight extraction
        still waits on _prefetch_lock instead of extracting concurrently."""
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 3
        _load(h, "q", 3)  # q0 playing -> q1, q2 remaining

        gated = _GatedResolver("q1")
        h.stream_resolver = gated.resolver

        try:
            # Track change starts W1 (exclusive=True) for [q1, q2]; q1 blocks mid-extract.
            h._prefetch_next_track(exclusive=True)
            assert await asyncio.to_thread(gated.entered.wait, 5)

            # Splice urgent0 ahead of q1 so W2's upcoming window [urgent0, q1, q2]
            # starts with an uncached track NOT in resolver._pending — only
            # _prefetch_lock prevents W2 from extracting urgent0 concurrently with q1.
            h.queue.add_next({"video_id": "urgent0", "title": "urgent0"})
            h.ytmusic.get_radio = AsyncMock(return_value=_tracks("radio", 2))
            await h._fetch_and_play_radio(h.queue.tracks[0], append=True)
            await asyncio.sleep(0.05)

            # While q1 is still in flight, W2 must NOT have started urgent0, q2, or radio0.
            assert gated.extractions == ["q1"]
        finally:
            gated.release.set()
            await workers.settle()

        assert gated.max_active == 1
        assert gated.extractions == ["q1", "urgent0", "q2"]

    async def test_autoplay_at_queue_end_with_lookahead_serializes_with_play_resolve(self):
        """At EOF with prefetch_next=3, the refill prefetch waits on play_track's
        in-flight radio0 resolve before extracting radio1/radio2."""
        workers = _Workers()
        h = _fresh_playback_host()
        h.queue = QueueManager()
        h.queue.add_multiple(_tracks("q", 1))
        h.queue.jump_to(0)
        h.workers = workers
        h.run_worker = workers.run
        h._refresh_queue_page = MagicMock()
        h.settings.playback.autoplay = True
        h.settings.cache.prefetch_next = 3
        h.player.current_track = h.queue.tracks[0]
        h.ytmusic = MagicMock()
        h.ytmusic.get_radio = AsyncMock(return_value=_tracks("radio", 5))
        _accept_playback(h)

        gated = _GatedResolver("radio0")
        h.stream_resolver = gated.resolver

        play_next = asyncio.create_task(h._play_next())
        try:
            assert await asyncio.to_thread(gated.entered.wait, 5)
            await asyncio.sleep(0.05)

            # While play_track is still extracting radio0, the refill prefetch
            # worker must wait rather than starting radio1 concurrently.
            assert gated.extractions == ["radio0"]
            gated.release.set()
            await asyncio.wait_for(play_next, timeout=5)
            await workers.settle()
        finally:
            gated.release.set()
            if not play_next.done():
                play_next.cancel()
                await asyncio.gather(play_next, return_exceptions=True)

        assert h.player.play.await_args.args[0] == "http://stream/radio0"
        assert gated.max_active == 1
        assert gated.extractions == ["radio0", "radio1", "radio2"]

    async def test_prefetch_lookahead_waits_for_inflight_resolve_before_advancing(self):
        """When upcoming q1 is already mid-resolve on StreamResolver, lookahead
        joins q1's pending future instead of skipping ahead to extract q2 in parallel."""
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 2
        _load(h, "q", 3)  # q0 playing -> upcoming [q1, q2]

        gated = _GatedResolver("q1")
        h.stream_resolver = gated.resolver

        inflight = asyncio.create_task(gated.resolver.resolve("q1"))
        try:
            assert await asyncio.to_thread(gated.entered.wait, 5)
            h._prefetch_next_track(exclusive=True)
            await asyncio.sleep(0.05)

            assert gated.extractions == ["q1"]
            gated.release.set()
            await asyncio.wait_for(inflight, timeout=5)
            await workers.settle()
        finally:
            gated.release.set()
            if not inflight.done():
                inflight.cancel()
                await asyncio.gather(inflight, return_exceptions=True)

        assert gated.max_active == 1
        assert gated.extractions == ["q1", "q2"]

    async def test_prefetch_lookahead_adapts_to_mid_sequence_queue_mutation(self):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 3
        _load(h, "q", 5)  # q0 playing; upcoming initially [q1, q2, q3]

        seen: list[str] = []

        async def mutate_on_first(video_id: str) -> None:
            seen.append(video_id)
            if video_id == "q1":
                h.queue.remove(2)
                h.queue.add_next({"video_id": "urgent", "title": "urgent"})

        h.stream_resolver.prefetch = AsyncMock(side_effect=mutate_on_first)

        h._prefetch_next_track()
        await workers.settle()

        assert seen == ["q1", "urgent", "q3"]

    async def test_prefetch_yields_during_foreground_play_track_pre_resolve(self):
        workers = _Workers()
        h = _fresh_playback_host()
        h.queue = QueueManager()
        h.queue.add_multiple(_tracks("q", 5))
        h.queue.jump_to(0)
        h.workers = workers
        h.run_worker = workers.run
        h.settings.cache.prefetch_next = 3
        _accept_playback(h)

        log_entered = asyncio.Event()
        log_release = asyncio.Event()

        async def slow_log() -> None:
            log_entered.set()
            await log_release.wait()

        h._log_current_listen = slow_log
        extractions: list[str] = []
        resolver = StreamResolver()

        def extract(video_id: str):
            extractions.append(video_id)
            return _stream_info(video_id)

        resolver._resolve_sync = extract
        h.stream_resolver = resolver

        jumped = h.queue.jump_to(3)
        play_task = asyncio.create_task(h.play_track(jumped))
        await log_entered.wait()

        # Schedule a prefetch batch while play_track(q3) is still pre-resolving.
        h._prefetch_next_track(exclusive=False)
        await asyncio.sleep(0.02)
        assert extractions == []

        log_release.set()
        await play_task
        await workers.settle()

        assert extractions == ["q3", "q4"]

    async def test_prefetch_skips_tracks_already_cached_on_disk(self, tmp_path):
        from ytm_player.services.cache import CacheManager

        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 3
        cache = CacheManager(cache_dir=tmp_path / "cache", db_path=tmp_path / "cache.db")
        await cache.init()
        try:
            await cache.put("q1", b"cached audio", "opus")
            h.cache = cache
            _load(h, "q", 4)  # q0 playing -> upcoming [q1 (cached), q2, q3]

            h._prefetch_next_track()
            await workers.settle()

            assert [c.args[0] for c in h.stream_resolver.prefetch.await_args_list] == ["q2", "q3"]
        finally:
            await cache.close()

    async def test_track_advance_cancellation_reuses_inflight_prefetch_extraction(self):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 2
        _load(h, "q", 4)  # q0 playing -> upcoming [q1, q2]

        gated = _GatedResolver("q2")
        h.stream_resolver = gated.resolver

        try:
            # W1 caches q1, then blocks mid-extraction on q2.
            h._prefetch_next_track(exclusive=True)
            old_worker = workers.tasks[-1][1]
            assert await asyncio.to_thread(gated.entered.wait, 5)

            # Advance queue to q1 and trigger _on_track_change's exclusive=True
            # replacement prefetch for [q2, q3].
            assert h.queue.next_track()["video_id"] == "q1"
            h._prefetch_next_track(exclusive=True)
            await asyncio.sleep(0)
            assert old_worker.cancelled()
        finally:
            gated.release.set()
            await workers.settle()

        assert gated.resolver._get_cached("q2") is not None
        assert gated.resolver._get_cached("q3") is not None
        assert gated.extractions == ["q1", "q2", "q3"]

    async def test_prefetch_continues_when_playing_track_removed_from_queue(self):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 3
        _load(h, "q", 4)  # q0 playing -> upcoming [q1, q2, q3]

        seen: list[str] = []

        async def remove_current_on_first(video_id: str) -> None:
            seen.append(video_id)
            if video_id == "q1":
                h.queue.remove(0)  # removes q0 while mpv keeps playing q0

        h.stream_resolver.prefetch = AsyncMock(side_effect=remove_current_on_first)

        h._prefetch_next_track(exclusive=True)
        await workers.settle()

        assert seen == ["q1", "q2", "q3"]

    async def test_clear_cache_before_worker_replacement_reprefetches_stale_inflight_track(self):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 2
        _load(h, "q", 4)  # q0 playing -> upcoming [q1, q2]

        gated = _GatedResolver("q2")
        h.stream_resolver = gated.resolver

        try:
            h._prefetch_next_track(exclusive=True)
            assert await asyncio.to_thread(gated.entered.wait, 5)

            # Invalidate cache generation BEFORE scheduling the replacement worker.
            gated.resolver.clear_cache()
            assert h.queue.next_track()["video_id"] == "q1"
            h._prefetch_next_track(exclusive=True)
            await asyncio.sleep(0)
        finally:
            gated.release.set()
            await workers.settle()

        assert gated.resolver._get_cached("q2") is not None
        assert gated.resolver._get_cached("q3") is not None
        assert gated.extractions == ["q1", "q2", "q2", "q3"]

    async def test_prefetch_lookahead_recovers_past_failed_extractions_and_cools_down_unplayable(
        self,
    ):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 3
        _load(h, "q", 5)  # q0 playing -> upcoming [q1, q2, q3]

        resolver = StreamResolver()
        extractions: list[str] = []

        def extract(video_id: str):
            extractions.append(video_id)
            if video_id == "q1":
                raise RuntimeError("boom")
            if video_id == "q2":
                return None
            return _stream_info(video_id)

        resolver._resolve_sync = extract
        h.stream_resolver = resolver

        h._prefetch_next_track(exclusive=True)
        await workers.settle()
        assert extractions == ["q1", "q2", "q3"]
        assert resolver._get_cached("q3") is not None

        # Advancing to q1 shifts window to [q2, q3, q4]: q2 is cooled down,
        # q3 is cached, so only q4 is extracted.
        assert h.queue.next_track()["video_id"] == "q1"
        h._prefetch_next_track(exclusive=True)
        await workers.settle()
        assert extractions == ["q1", "q2", "q3", "q4"]

    async def test_prefetch_ignores_cache_check_errors_and_invalid_cache_hooks(self):
        import sqlite3

        for bad_cache in (
            SimpleNamespace(has="not_callable"),
            SimpleNamespace(has=lambda _vid: True),
            SimpleNamespace(has=AsyncMock(side_effect=sqlite3.OperationalError("locked"))),
            SimpleNamespace(has=AsyncMock(side_effect=RuntimeError("Database not initialized"))),
            SimpleNamespace(has=AsyncMock(side_effect=ValueError("Connection closed"))),
        ):
            workers = _Workers()
            h = _host(workers)
            h.settings.cache.prefetch_next = 2
            h.cache = bad_cache
            _load(h, "q", 3)

            h._prefetch_next_track(exclusive=True)
            await workers.settle()

            assert [c.args[0] for c in h.stream_resolver.prefetch.await_args_list] == ["q1", "q2"]

    async def test_overlapping_foreground_play_tracks_keep_prefetch_parked_until_winner_finishes(
        self,
    ):
        workers = _Workers()
        h = _fresh_playback_host()
        h.queue = QueueManager()
        h.queue.add_multiple(_tracks("q", 5))
        h.queue.jump_to(0)
        h.workers = workers
        h.run_worker = workers.run
        h.settings.cache.prefetch_next = 2
        _accept_playback(h)

        a_entered = asyncio.Event()
        a_release = asyncio.Event()
        b_entered = asyncio.Event()
        b_release = asyncio.Event()
        extractions: list[str] = []
        resolver = StreamResolver()

        async def gated_resolve(video_id: str):
            if video_id == "q1":
                a_entered.set()
                await a_release.wait()
            elif video_id == "q2":
                b_entered.set()
                await b_release.wait()
            extractions.append(video_id)
            return _stream_info(video_id)

        resolver.resolve = gated_resolve  # type: ignore[method-assign]
        resolver._resolve_sync = lambda vid: extractions.append(vid) or _stream_info(vid)
        h.stream_resolver = resolver

        t_a = asyncio.create_task(h.play_track(h.queue.jump_to(1)))
        await a_entered.wait()
        h._prefetch_next_track(exclusive=True)  # scheduled under play_gen 1
        t_b = asyncio.create_task(h.play_track(h.queue.jump_to(2)))  # bumps to play_gen 2
        await b_entered.wait()

        # Earlier play_track(q1) finishes first — must NOT unblock prefetch while q2 resolves.
        a_release.set()
        await t_a
        h._prefetch_next_track(exclusive=False)  # scheduled under play_gen 2 -> [q3, q4]
        await asyncio.sleep(0.02)
        assert extractions == ["q1"]

        # Once active play_track(q2) finishes, the play_gen 1 exclusive=True worker
        # exits with window=0 and the play_gen 2 worker extracts [q3, q4].
        b_release.set()
        await t_b
        await workers.settle()
        assert extractions == ["q1", "q2", "q3", "q4"]

    async def test_prefetch_revalidates_upcoming_after_async_disk_cache_check(self):
        workers = _Workers()
        h = _host(workers)
        h.settings.cache.prefetch_next = 2
        _load(h, "q", 4)  # q0 playing -> upcoming [q1, q2]

        async def mutating_has(video_id: str) -> bool:
            if video_id == "q1":
                h.queue.remove(1)  # removes q1 while cache.has("q1") awaits
            elif video_id == "q3":
                h.queue.clear()  # clears queue while cache.has("q3") awaits
            await asyncio.sleep(0)
            return False

        h.cache = SimpleNamespace(has=mutating_has)

        h._prefetch_next_track(exclusive=True)
        await workers.settle()

        # q1 was removed during its cache check -> skipped; q2 prefetched;
        # q3 triggered queue.clear() during its cache check -> aborted cleanly.
        assert [c.args[0] for c in h.stream_resolver.prefetch.await_args_list] == ["q2"]
