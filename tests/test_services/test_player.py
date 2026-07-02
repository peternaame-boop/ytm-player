"""Concurrency and recovery tests for Player.

The Player runs on the main thread but mpv invokes end-file/time-pos
callbacks on its own thread, which dispatches through Player._dispatch.
The shared state (_current_track, _end_file_skip) MUST be mutated only
under _skip_lock — otherwise mpv's callback can read a half-updated
state and miscount _end_file_skip, swallowing legitimate end-of-track
events (the "auto-advance randomly stops" class of bug).
"""

from __future__ import annotations

import asyncio
import logging
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest


@pytest.fixture
def player(monkeypatch):
    """Construct a Player with mpv fully mocked, reset singleton on teardown."""
    from ytm_player.services.player import Player

    # Reset the singleton so each test gets a fresh instance.
    Player._instance = None

    mock_mpv_instance = MagicMock()
    mock_mpv_instance.volume = 80
    mock_mpv_instance.pause = False
    mock_mpv_class = MagicMock(return_value=mock_mpv_instance)

    # Patch the mpv proxy on the player module — works whether mpv is the
    # real python-mpv module or the stub substituted when libmpv is absent.
    monkeypatch.setattr("ytm_player.services.player.mpv.MPV", mock_mpv_class)

    p = Player()
    p._mpv = mock_mpv_instance  # ensure tests use the mock
    yield p
    Player._instance = None


class TestPlayCurrentTrackLocking:
    """C1: play()/stop() must clear _current_track under _skip_lock."""

    async def test_play_error_path_clears_current_track_under_lock(self, player):
        """If _play_sync raises, _current_track must be cleared atomically.

        The bug: clearing _current_track outside _skip_lock races with
        mpv's end-file callback, which reads _current_track to decide
        whether to dispatch TRACK_END.
        """
        from ytm_player.services.player import PlayerEvent

        # Pretend a previous track is still loaded.
        player._current_track = {"video_id": "prev", "title": "Prev"}

        # Track lock acquisitions during the failure path.
        original_lock = player._skip_lock
        lock_acquisitions: list[bool] = []

        class TrackingLock:
            def __enter__(self):
                lock_acquisitions.append(True)
                original_lock.__enter__()
                return self

            def __exit__(self, *args):
                original_lock.__exit__(*args)

        player._skip_lock = TrackingLock()

        def fail_sync(url):
            raise RuntimeError("simulated mpv failure")

        # Subscribe to ERROR events to confirm dispatch happens.
        errors: list = []
        player.on(PlayerEvent.ERROR, lambda exc: errors.append(exc))

        with patch.object(player, "_play_sync", side_effect=fail_sync):
            await player.play("http://stream", {"video_id": "new", "title": "New"})

        # After failure: _current_track should be cleared.
        assert player._current_track is None, "play() error path must clear _current_track"
        # Lock should have been acquired at least twice (once for the play
        # setup, once for the cleanup).
        assert len(lock_acquisitions) >= 2, (
            f"expected >=2 lock acquisitions (setup + error cleanup), got {len(lock_acquisitions)}"
        )
        # ERROR event should have fired.
        assert len(errors) == 1


class TestTryRecoverState:
    """Regression: _try_recover() must NOT clear _current_track.

    _try_recover is only ever called from within play(), which has already
    set _current_track to the new track being started. Clearing it breaks
    MPRIS, Discord, Last.fm, and the _on_end_file guard for the recovered
    track — auto-advance silently breaks until the next manual play.
    """

    def test_try_recover_preserves_current_track(self, player):
        """Regression: _try_recover must NOT clear _current_track.

        It's only called from play() which has already set _current_track
        to the new track we're starting. Clearing it would break MPRIS,
        Discord, and the _on_end_file guard for the recovered track.
        """
        player._current_track = {"video_id": "abc", "title": "X"}
        player._end_file_skip = 7  # arbitrary leftover

        new_mock_mpv = MagicMock()
        new_mock_mpv.volume = 80
        with patch.object(player, "_init_mpv", return_value=new_mock_mpv):
            ok = player._try_recover()

        assert ok is True
        assert player._current_track == {"video_id": "abc", "title": "X"}, (
            "_try_recover must NOT clear _current_track"
        )
        assert player._end_file_skip == 0

    async def test_play_with_recovery_keeps_current_track_set(self, player):
        """End-to-end: play() → _play_sync raises ShutdownError → _try_recover
        succeeds → second mpv.play() succeeds → _current_track is the new track,
        NOT None.
        """
        # Use the mpv proxy from services.player so this test works whether
        # libmpv is genuinely importable or replaced with the stub.
        from ytm_player.services.player import mpv as _mpv

        # First mpv.play() raises ShutdownError; second succeeds.
        player._mpv.play = MagicMock(side_effect=[_mpv.ShutdownError("simulated crash"), None])
        player._mpv.pause = False

        # _try_recover replaces _mpv with a fresh instance.
        new_mpv = MagicMock()
        new_mpv.play = MagicMock(return_value=None)
        new_mpv.pause = False
        player._init_mpv = MagicMock(return_value=new_mpv)
        player._loop = None  # Skip volume restore branch.

        track = {"video_id": "abc", "title": "X"}
        await player.play("http://example.com/stream", track)

        assert player._current_track == track, (
            "After successful recovery, _current_track must reflect the new track"
        )


