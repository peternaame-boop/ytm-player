"""Reusable track listing table widget."""

from __future__ import annotations

import logging
from collections import Counter
from collections.abc import Callable, Hashable, Iterable, Sequence
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

_MARK_GLYPH = "✓"

# Sort key per sortable column, shared by the visible sort and by
# ``marked_tracks`` (which orders the whole backing list the same way).
_SORT_KEYS: dict[str, Callable[[dict], Any]] = {
    "index": lambda t: t.get("_original_index", 0),
    "title": lambda t: (t.get("title") or "").lower(),
    "artist": lambda t: extract_artist(t).lower(),
    "album": lambda t: (t.get("album") or "").lower(),
    "duration": lambda t: extract_duration(t),
}


class TrackTable(DataTable):
    """A DataTable subclass for displaying lists of tracks.

    Columns: mark, #, Title, Artist, Album, Duration.

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

    Marks: rows can be marked for a bulk action (``v``/``V``/Escape, Ctrl-
    click, Shift-click, or a click on the mark column). A mark belongs to
    one occurrence — the row's position in the loaded list — not to a video
    ID, so two rows of the same track are marked independently. Marks live
    until the table is reloaded (``load_tracks``), cleared, or the page is
    left; ``refresh_tracks`` carries them across a background reload. While
    any row is marked the table draws its own status line underneath.
    """

    DEFAULT_CSS = """
    TrackTable {
        height: 1fr;
        width: 1fr;
    }
    TrackTable > .datatable--cursor {
        background: $selected-item;
    }
    TrackTable.-marked {
        border-bottom: hkey $accent;
        border-subtitle-align: left;
        border-subtitle-color: $text;
    }
    """

    class TrackSelected(Message):
        """Emitted when a track row is activated (Enter key)."""

        def __init__(self, track: dict, index: int) -> None:
            super().__init__()
            self.track = track
            self.index = index

    class TrackRightClicked(Message):
        """Emitted when a track row is right-clicked (title or non-specific column).

        *index* is the visible row and *track* the table's own copy of it
        (unlike ``TrackSelected``, whose index is the load-order one). The
        row's occurrence is snapshotted here, when the message is created:
        ``occurrence_key`` is the key the row was loaded with (see
        ``occurrence_key()``) and ``from_queue`` says whether that key is a
        queue entry id (``queue_entry_keys`` on the table). A handler runs
        later, possibly after the table re-rendered or was removed, so it
        must use these and not look the row up again. ``control`` is the table.
        """

        def __init__(self, table: TrackTable, track: dict, index: int) -> None:
            super().__init__()
            self.table = table
            self.track = track
            self.index = index
            original = track.get("_original_index")
            self.occurrence_key: Hashable | None = (
                table.occurrence_key(original) if isinstance(original, int) else None
            )
            self.from_queue: bool = table.queue_entry_keys

        @property
        def control(self) -> TrackTable:
            return self.table

    class ArtistRightClicked(Message):
        """Emitted when the Artist column of a track row is right-clicked."""

        def __init__(self, track: dict, index: int) -> None:
            super().__init__()
            self.track = track
            self.index = index

    class AlbumRightClicked(Message):
        """Emitted when the Album column of a track row is right-clicked."""

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
        queue_entry_keys: bool = False,
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
        # True on the Queue page only: its rows are loaded with QueueManager
        # entry ids as keys, so a row names one queue occurrence.
        self.queue_entry_keys = queue_entry_keys
        self._all_tracks: list[dict] = []
        self._tracks: list[dict] = []
        self._filtered_map: list[int] = []
        self._row_keys: list[RowKey] = []
        self._playing_video_id: str | None = None
        self._playing_index: int | None = None
        self._right_clicked: bool = False
        # Armed on right-click; only escalated to _suppress_select_on_refocus
        # once the table actually blurs (a modal took focus). Right-clicks
        # whose handler early-returns without opening a popup never blur, so
        # they no longer leave the suppress flag stale.
        self._popup_pending: bool = False
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
        # Selection state. Every index here is an ``_original_index`` into
        # ``_all_tracks`` (one occurrence), never a visible row number.
        self._marked: set[int] = set()
        self._anchor: int | None = None
        self._range_mode: bool = False
        self._range_base: frozenset[int] = frozenset()
        # Bumped whenever the marked set changes or the list is replaced;
        # a bulk action captures it when it starts and only clears the
        # marks on completion if it still matches.
        self._selection_generation: int = 0
        # One key per loaded track (load order) naming its occurrence for
        # ``refresh_tracks``; None when the list was loaded without keys.
        self._occurrence_keys: list[Hashable] | None = None
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

    @property
    def selected_original_index(self) -> int | None:
        """Return the highlighted row's index in the originally loaded order.

        ``cursor_row`` is a visible-row index that diverges from the load
        order once a sort or filter is active. Callers that mutate the
        backing collection (e.g. queue remove/move) must use this mapped
        index — the same mapping ``TrackSelected`` messages carry.
        """
        if self.cursor_row is not None and 0 <= self.cursor_row < len(self._tracks):
            return self._filtered_map[self.cursor_row] if self._filtered_map else self.cursor_row
        return None

    @property
    def marked_count(self) -> int:
        """Number of marked rows, hidden (filtered-out) ones included."""
        return len(self._marked)

    @property
    def hidden_marked_count(self) -> int:
        """Marked rows the active filter currently hides."""
        visible = set(self._filtered_map)
        return sum(1 for i in self._marked if i not in visible)

    @property
    def selection_generation(self) -> int:
        """Changes whenever the marked set changes or the list is replaced."""
        return self._selection_generation

    def occurrence_key(self, original_index: int) -> Hashable | None:
        """The key naming occurrence *original_index*, or None.

        Keys come from the ``keys`` the list was loaded with (queue entry ids
        on the Queue page); None when the list was loaded without keys or the
        index is out of range.
        """
        keys = self._occurrence_keys
        if keys is None or not 0 <= original_index < len(keys):
            return None
        return keys[original_index]

    def marked_tracks(self) -> list[dict]:
        """Marked tracks in the displayed order, hidden ones included.

        Under a sort the whole backing list is ordered by that sort, so a
        marked track the filter hides lands where it would show without
        the filter; otherwise the load order applies.
        """
        if not self._marked:
            return []
        ordered = list(self._all_tracks)
        key_fn = _SORT_KEYS.get(self._sort_column or "")
        if key_fn is not None:
            ordered.sort(key=key_fn, reverse=self._sort_reverse)
        return [t for t in ordered if t["_original_index"] in self._marked]

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

        # Mark column first: one cell, blank or a check mark.
        self.add_column(" ", width=1, key="mark")
        if self._show_index:
            self.add_column("#", width=w(ui.col_index), key="index")
        self.add_column("Title", width=w(ui.col_title), key="title")
        self.add_column("Artist", width=w(ui.col_artist), key="artist")
        if self._show_album:
            self.add_column("Album", width=w(ui.col_album), key="album")
        self.add_column("Duration", width=w(ui.col_duration), key="duration")

    # -- Data loading -----------------------------------------------------

    def load_tracks(self, tracks: list[dict], *, keys: Sequence[Hashable] | None = None) -> None:
        """Replace the table contents with a new list of tracks.

        This is the deliberate replacement: the sort, filter and every mark
        go. *keys*, when given, name each track's occurrence (one per
        track, in order) so a later ``refresh_tracks`` can carry marks
        across a background reload of the same list.
        """
        if keys is not None and len(keys) != len(tracks):
            raise ValueError("keys must have one entry per track")
        self._occurrence_keys = list(keys) if keys is not None else None
        self._reset_selection()
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
        self._update_mark_status()

    def refresh_tracks(self, tracks: list[dict], keys: Sequence[Hashable]) -> None:
        """Re-render the same list after a background change, keeping the view.

        For reloads the user didn't ask for (a play landing in Recently
        Played, a queue entry added from elsewhere): the active sort and
        filter, the cursor, the marks, the range anchor and the range
        baseline all carry over to the occurrences that survive. *keys*
        names each row's occurrence, one per track and unique, in the same
        scheme the previous load used — queue entry ids, or video IDs where
        the list holds each track once. A key that is missing, or that
        appears more than once, resolves nothing: the mark on it is dropped
        rather than moved to another row.

        Dropping a marked occurrence, or ending up with a different marked
        set, advances the selection generation so a bulk action started
        before the refresh won't clear marks it didn't submit. A refresh
        that keeps every marked occurrence leaves the generation alone.
        Use ``load_tracks`` for a deliberate replacement.
        """
        if len(keys) != len(tracks):
            raise ValueError("keys must have one entry per track")
        old_keys = self._occurrence_keys
        if old_keys is None or len(old_keys) != len(self._all_tracks):
            self.load_tracks(tracks, keys=keys)
            return

        old_counts = Counter(old_keys)

        def key_at(index: int) -> Hashable | None:
            key = old_keys[index]
            return key if old_counts[key] == 1 else None

        marked_keys = [key_at(i) for i in self._marked]
        base_keys = [key_at(i) for i in self._range_base]
        anchor_key = key_at(self._anchor) if self._anchor is not None else None
        cursor_index = self.selected_original_index
        cursor_key = key_at(cursor_index) if cursor_index is not None else None
        range_mode = self._range_mode
        sort_column, sort_reverse = self._sort_column, self._sort_reverse
        filter_text, filter_active = self._filter_text, self._filter_active
        generation = self._selection_generation

        self.load_tracks(tracks, keys=keys)

        new_counts = Counter(keys)
        position = {k: i for i, k in enumerate(keys) if new_counts[k] == 1}

        def resolve(candidates: Iterable[Hashable | None]) -> set[int]:
            return {position[k] for k in candidates if k is not None and k in position}

        # The view first, so the rows exist in their final order before
        # marks are painted and the cursor is placed.
        self._filter_text, self._filter_active = filter_text, filter_active
        self._sort_column, self._sort_reverse = sort_column, sort_reverse
        if filter_text or sort_column is not None:
            self._rebuild_view()

        resolved = resolve(marked_keys)
        dropped = len(resolved) < len(marked_keys)
        self._range_base = frozenset(resolve(base_keys))
        self._anchor = next(iter(resolve([anchor_key])), None)
        self._range_mode = range_mode and self._anchor is not None
        self._set_marks(resolved, bump=False)

        target = resolve([cursor_key])
        if target:
            try:
                self.move_cursor(row=self._filtered_map.index(next(iter(target))))
            except ValueError:
                pass
        if self._range_mode:
            self._apply_range(bump=False)

        same_selection = not dropped and self._marked == resolved
        self._selection_generation = generation if same_selection else generation + 1
        self._update_mark_status()

    def append_tracks(self, tracks: list[dict], *, keys: Sequence[Hashable] | None = None) -> None:
        """Append additional tracks without clearing existing ones.

        The new rows join the view as it is: filtered like the rest and, under
        an active sort, placed where the sort puts them (the whole view is
        rebuilt then, with the cursor kept on its occurrence). Marks are
        untouched either way. A keyed table stays keyed only when *keys*
        arrives with one entry per track; otherwise the keys are dropped and
        the next ``refresh_tracks`` reloads instead of carrying marks over.
        """
        if self._occurrence_keys is not None:
            if keys is not None and len(keys) == len(tracks):
                self._occurrence_keys.extend(keys)
            else:
                self._occurrence_keys = None
        start_idx = len(self._all_tracks)
        if self._sort_column is not None:
            for i, track in enumerate(tracks, start=start_idx):
                t = dict(track)
                t["_original_index"] = i
                self._all_tracks.append(t)
            current = self.selected_original_index
            self._rebuild_view()
            self._place_cursor_on(current)
            self._fill_title_column()
            self._invalidate_table()
            return
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
                        if self._occurrence_keys is not None and all_idx < len(
                            self._occurrence_keys
                        ):
                            self._occurrence_keys.pop(all_idx)
                        self._shift_selection_after_removal(all_idx)
                        break

                # Re-number all remaining tracks so the # column stays
                # contiguous after the removal.
                for new_idx, t in enumerate(self._all_tracks):
                    t["_original_index"] = new_idx

                # Rebuild the visible→original map in VISIBLE order. The
                # visible list may be sorted, so walking _all_tracks would
                # put the entries in the wrong slots.
                self._filtered_map = [t["_original_index"] for t in self._tracks]

                # Refresh the # cell for every visible row that shifted.
                for vis_idx, (rk, t) in enumerate(zip(self._row_keys, self._tracks)):
                    try:
                        self.update_cell(rk, "index", str(t["_original_index"] + 1))
                    except Exception:
                        pass

                self._fill_title_column()
                self._invalidate_table()
                self._update_mark_status()
                return True
        return False

    def _add_track_row(self, index: int, track: dict) -> RowKey:
        """Add a single track as a row in the table."""
        title = track.get("title", "Unknown")
        artist = extract_artist(track)
        album = track.get("album") or ""
        duration = extract_duration(track)

        from ytm_player.utils.bidi import isolate_bidi, reorder_rtl_line

        cells: list[str | int] = [self._mark_glyph(track.get("_original_index", index))]
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
            cells["mark"] = self._mark_glyph(track.get("_original_index", row_index))
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

    # -- Marks ------------------------------------------------------------

    def clear_marks(self) -> None:
        """Drop every mark, the anchor and range mode."""
        self._range_mode = False
        self._range_base = frozenset()
        self._anchor = None
        self._set_marks(set())

    def end_range_mode(self) -> None:
        """Leave range mode keeping the marks as they are; later movement won't extend them."""
        self._range_mode = False
        self._range_base = frozenset()

    def _reset_selection(self) -> None:
        """Forget the selection without repainting (the rows are being rebuilt)."""
        self._marked = set()
        self._anchor = None
        self._range_mode = False
        self._range_base = frozenset()
        self._selection_generation += 1

    def _mark_glyph(self, original: int) -> str:
        return _MARK_GLYPH if original in self._marked else " "

    def _set_marks(self, marks: set[int], *, bump: bool = True) -> None:
        """Make *marks* the marked set, repainting only the rows that changed."""
        changed = self._marked ^ marks
        if not changed:
            return
        self._marked = set(marks)
        if bump:
            self._selection_generation += 1
        self._paint_marks(changed)
        self._update_mark_status()

    def _paint_marks(self, originals: Iterable[int]) -> None:
        """Rewrite the mark cell of each visible row among *originals*."""
        row_of = {orig: row for row, orig in enumerate(self._filtered_map)}
        for original in originals:
            row = row_of.get(original)
            if row is None or row >= len(self._row_keys):
                continue
            try:
                self.update_cell(self._row_keys[row], "mark", self._mark_glyph(original))
            except Exception:
                logger.debug("Failed to paint mark on row %d", row, exc_info=True)

    def _toggle_mark(self, original: int) -> None:
        self._anchor = original
        self._set_marks(self._marked ^ {original})

    def _extend_to(self, original: int) -> None:
        """Shift-click: mark the displayed run from the anchor to *original*.

        Existing marks outside the run stay. With no anchor, or an anchor
        the filter hides, the row is marked and becomes the anchor — a run
        never reaches a row that isn't shown.
        """
        if self._anchor is None or self._anchor not in self._filtered_map:
            self._anchor = original
            self._set_marks(self._marked | {original})
            return
        self._set_marks(self._marked | self._run(self._anchor, original))

    def _run(self, start: int, end: int) -> set[int]:
        """Original indices of the visible rows from *start* to *end*, inclusive.

        A *start* the filter hides counts as no start: the run is *end*
        alone, so an invisible row is never marked through a run.
        """
        try:
            lo, hi = sorted((self._filtered_map.index(start), self._filtered_map.index(end)))
        except ValueError:
            return {end}
        return set(self._filtered_map[lo : hi + 1])

    def _apply_range(self, *, bump: bool = True) -> None:
        """Range mode: marks are the baseline plus the anchor-to-cursor run."""
        if self._anchor is None:
            return
        cursor = self.selected_original_index
        run = set() if cursor is None else self._run(self._anchor, cursor)
        self._set_marks(set(self._range_base) | run, bump=bump)

    def _shift_selection_after_removal(self, removed: int) -> None:
        """Keep marks on their rows when the occurrence *removed* leaves the list."""

        def shift(original: int) -> int:
            return original - 1 if original > removed else original

        if removed in self._marked:
            self._selection_generation += 1
        self._marked = {shift(i) for i in self._marked if i != removed}
        self._range_base = frozenset(shift(i) for i in self._range_base if i != removed)
        if self._anchor is not None:
            self._anchor = None if self._anchor == removed else shift(self._anchor)
        if self._anchor is None and self._range_mode:
            self._range_mode = False
            self._range_base = frozenset()

    def _update_mark_status(self) -> None:
        """Show the count on the table's own bottom line while anything is marked.

        The line is the table's border subtitle on a border that only
        exists while the ``-marked`` class is set, so it costs a row only
        then and doesn't depend on the optional selection-info bar.
        """
        count = len(self._marked)
        if count == 0:
            self.border_subtitle = ""
            self.remove_class("-marked")
            return
        hidden = self.hidden_marked_count
        text = f"{count} selected"
        if hidden:
            text += f" ({hidden} hidden)"
        self.border_subtitle = text + " · A: add · Esc: clear"
        self.add_class("-marked")

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
        # A right-click that actually opened a modal blurs the table; arm the
        # one-shot suppression so the spurious RowSelected on refocus is eaten.
        if self._popup_pending:
            self._popup_pending = False
            self._suppress_select_on_refocus = True

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
        # A genuine selection got through — drop any pending right-click intent
        # so it can't linger and arm suppression on a later, unrelated blur.
        self._popup_pending = False
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

        # Range mode follows the cursor: the marks are the baseline plus
        # whatever now lies between the anchor and the highlighted row.
        if self._range_mode:
            self._apply_range()

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
        """Selection gestures on left-click; column-specific messages on right-click.

        Ctrl-click toggles the row, Shift-click marks the run from the
        anchor to the row (displayed order, on top of existing marks) and a
        click on the mark column toggles the row. Each moves the highlight
        to that row and never starts playback: ``prevent_default`` keeps
        DataTable from moving the cursor itself and from posting the
        RowSelected that a click on the highlighted row would raise. Plain
        clicks fall through unchanged. Whether Ctrl-click or Shift-click
        arrives with its modifier is up to the terminal; ``v``/``V`` are the
        keyboard route.
        """
        if event.button == 1:
            meta = event.style.meta
            row_idx = meta.get("row") if meta else None
            if row_idx is None or not (0 <= row_idx < len(self._tracks)):
                return
            # DataTable tags the filler right of the last column as column 0
            # "out of bounds"; a plain click there is a row click, not a
            # click on the mark column.
            on_mark_column = meta.get("column") == 0 and not meta.get("out_of_bounds")
            if not (event.ctrl or event.shift or on_mark_column):
                return
            event.stop()
            event.prevent_default()
            self._range_mode = False
            self._range_base = frozenset()
            original = self._filtered_map[row_idx]
            if event.shift and not event.ctrl:
                self._extend_to(original)
            else:
                self._toggle_mark(original)
            self.move_cursor(row=row_idx)
            return
        if event.button == 3:
            event.stop()
            event.prevent_default()
            self._right_clicked = True
            self._popup_pending = True
            meta = event.style.meta
            row_idx = meta.get("row") if meta else None
            if row_idx is not None and 0 <= row_idx < len(self._tracks):
                track = self._tracks[row_idx]
                col = self._column_at_x(int(event.x + self.scroll_x))
                col_key = col.key.value if col and col.key else ""
                if col_key == "artist":
                    self.post_message(self.ArtistRightClicked(track, row_idx))
                elif col_key == "album":
                    self.post_message(self.AlbumRightClicked(track, row_idx))
                else:
                    self.post_message(self.TrackRightClicked(self, track, row_idx))

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
        self._filter_active = bool(self._filter_text)
        if self._filter_timer is not None:
            try:
                self._filter_timer.stop()
            except Exception:
                pass
        if not self._filter_text:
            self._rebuild_view()
            return
        self._filter_timer = self.set_timer(0.15, self._execute_filter)

    def _execute_filter(self) -> None:
        """Rebuild the table with only matching tracks (debounced)."""
        self._filter_timer = None
        self._rebuild_view()

    def _rebuild_view(self) -> None:
        """Recompute the visible rows from the backing list: the filter, then the sort.

        One path for filtering, sorting and ``refresh_tracks``, so the
        rows come out the same whichever order the user applied them in
        — a filter typed on a sorted list keeps the sort.
        """
        query = self._filter_text
        if query:
            self._tracks = [t for t in self._all_tracks if self._matches_filter(t, query)]
        else:
            self._tracks = list(self._all_tracks)
        key_fn = _SORT_KEYS.get(self._sort_column or "")
        if key_fn is not None:
            self._tracks.sort(key=key_fn, reverse=self._sort_reverse)
        self._filtered_map = [t["_original_index"] for t in self._tracks]
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
        self._rebuild_view()
        self.post_message(self.FilterClosed())

    # -- Sorting ----------------------------------------------------------

    def sort_by(self, column: str) -> None:
        """Sort tracks by column. Toggles direction if the same column is sorted again."""
        if not self._tracks:
            return

        if column not in _SORT_KEYS:
            return
        if self._sort_column == column:
            self._sort_reverse = not self._sort_reverse
        else:
            self._sort_column = column
            self._sort_reverse = False

        # Restore by occurrence, not video ID: with the same track listed
        # twice, the cursor stays on the copy it was on.
        current = self.selected_original_index
        self._rebuild_view()
        self._place_cursor_on(current)

    def clear_sort(self) -> None:
        """Drop the active sort, back to load order; a no-op when unsorted.

        The filter, the marks and the cursor's occurrence all stay.
        """
        if self._sort_column is None:
            return
        current = self.selected_original_index
        self._sort_column = None
        self._sort_reverse = False
        self._rebuild_view()
        self._place_cursor_on(current)

    def _place_cursor_on(self, original: int | None) -> None:
        """Move the cursor to the visible row of occurrence *original*, if shown."""
        if original is None:
            return
        try:
            self.move_cursor(row=self._filtered_map.index(original))
        except ValueError:
            pass

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
        self._update_mark_status()

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
            case Action.MARK_TOGGLE:
                # A range in progress ends first (its marks stay), then the
                # highlighted row toggles.
                self.end_range_mode()
                original = self.selected_original_index
                if original is not None:
                    self._toggle_mark(original)
            case Action.MARK_RANGE:
                if self._range_mode:
                    self.end_range_mode()
                    return
                original = self.selected_original_index
                if original is not None:
                    self._range_base = frozenset(self._marked)
                    self._anchor = original
                    self._range_mode = True
                    self._apply_range()
            case Action.MARK_CLEAR:
                self.clear_marks()
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
                    current = self.selected_original_index
                    self._rebuild_view()
                    self._place_cursor_on(current)
