"""Main Textual TUI application for ytm-player."""

from __future__ import annotations

import asyncio
import logging
import sys
from pathlib import Path
from typing import Any

if sys.version_info >= (3, 11):
    import tomllib
else:
    # Python 3.10 backport via PyPI
    import tomli as tomllib  # pyright: ignore[reportMissingImports]

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal, Vertical

from ytm_player.app._ipc import IPCMixin
from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.app._mpris import MPRISMixin
from ytm_player.app._navigation import PAGE_NAMES, NavigationMixin
from ytm_player.app._playback import PlaybackMixin
from ytm_player.app._session import SessionMixin
from ytm_player.app._sidebar import SidebarMixin
from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.config import KeyMap, get_keymap
from ytm_player.config.paths import THEME_FILE  # noqa: F401  # module-level for monkeypatch
from ytm_player.config.settings import Settings, get_settings
from ytm_player.ipc import IPCServer, remove_pid, write_pid
from ytm_player.services.auth import AuthManager
from ytm_player.services.cache import CacheManager
from ytm_player.services.discord_rpc import DiscordRPC
from ytm_player.services.download import DownloadService
from ytm_player.services.history import HistoryManager
from ytm_player.services.lastfm import LastFMService
from ytm_player.services.mediakeys import MediaKeysService
from ytm_player.services.mpris import MPRISService
from ytm_player.services.player import Player, PlayerEvent
from ytm_player.services.queue import QueueManager
from ytm_player.services.shuffle_prefs import ShufflePreferences
from ytm_player.services.stream import StreamResolver
from ytm_player.services.ytmusic import YTMusicService
from ytm_player.ui.header_bar import HeaderBar
from ytm_player.ui.playback_bar import FooterBar, PlaybackBar
from ytm_player.ui.selection_info_bar import SelectionInfoBar
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar
from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar
from ytm_player.ui.theme import DEFAULT_LYRIC_CURRENT, ThemeColors, get_theme

logger = logging.getLogger(__name__)

_POSITION_POLL_INTERVAL = 0.5

# Cache for theme.toml so get_css_variables doesn't re-parse TOML on
# every CSS resolution.  Invalidated when the file's mtime changes
# (covers user edits via `ytm config` or external editors).
_theme_toml_cache: dict | None = None
_theme_toml_mtime: float | None = None


def _read_theme_toml_cached() -> dict:
    """Return the [colors] section of theme.toml, cached by file mtime."""
    global _theme_toml_cache, _theme_toml_mtime

    # Re-read the THEME_FILE binding dynamically (tests monkeypatch this module attribute).
    path = globals().get("THEME_FILE")
    if path is None:
        return {}

    try:
        if not path.exists():
            _theme_toml_cache = {}
            _theme_toml_mtime = None
            return _theme_toml_cache

        mtime = path.stat().st_mtime
        if _theme_toml_cache is not None and _theme_toml_mtime == mtime:
            return _theme_toml_cache

        with open(path, "rb") as f:
            data = tomllib.load(f)
        # The file may have a [colors] table OR be a flat top-level dict.
        colors = data.get("colors", data)
        _theme_toml_cache = colors if isinstance(colors, dict) else {}
        _theme_toml_mtime = mtime
        return _theme_toml_cache
    except Exception:
        return {}


def _get_ytm_commands_provider():
    """Lazy-load the custom YTM command provider to avoid circular imports."""
    from ytm_player.app._commands import YTMCommandProvider

    return YTMCommandProvider


# ── Main Application ────────────────────────────────────────────────


