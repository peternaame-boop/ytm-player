"""Persistent lyrics sidebar — right side, stays mounted across page switches."""

from __future__ import annotations

import bisect
import logging
import re
import time
from typing import Any

from textual.app import ComposeResult
from textual.containers import VerticalScroll
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Label, Static
from textual.worker import Worker, WorkerState

from ytm_player.config.keymap import Action
from ytm_player.services.player import PlayerEvent

logger = logging.getLogger(__name__)

_AUTO_SCROLL_RESUME_DELAY = 3.0
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
    lines.sort(key=lambda x: x[0])
    return lines


class _LyricLine(Static):
    """A single line of lyrics with state-driven styling."""

    DEFAULT_CSS = """
    _LyricLine {
        width: 1fr;
        height: auto;
        padding: 0 2;
    }
    """

    def __init__(self, text: str, timestamp: float | None = None, **kwargs: Any) -> None:
        super().__init__(text, **kwargs)
        self._text = text
        self._timestamp = timestamp

    async def on_click(self) -> None:
        if self._timestamp is None:
            return
        player = getattr(self.app, "player", None)
        if player:
            await player.seek_absolute(self._timestamp)


class LyricsSidebar(Widget):
    """Right-side lyrics sidebar. Stays mounted; toggled via display CSS.

    Registers player events once in on_mount. When hidden (display: none),
    skips visual updates and sets a _needs_rebuild flag for when shown.
    """

    DEFAULT_CSS = """
    LyricsSidebar {
        width: 40;
        height: 1fr;
        layout: vertical;
        border-left: solid $border;
    }

    LyricsSidebar.hidden {
        display: none;
    }

    LyricsSidebar .ls-header {
        height: 1;
        padding: 0 2;
        text-style: bold;
    }

    LyricsSidebar .ls-scroll {
        height: 1fr;
        width: 1fr;
    }

    LyricsSidebar .ls-status {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }

    LyricsSidebar .lyrics-played {
        color: $text-muted;
    }

    LyricsSidebar .lyrics-current {
        color: $success;
        text-style: bold;
    }

    LyricsSidebar .lyrics-upcoming {
        color: $text;
    }
    """

    current_line_index: reactive[int] = reactive(-1)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._synced_lines: list[tuple[float, str]] = []
        self._synced_timestamps: list[float] = []
        self._unsynced_lines: list[str] = []
        self._is_synced: bool = False
        self._lyric_widgets: list[_LyricLine] = []
        self._auto_scroll: bool = True
        self._last_manual_scroll: float = 0.0
        self._current_video_id: str | None = None
        self._position_callback: Any = None
        self._track_change_callback: Any = None
        self._needs_rebuild: bool = False
        self._pending_track: dict | None = None

    def compose(self) -> ComposeResult:
        yield Label("", id="ls-title", classes="ls-header")
        yield Label("No track playing", id="ls-status", classes="ls-status")
        yield VerticalScroll(id="ls-scroll", classes="ls-scroll")

    def on_mount(self) -> None:
        self.query_one("#ls-scroll").display = False
        # Player may not be ready yet (app services init after compose).
        # Events are registered lazily in _ensure_player_events().
        self._events_registered = False

    def on_unmount(self) -> None:
        self._unregister_player_events()

    # ── Public API ────────────────────────────────────────────────────

    def activate(self) -> None:
        """Called by the app when the sidebar is toggled visible.

        Registers player events (lazy, since player isn't ready at mount
        time) and loads lyrics for the current track if it changed.
        """
        self._ensure_player_events()
        self._load_for_current_track()

    # ── Player event integration ─────────────────────────────────────

    def _ensure_player_events(self) -> None:
        """Register player events if not already done and player is available."""
        if self._events_registered:
            return
        player = getattr(self.app, "player", None)
        if not player:
            return
        self._events_registered = True
        self._position_callback = self._on_position_change
        self._track_change_callback = self._on_track_change
        player.on(PlayerEvent.POSITION_CHANGE, self._position_callback)
        player.on(PlayerEvent.TRACK_CHANGE, self._track_change_callback)

    def _unregister_player_events(self) -> None:
        try:
            player = getattr(self.app, "player", None)
            if not player:
                return
            if self._position_callback:
                player.off(PlayerEvent.POSITION_CHANGE, self._position_callback)
            if self._track_change_callback:
                player.off(PlayerEvent.TRACK_CHANGE, self._track_change_callback)
        except Exception:
            logger.debug("Failed to unregister player events in lyrics sidebar", exc_info=True)

    def _on_track_change(self, track_info: dict) -> None:
        try:
            if not self.display:
                self._needs_rebuild = True
                self._pending_track = track_info
                return
            self._load_for_current_track()
        except Exception:
            logger.debug("Error in lyrics sidebar track change handler", exc_info=True)

    def _on_position_change(self, position: float) -> None:
        try:
            if not self.display:
                return
            if not self._is_synced or not self._synced_lines:
                return
            idx = bisect.bisect_right(self._synced_timestamps, position)
            new_index = idx - 1
            if new_index != self.current_line_index:
                self.current_line_index = new_index
        except Exception:
            logger.debug("Error in lyrics sidebar position handler", exc_info=True)

    # ── Data loading ─────────────────────────────────────────────────

    def _load_for_current_track(self) -> None:
        player = getattr(self.app, "player", None)
        if not player:
            self._show_status("No track playing.")
            return
        track = player.current_track
        if not track:
            self._show_status("No track playing.")
            return

        video_id = track.get("video_id", "")
        if video_id == self._current_video_id:
            return

        self._current_video_id = video_id
        title = track.get("title", "Unknown")
        artist = track.get("artist", "Unknown")

        try:
            title_label = self.query_one("#ls-title", Label)
            title_label.update(f"{title} \u2014 {artist}")
        except Exception:
            logger.debug("Failed to update lyrics sidebar header", exc_info=True)

        self._show_status("Loading lyrics...")
        self.run_worker(
            self._fetch_lyrics(video_id),
            name="fetch_sidebar_lyrics",
            exclusive=True,
        )

    async def _fetch_lyrics(self, video_id: str) -> dict[str, Any] | None:
        ytmusic = getattr(self.app, "ytmusic", None)
        if not ytmusic:
            return None
        data = await ytmusic.get_lyrics(video_id)

        # If we got synced lyrics from YTM, return immediately
        if data and data.get("hasTimestamps"):
            return data

        # Try LRCLIB fallback for synced lyrics
        player = getattr(self.app, "player", None)
        track = player.current_track if player else None
        if track:
            title = track.get("title", "")
            artist = track.get("artist", "")
            duration = track.get("duration_seconds") or track.get("duration")
            if title and artist:
                try:
                    from ytm_player.services.lrclib import get_synced_lyrics

                    lrc = await get_synced_lyrics(title, artist, duration)
                    if lrc:
                        return {"lyrics": lrc, "hasTimestamps": False}
                except Exception:
                    logger.debug("LRCLIB fallback failed", exc_info=True)

        return data

    def on_worker_state_changed(self, event: Worker.StateChanged) -> None:
        if event.worker.name != "fetch_sidebar_lyrics":
            return
        if event.state == WorkerState.SUCCESS:
            result = event.worker.result
            if result is None:
                self._show_status("No lyrics available.")
                return
            self._process_lyrics(result)
        elif event.state == WorkerState.ERROR:
            self._show_status("Failed to load lyrics.")

    def _process_lyrics(self, data: dict[str, Any]) -> None:
        lyrics_data = data.get("lyrics")
        if not lyrics_data:
            self._show_status("No lyrics available.")
            return

        # ytmusicapi with timestamps=True returns hasTimestamps + lyrics as list of LyricLine objects
        if data.get("hasTimestamps") and isinstance(lyrics_data, list):
            synced = [
                (entry.start_time / 1000.0, getattr(entry, "text", ""))
                for entry in lyrics_data
                if hasattr(entry, "start_time")
            ]
            if synced:
                self._is_synced = True
                self._synced_lines = synced
                self._synced_timestamps = [ts for ts, _text in synced]
                self._unsynced_lines = []
                self._build_synced_view()
                return

        # Fall back to string lyrics (plain text or LRC format)
        lyrics_text = lyrics_data if isinstance(lyrics_data, str) else ""
        if not lyrics_text:
            self._show_status("No lyrics available.")
            return

        synced = _parse_synced_lyrics(lyrics_text)
        if synced:
            self._is_synced = True
            self._synced_lines = synced
            self._synced_timestamps = [ts for ts, _text in synced]
            self._unsynced_lines = []
            self._build_synced_view()
        else:
            self._is_synced = False
            self._synced_lines = []
            self._synced_timestamps = []
            self._unsynced_lines = lyrics_text.splitlines()
            self._build_unsynced_view()

    def _build_synced_view(self) -> None:
        self._show_scroll()
        scroll = self.query_one("#ls-scroll", VerticalScroll)
        scroll.remove_children()
        self._lyric_widgets = []
        self.current_line_index = -1
        for ts, text in self._synced_lines:
            display_text = text if text else ""
            widget = _LyricLine(display_text, timestamp=ts)
            widget.add_class("lyrics-upcoming")
            self._lyric_widgets.append(widget)
            scroll.mount(widget)

    def _build_unsynced_view(self) -> None:
        self._show_scroll()
        scroll = self.query_one("#ls-scroll", VerticalScroll)
        scroll.remove_children()
        self._lyric_widgets = []
        for line in self._unsynced_lines:
            widget = _LyricLine(line)
            widget.add_class("lyrics-upcoming")
            self._lyric_widgets.append(widget)
            scroll.mount(widget)

    def _show_status(self, message: str) -> None:
        try:
            status = self.query_one("#ls-status", Label)
            status.update(message)
            status.display = True
            self.query_one("#ls-scroll").display = False
        except Exception:
            logger.debug("Failed to update lyrics sidebar status", exc_info=True)

    def _show_scroll(self) -> None:
        try:
            self.query_one("#ls-status").display = False
            self.query_one("#ls-scroll").display = True
        except Exception:
            logger.debug("Failed to toggle lyrics sidebar scroll visibility", exc_info=True)

    # ── Reactive watchers ────────────────────────────────────────────

    def watch_current_line_index(self, new_index: int) -> None:
        try:
            self._apply_line_highlight(new_index)
        except Exception:
            logger.debug("Error updating lyrics line highlight", exc_info=True)

    def _apply_line_highlight(self, new_index: int) -> None:
        if not self._is_synced or not self._lyric_widgets:
            return
        old_index = getattr(self, "_prev_line_index", -1)
        self._prev_line_index = new_index
        if old_index == new_index:
            return

        changed = set()
        if 0 <= old_index < len(self._lyric_widgets):
            changed.add(old_index)
        if 0 <= new_index < len(self._lyric_widgets):
            changed.add(new_index)
        if old_index >= 0 and new_index >= 0:
            for i in range(min(old_index, new_index), max(old_index, new_index) + 1):
                if 0 <= i < len(self._lyric_widgets):
                    changed.add(i)

        for i in changed:
            w = self._lyric_widgets[i]
            w.remove_class("lyrics-played", "lyrics-current", "lyrics-upcoming")
            if i < new_index:
                w.add_class("lyrics-played")
            elif i == new_index:
                w.add_class("lyrics-current")
            else:
                w.add_class("lyrics-upcoming")

        now = time.monotonic()
        if not self._auto_scroll:
            if now - self._last_manual_scroll > _AUTO_SCROLL_RESUME_DELAY:
                self._auto_scroll = True

        if self._auto_scroll and 0 <= new_index < len(self._lyric_widgets):
            widget = self._lyric_widgets[new_index]
            try:
                scroll = self.query_one("#ls-scroll", VerticalScroll)
                widget_y = widget.virtual_region.y
                widget_h = widget.virtual_region.height
                viewport_h = scroll.scrollable_content_region.height
                # Center the current line vertically in the viewport
                target_y = widget_y - (viewport_h // 2) + (widget_h // 2)
                scroll.scroll_to(y=max(0, target_y), animate=True)
            except Exception:
                logger.debug("Failed to auto-scroll lyrics sidebar", exc_info=True)

    # ── Action handling ──────────────────────────────────────────────

    async def handle_action(self, action: Action, count: int = 1) -> None:
        match action:
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
        self._auto_scroll = False
        self._last_manual_scroll = time.monotonic()
        try:
            scroll = self.query_one("#ls-scroll", VerticalScroll)
            if lines > 0:
                for _ in range(abs(lines)):
                    scroll.action_scroll_down()
            else:
                for _ in range(abs(lines)):
                    scroll.action_scroll_up()
        except Exception:
            logger.debug("Failed to manually scroll lyrics sidebar", exc_info=True)

    def _scroll_to_top(self) -> None:
        self._auto_scroll = False
        self._last_manual_scroll = time.monotonic()
        try:
            scroll = self.query_one("#ls-scroll", VerticalScroll)
            scroll.scroll_home(animate=False)
        except Exception:
            logger.debug("Failed to scroll lyrics sidebar to top", exc_info=True)

    def _scroll_to_bottom(self) -> None:
        self._auto_scroll = False
        self._last_manual_scroll = time.monotonic()
        try:
            scroll = self.query_one("#ls-scroll", VerticalScroll)
            scroll.scroll_end(animate=False)
        except Exception:
            logger.debug("Failed to scroll lyrics sidebar to bottom", exc_info=True)
