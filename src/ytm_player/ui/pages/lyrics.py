"""Synced lyrics display page."""

from __future__ import annotations

import bisect
import logging
import re
import time
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical, VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static
from textual.worker import Worker, WorkerState

from ytm_player.config.keymap import Action
from ytm_player.services.player import PlayerEvent

logger = logging.getLogger(__name__)

# Seconds of manual-scroll inactivity before snapping back to auto-scroll.
_AUTO_SCROLL_RESUME_DELAY = 3.0

# Regex for synced lyrics timestamps: [mm:ss.xx] or [mm:ss]
_TIMESTAMP_RE = re.compile(r"\[(\d{1,2}):(\d{2})(?:\.(\d{1,3}))?\]")


def _parse_synced_lyrics(raw: str) -> list[tuple[float, str]]:
    """Parse LRC-format synced lyrics into (timestamp_seconds, text) tuples."""
    lines: list[tuple[float, str]] = []
    for line in raw.splitlines():
        match = _TIMESTAMP_RE.match(line.strip())
        if match:
            minutes = int(match.group(1))
            seconds = int(match.group(2))
            centis = match.group(3)
            frac = int(centis) / (10 ** len(centis)) if centis else 0.0
            ts = minutes * 60 + seconds + frac
            text = _TIMESTAMP_RE.sub("", line).strip()
            lines.append((ts, text))
    # Sort by timestamp in case the source isn't ordered.
    lines.sort(key=lambda x: x[0])
    return lines


class _LyricLine(Static):
    """A single line of lyrics with state-driven styling."""

    DEFAULT_CSS = """
    _LyricLine {
        width: 1fr;
        height: auto;
        padding: 0 4;
    }
    """

    def __init__(self, text: str, **kwargs: Any) -> None:
        super().__init__(text, **kwargs)
        self._text = text


