"""Main Textual TUI application for ytm-player."""

from __future__ import annotations

import asyncio
import logging
import sys
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container, Horizontal

from ytm_player.app._ipc import IPCMixin
from ytm_player.app._keys import KeyHandlingMixin
from ytm_player.app._mpris import MPRISMixin
from ytm_player.app._navigation import PAGE_NAMES, NavigationMixin
from ytm_player.app._playback import PlaybackMixin
from ytm_player.app._session import SessionMixin
from ytm_player.app._sidebar import SidebarMixin
from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.config import KeyMap, get_keymap
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
from ytm_player.services.stream import StreamResolver
from ytm_player.services.ytmusic import YTMusicService
from ytm_player.ui.header_bar import HeaderBar
from ytm_player.ui.playback_bar import FooterBar, PlaybackBar
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar
from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar
from ytm_player.ui.theme import ThemeColors, get_theme

logger = logging.getLogger(__name__)

_POSITION_POLL_INTERVAL = 0.5


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

    #app-body {
        height: 1fr;
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

    def __init__(self) -> None:
        super().__init__()

        # Configuration.
        self.settings: Settings = get_settings()
        self.keymap: KeyMap = get_keymap()
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

        # Key input state for multi-key sequences and count prefixes.
        self._key_buffer: list[str] = []
        self._count_buffer: str = ""

        # Current active page name (empty until first navigate_to).
        self._current_page: str = ""
        self._current_page_kwargs: dict[str, Any] = {}

        # Navigation stack for back navigation.
        self._nav_stack: list[tuple[str, dict]] = []
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

    def get_css_variables(self) -> dict[str, str]:
        """Inject theme colors as Textual CSS variables ($var-name)."""
        variables = super().get_css_variables()
        tc = getattr(self, "theme_colors", None) or get_theme()
        variables.update(
            {
                "background": tc.background,
                "foreground": tc.foreground,
                "primary": tc.primary,
                "secondary": tc.secondary,
                "accent": tc.accent,
                "success": tc.success,
                "warning": tc.warning,
                "error": tc.error,
                "playback-bar-bg": tc.playback_bar_bg,
                "active-tab": tc.active_tab,
                "inactive-tab": tc.inactive_tab,
                "selected-item": tc.selected_item,
                "progress-filled": tc.progress_filled,
                "progress-empty": tc.progress_empty,
                "lyrics-played": tc.lyrics_played,
                "lyrics-current": tc.lyrics_current,
                "lyrics-upcoming": tc.lyrics_upcoming,
                "border": tc.border,
                "text-muted": tc.muted_text,
                "surface": tc.surface,
                "text": tc.text,
            }
        )
        return variables

    # ── Compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="app-header")
        yield PlaybackBar(id="playback-bar")
        yield FooterBar(id="app-footer")
        with Horizontal(id="app-body"):
            yield PlaylistSidebar(id="playlist-sidebar")
            yield Container(id="main-content")
            yield LyricsSidebar(id="lyrics-sidebar", classes="hidden")

    # ── Lifecycle ────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        """Initialize services and navigate to the startup page."""
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
            self.ytmusic = YTMusicService(auth.auth_file, auth_manager=auth)
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

        # Start MPRIS if enabled (Linux only).
        if self.settings.mpris.enabled:
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
            self.discord = DiscordRPC()
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
