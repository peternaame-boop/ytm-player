"""Discord Rich Presence integration for ytm-player.

Shows the currently playing track in the user's Discord status.
Requires the optional `pypresence` package.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Discord application ID for ytm-player (YouTube Music category).
_CLIENT_ID = "1338519089781362698"


class DiscordRPC:
    """Manages Discord Rich Presence connection and updates.

    Gracefully handles Discord not running or pypresence not installed.
    Reconnects automatically if the connection drops.
    """

    def __init__(self) -> None:
        self._rpc: object | None = None
        self._connected = False
        self._start_time: float = 0

    async def connect(self) -> bool:
        """Attempt to connect to Discord. Returns True on success."""
        try:
            from pypresence import AioPresence
        except ImportError:
            logger.info("pypresence not installed — Discord RPC disabled")
            return False

        try:
            self._rpc = AioPresence(_CLIENT_ID)
            await self._rpc.connect()  # type: ignore[union-attr]
            self._connected = True
            logger.info("Connected to Discord Rich Presence")
            return True
        except Exception:
            logger.debug("Could not connect to Discord (not running?)", exc_info=True)
            self._rpc = None
            self._connected = False
            return False

    async def disconnect(self) -> None:
        """Disconnect from Discord."""
        if self._rpc and self._connected:
            try:
                await self._rpc.close()  # type: ignore[union-attr]
            except Exception:
                pass
            self._connected = False
            self._rpc = None

    async def update(
        self,
        title: str,
        artist: str,
        album: str = "",
        duration: int = 0,
        position: float = 0,
    ) -> None:
        """Update the Discord presence with current track info."""
        if not self._connected or not self._rpc:
            return

        self._start_time = time.time() - position

        details = title[:128] if title else "Unknown Track"
        state = artist[:128] if artist else "Unknown Artist"

        kwargs: dict = {
            "details": details,
            "state": state,
            "large_image": "ytm_icon",
            "large_text": album[:128] if album else "YouTube Music",
            "small_image": "play",
            "small_text": "Playing",
        }

        if duration > 0:
            kwargs["end"] = int(self._start_time + duration)

        try:
            await self._rpc.update(**kwargs)  # type: ignore[union-attr]
        except Exception:
            logger.debug("Failed to update Discord presence", exc_info=True)
            self._connected = False

    async def clear(self) -> None:
        """Clear the Discord presence (paused/stopped)."""
        if not self._connected or not self._rpc:
            return
        try:
            await self._rpc.clear()  # type: ignore[union-attr]
        except Exception:
            logger.debug("Failed to clear Discord presence", exc_info=True)
            self._connected = False

    @property
    def is_connected(self) -> bool:
        return self._connected
