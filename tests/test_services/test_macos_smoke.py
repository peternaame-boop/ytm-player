"""Real-framework smoke test for the macOS integrations (runs on macOS only).

Every other macOS test mocks AppKit, Foundation and MediaPlayer. This module
exercises the new code paths against the frameworks as installed, so a CI run
on macos-latest shows they execute without raising and that what the code
reports matches what the frameworks report back.

What it proves: the process can be demoted to an accessory app and restored,
the media service starts against the real MPRemoteCommandCenter, publishes
metadata that MPNowPlayingInfoCenter reads back, services the main run loop
alongside asyncio, and tears everything down. What it cannot prove: that the
Dock tile, Control Center or the media keys behave as a user sees them. Those
stay unverified until someone runs the branch on a desktop session.

Deliberately NOT exercised: the media-key event tap. Creating a CGEventTap
needs Input Monitoring / Accessibility permission and captures keyboard events
system-wide; ``test_media_key_event_tap_is_not_exercised`` records that gap
explicitly instead of pretending to cover it.

Any framework that is missing on macOS is a hard failure here, not a skip:
pyproject declares them for ``sys_platform == 'darwin'``, so absence means a
packaging problem that must not pass as validated.
"""

from __future__ import annotations

import asyncio
import importlib
import sys
import threading
from typing import Any

import pytest

pytestmark = pytest.mark.skipif(sys.platform != "darwin", reason="macOS frameworks only")

_ACCESSORY = 1
_SMOKE_TIMEOUT = 5.0


def _framework(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        pytest.fail(
            f"{name} is not importable on macOS ({exc}); pyproject declares the pyobjc "
            "frameworks for darwin, so this install cannot validate the macOS integration"
        )


class TestDockPolicySmoke:
    def test_accessory_policy_is_applied_and_restored(self) -> None:
        appkit = _framework("AppKit")
        from ytm_player.services import macos_app
        from ytm_player.services.macos_app import hide_dock_icon

        assert macos_app._APPKIT_AVAILABLE, "macos_app did not pick up AppKit at import"
        assert threading.current_thread() is threading.main_thread()
        app = appkit.NSApplication.sharedApplication()
        previous = app.activationPolicy()
        try:
            switched = hide_dock_icon()
            actual = app.activationPolicy()
            assert switched is True, (
                f"AppKit refused the accessory activation policy on this host "
                f"(policy now {actual}); Dock behaviour cannot be validated here"
            )
            assert actual == _ACCESSORY
        finally:
            restored = app.setActivationPolicy_(previous)
            assert restored, f"could not restore activation policy {previous}"
            assert app.activationPolicy() == previous


class TestMediaServiceSmoke:
    async def test_start_publish_pump_and_stop(self) -> None:
        mp = _framework("MediaPlayer")
        foundation = _framework("Foundation")
        from ytm_player.services import macos_media
        from ytm_player.services.macos_media import MacOSMediaService

        assert macos_media._MEDIA_PLAYER_AVAILABLE, "macos_media did not pick up MediaPlayer"
        assert macos_media._FOUNDATION is not None, "macos_media did not pick up Foundation"
        assert threading.current_thread() is threading.main_thread()
        assert foundation.NSRunLoop.mainRunLoop() is not None

        service = MacOSMediaService()
        center = mp.MPNowPlayingInfoCenter.defaultCenter()
        loop = asyncio.get_running_loop()
        try:
            await asyncio.wait_for(service.start({}, loop), _SMOKE_TIMEOUT)
            assert service._running is True
            assert len(service._registered_targets) == 5, "not every remote command registered"
            assert service._run_loop_task is not None, "run-loop pump was not started"

            await asyncio.wait_for(
                service.update_metadata("Smoke title", "Smoke artist", "Smoke album", 90_000_000),
                _SMOKE_TIMEOUT,
            )
            published = center.nowPlayingInfo()
            assert published is not None, "MPNowPlayingInfoCenter has no now-playing info"
            assert published[macos_media._TITLE_KEY] == "Smoke title"
            assert published[macos_media._ARTIST_KEY] == "Smoke artist"
            assert float(published[macos_media._DURATION_KEY]) == 90.0

            await asyncio.wait_for(service.update_playback_status("playing"), _SMOKE_TIMEOUT)
            # Two pump intervals: the run-loop task must survive real
            # runMode:beforeDate: calls without raising.
            await asyncio.sleep(1.1)
            assert service._run_loop_task is not None and not service._run_loop_task.done(), (
                "run-loop pump stopped on its own — check the log for an exception"
            )
        finally:
            service.stop()

        assert service._running is False
        assert service._run_loop_task is None
        assert service._registered_targets == []
        assert center.nowPlayingInfo() is None, "now-playing info not cleared on stop"


def test_media_key_event_tap_is_not_exercised() -> None:
    """Recorded gap, on purpose: no CGEventTap is created in this suite."""
    quartz = _framework("Quartz")
    # The constants the re-enable branch keys on exist in the real framework;
    # the branch itself is covered with a fake tap in test_macos_eventtap.py.
    assert hasattr(quartz, "kCGEventTapDisabledByTimeout")
    assert hasattr(quartz, "kCGEventTapDisabledByUserInput")
    pytest.skip(
        "media-key event tap not exercised: creating a CGEventTap needs Input Monitoring / "
        "Accessibility and captures keyboard events system-wide; media keys stay unverified"
    )
