"""TrackTable marks: keyboard, mouse, sort/filter, removal, background refresh, status line.

A mark belongs to one occurrence (a loaded row), not to a video ID. The
table draws its own status line while anything is marked, independent of
the optional selection-info bar.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from ytm_player.config.keymap import Action
from ytm_player.ui.widgets.track_table import TrackTable

STATUS_TAIL = " · A: add · Esc: clear"


def _track(video_id: str, title: str, artist: str = "Artist", duration: int = 100) -> dict:
    return {
        "video_id": video_id,
        "title": title,
        "artist": artist,
        "artists": [{"name": artist, "id": "1"}],
        "album": "Album",
        "album_id": None,
        "duration": duration,
        "thumbnail_url": None,
        "is_video": False,
    }


def _tracks() -> list[dict]:
    # "b" appears twice: two occurrences of one video.
    return [
        _track("a", "Alpha"),
        _track("b", "Bravo"),
        _track("c", "Charlie"),
        _track("b", "Bravo again"),
        _track("d", "Delta"),
    ]


def _unique_tracks() -> list[dict]:
    return [_track(v, t) for v, t in zip("abcd", ["Alpha", "Bravo", "Charlie", "Delta"])]


def _keys(tracks: list[dict]) -> list[str]:
    return [t["video_id"] for t in tracks]


class _Host(App):
    """Minimal host providing the theme variable TrackTable's CSS needs."""

    def __init__(self) -> None:
        super().__init__()
        self.selected: list[str] = []

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        # Built here, not in __init__: add_column measures with the app console.
        self.table = TrackTable(id="t")
        yield self.table

    def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        self.selected.append(event.track["title"])


def _glyphs(table: TrackTable) -> str:
    """The mark column top to bottom, e.g. '✓  ✓ ' for rows 0 and 3 marked."""
    return "".join(str(table.get_row_at(i)[0]) for i in range(table.row_count))


def _titles(tracks: list[dict]) -> list[str]:
    return [t["title"] for t in tracks]


def _status(table: TrackTable) -> str:
    return str(table.border_subtitle)


def _x_of(table: TrackTable, key: str) -> int:
    """An x offset inside the column *key* (plus one cell of padding)."""
    for x in range(200):
        column = table._column_at_x(x)
        if column is not None and column.key is not None and column.key.value == key:
            return x + 1
    raise AssertionError(f"no column {key!r}")


# ── Keyboard ─────────────────────────────────────────────────────────


async def test_v_toggles_the_highlighted_row_and_shows_the_status_line():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        await table.handle_action(Action.MARK_TOGGLE)
        assert _glyphs(table) == "✓    "
        assert table.marked_count == 1
        assert table.has_class("-marked")
        assert _status(table) == "1 selected" + STATUS_TAIL

        await table.handle_action(Action.MARK_TOGGLE)
        assert _glyphs(table) == "     "
        assert not table.has_class("-marked")
        assert _status(table) == ""


async def test_marks_bind_to_occurrences_not_video_ids():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        table.move_cursor(row=1)
        await table.handle_action(Action.MARK_TOGGLE)
        assert _titles(table.marked_tracks()) == ["Bravo"]
        assert _glyphs(table) == " ✓   "

        table.move_cursor(row=3)
        await table.handle_action(Action.MARK_TOGGLE)
        assert _titles(table.marked_tracks()) == ["Bravo", "Bravo again"]

        table.move_cursor(row=1)
        await table.handle_action(Action.MARK_TOGGLE)
        assert _titles(table.marked_tracks()) == ["Bravo again"]


async def test_range_mode_follows_the_cursor_shrinks_and_keeps_the_baseline():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        table.move_cursor(row=4)
        await table.handle_action(Action.MARK_TOGGLE)  # baseline: Delta
        table.move_cursor(row=0)
        await pilot.pause()
        await table.handle_action(Action.MARK_RANGE)
        assert _glyphs(table) == "✓   ✓"

        await table.handle_action(Action.MOVE_DOWN, 2)
        await pilot.pause()
        assert _glyphs(table) == "✓✓✓ ✓"

        await table.handle_action(Action.MOVE_UP)
        await pilot.pause()
        assert _glyphs(table) == "✓✓  ✓"  # shrinks, Delta stays
        assert _status(table) == "3 selected" + STATUS_TAIL

        await table.handle_action(Action.MARK_RANGE)  # ends range mode, marks stay
        await table.handle_action(Action.MOVE_DOWN, 2)
        await pilot.pause()
        assert _glyphs(table) == "✓✓  ✓"
        assert table._range_mode is False


