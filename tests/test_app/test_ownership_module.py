"""Direct tests of PlaybackOwnership, without a mixin host.

One test per row of the invalidation table in
docs/design/play-attempt-module.md, plus the other rules behind the seam.
"""

from __future__ import annotations

import asyncio

from ytm_player.app._ownership import (
    ErrorAction,
    PlaybackOwnership,
    Policy,
    adopt_queue,
    owns_request,
    playback_request,
    unowned_scope,
)
from ytm_player.services.queue import QueueManager


def _classify(ownership: PlaybackOwnership, chosen: Policy) -> Policy:
    return chosen


class _Host:
    """The two attributes the decorator reads, plus decorated methods."""

    def __init__(self) -> None:
        self._ownership = PlaybackOwnership()
        self.queue = QueueManager()
        self.release = asyncio.Event()
        self.ran: list[str] = []

    @playback_request(Policy.FOREGROUND)
    async def request(self) -> bool:
        """Wait for release, then report whether the request still owns playback."""
        self.ran.append("request")
        await self.release.wait()
        return owns_request(self)

    @playback_request(Policy.BACKGROUND)
    async def tail(self) -> bool:
        self.ran.append("tail")
        await self.release.wait()
        return owns_request(self)

    @playback_request(Policy.FOREGROUND)
    async def instant(self) -> str:
        self.ran.append("instant")
        return "ran"

    @playback_request(Policy.FOREGROUND)
    async def replace_queue(self) -> bool:
        self.queue.clear()
        adopt_queue(self)
        return owns_request(self)

    @playback_request(Policy.FOREGROUND)
    async def outer_replacing_via_nested(self) -> bool:
        await self.replace_queue()
        return owns_request(self)

    @playback_request(Policy.FOREGROUND)
    async def nested_duplicate_check(self, video_id: str) -> bool:
        return self._ownership.is_duplicate_click(video_id)

    @playback_request(_classify)
    async def classified(self, chosen: Policy) -> str:
        self.ran.append("classified")
        return "result"


async def _running(coro) -> asyncio.Task:
    """Start a coroutine and let it reach its first await."""
    task = asyncio.ensure_future(coro)
    await asyncio.sleep(0)
    return task


async def _finish(host: _Host, task: asyncio.Task):
    host.release.set()
    return await task


# ── Row: newer foreground request reserved ───────────────────────────


async def test_newer_request_invalidates_the_running_request():
    h = _Host()
    first = await _running(h.request())
    h.instant().close()  # constructing the coroutine reserves the request
    assert await _finish(h, first) is False


async def test_newer_request_keeps_the_audible_attempt_eligible():
    o = PlaybackOwnership()
    end_attempt = o.commit_attempt(retry=False)
    h = _Host()
    h._ownership = o
    h.instant().close()
    assert o.claim_end(end_attempt) is True

    error_attempt = o.commit_attempt(retry=False)
    h.instant().close()
    assert o.claim_error(error_attempt) is ErrorAction.RETRY


async def test_newer_request_keeps_the_background_tail():
    h = _Host()
    tail = await _running(h.tail())
    h.instant().close()
    assert await _finish(h, tail) is True


async def test_delayed_worker_cannot_claim_ownership_after_a_newer_request():
    h = _Host()
    older = h.instant()
    newer = h.instant()
    assert await newer == "ran"
    assert await older is None
    assert h.ran == ["instant"]


# ── Row: newer play attempt committed ────────────────────────────────


def test_newer_attempt_invalidates_the_older_attempt():
    o = PlaybackOwnership()
    old = o.commit_attempt(retry=False)
    new = o.commit_attempt(retry=False)
    assert not o.is_current_attempt(old)
    assert o.claim_end(old) is False
    assert o.claim_error(old) is None
    assert o.claim_end(new) is True


async def test_newer_attempt_keeps_the_background_tail():
    h = _Host()
    tail = await _running(h.tail())
    h._ownership.commit_attempt(retry=False)
    assert await _finish(h, tail) is True


# ── Row: Stop ────────────────────────────────────────────────────────


async def test_stop_invalidates_the_request():
    h = _Host()
    task = await _running(h.request())
    h._ownership.stop()
    assert await _finish(h, task) is False