class TestPlayerMpvLogHandler:
    """The Player must construct mpv.MPV with log_handler + loglevel='warn'.

    Without these kwargs, libmpv's internal warnings and errors vanish
    silently. Routing them through our Python logger puts them in
    ytm.log so ytm doctor's 'Recent mpv warnings/errors' section can
    surface them.
    """

    def test_mpv_constructed_with_log_handler_and_warn_loglevel(self, monkeypatch):
        """Verify _init_mpv passes log_handler=callable + loglevel='warn'."""
        from unittest.mock import MagicMock

        from ytm_player.services.player import Player

        captured: dict[str, dict[str, object]] = {}

        def fake_mpv_ctor(*_args: object, **kwargs: object) -> MagicMock:
            captured["kwargs"] = kwargs
            instance = MagicMock()
            instance.volume = 80
            instance.pause = False
            return instance

        monkeypatch.setattr("ytm_player.services.player.mpv.MPV", fake_mpv_ctor)

        Player._instance = None
        try:
            Player()
        finally:
            Player._instance = None

        kwargs = captured.get("kwargs") or {}
        assert "log_handler" in kwargs, (
            f"log_handler kwarg missing from mpv.MPV(...): got {sorted(kwargs)!r}"
        )
        assert callable(kwargs["log_handler"]), "log_handler must be callable"
        assert kwargs.get("loglevel") == "warn", (
            f"expected loglevel='warn', got {kwargs.get('loglevel')!r}"
        )

    def test_mpv_log_handler_routes_to_python_logger(self, monkeypatch, caplog):
        """The log_handler callback must emit through Python logging at the
        right level for each mpv level string."""
        from unittest.mock import MagicMock

        from ytm_player.services.player import Player

        captured_handler: dict[str, object] = {}

        def fake_mpv_ctor(*_args: object, **kwargs: object) -> MagicMock:
            captured_handler["handler"] = kwargs.get("log_handler")
            instance = MagicMock()
            instance.volume = 80
            instance.pause = False
            return instance

        monkeypatch.setattr("ytm_player.services.player.mpv.MPV", fake_mpv_ctor)

        Player._instance = None
        try:
            Player()
        finally:
            Player._instance = None

        handler = captured_handler.get("handler")
        assert callable(handler)

        with caplog.at_level("DEBUG", logger="ytm_player.services.player"):
            handler("warn", "ao", "format mismatch detected")
            handler("error", "file", "cannot open")
            handler("info", "demuxer", "stream opened")

        # Each mpv level should appear with the mpv[prefix] format.
        records = [r for r in caplog.records if r.name == "ytm_player.services.player"]
        messages = [r.getMessage() for r in records]
        assert any("mpv[ao]:" in m and "format mismatch" in m for m in messages), (
            f"warn message not routed: {messages!r}"
        )
        assert any("mpv[file]:" in m and "cannot open" in m for m in messages), (
            f"error message not routed: {messages!r}"
        )
        assert any("mpv[demuxer]:" in m and "stream opened" in m for m in messages), (
            f"info message not routed: {messages!r}"
        )