async def test_v_in_range_mode_ends_it_first_then_toggles_the_highlighted_row():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        await table.handle_action(Action.MARK_RANGE)
        await table.handle_action(Action.MOVE_DOWN)
        await pilot.pause()
        assert _glyphs(table) == "✓✓   "

        await table.handle_action(Action.MARK_TOGGLE)  # range ends, row 1 toggles off
        assert _glyphs(table) == "✓    "
        assert table._range_mode is False
        await table.handle_action(Action.MOVE_DOWN)
        await pilot.pause()
        assert _glyphs(table) == "✓    "


async def test_escape_clears_marks_anchor_and_range_mode():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        await table.handle_action(Action.MARK_RANGE)
        await table.handle_action(Action.MOVE_DOWN, 3)
        await pilot.pause()
        generation = table.selection_generation

        await table.handle_action(Action.MARK_CLEAR)
        assert _glyphs(table) == "     "
        assert table.marked_count == 0
        assert table._range_mode is False
        assert table._anchor is None
        assert not table.has_class("-marked")
        assert table.selection_generation != generation

        await table.handle_action(Action.MOVE_UP)
        await pilot.pause()
        assert _glyphs(table) == "     "


# ── Sort / filter / removal ──────────────────────────────────────────


async def test_sort_and_filter_keep_marks_and_count_hidden_ones():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        await table.handle_action(Action.MARK_TOGGLE)  # Alpha
        table.move_cursor(row=4)
        await table.handle_action(Action.MARK_TOGGLE)  # Delta

        table.sort_by("title")
        table.sort_by("title")  # reverse: Delta, Charlie, Bravo again, Bravo, Alpha
        await pilot.pause()
        assert _glyphs(table) == "✓   ✓"
        assert _titles(table.marked_tracks()) == ["Delta", "Alpha"]

        table.apply_filter("alp")
        await pilot.pause(0.3)
        assert _glyphs(table) == "✓"
        assert table.hidden_marked_count == 1
        assert _status(table) == "2 selected (1 hidden)" + STATUS_TAIL
        # Hidden marks are still submitted, in the displayed sort.
        assert _titles(table.marked_tracks()) == ["Delta", "Alpha"]

        table.clear_filter()
        await pilot.pause()
        assert _glyphs(table) == "✓   ✓"
        assert _status(table) == "2 selected" + STATUS_TAIL


async def test_remove_track_keeps_marks_on_their_rows_and_maps_visible_order():
    """Regression: after a removal the visible→original map is built in
    VISIBLE order. Under a sort, walking the backing list put entries in
    the wrong slots, so ``selected_original_index`` pointed at the wrong
    track and marks would have landed on the wrong rows."""
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        await table.handle_action(Action.MARK_TOGGLE)  # Alpha (orig 0)
        table.move_cursor(row=4)
        await table.handle_action(Action.MARK_TOGGLE)  # Delta (orig 4)
        table.sort_by("title")
        table.sort_by("title")  # Delta, Charlie, Bravo again, Bravo, Alpha
        await pilot.pause()

        assert table.remove_track("c") is True  # Charlie, orig 2
        await pilot.pause()

        assert _titles(table.visible_tracks) == ["Delta", "Bravo again", "Bravo", "Alpha"]
        assert table._filtered_map == [3, 2, 1, 0]
        assert _glyphs(table) == "✓  ✓"
        assert _titles(table.marked_tracks()) == ["Delta", "Alpha"]
        table.move_cursor(row=0)
        assert table.selected_original_index == 3
        assert table.selected_track is not None and table.selected_track["title"] == "Delta"


