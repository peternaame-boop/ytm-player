"""Cancelling a prefetch does not cancel the play waiting on it (review B3).

Concurrent resolves of one video share an in-flight future. When its owner
(a prefetch worker) was cancelled, every waiter got ``CancelledError``
although nobody cancelled it; for a ``play_track`` running inside the
App's key handler that ended the App's message loop. The owner now settles
the shared future with a retry outcome and the waiter resolves again.

Extraction is a fake ``yt_dlp.YoutubeDL``; nothing reaches the network.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App
from textual.events import Key
from textual.worker import WorkerCancelled

from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.app._playback import PlaybackMixin
from ytm_player.config.keymap import KeyMap
from ytm_player.services.player import Player, PlayerEvent
from ytm_player.services.queue import QueueManager
from ytm_player.services.stream import StreamInfo, StreamResolver


class _Extraction:
    """Stands in for ``yt_dlp.YoutubeDL``: a gated video's ``extract_info``
    stays in flight until the test releases it."""

    def __init__(self, *gated: str) -> None:
        self.calls: list[str] = []
        self.cookiejar = MagicMock()
        self._gates = {video_id: threading.Event() for video_id in gated}
        self._entered = {video_id: threading.Event() for video_id in gated}

    def __call__(self, _opts: dict) -> _Extraction:
        return self

    def extract_info(self, url: str, download: bool = False) -> dict:
        video_id = url.rsplit("=", 1)[1]
        self.calls.append(video_id)
        if video_id in self._gates:
            self._entered[video_id].set()
            assert self._gates[video_id].wait(30), "the test never released the extraction"
        return {
            "url": f"https://stream.invalid/{video_id}",
            "acodec": "opus",
            "abr": 128,
            "duration": 200,
            "ext": "webm",
        }

    def close(self) -> None:
        pass

    async def entered(self, video_id: str) -> None:
        assert await asyncio.to_thread(self._entered[video_id].wait, 3), "extraction never started"

    def release(self) -> None:
        for gate in self._gates.values():
            gate.set()


@pytest.fixture
async def extraction(monkeypatch, tmp_path):
    """A resolver's extraction with video "B" held in flight.

    Async so the gates are released before the test's event loop closes:
    closing it joins the worker threads still inside ``extract_info``.
    """
    # Same isolation as tests/test_services/test_stream.py: never look at
    # the developer's real cookiejar or sign-in files.
    monkeypatch.setattr("ytm_player.services.stream._detect_stream_cookies", lambda: None)
    monkeypatch.setattr(
        "ytm_player.services.stream._detect_session_record",
        lambda: (str(tmp_path / "auth.json"), str(tmp_path / "account.json")),
    )
    fake = _Extraction("B")
    monkeypatch.setattr("yt_dlp.YoutubeDL", fake)
    yield fake
    fake.release()


class _Joins:
    """Tells when a task has joined an in-flight resolve.

    ``_resolve`` awaits the future ``_current_pending`` hands it within the
    same step, so a task that was handed one is waiting on that owner.
    """

    def __init__(self, resolver: StreamResolver) -> None:
        self._counts: dict[asyncio.Task, int] = {}
        self._followed: set[asyncio.Task] = set()
        self._changed = asyncio.Event()
        current_pending = resolver._current_pending

        def joining(video_id: str, generation: int):
            pending = current_pending(video_id, generation)
            task = asyncio.current_task()
            if pending is not None and task is not None:
                self._counts[task] = self._counts.get(task, 0) + 1
                # Signalled from the loop, not from inside the resolve's own
                # call stack, whatever its depth.
                asyncio.get_running_loop().call_soon(self._changed.set)
            return pending

        resolver._current_pending = joining  # type: ignore[method-assign]

    def count(self, task: asyncio.Task) -> int:
        return self._counts.get(task, 0)

    async def reached(self, task: asyncio.Task, count: int = 1) -> None:
        """Wait until *task* has joined *count* resolves, or has ended."""
        if task not in self._followed:
            self._followed.add(task)
            task.add_done_callback(lambda _task: self._changed.set())
        while self.count(task) < count and not task.done():
            self._changed.clear()
            await asyncio.wait_for(self._changed.wait(), 3)


async def _settled(*tasks: asyncio.Task) -> None:
    await asyncio.wait(tasks, timeout=3)
    assert all(task.done() for task in tasks), "a resolve never settled"


# ── The resolver ────────────────────────────────────────────────────────


async def test_cancelling_a_prefetch_does_not_cancel_the_play_waiting_on_it(extraction):
    resolver = StreamResolver()
    joins = _Joins(resolver)
    prefetch = asyncio.create_task(resolver.prefetch("B"))
    await extraction.entered("B")
    play = asyncio.create_task(resolver.resolve("B"))
    await joins.reached(play)

    prefetch.cancel()
    await _settled(prefetch)
    assert prefetch.cancelled()
    # Nobody cancelled the play: it is still waiting for the stream.
    assert not play.done()

    extraction.release()
    await _settled(play)

    assert not play.cancelled()
    info = play.result()
    assert isinstance(info, StreamInfo)
    assert info.video_id == "B"
    # The play's own resolve is cached and leaves no pending entry behind.
    assert resolver._get_cached("B") is info
    assert resolver._pending == {}
    # The cancelled owner's extraction, then the play's own.
    assert extraction.calls == ["B", "B"]


async def test_every_waiter_survives_and_they_share_one_new_resolve(extraction):
    resolver = StreamResolver()
    joins = _Joins(resolver)
    prefetch = asyncio.create_task(resolver.prefetch("B"))
    await extraction.entered("B")
    first = asyncio.create_task(resolver.resolve("B"))
    second = asyncio.create_task(resolver.resolve("B"))
    await joins.reached(first)
    await joins.reached(second)

    prefetch.cancel()
    await _settled(prefetch)
    extraction.release()
    await _settled(first, second)

    assert not first.cancelled() and not second.cancelled()
    assert first.result() is second.result()
    assert first.result().video_id == "B"
    assert extraction.calls == ["B", "B"]


async def test_the_play_joins_the_prefetch_that_replaced_the_cancelled_one(extraction):
    """What an exclusive prefetch worker does: cancel the old one, start anew."""
    resolver = StreamResolver()
    joins = _Joins(resolver)
    old = asyncio.create_task(resolver.prefetch("B"))
    await extraction.entered("B")
    play = asyncio.create_task(resolver.resolve("B"))
    await joins.reached(play)

    old.cancel()
    new = asyncio.create_task(resolver.prefetch("B"))
    await _settled(old)
    # The play now waits on the new prefetch's resolve, which that one owns.
    await joins.reached(play, 2)
    assert not new.done()
    extraction.release()
    await _settled(play, new)

    assert not play.cancelled()
    assert play.result().video_id == "B"
    assert extraction.calls == ["B", "B"]


# Python's default recursion limit is 1000: a retry that cost a stack frame
# per cancelled owner raised RecursionError in the waiter just below that.
_REPLACED_OWNERS = 1500


async def test_a_waiter_outlives_any_number_of_cancelled_owners(extraction):
    resolver = StreamResolver()
    joins = _Joins(resolver)
    owner = asyncio.create_task(resolver.prefetch("B"))
    await extraction.entered("B")
    waiter = asyncio.create_task(resolver.resolve("B"))
    await joins.reached(waiter)

    for replaced in range(1, _REPLACED_OWNERS + 1):
        if waiter.done():
            break
        # The waiter waits on a resolve this prefetch owns: replace it.
        assert not owner.done()
        owner.cancel()
        owner = asyncio.create_task(resolver.prefetch("B"))
        await joins.reached(waiter, replaced + 1)

    extraction.release()
    await _settled(waiter, owner)

    assert not waiter.cancelled()
    info = waiter.result()
    assert isinstance(info, StreamInfo)
    assert info.video_id == "B"
    assert joins.count(waiter) == _REPLACED_OWNERS + 1
    assert resolver._pending == {}


async def test_a_waiter_that_is_cancelled_itself_is_still_cancelled(extraction):
    """Guard for the fix: only the owner's cancellation is absorbed."""
    resolver = StreamResolver()
    joins = _Joins(resolver)
    owner = asyncio.create_task(resolver.resolve("B"))
    await extraction.entered("B")
    waiter = asyncio.create_task(resolver.resolve("B"))
    await joins.reached(waiter)

    waiter.cancel()
    await _settled(waiter)
    assert waiter.cancelled()

    extraction.release()
    await _settled(owner)
    assert owner.result().video_id == "B"
    assert extraction.calls == ["B"]


