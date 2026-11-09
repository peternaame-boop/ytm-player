"""Exercise real Player workers with bounded, deliberately reordered native calls."""

import asyncio
import threading
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

from ytm_player.services.player import Player, PlayerEvent


@pytest.fixture
def native_player(monkeypatch):
    monkeypatch.setattr(Player, "_instance", None)
    native = MagicMock()
    native.command.return_value = {"playlist_entry_id": 10}
    monkeypatch.setattr("ytm_player.services.player.mpv.MPV", lambda **kwargs: native)
    return Player(), native


async def entered(event):
    assert await asyncio.to_thread(event.wait, 3), "worker never entered the native gate"


@pytest.mark.parametrize("fail_old", [False, True])
async def test_newer_same_dict_play_owns_state_and_events(native_player, fail_old):
    player, native = native_player
    started, release = threading.Event(), threading.Event()
    calls = []

    def command(name, url):
        calls.append(url)
        if url == "old":
            started.set()
            assert release.wait(3)
            if fail_old:
                raise RuntimeError("late old failure")
            return {"playlist_entry_id": 10}
        return {"playlist_entry_id": 11}

    native.command.side_effect = command
    changes, errors = [], []
    player.on(PlayerEvent.TRACK_CHANGE, changes.append)
    player.on(PlayerEvent.ERROR, errors.append)
    same = {"video_id": "same"}
    old = asyncio.create_task(player.play("old", same, attempt=1))
    newer = None
    try:
        await entered(started)
        newer = asyncio.create_task(player.play("new", same, attempt=2))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(old, newer), 3)
        assert calls == ["old", "new"]
        assert player._current_attempt == 2
        assert player._current_entry_id == 11
        assert changes == [same]
        assert errors == []
    finally:
        release.set()
        await asyncio.gather(old, *([newer] if newer else []), return_exceptions=True)


async def test_cancelled_awaiter_does_not_release_native_gate(native_player):
    player, native = native_player
    started, release = threading.Event(), threading.Event()
    calls = []

    def command(*args):
        started.set()
        assert release.wait(3)
        calls.append("load")
        return {"playlist_entry_id": 10}

    native.command.side_effect = command
    native.stop.side_effect = lambda: calls.append("stop")
    changes = []
    player.on(PlayerEvent.TRACK_CHANGE, changes.append)
    loading = asyncio.create_task(player.play("url", {"video_id": "A"}, attempt=1))
    try:
        await entered(started)
        loading.cancel()
        with pytest.raises(asyncio.CancelledError):
            await loading
        # The event loop is responsive while the native worker is blocked.
        await asyncio.wait_for(asyncio.sleep(0), 1)
        assert calls == []
        release.set()
        await asyncio.wait_for(asyncio.gather(*tuple(player._background_tasks)), 3)
        assert calls == ["load", "stop"]
        assert player.current_track is None
        assert changes == []
    finally:
        release.set()
        await asyncio.gather(loading, *tuple(player._background_tasks), return_exceptions=True)


async def test_stop_superseded_by_new_play_does_not_stop_winner(native_player):
    player, native = native_player
    started, release = threading.Event(), threading.Event()
    calls = []

    def command(name, url):
        if url == "old":
            started.set()
            assert release.wait(3)
        calls.append(url)
        return {"playlist_entry_id": 10 if url == "old" else 11}

    native.command.side_effect = command
    native.stop.side_effect = lambda: calls.append("stop")
    old = asyncio.create_task(player.play("old", {"video_id": "A"}, attempt=1))
    pending = [old]
    try:
        await entered(started)
        pending.append(asyncio.create_task(player.stop()))
        await asyncio.sleep(0)
        pending.append(asyncio.create_task(player.play("new", {"video_id": "B"}, attempt=2)))
        await asyncio.sleep(0)
        release.set()
        await asyncio.wait_for(asyncio.gather(*pending), 3)
        assert calls == ["old", "new"]
        assert player.current_track == {"video_id": "B"}
    finally:
        release.set()
        await asyncio.gather(*pending, return_exceptions=True)


async def test_queued_track_change_is_dropped_after_stop(native_player):
    player, native = native_player
    queued = []
    loop = MagicMock()
    loop.is_closed.return_value = False
    loop.call_soon_threadsafe.side_effect = lambda fn, *args, **kwargs: queued.append((fn, args))
    player.set_event_loop(loop)
    changes = []
    player.on(PlayerEvent.TRACK_CHANGE, changes.append)
    await player.play("url", {"video_id": "A"}, attempt=1)
    assert queued
    await player.stop()
    for callback, args in queued:
        callback(*args)
    assert changes == []


async def test_delayed_end_from_previous_load_cannot_take_same_song_replay(native_player):
    player, native = native_player
    native.command.side_effect = [{"playlist_entry_id": 10}, {"playlist_entry_id": 11}]
    same = {"video_id": "A"}
    await player.play("url", same, attempt=1)
    await player.play("url", same, attempt=2)
    events = []
    player.on(PlayerEvent.TRACK_END, events.append)
    for entry in (10, 10, 11, 11):
        player._handle_end_file(
            native, SimpleNamespace(data=SimpleNamespace(reason=0, playlist_entry_id=entry))
        )
    assert events == [{"reason": 0, "track": same, "attempt": 2}]


async def test_older_mpv_reply_uses_the_owned_playlist_entry(native_player):
    player, native = native_player
    native.command.return_value = None
    native.playlist = [{"id": 42}]
    await player.play("url", {"video_id": "A"}, attempt=7)
    assert player._current_entry_id == 42
    assert player._current_attempt == 7