class TestSeekDispatch:
    """T10: seek/seek_absolute must dispatch PlayerEvent.SEEK with the
    requested target, clamped to [0, duration]."""

    async def test_seek_relative_dispatches_target(self, player):
        from ytm_player.services.player import PlayerEvent

        player._mpv.time_pos = 30.0
        player._mpv.duration = 300.0
        received: list[float] = []
        player.on(PlayerEvent.SEEK, received.append)

        await player.seek(15.0)

        assert received == [45.0]
        player._mpv.seek.assert_called_once_with(15.0, reference="relative")

    async def test_seek_relative_clamps_below_zero(self, player):
        from ytm_player.services.player import PlayerEvent

        player._mpv.time_pos = 5.0
        player._mpv.duration = 300.0
        received: list[float] = []
        player.on(PlayerEvent.SEEK, received.append)

        await player.seek(-30.0)

        assert received == [0.0]

    async def test_seek_relative_clamps_to_duration(self, player):
        from ytm_player.services.player import PlayerEvent

        player._mpv.time_pos = 290.0
        player._mpv.duration = 300.0
        received: list[float] = []
        player.on(PlayerEvent.SEEK, received.append)

        await player.seek(60.0)

        assert received == [300.0]

    async def test_seek_absolute_dispatches_target(self, player):
        from ytm_player.services.player import PlayerEvent

        player._mpv.duration = 300.0
        received: list[float] = []
        player.on(PlayerEvent.SEEK, received.append)

        await player.seek_absolute(90.0)

        assert received == [90.0]
        player._mpv.seek.assert_called_once_with(90.0, reference="absolute")

    async def test_seek_absolute_unknown_duration_does_not_clamp(self, player):
        from ytm_player.services.player import PlayerEvent

        player._mpv.duration = None  # duration unknown → 0.0 → no upper clamp
        received: list[float] = []
        player.on(PlayerEvent.SEEK, received.append)

        await player.seek_absolute(90.0)

        assert received == [90.0]

    async def test_seek_on_dead_mpv_does_not_dispatch(self, player):
        from ytm_player.services.player import PlayerEvent
        from ytm_player.services.player import mpv as _mpv

        received: list[float] = []
        player.on(PlayerEvent.SEEK, received.append)
        player._mpv.seek.side_effect = _mpv.ShutdownError("dead")

        await player.seek_absolute(10.0)

        assert received == []


# ── Shared helpers for the transport / dispatch / end-file tests ────────


class _DeadMpv:
    """mpv stand-in whose every attribute get/set raises ShutdownError.

    Simulates an mpv instance that has shut down (crashed / terminated).
    Every access — reading ``pause``, assigning ``volume``, calling
    ``stop()`` — raises, exercising the graceful-degrade guards.
    """

    def __init__(self, exc: BaseException) -> None:
        object.__setattr__(self, "_exc", exc)

    def __getattr__(self, name: str):
        raise object.__getattribute__(self, "_exc")

    def __setattr__(self, name: str, value) -> None:
        raise object.__getattribute__(self, "_exc")


def _dead_mpv() -> _DeadMpv:
    """A _DeadMpv that raises the module's real ShutdownError type."""
    from ytm_player.services.player import mpv as _mpv

    return _DeadMpv(_mpv.ShutdownError("mpv is dead"))


def _end_file_event(reason: int | None) -> SimpleNamespace:
    """Build a fake mpv end-file event with ``event.data.reason``."""
    return SimpleNamespace(data=SimpleNamespace(reason=reason))


@pytest.fixture
def player_with_end_file(monkeypatch):
    """A Player plus the real ``_on_end_file`` closure registered by _init_mpv.

    ``event_callback`` is replaced with a decorator that returns the wrapped
    function unchanged and records it, so the test can invoke the true
    ``_on_end_file`` closure (which closes over the Player's live state).
    """
    from ytm_player.services.player import Player

    Player._instance = None

    captured: dict[str, object] = {}

    mock_mpv_instance = MagicMock()
    mock_mpv_instance.volume = 80
    mock_mpv_instance.pause = False

    def event_callback(name: str):
        def decorator(fn):
            captured[name] = fn
            return fn

        return decorator

    mock_mpv_instance.event_callback = event_callback
    monkeypatch.setattr(
        "ytm_player.services.player.mpv.MPV",
        MagicMock(return_value=mock_mpv_instance),
    )

    p = Player()
    p._mpv = mock_mpv_instance
    yield p, captured["end-file"]
    Player._instance = None


# ── Transport operations (healthy mpv) ─────────────────────────────────


