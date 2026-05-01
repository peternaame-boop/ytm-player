"""Queue management page showing the playback queue."""

from __future__ import annotations

import logging
from typing import TYPE_CHECKING, Any, cast

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import Input, Label, Static

from ytm_player.config.keymap import Action
from ytm_player.config.settings import get_settings
from ytm_player.services.player import PlayerEvent
from ytm_player.ui.widgets.track_table import TrackTable

if TYPE_CHECKING:
    from ytm_player.app._base import YTMHostBase

logger = logging.getLogger(__name__)


class QueuePage(Widget):
    """Displays and manages the playback queue.

    Shows the currently playing track at the top, the upcoming queue in a
    table, and repeat/shuffle state in a footer bar. Supports reordering,
    removal, and jumping to tracks.
    """

    DEFAULT_CSS = """
    QueuePage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .queue-now-playing {
        height: auto;
        max-height: 3;
        padding: 1 2;
        background: $surface;
    }
    .queue-now-playing-title {
        text-style: bold;
        color: $primary;
    }
    .queue-now-playing-artist {
        color: $text-muted;
    }
    .queue-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        dock: bottom;
    }
    .queue-empty {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    .queue-source {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        display: none;
    }
    .track-filter {
        dock: bottom;
        display: none;
    }
    .track-filter.visible {
        display: block;
    }
    """

    queue_length: reactive[int] = reactive(0)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._track_change_callback: Any = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="queue-header", classes="queue-now-playing")
        yield Static("", id="queue-source", classes="queue-source")
        yield Label("Queue is empty.", id="queue-empty", classes="queue-empty")
        yield TrackTable(show_album=False, id="queue-table")
        yield Static("", id="queue-footer", classes="queue-footer")
        yield Input(placeholder="/ Filter tracks...", id="track-filter", classes="track-filter")

    def on_mount(self) -> None:
        self._register_player_events()
        self._refresh_queue()

    def on_unmount(self) -> None:
        self._unregister_player_events()

    # ── Player event integration ──────────────────────────────────────

    def _register_player_events(self) -> None:
        player = self.app.player  # type: ignore[attr-defined]
        self._track_change_callback = self._on_track_change
        player.on(PlayerEvent.TRACK_CHANGE, self._track_change_callback)

    def _unregister_player_events(self) -> None:
        try:
            player = self.app.player  # type: ignore[attr-defined]
            if self._track_change_callback:
                player.off(PlayerEvent.TRACK_CHANGE, self._track_change_callback)
        except Exception:
            logger.debug("Failed to unregister player events in queue page", exc_info=True)

    def _on_track_change(self, _track_info: dict) -> None:
        """Update the queue display when the track changes.

        Player events are dispatched onto the asyncio loop via
        ``call_soon_threadsafe`` (see services/player.py), so this
        callback already runs on the main thread — call directly.
        """
        try:
            self._update_current_track()
        except Exception:
            logger.debug("Failed to update queue page on track change", exc_info=True)

    def _update_current_track(self) -> None:
        """Lightweight update: refresh header and play indicator without rebuilding the table."""
        queue = self.app.queue  # type: ignore[attr-defined]
        current_track = queue.current_track

        # Update the "Now Playing" header.
        header = self.query_one("#queue-header", Vertical)
        header.remove_children()
        if current_track:
            title = current_track.get("title", "Unknown")
            artist = current_track.get("artist", "Unknown")
            header.mount(Label(f"Now Playing: {title}", classes="queue-now-playing-title"))
            header.mount(Label(artist, classes="queue-now-playing-artist"))
            header.display = True
        else:
            header.display = False

        # Update the playing indicator via TrackTable.
        video_id = current_track.get("video_id", "") if current_track else None
        try:
            table = self.query_one("#queue-table", TrackTable)
            table.set_playing(video_id)
        except Exception:
            logger.debug("Failed to update playing indicator", exc_info=True)

    # ── Queue rendering ───────────────────────────────────────────────

    def _refresh_queue(self) -> None:
        """Rebuild the entire queue display from the QueueManager state."""
        queue = self.app.queue  # type: ignore[attr-defined]
        tracks = list(queue.tracks)
        current_track = queue.current_track

        # Update the "Now Playing" header.
        header = self.query_one("#queue-header", Vertical)
        header.remove_children()
        if current_track:
            title = current_track.get("title", "Unknown")
            artist = current_track.get("artist", "Unknown")
            header.mount(Label(f"Now Playing: {title}", classes="queue-now-playing-title"))
            header.mount(Label(artist, classes="queue-now-playing-artist"))
            header.display = True
        else:
            header.display = False

        table = self.query_one("#queue-table", TrackTable)

        if not tracks:
            table.display = False
            self.query_one("#queue-empty").display = True
        else:
            table.display = True
            self.query_one("#queue-empty").display = False
            table.load_tracks(tracks)

            # Set the playing indicator on the current track.
            video_id = current_track.get("video_id", "") if current_track else None
            table.set_playing(video_id)

        self.queue_length = len(tracks)
        self._update_queue_source()
        self._update_footer()

    def _update_queue_source(self) -> None:
        """Update the seed source label from QueueManager state."""
        source = self.query_one("#queue-source", Static)
        seeds = self.app.queue.radio_seeds  # type: ignore[attr-defined]
        if seeds and get_settings().ui.show_queue_source:
            titles = [s.get("title", "Unknown") for s in seeds]
            if len(titles) <= 3:
                summary = ", ".join(titles)
            else:
                summary = f"{titles[0]}, {titles[1]} + {len(titles) - 2} more"
            source.update(f"Generated from: {summary}")
            source.tooltip = "Generated from:\n" + "\n".join(f"  • {t}" for t in titles)
            source.display = True
        else:
            source.update("")
            source.tooltip = None
            source.display = False

    def _update_footer(self) -> None:
        """Update the footer bar with track count info."""
        queue = self.app.queue  # type: ignore[attr-defined]
        count = queue.length
        footer_text = f"Tracks: {count}"

        try:
            footer = self.query_one("#queue-footer", Static)
            footer.update(footer_text)
        except Exception:
            logger.debug("Failed to update queue footer", exc_info=True)

    # ── Track selection ───────────────────────────────────────────────

    async def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        """Jump to and play the selected track."""
        event.stop()
        queue = self.app.queue  # type: ignore[attr-defined]
        track = queue.jump_to(event.index)
        if track:
            host = cast("YTMHostBase", self.app)
            await host.play_track(track)
            self._refresh_queue()

    # ── Action handling ───────────────────────────────────────────────

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation and queue management actions."""
        table = self.query_one("#queue-table", TrackTable)
        queue = self.app.queue  # type: ignore[attr-defined]

        match action:
            case Action.DELETE_ITEM:
                self._remove_selected(table, queue)

            case Action.FOCUS_PREV if self._is_reorder_context():
                self._move_track(table, queue, direction=-1)

            case Action.FOCUS_NEXT if self._is_reorder_context():
                self._move_track(table, queue, direction=1)

            case Action.CYCLE_REPEAT:
                queue.cycle_repeat()
                self._update_footer()

            # NOTE: Action.TOGGLE_SHUFFLE is handled at the app level in
            # app/_keys.py and never delegated here, so no QueuePage case
            # for it.

            case Action.TRACK_ACTIONS:
                track = table.selected_track
                if track:
                    host = cast("YTMHostBase", self.app)
                    host._open_actions_for_track(track)

            case _:
                await table.handle_action(action, count)

    def _is_reorder_context(self) -> bool:
        """Always allow reorder in the queue page."""
        return True

    def _remove_selected(self, table: TrackTable, queue: Any) -> None:
        """Remove the currently highlighted track from the queue."""
        track = table.selected_track
        if not track:
            return
        # Find the track's real index in the queue.
        idx = table.cursor_row
        if idx is not None and 0 <= idx < queue.length:
            queue.remove(idx)
            self._refresh_queue()
            if table.row_count > 0:
                new_row = min(idx, table.row_count - 1)
                table.move_cursor(row=new_row)

    def _move_track(self, table: TrackTable, queue: Any, direction: int) -> None:
        """Move the highlighted track up or down in the queue."""
        if table.cursor_row is None:
            return
        from_idx = table.cursor_row
        to_idx = from_idx + direction
        if not (0 <= to_idx < queue.length):
            return
        queue.move(from_idx, to_idx)
        self._refresh_queue()
        table.move_cursor(row=to_idx)

    # ── Filter wiring ────────────────────────────────────────────────

    def on_track_table_filter_requested(self, event: TrackTable.FilterRequested) -> None:
        event.stop()
        try:
            f = self.query_one("#track-filter", Input)
            f.value = ""
            f.add_class("visible")
            f.focus()
        except Exception:
            pass

    def on_track_table_filter_closed(self, event: TrackTable.FilterClosed) -> None:
        event.stop()
        try:
            f = self.query_one("#track-filter", Input)
            f.remove_class("visible")
            self.query_one("#queue-table", TrackTable).focus()
        except Exception:
            pass

    def on_input_changed(self, event: Input.Changed) -> None:
        if event.input.id == "track-filter":
            self.query_one("#queue-table", TrackTable).apply_filter(event.value)

    def on_input_submitted(self, event: Input.Submitted) -> None:
        if event.input.id == "track-filter":
            try:
                f = self.query_one("#track-filter", Input)
                f.remove_class("visible")
                self.query_one("#queue-table", TrackTable).focus()
            except Exception:
                pass

    def on_key(self, event: object) -> None:
        from textual.events import Key

        if not isinstance(event, Key):
            return
        if event.key == "escape":
            try:
                f = self.query_one("#track-filter", Input)
                if f.has_class("visible"):
                    event.stop()
                    event.prevent_default()
                    self.query_one("#queue-table", TrackTable).clear_filter()
                    f.remove_class("visible")
                    self.query_one("#queue-table", TrackTable).focus()
            except Exception:
                pass