async def test_removing_a_marked_row_drops_its_mark_and_bumps_the_generation():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        table.move_cursor(row=2)
        await table.handle_action(Action.MARK_TOGGLE)  # Charlie
        table.move_cursor(row=4)
        await table.handle_action(Action.MARK_TOGGLE)  # Delta
        generation = table.selection_generation

        table.remove_track("c")

        assert _titles(table.marked_tracks()) == ["Delta"]
        assert _glyphs(table) == "   ✓"
        assert table.selection_generation != generation


# ── Mouse ────────────────────────────────────────────────────────────


@pytest.mark.parametrize("start,end", [(0, 2), (2, 0)])
@pytest.mark.parametrize("sorted_view", [False, True])
async def test_plain_click_then_shift_click_marks_inclusive_range(start, end, sorted_view):
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        if sorted_view:
            table.sort_by("title")
        await pilot.pause()
        x = _x_of(table, "title")

        await pilot.click(TrackTable, offset=(x, start + 1))
        assert table.marked_count == 0
        selected_before_shift = list(app.selected)
        await pilot.click(TrackTable, offset=(x, end + 1), shift=True)
        await pilot.pause()

        assert _glyphs(table) == "✓✓✓  "
        assert table.cursor_row == end
        assert app.selected == selected_before_shift


async def test_plain_click_reanchors_without_discarding_other_marks():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        table.move_cursor(row=4)
        await table.handle_action(Action.MARK_TOGGLE)
        await pilot.pause()
        x = _x_of(table, "title")

        await pilot.click(TrackTable, offset=(x, 1))
        await pilot.click(TrackTable, offset=(x, 3), shift=True)
        await pilot.pause()

        assert _glyphs(table) == "✓✓✓ ✓"


async def test_plain_click_during_keyboard_range_keeps_original_anchor():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await table.handle_action(Action.MARK_RANGE)
        await pilot.pause()

        await pilot.click(TrackTable, offset=(_x_of(table, "title"), 3))
        await pilot.pause()

        assert table._anchor == 0
        assert _glyphs(table) == "✓✓✓  "


async def test_ctrl_click_toggles_moves_the_highlight_and_never_selects():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        x = _x_of(table, "title")

        await pilot.click(TrackTable, offset=(x, 2), control=True)  # row 1
        await pilot.pause()
        assert _glyphs(table) == " ✓   "
        assert table.cursor_row == 1
        assert app.selected == []

        await pilot.click(TrackTable, offset=(x, 2), control=True)  # highlighted row
        await pilot.pause()
        assert _glyphs(table) == "     "
        assert app.selected == []


async def test_shift_click_marks_the_run_from_the_anchor_keeping_other_marks():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        x = _x_of(table, "title")

        table.move_cursor(row=4)
        await table.handle_action(Action.MARK_TOGGLE)  # Delta, outside the run
        await pilot.click(TrackTable, offset=(x, 1), control=True)  # row 0 = anchor
        await pilot.click(TrackTable, offset=(x, 3), shift=True)  # row 2
        await pilot.pause()

        assert _glyphs(table) == "✓✓✓ ✓"
        assert table.cursor_row == 2
        assert app.selected == []


async def test_shift_click_without_an_anchor_marks_the_row():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        await pilot.click(TrackTable, offset=(_x_of(table, "title"), 3), shift=True)
        await pilot.pause()
        assert _glyphs(table) == "  ✓  "
        assert table._anchor == 2


async def test_click_on_the_mark_column_toggles_without_selecting():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        await pilot.click(TrackTable, offset=(_x_of(table, "mark"), 1))  # highlighted row 0
        await pilot.pause()
        assert _glyphs(table) == "✓    "
        assert app.selected == []


async def _with_blank_strip(app: _Host, pilot) -> int:
    """Load four rows, narrow the Title column, return an x inside the blank strip.

    The user dragged the Title column narrower: auto-fill is off and a
    blank strip opens right of the last column (the drag gesture's state).
    DataTable tags a click there as column 0, out of bounds.
    """
    table = app.table
    table.load_tracks(_unique_tracks())
    await pilot.pause()
    title = next(c for c in table.ordered_columns if c.key.value == "title")
    title.width = 10
    title.auto_width = False
    table._title_manual_width = True
    table._invalidate_table()
    await pilot.pause()
    used = table._row_label_column_width + sum(
        c.get_render_width(table) for c in table.ordered_columns
    )
    assert used < table.size.width, "no blank strip"
    return used + 2


