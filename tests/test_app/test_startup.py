"""Startup (``on_mount``) boundary tests with fake services.

The service classes are patched at the module level; the host is a
MagicMock whose awaited attributes are AsyncMocks. These pin the two
degraded-start paths — YouTube Music unreachable, history.db unreadable —
and the two exits that must stay exits.
"""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock, patch

import pytest
import requests

from ytm_player.app._app import YTMPlayerApp
from ytm_player.config import paths


def _host() -> MagicMock:
    host = MagicMock()
    host.settings.mpris.enabled = False
    host.settings.discord.enabled = False
    host.settings.lastfm.enabled = False
    host.settings.general.startup_page = "library"
    host._first_run_hint_shown = True
    host._restore_session_state = AsyncMock()
    host.navigate_to = AsyncMock()
    return host


def _auth(validate: object = True) -> MagicMock:
    auth = MagicMock()
    auth.is_authenticated.return_value = True
    if isinstance(validate, BaseException):
        auth.validate.side_effect = validate
    else:
        auth.validate.return_value = validate
    auth.try_auto_refresh.return_value = False
    return auth


async def _mount(host: MagicMock, auth: MagicMock, **overrides: object) -> SimpleNamespace:
    """Run the real ``on_mount`` against *host*; returns the patched service classes."""
    loop = asyncio.get_running_loop()
    previous = loop.get_exception_handler()
    with (
        patch("ytm_player.app._app.AuthManager", return_value=auth),
        patch("ytm_player.config.paths.ensure_dirs"),
        patch("ytm_player.app._app.Player") as player,
        patch("ytm_player.app._app.YTMusicService"),
        patch("ytm_player.app._app.StreamResolver"),
        patch("ytm_player.app._app.HistoryManager") as history,
        patch("ytm_player.app._app.CacheManager") as cache,
        patch("ytm_player.app._app.IPCServer") as ipc,
    ):
        ipc.return_value.start = AsyncMock()
        history.return_value.init = overrides.get("history_init", AsyncMock())
        cache.return_value.init = overrides.get("cache_init", AsyncMock())
        try:
            await YTMPlayerApp.on_mount(host)
        finally:
            loop.set_exception_handler(previous)
    return SimpleNamespace(player=player, history=history, cache=cache)


def _notices(host: MagicMock) -> list[str]:
    return [str(c.args[0]) for c in host.notify.call_args_list if c.args]


def _scheduled_exit(host: MagicMock) -> bool:
    return any(len(c.args) > 1 and c.args[1] is host.exit for c in host.set_timer.call_args_list)


class TestUnreachableService:
    @pytest.mark.parametrize(
        "error",
        [requests.exceptions.ConnectionError, requests.exceptions.Timeout],
        ids=["connection-error", "timeout"],
    )
    async def test_network_error_during_validation_starts_without_validation(self, error):
        host = _host()
        auth = _auth(error("synthetic offline"))

        services = await _mount(host, auth)

        assert services.player.called, "Player must still be constructed"
        auth.try_auto_refresh.assert_not_called()
        assert any("couldn't be reached" in n for n in _notices(host))
        host._restore_session_state.assert_awaited_once()
        host.navigate_to.assert_awaited_once_with("library")
        assert not _scheduled_exit(host)

    async def test_expired_session_branch_is_unchanged(self):
        host = _host()
        auth = _auth(validate=False)

        await _mount(host, auth)

        auth.try_auto_refresh.assert_called_once()
        assert any("Run `ytm setup`" in n for n in _notices(host))
        host._restore_session_state.assert_awaited_once()
        assert not _scheduled_exit(host)


class TestHistoryInitFailure:
    async def test_unreadable_history_db_disables_history_and_continues(self):
        host = _host()
        failing_init = AsyncMock(
            side_effect=RuntimeError("Failed to open history database: file is not a database")
        )

        services = await _mount(host, _auth(), history_init=failing_init)

        assert host.history is None
        services.cache.return_value.init.assert_awaited_once()
        host._restore_session_state.assert_awaited_once()
        notice = next(n for n in _notices(host) if "Play history is unavailable" in n)
        assert str(paths.HISTORY_DB) in notice
        assert "not deleted or replaced" in notice
        assert not _scheduled_exit(host)

    async def test_other_service_failure_still_exits(self):
        host = _host()
        failing_cache = AsyncMock(side_effect=RuntimeError("disk"))

        await _mount(host, _auth(), cache_init=failing_cache)

        assert any("Could not start player services" in n for n in _notices(host))
        assert _scheduled_exit(host)
        host._restore_session_state.assert_not_awaited()
