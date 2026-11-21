"""Playback ownership: may an async playback continuation still act?

One app-owned object, ``PlaybackOwnership``, makes every ownership decision:
playback requests, play attempts, the single retry, background tails, autoplay
refill and the duplicate-click debounce. Terms are defined in GLOSSARY.md; the
contract is the invalidation table in docs/design/play-attempt-module.md.

The queue lifetime counter belongs to QueueManager and is only read here, so
callers pass ``queue.generation`` in. Native command ordering belongs to Player.

Ownership is captured when the caller constructs the coroutine, before
``run_worker`` runs it: a delayed worker must not claim ownership after a
newer request. A ContextVar carries the owner, so a nested helper runs under
its caller's request.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from dataclasses import dataclass
from enum import Enum
from functools import wraps
from typing import Any, TypeVar, cast

_F = TypeVar("_F", bound=Callable[..., Coroutine[Any, Any, Any]])

# A click on the same video within this window is a duplicate.
_DEBOUNCE_SECONDS = 1.0


class Policy(Enum):
    """How a decorated method relates to playback requests."""

    # Reserves a new playback request, or inherits the caller's on the same app.
    FOREGROUND = "foreground"
    # A background tail: owned by the queue lifetime and the stop count.
    BACKGROUND = "background"
    # Runs untouched; neither replaces nor supersedes playback.
    BYPASS = "bypass"
    # Does nothing and leaves the current request alone.
    IGNORE = "ignore"


class ErrorAction(Enum):
    """What to do with the ERROR of the current play attempt."""

    RETRY = "retry"  # the request's first failure: replay once
    GIVE_UP = "give_up"  # the retry failed too: run the failure policy


@dataclass(frozen=True)
class _Owner:
    ownership: PlaybackOwnership
    request: int | None  # None: a background tail
    queue: int
    stop: int


_owner: ContextVar[_Owner | None] = ContextVar("playback_owner", default=None)


@dataclass(frozen=True)
class Snapshot:
    """The current attempt, request and queue lifetime at one moment."""

    ownership: PlaybackOwnership
    attempt: int
    request: int
    queue: int

    def still_current(self, queue_generation: int) -> bool:
        """No newer attempt, request, stop or queue replacement since the snapshot."""
        return (
            self.attempt == self.ownership._attempt
            and self.request == self.ownership._request
            and self.queue == queue_generation
        )

    @contextmanager
    def request_scope(self) -> Iterator[None]:
        """Run nested playback under the snapshot's request."""
        token = _owner.set(_Owner(self.ownership, self.request, self.queue, 0))
        try:
            yield
        finally:
            _owner.reset(token)


@dataclass(frozen=True)
class PrefetchSnapshot:
    """Queue lifetime, stop count and play attempt a prefetch batch was scheduled under."""

    queue: int
    stop: int
    attempt: int


