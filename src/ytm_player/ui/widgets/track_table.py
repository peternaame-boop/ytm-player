"""Reusable track listing table widget."""

from __future__ import annotations

import logging

from textual.events import Click
from textual.message import Message
from textual.widgets import DataTable
from textual.widgets.data_table import RowKey

from ytm_player.config import Action
from ytm_player.utils.formatting import extract_artist, extract_duration, format_duration

logger = logging.getLogger(__name__)


class TrackTable(DataTable):
    """A DataTable subclass for displaying lists of tracks.

    Columns: #, Title, Artist, Album, Duration.

    Tracks are stored as dicts matching the queue/search result format:
        {
            "video_id": str,
            "title": str,
            "artist": str,
            "album": str | None,
            "duration": int | None,       # seconds
            "duration_seconds": int | None,
            ...
        }
    """

    DEFAULT_CSS = """
    TrackTable {
        height: 1fr;
        width: 1fr;
    }
    TrackTable > .datatable--cursor {
        background: $selected-item;
    }
    """

    class TrackSelected(Message):
        """Emitted when a track row is activated (Enter key)."""

        def __init__(self, track: dict, index: int) -> None:
            super().__init__()
            self.track = track
            self.index = index

    class TrackRightClicked(Message):
        """Emitted when a track row is right-clicked."""

        def __init__(self, track: dict, index: int) -> None:
            super().__init__()
            self.track = track
            self.index = index

    class TrackHighlighted(Message):
        """Emitted when the cursor moves to a different row."""

        def __init__(self, track: dict | None, index: int) -> None:
            super().__init__()
            self.track = track
            self.index = index

    def __init__(
        self,
        *,
        show_index: bool = True,
        show_album: bool = True,
        zebra_stripes: bool = True,
        name: str | None = None,
        id: str | None = None,
        classes: str | None = None,
    ) -> None:
        super().__init__(
            cursor_type="row",
            zebra_stripes=zebra_stripes,
            name=name,
            id=id,
            classes=classes,
        )
        self._show_index = show_index
        self._show_album = show_album
        self._tracks: list[dict] = []
        self._row_keys: list[RowKey] = []
        self._playing_video_id: str | None = None
        self._playing_index: int | None = None

    @property
    def tracks(self) -> list[dict]:
        return list(self._tracks)

    @property
    def track_count(self) -> int:
        return len(self._tracks)

    @property
    def selected_track(self) -> dict | None:
        """Return the track dict for the currently highlighted row."""
        if self.cursor_row is not None and 0 <= self.cursor_row < len(self._tracks):
            return self._tracks[self.cursor_row]
        return None

    # -- Setup ------------------------------------------------------------

    def on_mount(self) -> None:
        self._setup_columns()

    def _setup_columns(self) -> None:
        """Add the standard track table columns."""
        if self._show_index:
            self.add_column("#", width=4, key="index")
        self.add_column("Title", width=None, key="title")
        self.add_column("Artist", width=None, key="artist")
        if self._show_album:
            self.add_column("Album", width=None, key="album")
        self.add_column("Duration", width=8, key="duration")

    # -- Data loading -----------------------------------------------------

    def load_tracks(self, tracks: list[dict]) -> None:
        """Replace the table contents with a new list of tracks."""
        self.clear()
        self._tracks = list(tracks)
        self._row_keys = []
        self._playing_index = None

        for i, track in enumerate(self._tracks):
            row_key = self._add_track_row(i, track)
            self._row_keys.append(row_key)

        self._highlight_playing()

    def append_tracks(self, tracks: list[dict]) -> None:
        """Append additional tracks without clearing existing ones."""
        start_idx = len(self._tracks)
        self._tracks.extend(tracks)
        for i, track in enumerate(tracks, start=start_idx):
            row_key = self._add_track_row(i, track)
            self._row_keys.append(row_key)

    def _add_track_row(self, index: int, track: dict) -> RowKey:
        """Add a single track as a row in the table."""
        title = track.get("title", "Unknown")
        artist = extract_artist(track)
        album = track.get("album") or ""
        duration = extract_duration(track)

        cells: list[str | int] = []
        if self._show_index:
            cells.append(str(index + 1))
        cells.append(title)
        cells.append(artist)
        if self._show_album:
            cells.append(album)
        cells.append(format_duration(duration) if duration else "--:--")

        video_id = track.get("video_id", f"row_{index}")
        return self.add_row(*cells, key=f"{video_id}_{index}")

    # -- Playing state ----------------------------------------------------

    def set_playing(self, video_id: str | None) -> None:
        """Mark a track as currently playing (updates visual indicator)."""
        self._playing_video_id = video_id
        self._highlight_playing()

    def _highlight_playing(self) -> None:
        """Update the index column to show the playing indicator.

        Only touches the previously-playing and newly-playing rows
        instead of iterating every row.
        """
        if not self._show_index:
            return

        # Find the new playing index by matching video_id.
        new_index: int | None = None
        if self._playing_video_id is not None:
            for i, track in enumerate(self._tracks):
                if track.get("video_id") == self._playing_video_id:
                    new_index = i
                    break

        old_index = self._playing_index

        # Nothing changed -- skip the update.
        if old_index == new_index:
            return

        # Restore the old row's number indicator.
        if old_index is not None and old_index < len(self._row_keys):
            try:
                self.update_cell(self._row_keys[old_index], "index", str(old_index + 1))
            except Exception:
                logger.debug("Failed to restore row number for index %d", old_index, exc_info=True)

        # Set the play indicator on the new row.
        if new_index is not None and new_index < len(self._row_keys):
            try:
                self.update_cell(self._row_keys[new_index], "index", "\u25b6")
            except Exception:
                logger.debug(
                    "Failed to set playing indicator for index %d", new_index, exc_info=True
                )

        self._playing_index = new_index

    # -- Event handlers ---------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Forward track selection as a TrackSelected message."""
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self._tracks):
            self.post_message(self.TrackSelected(self._tracks[row_idx], row_idx))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Forward row highlight as a TrackHighlighted message."""
        row_idx = event.cursor_row
        track = self._tracks[row_idx] if 0 <= row_idx < len(self._tracks) else None
        self.post_message(self.TrackHighlighted(track, row_idx))

    def on_click(self, event: Click) -> None:
        """Handle right-click to emit TrackRightClicked."""
        if event.button == 3:
            event.stop()
            # The cursor row is updated by Textual before on_click fires,
            # so we can use the current cursor position.
            row_idx = self.cursor_row
            if row_idx is not None and 0 <= row_idx < len(self._tracks):
                self.post_message(self.TrackRightClicked(self._tracks[row_idx], row_idx))

    # -- Vim-style navigation ---------------------------------------------

    async def handle_action(self, action: Action, count: int = 1) -> None:
        """Process navigation actions dispatched from the app."""
        match action:
            case Action.MOVE_DOWN:
                for _ in range(count):
                    self.action_cursor_down()
            case Action.MOVE_UP:
                for _ in range(count):
                    self.action_cursor_up()
            case Action.PAGE_DOWN:
                self.action_scroll_down()
            case Action.PAGE_UP:
                self.action_scroll_up()
            case Action.GO_TOP:
                if self.row_count > 0:
                    self.move_cursor(row=0)
            case Action.GO_BOTTOM:
                if self.row_count > 0:
                    self.move_cursor(row=self.row_count - 1)
            case Action.SELECT:
                if self.cursor_row is not None and 0 <= self.cursor_row < len(self._tracks):
                    self.post_message(
                        self.TrackSelected(self._tracks[self.cursor_row], self.cursor_row)
                    )
