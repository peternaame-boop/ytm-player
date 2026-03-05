"""
Purpose: Capture macOS hardware media keys for terminal ytm-player sessions.
Interface: MacOSEventTapService.start/stop registers and tears down a global event tap.
Invariants: Only media-key key-down events are intercepted; all other events pass through.
Decisions: Swallow handled media keys so Apple Music does not auto-launch while ytm-player is active.
"""

from __future__ import annotations

import asyncio
import logging
import threading
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

try:
    import AppKit
    import Quartz

    _EVENT_TAP_AVAILABLE = True
except ImportError:
    AppKit = None
    Quartz = None
    _EVENT_TAP_AVAILABLE = False

PlayerCallback = Callable[..., Coroutine[Any, Any, None]]

_MEDIA_EVENT_SUBTYPE = 8
_MEDIA_KEY_DOWN_STATE = 0xA

_PLAY_PAUSE_KEY = 16
_NEXT_KEY = 17
_PREVIOUS_KEY = 18

_KEY_TO_ACTION = {
    _PLAY_PAUSE_KEY: "play_pause",
    _NEXT_KEY: "next",
    _PREVIOUS_KEY: "previous",
}


def _event_action(ns_event: Any) -> str | None:
    """Return callback action for a media key event, else None."""
    if ns_event is None:
        return None
    try:
        if ns_event.subtype() != _MEDIA_EVENT_SUBTYPE:
            return None
        data1 = int(ns_event.data1())
    except Exception:
        return None

    key_code = (data1 & 0xFFFF0000) >> 16
    key_flags = data1 & 0x0000FFFF
    key_state = (key_flags & 0xFF00) >> 8
    if key_state != _MEDIA_KEY_DOWN_STATE:
        return None

    return _KEY_TO_ACTION.get(key_code)


class MacOSEventTapService:
    """Global media-key listener using Quartz event taps."""

    def __init__(self) -> None:
        self._callbacks: dict[str, PlayerCallback] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False
        self._tap: Any = None
        self._run_loop: Any = None
        self._thread: threading.Thread | None = None
        self._ready = threading.Event()

    async def start(
        self,
        callbacks: dict[str, PlayerCallback],
        loop: asyncio.AbstractEventLoop,
    ) -> bool:
        """Start background event tap thread."""
        if not _EVENT_TAP_AVAILABLE:
            logger.info("Quartz/AppKit not available — macOS media key tap disabled")
            return False

        if self._running:
            return True

        self._callbacks = callbacks
        self._loop = loop
        self._ready.clear()
        self._thread = threading.Thread(target=self._run_tap_loop, daemon=True)
        self._thread.start()
        self._ready.wait(timeout=1.0)
        return self._running

    def stop(self) -> None:
        """Stop event tap and run loop."""
        if not _EVENT_TAP_AVAILABLE:
            return

        self._running = False
        if self._tap is not None:
            try:
                Quartz.CGEventTapEnable(self._tap, False)
            except Exception:
                logger.debug("Failed to disable macOS event tap", exc_info=True)
        if self._run_loop is not None:
            try:
                Quartz.CFRunLoopStop(self._run_loop)
            except Exception:
                logger.debug("Failed to stop macOS event tap loop", exc_info=True)
        if self._thread is not None:
            self._thread.join(timeout=1.0)
            self._thread = None
        self._tap = None
        self._run_loop = None

    def _run_tap_loop(self) -> None:
        mask = Quartz.CGEventMaskBit(Quartz.NSSystemDefined)
        self._tap = Quartz.CGEventTapCreate(
            Quartz.kCGSessionEventTap,
            Quartz.kCGHeadInsertEventTap,
            Quartz.kCGEventTapOptionDefault,
            mask,
            self._tap_callback,
            None,
        )

        if self._tap is None:
            logger.info(
                "Could not create macOS media key event tap. "
                "Grant Accessibility permission to your terminal app."
            )
            self._ready.set()
            return

        source = Quartz.CFMachPortCreateRunLoopSource(None, self._tap, 0)
        self._run_loop = Quartz.CFRunLoopGetCurrent()
        Quartz.CFRunLoopAddSource(self._run_loop, source, Quartz.kCFRunLoopCommonModes)
        Quartz.CGEventTapEnable(self._tap, True)
        self._running = True
        self._ready.set()
        logger.info("macOS media key event tap enabled")
        Quartz.CFRunLoopRun()

        try:
            Quartz.CFMachPortInvalidate(self._tap)
        except Exception:
            logger.debug("Failed to invalidate macOS event tap", exc_info=True)

    def _tap_callback(self, _proxy: Any, event_type: int, event: Any, _refcon: Any) -> Any:
        if not self._running or event_type != Quartz.NSSystemDefined:
            return event

        ns_event = AppKit.NSEvent.eventWithCGEvent_(event)
        action = _event_action(ns_event)
        if action is None:
            return event

        callback = self._callbacks.get(action)
        if callback is None or self._loop is None or self._loop.is_closed():
            return event

        try:
            self._loop.call_soon_threadsafe(lambda cb=callback: asyncio.ensure_future(cb()))
        except RuntimeError:
            logger.debug("Event loop closed, cannot dispatch macOS event-tap key")
            return event

        # Swallow event so Apple Music does not steal media-key presses.
        return None
