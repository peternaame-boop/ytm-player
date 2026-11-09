"""A right-click on the TrackTable only arms click suppression on a real row.

A right-click on the header or on the empty area under the rows opens no
menu, so it must not make the table ignore the next play click. A
right-click on a row still arms the suppression that protects against the
stray selection when an actions popup hands focus back.
"""

from __future__ import annotations

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Button

from ytm_player.ui.widgets.track_table import TrackTable

# Rows sit at y=1..3 (y=0 is the header); the table is taller than that.
HEADER_Y = 0
EMPTY_Y = 8


def _track(video_id: str, title: str) -> dict:
    return {
        "video_id": video_id,
        "title": title,
        "artist": "Artist",
        "artists": [{"name": "Artist", "id": "1"}],
        "album": "Album",
        "album_id": None,
        "duration": 100,
        "thumbnail_url": None,
        "is_video": False,
    }


def _tracks() -> list[dict]:
    return [_track("a", "Alpha"), _track("b", "Bravo"), _track("c", "Charlie")]


class _Host(App):
    """A table plus another focusable widget; records what the table posts."""

    def __init__(self) -> None:
        super().__init__()
        self.selected: list[str] = []
        self.right_clicked: list[tuple[str, str]] = []

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        # Built here, not in __init__: add_column measures with the app console.
        self.table = TrackTable(id="t")
        yield self.table
        yield Button("elsewhere", id="elsewhere")

    def on_track_table_track_selected(self, event: TrackTable.TrackSelected) -> None:
        self.selected.append(event.track["title"])

    def on_track_table_track_right_clicked(self, event: TrackTable.TrackRightClicked) -> None:
        self.right_clicked.append(("track", event.track["title"]))

    def on_track_table_artist_right_clicked(self, event: TrackTable.ArtistRightClicked) -> None:
        self.right_clicked.append(("artist", event.track["title"]))

    def on_track_table_album_right_clicked(self, event: TrackTable.AlbumRightClicked) -> None:
        self.right_clicked.append(("album", event.track["title"]))


def _x_of(table: TrackTable, key: str) -> int:
    """An x offset inside the column *key* (plus one cell of padding)."""
    for x in range(200):
        column = table._column_at_x(x)
        if column is not None and column.key is not None and column.key.value == key:
            return x + 1
    raise AssertionError(f"no column {key!r}")


async def _settle(pilot, table: TrackTable) -> None:
    """Wait until the table has done its post-layout column refit.

    The table refits its columns once more after a refresh. On a slow machine
    an idle message queue is not enough: the screen can still show the
    pre-refit widths, with the last column cut off and columns at other x
    offsets.
    """
    for _ in range(200):
        await pilot.pause()
        if not table._refill_pending:
            break
    else:
        raise AssertionError("the table never finished its column refit")
    await pilot.pause()


async def _start(pilot, app: _Host) -> TrackTable:
    """Load three rows, focus the table, highlight row 0 ("Alpha")."""
    table = app.table
    table.load_tracks(_tracks())
    table.focus()
    await _settle(pilot, app.table)
    assert table.cursor_row == 0
    return table


async def _blur_and_refocus(pilot, app: _Host) -> None:
    app.query_one("#elsewhere").focus()
    await _settle(pilot, app.table)
    assert not app.table.has_focus
    app.table.focus()
    await _settle(pilot, app.table)
    assert app.table.has_focus


# ── No menu target: the next play click must get through ─────────────


@pytest.mark.parametrize("y", [HEADER_Y, EMPTY_Y], ids=["header", "empty-area"])
async def test_right_click_off_the_rows_does_not_eat_the_next_play_click(y):
    app = _Host()
    async with app.run_test(size=(120, 24)) as pilot:
        table = await _start(pilot, app)
        x = _x_of(table, "title")
        # A first click puts the highlight on the cell; it plays nothing.
        await pilot.click(TrackTable, offset=(x, 1))
        await _settle(pilot, app.table)
        assert app.selected == []

        await pilot.click(TrackTable, offset=(x, y), button=3)
        await _settle(pilot, app.table)
        assert app.right_clicked == []

        # One click on the highlighted row plays it.
        await pilot.click(TrackTable, offset=(x, 1))
        await _settle(pilot, app.table)
        assert app.selected == ["Alpha"]