async def test_a_waiter_cancelled_while_it_resolves_again_is_still_cancelled(extraction):
    """Guard for the fix: the retry is an ordinary, cancellable resolve."""
    resolver = StreamResolver()
    joins = _Joins(resolver)
    prefetch = asyncio.create_task(resolver.prefetch("B"))
    await extraction.entered("B")
    waiter = asyncio.create_task(resolver.resolve("B"))
    await joins.reached(waiter)
    prefetch.cancel()
    await _settled(prefetch)

    waiter.cancel()
    await _settled(waiter)

    assert waiter.cancelled()
    assert resolver._pending == {}


# ── The app: keyboard Next waiting on a prefetch that gets replaced ─────


def _track(video_id: str) -> dict:
    return {
        "video_id": video_id,
        "title": video_id,
        "artist": "",
        "artists": [],
        "album": "",
        "album_id": None,
        "duration": 200,
        "thumbnail_url": None,
        "is_video": False,
    }


class _PlaybackApp(PlaybackMixin, KeyHandlingMixin, App[None]):
    """The real playback and key mixins over a real Player (fake mpv
    handle), a real StreamResolver (fake extraction) and a real queue."""

    def __init__(self, player: Player, resolver: StreamResolver) -> None:
        super().__init__()
        self.player = player
        self.stream_resolver = resolver
        self.queue = QueueManager()
        self.queue.add_multiple([_track("A"), _track("B"), _track("C")])
        self.queue.jump_to(0)
        self.settings = MagicMock()
        self.settings.notifications.enabled = False
        self.settings.playback.autoplay = False
        self.keymap = KeyMap()
        self.keymap._load_defaults()
        self._key_buffer = []
        self._count_buffer = ""
        self.history = None
        self.cache = None
        self.discord = None
        self.lastfm = None
        self.mpris = None
        self.mac_media = None
        self.ytmusic = None
        self._last_play_video_id = ""
        self._last_play_time = 0.0
        self._consecutive_failures = 0
        self._track_start_position = 0.0
        self._pending_resume_video_id = None
        self._pending_resume_position = 0.0
        self._loaded_resume = None
        self._play_generation = 0
        self._recovery_generation = None
        self._handled_error_attempt = 0
        self._local_history_claim = None
        self._play_lock = asyncio.Lock()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def on_mount(self) -> None:
        # As YTMPlayerApp.on_mount wires the Player.
        self.player.set_event_loop(asyncio.get_running_loop())
        self.player.on(PlayerEvent.TRACK_CHANGE, self._on_track_change)

    def _get_current_page(self) -> None:
        return None

    def _refresh_queue_page(self) -> None:
        pass


