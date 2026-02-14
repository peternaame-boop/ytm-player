"""Liked Songs page showing the user's liked music."""

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
from ytm_player.utils.formatting import (
    extract_artist,
    extract_duration,
    format_duration,
    normalize_tracks,
)

logger = logging.getLogger(__name__)


class LikedSongsPage(Widget):
    """Displays the user's Liked Music playlist."""

    DEFAULT_CSS = """
    LikedSongsPage {
        layout: vertical;
        width: 1fr;
        height: 1fr;
    }
    .liked-header {
        height: auto;
        max-height: 3;
        padding: 1 2;
        background: $surface;
    }
    .liked-header-title {
        text-style: bold;
        color: $primary;
    }
    .liked-table {
        height: 1fr;
        width: 1fr;
    }
    .liked-table > .datatable--cursor {
        background: $selected-item;
    }
    .liked-footer {
        height: 1;
        padding: 0 2;
        color: $text-muted;
        dock: bottom;
    }
    .liked-loading {
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
            Label("Liked Songs", classes="liked-header-title"),
            id="liked-header",
            classes="liked-header",
        )
        yield Label("Loading liked songs...", id="liked-loading", classes="liked-loading")
        yield DataTable(
            cursor_type="row",
            zebra_stripes=True,
            id="liked-table",
            classes="liked-table",
        )
        yield Static("", id="liked-footer", classes="liked-footer")

    def on_mount(self) -> None:
        table = self.query_one("#liked-table", DataTable)
        ui = get_settings().ui

        def w(v: int) -> int | None:
            return v if v > 0 else None

        table.add_column("#", width=w(ui.col_index), key="index")
        table.add_column("Title", width=w(ui.col_title), key="title")
        table.add_column("Artist", width=w(ui.col_artist), key="artist")
        table.add_column("Duration", width=w(ui.col_duration), key="duration")
        table.display = False
        self.run_worker(self._load_liked_songs(), group="liked-load")

    async def _load_liked_songs(self) -> None:
        ytmusic = self.app.ytmusic  # type: ignore[attr-defined]
        if not ytmusic:
            self.query_one("#liked-loading", Label).update("YouTube Music not connected.")
            return

        try:
            raw_tracks = await ytmusic.get_liked_songs(limit=200)
            self._tracks = normalize_tracks(raw_tracks)
        except Exception:
            logger.exception("Failed to load liked songs")
            self._tracks = []

        self._refresh_table()

    def _refresh_table(self) -> None:
        table = self.query_one("#liked-table", DataTable)
        loading = self.query_one("#liked-loading", Label)
        table.clear()
        self._row_keys = []

        if not self._tracks:
            table.display = False
            loading.update("No liked songs found.")
            loading.display = True
            return

        loading.display = False
        table.display = True

        for i, track in enumerate(self._tracks):
            title = track.get("title", "Unknown")
            artist = extract_artist(track)
            dur = extract_duration(track)
            dur_str = format_duration(dur) if dur else "--:--"
            row_key = table.add_row(str(i + 1), title, artist, dur_str, key=f"liked_{i}")
            self._row_keys.append(row_key)

        self.track_count = len(self._tracks)
        footer = self.query_one("#liked-footer", Static)
        footer.update(f"{len(self._tracks)} liked songs")

    async def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        event.stop()
        idx = event.cursor_row
        if 0 <= idx < len(self._tracks):
            queue = self.app.queue  # type: ignore[attr-defined]
            queue.clear()
            queue.add_multiple(self._tracks)
            queue.jump_to_real(idx)
            await self.app.play_track(self._tracks[idx])  # type: ignore[attr-defined]

    async def handle_action(self, action: Action, count: int = 1) -> None:
        table = self.query_one("#liked-table", DataTable)

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
                    queue = self.app.queue  # type: ignore[attr-defined]
                    queue.clear()
                    queue.add_multiple(self._tracks)
                    queue.jump_to_real(table.cursor_row)
                    await self.app.play_track(self._tracks[table.cursor_row])  # type: ignore[attr-defined]
            case Action.ADD_TO_QUEUE:
                if table.cursor_row is not None and 0 <= table.cursor_row < len(self._tracks):
                    queue = self.app.queue  # type: ignore[attr-defined]
                    queue.add(self._tracks[table.cursor_row])
                    self.app.notify("Added to queue", timeout=2)
