"""MPRIS D-Bus integration for Linux media key support.

Exposes ytm-player on the session bus so desktop environments and media key
daemons can control playback.
"""

from __future__ import annotations

import logging
from collections.abc import Callable, Coroutine
from typing import Any

logger = logging.getLogger(__name__)

try:
    from dbus_next import Variant
    from dbus_next.aio import MessageBus
    from dbus_next.service import PropertyAccess, ServiceInterface, dbus_property, method, signal

    _DBUS_AVAILABLE = True
except ImportError:
    _DBUS_AVAILABLE = False

BUS_NAME = "org.mpris.MediaPlayer2.ytm_player"
OBJECT_PATH = "/org/mpris/MediaPlayer2"

# Type alias for the async callback functions the player provides.
PlayerCallback = Callable[..., Coroutine[Any, Any, None]]


def _empty_metadata() -> dict[str, Variant]:
    """Return a default/empty MPRIS metadata dict."""
    return {
        "mpris:trackid": Variant("o", "/org/mpris/MediaPlayer2/TrackList/NoTrack"),
        "xesam:title": Variant("s", ""),
        "xesam:artist": Variant("as", [""]),
        "xesam:album": Variant("s", ""),
        "mpris:artUrl": Variant("s", ""),
        "mpris:length": Variant("x", 0),
    }


if _DBUS_AVAILABLE:
    # ------------------------------------------------------------------ #
    #  org.mpris.MediaPlayer2  (root interface)
    # ------------------------------------------------------------------ #

    class _MediaPlayer2Interface(ServiceInterface):
        """Basic MPRIS identity interface."""

        def __init__(self, callbacks: dict[str, PlayerCallback]) -> None:
            super().__init__("org.mpris.MediaPlayer2")
            self._callbacks = callbacks

        @dbus_property(access=PropertyAccess.READ)
        def Identity(self) -> "s":  # type: ignore[override]
            return "ytm-player"

        @dbus_property(access=PropertyAccess.READ)
        def CanQuit(self) -> "b":  # type: ignore[override]
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanRaise(self) -> "b":  # type: ignore[override]
            return False

        @dbus_property(access=PropertyAccess.READ)
        def HasTrackList(self) -> "b":  # type: ignore[override]
            return False

        @dbus_property(access=PropertyAccess.READ)
        def DesktopEntry(self) -> "s":  # type: ignore[override]
            return "ytm-player"

        @dbus_property(access=PropertyAccess.READ)
        def SupportedUriSchemes(self) -> "as":  # type: ignore[override]
            return []

        @dbus_property(access=PropertyAccess.READ)
        def SupportedMimeTypes(self) -> "as":  # type: ignore[override]
            return []

        @method()
        async def Quit(self) -> None:
            cb = self._callbacks.get("quit")
            if cb:
                await cb()

        @method()
        async def Raise(self) -> None:
            pass  # TUI cannot raise a window.

    # ------------------------------------------------------------------ #
    #  org.mpris.MediaPlayer2.Player
    # ------------------------------------------------------------------ #

    class _PlayerInterface(ServiceInterface):
        """MPRIS Player interface for playback control."""

        def __init__(self, callbacks: dict[str, PlayerCallback]) -> None:
            super().__init__("org.mpris.MediaPlayer2.Player")
            self._callbacks = callbacks
            self._playback_status = "Stopped"
            self._metadata: dict[str, Variant] = _empty_metadata()
            self._volume = 0.8
            self._position_us: int = 0

        # --- Properties ------------------------------------------------

        @dbus_property(access=PropertyAccess.READ)
        def PlaybackStatus(self) -> "s":  # type: ignore[override]
            return self._playback_status

        @dbus_property(access=PropertyAccess.READ)
        def Metadata(self) -> "a{sv}":  # type: ignore[override]
            return self._metadata

        @dbus_property()
        def Volume(self) -> "d":  # type: ignore[override]
            return self._volume

        @Volume.setter  # type: ignore[attr-defined]
        def Volume(self, value: "d") -> None:  # type: ignore[override]
            self._volume = max(0.0, min(1.0, value))

        @dbus_property(access=PropertyAccess.READ)
        def Position(self) -> "x":  # type: ignore[override]
            return self._position_us

        @dbus_property(access=PropertyAccess.READ)
        def Rate(self) -> "d":  # type: ignore[override]
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MinimumRate(self) -> "d":  # type: ignore[override]
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def MaximumRate(self) -> "d":  # type: ignore[override]
            return 1.0

        @dbus_property(access=PropertyAccess.READ)
        def CanPlay(self) -> "b":  # type: ignore[override]
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanPause(self) -> "b":  # type: ignore[override]
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanSeek(self) -> "b":  # type: ignore[override]
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanGoNext(self) -> "b":  # type: ignore[override]
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanGoPrevious(self) -> "b":  # type: ignore[override]
            return True

        @dbus_property(access=PropertyAccess.READ)
        def CanControl(self) -> "b":  # type: ignore[override]
            return True

        # --- Methods ---------------------------------------------------

        @method()
        async def Play(self) -> None:
            cb = self._callbacks.get("play")
            if cb:
                await cb()

        @method()
        async def Pause(self) -> None:
            cb = self._callbacks.get("pause")
            if cb:
                await cb()

        @method()
        async def PlayPause(self) -> None:
            cb = self._callbacks.get("play_pause")
            if cb:
                await cb()

        @method()
        async def Stop(self) -> None:
            cb = self._callbacks.get("stop")
            if cb:
                await cb()

        @method()
        async def Next(self) -> None:
            cb = self._callbacks.get("next")
            if cb:
                await cb()

        @method()
        async def Previous(self) -> None:
            cb = self._callbacks.get("previous")
            if cb:
                await cb()

        @method()
        async def Seek(self, offset: "x") -> None:
            cb = self._callbacks.get("seek")
            if cb:
                await cb(offset)

        @method()
        async def SetPosition(self, track_id: "o", position: "x") -> None:
            cb = self._callbacks.get("set_position")
            if cb:
                await cb(position)

        # --- Signals ---------------------------------------------------

        @signal()
        def Seeked(self) -> "x":
            return self._position_us

        # --- Internal helpers for state updates ------------------------

        def set_metadata(
            self,
            title: str,
            artist: str,
            album: str,
            art_url: str,
            length_us: int,
        ) -> None:
            self._metadata = {
                "mpris:trackid": Variant(
                    "o", "/org/mpris/MediaPlayer2/TrackList/CurrentTrack"
                ),
                "xesam:title": Variant("s", title),
                "xesam:artist": Variant("as", [artist]),
                "xesam:album": Variant("s", album),
                "mpris:artUrl": Variant("s", art_url),
                "mpris:length": Variant("x", length_us),
            }

        def set_playback_status(self, status: str) -> None:
            self._playback_status = status

        def set_position(self, position_us: int) -> None:
            self._position_us = position_us


