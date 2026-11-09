"""A right-click that opens no menu must not eat the next selection (U5, app half).

Right-clicking a track's Artist or Album cell arms the table's click
suppression, which the menu's close callback clears. A track with no artist
or album id gets only a "No ... info available." toast: no menu opens, so
nothing cleared the suppression and the next play of a row did nothing.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.queue import QueueManager
from ytm_player.ui.popups.actions import ActionsPopup
from ytm_player.ui.widgets.track_table import TrackTable


def _track(video_id: str, title: str, artists: list[dict]) -> dict:
    """A row with nothing to open a menu on: no artist ids, no album id."""
    return {
        "video_id": video_id,
        "title": title,
        "artist": "Somebody",
        "artists": artists,
        "album": "Some Album",
        "album_id": None,
        "duration": 120,
        "thumbnail_url": None,
        "is_video": False,
    }


def _tracks(artists: list[dict]) -> list[dict]:
    return [_track("1", "One", artists), _track("2", "Two", artists), _track("3", "Three", artists)]


_NAME_ONLY = [{"name": "Somebody", "id": None}]


class _Host(TrackActionsMixin, App[None]):
    """The real mixin over a real TrackTable; a play shows up on the AsyncMock."""

    def __init__(self) -> None:
        super().__init__()
        self.play_track = AsyncMock()
        self.notify = MagicMock()
        self._current_page = "search"
        self._current_page_kwargs = {}
        self.queue = QueueManager()

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield Input(id="elsewhere")
        yield TrackTable(id="t")


def _column_x(table: TrackTable, column: str) -> int:
    """An x offset, from the table's left edge, inside *column*'s body."""
    for x in range(table.size.width):
        hit = table._column_at_x(x)
        if hit is not None and hit.key.value == column:
            return x + 1
    raise AssertionError(f"no {column} column on screen")


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


@pytest.mark.parametrize(
    ("column", "artists", "toast"),
    [
        ("artist", _NAME_ONLY, "No artist info available."),
        ("artist", [], "No artist info available."),
        ("album", _NAME_ONLY, "No album info available."),
    ],
    ids=["artist-without-id", "no-artists", "album-without-id"],
)
async def test_right_click_that_opens_no_menu_does_not_eat_the_next_selection(
    column, artists, toast
):
    app = _Host()
    async with app.run_test(size=(120, 40)) as pilot:
        tracks = _tracks(artists)
        app.queue.add_multiple(tracks)
        app.queue.jump_to_real(0)
        table = app.query_one(TrackTable)
        table.load_tracks(tracks)
        # Focus starts somewhere else, as when the click comes from a sidebar.
        app.query_one("#elsewhere").focus()
        await _settle(pilot, table)

        # Row 1 (y=0 is the header), in the Artist / Album cell.
        await pilot.click(TrackTable, offset=(_column_x(table, column), 2), button=3)
        await _settle(pilot, table)
        app.notify.assert_called_once_with(toast, severity="warning", timeout=2)
        assert not isinstance(app.screen, ActionsPopup)
        app.play_track.assert_not_awaited()

        table.focus()
        table.move_cursor(row=2)
        await _settle(pilot, table)
        await pilot.press("enter")
        await _settle(pilot, table)
        await _settle(pilot, table)

        # Master swallowed this Enter; only a second one played the row.
        app.play_track.assert_awaited_once()
        assert app.play_track.await_args.args[0]["video_id"] == "3"
