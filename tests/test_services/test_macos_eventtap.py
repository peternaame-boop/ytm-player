"""Tests for ytm_player.services.macos_eventtap."""

from __future__ import annotations

import asyncio
from types import SimpleNamespace
from unittest.mock import patch

from ytm_player.services.macos_eventtap import MacOSEventTapService, _event_action


class _FakeNSEvent:
    def __init__(self, subtype: int, data1: int) -> None:
        self._subtype = subtype
        self._data1 = data1

    def subtype(self) -> int:
        return self._subtype

    def data1(self) -> int:
        return self._data1


def _media_data1(key_code: int, state: int) -> int:
    return (key_code << 16) | (state << 8)


class TestEventAction:
    def test_play_pause_key_down(self) -> None:
        event = _FakeNSEvent(8, _media_data1(16, 0xA))
        assert _event_action(event) == "play_pause"

    def test_ignores_key_up(self) -> None:
        event = _FakeNSEvent(8, _media_data1(16, 0xB))
        assert _event_action(event) is None

    def test_ignores_non_media_subtype(self) -> None:
        event = _FakeNSEvent(0, _media_data1(16, 0xA))
        assert _event_action(event) is None


class TestTapCallback:
    async def test_start_returns_false_when_unavailable(self) -> None:
        svc = MacOSEventTapService()
        with patch("ytm_player.services.macos_eventtap._EVENT_TAP_AVAILABLE", False):
            started = await svc.start({}, asyncio.get_running_loop())
        assert started is False

    async def test_dispatches_and_swallows_event(self) -> None:
        svc = MacOSEventTapService()
        svc._running = True
        svc._loop = asyncio.get_running_loop()

        fired = asyncio.Event()

        async def _play_pause() -> None:
            fired.set()

        svc._callbacks = {"play_pause": _play_pause}
        ns_event = _FakeNSEvent(8, _media_data1(16, 0xA))

        fake_appkit = SimpleNamespace(
            NSEvent=SimpleNamespace(eventWithCGEvent_=lambda _event: ns_event)
        )
        fake_quartz = SimpleNamespace(NSSystemDefined=14)

        with (
            patch("ytm_player.services.macos_eventtap.AppKit", fake_appkit),
            patch("ytm_player.services.macos_eventtap.Quartz", fake_quartz),
        ):
            result = svc._tap_callback(None, 14, object(), None)

        assert result is None
        await asyncio.wait_for(fired.wait(), timeout=1)

    async def test_passthrough_when_not_mapped(self) -> None:
        svc = MacOSEventTapService()
        svc._running = True
        svc._loop = asyncio.get_running_loop()
        svc._callbacks = {}

        ns_event = _FakeNSEvent(8, _media_data1(16, 0xA))
        source_event = object()

        fake_appkit = SimpleNamespace(
            NSEvent=SimpleNamespace(eventWithCGEvent_=lambda _event: ns_event)
        )
        fake_quartz = SimpleNamespace(NSSystemDefined=14)

        with (
            patch("ytm_player.services.macos_eventtap.AppKit", fake_appkit),
            patch("ytm_player.services.macos_eventtap.Quartz", fake_quartz),
        ):
            result = svc._tap_callback(None, 14, source_event, None)

        assert result is source_event
