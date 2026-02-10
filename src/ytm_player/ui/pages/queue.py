"""Queue management page showing the playback queue."""

from __future__ import annotations

import logging
from typing import Any

from textual.app import ComposeResult
from textual.containers import Vertical
from textual.reactive import reactive
from textual.widget import Widget
from textual.widgets import DataTable, Label, Static
from textual.widgets.data_table import RowKey

from ytm_player.config.keymap import Action
from ytm_player.services.player import PlayerEvent
from ytm_player.services.queue import RepeatMode
from ytm_player.utils.formatting import extract_artist, extract_duration, format_duration

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
        color: $success;
    }
    .queue-now-playing-artist {
        color: $text-muted;
    }
    .queue-table {
        height: 1fr;
        width: 1fr;
    }
    .queue-table > .datatable--cursor {
        background: #2a2a2a;
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
    """

    queue_length: reactive[int] = reactive(0)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._row_keys: list[RowKey] = []
        self._track_change_callback: Any = None

    def compose(self) -> ComposeResult:
        yield Vertical(id="queue-header", classes="queue-now-playing")
        yield Label("Queue is empty.", id="queue-empty", classes="queue-empty")
        yield DataTable(
            cursor_type="row",
            zebra_stripes=True,
            id="queue-table",
            classes="queue-table",
        )
        yield Static("", id="queue-footer", classes="queue-footer")

    def on_mount(self) -> None:
        table = self.query_one("#queue-table", DataTable)
        table.add_column("#", width=4, key="index")
        table.add_column("Title", width=None, key="title")
        table.add_column("Artist", width=None, key="artist")
        table.add_column("Duration", width=8, key="duration")

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
            pass

    def _on_track_change(self, _track_info: dict) -> None:
        """Refresh the queue display when the track changes."""
        try:
            self.call_from_thread(self._refresh_queue)
        except Exception:
            logger.debug("call_from_thread failed in queue page", exc_info=True)

    # ── Queue rendering ───────────────────────────────────────────────

    def _refresh_queue(self) -> None:
        """Rebuild the entire queue display from the QueueManager state."""
        queue = self.app.queue  # type: ignore[attr-defined]
        tracks = queue.tracks
        current_index = queue.current_index
        current_track = queue.current_track

        # Update the "Now Playing" header.
        header = self.query_one("#queue-header", Vertical)
        header.remove_children()
        if current_track:
            title = current_track.get("title", "Unknown")
            artist = current_track.get("artist", "Unknown")
            header.mount(
                Label(f"Now Playing: {title}", classes="queue-now-playing-title")
            )
            header.mount(Label(artist, classes="queue-now-playing-artist"))
            header.display = True
        else:
            header.display = False

        # Build the queue table with upcoming tracks (everything except current).
        table = self.query_one("#queue-table", DataTable)
        table.clear()
        self._row_keys = []

        # Show all tracks; the current one gets a play indicator.
        if not tracks:
            table.display = False
            self.query_one("#queue-empty").display = True
        else:
            table.display = True
            self.query_one("#queue-empty").display = False

            for i, track in enumerate(tracks):
                title = track.get("title", "Unknown")
                artist = extract_artist(track)
                dur = extract_duration(track)
                dur_str = format_duration(dur) if dur else "--:--"
                indicator = "\u25b6" if i == current_index else str(i + 1)

                row_key = table.add_row(
                    indicator, title, artist, dur_str,
                    key=f"q_{i}",
                )
                self._row_keys.append(row_key)

        self.queue_length = len(tracks)
        self._update_footer()

    def _update_footer(self) -> None:
        """Update the footer bar with repeat, shuffle, and track count info."""
        queue = self.app.queue  # type: ignore[attr-defined]
        repeat = queue.repeat_mode
        shuffle = queue.shuffle_enabled
        count = queue.length

        repeat_label = {
            RepeatMode.OFF: "Off",
            RepeatMode.ALL: "All",
            RepeatMode.ONE: "One",
        }.get(repeat, "Off")

        shuffle_label = "On" if shuffle else "Off"
        footer_text = (
            f"Repeat: {repeat_label}  "
            f"Shuffle: {shuffle_label}  "
            f"Tracks: {count}"
        )

        try:
            footer = self.query_one("#queue-footer", Static)
            footer.update(footer_text)
        except Exception:
            pass

    # ── DataTable events ──────────────────────────────────────────────

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Jump to and play the selected track."""
        event.stop()
        idx = event.cursor_row
        queue = self.app.queue  # type: ignore[attr-defined]
        track = queue.jump_to(idx)
        if track:
            await self.app.play_track(track)  # type: ignore[attr-defined]
            self._refresh_queue()

    # ── Action handling ───────────────────────────────────────────────

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process vim-style navigation and queue management actions."""
        table = self.query_one("#queue-table", DataTable)
        queue = self.app.queue  # type: ignore[attr-defined]

        match action:
            case Action.GO_BACK:
                await self.app.navigate_to("back")  # type: ignore[attr-defined]

            case Action.MOVE_DOWN:
                for _ in range(count):
                    table.action_cursor_down()

            case Action.MOVE_UP:
                for _ in range(count):
                    table.action_cursor_up()

            case Action.PAGE_DOWN:
                table.action_scroll_down()

            case Action.PAGE_UP:
                table.action_scroll_up()

            case Action.GO_TOP:
                if table.row_count > 0:
                    table.move_cursor(row=0)

            case Action.GO_BOTTOM:
                if table.row_count > 0:
                    table.move_cursor(row=table.row_count - 1)

            case Action.SELECT:
                if table.cursor_row is not None:
                    idx = table.cursor_row
                    track = queue.jump_to(idx)
                    if track:
                        await self.app.play_track(track)  # type: ignore[attr-defined]
                        self._refresh_queue()

            # Remove selected track from queue (d key typically).
            case Action.TRACK_ACTIONS:
                self._remove_selected(table, queue)

            # Reorder: move track up (C-k).
            case Action.FOCUS_PREV if self._is_reorder_context():
                self._move_track(table, queue, direction=-1)

            # Reorder: move track down (C-j).
            case Action.FOCUS_NEXT if self._is_reorder_context():
                self._move_track(table, queue, direction=1)

            case Action.CYCLE_REPEAT:
                queue.cycle_repeat()
                self._update_footer()

            case Action.TOGGLE_SHUFFLE:
                queue.toggle_shuffle()
                self._refresh_queue()

    def _is_reorder_context(self) -> bool:
        """Always allow reorder in the queue page."""
        return True

    def _remove_selected(self, table: DataTable, queue: Any) -> None:
        """Remove the currently highlighted track from the queue."""
        if table.cursor_row is None:
            return
        idx = table.cursor_row
        if 0 <= idx < queue.length:
            queue.remove(idx)
            self._refresh_queue()
            # Keep cursor in bounds after removal.
            if table.row_count > 0:
                new_row = min(idx, table.row_count - 1)
                table.move_cursor(row=new_row)

    def _move_track(self, table: DataTable, queue: Any, direction: int) -> None:
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