async def test_plain_click_in_the_blank_strip_is_a_row_click_not_a_mark():
    app = _Host()
    async with app.run_test(size=(120, 20)) as pilot:
        table = app.table
        x = await _with_blank_strip(app, pilot)

        # Row 0 is highlighted after the load and the strip counts as column
        # 0, so one click there lands on the cursor cell: it selects at once.
        await pilot.click(TrackTable, offset=(x, 1))
        await pilot.pause()
        assert _glyphs(table) == "    "
        assert table.marked_count == 0
        assert app.selected == ["Alpha"]


async def test_ctrl_click_in_the_blank_strip_still_marks_the_row():
    app = _Host()
    async with app.run_test(size=(120, 20)) as pilot:
        table = app.table
        x = await _with_blank_strip(app, pilot)

        await pilot.click(TrackTable, offset=(x, 2), control=True)  # row 1
        await pilot.pause()
        assert _glyphs(table) == " ✓  "
        assert app.selected == []


async def test_plain_click_on_a_data_cell_still_selects():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()

        # DataTable selects on a click at the cursor's cell: the first click
        # moves the cursor cell onto the title column, the second selects.
        x = _x_of(table, "title")
        await pilot.click(TrackTable, offset=(x, 1))
        await pilot.click(TrackTable, offset=(x, 1))
        await pilot.pause()
        assert app.selected == ["Alpha"]
        assert table.marked_count == 0


# ── Background refresh ───────────────────────────────────────────────


async def test_refresh_keeps_marks_on_their_occurrences_when_a_row_is_prepended():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        tracks = _unique_tracks()
        table.load_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        table.move_cursor(row=1)
        await table.handle_action(Action.MARK_TOGGLE)  # Bravo
        table.move_cursor(row=3)
        await table.handle_action(Action.MARK_TOGGLE)  # Delta
        table.move_cursor(row=2)  # cursor on Charlie
        generation = table.selection_generation

        new = [_track("n", "New"), *tracks]
        table.refresh_tracks(new, keys=_keys(new))
        await pilot.pause()

        assert _glyphs(table) == "  ✓ ✓"
        assert _titles(table.marked_tracks()) == ["Bravo", "Delta"]
        assert table.selected_track is not None and table.selected_track["title"] == "Charlie"
        assert table.selection_generation == generation


async def test_refresh_drops_a_vanished_mark_and_bumps_the_generation():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        tracks = _unique_tracks()
        table.load_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        table.move_cursor(row=1)
        await table.handle_action(Action.MARK_TOGGLE)  # Bravo
        table.move_cursor(row=3)
        await table.handle_action(Action.MARK_TOGGLE)  # Delta
        generation = table.selection_generation

        without_bravo = [t for t in tracks if t["video_id"] != "b"]
        table.refresh_tracks(without_bravo, keys=_keys(without_bravo))
        await pilot.pause()

        assert _titles(table.marked_tracks()) == ["Delta"]
        assert _glyphs(table) == "  ✓"
        assert table.selection_generation != generation


async def test_refresh_preserves_sort_filter_and_cursor():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        tracks = _unique_tracks()
        table.load_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        table.sort_by("title")
        table.sort_by("title")  # Delta, Charlie, Bravo, Alpha
        table.apply_filter("ha")  # matches only Charlie and Alpha
        await pilot.pause(0.3)
        assert _titles(table.visible_tracks) == ["Charlie", "Alpha"]
        await table.handle_action(Action.MARK_TOGGLE)  # Charlie
        table.move_cursor(row=1)  # cursor on Alpha

        new = [_track("n", "Noon"), *tracks]  # hidden by the filter
        table.refresh_tracks(new, keys=_keys(new))
        await pilot.pause()

        assert table._sort_column == "title" and table._sort_reverse is True
        assert table._filter_text == "ha"
        assert _titles(table.visible_tracks) == ["Charlie", "Alpha"]
        assert table.selected_track is not None and table.selected_track["title"] == "Alpha"
        assert _glyphs(table) == "✓ "
        assert _titles(table.marked_tracks()) == ["Charlie"]


