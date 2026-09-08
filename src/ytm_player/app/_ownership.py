"""Scheduling-time ownership for playback requests and queue continuations.

Nested collection/radio helpers inherit their caller's request. Background
tails instead belong to a queue lifetime: ordinary Next must not cancel them.
Only tokens live here; native command ordering belongs to Player.
"""

from __future__ import annotations

import time
from collections.abc import Callable, Coroutine, Iterator
from contextlib import contextmanager
from contextvars import ContextVar
from functools import wraps
from typing import Any, TypeVar, cast

_Owner = tuple[Any, int | None, int, int]
_owner: ContextVar[_Owner | None] = ContextVar("playback_owner", default=None)
_F = TypeVar("_F", bound=Callable[..., Coroutine[Any, Any, Any]])


def request_number(host: Any) -> int:
    return getattr(host, "_play_request", 0)


def invalidate_requests(host: Any) -> None:
    host._play_request = request_number(host) + 1
    host._queue_stop_generation = getattr(host, "_queue_stop_generation", 0) + 1


def owns_request(host: Any) -> bool:
    owner = _owner.get()
    if owner is None or owner[0] is not host:
        return True
    _, request, queue, stopped = owner
    if request is not None:
        return request == request_number(host) and queue == host.queue.generation
    return queue == host.queue.generation and stopped == getattr(host, "_queue_stop_generation", 0)


@contextmanager
def request_scope(host: Any, request: int) -> Iterator[None]:
    token = _owner.set((host, request, host.queue.generation, 0))
    try:
        yield
    finally:
        _owner.reset(token)


def adopt_queue(host: Any) -> None:
    """The owning request just replaced the queue; continue under its new lifetime."""
    owner = _owner.get()
    if owner is not None and owner[0] is host:
        _owner.set((host, owner[1], host.queue.generation, owner[3]))


def playback_request(fn: _F) -> _F:
    """Reserve before run_worker; check again before executing a queued request."""

    @wraps(fn)
    def schedule(host: Any, *args: Any, **kwargs: Any) -> Coroutine[Any, Any, Any]:
        if fn.__name__ == "play_track":
            from ytm_player.utils.formatting import get_video_id

            track = args[0] if args else kwargs.get("track")
            video_id = get_video_id(track) if isinstance(track, dict) else None
            parent = _owner.get()
            if track is None or (
                (parent is None or parent[0] is not host)
                and video_id
                and video_id == host._last_play_video_id
                and time.monotonic() - host._last_play_time < 1.0
            ):
                # A debounced click must not cancel the request it duplicates.
                async def ignored() -> None:
                    return

                return ignored()
        background = fn.__name__ in (
            "_fetch_remaining_for_queue",
            "_fetch_remaining_artist_songs",
        ) or (fn.__name__ == "_fetch_and_play_radio" and kwargs.get("append", False))
        # Non-play entity actions neither replace nor supersede playback.
        action = args[0] if args else kwargs.get("action_id")
        if fn.__name__ == "_dispatch_entity_action" and action not in (
            "play_all",
            "shuffle_play",
            "start_radio",
            "play_top_songs",
        ):
            return fn(host, *args, **kwargs)
        inherited = _owner.get()
        if background:
            owner: _Owner = (
                host,
                None,
                host.queue.generation,
                getattr(host, "_queue_stop_generation", 0),
            )
        elif inherited is not None and inherited[0] is host:
            owner = inherited
        else:
            host._play_request = request_number(host) + 1
            owner = (host, host._play_request, host.queue.generation, 0)

        async def run() -> Any:
            token = _owner.set(owner)
            try:
                if owns_request(host):
                    return await fn(host, *args, **kwargs)
            finally:
                finished_owner = _owner.get()
                _owner.reset(token)
                # Nested replacement must carry its new queue lifetime back
                # to the collection helper that will schedule the tail.
                if inherited is not None and inherited[0] is host and not background:
                    _owner.set(finished_owner)

        return run()

    return cast(_F, schedule)
