"""Last.fm scrobbling integration for ytm-player.

Scrobbles tracks after 50% playback or 4 minutes (whichever comes first),
per the Last.fm scrobbling spec. Sends "Now Playing" on track start.
Requires the optional `pylast` package.
"""

from __future__ import annotations

import asyncio
import logging
import time

logger = logging.getLogger(__name__)

# Scrobble threshold: 50% of track or 4 minutes, whichever is less.
_SCROBBLE_PERCENT = 0.5
_SCROBBLE_MAX_SECONDS = 240


class LastFMService:
    """Manages Last.fm authentication and scrobbling.

    API credentials are stored in the app config. The session key is
    cached after first auth so the user doesn't need to re-authenticate.
    """

    def __init__(
        self,
        api_key: str = "",
        api_secret: str = "",
        session_key: str = "",
        username: str = "",
        password_hash: str = "",
    ) -> None:
        self._api_key = api_key
        self._api_secret = api_secret
        self._session_key = session_key
        self._username = username
        self._password_hash = password_hash
        self._network: object | None = None
        self._connected = False
        self._current_track: dict | None = None
        self._track_start: float = 0
        self._scrobbled = False

    async def connect(self) -> bool:
        """Authenticate with Last.fm. Returns True on success."""
        if not self._api_key or not self._api_secret:
            logger.info("Last.fm API credentials not configured")
            return False

        try:
            import pylast
        except ImportError:
            logger.info("pylast not installed — Last.fm scrobbling disabled")
            return False

        try:
            self._network = pylast.LastFMNetwork(
                api_key=self._api_key,
                api_secret=self._api_secret,
                session_key=self._session_key or None,
                username=self._username or None,
                password_hash=self._password_hash or None,
            )
            self._connected = True
            logger.info("Connected to Last.fm")
            return True
        except Exception:
            logger.debug("Failed to connect to Last.fm", exc_info=True)
            self._connected = False
            return False

    async def now_playing(
        self, title: str, artist: str, album: str = "", duration: int = 0
    ) -> None:
        """Send a "Now Playing" notification to Last.fm."""
        if not self._connected or not self._network:
            return

        self._current_track = {
            "title": title,
            "artist": artist,
            "album": album,
            "duration": duration,
        }
        self._track_start = time.time()
        self._scrobbled = False

        try:
            await asyncio.to_thread(
                self._network.update_now_playing,  # type: ignore[union-attr]
                artist=artist,
                title=title,
                album=album or None,
                duration=duration or None,
            )
        except Exception:
            logger.debug("Failed to update Last.fm Now Playing", exc_info=True)

    async def check_scrobble(self, position: float) -> None:
        """Check if the current track should be scrobbled based on position.

        Call this periodically (e.g. every few seconds) with the current
        playback position.
        """
        if not self._connected or not self._current_track or self._scrobbled:
            return

        duration = self._current_track.get("duration", 0)
        if duration <= 0:
            # No duration info — scrobble after 4 minutes.
            threshold = _SCROBBLE_MAX_SECONDS
        else:
            threshold = min(duration * _SCROBBLE_PERCENT, _SCROBBLE_MAX_SECONDS)

        if position >= threshold:
            await self._scrobble()

    async def _scrobble(self) -> None:
        """Submit the current track as a scrobble."""
        if not self._network or not self._current_track:
            return

        self._scrobbled = True
        track = self._current_track
        timestamp = int(self._track_start)

        try:
            await asyncio.to_thread(
                self._network.scrobble,  # type: ignore[union-attr]
                artist=track["artist"],
                title=track["title"],
                timestamp=timestamp,
                album=track.get("album") or None,
                duration=track.get("duration") or None,
            )
            logger.debug("Scrobbled: %s - %s", track["artist"], track["title"])
        except Exception:
            logger.debug("Failed to scrobble to Last.fm", exc_info=True)

    @property
    def is_connected(self) -> bool:
        return self._connected
