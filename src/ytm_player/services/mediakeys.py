"""Windows media key listener using pynput.

Uses pynput to capture global media key events (play/pause, next, previous)
and dispatch them to the app's playback callbacks. Linux uses MPRIS (D-Bus),
and macOS uses native event-tap + Now Playing services.
"""

from __future__ import annotations

import asyncio
import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

try:
    from pynput.keyboard import Key, Listener

    _PYNPUT_AVAILABLE = True
except ImportError:
    _PYNPUT_AVAILABLE = False
    Key = None  # type: ignore[assignment,misc]
    Listener = None  # type: ignore[assignment,misc]

# Type alias matching MPRIS convention.
PlayerCallback = Callable[..., Coroutine[Any, Any, None]]

# Map pynput media Key enum members to callback action names (same as MPRIS).
# Uses the Key enum directly — not str() — because pynput delivers Key enum
# instances for special keys and they're hashable singletons.
_KEY_MAP: dict[Any, str] = {}
if _PYNPUT_AVAILABLE and Key is not None:
    _KEY_MAP = {
        Key.media_play_pause: "play_pause",
        Key.media_next: "next",
        Key.media_previous: "previous",
    }


class MediaKeysService:
    """Listens for global media key presses on Windows."""

    def __init__(self) -> None:
        self._listener: Any | None = None
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

        listener_cls = Listener
        if listener_cls is None:
            logger.debug("pynput listener unavailable — media keys disabled")
            return

        listener = listener_cls(on_press=self._on_press)
        listener.daemon = True
        listener.start()
        self._listener = listener
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
