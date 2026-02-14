"""Recently Played page showing play history from local SQLite database."""

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
from ytm_player.config.settings import get_settings
from ytm_player.utils.formatting import format_duration

logger = logging.getLogger(__name__)


class RecentlyPlayedPage(Widget):
    """Displays recently played tracks from the local history database."""

    DEFAULT_CSS = """
    RecentlyPlayedPage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .recent-header {
        height: auto;
        max-height: 3;
        padding: 1 2;
        background: $surface;
    }
    .recent-header-title {
        text-style: bold;
        color: $primary;
    }
    .recent-table {
        height: 1fr;
        width: 1fr;
    }
    .recent-table > .datatable--cursor {
        background: $selected-item;
    }
    .recent-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        dock: bottom;
    }
    .recent-loading {
        width: 1fr;
        height: 1fr;
        content-align: center middle;
        color: $text-muted;
    }
    """

    track_count: reactive[int] = reactive(0)

    def __init__(self, **kwargs: Any) -> None:
        super().__init__(**kwargs)
        self._row_keys: list[RowKey] = []
        self._tracks: list[dict] = []

    def compose(self) -> ComposeResult:
        yield Vertical(
            Label("Recently Played", classes="recent-header-title"),
            id="recent-header",
            classes="recent-header",
        )
        yield Label("Loading history...", id="recent-loading", classes="recent-loading")
        yield DataTable(
            cursor_type="row",
            zebra_stripes=True,
            id="recent-table",
            classes="recent-table",
        )
        yield Static("", id="recent-footer", classes="recent-footer")

    def on_mount(self) -> None:
        table = self.query_one("#recent-table", DataTable)
        ui = get_settings().ui

        def w(v: int) -> int | None:
            return v if v > 0 else None

        table.add_column("#", width=w(ui.col_index), key="index")
        table.add_column("Title", width=w(ui.col_title), key="title")
        table.add_column("Artist", width=w(ui.col_artist), key="artist")
        table.add_column("Duration", width=w(ui.col_duration), key="duration")
        table.display = False
        self.run_worker(self._load_history(), group="recent-load")

    async def _load_history(self) -> None:
        history = self.app.history  # type: ignore[attr-defined]
        if not history:
            self.query_one("#recent-loading", Label).update("History not available.")
            return

        try:
            self._tracks = await history.get_recently_played(limit=100)
        except Exception:
            logger.exception("Failed to load play history")
            self._tracks = []

        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#recent-table", DataTable)
        loading = self.query_one("#recent-loading", Label)
        table.clear()
        self._row_keys = []

        if not self._tracks:
            table.display = False
            loading.update("No play history yet. Start listening!")
            loading.display = True
            return

        loading.display = False
        table.display = True

        for i, track in enumerate(self._tracks):
            title = track.get("title", "Unknown")
            artist = track.get("artist", "Unknown")
            dur = track.get("duration_seconds", 0)
            dur_str = format_duration(dur) if dur else "--:--"
            row_key = table.add_row(str(i + 1), title, artist, dur_str, key=f"recent_{i}")
            self._row_keys.append(row_key)

        self.track_count = len(self._tracks)
        footer = self.query_one("#recent-footer", Static)
        footer.update(f"{len(self._tracks)} recently played tracks")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        idx = event.cursor_row
        if 0 <= idx < len(self._tracks):
            track = self._tracks[idx]
            queue = self.app.queue  # type: ignore[attr-defined]
            queue.add(track)
            queue.jump_to_real(queue.length - 1)
            await self.app.play_track(track)  # type: ignore[attr-defined]

    async def handle_action(self, action: Action, count: int = 1) -> None:
        table = self.query_one("#recent-table", DataTable)

        match action:
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
                if table.cursor_row is not None and 0 <= table.cursor_row < len(self._tracks):
                    track = self._tracks[table.cursor_row]
                    queue = self.app.queue  # type: ignore[attr-defined]
                    queue.add(track)
                    queue.jump_to_real(queue.length - 1)
                    await self.app.play_track(track)  # type: ignore[attr-defined]
            case Action.ADD_TO_QUEUE:
                if table.cursor_row is not None and 0 <= table.cursor_row < len(self._tracks):
                    queue = self.app.queue  # type: ignore[attr-defined]
                    queue.add(self._tracks[table.cursor_row])
                    self.app.notify("Added to queue", timeout=2)