class YTMPlayerApp(
    PlaybackMixin,
    NavigationMixin,
    KeyHandlingMixin,
    SessionMixin,
    SidebarMixin,
    TrackActionsMixin,
    MPRISMixin,
    IPCMixin,
    App,
):
    """The main ytm-player Textual application.

    Manages service lifecycle, page navigation, keybindings, and
    coordinates playback through the Player and QueueManager.
    """

    TITLE = "ytm-player"
    SUB_TITLE = "YouTube Music TUI"

    CSS = """
    Screen {
        background: $background;
        color: $foreground;
    }

    ToastRack {
        dock: top;
        align-horizontal: right;
    }

    /* When the lyrics sidebar is open, shift the toast rack left
       so notifications don't cover the lyrics. The lyrics sidebar
       is 40 cells wide; +1 for its left border. Use offset (post-
       layout) since Textual's own ToastRack DEFAULT_CSS sets margins
       that would override a margin rule. */
    Screen.lyrics-open ToastRack {
        offset: -41 0;
    }

    Toast {
        background: $surface;
    }

    Toast.-information {
        border-left: thick $primary;
    }

    Toast.-warning {
        border-left: thick $warning;
    }

    Toast.-error {
        border-left: thick $error;
    }

    #app-body {
        height: 1fr;
        width: 1fr;
    }

    #bottom-stack {
        dock: bottom;
        height: auto;
        width: 1fr;
    }

    #main-content {
        width: 1fr;
        height: 1fr;
    }

    #playback-bar {
        dock: bottom;
    }

    _PlaceholderPage #placeholder-text {
        width: 1fr;
        height: auto;
        color: $text-muted;
        text-align: center;
        padding: 2 4;
    }
    """

    # We handle all bindings ourselves through the KeyMap system.
    BINDINGS = []

    # Register custom command palette providers alongside Textual's defaults.
    COMMANDS = App.COMMANDS | {_get_ytm_commands_provider}

    def __init__(self) -> None:
        super().__init__()

        # Register custom YTM theme and set the configured default.
        from ytm_player.ui.theme import YTM_DARK

        self.register_theme(YTM_DARK)

        # Configuration.
        self.settings: Settings = get_settings()
        self.keymap: KeyMap = get_keymap()
        self.theme = self.settings.ui.theme or "ytm-dark"
        self.theme_colors: ThemeColors = get_theme()

        # Services (initialized in on_mount).
        self.ytmusic: YTMusicService | None = None
        self.player: Player | None = None
        self.queue: QueueManager = QueueManager()
        self.stream_resolver: StreamResolver | None = None
        self.history: HistoryManager | None = None
        self.cache: CacheManager | None = None
        self.mpris: MPRISService | None = None
        self.mac_media: Any = None
        self.mac_eventtap: Any = None
        self.mediakeys: MediaKeysService | None = None
        self.discord: DiscordRPC | None = None
        self.lastfm: LastFMService | None = None
        self.downloader: DownloadService = DownloadService()

        # Per-collection shuffle memory (Spotify-style).
        from ytm_player.config.paths import SHUFFLE_PREFS_FILE

        self.shuffle_prefs: ShufflePreferences = ShufflePreferences(SHUFFLE_PREFS_FILE)

        # Key input state for multi-key sequences and count prefixes.
        self._key_buffer: list[str] = []
        self._count_buffer: str = ""

        # Current active page name (empty until first navigate_to).
        self._current_page: str = ""
        self._current_page_kwargs: dict[str, Any] = {}

        # Navigation stack for back navigation.
        self._nav_stack: list[tuple[str, dict]] = []
        # Forward stack for browser-style "go forward" after a back.
        # Pushed when going back, popped when going forward. Cleared on any
        # new (non-back, non-forward) navigation, matching browser semantics.
        self._forward_stack: list[tuple[str, dict]] = []
        # Cached page state for forward navigation restoration.
        self._page_state_cache: dict[str, dict] = {}

        # Last playlist played from Library (for auto-selecting on return).
        self._active_library_playlist_id: str | None = None

        # Track position tracking for history logging.
        self._track_start_position: float = 0.0

        # Consecutive stream failure counter (prevents infinite skip loops).
        self._consecutive_failures: int = 0

        # Guard against duplicate end-file events advancing twice.
        self._advancing: bool = False
        # Debounce rapid play_track calls (e.g. double-click).
        self._last_play_video_id: str = ""
        self._last_play_time: float = 0.0

        # Pending resume from prior session (set by _restore_session_state).
        # Cleared on first matching play_track call.
        self._pending_resume_video_id: str | None = None
        self._pending_resume_position: float = 0.0

        # Reference to the position poll timer (for cleanup).
        self._poll_timer = None

        # IPC server for CLI command channel.
        self._ipc_server: IPCServer | None = None

        # Clean exit flag: True when user quits via q/C-q (no resume on next start).
        self._clean_exit: bool = False

        # Sidebar state: per-page playlist sidebar visibility and global lyrics toggle.
        # Default True for all pages -- user can toggle off per-view.
        self._sidebar_default: bool = True
        self._sidebar_per_page: dict[str, bool] = {}
        self._lyrics_sidebar_open: bool = False

        # Active pane for keyboard focus traversal (Ctrl+w h/l/w). One of
        # "content" (default), "playlists", or "lyrics". Drives which widget
        # movement/select/filter actions are routed to. Reset to "content"
        # on every page navigation.
        self._active_pane: str = "content"

        # First-run discoverability hint (Task 4.8). Flipped to True after
        # the toast fires; persisted via session.json so the hint shows
        # exactly once per install.
        self._first_run_hint_shown: bool = False

    def get_css_variables(self) -> dict[str, str]:
        """Inject app-specific CSS variables alongside Textual's theme variables.

        Base colors (primary, background, surface, etc.) come from the
        active Textual theme.  App-specific variables are derived from
        the theme's palette when not explicitly provided by the theme.
        """
        variables = super().get_css_variables()

        # App-specific variables — derive from theme palette if not set.
        app_defaults = {
            "playback-bar-bg": variables.get("surface", "#1a1a1a"),
            "active-tab": variables.get("text", "#ffffff"),
            "inactive-tab": variables.get("text-muted", "#999999"),
            "selected-item": variables.get("surface", "#2a2a2a"),
            "progress-filled": variables.get("primary", "#ff0000"),
            "progress-empty": variables.get("surface", "#555555"),
            "lyrics-played": variables.get("text-muted", "#999999"),
            "lyrics-current": variables.get(
                "accent", variables.get("primary", DEFAULT_LYRIC_CURRENT)
            ),
            "lyrics-upcoming": variables.get("text", "#aaaaaa"),
        }
        for key, default in app_defaults.items():
            if key not in variables:
                variables[key] = default

        # Apply theme.toml overrides on top (user customizations win over everything).
        colors = _read_theme_toml_cached()
        if colors:
            # Map underscore field names to CSS dash-case variable names.
            field_to_css = {
                "background": "background",
                "foreground": "foreground",
                "primary": "primary",
                "secondary": "secondary",
                "accent": "accent",
                "success": "success",
                "warning": "warning",
                "error": "error",
                "surface": "surface",
                "border": "border",
                "text": "text",
                "muted_text": "text-muted",
                "playback_bar_bg": "playback-bar-bg",
                "active_tab": "active-tab",
                "inactive_tab": "inactive-tab",
                "selected_item": "selected-item",
                "progress_filled": "progress-filled",
                "progress_empty": "progress-empty",
                "lyrics_played": "lyrics-played",
                "lyrics_current": "lyrics-current",
                "lyrics_upcoming": "lyrics-upcoming",
            }
            for field_name, css_name in field_to_css.items():
                if field_name in colors:
                    variables[css_name] = colors[field_name]

        return variables

    def watch_theme(self, theme_name: str) -> None:
        """Rebuild ThemeColors when the Textual theme changes."""
        from ytm_player.ui.theme import ThemeColors, set_theme

        try:
            t = self.current_theme
            v = t.variables
            tc = ThemeColors(
                primary=t.primary,
                background=t.background or "#0f0f0f",
                foreground=t.foreground or "#ffffff",
                secondary=t.secondary or "#aaaaaa",
                accent=t.accent or "#ff4e45",
                success=t.success or "#2ecc71",
                warning=t.warning or "#f39c12",
                error=t.error or "#e74c3c",
                surface=t.surface or "#1a1a1a",
                text=t.foreground or "#ffffff",
                muted_text=t.secondary or "#999999",
                border=t.surface or "#333333",
                playback_bar_bg=v.get("playback-bar-bg", t.surface or "#1a1a1a"),
                active_tab=v.get("active-tab", t.foreground or "#ffffff"),
                inactive_tab=v.get("inactive-tab", t.secondary or "#999999"),
                selected_item=v.get("selected-item", t.surface or "#2a2a2a"),
                progress_filled=v.get("progress-filled", t.primary),
                progress_empty=v.get("progress-empty", t.surface or "#555555"),
                lyrics_played=v.get("lyrics-played", t.secondary or "#999999"),
                lyrics_current=v.get(
                    "lyrics-current", t.accent or t.primary or DEFAULT_LYRIC_CURRENT
                ),
                lyrics_upcoming=v.get("lyrics-upcoming", t.foreground or "#aaaaaa"),
            )
            tc._apply_toml_overrides()
            set_theme(tc)
            self.theme_colors = tc
        except Exception:
            pass

    def action_set_current_theme_as_default(self) -> None:
        """Persist the active runtime theme as the config.toml default."""
        previous_theme = self.settings.ui.theme
        current_theme = str(self.theme)
        self.settings.ui.theme = current_theme
        try:
            self.settings.save()
        except OSError:
            self.settings.ui.theme = previous_theme
            logger.exception("Failed to save default theme to config.toml")
            self.notify(
                "Could not save default theme to config.toml",
                severity="error",
                timeout=5,
            )
            return

        self.notify(f"Saved {current_theme} as default theme", timeout=3)

    # ── Crash diagnostics ────────────────────────────────────────────

    def _handle_exception(self, error: Exception) -> None:
        """Override Textual's unhandled-exception path to keep the TUI alive.

        Textual's base ``_handle_exception`` is documented as "Always
        results in the app exiting" — it sets ``_return_code = 1`` and
        calls ``_close_messages_no_wait()``. For a music TUI, that's
        the wrong default: a parser drift on one page or a transient
        render glitch shouldn't kill the player mid-track.

        We persist the traceback to the crash dir (so ``ytm doctor``
        can still surface it), log it, surface a toast, and *do not*
        defer to ``super()`` — letting the app keep running. If the
        next render also fails the user will see stale UI and can
        quit cleanly.
        """
        import traceback as _traceback

        from ytm_player.utils.logging import write_crash_file

        crash_path: Path | None = None
        try:
            text = "".join(_traceback.format_exception(type(error), error, error.__traceback__))
            crash_path = write_crash_file(text, label="Textual crash (App._handle_exception)")
            logger.exception(
                "Textual unhandled exception (crash file: %s)",
                crash_path or "<not written>",
                exc_info=(type(error), error, error.__traceback__),
            )
        except Exception:
            # Never let crash-capture itself crash the app any harder.
            pass

        # Surface to the user so it's not invisible.
        try:
            hint = f" (crash: {crash_path.name})" if crash_path else ""
            self.notify(
                f"Background error{hint} — see ~/.config/ytm-player/crashes/",
                severity="error",
                timeout=5,
            )
        except Exception:
            pass

        # Intentionally NOT calling super()._handle_exception(error) — that
        # would tear down the app. Errors that genuinely make the TUI
        # unusable will still be obvious to the user via stale UI; a soft
        # error shouldn't cost them their queue position.

    def _asyncio_exception_handler(
        self, _loop: asyncio.AbstractEventLoop, context: dict[str, Any]
    ) -> None:
        """Capture asyncio loop exceptions that bypass _handle_exception.

        Default behaviour prints to stderr (invisible inside Textual's
        alt-screen). We funnel into write_crash_file so background-task
        failures are visible in crashes/ and ytm doctor. Loop is not
        terminated — the handler is informational only.
        """
        import traceback as _traceback

        from ytm_player.utils.logging import write_crash_file

        exc = context.get("exception")
        try:
            if exc is not None:
                text = "".join(_traceback.format_exception(type(exc), exc, exc.__traceback__))
            else:
                text = (
                    "asyncio loop exception (no traceback available)\n"
                    f"Message: {context.get('message', '<none>')}\n"
                    f"Context: {context!r}"
                )
            crash_path = write_crash_file(text, label="Asyncio loop exception")
            logger.warning(
                "Asyncio loop exception captured (crash file: %s)",
                crash_path or "<not written>",
            )
        except Exception:
            # Never let crash-capture itself crash the loop.
            pass

    # ── Compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="app-header")
        with Vertical(id="bottom-stack"):
            yield SelectionInfoBar(id="selection-info-bar")
            yield PlaybackBar(id="playback-bar")
            yield FooterBar(id="app-footer")
        with Horizontal(id="app-body"):
            yield PlaylistSidebar(id="playlist-sidebar")
            yield Container(id="main-content")
            yield LyricsSidebar(id="lyrics-sidebar", classes="hidden")

    # ── Lifecycle ────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        """Initialize services and navigate to the startup page."""
        # Wire the asyncio loop exception handler so background-task
        # failures and 'Task exception was never retrieved' warnings end
        # up in crashes/ instead of invisible stderr.
        try:
            loop = asyncio.get_running_loop()
            loop.set_exception_handler(self._asyncio_exception_handler)
        except RuntimeError:
            # No running loop somehow — extreme edge case; default handler stays.
            logger.debug("No running asyncio loop in on_mount — skipping handler install")

        from ytm_player.config.paths import ensure_dirs

        ensure_dirs()

        # Check authentication.
        auth = AuthManager(cookies_file=self.settings.yt_dlp.cookies_file)
        if not auth.is_authenticated():
            self.notify(
                "Not signed in to YouTube Music. Run `ytm setup` to connect your account.",
                severity="error",
                timeout=5,
            )
            # Give the user a moment to see the message.
            self.set_timer(2.0, self.exit)
            return

        # Validate auth actually works (not just file exists).
        auth_valid = await asyncio.to_thread(auth.validate)
        if not auth_valid:
            # Try to auto-refresh from the browser's cookies.
            logger.info("Auth expired, attempting auto-refresh from browser...")
            refreshed = await asyncio.to_thread(auth.try_auto_refresh)
            if refreshed:
                self.notify("Cookies refreshed from browser.", timeout=4)
                logger.info("Auto-refresh succeeded.")
            else:
                self.notify(
                    "Your YouTube Music session expired. Run `ytm setup` to sign in again.",
                    severity="error",
                    timeout=8,
                )
                logger.warning("Auth validation failed at startup — session expired.")

        # Write PID for CLI IPC detection.
        write_pid()

        # Start IPC server for CLI command channel.
        self._ipc_server = IPCServer(self._handle_ipc_command)
        await self._ipc_server.start()

        # Initialize services.
        try:
            self.ytmusic = YTMusicService(
                auth.auth_file,
                auth_manager=auth,
                user=self.settings.general.brand_account_id,
            )
            self.player = Player()
            self.player.set_event_loop(asyncio.get_running_loop())
            self.stream_resolver = StreamResolver(self.settings.playback.audio_quality)
            self.history = HistoryManager()
            await self.history.init()
            self.cache = CacheManager()
            await self.cache.init()
        except Exception as exc:
            logger.exception("Failed to initialize services")
            self.notify(
                f"Could not start player services: {exc}",
                severity="error",
                timeout=10,
            )
            self.set_timer(2.0, self.exit)
            return

        # Restore session state (volume, shuffle, repeat) from last session.
        await self._restore_session_state()

        # Start MPRIS if enabled (Linux only — dbus-fast is Linux-only, and
        # macOS/Windows have their own media integrations below).
        if sys.platform == "linux" and self.settings.mpris.enabled:
            self.mpris = MPRISService()
            callbacks = self._build_mpris_callbacks()
            await self.mpris.start(callbacks)

        # Start media key listener on Windows (MPRIS handles Linux).
        if sys.platform == "win32" and self.settings.mpris.enabled:
            self.mediakeys = MediaKeysService()
            callbacks = self._build_mpris_callbacks()
            await self.mediakeys.start(callbacks, asyncio.get_running_loop())

        # Start native macOS media key integration (Now Playing center).
        if sys.platform == "darwin" and self.settings.mpris.enabled:
            from ytm_player.services.macos_eventtap import MacOSEventTapService
            from ytm_player.services.macos_media import MacOSMediaService

            self.mac_media = MacOSMediaService()
            self.mac_eventtap = MacOSEventTapService()
            callbacks = self._build_mpris_callbacks()
            await self.mac_media.start(callbacks, asyncio.get_running_loop())
            tap_started = await self.mac_eventtap.start(callbacks, asyncio.get_running_loop())
            if not tap_started:
                self.notify(
                    "Media keys unavailable: grant Accessibility permission to your terminal app.",
                    severity="warning",
                    timeout=8,
                )

        # Start Discord Rich Presence if enabled.
        if self.settings.discord.enabled:
            self.discord = DiscordRPC(client_id=self.settings.discord.client_id)
            await self.discord.connect()

        # Start Last.fm scrobbling if enabled.
        if self.settings.lastfm.enabled:
            self.lastfm = LastFMService(
                api_key=self.settings.lastfm.api_key,
                api_secret=self.settings.lastfm.api_secret,
                session_key=self.settings.lastfm.session_key,
                username=self.settings.lastfm.username,
                password_hash=self.settings.lastfm.password_hash,
            )
            await self.lastfm.connect()

        # Pre-warm yt-dlp import in a thread so first playback isn't slow.
        asyncio.get_running_loop().run_in_executor(None, StreamResolver.warm_import)

        # Register player event handlers.
        self.player.on(PlayerEvent.TRACK_END, self._on_track_end)
        self.player.on(PlayerEvent.TRACK_CHANGE, self._on_track_change)
        self.player.on(PlayerEvent.VOLUME_CHANGE, self._on_volume_change)
        self.player.on(PlayerEvent.PAUSE_CHANGE, self._on_pause_change)

        # Poll playback position on a timer (avoids cross-thread issues).
        self._poll_timer = self.set_interval(_POSITION_POLL_INTERVAL, self._poll_position)

        # Dim the header lyrics toggle until a track is playing.
        try:
            header = self.query_one("#app-header", HeaderBar)
            header.set_lyrics_dimmed(True)
        except Exception:
            pass

        # Load playlist sidebar data.
        try:
            ps = self.query_one("#playlist-sidebar", PlaylistSidebar)
            await ps.ensure_loaded()
        except Exception:
            logger.debug("Failed to load playlist sidebar on mount", exc_info=True)

        # Navigate to startup page.
        startup = self.settings.general.startup_page
        if startup not in PAGE_NAMES:
            startup = "library"
        await self.navigate_to(startup)

        # Honour the [ui] show_selection_info toggle (TP-3).
        try:
            bar = self.query_one("#selection-info-bar", SelectionInfoBar)
            bar.display = self.settings.ui.show_selection_info
        except Exception:
            logger.exception("Failed to apply show_selection_info toggle")

        self._start_update_check()

        # First-run discoverability hint (Task 4.8). Delay slightly so the
        # toast lands after the initial layout settles, not during the
        # first paint flash.
        if not self._first_run_hint_shown:

            def _show_first_run_hint() -> None:
                self.notify(
                    "Press ? for help · vim-style keys",
                    severity="information",
                    timeout=8,
                )
                self._first_run_hint_shown = True

            self.set_timer(1.5, _show_first_run_hint)

    async def on_unmount(self) -> None:
        """Clean up services and remove PID file."""
        self._save_session_state()

        if self._ipc_server:
            await self._ipc_server.stop()
            self._ipc_server = None

        remove_pid()

        # Stop the position poll timer.
        if self._poll_timer is not None:
            self._poll_timer.stop()
            self._poll_timer = None

        if self.player:
            # Log the final track listen duration.
            await self._log_current_listen()
            self.player.clear_callbacks()
            self.player.shutdown()

        if self.stream_resolver:
            self.stream_resolver.clear_cache()

        if self.mpris:
            await self.mpris.stop()

        if self.mediakeys:
            self.mediakeys.stop()

        if self.mac_media:
            self.mac_media.stop()

        if self.mac_eventtap:
            self.mac_eventtap.stop()

        if self.discord:
            await self.discord.disconnect()

        if self.history:
            await self.history.close()

        if self.cache:
            await self.cache.close()

    def _start_update_check(self) -> None:
        """Background-check PyPI for a newer release; toast once if found."""
        if not self.settings.general.check_for_updates:
            return

        async def _run() -> None:
            from ytm_player import __version__
            from ytm_player.config.paths import UPDATE_CHECK_CACHE
            from ytm_player.services.update_check import check_for_update

            try:
                import asyncio

                latest = await asyncio.to_thread(check_for_update, __version__, UPDATE_CHECK_CACHE)
            except Exception:
                logger.debug("Update check failed", exc_info=True)
                return

            if latest:
                self.notify(
                    f"ytm-player {latest} is available (you have {__version__}). "
                    f"Run: pip install -U ytm-player",
                    timeout=8,
                )

        self.run_worker(_run(), group="update-check", exclusive=True)
