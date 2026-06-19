"""Audio playback control using python-mpv."""

from __future__ import annotations

import asyncio
import locale
import logging
import os
import sys
import threading
import time
from collections.abc import Callable
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    from enum import StrEnum, auto
else:
    # Python 3.10 backport — match StrEnum.auto() lowercase-name behavior
    from enum import Enum, auto

    class StrEnum(str, Enum):
        @staticmethod
        def _generate_next_value_(name, start, count, last_values):
            return name.lower()


if sys.platform == "win32":
    # python-mpv uses ctypes to find mpv DLLs on PATH.  Package managers like
    # scoop/chocolatey install mpv to an app directory that isn't directly on
    # PATH (only shims are), so we locate the DLL and add its directory.
    _MPV_DLL_NAMES = ("libmpv-2.dll", "mpv-2.dll", "mpv-1.dll")

    def _dll_on_path() -> bool:
        for d in os.environ.get("PATH", "").split(os.pathsep):
            if d and any((Path(d) / n).exists() for n in _MPV_DLL_NAMES):
                return True
        return False

    if not _dll_on_path():
        _home = Path.home()
        _scoop = Path(os.environ.get("SCOOP", str(_home / "scoop")))
        _candidates = [
            _scoop / "apps" / "mpv" / "current",
            _scoop / "apps" / "mpv-git" / "current",
            Path(os.environ.get("ProgramFiles", r"C:\Program Files")) / "mpv",
            Path(os.environ.get("ProgramFiles(x86)", r"C:\Program Files (x86)")) / "mpv",
        ]
        for _d in _candidates:
            if _d.is_dir() and any((_d / n).exists() for n in _MPV_DLL_NAMES):
                os.environ["PATH"] = str(_d) + os.pathsep + os.environ.get("PATH", "")
                break


# Homebrew installs libmpv outside ctypes.util.find_library's search
# scope: /opt/homebrew/lib on Apple Silicon macOS isn't in the default
# dyld fallback path, and /home/linuxbrew/.linuxbrew/lib isn't in
# ldconfig's cache. Pythons not installed via brew (uv tool, pipx,
# distro python) therefore can't find libmpv even though `mpv` the CLI
# is on PATH — `ytm doctor` says mpv exists while Player() fails
# (#90, #101, #104). Locate the library in known brew prefixes and, if
# found, point find_library at it for the duration of the mpv import.
_BREW_LIB_DIRS = (
    Path("/opt/homebrew/lib"),  # macOS Apple Silicon
    Path("/usr/local/lib"),  # macOS Intel
    Path("/home/linuxbrew/.linuxbrew/lib"),  # Linuxbrew (Bazzite etc.)
    Path.home() / ".linuxbrew" / "lib",
)


def _find_brew_libmpv() -> str | None:
    """Return a libmpv path from a known Homebrew prefix, or None."""
    patterns = (
        ("libmpv.dylib", "libmpv.*.dylib")
        if sys.platform == "darwin"
        else ("libmpv.so", "libmpv.so.*")
    )
    for lib_dir in _BREW_LIB_DIRS:
        try:
            if not lib_dir.is_dir():
                continue
            for pattern in patterns:
                hits = sorted(lib_dir.glob(pattern))
                if hits:
                    return str(hits[0])
        except OSError:
            continue
    return None


# python-mpv calls ctypes.find_library("mpv") at import time. If libmpv
# isn't discoverable (no system mpv, or a CI-runner environment quirk),
# that raises OSError before any of OUR code runs — meaning the module
# can't even be imported for test discovery, IPC subcommands, or the
# `ytm doctor` command. We keep the import soft: substitute a stub when
# it fails so the module loads, and surface the real install instructions
# only when something actually tries to construct a Player.
class _MpvUnavailableError(RuntimeError):
    """Raised when python-mpv tried to load libmpv at module import and failed."""


_brew_libmpv: str | None = None
if sys.platform != "win32":
    import ctypes.util as _ctypes_util

    if _ctypes_util.find_library("mpv") is None:
        _brew_libmpv = _find_brew_libmpv()

