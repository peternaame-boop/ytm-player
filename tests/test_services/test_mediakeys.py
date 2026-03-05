"""Tests for ytm_player.services.mediakeys.

pynput is optional and may not be installed in CI.  All tests mock the module
internals so they never require a real keyboard listener or display server.
"""

from __future__ import annotations

import asyncio
import logging
from unittest.mock import AsyncMock, MagicMock, patch

import pytest  # noqa: F401 (used by pytest.fixture)

from ytm_player.services.mediakeys import MediaKeysService

# ── Helpers ─────────────────────────────────────────────────────────


# Sentinel objects standing in for pynput Key enum members.
_PLAY_PAUSE = object()
_NEXT = object()
_PREVIOUS = object()
_OTHER = object()

_TEST_KEY_MAP = {
    _PLAY_PAUSE: "play_pause",
    _NEXT: "next",
    _PREVIOUS: "previous",
}


def _make_callbacks() -> dict[str, AsyncMock]:
    return {
        "play_pause": AsyncMock(),
        "next": AsyncMock(),
        "previous": AsyncMock(),
    }


async def _started_service(callbacks=None, loop=None):
    """Create and start a MediaKeysService with mocked pynput."""
    svc = MediaKeysService()
    if callbacks is None:
        callbacks = _make_callbacks()
    if loop is None:
        loop = asyncio.get_running_loop()

    mock_listener_cls = MagicMock()
    mock_listener_inst = MagicMock()
    mock_listener_cls.return_value = mock_listener_inst

    with (
        patch("ytm_player.services.mediakeys._PYNPUT_AVAILABLE", True),
        patch("ytm_player.services.mediakeys.Listener", mock_listener_cls, create=True),
    ):
        await svc.start(callbacks, loop)

    return svc, callbacks, mock_listener_inst


# ── Start (pynput unavailable) ─────────────────────────────────────


class TestStartWithoutPynput:
    async def test_logs_debug_message(self, caplog):
        svc = MediaKeysService()
        with (
            patch("ytm_player.services.mediakeys._PYNPUT_AVAILABLE", False),
            caplog.at_level(logging.DEBUG),
        ):
            await svc.start({}, asyncio.get_running_loop())
        assert "pynput is not installed" in caplog.text

    async def test_not_running(self):
        svc = MediaKeysService()
        with patch("ytm_player.services.mediakeys._PYNPUT_AVAILABLE", False):
            await svc.start({}, asyncio.get_running_loop())
        assert not svc._running

    async def test_no_listener(self):
        svc = MediaKeysService()
        with patch("ytm_player.services.mediakeys._PYNPUT_AVAILABLE", False):
            await svc.start({}, asyncio.get_running_loop())
        assert svc._listener is None


# ── Start (pynput available) ──────────────────────────────────────


class TestStartWithPynput:
    async def test_running(self):
        svc, _, _ = await _started_service()
        assert svc._running is True

    async def test_listener_started(self):
        svc, _, listener = await _started_service()
        listener.start.assert_called_once()

    async def test_listener_is_daemon(self):
        svc, _, listener = await _started_service()
        assert listener.daemon is True

    async def test_callbacks_stored(self):
        cbs = _make_callbacks()
        svc, _, _ = await _started_service(callbacks=cbs)
        assert svc._callbacks is cbs

    async def test_loop_stored(self):
        loop = asyncio.get_running_loop()
        svc, _, _ = await _started_service(loop=loop)
        assert svc._loop is loop


# ── Stop ──────────────────────────────────────────────────────────


class TestStop:
    async def test_stops_listener(self):
        svc, _, listener = await _started_service()
        svc.stop()
        listener.stop.assert_called_once()

    async def test_clears_listener_ref(self):
        svc, _, _ = await _started_service()
        svc.stop()
        assert svc._listener is None

    async def test_sets_not_running(self):
        svc, _, _ = await _started_service()
        svc.stop()
        assert svc._running is False

    async def test_double_stop_no_error(self):
        svc, _, _ = await _started_service()
        svc.stop()
        svc.stop()
        assert svc._running is False

    async def test_stop_before_start(self):
        svc = MediaKeysService()
        svc.stop()
        assert svc._running is False


# ── _on_press ─────────────────────────────────────────────────────


class TestOnPress:
    @pytest.fixture()
    def service(self):
        svc = MediaKeysService()
        svc._running = True
        svc._loop = MagicMock()
        svc._callbacks = _make_callbacks()
        return svc

    def test_play_pause_dispatches(self, service):
        with patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP):
            service._on_press(_PLAY_PAUSE)
        service._loop.call_soon_threadsafe.assert_called_once()

    def test_next_dispatches(self, service):
        with patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP):
            service._on_press(_NEXT)
        service._loop.call_soon_threadsafe.assert_called_once()

    def test_previous_dispatches(self, service):
        with patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP):
            service._on_press(_PREVIOUS)
        service._loop.call_soon_threadsafe.assert_called_once()

    def test_non_media_key_no_dispatch(self, service):
        with patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP):
            service._on_press(_OTHER)
        service._loop.call_soon_threadsafe.assert_not_called()

    def test_not_running_no_dispatch(self, service):
        service._running = False
        with patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP):
            service._on_press(_PLAY_PAUSE)
        service._loop.call_soon_threadsafe.assert_not_called()

    def test_loop_none_no_dispatch(self, service):
        service._loop = None
        with patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP):
            service._on_press(_PLAY_PAUSE)
        # Must not raise.

    def test_missing_callback_no_dispatch(self, service):
        service._callbacks = {}
        with patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP):
            service._on_press(_PLAY_PAUSE)
        service._loop.call_soon_threadsafe.assert_not_called()

    def test_closed_loop_caught(self, service, caplog):
        service._loop.call_soon_threadsafe.side_effect = RuntimeError("loop closed")
        with (
            patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP),
            caplog.at_level(logging.DEBUG),
        ):
            service._on_press(_PLAY_PAUSE)
        assert "Event loop closed" in caplog.text

    def test_lambda_dispatches_correct_callback(self, service):
        """Verify the lambda captures the right callback via default arg."""
        captured = []
        service._loop.call_soon_threadsafe.side_effect = lambda fn: captured.append(fn)

        with patch("ytm_player.services.mediakeys._KEY_MAP", _TEST_KEY_MAP):
            service._on_press(_NEXT)

        assert len(captured) == 1
        with patch("ytm_player.services.mediakeys.asyncio") as mock_aio:
            captured[0]()
            mock_aio.ensure_future.assert_called_once()
            service._callbacks["next"].assert_called_once()
