"""Child-process half of the macOS real-framework smoke test.

Run as ``python _macos_smoke_child.py <case>`` by test_macos_smoke.py, one
process per case, so the framework calls run on a fresh main thread and the
parent can enforce a wall-clock timeout by killing the process -- the calls
are synchronous, so nothing inside the process could interrupt them.

Prints one JSON object on stdout. Exit status: 0 with a result, 2 when a
framework is not importable (message on stderr), 1 on any other failure
(traceback on stderr). Not collected by pytest (no ``test_`` prefix).
"""

from __future__ import annotations

import asyncio
import importlib
import json
import sys
import threading
import time
from typing import Any

_ACCESSORY = 1


def _framework(name: str) -> Any:
    try:
        return importlib.import_module(name)
    except ImportError as exc:
        print(f"{name} is not importable: {exc}", file=sys.stderr)
        sys.exit(2)


def _on_main_thread() -> bool:
    return threading.current_thread() is threading.main_thread()


def dock() -> dict[str, Any]:
    """Apply the accessory policy, read it back, restore the previous one."""
    appkit = _framework("AppKit")
    from ytm_player.services import macos_app
    from ytm_player.services.macos_app import hide_dock_icon

    app = appkit.NSApplication.sharedApplication()
    previous = int(app.activationPolicy())
    result: dict[str, Any] = {
        "appkit_available": macos_app._APPKIT_AVAILABLE,
        "main_thread": _on_main_thread(),
        "previous": previous,
    }
    try:
        result["switched"] = bool(hide_dock_icon())
        result["policy_after"] = int(app.activationPolicy())
    finally:
        result["restored"] = bool(app.setActivationPolicy_(previous))
        result["policy_final"] = int(app.activationPolicy())
    return result


async def _media() -> dict[str, Any]:
    mp = _framework("MediaPlayer")
    foundation = _framework("Foundation")
    from ytm_player.services import macos_media
    from ytm_player.services.macos_media import MacOSMediaService

    center = mp.MPNowPlayingInfoCenter.defaultCenter()
    result: dict[str, Any] = {
        "media_player_available": macos_media._MEDIA_PLAYER_AVAILABLE,
        "foundation_available": macos_media._FOUNDATION is not None,
        "main_thread": _on_main_thread(),
        "main_run_loop": foundation.NSRunLoop.mainRunLoop() is not None,
    }
    service = MacOSMediaService()
    pump: asyncio.Task[None] | None = None
    try:
        await service.start({}, asyncio.get_running_loop())
        pump = service._run_loop_task
        result["running"] = service._running
        result["registered"] = len(service._registered_targets)
        result["pump_started"] = pump is not None

        await service.update_metadata("Smoke title", "Smoke artist", "Smoke album", 90_000_000)
        info = center.nowPlayingInfo()
        result["published"] = None if info is None else {str(k): _plain(v) for k, v in info.items()}

        await service.update_playback_status("playing")
        info = center.nowPlayingInfo()
        result["rate_after_playing"] = (
            None if info is None else _plain(info.get(macos_media._RATE_KEY))
        )
        result["state_after_playing"] = (
            int(center.playbackState()) if hasattr(center, "playbackState") else None
        )
        result["state_playing_const"] = (
            None
            if macos_media._PLAYBACK_STATE_PLAYING is None
            else int(macos_media._PLAYBACK_STATE_PLAYING)
        )

        # Two pump intervals of real runMode:beforeDate: calls.
        await asyncio.sleep(1.1)
        result["pump_alive_after_pumping"] = pump is not None and not pump.done()
    finally:
        service.stop()
        if pump is not None:
            try:
                await pump
            except asyncio.CancelledError:
                pass
            result["pump_finished_after_stop"] = pump.done()
    result["running_after_stop"] = service._running
    result["targets_after_stop"] = len(service._registered_targets)
    result["info_after_stop"] = center.nowPlayingInfo() is not None
    return result


def _plain(value: Any) -> Any:
    """JSON-friendly copy of an Objective-C bridged scalar."""
    if isinstance(value, bool | int | float | str) or value is None:
        return value
    try:
        return float(value)
    except (TypeError, ValueError):
        return str(value)


def media() -> dict[str, Any]:
    return asyncio.run(_media())


def tap_constants() -> dict[str, Any]:
    """The re-enable branch's constants exist; no tap is created (see the test)."""
    quartz = _framework("Quartz")
    return {
        "has_timeout_const": hasattr(quartz, "kCGEventTapDisabledByTimeout"),
        "has_user_input_const": hasattr(quartz, "kCGEventTapDisabledByUserInput"),
    }


def hang() -> dict[str, Any]:
    """Harness self-check: a synchronous block the parent must be able to kill."""
    time.sleep(60)
    return {"unreachable": True}


CASES = {"dock": dock, "media": media, "tap_constants": tap_constants, "hang": hang}


def main() -> None:
    case = CASES[sys.argv[1]]
    print(json.dumps(case()))


if __name__ == "__main__":
    main()