class TestTransportOps:
    """Direct behaviour of pause/resume/stop/seek/volume/mute on a live mpv."""

    async def test_pause_sets_mpv_pause_true(self, player):
        player._mpv.pause = False
        await player.pause()
        assert player._mpv.pause is True

    async def test_resume_sets_mpv_pause_false(self, player):
        player._mpv.pause = True
        await player.resume()
        assert player._mpv.pause is False

    async def test_toggle_pause_flips_state(self, player):
        player._mpv.pause = False
        await player.toggle_pause()
        assert player._mpv.pause is True
        await player.toggle_pause()
        assert player._mpv.pause is False

    async def test_stop_clears_track_and_increments_skip(self, player):
        player._current_track = {"video_id": "abc", "title": "X"}
        player._end_file_skip = 0
        await player.stop()
        assert player._current_track is None
        assert player._end_file_skip == 1, "stop() must arm the skip for mpv's end-file"
        player._mpv.stop.assert_called_once()

    async def test_stop_when_idle_does_not_arm_skip(self, player):
        player._current_track = None
        player._end_file_skip = 0
        await player.stop()
        assert player._end_file_skip == 0
        player._mpv.stop.assert_called_once()

    async def test_seek_relative(self, player):
        await player.seek(10.0)
        player._mpv.seek.assert_called_once_with(10.0, reference="relative")

    async def test_seek_absolute(self, player):
        await player.seek_absolute(30.0)
        player._mpv.seek.assert_called_once_with(30.0, reference="absolute")

    async def test_seek_start_seeks_to_zero_absolute(self, player):
        await player.seek_start()
        player._mpv.seek.assert_called_once_with(0.0, reference="absolute")

    async def test_set_volume_clamps_high_and_dispatches(self, player):
        from ytm_player.services.player import PlayerEvent

        vols: list[int] = []
        player.on(PlayerEvent.VOLUME_CHANGE, lambda v: vols.append(v))
        await player.set_volume(150)
        assert player._mpv.volume == 100
        assert vols == [100]

    async def test_set_volume_clamps_low(self, player):
        await player.set_volume(-10)
        assert player._mpv.volume == 0

    async def test_change_volume_relative(self, player):
        player._mpv.volume = 50
        await player.change_volume(10)
        assert player._mpv.volume == 60

    async def test_mute_toggles(self, player):
        player._mpv.mute = False
        await player.mute()
        assert player._mpv.mute is True
        await player.mute()
        assert player._mpv.mute is False


# ── _dispatch: mpv-thread → asyncio bridge ─────────────────────────────


class TestDispatchBridge:
    """The _dispatch bridge from mpv's callback thread to the event loop."""

    async def test_sync_callback_runs_inline_without_loop(self, player):
        from ytm_player.services.player import PlayerEvent

        received: list = []
        player._loop = None
        player.on(PlayerEvent.VOLUME_CHANGE, lambda v: received.append(v))
        player._dispatch(PlayerEvent.VOLUME_CHANGE, 42)
        assert received == [42], "sync callbacks must run inline when no loop is set"

    async def test_async_callback_dropped_without_loop(self, player, caplog):
        from ytm_player.services.player import PlayerEvent

        called: list = []

        async def acb(v):
            called.append(v)

        player._loop = None
        player.on(PlayerEvent.TRACK_END, acb)
        with caplog.at_level(logging.WARNING, logger="ytm_player.services.player"):
            player._dispatch(PlayerEvent.TRACK_END, "data")
        assert called == []
        assert any("Dropping async" in r.getMessage() for r in caplog.records), (
            "async callback with no loop should warn, not silently vanish"
        )

    async def test_sync_callback_with_loop_bridges_via_threadsafe(self, player):
        from ytm_player.services.player import PlayerEvent

        received: list = []
        player.set_event_loop(asyncio.get_running_loop())
        player.on(PlayerEvent.POSITION_CHANGE, lambda v: received.append(v))
        player._dispatch(PlayerEvent.POSITION_CHANGE, 12.5)
        # call_soon_threadsafe defers to the next loop iteration.
        assert received == []
        await asyncio.sleep(0)
        assert received == [12.5]

    async def test_async_callback_with_loop_scheduled(self, player):
        from ytm_player.services.player import PlayerEvent

        received: list = []

        async def acb(v):
            received.append(v)

        player.set_event_loop(asyncio.get_running_loop())
        player.on(PlayerEvent.TRACK_END, acb)
        player._dispatch(PlayerEvent.TRACK_END, {"x": 1})
        await asyncio.sleep(0.05)
        assert received == [{"x": 1}]

    async def test_callback_exception_is_isolated(self, player, caplog):
        from ytm_player.services.player import PlayerEvent

        good: list = []

        def bad(_v):
            raise RuntimeError("callback boom")

        player._loop = None
        player.on(PlayerEvent.POSITION_CHANGE, bad)
        player.on(PlayerEvent.POSITION_CHANGE, lambda v: good.append(v))
        with caplog.at_level(logging.ERROR, logger="ytm_player.services.player"):
            player._dispatch(PlayerEvent.POSITION_CHANGE, 1.0)  # must not raise
        assert good == [1.0], "a raising callback must not stop later callbacks"
        assert any("Failed to schedule" in r.getMessage() for r in caplog.records)


