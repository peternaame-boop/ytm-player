"""Main Textual TUI application for ytm-player."""

from __future__ import annotations

import asyncio
import logging
from typing import Any

from textual.app import App, ComposeResult
from textual.containers import Container
from textual.events import Key
from textual.widget import Widget
from textual.widgets import Static

from ytm_player.config import Action, KeyMap, MatchResult, get_keymap
from ytm_player.config.settings import Settings, get_settings
from ytm_player.ipc import remove_pid, write_pid
from ytm_player.services.auth import AuthManager
from ytm_player.services.cache import CacheManager
from ytm_player.services.history import HistoryManager
from ytm_player.services.mpris import MPRISService
from ytm_player.services.player import Player, PlayerEvent
from ytm_player.services.queue import QueueManager, RepeatMode
from ytm_player.services.stream import StreamResolver
from ytm_player.services.ytmusic import YTMusicService
from ytm_player.ui.playback_bar import FooterBar, PlaybackBar
from ytm_player.ui.popups.actions import ActionsPopup
from ytm_player.ui.popups.playlist_picker import PlaylistPicker
from ytm_player.ui.theme import ThemeColors, get_theme
from ytm_player.ui.widgets.track_table import TrackTable

logger = logging.getLogger(__name__)

# Valid page names.
PAGE_NAMES = ("library", "search", "context", "browse", "lyrics", "queue", "help")


# ── Placeholder page widget ─────────────────────────────────────────