class LyricsPage(Widget):
    """Displays lyrics for the currently playing track.

    Supports synced (timestamped) and unsynced lyrics. Synced lyrics auto-scroll
    to the current line, with color coding for played, current, and upcoming lines.
    Manual scrolling pauses auto-scroll for a few seconds before resuming.
    """

    DEFAULT_CSS = """
    LyricsPage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .lyrics-header {
        height: auto;
        max-height: 3;
        padding: 1 2;
        text-style: bold;
    }
    .lyrics-header-subtitle {
        color: $text-muted;
        padding: 0 2;
    }
    .lyrics-scroll {
        height: 1fr;
        width: 1fr;
    }
    .lyrics-status {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    .lyrics-played {
        color: $text-muted;
    }
    .lyrics-current {
        color: $success;
        text-style: bold;
    }
    .lyrics-upcoming {
        color: $text;
    }
    """

    loading: reactive[bool] = reactive(True)
    current_line_index: reactive[int] = reactive(-1)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._synced_lines: list[tuple[float, str]] = []
        self._unsynced_lines: list[str] = []
        self._is_synced: bool = False
        self._lyric_widgets: list[_LyricLine] = []
        self._auto_scroll: bool = True
        self._last_manual_scroll: float = 0.0
        self._current_video_id: str | None = None
        self._position_callback: Any = None
        self._track_change_callback: Any = None

    def compose(self) -> ComposeResult:
        yield Label("", id="lyrics-title", classes="lyrics-header")
        yield Label("", id="lyrics-subtitle", classes="lyrics-header-subtitle")
        yield Label("Loading lyrics...", id="lyrics-status", classes="lyrics-status")
        yield VerticalScroll(id="lyrics-scroll", classes="lyrics-scroll")

    def on_mount(self) -> None:
        self.query_one("#lyrics-scroll").display = False
        self.query_one("#lyrics-subtitle").display = False
        self._register_player_events()
        self._load_for_current_track()

    def on_unmount(self) -> None:
        self._unregister_player_events()

    # ── Player event integration ──────────────────────────────────────

    def _register_player_events(self) -> None:
        """Subscribe to player position and track-change events."""
        player = self.app.player  # type: ignore[attr-defined]

        self._position_callback = self._on_position_change
        self._track_change_callback = self._on_track_change

        player.on(PlayerEvent.POSITION_CHANGE, self._position_callback)
        player.on(PlayerEvent.TRACK_CHANGE, self._track_change_callback)

    def _unregister_player_events(self) -> None:
        """Unsubscribe from player events."""
        try:
            player = self.app.player  # type: ignore[attr-defined]
            if self._position_callback:
                player.off(PlayerEvent.POSITION_CHANGE, self._position_callback)
            if self._track_change_callback:
                player.off(PlayerEvent.TRACK_CHANGE, self._track_change_callback)
        except Exception:
            pass

    def _on_track_change(self, track_info: dict) -> None:
        """Called when the player switches to a new track."""
        self._load_for_current_track()

    def _on_position_change(self, position: float) -> None:
        """Called on playback position updates; drives synced lyrics highlighting."""
        if not self._is_synced or not self._synced_lines:
            return

        # Determine which line should be current using binary search.
        timestamps = [ts for ts, _text in self._synced_lines]
        idx = bisect.bisect_right(timestamps, position)
        new_index = idx - 1

        if new_index != self.current_line_index:
            self.current_line_index = new_index

    # ── Data loading ──────────────────────────────────────────────────

    def _load_for_current_track(self) -> None:
        """Fetch lyrics for whatever track is currently playing."""
        player = self.app.player  # type: ignore[attr-defined]
        track = player.current_track
        if not track:
            self._show_status("No track playing.")
            return

        video_id = track.get("video_id", "")
        if video_id == self._current_video_id:
            return  # Already loaded for this track.

        self._current_video_id = video_id
        title = track.get("title", "Unknown")
        artist = track.get("artist", "Unknown")

        try:
            title_label = self.query_one("#lyrics-title", Label)
            title_label.update(f"{title}")
            subtitle_label = self.query_one("#lyrics-subtitle", Label)
            subtitle_label.update(artist)
            subtitle_label.display = True
        except Exception:
            pass

        self.loading = True
        self._show_status("Loading lyrics...")
        self.run_worker(
            self._fetch_lyrics(video_id),
            name="fetch_lyrics",
            exclusive=True,
        )

    async def _fetch_lyrics(self, video_id: str) -> dict[str, Any] | None:
        ytmusic = self.app.ytmusic  # type: ignore[attr-defined]
        return await ytmusic.get_lyrics(video_id)

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "fetch_lyrics":
            return

        if event.state == WorkerState.SUCCESS:
            self.loading = False
            result = event.worker.result
            if result is None:
                self._show_status("No lyrics available.")
                return
            self._process_lyrics(result)
        elif event.state == WorkerState.ERROR:
            self.loading = False
            self._show_status("Failed to load lyrics.")

    def _process_lyrics(self, data: dict[str, Any]) -> None:
        """Parse lyrics data and build the line widgets."""
        lyrics_text = data.get("lyrics", "")
        if not lyrics_text:
            self._show_status("No lyrics available.")
            return

        # Check if the lyrics contain timestamp markers (synced).
        synced = _parse_synced_lyrics(lyrics_text)
        if synced:
            self._is_synced = True
            self._synced_lines = synced
            self._unsynced_lines = []
            self._build_synced_view()
        else:
            self._is_synced = False
            self._synced_lines = []
            self._unsynced_lines = lyrics_text.splitlines()
            self._build_unsynced_view()

    def _build_synced_view(self) -> None:
        """Build lyric line widgets for synced lyrics."""
        self._show_scroll()
        scroll = self.query_one("#lyrics-scroll", VerticalScroll)
        scroll.remove_children()
        self._lyric_widgets = []
        self.current_line_index = -1

        for _ts, text in self._synced_lines:
            display_text = text if text else ""
            widget = _LyricLine(display_text)
            widget.add_class("lyrics-upcoming")
            self._lyric_widgets.append(widget)
            scroll.mount(widget)

    def _build_unsynced_view(self) -> None:
        """Build lyric line widgets for unsynced (plain) lyrics."""
        self._show_scroll()
        scroll = self.query_one("#lyrics-scroll", VerticalScroll)
        scroll.remove_children()
        self._lyric_widgets = []

        for line in self._unsynced_lines:
            widget = _LyricLine(line)
            widget.add_class("lyrics-upcoming")
            self._lyric_widgets.append(widget)
            scroll.mount(widget)

    def _show_status(self, message: str) -> None:
        """Show a status message and hide the scroll area."""
        try:
            status = self.query_one("#lyrics-status", Label)
            status.update(message)
            status.display = True
            self.query_one("#lyrics-scroll").display = False
        except Exception:
            pass

    def _show_scroll(self) -> None:
        """Show the scroll area and hide the status label."""
        try:
            self.query_one("#lyrics-status").display = False
            self.query_one("#lyrics-scroll").display = True
        except Exception:
            pass

    # ── Reactive watchers ─────────────────────────────────────────────

    def watch_current_line_index(self, new_index: int) -> None:
        """Update line styling when the current synced line changes."""
        if not self._is_synced or not self._lyric_widgets:
            return

        for i, widget in enumerate(self._lyric_widgets):
            widget.remove_class("lyrics-played", "lyrics-current", "lyrics-upcoming")
            if i < new_index:
                widget.add_class("lyrics-played")
            elif i == new_index:
                widget.add_class("lyrics-current")
            else:
                widget.add_class("lyrics-upcoming")

        # Auto-scroll to the current line if enabled.
        now = time.monotonic()
        if not self._auto_scroll:
            if now - self._last_manual_scroll > _AUTO_SCROLL_RESUME_DELAY:
                self._auto_scroll = True

        if self._auto_scroll and 0 <= new_index < len(self._lyric_widgets):
            widget = self._lyric_widgets[new_index]
            try:
                scroll = self.query_one("#lyrics-scroll", VerticalScroll)
                scroll.scroll_visible(widget, animate=True)
            except Exception:
                pass

    # ── Action handling ───────────────────────────────────────────────

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation and other actions."""
        match action:
            case Action.GO_BACK:
                await self.app.navigate_to("back")  # type: ignore[attr-defined]
            case Action.MOVE_DOWN:
                self._manual_scroll(count)
            case Action.MOVE_UP:
                self._manual_scroll(-count)
            case Action.PAGE_DOWN:
                self._manual_scroll(10)
            case Action.PAGE_UP:
                self._manual_scroll(-10)
            case Action.GO_TOP:
                self._scroll_to_top()
            case Action.GO_BOTTOM:
                self._scroll_to_bottom()

    def _manual_scroll(self, lines: int) -> None:
        """Manually scroll up or down, pausing auto-scroll."""
        self._auto_scroll = False
        self._last_manual_scroll = time.monotonic()
        try:
            scroll = self.query_one("#lyrics-scroll", VerticalScroll)
            if lines > 0:
                for _ in range(abs(lines)):
                    scroll.action_scroll_down()
            else:
                for _ in range(abs(lines)):
                    scroll.action_scroll_up()
        except Exception:
            pass

    def _scroll_to_top(self) -> None:
        self._auto_scroll = False
        self._last_manual_scroll = time.monotonic()
        try:
            scroll = self.query_one("#lyrics-scroll", VerticalScroll)
            scroll.scroll_home(animate=False)
        except Exception:
            pass

    def _scroll_to_bottom(self) -> None:
        self._auto_scroll = False
        self._last_manual_scroll = time.monotonic()
        try:
            scroll = self.query_one("#lyrics-scroll", VerticalScroll)
            scroll.scroll_end(animate=False)
        except Exception:
            pass