def test_stop_invalidates_the_attempt():
    o = PlaybackOwnership()
    attempt = o.commit_attempt(retry=False)
    o.stop()
    assert not o.is_current_attempt(attempt)
    assert o.claim_end(attempt) is False
    assert o.claim_error(attempt) is None


def test_stop_clears_a_pending_retry():
    o = PlaybackOwnership()
    retry = o.commit_attempt(retry=True)
    o.stop()
    assert not o.retry_pending
    assert o.claim_error(retry) is None


async def test_stop_invalidates_the_background_tail():
    h = _Host()
    tail = await _running(h.tail())
    h._ownership.stop()
    assert await _finish(h, tail) is False


# ── Row: queue clear/replace by someone else ─────────────────────────


async def test_foreign_queue_replace_invalidates_the_request():
    h = _Host()
    task = await _running(h.request())
    h.queue.clear()
    assert await _finish(h, task) is False


def test_foreign_queue_replace_before_eof_keeps_the_attempt():
    h = _Host()
    end_attempt = h._ownership.commit_attempt(retry=False)
    h.queue.clear()
    assert h._ownership.claim_end(end_attempt) is True

    error_attempt = h._ownership.commit_attempt(retry=False)
    h.queue.clear()
    assert h._ownership.claim_error(error_attempt) is ErrorAction.RETRY


async def test_foreign_queue_replace_invalidates_the_background_tail():
    h = _Host()
    tail = await _running(h.tail())
    h.queue.clear()
    assert await _finish(h, tail) is False


# ── Row: queue replaced by the owning request (adopt_queue) ──────────


async def test_owning_request_keeps_ownership_after_adopting_its_new_queue():
    h = _Host()
    assert await h.replace_queue() is True


async def test_adopted_queue_lifetime_carries_back_to_the_calling_request():
    h = _Host()
    assert await h.outer_replacing_via_nested() is True


# ── Row: queue change while EOF awaits listen logging ────────────────


def test_eof_snapshot_goes_stale_when_the_queue_changes():
    h = _Host()
    attempt = h._ownership.commit_attempt(retry=False)
    assert h._ownership.claim_end(attempt)
    ended = h._ownership.snapshot(h.queue.generation)
    assert ended.still_current(h.queue.generation)
    h.queue.clear()
    assert not ended.still_current(h.queue.generation)


def test_eof_snapshot_goes_stale_on_new_attempt_request_or_stop():
    o = PlaybackOwnership()
    o.commit_attempt(retry=False)
    snap = o.snapshot(0)
    o.commit_attempt(retry=False)
    assert not snap.still_current(0)

    snap = o.snapshot(0)
    h = _Host()
    h._ownership = o
    h.instant().close()
    assert not snap.still_current(0)

    snap = o.snapshot(0)
    o.stop()
    assert not snap.still_current(0)


async def test_snapshot_request_scope_runs_nested_playback_under_its_request():
    h = _Host()
    snap = h._ownership.snapshot(h.queue.generation)
    with snap.request_scope():
        coro = h.instant()
    # Nested: no new request was reserved, so the snapshot is still current.
    assert snap.still_current(h.queue.generation)
    assert await coro == "ran"


# ── EOF and error dedupe ─────────────────────────────────────────────


def test_each_attempt_eof_is_handled_once():
    o = PlaybackOwnership()
    attempt = o.commit_attempt(retry=False)
    assert o.claim_end(attempt) is True
    assert o.claim_end(attempt) is False


def test_each_attempt_error_is_handled_once():
    o = PlaybackOwnership()
    attempt = o.commit_attempt(retry=False)
    assert o.claim_error(attempt) is ErrorAction.RETRY
    assert o.claim_error(attempt) is None


# ── Single-retry budget ──────────────────────────────────────────────


def test_failed_retry_gives_up():
    o = PlaybackOwnership()
    first = o.commit_attempt(retry=False)
    assert o.claim_error(first) is ErrorAction.RETRY
    retry = o.commit_attempt(retry=True)
    assert o.retry_pending
    assert o.claim_error(retry) is ErrorAction.GIVE_UP
    assert not o.retry_pending


def test_retry_budget_clears_once_audio_advances():
    o = PlaybackOwnership()
    retry = o.commit_attempt(retry=True)
    o.audio_advanced()
    assert not o.retry_pending
    assert o.claim_error(retry) is ErrorAction.RETRY


def test_ordinary_commit_clears_a_pending_retry():
    o = PlaybackOwnership()
    o.commit_attempt(retry=True)
    o.commit_attempt(retry=False)
    assert not o.retry_pending


