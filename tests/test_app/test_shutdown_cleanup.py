"""Shutdown attempts every cleanup step even after one of them fails.

Runs the real ``YTMPlayerApp.on_unmount`` against fake services: no mpv,
IPC socket, database or cache is opened.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock

import pytest

from ytm_player.app._app import YTMPlayerApp

# Today's cleanup order, one name per step.
STEPS = [
    "save",
    "ipc",
    "pid",
    "timer",
    "listen",
    "callbacks",
    "player",
    "resolver",
    "mpris",
    "mediakeys",
    "mac_media",
    "mac_eventtap",
    "discord",
    "history",
    "cache",
]

# Which host attribute carries each optional resource.
RESOURCES = {
    "ipc": "_ipc_server",
    "timer": "_poll_timer",
    "player": "player",
    "resolver": "stream_resolver",
    "mpris": "mpris",
    "mediakeys": "mediakeys",
    "mac_media": "mac_media",
    "mac_eventtap": "mac_eventtap",
    "discord": "discord",
    "history": "history",
    "cache": "cache",
}


def _host(order: list[str], monkeypatch, *, failing: str | None = None) -> MagicMock:
    """A host whose cleanup steps append their name to *order* when they complete.

    *failing* names the one step that raises instead.
    """
    h = MagicMock()

    def sync(name: str):
        def run() -> None:
            if name == failing:
                raise OSError(f"synthetic {name} failure")
            order.append(name)

        return MagicMock(side_effect=run)

    def coro(name: str):
        async def run() -> None:
            if name == failing:
                raise OSError(f"synthetic {name} failure")
            order.append(name)

        return AsyncMock(side_effect=run)

    h._save_session_state = sync("save")
    h._ipc_server.stop = coro("ipc")
    monkeypatch.setattr("ytm_player.app._app.remove_pid", sync("pid"))
    h._poll_timer.stop = sync("timer")
    h._log_current_listen = coro("listen")
    h.player.clear_callbacks = sync("callbacks")
    h.player.shutdown = sync("player")
    h.stream_resolver.clear_cache = sync("resolver")
    h.mpris.stop = coro("mpris")
    h.mediakeys.stop = sync("mediakeys")
    h.mac_media.stop = sync("mac_media")
    h.mac_eventtap.stop = sync("mac_eventtap")
    h.discord.disconnect = coro("discord")
    h.history.close = coro("history")
    h.cache.close = coro("cache")
    return h


async def test_success_runs_every_step_once_in_order(monkeypatch, caplog):
    order: list[str] = []
    h = _host(order, monkeypatch)
    with caplog.at_level(logging.WARNING, logger="ytm_player.app._app"):
        await YTMPlayerApp.on_unmount(h)
    assert order == STEPS
    assert h._ipc_server is None
    assert h._poll_timer is None
    assert caplog.records == []


async def test_absent_resources_are_skipped(monkeypatch):
    order: list[str] = []
    h = _host(order, monkeypatch)
    for attr in RESOURCES.values():
        setattr(h, attr, None)
    await YTMPlayerApp.on_unmount(h)
    assert order == ["save", "pid"]


@pytest.mark.parametrize("failing", ["save", "ipc", "listen", "player", "history"])
async def test_a_failed_step_is_logged_and_the_rest_still_run(failing, monkeypatch, caplog):
    order: list[str] = []
    h = _host(order, monkeypatch, failing=failing)
    with caplog.at_level(logging.ERROR, logger="ytm_player.app._app"):
        await YTMPlayerApp.on_unmount(h)  # does not raise
    assert order == [step for step in STEPS if step != failing]
    (record,) = caplog.records
    assert record.levelno == logging.ERROR
    assert "Shutdown step failed" in record.getMessage()
    assert record.exc_info is not None
    assert isinstance(record.exc_info[1], OSError)
    assert f"synthetic {failing} failure" in str(record.exc_info[1])


async def test_ipc_failure_keeps_player_history_and_cache_cleanup(monkeypatch):
    """The reproduced gap: an IPC stop error used to skip everything after it."""
    order: list[str] = []
    h = _host(order, monkeypatch, failing="ipc")
    ipc = h._ipc_server
    await YTMPlayerApp.on_unmount(h)
    assert {"player", "history", "cache"} <= set(order)
    # The reference is kept: the server did not stop.
    assert h._ipc_server is ipc


async def test_final_listen_failure_still_shuts_the_player_down(monkeypatch):
    order: list[str] = []
    h = _host(order, monkeypatch, failing="listen")
    await YTMPlayerApp.on_unmount(h)
    assert order.index("callbacks") < order.index("player") < order.index("history")


async def test_history_close_failure_still_closes_the_cache(monkeypatch):
    order: list[str] = []
    h = _host(order, monkeypatch, failing="history")
    await YTMPlayerApp.on_unmount(h)
    assert order[-1] == "cache"


async def test_cancellation_at_an_awaited_step_finishes_the_rest_then_propagates(monkeypatch):
    order: list[str] = []
    h = _host(order, monkeypatch)
    entered = asyncio.Event()

    async def stop_forever() -> None:
        entered.set()
        await asyncio.Event().wait()

    h._ipc_server.stop = stop_forever
    ipc = h._ipc_server
    before = {t for t in asyncio.all_tasks() if not t.done()}
    task = asyncio.create_task(YTMPlayerApp.on_unmount(h))
    await entered.wait()
    task.cancel()
    with pytest.raises(asyncio.CancelledError):
        await task
    # Everything after the cancelled step still ran, in order.
    assert order == [step for step in STEPS if step != "ipc"]
    assert h._ipc_server is ipc
    # No detached cleanup task was left behind.
    after = {t for t in asyncio.all_tasks() if not t.done()}
    assert after - before == set()
