"""Reusable track listing table widget."""

from __future__ import annotations

import logging
from typing import Any

from textual.events import Click, MouseDown, MouseMove, MouseUp
from textual.geometry import Size
from textual.message import Message
from textual.timer import Timer
from textual.widgets import DataTable
from textual.widgets.data_table import Column, RowKey

from ytm_player.config import Action
from ytm_player.config.settings import get_settings
from ytm_player.ui.selection_info_bar import SelectionChanged
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

    class FilterRequested(Message):
        """Emitted when the user presses / to start filtering."""

    class FilterClosed(Message):
        """Emitted when the filter is dismissed."""

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
            cursor_foreground_priority="renderable",
            name=name,
            id=id,
            classes=classes,
        )
        self._show_index = show_index
        self._show_album = show_album
        self._all_tracks: list[dict] = []
        self._tracks: list[dict] = []
        self._filtered_map: list[int] = []
        self._row_keys: list[RowKey] = []
        self._playing_video_id: str | None = None
        self._playing_index: int | None = None
        self._right_clicked: bool = False
        self._suppress_select_on_refocus: bool = False
        self._sort_column: str | None = None
        self._sort_reverse: bool = False
        self._filter_text: str = ""
        self._filter_active: bool = False
        self._filter_timer: Timer | None = None
        # Column resize drag state.
        self._resize_col: Column | None = None
        self._resize_start_x: int = 0
        self._resize_start_width: int = 0
        self._title_manual_width: bool = False
        # Set up columns at construction time, not on_mount. Otherwise a
        # caller that mounts the table and immediately calls load_tracks()
        # synchronously (e.g. context._build_artist's nested-mount chain)
        # hits add_row() before on_mount runs, and add_row raises because
        # there are 0 columns.
        self._setup_columns()

    @property
    def tracks(self) -> list[dict]:
        """Return ALL tracks regardless of filter (for queue integration)."""
        return list(self._all_tracks)

    @property
    def visible_tracks(self) -> list[dict]:
        """Return only visible (possibly filtered) tracks."""
        return list(self._tracks)

    @property
    def track_count(self) -> int:
        return len(self._all_tracks)

    @property
    def selected_track(self) -> dict | None:
        """Return the track dict for the currently highlighted row."""
        if self.cursor_row is not None and 0 <= self.cursor_row < len(self._tracks):
            return self._tracks[self.cursor_row]
        return None

    # -- Setup ------------------------------------------------------------

    def on_mount(self) -> None:
        # Pick up the currently-playing video_id from the app's player so
        # the playing-row highlight survives navigating away and back.
        try:
            player = getattr(self.app, "player", None)
            current = player.current_track if player else None
            video_id = current.get("video_id", "") if current else ""
            if video_id:
                self._playing_video_id = video_id
                # _highlight_playing runs after columns/rows are populated;
                # load_tracks will trigger it. If the table is already empty,
                # this is a no-op until tracks land.
                self._highlight_playing()
        except Exception:
            logger.debug("Failed to pick up current playing track on mount", exc_info=True)

    def _setup_columns(self) -> None:
        """Add the standard track table columns."""
        ui = get_settings().ui

        def w(v: int) -> int | None:
            return v if v > 0 else None

        if self._show_index:
            self.add_column("#", width=w(ui.col_index), key="index")
        self.add_column("Title", width=w(ui.col_title), key="title")
        self.add_column("Artist", width=w(ui.col_artist), key="artist")
        if self._show_album:
            self.add_column("Album", width=w(ui.col_album), key="album")
        self.add_column("Duration", width=w(ui.col_duration), key="duration")

    # -- Data loading -----------------------------------------------------

    def load_tracks(self, tracks: list[dict]) -> None:
        """Replace the table contents with a new list of tracks."""
        self.clear()
        # Stamp each track with its original playlist position.
        self._all_tracks = []
        self._tracks = []
        self._filtered_map = []
        for i, track in enumerate(tracks):
            t = dict(track)
            t["_original_index"] = i
            self._all_tracks.append(t)
            self._tracks.append(t)
            self._filtered_map.append(i)
        self._row_keys = []
        self._playing_index = None
        self._sort_column = None
        self._sort_reverse = False
        self._filter_text = ""
        self._filter_active = False

        for i, track in enumerate(self._tracks):
            row_key = self._add_track_row(i, track)
            self._row_keys.append(row_key)

        # Reflow the title column now that rows (with row labels) exist
        # — the row-label column eats ~3 cells, so the initial column
        # widths from settings would push the rightmost column off-screen.
        self._fill_title_column()
        self._invalidate_table()

        self._highlight_playing()

    def append_tracks(self, tracks: list[dict]) -> None:
        """Append additional tracks without clearing existing ones."""
        start_idx = len(self._all_tracks)
        for i, track in enumerate(tracks, start=start_idx):
            t = dict(track)
            t["_original_index"] = i
            self._all_tracks.append(t)
            # If filter is active, only add matching tracks to visible table.
            if self._filter_active and self._filter_text:
                if not self._matches_filter(t, self._filter_text):
                    continue
            self._tracks.append(t)
            self._filtered_map.append(i)
            row_key = self._add_track_row(len(self._tracks) - 1, t)
            self._row_keys.append(row_key)
        # Same reflow as load_tracks — the row-label column may have been
        # 0-width if append_tracks fires before any rows existed.
        self._fill_title_column()
        self._invalidate_table()

    def remove_track(self, video_id: str, set_video_id: str = "") -> bool:
        """Remove a track from the table.

        When *set_video_id* is provided it is used as the primary key so that
        duplicate tracks (same ``video_id``, different ``setVideoId``) are
        removed correctly.  Falls back to matching by ``video_id`` alone when
        *set_video_id* is empty.

        Returns ``True`` if the track was found and removed.
        """

        def _matches(t: dict) -> bool:
            if set_video_id:
                return t.get("setVideoId") == set_video_id
            return t.get("video_id") == video_id

        # Find in visible tracks first (handles filtered view).
        for visible_idx, track in enumerate(self._tracks):
            if _matches(track):
                # Remove from DataTable.
                row_key = self._row_keys[visible_idx]
                try:
                    self.remove_row(row_key)
                except Exception:
                    logger.exception("Failed to remove row %r from table", row_key)

                # Remove from visible list.
                self._tracks.pop(visible_idx)
                self._row_keys.pop(visible_idx)

                # Remove from all tracks.
                for all_idx, t in enumerate(self._all_tracks):
                    if _matches(t):
                        self._all_tracks.pop(all_idx)
                        # Rebuild filtered map from scratch — simplest correct approach.
                        self._filtered_map = [
                            i for i, trk in enumerate(self._all_tracks) if trk in self._tracks
                        ]
                        break

                # Re-number all remaining tracks so the # column stays
                # contiguous after the removal.
                for new_idx, t in enumerate(self._all_tracks):
                    t["_original_index"] = new_idx

                # Refresh the # cell for every visible row that shifted.
                for vis_idx, (rk, t) in enumerate(zip(self._row_keys, self._tracks)):
                    try:
                        self.update_cell(rk, "index", str(t["_original_index"] + 1))
                    except Exception:
                        pass

                self._fill_title_column()
                self._invalidate_table()
                return True
        return False

    def _add_track_row(self, index: int, track: dict) -> RowKey:
        """Add a single track as a row in the table."""
        title = track.get("title", "Unknown")
        artist = extract_artist(track)
        album = track.get("album") or ""
        duration = extract_duration(track)

        from ytm_player.utils.bidi import isolate_bidi, reorder_rtl_line

        cells: list[str | int] = []
        if self._show_index:
            # Always show original playlist position, not current row number.
            orig = track.get("_original_index", index)
            cells.append(str(orig + 1))
        cells.append(isolate_bidi(reorder_rtl_line(title)))
        cells.append(isolate_bidi(reorder_rtl_line(artist)))
        if self._show_album:
            cells.append(isolate_bidi(reorder_rtl_line(album)))
        cells.append(format_duration(duration) if duration else "--:--")

        video_id = track.get("video_id", f"row_{index}")
        # Pass label=" " on every row so Textual reserves the row-label
        # column once any row has a non-None label. _highlight_playing
        # mutates this slot to show ▶ on the playing row.
        return self.add_row(*cells, key=f"{video_id}_{index}", label=" ")

    # -- Playing state ----------------------------------------------------

    def set_playing(self, video_id: str | None) -> None:
        """Mark a track as currently playing (updates visual indicator)."""
        self._playing_video_id = video_id
        self._highlight_playing()

    def _jump_to_current(self) -> None:
        """Move cursor to the currently playing track if visible."""
        if not self._tracks:
            return
        # Prefer the app's current track over our cached _playing_video_id
        # because a freshly-mounted page won't have received set_playing yet.
        video_id = self._playing_video_id
        try:
            queue = self.app.queue  # type: ignore[attr-defined]
            current = queue.current_track if queue else None
            if current and current.get("video_id"):
                video_id = current["video_id"]
        except Exception:
            pass
        if not video_id:
            return
        for i, track in enumerate(self._tracks):
            if track.get("video_id") == video_id:
                self.move_cursor(row=i)
                return

    def _highlight_playing(self) -> None:
        """Style the now-playing row across its full width.

        Only touches the previously-playing and newly-playing rows
        instead of iterating every row. Every cell of the playing row
        gets bold + accent-color styling so the row stays visually
        distinct even when the cursor is elsewhere \u2014 without competing
        with the cursor-row CSS background.
        """
        if not self._show_index:
            return

        from rich.text import Text

        from ytm_player.utils.bidi import isolate_bidi, reorder_rtl_line
        from ytm_player.utils.formatting import (
            extract_artist as _extract_artist,
        )
        from ytm_player.utils.formatting import (
            extract_duration as _extract_duration,
        )
        from ytm_player.utils.formatting import (
            format_duration as _format_duration,
        )

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

        def _plain_cells(track: dict, row_index: int) -> dict[str, Any]:
            """Restore-cells: original (un-styled) values for a row."""
            title = track.get("title", "Unknown")
            artist = _extract_artist(track)
            album = track.get("album") or ""
            duration = _extract_duration(track)
            cells: dict[str, Any] = {}
            cells["index"] = str(track.get("_original_index", row_index) + 1)
            cells["title"] = isolate_bidi(reorder_rtl_line(title))
            cells["artist"] = isolate_bidi(reorder_rtl_line(artist))
            if self._show_album:
                cells["album"] = isolate_bidi(reorder_rtl_line(album))
            cells["duration"] = _format_duration(duration) if duration else "--:--"
            return cells

        def _styled_cells(track: dict, row_index: int, style: str) -> dict[str, Any]:
            """Active-cells: same data values wrapped in Rich Text with *style*.

            Unlike before, we do NOT overload the # column with a play
            glyph — the playing indicator now lives in Textual's row-label
            column (left of #). The # column keeps its original number.
            """
            plain = _plain_cells(track, row_index)
            return {key: Text(value, style=style) for key, value in plain.items()}

        def _set_row_label(row_key: RowKey, label_text: Text) -> None:
            """Mutate Textual's per-row label slot. Internal access
            wrapped so any breaking change degrades to a debug log."""
            try:
                row = self.rows[row_key]
                row.label = label_text
                self._update_count += 1
                self.refresh()
            except Exception:
                logger.debug("Failed to set row label", exc_info=True)

        # Resolve the theme's text color for the ▶ glyph. Normalize to
        # #rrggbb so Rich always parses (Textual may emit rgb(...) form).
        from textual.color import Color

        from ytm_player.ui.theme import get_theme

        # Restore the old row: plain data cells + blank label.
        if old_index is not None and old_index < len(self._row_keys):
            row_key = self._row_keys[old_index]
            try:
                cells = _plain_cells(self._tracks[old_index], old_index)
                for col_key, value in cells.items():
                    self.update_cell(row_key, col_key, value)
            except Exception:
                logger.debug("Failed to restore row %d cells", old_index, exc_info=True)
            _set_row_label(row_key, Text(" "))

        # Mark the new row: bold (no color) on data cells + ▶ label.
        # Why no color: the user's theme can have $primary close to
        # $selected-item (the cursor bg), so any colored foreground on the
        # playing row's data cells produces unreadable monochrome blocks
        # when the cursor lands on it. The ▶ glyph in the row-label column
        # is the unambiguous primary signal — it lives in its own render
        # path so it's always visible. Bold on the data cells is the
        # secondary cue for at-a-glance recognition without color clash.
        if new_index is not None and new_index < len(self._row_keys):
            row_key = self._row_keys[new_index]
            try:
                cells = _styled_cells(self._tracks[new_index], new_index, "bold")
                for col_key, value in cells.items():
                    self.update_cell(row_key, col_key, value)
            except Exception:
                logger.debug("Failed to style row %d cells", new_index, exc_info=True)
            try:
                text_hex = Color.parse(get_theme().text or "#ffffff").hex
            except Exception:
                text_hex = "#ffffff"
            _set_row_label(row_key, Text("▶", style=f"bold {text_hex}"))

        self._playing_index = new_index

    # -- Column resize (drag header border) ------------------------------

    def _invalidate_table(self) -> None:
        """Clear render caches and update virtual size after column changes."""
        if hasattr(self, "_clear_caches"):
            self._clear_caches()
        # Recalculate virtual_size so the scrollbar reflects new widths.
        try:
            data_width = sum(col.get_render_width(self) for col in self.columns.values())
            label_w = (
                self._row_label_column_width if hasattr(self, "_row_label_column_width") else 0
            )
            total_width = data_width + label_w
            header_h = self.header_height if self.show_header else 0
            self.virtual_size = Size(total_width, self._total_row_height + header_h)
        except Exception:
            pass
        self.refresh()

    def _fill_title_column(self) -> None:
        """Expand the Title column to fill any remaining table width."""
        if self._resize_col is not None or self._title_manual_width:
            return
        if self.size.width == 0:
            return
        title_col = next((c for c in self.ordered_columns if c.key == "title"), None)
        if title_col is None:
            return

        available = self.size.width - self._row_label_column_width
        used = sum(col.get_render_width(self) for col in self.ordered_columns if col.key != "title")
        remaining = available - used - 2 * self.cell_padding
        if remaining > 10:
            title_col.width = remaining
            title_col.auto_width = False

    def _column_at_edge(self, x: int) -> Column | None:
        """Return the Column whose right edge is near *x*, or None."""
        edge = self._row_label_column_width
        for col in self.ordered_columns:
            edge += col.get_render_width(self)
            if abs(x - edge) <= 1:
                return col
        return None

    def _column_at_x(self, x: int) -> Column | None:
        """Return the Column whose body contains *x*, or None."""
        edge = self._row_label_column_width
        for col in self.ordered_columns:
            w = col.get_render_width(self)
            if edge <= x < edge + w:
                return col
            edge += w
        return None

    _SORTABLE_KEYS = {"index", "title", "artist", "album", "duration"}

    def on_mouse_down(self, event: MouseDown) -> None:
        """Start column resize on header edge drag."""
        if event.button != 1:
            return
        if event.y != 0 or not self.show_header:
            return
        scroll_x = event.x + int(self.scroll_x)
        col = self._column_at_edge(scroll_x)
        if col is not None:
            event.stop()
            event.prevent_default()
            self._resize_col = col
            self._resize_start_x = event.screen_x
            self._resize_start_width = col.get_render_width(self)
            self.capture_mouse()

    def on_mouse_move(self, event: MouseMove) -> None:
        """Resize column while dragging."""
        if self._resize_col is None:
            return
        event.stop()
        self.suppress_click()
        delta = event.screen_x - self._resize_start_x
        padding = 2 * self.cell_padding
        new_width = max(3, self._resize_start_width + delta - padding)
        self._resize_col.width = new_width
        self._resize_col.auto_width = False
        self._fill_title_column()
        self._invalidate_table()

    def on_mouse_up(self, event: MouseUp) -> None:
        """End column resize."""
        if self._resize_col is not None:
            col_key = getattr(self._resize_col.key, "value", self._resize_col.key)
            if col_key == "title":
                self._title_manual_width = True
            self._resize_col = None
            self.release_mouse()
            self.suppress_click()
            event.stop()
            self._fill_title_column()
            self._invalidate_table()

    def on_resize(self, event: object) -> None:
        """Re-fill title column when widget is resized."""
        self._title_manual_width = False
        self._fill_title_column()
        self._invalidate_table()

    def on_blur(self) -> None:
        """Clean up drag state if widget loses focus."""
        if self._resize_col is not None:
            self._resize_col = None
            self.release_mouse()

    # -- Event handlers ---------------------------------------------------

    def on_data_table_row_selected(self, event: DataTable.RowSelected) -> None:
        """Forward track selection as a TrackSelected message."""
        if self._right_clicked:
            self._right_clicked = False
            return
        # Suppress the spurious RowSelected that fires when a modal popup
        # (e.g. ActionsPopup) dismisses and focus returns to this table.
        # The flag is set on right-click and consumed here once.
        if self._suppress_select_on_refocus:
            self._suppress_select_on_refocus = False
            return
        row_idx = event.cursor_row
        if 0 <= row_idx < len(self._tracks):
            original_idx = self._filtered_map[row_idx] if self._filtered_map else row_idx
            self.post_message(self.TrackSelected(self._tracks[row_idx], original_idx))

    def on_data_table_row_highlighted(self, event: DataTable.RowHighlighted) -> None:
        """Forward row highlight as a TrackHighlighted message and update SelectionInfoBar.

        Only posts SelectionChanged when this table is actually focused —
        otherwise a freshly-mounted table fires RowHighlighted at row 0
        on init and stomps the sidebar's selection in the info bar.
        """
        row_idx = event.cursor_row
        track = self._tracks[row_idx] if 0 <= row_idx < len(self._tracks) else None
        self.post_message(self.TrackHighlighted(track, row_idx))

        if not self.has_focus:
            return

        try:
            if track is None:
                self.post_message(SelectionChanged(""))
                return
            title = track.get("title", "") or ""
            artist = extract_artist(track) or ""
            label = f"{title} — {artist}" if artist else title
            self.post_message(SelectionChanged(label))
        except Exception:
            self.post_message(SelectionChanged(""))

    def on_focus(self) -> None:
        """When focus moves to this table, push the current row into the bar."""
        try:
            row_idx = self.cursor_row
            if row_idx is None or not (0 <= row_idx < len(self._tracks)):
                self.post_message(SelectionChanged(""))
                return
            track = self._tracks[row_idx]
            title = track.get("title", "") or ""
            artist = extract_artist(track) or ""
            label = f"{title} — {artist}" if artist else title
            self.post_message(SelectionChanged(label))
        except Exception:
            self.post_message(SelectionChanged(""))

    def on_click(self, event: Click) -> None:
        """Handle right-click to emit TrackRightClicked."""
        if event.button == 3:
            event.stop()
            event.prevent_default()
            self._right_clicked = True
            self._suppress_select_on_refocus = True
            meta = event.style.meta
            row_idx = meta.get("row") if meta else None
            if row_idx is not None and 0 <= row_idx < len(self._tracks):
                self.post_message(self.TrackRightClicked(self._tracks[row_idx], row_idx))

    def on_data_table_header_selected(self, event: DataTable.HeaderSelected) -> None:
        """Sort by the clicked column header."""
        event.stop()
        key = event.column_key.value
        if key in self._SORTABLE_KEYS:
            self.sort_by(key)

    # -- Filtering --------------------------------------------------------

    def apply_filter(self, query: str) -> None:
        """Filter visible rows by query. Empty string restores all tracks."""
        self._filter_text = query.strip().lower()
        if self._filter_timer is not None:
            try:
                self._filter_timer.stop()
            except Exception:
                pass
        if not self._filter_text:
            self._tracks = list(self._all_tracks)
            self._filtered_map = list(range(len(self._all_tracks)))
            self._reload_sorted()
            return
        self._filter_timer = self.set_timer(0.15, self._execute_filter)

    def _execute_filter(self) -> None:
        """Rebuild the table with only matching tracks (debounced)."""
        self._filter_timer = None
        self._tracks = []
        self._filtered_map = []
        for i, track in enumerate(self._all_tracks):
            if self._matches_filter(track, self._filter_text):
                self._tracks.append(track)
                self._filtered_map.append(i)
        self._reload_sorted()

    @staticmethod
    def _matches_filter(track: dict, query: str) -> bool:
        """Check if a track matches the filter (title + artist + album)."""
        title = (track.get("title") or "").lower()
        artist = extract_artist(track).lower()
        album = (track.get("album") or "").lower()
        return query in title or query in artist or query in album

    def show_filter(self) -> None:
        """Signal that filter mode should begin."""
        self._filter_active = True
        self.post_message(self.FilterRequested())

    def clear_filter(self) -> None:
        """Remove the filter, restoring all tracks."""
        self._filter_text = ""
        self._filter_active = False
        self._tracks = list(self._all_tracks)
        self._filtered_map = list(range(len(self._all_tracks)))
        self._reload_sorted()
        self.post_message(self.FilterClosed())

    # -- Sorting ----------------------------------------------------------

    def sort_by(self, column: str) -> None:
        """Sort tracks by column. Toggles direction if the same column is sorted again."""
        if not self._tracks:
            return

        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False

        key_funcs = {
            "index": lambda t: t.get("_original_index", 0),
            "title": lambda t: (t.get("title") or "").lower(),
            "artist": lambda t: extract_artist(t).lower(),
            "album": lambda t: (t.get("album") or "").lower(),
            "duration": lambda t: extract_duration(t),
        }
        key_fn = key_funcs.get(column)
        if key_fn is None:
            return

        current_track = self.selected_track
        self._tracks.sort(key=key_fn, reverse=self._sort_reverse)
        self._filtered_map = [t["_original_index"] for t in self._tracks]
        self._reload_sorted()

        if current_track:
            vid = current_track.get("video_id")
            for i, t in enumerate(self._tracks):
                if t.get("video_id") == vid:
                    self.move_cursor(row=i)
                    break

    def _reload_sorted(self) -> None:
        """Rebuild table rows from the current _tracks order."""
        saved_scroll_x = self.scroll_x
        self.clear()
        self._row_keys = []
        self._playing_index = None
        for i, track in enumerate(self._tracks):
            row_key = self._add_track_row(i, track)
            self._row_keys.append(row_key)
        self._highlight_playing()
        self.scroll_x = saved_scroll_x

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
                    original_idx = (
                        self._filtered_map[self.cursor_row]
                        if self._filtered_map
                        else self.cursor_row
                    )
                    self.post_message(
                        self.TrackSelected(self._tracks[self.cursor_row], original_idx)
                    )
            case Action.FILTER:
                self.show_filter()
            case Action.JUMP_TO_CURRENT:
                self._jump_to_current()
            case Action.SORT_TITLE:
                self.sort_by("title")
            case Action.SORT_ARTIST:
                self.sort_by("artist")
            case Action.SORT_ALBUM:
                self.sort_by("album")
            case Action.SORT_DURATION:
                self.sort_by("duration")
            case Action.REVERSE_SORT:
                if self._sort_column and self._tracks:
                    self._sort_reverse = not self._sort_reverse
                    current_track = self.selected_track
                    self._tracks.reverse()
                    self._filtered_map = [t["_original_index"] for t in self._tracks]
                    self._reload_sorted()
                    if current_track:
                        vid = current_track.get("video_id")
                        for i, t in enumerate(self._tracks):
                            if t.get("video_id") == vid:
                                self.move_cursor(row=i)
                                break
