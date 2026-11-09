"""Seeking with nothing loaded is a quiet no-op (review B1).

mpv rejects ``seek`` (MPV_ERROR_COMMAND, -12) whenever no file is
initialised: nothing loaded yet, the queue ended, or the first moments of
a track whose stream is still opening. python-mpv raises that as a plain
``SystemError``; it escaped the Player and, from the seek keys, ended the
App's message loop. The fake below applies mpv's rule; no real mpv runs.
"""

from __future__ import annotations

import asyncio
import threading
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App

from tests.test_app.test_playback import _fresh_playback_host, _stream_info
from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.config import Action
from ytm_player.config.keymap import KeyMap
from ytm_player.services.player import Player, PlayerEvent


class _Native:
    """What the tests need of an mpv handle, with mpv's seek rule."""

    def __init__(self) -> None:
        self.mock = MagicMock()
        self.mock.command.return_value = {"playlist_entry_id": 10}
        self.mock.pause = False
        self.mock.time_pos = None
        self.mock.duration = None
        self.mock.seek.side_effect = self._seek
        # mpv has a file initialised (it lags behind an accepted loadfile).
        self.file_loaded = False
        self.seeks: list[tuple[float, str]] = []

    def _seek(self, amount: float, reference: str = "relative") -> None:
        if not self.file_loaded:
            # What python-mpv raises for error code -12.
            raise SystemError("Error running mpv command", -12, ())
        self.seeks.append((amount, reference))


@pytest.fixture
def idle(monkeypatch):
    """A real Player over the fake handle, with nothing loaded."""
    monkeypatch.setattr(Player, "_instance", None)
    native = _Native()
    monkeypatch.setattr("ytm_player.services.player.mpv.MPV", lambda **kwargs: native.mock)
    player = Player()
    seeks: list[float] = []
    player.on(PlayerEvent.SEEK, seeks.append)
    return player, native, seeks


_SEEKS = {
    "forward": lambda player: player.seek(5.0),
    "backward": lambda player: player.seek(-5.0),
    "absolute": lambda player: player.seek_absolute(10.0),
    "start": lambda player: player.seek_start(),
}


@pytest.mark.parametrize("kind", list(_SEEKS))
async def test_seek_with_nothing_loaded_is_a_quiet_no_op(idle, kind):
    player, native, seeks = idle

    await _SEEKS[kind](player)  # raised SystemError on master

    native.mock.seek.assert_called_once()
    # Nothing moved, so nobody is told that it did.
    assert seeks == []


@pytest.mark.parametrize("kind", list(_SEEKS))
async def test_seek_while_the_stream_is_still_opening_is_dropped(idle, kind):
    """play() returns once loadfile is accepted; mpv rejects seeks until
    the file is initialised, although the Player already has a track."""
    player, native, seeks = idle
    await player.play("http://stream/a", {"video_id": "a"}, attempt=1)
    assert player.current_track is not None

    await _SEEKS[kind](player)

    assert seeks == []

    native.file_loaded = True
    native.mock.time_pos = 20.0
    native.mock.duration = 200.0
    await _SEEKS[kind](player)

    assert len(native.seeks) == 1
    assert len(seeks) == 1


async def test_seek_during_the_native_load_is_dropped(idle):
    """The load worker is still inside loadfile when the seek arrives."""
    player, native, seeks = idle
    entered, release = threading.Event(), threading.Event()

    def command(*args):
        entered.set()
        assert release.wait(3), "the test never released the native load"
        return {"playlist_entry_id": 10}

    native.mock.command.side_effect = command
    loading = asyncio.create_task(player.play("http://stream/a", {"video_id": "a"}, attempt=1))
    try:
        assert await asyncio.to_thread(entered.wait, 3), "worker never entered the native gate"

        await player.seek(5.0)
        await player.seek_absolute(30.0)

        assert seeks == []
    finally:
        release.set()
        await asyncio.wait_for(loading, 3)
    assert player.current_track == {"video_id": "a"}


async def test_seek_absolute_reports_whether_the_seek_was_applied(idle):
    player, native, _seeks = idle

    assert await player.seek_absolute(10.0) is False

    native.file_loaded = True
    assert await player.seek_absolute(10.0) is True


@pytest.mark.parametrize(
    "action",
    [Action.SEEK_FORWARD, Action.SEEK_BACKWARD, Action.SEEK_START],
    ids=["forward", "backward", "start"],
)
async def test_seek_actions_on_an_idle_player_do_not_raise(idle, action):
    player, native, seeks = idle
    host = MagicMock()
    host.player = player
    host.settings.playback.seek_step = 5

    await KeyHandlingMixin._handle_action(host, action, 1)

    native.mock.seek.assert_called_once()
    assert seeks == []


class _KeysApp(KeyHandlingMixin, App[None]):
    """The real key handler over a bare app and an idle Player."""

    def __init__(self, player: Player) -> None:
        super().__init__()
        self.player = player
        self.settings = MagicMock()
        self.settings.playback.seek_step = 5
        self.keymap = KeyMap()
        self.keymap._load_defaults()
        self._key_buffer = []
        self._count_buffer = ""
        self.muted = AsyncMock()
        player.mute = self.muted  # type: ignore[method-assign]


async def test_seek_keys_with_nothing_loaded_leave_the_app_running(idle):
    """On master the error left on_key and ended the App's message loop."""
    player, native, _seeks = idle
    app = _KeysApp(player)
    async with app.run_test() as pilot:
        await pilot.press(">", "<", "^")
        await pilot.pause()

        assert native.mock.seek.call_count == 3
        assert app.is_running
        # The key handler still answers.
        await pilot.press("_")
        await pilot.pause()
        app.muted.assert_awaited_once()


async def test_a_dropped_resume_seek_is_not_taken_as_a_resumed_position(idle):
    """Guard for the fix itself (passes on master, where the error was
    caught at this call site): a seek the Player drops must not move the
    position that listen time is measured from."""
    player, _native, _seeks = idle
    host = _fresh_playback_host()
    host.player = player
    host.stream_resolver.resolve = AsyncMock(return_value=_stream_info("a"))
    host._pending_resume_video_id = "a"
    host._pending_resume_position = 42.0

    await host.play_track({"video_id": "a", "title": "A"})

    assert player.current_track is not None
    assert host._track_start_position == 0.0