# ── _on_end_file: _end_file_skip counting (the critical invariant) ─────


class TestEndFileSkipCounting:
    """_on_end_file's skip-counting — the file's documented race invariant."""

    def test_skip_swallows_one_event_without_dispatch(self, player_with_end_file):
        player, on_end_file = player_with_end_file
        player._end_file_skip = 1
        player._current_track = {"video_id": "abc"}
        player._dispatch = MagicMock()

        on_end_file(_end_file_event(0))

        assert player._end_file_skip == 0, "skip must decrement"
        assert player._current_track == {"video_id": "abc"}, "swallowed event keeps track"
        player._dispatch.assert_not_called()

    def test_eof_dispatches_track_end_and_clears_track(self, player_with_end_file):
        from ytm_player.services.player import PlayerEvent

        player, on_end_file = player_with_end_file
        player._end_file_skip = 0
        track = {"video_id": "abc"}
        player._current_track = track
        player._dispatch = MagicMock()

        on_end_file(_end_file_event(0))

        assert player._current_track is None
        player._dispatch.assert_called_once_with(
            PlayerEvent.TRACK_END, {"reason": 0, "track": track}
        )

    def test_none_reason_treated_as_eof(self, player_with_end_file):
        from ytm_player.services.player import PlayerEvent

        player, on_end_file = player_with_end_file
        player._end_file_skip = 0
        track = {"video_id": "abc"}
        player._current_track = track
        player._dispatch = MagicMock()

        on_end_file(_end_file_event(None))

        player._dispatch.assert_called_once_with(
            PlayerEvent.TRACK_END, {"reason": None, "track": track}
        )

    def test_error_reason_dispatches_error(self, player_with_end_file):
        from ytm_player.services.player import PlayerEvent

        player, on_end_file = player_with_end_file
        player._end_file_skip = 0
        player._current_track = {"video_id": "abc"}
        player._dispatch = MagicMock()

        on_end_file(_end_file_event(4))

        assert player._current_track is None
        player._dispatch.assert_called_once_with(PlayerEvent.ERROR, "stream error")

    def test_aborted_reason_clears_track_without_dispatch(self, player_with_end_file):
        player, on_end_file = player_with_end_file
        player._end_file_skip = 0
        player._current_track = {"video_id": "abc"}
        player._dispatch = MagicMock()

        on_end_file(_end_file_event(2))  # 2 = ABORTED (intentional stop)

        assert player._current_track is None
        player._dispatch.assert_not_called()

    def test_idle_end_file_is_ignored(self, player_with_end_file):
        player, on_end_file = player_with_end_file
        player._end_file_skip = 0
        player._current_track = None
        player._dispatch = MagicMock()

        on_end_file(_end_file_event(0))

        player._dispatch.assert_not_called()

    def test_multiple_skips_counted_before_dispatch(self, player_with_end_file):
        from ytm_player.services.player import PlayerEvent

        player, on_end_file = player_with_end_file
        player._end_file_skip = 2
        player._current_track = {"video_id": "abc"}
        player._dispatch = MagicMock()

        on_end_file(_end_file_event(0))  # swallowed → skip 1
        on_end_file(_end_file_event(0))  # swallowed → skip 0
        player._dispatch.assert_not_called()
        assert player._end_file_skip == 0

        on_end_file(_end_file_event(0))  # now a real EOF
        player._dispatch.assert_called_once()
        assert player._dispatch.call_args[0][0] == PlayerEvent.TRACK_END