class _PlaceholderPage(Widget):
    """Temporary placeholder shown for pages not yet implemented."""

    DEFAULT_CSS = """
    _PlaceholderPage {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
    }
    """

    def __init__(self, page_name: str, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._page_name = page_name

    def compose(self) -> ComposeResult:
        yield Static(
            f"\n\n  [{self._page_name.upper()}]\n\n"
            f"  This page is not yet implemented.\n"
            f"  Navigate with: g l (library), g s (search), z (queue), ? (help)\n",
            id="placeholder-text",
        )

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """No-op action handler for placeholder pages."""
        pass


# ── Main Application ────────────────────────────────────────────────


class YTMPlayerApp(App):
    """The main ytm-player Textual application.

    Manages service lifecycle, page navigation, keybindings, and
    coordinates playback through the Player and QueueManager.
    """

    TITLE = "ytm-player"
    SUB_TITLE = "YouTube Music TUI"

    CSS = """
    Screen {
        background: #0f0f0f;
        color: #ffffff;
    }

    ToastRack {
        dock: top;
        align-horizontal: right;
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
        color: #999999;
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

        # Key input state for multi-key sequences and count prefixes.
        self._key_buffer: list[str] = []
        self._count_buffer: str = ""

        # Current active page name.
        self._current_page: str = "library"

        # Navigation stack for back navigation.
        self._nav_stack: list[tuple[str, dict]] = []

        # Track position tracking for history logging.
        self._track_start_position: float = 0.0

        # Consecutive stream failure counter (prevents infinite skip loops).
        self._consecutive_failures: int = 0

        # Reference to the position poll timer (for cleanup).
        self._poll_timer = None

    @property
    def current_page_name(self) -> str:
        return self._current_page

    # ── Compose ──────────────────────────────────────────────────────

    def compose(self) -> ComposeResult:
        yield PlaybackBar(id="playback-bar")
        yield Container(id="main-content")
        yield FooterBar(id="app-footer")

    # ── Lifecycle ────────────────────────────────────────────────────

    async def on_mount(self) -> None:
        """Initialize services and navigate to the startup page."""
        # Check authentication.
        auth = AuthManager()
        if not auth.is_authenticated():
            self.notify(
                "Not authenticated. Run `ytm setup` first.",
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
                    "Session expired. Run `ytm setup` to re-authenticate.",
                    severity="error",
                    timeout=8,
                )
                logger.warning("Auth validation failed at startup — session expired.")

        # Write PID for CLI IPC detection.
        write_pid()

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
        except Exception:
            logger.exception("Failed to initialize services")
            self.notify("Failed to initialize services. Check logs.", severity="error")
            self.set_timer(2.0, self.exit)
            return

        # Set initial volume.
        await self.player.set_volume(self.settings.playback.default_volume)

        # Start MPRIS if enabled.
        if self.settings.mpris.enabled:
            self.mpris = MPRISService()
            callbacks = self._build_mpris_callbacks()
            await self.mpris.start(callbacks)

        # Register player event handlers.
        self.player.on(PlayerEvent.TRACK_END, self._on_track_end)
        self.player.on(PlayerEvent.TRACK_CHANGE, self._on_track_change)
        self.player.on(PlayerEvent.VOLUME_CHANGE, self._on_volume_change)
        self.player.on(PlayerEvent.PAUSE_CHANGE, self._on_pause_change)

        # Poll playback position on a timer (avoids cross-thread issues).
        self._poll_timer = self.set_interval(0.5, self._poll_position)

        # Navigate to startup page.
        startup = self.settings.general.startup_page
        if startup not in PAGE_NAMES:
            startup = "library"
        await self.navigate_to(startup)

    async def on_unmount(self) -> None:
        """Clean up services and remove PID file."""
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

        if self.history:
            await self.history.close()

        if self.cache:
            await self.cache.close()

    # ── Key handling ─────────────────────────────────────────────────

    async def on_key(self, event: Key) -> None:
        """Process keyboard input through the KeyMap system.

        Supports vim-style count prefixes (e.g. "5j" to move down 5 rows)
        and multi-key sequences (e.g. "g g" to go to top).
        """
        # Don't intercept keys when a modal screen is active — let the
        # modal's own widgets (Input, ListView, etc.) handle them.
        if self.screen.is_modal:
            return

        # Don't intercept keys when an Input or TextArea is focused — let
        # the widget handle normal text entry.
        from textual.widgets import Input, TextArea
        focused = self.focused
        if isinstance(focused, (Input, TextArea)):
            return

        key = self._normalize_key(event)

        # Digit handling: accumulate count prefix if no keys buffered yet.
        if key.isdigit() and not self._key_buffer:
            self._count_buffer += key
            event.prevent_default()
            return

        self._key_buffer.append(key)
        sequence = tuple(self._key_buffer)

        result, action = self.keymap.match(sequence)

        if result == MatchResult.EXACT:
            count = int(self._count_buffer) if self._count_buffer else 1
            count = min(count, 1000)  # Safety cap.
            self._key_buffer.clear()
            self._count_buffer = ""
            event.prevent_default()
            event.stop()
            await self._handle_action(action, count)

        elif result == MatchResult.PENDING:
            # Waiting for more keys in the sequence.
            event.prevent_default()
            event.stop()

        else:
            # No match -- reset buffers.
            self._key_buffer.clear()
            self._count_buffer = ""

    @staticmethod
    def _normalize_key(event: Key) -> str:
        """Convert a Textual Key event into the string format used by KeyMap.

        Textual key names like 'ctrl+r' become 'C-r', 'shift+tab' becomes
        'S-tab', etc.
        """
        key = event.key

        # Textual uses names like "ctrl+x", "shift+tab", "alt+v".
        if key.startswith("ctrl+"):
            return f"C-{key[5:]}"
        if key.startswith("shift+"):
            return f"S-{key[6:]}"
        if key.startswith("alt+"):
            return f"M-{key[4:]}"

        # Map Textual's special key names to our keymap names.
        key_map = {
            "up": "up",
            "down": "down",
            "left": "left",
            "right": "right",
            "home": "home",
            "end": "end",
            "pageup": "page_up",
            "pagedown": "page_down",
            "page_up": "page_up",
            "page_down": "page_down",
            "backspace": "backspace",
            "delete": "delete",
            "tab": "tab",
            "enter": "enter",
            "return": "enter",
            "escape": "escape",
            "plus": "+",
            "minus": "-",
            "equals": "=",
            "question_mark": "?",
            "slash": "/",
        }

        return key_map.get(key, key)

    # ── Action dispatch ──────────────────────────────────────────────

    async def _handle_action(self, action: Action | None, count: int = 1) -> None:
        """Dispatch a resolved action to the appropriate handler."""
        if action is None:
            return

        match action:
            # -- Playback controls --
            case Action.PLAY_PAUSE:
                if self.player:
                    await self.player.toggle_pause()

            case Action.NEXT_TRACK:
                await self._play_next()

            case Action.PREVIOUS_TRACK:
                await self._play_previous()

            case Action.PLAY_RANDOM:
                track = self.queue.play_random()
                if track:
                    await self.play_track(track)

            case Action.VOLUME_UP:
                if self.player:
                    await self.player.change_volume(5 * count)

            case Action.VOLUME_DOWN:
                if self.player:
                    await self.player.change_volume(-5 * count)

            case Action.MUTE:
                if self.player:
                    await self.player.mute()

            case Action.SEEK_FORWARD:
                if self.player:
                    await self.player.seek(self.settings.playback.seek_step * count)

            case Action.SEEK_BACKWARD:
                if self.player:
                    await self.player.seek(-self.settings.playback.seek_step * count)

            case Action.SEEK_START:
                if self.player:
                    await self.player.seek_start()

            case Action.CYCLE_REPEAT:
                mode = self.queue.cycle_repeat()
                bar = self.query_one("#playback-bar", PlaybackBar)
                bar.update_repeat(mode)
                self.notify(f"Repeat: {mode.value}", timeout=2)

            case Action.TOGGLE_SHUFFLE:
                self.queue.toggle_shuffle()
                bar = self.query_one("#playback-bar", PlaybackBar)
                bar.update_shuffle(self.queue.shuffle_enabled)
                state = "on" if self.queue.shuffle_enabled else "off"
                self.notify(f"Shuffle: {state}", timeout=2)

            # -- Page navigation --
            case Action.LIBRARY:
                await self.navigate_to("library")
            case Action.SEARCH:
                await self.navigate_to("search")
            case Action.QUEUE:
                await self.navigate_to("queue")
            case Action.LYRICS:
                await self.navigate_to("lyrics")
            case Action.BROWSE:
                await self.navigate_to("browse")
            case Action.HELP:
                await self.navigate_to("help")
            case Action.CURRENT_CONTEXT:
                await self.navigate_to("context")

            case Action.GO_BACK:
                await self.navigate_to("back")

            case Action.CLOSE_POPUP:
                # Dismiss active popup if any; otherwise ignore.
                pass

            # -- Add to playlist (quick shortcut for current track) --
            case Action.ADD_TO_PLAYLIST:
                await self._open_add_to_playlist()

            # -- Track actions (opens popup, handles result) --
            case Action.TRACK_ACTIONS:
                await self._open_track_actions()

            # -- Navigation actions delegated to the current page --
            case (
                Action.MOVE_DOWN
                | Action.MOVE_UP
                | Action.PAGE_DOWN
                | Action.PAGE_UP
                | Action.GO_TOP
                | Action.GO_BOTTOM
                | Action.SELECT
                | Action.FOCUS_NEXT
                | Action.FOCUS_PREV
                | Action.CONTEXT_ACTIONS
                | Action.SELECTED_ACTIONS
                | Action.ADD_TO_QUEUE
                | Action.DELETE_ITEM
                | Action.FILTER
                | Action.SORT_TITLE
                | Action.SORT_ARTIST
                | Action.SORT_ALBUM
                | Action.SORT_DURATION
                | Action.SORT_DATE
                | Action.REVERSE_SORT
                | Action.LIKED_SONGS
                | Action.RECENTLY_PLAYED
                | Action.JUMP_TO_CURRENT
                | Action.TOGGLE_SEARCH_MODE
            ):
                page = self._get_current_page()
                if page and hasattr(page, "handle_action"):
                    await page.handle_action(action, count)

            case _:
                logger.debug("Unhandled action: %s", action)

    # ── Page navigation ──────────────────────────────────────────────

    async def navigate_to(self, page_name: str, **kwargs: Any) -> None:
        """Swap the content of #main-content to a new page.

        Extra *kwargs* are forwarded to the page constructor (e.g.
        ``context_type`` and ``context_id`` for ContextPage).
        Pass ``page_name="back"`` to pop from the navigation stack.
        """
        # Handle "back" navigation via stack.
        if page_name == "back":
            if self._nav_stack:
                prev_page, prev_kwargs = self._nav_stack.pop()
                page_name = prev_page
                kwargs = prev_kwargs
            else:
                page_name = "library"

        if page_name not in PAGE_NAMES:
            logger.warning("Unknown page: %s", page_name)
            return

        if page_name == self._current_page and not kwargs:
            return  # Already on this page; nothing to do.

        # Push current page onto the nav stack before switching.
        if self._current_page and self._current_page != page_name:
            self._nav_stack.append((self._current_page, {}))
            # Cap stack size.
            if len(self._nav_stack) > 20:
                self._nav_stack = self._nav_stack[-20:]

        container = self.query_one("#main-content", Container)

        # remove_children and mount are async; must await them.
        await container.remove_children()
        page_widget = self._create_page(page_name, **kwargs)
        await container.mount(page_widget)
        self._current_page = page_name

        # Update footer active page indicator.
        try:
            footer = self.query_one("#app-footer", FooterBar)
            footer.set_active_page(page_name)
        except Exception:
            pass

        logger.debug("Navigated to page: %s", page_name)

    def _create_page(self, page_name: str, **kwargs: Any) -> Widget:
        """Instantiate the widget for a given page name."""
        from ytm_player.ui.pages.browse import BrowsePage
        from ytm_player.ui.pages.context import ContextPage
        from ytm_player.ui.pages.help import HelpPage
        from ytm_player.ui.pages.library import LibraryPage
        from ytm_player.ui.pages.lyrics import LyricsPage
        from ytm_player.ui.pages.queue import QueuePage
        from ytm_player.ui.pages.search import SearchPage

        page_map: dict[str, type[Widget]] = {
            "library": LibraryPage,
            "search": SearchPage,
            "context": ContextPage,
            "browse": BrowsePage,
            "lyrics": LyricsPage,
            "queue": QueuePage,
            "help": HelpPage,
        }
        page_cls = page_map.get(page_name)
        if page_cls is None:
            return _PlaceholderPage(page_name, id=f"page-{page_name}")
        return page_cls(id=f"page-{page_name}", **kwargs)

    def _get_current_page(self) -> Widget | None:
        """Return the currently mounted page widget, or None."""
        try:
            container = self.query_one("#main-content", Container)
            children = list(container.children)
            return children[0] if children else None
        except Exception:
            return None

    # ── Playback coordination ────────────────────────────────────────

    async def play_track(self, track: dict) -> None:
        """Resolve a stream URL and start playback for a track.

        This is the main entry point for initiating playback from any
        page or action.
        """
        if not self.player or not self.stream_resolver:
            self.notify("Player not initialized.", severity="error")
            return

        video_id = track.get("video_id", "")
        if not video_id:
            self.notify("Track has no video ID.", severity="error")
            return

        # Log listen time for the previous track.
        await self._log_current_listen()

        # Update UI immediately.
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_track(track)
            bar.update_playback_state(is_playing=False, is_paused=False)
        except Exception:
            logger.debug("Playback bar not ready during play_track", exc_info=True)

        # Resolve the stream URL.
        self.notify(f"Loading: {track.get('title', video_id)}", timeout=3)
        stream_info = await self.stream_resolver.resolve(video_id)

        if stream_info is None:
            self._consecutive_failures += 1
            self.notify(
                f"Failed to resolve stream for: {track.get('title', video_id)}",
                severity="error",
            )
            # Auto-advance to the next track unless we've failed too many times.
            if self._consecutive_failures < 5:
                next_track = self.queue.next_track()
                if next_track:
                    self.call_later(lambda: self.run_worker(self.play_track(next_track)))
            else:
                self.notify("Too many consecutive failures — stopping.", severity="error")
                self._consecutive_failures = 0
            return

        self._consecutive_failures = 0

        # Start playback.
        await self.player.play(stream_info.url, track)
        self._track_start_position = 0.0

        # Update MPRIS metadata.
        if self.mpris:
            duration_us = int(stream_info.duration * 1_000_000)
            await self.mpris.update_metadata(
                title=track.get("title", ""),
                artist=track.get("artist", ""),
                album=track.get("album", ""),
                art_url=track.get("thumbnail_url", ""),
                length_us=duration_us,
            )
            await self.mpris.update_playback_status("Playing")

    async def _play_next(self) -> None:
        """Advance to the next track in the queue and play it."""
        track = self.queue.next_track()
        if track:
            await self.play_track(track)
        elif self.settings.playback.autoplay and self.player and self.player.current_track:
            # Fetch radio/autoplay suggestions.
            await self._fetch_and_play_radio()
        else:
            self.notify("Queue is empty.", timeout=2)

    async def _play_previous(self) -> None:
        """Go back to the previous track in the queue."""
        # If we're more than 3 seconds into a track, restart it instead.
        if self.player and self.player.position > 3.0:
            await self.player.seek_start()
            return

        track = self.queue.previous_track()
        if track:
            await self.play_track(track)

    async def _fetch_and_play_radio(self) -> None:
        """Fetch radio suggestions for the current track and continue playback."""
        if not self.ytmusic or not self.player or not self.player.current_track:
            return

        video_id = self.player.current_track.get("video_id", "")
        if not video_id:
            return

        self.notify("Loading radio suggestions...", timeout=3)
        try:
            radio_tracks = await self.ytmusic.get_radio(video_id)
            if radio_tracks:
                self.queue.set_radio_tracks(radio_tracks)
                next_track = self.queue.next_track()
                if next_track:
                    await self.play_track(next_track)
                    return
        except Exception:
            logger.exception("Failed to fetch radio tracks")

        self.notify("No more tracks available.", timeout=2)

    # ── Player event callbacks ───────────────────────────────────────

    async def _on_track_end(self, event: Any = None) -> None:
        """Handle track ending -- advance to next."""
        await self._play_next()

    def _poll_position(self) -> None:
        """Timer callback: poll the player position and update the bar."""
        if not self.player:
            return
        try:
            pos = self.player.position
            dur = self.player.duration
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_position(pos, dur)
        except Exception:
            pass

        if self.mpris and self.player.is_playing:
            try:
                self.mpris.update_position(int(self.player.position * 1_000_000))
            except Exception:
                pass

    def _on_track_change(self, track: dict) -> None:
        """Handle track change event from the player.

        Called on the event loop via call_soon_threadsafe — safe to touch widgets.
        """
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_track(track)
            bar.update_playback_state(is_playing=True, is_paused=False)
        except Exception:
            logger.debug("Failed to update playback bar on track change", exc_info=True)

        # Update playing indicator on any visible TrackTable.
        video_id = track.get("video_id", "")
        try:
            page = self._get_current_page()
            if page:
                for table in page.query(TrackTable):
                    table.set_playing(video_id)
        except Exception:
            pass

    def _on_volume_change(self, volume: int) -> None:
        """Handle volume change events."""
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_volume(volume)
        except Exception:
            logger.debug("Failed to update volume display", exc_info=True)

    def _on_pause_change(self, paused: bool) -> None:
        """Handle pause/resume events."""
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_playback_state(is_playing=not paused, is_paused=paused)
        except Exception:
            logger.debug("Failed to update pause state display", exc_info=True)

        if self.mpris:
            status = "Paused" if paused else "Playing"
            try:
                self.call_later(
                    lambda s=status: self.run_worker(self.mpris.update_playback_status(s))
                )
            except Exception:
                logger.debug("Failed to update MPRIS playback status", exc_info=True)

    # ── History logging ──────────────────────────────────────────────

    async def _log_current_listen(self) -> None:
        """Log the listen duration for the currently playing track."""
        if not self.history or not self.player or not self.player.current_track:
            return

        listened = int(self.player.position - self._track_start_position)
        if listened > 0:
            try:
                await self.history.log_play(
                    track=self.player.current_track,
                    listened_seconds=listened,
                    source="tui",
                )
            except Exception:
                logger.exception("Failed to log play history")

    # ── MPRIS callback builder ───────────────────────────────────────

    def _build_mpris_callbacks(self) -> dict[str, Any]:
        """Build the callback dict expected by MPRISService.start()."""
        return {
            "play": self._mpris_play,
            "pause": self._mpris_pause,
            "play_pause": self._mpris_play_pause,
            "stop": self._mpris_stop,
            "next": self._mpris_next,
            "previous": self._mpris_previous,
            "seek": self._mpris_seek,
            "set_position": self._mpris_set_position,
            "quit": self._mpris_quit,
        }

    async def _mpris_play(self) -> None:
        if self.player and self.player.is_paused:
            await self.player.resume()

    async def _mpris_pause(self) -> None:
        if self.player:
            await self.player.pause()

    async def _mpris_play_pause(self) -> None:
        if self.player:
            await self.player.toggle_pause()

    async def _mpris_stop(self) -> None:
        if self.player:
            await self.player.stop()

    async def _mpris_next(self) -> None:
        await self._play_next()

    async def _mpris_previous(self) -> None:
        await self._play_previous()

    async def _mpris_seek(self, offset_us: int) -> None:
        if self.player:
            await self.player.seek(offset_us / 1_000_000)

    async def _mpris_set_position(self, position_us: int) -> None:
        if self.player:
            await self.player.seek_absolute(position_us / 1_000_000)

    async def _mpris_quit(self) -> None:
        self.exit()

    # ── Track table integration ──────────────────────────────────────

    async def on_track_table_track_selected(
        self, message: TrackTable.TrackSelected
    ) -> None:
        """Handle track selection from any TrackTable widget."""
        track = message.track
        index = message.index

        # Set the queue position and play.
        self.queue.jump_to(index)
        await self.play_track(track)

    # ── Add-to-playlist / Track-actions wiring ──────────────────────

    def _get_focused_track(self) -> dict | None:
        """Try to get a track dict from the currently focused widget."""
        focused = self.focused
        if focused is None:
            return None

        # Walk up to find a TrackTable parent.
        widget = focused
        while widget is not None:
            if isinstance(widget, TrackTable):
                return widget.selected_track
            widget = widget.parent
        return None

    async def _open_add_to_playlist(self) -> None:
        """Open PlaylistPicker for the currently playing track."""
        track = None

        # Prefer the currently playing track.
        if self.player and self.player.current_track:
            track = self.player.current_track

        if not track:
            self.notify("No track is playing.", severity="warning", timeout=2)
            return

        video_id = track.get("video_id", "") or track.get("videoId", "")
        if not video_id:
            self.notify("Track has no video ID.", severity="warning", timeout=2)
            return

        self.push_screen(PlaylistPicker(video_ids=[video_id]))

    async def _open_track_actions(self) -> None:
        """Open ActionsPopup for the focused track."""
        track = self._get_focused_track()
        if not track:
            # Fall back to currently playing track.
            if self.player and self.player.current_track:
                track = self.player.current_track
            else:
                self.notify("No track selected.", severity="warning", timeout=2)
                return

        def _handle_action_result(action_id: str | None) -> None:
            """Callback when the user picks an action from the popup."""
            if action_id is None:
                return

            if action_id == "add_to_playlist":
                video_id = track.get("video_id", "") or track.get("videoId", "")
                if video_id:
                    self.push_screen(PlaylistPicker(video_ids=[video_id]))
                return

            if action_id == "play":
                self.run_worker(self.play_track(track))
            elif action_id == "play_next":
                self.queue.add_next(track)
                self.notify("Playing next", timeout=2)
            elif action_id == "add_to_queue":
                self.queue.add(track)
                self.notify("Added to queue", timeout=2)
            elif action_id == "start_radio":
                self.run_worker(self._start_radio_for(track))
            elif action_id == "go_to_artist":
                artists = track.get("artists", [])
                if isinstance(artists, list) and artists:
                    artist = artists[0]
                    artist_id = artist.get("id") or artist.get("browseId", "")
                    if artist_id:
                        self.run_worker(self.navigate_to(
                            "context", context_type="artist", context_id=artist_id
                        ))
            elif action_id == "go_to_album":
                album = track.get("album", {})
                album_id = (
                    track.get("album_id")
                    or (album.get("id") if isinstance(album, dict) else None)
                    or ""
                )
                if album_id:
                    self.run_worker(self.navigate_to(
                        "context", context_type="album", context_id=album_id
                    ))
            elif action_id == "toggle_like":
                video_id = track.get("video_id", "") or track.get("videoId", "")
                if video_id and self.ytmusic:
                    is_liked = (
                        track.get("likeStatus") == "LIKE"
                        or track.get("liked", False)
                    )
                    rating = "INDIFFERENT" if is_liked else "LIKE"
                    self.run_worker(self.ytmusic.rate_song(video_id, rating))
                    label = "Unliked" if is_liked else "Liked"
                    self.notify(label, timeout=2)
            elif action_id == "copy_link":
                video_id = track.get("video_id", "") or track.get("videoId", "")
                if video_id:
                    link = f"https://music.youtube.com/watch?v={video_id}"
                    try:
                        import subprocess
                        subprocess.run(
                            ["xclip", "-selection", "clipboard"],
                            input=link.encode(),
                            check=True,
                        )
                        self.notify("Link copied", timeout=2)
                    except Exception:
                        self.notify(link, timeout=5)

        self.push_screen(ActionsPopup(track, item_type="track"), _handle_action_result)

    async def _start_radio_for(self, track: dict) -> None:
        """Start radio from a specific track."""
        video_id = track.get("video_id", "") or track.get("videoId", "")
        if not video_id or not self.ytmusic:
            return

        self.notify("Starting radio...", timeout=3)
        try:
            radio_tracks = await self.ytmusic.get_radio(video_id)
            if radio_tracks:
                self.queue.clear()
                self.queue.set_radio_tracks(radio_tracks)
                first = self.queue.next_track()
                if first:
                    await self.play_track(first)
        except Exception:
            logger.exception("Failed to start radio")
            self.notify("Failed to start radio", severity="error")