class MPRISService:
    """Manages the MPRIS D-Bus presence for ytm-player."""

    def __init__(self) -> None:
        self._bus: MessageBus | None = None
        self._root_iface: _MediaPlayer2Interface | None = None
        self._player_iface: _PlayerInterface | None = None
        self._running = False

    # ------------------------------------------------------------------
    # Lifecycle
    # ------------------------------------------------------------------

    async def start(self, player_callbacks: dict[str, PlayerCallback]) -> None:
        """Connect to the session bus and export the MPRIS interfaces.

        *player_callbacks* maps action names to async functions the player
        exposes (play, pause, play_pause, next, previous, stop, seek,
        set_position, quit).
        """
        if not _DBUS_AVAILABLE:
            logger.warning("dbus-next is not installed -- MPRIS disabled")
            return

        try:
            self._bus = await MessageBus().connect()
        except Exception:
            logger.warning("Could not connect to the session D-Bus -- MPRIS disabled", exc_info=True)
            return

        self._root_iface = _MediaPlayer2Interface(player_callbacks)
        self._player_iface = _PlayerInterface(player_callbacks)

        self._bus.export(OBJECT_PATH, self._root_iface)
        self._bus.export(OBJECT_PATH, self._player_iface)

        await self._bus.request_name(BUS_NAME)
        self._running = True
        logger.info("MPRIS service registered as %s", BUS_NAME)

    async def stop(self) -> None:
        """Disconnect from D-Bus."""
        if self._bus is not None:
            self._bus.disconnect()
            self._bus = None
        self._running = False
        logger.info("MPRIS service stopped")

    # ------------------------------------------------------------------
    # State updates (called by the player engine)
    # ------------------------------------------------------------------

    async def update_metadata(
        self,
        title: str,
        artist: str,
        album: str,
        art_url: str,
        length_us: int,
    ) -> None:
        """Push new track metadata to D-Bus listeners."""
        if not self._running or self._player_iface is None:
            return

        self._player_iface.set_metadata(title, artist, album, art_url, length_us)
        self._emit_properties_changed(
            "org.mpris.MediaPlayer2.Player",
            {"Metadata": self._player_iface._metadata},
        )

    async def update_playback_status(self, status: str) -> None:
        """Update Playing / Paused / Stopped status on D-Bus."""
        if not self._running or self._player_iface is None:
            return

        self._player_iface.set_playback_status(status)
        self._emit_properties_changed(
            "org.mpris.MediaPlayer2.Player",
            {"PlaybackStatus": status},
        )

    def update_position(self, position_us: int) -> None:
        """Update the current playback position (microseconds)."""
        if not self._running or self._player_iface is None:
            return

        self._player_iface.set_position(position_us)

    # ------------------------------------------------------------------
    # Internal helpers
    # ------------------------------------------------------------------

    def _emit_properties_changed(
        self,
        interface_name: str,
        changed: dict[str, Any],
    ) -> None:
        """Emit org.freedesktop.DBus.Properties.PropertiesChanged.

        *changed* maps property names to their **raw Python values** (not
        Variant-wrapped) — dbus-next's ``emit_properties_changed`` handles
        the Variant wrapping internally.
        """
        if self._player_iface is None:
            return

        self._player_iface.emit_properties_changed(changed)
