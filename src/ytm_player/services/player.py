"""Audio playback control using python-mpv."""

from __future__ import annotations

import asyncio
import locale
import logging
import threading
import time
from collections.abc import Callable
from enum import StrEnum, auto
from typing import Any

import mpv

logger = logging.getLogger(__name__)


class PlayerEvent(StrEnum):
    """Events emitted by the Player."""

    TRACK_CHANGE = auto()
    TRACK_END = auto()
    POSITION_CHANGE = auto()
    ERROR = auto()
    VOLUME_CHANGE = auto()
    PAUSE_CHANGE = auto()


# Type alias for callback functions.
PlayerCallback = Callable[..., Any]


class Player:
    """Singleton audio player wrapping python-mpv.

    Configured for audio-only YouTube Music playback. Stream URLs are
    resolved externally (via StreamResolver) and passed directly to mpv.
    """

    _instance: Player | None = None
    _lock = threading.Lock()

    def __new__(cls) -> Player:
        with cls._lock:
            if cls._instance is None:
                cls._instance = super().__new__(cls)
                cls._instance._initialized = False
            return cls._instance

    def __init__(self) -> None:
        if self._initialized:
            return
        self._initialized = True

        # mpv segfaults if LC_NUMERIC is not C.  Textual's async runtime
        # resets locale, so we must force it immediately before mpv init.
        import ctypes
        _libc = ctypes.CDLL("libc.so.6")
        _libc.setlocale.restype = ctypes.c_char_p
        _libc.setlocale.argtypes = [ctypes.c_int, ctypes.c_char_p]
        _libc.setlocale(locale.LC_NUMERIC, b"C")

        from ytm_player.config.settings import get_settings
        settings = get_settings()

        self._mpv = mpv.MPV(
            ytdl=False,
            video=False,
            terminal=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
        )

        # Enable gapless playback if configured.
        if settings.playback.gapless:
            try:
                self._mpv["gapless-audio"] = "yes"
            except Exception:
                logger.debug("Failed to enable gapless-audio")

        self._current_track: dict | None = None
        self._callbacks: dict[PlayerEvent, list[PlayerCallback]] = {
            event: [] for event in PlayerEvent
        }
        self._loop: asyncio.AbstractEventLoop | None = None
        # Counter for end-file events to ignore.  Incremented when we
        # intentionally replace/stop a track (so the resulting end-file
        # from mpv doesn't trigger auto-advance).
        self._end_file_skip: int = 0
        self._skip_lock = threading.Lock()
        self._last_position_dispatch: float = 0.0

        # Register mpv property observers and event handlers.
        self._mpv.observe_property("time-pos", self._on_time_pos_change)
        self._mpv.observe_property("pause", self._on_pause_change)

        @self._mpv.event_callback("end-file")
        def _on_end_file(event: Any) -> None:
            with self._skip_lock:
                if self._end_file_skip > 0:
                    self._end_file_skip -= 1
                    return
            # Check if this was an error vs normal EOF.
            reason = getattr(event, "reason", None) if event else None
            self._dispatch(PlayerEvent.TRACK_END, {"reason": reason})

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        """Get the event loop, caching the reference."""
        if self._loop is None or self._loop.is_closed():
            try:
                self._loop = asyncio.get_running_loop()
            except RuntimeError:
                self._loop = None
        return self._loop

    def set_event_loop(self, loop: asyncio.AbstractEventLoop) -> None:
        """Explicitly set the asyncio event loop for callback dispatch."""
        self._loop = loop

    # ── Callback registration ───────────────────────────────────────

    def on(self, event: PlayerEvent, callback: PlayerCallback) -> None:
        """Register a callback for a player event (no duplicates)."""
        if callback not in self._callbacks[event]:
            self._callbacks[event].append(callback)

    def off(self, event: PlayerEvent, callback: PlayerCallback) -> None:
        """Unregister a callback for a player event."""
        try:
            self._callbacks[event].remove(callback)
        except ValueError:
            pass

    def clear_callbacks(self) -> None:
        """Remove all registered callbacks."""
        for event in PlayerEvent:
            self._callbacks[event].clear()

    def _dispatch(self, event: PlayerEvent, *args: Any) -> None:
        """Dispatch an event to all registered callbacks.

        If an asyncio event loop is available, callbacks are scheduled
        via call_soon_threadsafe to bridge from mpv's thread.
        """
        loop = self._get_loop()
        for cb in list(self._callbacks[event]):
            try:
                if loop is not None and not loop.is_closed():
                    if asyncio.iscoroutinefunction(cb):
                        loop.call_soon_threadsafe(lambda _cb=cb: asyncio.create_task(_cb(*args)))
                    else:
                        loop.call_soon_threadsafe(cb, *args)
                else:
                    # No loop available; call directly (best effort).
                    cb(*args)
            except Exception:
                logger.exception("Error in %s callback", event)

    # ── mpv observers ───────────────────────────────────────────────

    def _on_time_pos_change(self, _name: str, value: float | None) -> None:
        if value is not None:
            # Throttle position dispatches to ~2 per second (lyrics + UI).
            now = time.monotonic()
            if now - self._last_position_dispatch < 0.4:
                return
            self._last_position_dispatch = now
            self._dispatch(PlayerEvent.POSITION_CHANGE, value)

    def _on_pause_change(self, _name: str, value: bool | None) -> None:
        if value is not None:
            self._dispatch(PlayerEvent.PAUSE_CHANGE, value)

    # ── Properties ──────────────────────────────────────────────────

    @property
    def is_playing(self) -> bool:
        """True if a track is loaded and not paused."""
        try:
            return not self._mpv.pause and not self._mpv.idle_active
        except mpv.ShutdownError:
            return False

    @property
    def is_paused(self) -> bool:
        try:
            return bool(self._mpv.pause)
        except mpv.ShutdownError:
            return False

    @property
    def current_track(self) -> dict | None:
        return self._current_track

    @property
    def position(self) -> float:
        """Current playback position in seconds."""
        try:
            pos = self._mpv.time_pos
            return float(pos) if pos is not None else 0.0
        except mpv.ShutdownError:
            return 0.0

    @property
    def duration(self) -> float:
        """Duration of the current track in seconds."""
        try:
            dur = self._mpv.duration
            return float(dur) if dur is not None else 0.0
        except mpv.ShutdownError:
            return 0.0

    @property
    def volume(self) -> int:
        """Current volume level (0-100)."""
        try:
            vol = self._mpv.volume
            return int(vol) if vol is not None else 0
        except mpv.ShutdownError:
            return 0

    # ── Playback control ────────────────────────────────────────────

    async def play(self, url: str, track_info: dict) -> None:
        """Play a stream URL with associated track metadata."""
        if self._current_track is not None:
            # A track is already playing — mpv will fire end-file for it
            # when we load the new URL.  Tell the callback to ignore it.
            with self._skip_lock:
                self._end_file_skip += 1
        self._current_track = track_info
        try:
            await asyncio.to_thread(self._play_sync, url)
            self._dispatch(PlayerEvent.TRACK_CHANGE, track_info)
        except Exception as exc:
            logger.error("Failed to play %s: %s", track_info.get("video_id", "?"), exc)
            self._dispatch(PlayerEvent.ERROR, exc)

    def _play_sync(self, url: str) -> None:
        """Synchronous mpv play call."""
        try:
            self._mpv.play(url)
            self._mpv.pause = False
        except mpv.ShutdownError:
            logger.warning("mpv crashed, attempting recovery...")
            if self._try_recover():
                self._mpv.play(url)
                self._mpv.pause = False
            else:
                raise RuntimeError("mpv recovery failed")

    async def pause(self) -> None:
        self._mpv.pause = True

    async def resume(self) -> None:
        self._mpv.pause = False

    async def toggle_pause(self) -> None:
        self._mpv.pause = not self._mpv.pause

    async def stop(self) -> None:
        if self._current_track is not None:
            with self._skip_lock:
                self._end_file_skip += 1
        self._mpv.stop()
        self._current_track = None

    async def seek(self, seconds: float) -> None:
        """Seek relative to the current position."""
        try:
            self._mpv.seek(seconds, reference="relative")
        except mpv.ShutdownError:
            pass

    async def seek_absolute(self, seconds: float) -> None:
        """Seek to an absolute position in seconds."""
        try:
            self._mpv.seek(seconds, reference="absolute")
        except mpv.ShutdownError:
            pass

    async def seek_start(self) -> None:
        """Seek to the beginning of the track."""
        await self.seek_absolute(0.0)

    async def set_volume(self, level: int) -> None:
        """Set volume to a specific level (clamped to 0-100)."""
        level = max(0, min(100, level))
        self._mpv.volume = level
        self._dispatch(PlayerEvent.VOLUME_CHANGE, level)

    async def change_volume(self, delta: int) -> None:
        """Adjust volume by a relative amount."""
        await self.set_volume(self.volume + delta)

    async def mute(self) -> None:
        """Toggle mute state."""
        self._mpv.mute = not self._mpv.mute

    # ── Health & Recovery ────────────────────────────────────────────

    @property
    def is_healthy(self) -> bool:
        """Check if the mpv instance is responsive."""
        try:
            _ = self._mpv.idle_active
            return True
        except (mpv.ShutdownError, OSError):
            return False

    def _try_recover(self) -> bool:
        """Attempt to re-create the mpv instance after a crash."""
        try:
            logger.info("Re-initializing mpv instance...")
            import ctypes
            import locale as _locale
            _libc = ctypes.CDLL("libc.so.6")
            _libc.setlocale.restype = ctypes.c_char_p
            _libc.setlocale.argtypes = [ctypes.c_int, ctypes.c_char_p]
            _libc.setlocale(_locale.LC_NUMERIC, b"C")

            self._mpv = mpv.MPV(
                ytdl=False,
                video=False,
                terminal=False,
                input_default_bindings=False,
                input_vo_keyboard=False,
            )

            from ytm_player.config.settings import get_settings
            if get_settings().playback.gapless:
                try:
                    self._mpv["gapless-audio"] = "yes"
                except Exception:
                    pass

            # Re-register observers.
            self._mpv.observe_property("time-pos", self._on_time_pos_change)
            self._mpv.observe_property("pause", self._on_pause_change)

            @self._mpv.event_callback("end-file")
            def _on_end_file(event: Any) -> None:
                with self._skip_lock:
                    if self._end_file_skip > 0:
                        self._end_file_skip -= 1
                        return
                reason = getattr(event, "reason", None) if event else None
                self._dispatch(PlayerEvent.TRACK_END, {"reason": reason})

            # Restore volume if possible.
            if self._loop and not self._loop.is_closed():
                try:
                    from ytm_player.config.settings import get_settings as _gs
                    self._mpv.volume = _gs().playback.default_volume
                except Exception:
                    self._mpv.volume = 80

            logger.info("mpv recovery successful")
            return True
        except Exception:
            logger.exception("mpv recovery failed")
            return False

    # ── Lifecycle ───────────────────────────────────────────────────

    def shutdown(self) -> None:
        """Terminate the mpv instance. Call on application exit."""
        try:
            self._mpv.terminate()
        except mpv.ShutdownError:
            pass
        with Player._lock:
            Player._instance = None