async def test_refresh_during_range_mode_keeps_anchor_baseline_and_extends_after():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        tracks = _unique_tracks()
        table.load_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        table.move_cursor(row=3)
        await table.handle_action(Action.MARK_TOGGLE)  # baseline: Delta
        table.move_cursor(row=0)
        await pilot.pause()
        await table.handle_action(Action.MARK_RANGE)  # anchor Alpha
        await table.handle_action(Action.MOVE_DOWN)
        await pilot.pause()
        assert _glyphs(table) == "✓✓ ✓"
        generation = table.selection_generation

        new = [_track("n", "New"), *tracks]
        table.refresh_tracks(new, keys=_keys(new))
        await pilot.pause()

        assert table._range_mode is True
        assert _glyphs(table) == " ✓✓ ✓"
        assert table.selection_generation == generation

        await table.handle_action(Action.MOVE_DOWN)
        await pilot.pause()
        assert _glyphs(table) == " ✓✓✓✓"


async def test_refresh_with_ambiguous_keys_drops_only_the_ambiguous_mark():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        tracks = _tracks()  # "b" twice
        table.load_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        table.move_cursor(row=1)
        await table.handle_action(Action.MARK_TOGGLE)  # first "b"
        table.move_cursor(row=4)
        await table.handle_action(Action.MARK_TOGGLE)  # Delta

        table.refresh_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()

        assert _titles(table.marked_tracks()) == ["Delta"]


async def test_refresh_reloads_when_the_table_was_not_keyed():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        tracks = _unique_tracks()
        table.load_tracks(tracks)
        await pilot.pause()
        await table.handle_action(Action.MARK_TOGGLE)

        table.refresh_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        assert table.marked_count == 0


async def test_append_without_keys_makes_the_next_refresh_a_reload():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        tracks = _unique_tracks()
        table.load_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        await table.handle_action(Action.MARK_TOGGLE)
        table.append_tracks([_track("e", "Echo")])

        table.refresh_tracks(tracks, keys=_keys(tracks))
        await pilot.pause()
        assert table.marked_count == 0


async def test_load_tracks_clears_the_selection():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        await table.handle_action(Action.MARK_RANGE)
        await table.handle_action(Action.MOVE_DOWN)
        await pilot.pause()
        generation = table.selection_generation

        table.load_tracks(_tracks())
        await pilot.pause()
        assert table.marked_count == 0
        assert table._range_mode is False
        assert not table.has_class("-marked")
        assert table.selection_generation != generation


# ── Status line at the real page width ───────────────────────────────


class _BesideSidebar(App):
    """An 80-column screen split like the app: 30-column sidebar, 50-column page."""

    CSS = """
    Screen { layout: horizontal; }
    #sidebar { width: 30; height: 1fr; }
    #page { width: 1fr; height: 1fr; }
    """

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield Static("sidebar", id="sidebar")
        with Horizontal(id="page"):
            self.table = TrackTable(id="t", show_album=True)
            yield self.table


def _screen_lines(app: App) -> list[str]:
    return [strip.text for strip in app.screen._compositor.render_strips()]


async def test_status_line_is_visible_beside_the_sidebar_and_costs_a_row_only_while_marked():
    app = _BesideSidebar()
    async with app.run_test(size=(80, 16)) as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        assert table.size.width == 50
        assert not any("selected" in line for line in _screen_lines(app))
        rows_before = len(_screen_lines(app))

        await table.handle_action(Action.MARK_TOGGLE)  # Alpha
        table.move_cursor(row=4)
        await table.handle_action(Action.MARK_TOGGLE)  # Delta
        table.apply_filter("alp")
        await pilot.pause(0.3)

        lines = _screen_lines(app)
        assert len(lines) == rows_before
        status = [line for line in lines if "selected" in line]
        assert len(status) == 1
        text = "2 selected (1 hidden)" + STATUS_TAIL
        assert text in status[0]
        # The line lives under the table, right of the sidebar, and fits it.
        start = status[0].index(text)
        assert 30 <= start and start + len(text) <= 80
        header = next(line for line in lines if "Title" in line)
        assert "Duration" in header or "Durati" in header

        await table.handle_action(Action.MARK_CLEAR)
        await pilot.pause()
        assert not any("selected" in line for line in _screen_lines(app))