# ── Refill ownership ─────────────────────────────────────────────────


def test_one_refill_per_queue_lifetime_and_stop_count():
    o = PlaybackOwnership()
    assert not o.refill_claimed(0)
    o.claim_refill(0)
    assert o.refill_claimed(0)
    assert not o.refill_claimed(1)  # a new queue lifetime
    o.stop()
    assert not o.refill_claimed(0)  # a new stop count


# ── Debounce ─────────────────────────────────────────────────────────


def test_repeat_click_within_the_window_is_a_duplicate(monkeypatch):
    now = [100.0]
    monkeypatch.setattr("ytm_player.app._ownership.time.monotonic", lambda: now[0])
    o = PlaybackOwnership()
    o.record_play("abc")
    assert o.is_duplicate_click("abc")
    assert not o.is_duplicate_click("xyz")
    now[0] = 101.0
    assert not o.is_duplicate_click("abc")


def test_nothing_is_a_duplicate_before_a_play_is_recorded():
    assert not PlaybackOwnership().is_duplicate_click("abc")


def test_reset_debounce_and_stop_both_clear_the_guard():
    o = PlaybackOwnership()
    o.record_play("abc")
    o.reset_debounce()
    assert not o.is_duplicate_click("abc")
    o.record_play("abc")
    o.stop()
    assert not o.is_duplicate_click("abc")


async def test_nested_plays_are_exempt_from_the_debounce():
    h = _Host()
    h._ownership.record_play("abc")
    assert h._ownership.is_duplicate_click("abc")
    assert await h.nested_duplicate_check("abc") is False


# ── Policies ─────────────────────────────────────────────────────────


async def test_ignore_leaves_the_current_request_alone():
    h = _Host()
    task = await _running(h.request())
    assert await h.classified(Policy.IGNORE) is None
    assert "classified" not in h.ran
    assert await _finish(h, task) is True


async def test_bypass_returns_the_result_and_leaves_the_request_alone():
    h = _Host()
    task = await _running(h.request())
    assert await h.classified(Policy.BYPASS) == "result"
    assert await _finish(h, task) is True


async def test_classifier_receives_the_ownership_and_arguments():
    seen: list[tuple] = []

    def classify(ownership: PlaybackOwnership, *args, **kwargs) -> Policy:
        seen.append((ownership, args, kwargs))
        return Policy.BYPASS

    class _Classified(_Host):
        @playback_request(classify)
        async def act(self, action: str, *, append: bool) -> str:
            return action

    h = _Classified()
    assert await h.act("play", append=True) == "play"
    assert seen == [(h._ownership, ("play",), {"append": True})]


async def test_unowned_scope_makes_a_nested_call_a_fresh_request():
    class _Mounting(_Host):
        @playback_request(Policy.FOREGROUND)
        async def mount_page(self) -> bool:
            with unowned_scope():
                self.instant().close()  # the page's own play: a new request
            return owns_request(self)

    h = _Mounting()
    assert await h.mount_page() is False


def test_prefetch_window_ends_on_stop_or_queue_replacement():
    ownership = PlaybackOwnership()
    snapshot = ownership.capture_prefetch(queue_generation=1)
    assert ownership.prefetch_window(snapshot, 1, 3, exclusive=False) == 3
    assert ownership.prefetch_window(snapshot, 2, 3, exclusive=False) == 0
    ownership.stop()
    assert ownership.prefetch_window(snapshot, 1, 3, exclusive=False) == 0


def test_prefetch_window_after_newer_attempts():
    ownership = PlaybackOwnership()
    snapshot = ownership.capture_prefetch(queue_generation=1)
    ownership._owner_for(Policy.FOREGROUND, 1)  # a reserved request alone keeps the window
    assert ownership.prefetch_window(snapshot, 1, 3, exclusive=True) == 3
    ownership.commit_attempt(retry=False)
    assert ownership.prefetch_window(snapshot, 1, 3, exclusive=True) == 0
    assert ownership.prefetch_window(snapshot, 1, 3, exclusive=False) == 2
    ownership.commit_attempt(retry=False)
    ownership.commit_attempt(retry=False)
    ownership.commit_attempt(retry=False)
    assert ownership.prefetch_window(snapshot, 1, 3, exclusive=False) == 0