@pytest.mark.parametrize("y", [HEADER_Y, EMPTY_Y], ids=["header", "empty-area"])
async def test_right_click_off_the_rows_does_not_eat_enter(y):
    app = _Host()
    async with app.run_test(size=(120, 24)) as pilot:
        table = await _start(pilot, app)

        await pilot.click(TrackTable, offset=(_x_of(table, "title"), y), button=3)
        await _settle(pilot, app.table)
        await pilot.press("enter")
        await _settle(pilot, app.table)
        assert app.selected == ["Alpha"]


@pytest.mark.parametrize("y", [HEADER_Y, EMPTY_Y], ids=["header", "empty-area"])
async def test_right_click_off_the_rows_then_a_focus_round_trip_does_not_eat_enter(y):
    app = _Host()
    async with app.run_test(size=(120, 24)) as pilot:
        table = await _start(pilot, app)

        await pilot.click(TrackTable, offset=(_x_of(table, "title"), y), button=3)
        await _settle(pilot, app.table)
        # Nothing opened; a later, unrelated blur must not arm suppression.
        await _blur_and_refocus(pilot, app)
        await pilot.press("enter")
        await _settle(pilot, app.table)
        assert app.selected == ["Alpha"]


async def test_right_click_on_the_header_does_not_sort():
    app = _Host()
    async with app.run_test(size=(120, 24)) as pilot:
        table = await _start(pilot, app)

        await pilot.click(TrackTable, offset=(_x_of(table, "title"), HEADER_Y), button=3)
        await _settle(pilot, app.table)
        assert table._sort_column is None
        assert [t["title"] for t in table.tracks] == ["Alpha", "Bravo", "Charlie"]


# ── A real row: suppression is still armed ───────────────────────────


@pytest.mark.parametrize(
    ("column", "kind"),
    [("title", "track"), ("artist", "artist"), ("album", "album")],
)
async def test_right_click_on_a_row_posts_its_message_and_eats_one_selection(column, kind):
    app = _Host()
    async with app.run_test(size=(120, 24)) as pilot:
        table = await _start(pilot, app)

        await pilot.click(TrackTable, offset=(_x_of(table, column), 2), button=3)
        await _settle(pilot, app.table)
        assert app.right_clicked == [(kind, "Bravo")]

        # The one selection that follows is the popup's stray one: eaten.
        await pilot.press("enter")
        await _settle(pilot, app.table)
        assert app.selected == []
        # Only one: the next selection is genuine.
        await pilot.press("enter")
        await _settle(pilot, app.table)
        assert app.selected == ["Alpha"]


async def test_right_click_on_a_row_then_a_focus_round_trip_eats_one_selection():
    """The popup-refocus path: the table blurs (a modal took focus) and gets focus back."""
    app = _Host()
    async with app.run_test(size=(120, 24)) as pilot:
        table = await _start(pilot, app)

        await pilot.click(TrackTable, offset=(_x_of(table, "title"), 2), button=3)
        await _settle(pilot, app.table)
        await _blur_and_refocus(pilot, app)

        await pilot.press("enter")
        await _settle(pilot, app.table)
        assert app.selected == []
        await pilot.press("enter")
        await _settle(pilot, app.table)
        assert app.selected == ["Alpha"]


async def test_clear_popup_suppression_lets_the_next_selection_through():
    app = _Host()
    async with app.run_test(size=(120, 24)) as pilot:
        table = await _start(pilot, app)

        await pilot.click(TrackTable, offset=(_x_of(table, "title"), 2), button=3)
        await _settle(pilot, app.table)
        await _blur_and_refocus(pilot, app)
        table.clear_popup_suppression()

        await pilot.press("enter")
        await _settle(pilot, app.table)
        assert app.selected == ["Alpha"]