class PlaybackOwnership:
    """Owns every playback ownership decision for one app."""

    def __init__(self) -> None:
        self._request = 0
        self._stop = 0
        self._attempt = 0
        self._handled_end = -1
        self._handled_error = 0
        # The one retry a failed attempt gets, while it is in flight.
        self._retry_attempt: int | None = None
        # (queue lifetime, stop count) the running refill was started for.
        self._refill: tuple[int, int] | None = None
        self._last_video_id = ""
        self._last_play_time = 0.0

    # ── Playback requests ───────────────────────────────────────────

    def owns(self, queue_generation: int) -> bool:
        """True unless the running request or tail has been superseded."""
        owner = _owner.get()
        if owner is None or owner.ownership is not self:
            return True
        if owner.request is not None:
            return owner.request == self._request and owner.queue == queue_generation
        return owner.queue == queue_generation and owner.stop == self._stop

    def adopt_queue(self, queue_generation: int) -> None:
        """The owning request just replaced the queue; continue under its new lifetime."""
        owner = _owner.get()
        if owner is not None and owner.ownership is self:
            _owner.set(_Owner(self, owner.request, queue_generation, owner.stop))

    def stop(self) -> None:
        """Supersede every playback request, play attempt and background tail."""
        self._request += 1
        self._stop += 1
        self._attempt += 1
        self._retry_attempt = None
        self.reset_debounce()

    def _owner_for(self, policy: Policy, queue_generation: int) -> tuple[_Owner, bool]:
        """Owner a scheduled call runs under, and whether it is nested in this app's request."""
        inherited = _owner.get()
        nested = inherited is not None and inherited.ownership is self
        if policy is Policy.BACKGROUND:
            return _Owner(self, None, queue_generation, self._stop), nested
        if nested:
            return cast(_Owner, inherited), nested
        self._request += 1
        return _Owner(self, self._request, queue_generation, 0), nested

    # ── Duplicate-click debounce ────────────────────────────────────

    def is_duplicate_click(self, video_id: str) -> bool:
        """A direct repeat click; nested Next/EOF may replay the same song."""
        owner = _owner.get()
        if owner is not None and owner.ownership is self:
            return False
        return (
            video_id == self._last_video_id
            and time.monotonic() - self._last_play_time < _DEBOUNCE_SECONDS
        )

    def record_play(self, video_id: str) -> None:
        self._last_video_id = video_id
        self._last_play_time = time.monotonic()

    def reset_debounce(self) -> None:
        """Let the user (or the retry) play the same track again immediately."""
        self._last_video_id = ""
        self._last_play_time = 0.0

    # ── Play attempts ───────────────────────────────────────────────

    def commit_attempt(self, *, retry: bool) -> int:
        """Start a new play attempt; it supersedes every older one.

        A request that returns without committing a play does not orphan
        this attempt's events. Any committed play other than the retry
        starts without a retry in flight.
        """
        self._attempt += 1
        self._retry_attempt = self._attempt if retry else None
        return self._attempt

    def is_current_attempt(self, attempt: object) -> bool:
        return attempt == self._attempt

    def owns_attempt(self, attempt: int, queue_generation: int) -> bool:
        """The attempt is current and its request still owns playback."""
        return attempt == self._attempt and self.owns(queue_generation)

    def snapshot(self, queue_generation: int) -> Snapshot:
        return Snapshot(self, self._attempt, self._request, queue_generation)

    def claim_end(self, attempt: int) -> bool:
        """Consume the EOF of the current attempt, at most once."""
        if attempt != self._attempt or attempt <= self._handled_end:
            return False
        self._handled_end = attempt
        return True

    def claim_error(self, attempt: int) -> ErrorAction | None:
        """Consume the ERROR of the current attempt, at most once. None: drop it."""
        if attempt != self._attempt or attempt <= self._handled_error:
            return None
        self._handled_error = attempt
        if attempt == self._retry_attempt:
            self._retry_attempt = None
            return ErrorAction.GIVE_UP
        return ErrorAction.RETRY

    @property
    def retry_pending(self) -> bool:
        return self._retry_attempt is not None

    def audio_advanced(self) -> None:
        """Audio of the current attempt is flowing: the retry budget is free again."""
        self._retry_attempt = None

    # ── Stream prefetch ─────────────────────────────────────────────

    def capture_prefetch(self, queue_generation: int) -> PrefetchSnapshot:
        return PrefetchSnapshot(queue_generation, self._stop, self._attempt)

    def prefetch_window(
        self, snapshot: PrefetchSnapshot, queue_generation: int, count: int, exclusive: bool
    ) -> int:
        """How many upcoming tracks the batch may still prefetch; 0 ends it.

        Stop or a queue replacement ends the batch. A newer committed attempt
        ends an exclusive batch and shrinks a non-exclusive (refill) batch by
        one per attempt. A request that only reserved leaves the batch alone.
        """
        if snapshot.queue != queue_generation or snapshot.stop != self._stop:
            return 0
        advances = self._attempt - snapshot.attempt
        if exclusive and advances:
            return 0
        return max(0, count - advances)

    # ── Autoplay refill ─────────────────────────────────────────────

    def refill_claimed(self, queue_generation: int) -> bool:
        """A refill was already started for this queue lifetime since the last Stop."""
        return self._refill == (queue_generation, self._stop)

    def claim_refill(self, queue_generation: int) -> None:
        self._refill = (queue_generation, self._stop)


# ── App-side adapters (the app's ownership object and its queue) ─────


def owns_request(host: Any) -> bool:
    return host._ownership.owns(host.queue.generation)


def owns_attempt(host: Any, attempt: int) -> bool:
    return host._ownership.owns_attempt(attempt, host.queue.generation)


def adopt_queue(host: Any) -> None:
    host._ownership.adopt_queue(host.queue.generation)


@contextmanager
def unowned_scope() -> Iterator[None]:
    """Run a block outside any request, then restore the caller's ownership.

    A task started inside a request copies its context and keeps that
    request's token for life. A page mounted from a request must not: its
    own plays would be dropped as soon as a newer request exists.
    """
    token = _owner.set(None)
    try:
        yield
    finally:
        _owner.reset(token)


async def _ignored() -> None:
    return


def playback_request(policy: Policy | Callable[..., Policy]) -> Callable[[_F], _F]:
    """Declare a method's ownership policy; ownership is captured at scheduling.

    ``policy`` is a ``Policy``, or a classifier called with the app's
    ``PlaybackOwnership`` and the method's arguments for methods whose
    policy depends on them.
    """

    def decorate(fn: _F) -> _F:
        @wraps(fn)
        def schedule(host: Any, *args: Any, **kwargs: Any) -> Coroutine[Any, Any, Any]:
            ownership: PlaybackOwnership = host._ownership
            chosen = policy if isinstance(policy, Policy) else policy(ownership, *args, **kwargs)
            if chosen is Policy.IGNORE:
                # A debounced click must not cancel the request it duplicates.
                return _ignored()
            if chosen is Policy.BYPASS:
                return fn(host, *args, **kwargs)
            owner, nested = ownership._owner_for(chosen, host.queue.generation)

            async def run() -> Any:
                token = _owner.set(owner)
                try:
                    if ownership.owns(host.queue.generation):
                        return await fn(host, *args, **kwargs)
                finally:
                    finished = _owner.get()
                    _owner.reset(token)
                    # Nested replacement must carry its new queue lifetime back
                    # to the collection helper that will schedule the tail.
                    if nested and chosen is Policy.FOREGROUND:
                        _owner.set(finished)

            return run()

        return cast(_F, schedule)

    return decorate
