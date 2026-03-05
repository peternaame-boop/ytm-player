"""Cross-platform media key listener for macOS and Windows.

Uses pynput to capture global media key events (play/pause, next, previous)
and dispatch them to the app's playback callbacks.  On Linux, MPRIS handles
this via D-Bus instead.
"""

from __future__ import annotations

import asyncio
import logging
import sys
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

try:
    from pynput.keyboard import Key, Listener

    _PYNPUT_AVAILABLE = True
except ImportError:
    _PYNPUT_AVAILABLE = False
    Key = None  # type: ignore[assignment,misc]

# Type alias matching MPRIS convention.
PlayerCallback = Callable[..., Coroutine[Any, Any, None]]

# Map pynput media Key enum members to callback action names (same as MPRIS).
# Uses the Key enum directly — not str() — because pynput delivers Key enum
# instances for special keys and they're hashable singletons.
_KEY_MAP: dict[Any, str] = {}
if _PYNPUT_AVAILABLE:
    _KEY_MAP = {
        Key.media_play_pause: "play_pause",
        Key.media_next: "next",
        Key.media_previous: "previous",
    }


class MediaKeysService:
    """Listens for global media key presses on macOS and Windows."""

    def __init__(self) -> None:
        self._listener: Listener | None = None
        self._callbacks: dict[str, PlayerCallback] = {}
        self._loop: asyncio.AbstractEventLoop | None = None
        self._running = False

    async def start(
        self,
        callbacks: dict[str, PlayerCallback],
        loop: asyncio.AbstractEventLoop,
    ) -> None:
        """Begin listening for media key events.

        *callbacks* maps action names (play_pause, next, previous) to async
        functions — the same dict that MPRISService uses.
        """
        if not _PYNPUT_AVAILABLE:
            logger.debug("pynput is not installed — media keys disabled")
            return

        self._callbacks = callbacks
        self._loop = loop

        # Suppress pynput's own "This process is not trusted!" warning —
        # we emit a friendlier message ourselves.
        logging.getLogger("pynput").setLevel(logging.ERROR)

        # Check accessibility permissions on macOS.
        if sys.platform == "darwin":
            try:
                trusted = Listener.IS_TRUSTED
                if not trusted:
                    logger.info(
                        "Media keys: grant Accessibility permission to your terminal app "
                        "in System Settings → Privacy & Security → Accessibility"
                    )
            except AttributeError:
                pass  # Older pynput versions may not have IS_TRUSTED.

        self._listener = Listener(on_press=self._on_press)
        self._listener.daemon = True
        self._listener.start()
        self._running = True
        logger.info("Media key listener started")

    def stop(self) -> None:
        """Stop the media key listener."""
        if self._listener is not None:
            self._listener.stop()
            self._listener = None
        self._running = False
        logger.info("Media key listener stopped")

    def _on_press(self, key: Any) -> None:
        """Handle a key press event from pynput (runs on listener thread)."""
        if not self._running or not self._loop:
            return

        action_name = _KEY_MAP.get(key)
        if action_name is None:
            return

        callback = self._callbacks.get(action_name)
        if callback is None:
            return

        # Dispatch to asyncio event loop from pynput's thread.
        try:
            self._loop.call_soon_threadsafe(lambda cb=callback: asyncio.ensure_future(cb()))
        except RuntimeError:
            logger.debug("Event loop closed, cannot dispatch media key event")