async def test_next_key_survives_the_prefetch_it_waits_on_being_replaced(extraction, monkeypatch):
    monkeypatch.setattr(Player, "_instance", None)
    native = MagicMock()
    native.command.return_value = {"playlist_entry_id": 10}
    native.pause = False
    native.time_pos = 0.0
    native.duration = 200.0
    monkeypatch.setattr("ytm_player.services.player.mpv.MPV", lambda **kwargs: native)
    player = Player()
    player.mute = AsyncMock()  # type: ignore[method-assign]
    resolver = StreamResolver()

    # Observe (not replace) the resolve the key handler's play_track awaits.
    real_resolve = resolver.resolve
    waiting, settled = asyncio.Event(), asyncio.Event()
    outcomes: list[object] = []

    async def resolve(video_id: str) -> StreamInfo | None:
        if video_id != "B":
            return await real_resolve(video_id)
        waiting.set()
        try:
            info = await real_resolve(video_id)
        except BaseException as exc:
            outcomes.append(exc)
            raise
        else:
            outcomes.append(info)
            return info
        finally:
            settled.set()

    resolver.resolve = resolve  # type: ignore[method-assign]

    app = _PlaybackApp(player, resolver)
    async with app.run_test() as pilot:
        # A starts; its track change prefetches B, which stays in flight.
        await app.play_track(_track("A"))
        await extraction.entered("B")
        (old_prefetch,) = [worker for worker in app.workers if worker.group == "prefetch"]

        # Keyboard Next: the App's own message loop now waits on that prefetch.
        app.post_message(Key("n", "n"))
        await asyncio.wait_for(waiting.wait(), 3)

        # Another play arrives outside the key handler (a media key's
        # Previous). A's URL is cached, so it loads at once, and its track
        # change replaces the running prefetch with a new exclusive one.
        await app._play_previous()
        with pytest.raises(WorkerCancelled):
            await asyncio.wait_for(old_prefetch.wait(), 3)

        extraction.release()
        await asyncio.wait_for(settled.wait(), 3)

        # The waiting play was superseded, not cancelled: it got the stream
        # (and then stood down), and the App's message loop is still there.
        assert [type(outcome) for outcome in outcomes] == [StreamInfo]
        assert app.is_running
        assert player.current_track is not None
        assert player.current_track["video_id"] == "A"
        await pilot.press("_")
        await pilot.pause()
        player.mute.assert_awaited_once()