# ── Bug (a): play() failure must roll back the pre-incremented skip ─────


class TestPlayFailureRollsBackSkip:
    """A failed play() must not leave _end_file_skip inflated."""

    async def test_play_failure_rolls_back_end_file_skip(self, player):
        # A previous track is loaded, so play() pre-increments the skip.
        player._current_track = {"video_id": "prev", "title": "Prev"}
        player._end_file_skip = 0

        with patch.object(player, "_play_sync", side_effect=RuntimeError("boom")):
            await player.play("http://stream", {"video_id": "new", "title": "New"})

        assert player._current_track is None
        assert player._end_file_skip == 0, (
            "failed play must roll back the pre-incremented _end_file_skip; "
            "leaving it at 1 swallows a later legitimate end-file"
        )

    async def test_play_failure_does_not_swallow_later_end_file(self, player_with_end_file):
        from ytm_player.services.player import PlayerEvent

        player, on_end_file = player_with_end_file
        player._current_track = {"video_id": "prev", "title": "Prev"}
        player._end_file_skip = 0

        with patch.object(player, "_play_sync", side_effect=RuntimeError("boom")):
            await player.play("http://stream", {"video_id": "new", "title": "New"})

        assert player._end_file_skip == 0

        # A genuinely new track now ends naturally — TRACK_END must fire.
        player._current_track = {"video_id": "next", "title": "Next"}
        player._dispatch = MagicMock()
        on_end_file(_end_file_event(0))
        player._dispatch.assert_called_once()
        assert player._dispatch.call_args[0][0] == PlayerEvent.TRACK_END

    async def test_play_failure_after_takeover_leaves_newer_state_alone(self, player):
        """If another operation replaced _current_track while _play_sync was
        in flight, the failure cleanup must not steal that operation's skip
        counter or clear its track."""
        player._current_track = {"video_id": "prev", "title": "Prev"}
        player._end_file_skip = 0
        newer = {"video_id": "newer", "title": "Newer"}

        def takeover_then_fail(url):
            player._current_track = newer
            player._end_file_skip = 5
            raise RuntimeError("boom")

        with patch.object(player, "_play_sync", side_effect=takeover_then_fail):
            await player.play("http://stream", {"video_id": "mine", "title": "Mine"})

        assert player._current_track is newer, "failure cleanup clobbered a newer op's track"
        assert player._end_file_skip == 5, "failure cleanup stole a newer op's skip"


# ── Bug (b): transport ops must survive a dead mpv (ShutdownError) ──────


class TestTransportOpsShutdownGuard:
    """pause/resume/toggle_pause/stop/set_volume/mute must not raise on a dead mpv."""

    async def test_pause_swallows_shutdown_error(self, player):
        player._mpv = _dead_mpv()
        await player.pause()  # must not raise

    async def test_resume_swallows_shutdown_error(self, player):
        player._mpv = _dead_mpv()
        await player.resume()  # must not raise

    async def test_toggle_pause_swallows_shutdown_error(self, player):
        player._mpv = _dead_mpv()
        await player.toggle_pause()  # must not raise

    async def test_stop_swallows_shutdown_error_and_clears_track(self, player):
        player._current_track = {"video_id": "abc"}
        player._mpv = _dead_mpv()
        await player.stop()  # must not raise
        assert player._current_track is None, "stop() must still clear state on a dead mpv"

    async def test_set_volume_swallows_shutdown_error_and_skips_dispatch(self, player):
        from ytm_player.services.player import PlayerEvent

        vols: list = []
        player.on(PlayerEvent.VOLUME_CHANGE, lambda v: vols.append(v))
        player._mpv = _dead_mpv()
        await player.set_volume(50)  # must not raise
        assert vols == [], "VOLUME_CHANGE must not fire when the volume set failed"

    async def test_change_volume_swallows_shutdown_error(self, player):
        player._mpv = _dead_mpv()
        await player.change_volume(10)  # must not raise

    async def test_mute_swallows_shutdown_error(self, player):
        player._mpv = _dead_mpv()
        await player.mute()  # must not raise

    async def test_seek_swallows_shutdown_error(self, player):
        # seek already guards ShutdownError — asserted here for contrast/coverage.
        player._mpv = _dead_mpv()
        await player.seek(5.0)  # must not raise