# ── Occurrence focus, hidden anchor, appends ─────────────────────────


async def test_sort_and_reverse_keep_the_cursor_on_the_same_occurrence():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        table.move_cursor(row=3)  # "Bravo again", the second copy of "b"

        table.sort_by("title")  # Alpha, Bravo, Bravo again, Charlie, Delta
        await pilot.pause()
        assert table.selected_original_index == 3
        assert table.cursor_row == 2

        await table.handle_action(Action.REVERSE_SORT)  # Delta, Charlie, Bravo again, Bravo, Alpha
        await pilot.pause()
        assert table.selected_original_index == 3
        assert table.cursor_row == 2


async def test_sort_during_range_mode_keeps_the_anchor_and_cursor_occurrences():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        table.move_cursor(row=3)  # "Bravo again"
        await pilot.pause()
        await table.handle_action(Action.MARK_RANGE)
        assert _glyphs(table) == "   ✓ "

        table.sort_by("title")  # Alpha, Bravo, Bravo again, Charlie, Delta
        await pilot.pause()
        assert table.selected_original_index == 3
        assert _glyphs(table) == "  ✓  "

        await table.handle_action(Action.MOVE_DOWN)  # onto Charlie
        await pilot.pause()
        assert _glyphs(table) == "  ✓✓ "


async def test_shift_click_with_a_hidden_anchor_marks_only_the_clicked_row():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        await table.handle_action(Action.MARK_TOGGLE)  # Alpha marked, anchor 0
        await table.handle_action(Action.MARK_TOGGLE)  # unmarked, anchor still 0
        table.apply_filter("bravo")  # Alpha hidden
        await pilot.pause(0.3)
        assert _titles(table.visible_tracks) == ["Bravo", "Bravo again"]

        await pilot.click(TrackTable, offset=(_x_of(table, "title"), 2), shift=True)
        await pilot.pause()

        assert _titles(table.marked_tracks()) == ["Bravo again"]
        assert table.hidden_marked_count == 0
        assert table._anchor == 3
        assert _status(table) == "1 selected" + STATUS_TAIL


async def test_append_under_a_sort_places_rows_where_the_sort_puts_them():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks([_track("a", "Alpha"), _track("c", "Charlie")])
        await pilot.pause()
        table.sort_by("title")
        await table.handle_action(Action.MARK_TOGGLE)  # Alpha
        table.move_cursor(row=1)  # Charlie
        await pilot.pause()

        table.append_tracks([_track("b", "Bravo")])
        await pilot.pause()

        assert _titles(table.visible_tracks) == ["Alpha", "Bravo", "Charlie"]
        assert _glyphs(table) == "✓  "
        assert table.selected_track is not None and table.selected_track["title"] == "Charlie"

        await table.handle_action(Action.MARK_TOGGLE)  # Charlie
        table.move_cursor(row=1)
        await table.handle_action(Action.MARK_TOGGLE)  # Bravo
        # Submitted order is the displayed order.
        assert _titles(table.marked_tracks()) == _titles(table.visible_tracks)


async def test_append_without_a_sort_keeps_the_filter_and_the_load_order():
    app = _Host()
    async with app.run_test() as pilot:
        table = app.table
        table.load_tracks(_tracks())
        await pilot.pause()
        table.move_cursor(row=1)
        await table.handle_action(Action.MARK_TOGGLE)  # Bravo
        table.apply_filter("bravo")
        await pilot.pause(0.3)

        table.append_tracks([_track("b", "Bravo trio"), _track("e", "Echo")])
        await pilot.pause()

        assert _titles(table.visible_tracks) == ["Bravo", "Bravo again", "Bravo trio"]
        assert table.track_count == 7
        assert _glyphs(table) == "✓  "