try:
    if _brew_libmpv is not None:
        # Patch find_library only for the duration of the mpv import.
        _orig_find_library = _ctypes_util.find_library

        def _find_library_with_brew_mpv(name: str) -> str | None:
            if name == "mpv":
                return _brew_libmpv
            return _orig_find_library(name)

        _ctypes_util.find_library = _find_library_with_brew_mpv
        try:
            import mpv  # type: ignore[import-not-found]
        finally:
            _ctypes_util.find_library = _orig_find_library
    else:
        import mpv  # type: ignore[import-not-found]
except OSError as _exc:
    _IMPORT_ERROR_MSG = (
        "Cannot load libmpv. Install mpv:\n"
        "  Linux:   sudo apt install mpv libmpv-dev   (or your distro equivalent)\n"
        "  macOS:   brew install mpv\n"
        "  Windows: scoop install mpv  (or download libmpv-2.dll from\n"
        "           https://sourceforge.net/projects/mpv-player-windows/files/libmpv/\n"
        "           and place it on PATH)\n\n"
        f"Original ctypes error: {_exc}"
    )

    def _stub_mpv(*_args: Any, **_kwargs: Any) -> Any:
        raise _MpvUnavailableError(_IMPORT_ERROR_MSG)

    from types import SimpleNamespace as _SimpleNamespace

    mpv = _SimpleNamespace(MPV=_stub_mpv, ShutdownError=_MpvUnavailableError)  # type: ignore[assignment]

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
        # Strong references to dispatched async tasks so they aren't GC'd
        # before execution (the classic asyncio.create_task footgun).
        self._background_tasks: set[asyncio.Task] = set()

        self._mpv = self._init_mpv()

    def _init_mpv(self) -> Any:
        """Create and configure a new mpv instance.

        Sets LC_NUMERIC locale, creates the MPV process, enables gapless
        audio if configured, and registers property observers and the
        end-file event callback.

        Prerequisites: ``_end_file_skip``, ``_skip_lock``, and ``_callbacks``
        must already be initialised on *self* before calling this method.
        """
        # mpv segfaults if LC_NUMERIC is not C.  Textual's async runtime
        # resets locale, so we must force it immediately before mpv init.
        # On Windows, Python (3.5+) links ucrtbase.dll — calling setlocale on
        # the legacy msvcrt.dll does nothing.  locale.setlocale() targets the
        # correct CRT on every platform, but on Linux it can interact badly
        # with thread-local locale, so we use ctypes there.
        import ctypes
        import ctypes.util

        if sys.platform == "win32":
            locale.setlocale(locale.LC_NUMERIC, "C")
        else:
            if sys.platform == "darwin":
                _libc_name = "libSystem.B.dylib"
            else:
                _libc_name = ctypes.util.find_library("c") or "libc.so.6"
            _libc = ctypes.CDLL(_libc_name)
            _libc.setlocale.restype = ctypes.c_char_p
            _libc.setlocale.argtypes = [ctypes.c_int, ctypes.c_char_p]
            _libc.setlocale(locale.LC_NUMERIC, b"C")

        from ytm_player.config.settings import get_settings

        settings = get_settings()

        def _on_mpv_log(level: str, prefix: str, message: str) -> None:
            """Route mpv's internal log messages into our Python logger.

            python-mpv level strings: fatal / error / warn / info / v / debug / trace.
            We construct with loglevel='warn' so info/debug/trace are filtered
            out at the C side; this map handles the levels we actually receive.
            """
            py_level = {
                "fatal": logging.CRITICAL,
                "error": logging.ERROR,
                "warn": logging.WARNING,
                "info": logging.INFO,
            }.get(level, logging.DEBUG)
            logger.log(py_level, "mpv[%s]: %s", prefix, message.rstrip())

        instance = mpv.MPV(
            ytdl=False,
            video=False,
            terminal=False,
            input_default_bindings=False,
            input_vo_keyboard=False,
            log_handler=_on_mpv_log,
            loglevel="warn",
        )

        # Enable gapless playback if configured.
        if settings.playback.gapless:
            try:
                instance["gapless-audio"] = "yes"
            except Exception:
                logger.debug("Failed to enable gapless-audio")

        # Register mpv property observers and event handlers.
        instance.observe_property("time-pos", self._on_time_pos_change)
        instance.observe_property("pause", self._on_pause_change)

        @instance.event_callback("end-file")
        def _on_end_file(event: Any) -> None:
            # The reason code lives on event.data (MpvEventEndFile), not
            # on the MpvEvent itself.  Extract the integer reason:
            #   0 = EOF, 1 = RESTARTED, 2 = ABORTED, 3 = QUIT, 4 = ERROR, 5 = REDIRECT
            data = getattr(event, "data", None) if event else None
            reason = getattr(data, "reason", None) if data else None
            with self._skip_lock:
                if self._end_file_skip > 0:
                    self._end_file_skip -= 1
                    return
                if self._current_track is None:
                    return  # Already idle, nothing to do.
                # Capture track info before clearing — the app needs it
                # for history logging and autoplay decisions.
                ended_track = self._current_track
                # Clear so play() won't increment _end_file_skip for
                # an already-idle mpv.
                self._current_track = None
            # Only auto-advance on natural EOF (0).  Errors (4) are
            # dispatched separately.  Everything else (stop, redirect,
            # quit, restart) is intentional — ignore.
            if reason == 4:  # ERROR
                self._dispatch(PlayerEvent.ERROR, "stream error")
            elif reason is None or reason == 0:  # EOF
                self._dispatch(PlayerEvent.TRACK_END, {"reason": reason, "track": ended_track})

        return instance

    def _get_loop(self) -> asyncio.AbstractEventLoop | None:
        """Get the event loop, using the cached reference.

        Only ``set_event_loop()`` should write ``self._loop``.  If the
        cached loop is closed we return None but never clobber the field
        — otherwise a transient closed-loop check from mpv's callback
        thread would permanently destroy the reference.
        """
        loop = self._loop
        if loop is None or loop.is_closed():
            return None
        return loop

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

        def _schedule_async(coro_fn: Any, call_args: tuple) -> None:
            """Create a task for an async callback with error handling."""

            async def _safe_wrapper() -> None:
                try:
                    await coro_fn(*call_args)
                except Exception:
                    logger.exception("Async callback failed (event=%s)", event)

            task = asyncio.create_task(_safe_wrapper())
            self._background_tasks.add(task)
            task.add_done_callback(self._background_tasks.discard)

        def _safe_sync(sync_fn: Any, call_args: tuple) -> None:
            """Run a sync callback with error handling on the event loop."""
            try:
                sync_fn(*call_args)
            except Exception:
                logger.exception("Sync callback failed (event=%s)", event)

        for cb in list(self._callbacks[event]):
            try:
                if loop is not None and not loop.is_closed():
                    if asyncio.iscoroutinefunction(cb):
                        loop.call_soon_threadsafe(_schedule_async, cb, args)
                    else:
                        loop.call_soon_threadsafe(_safe_sync, cb, args)
                else:
                    # No loop: only sync callbacks can run; skip async ones.
                    if asyncio.iscoroutinefunction(cb):
                        logger.warning(
                            "Dropping async %s callback — no event loop available", event
                        )
                    else:
                        cb(*args)
            except Exception:
                logger.exception("Failed to schedule %s callback", event)

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
        with self._skip_lock:
            if self._current_track is not None:
                # A track is still playing — mpv will fire end-file for it
                # when we load the new URL.  Tell the callback to ignore it.
                self._end_file_skip += 1
            self._current_track = track_info
        try:
            await asyncio.to_thread(self._play_sync, url)
            self._dispatch(PlayerEvent.TRACK_CHANGE, track_info)
        except Exception as exc:
            with self._skip_lock:
                self._current_track = None
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
        with self._skip_lock:
            if self._current_track is not None:
                self._end_file_skip += 1
            self._current_track = None
        self._mpv.stop()

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
            with self._skip_lock:
                # Reset skip counter — it has no meaning across mpv instances.
                # Do NOT clear _current_track: _try_recover is only called from
                # within play() which has already set _current_track to the
                # NEW track we're about to play. Clearing it would break
                # downstream readers (MPRIS, Discord, _on_end_file guard).
                self._end_file_skip = 0
            self._mpv = self._init_mpv()

            # Restore volume if possible.
            if self._loop and not self._loop.is_closed():
                try:
                    from ytm_player.config.settings import get_settings

                    self._mpv.volume = get_settings().playback.default_volume
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
