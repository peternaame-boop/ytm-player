"""Marks on the real Recently Played page survive the plays that re-render it.

These tests drive the Local tab, which lists a track once, so every load
of its table is keyed by video ID and the first background refresh after
marking (a play landing) carries the marks over instead of falling back
to a fresh load. A deliberate load still starts with none. The YT Music
tab, whose rows are keyed by occurrence, is covered in
``test_recently_played_ytm_occurrences.py``.
"""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from textual.app import App, ComposeResult

from ytm_player.config.keymap import Action
from ytm_player.ui.pages.recently_played import _TAB_LOCAL, RecentlyPlayedPage
from ytm_player.ui.widgets.track_table import TrackTable


def _local(n: int) -> list[dict]:
    return [
        {
            "video_id": f"loc{i}",
            "title": f"Local {i}",
            "artist": "Artist",
            "album": "Album",
            "duration_seconds": 180,
            "played_at": f"2026-09-06T12:{59 - i:02d}:00",
        }
        for i in range(n)
    ]


class _Host(App):
    """Minimal host exposing the attributes RecentlyPlayedPage reads."""

    def __init__(self) -> None:
        super().__init__()
        self.history = MagicMock()
        self.history.get_recently_played = AsyncMock(return_value=_local(4))
        self.ytmusic = None
        self._ytm_history = None
        self._ytm_history_pending = []
        self._ytm_history_pending_seq = 0

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield RecentlyPlayedPage(id="page", active_tab=_TAB_LOCAL)


def _glyphs(table: TrackTable) -> str:
    return "".join(str(table.get_row_at(i)[0]) for i in range(table.row_count))


async def test_first_background_refresh_keeps_the_marks():
    host = _Host()
    async with host.run_test(size=(100, 24)) as pilot:
        await host.workers.wait_for_complete()
        await pilot.pause()
        page = host.query_one("#page", RecentlyPlayedPage)
        table = page.query_one("#recent-table", TrackTable)
        assert table.row_count == 4
        table.focus()
        table.move_cursor(row=1)
        await page.handle_action(Action.MARK_TOGGLE)
        assert _glyphs(table) == " ✓  "
        generation = table.selection_generation

        page.optimistic_add(_TAB_LOCAL, {"video_id": "new", "title": "New", "artist": "Artist"})
        await pilot.pause()

        assert table.row_count == 5
        assert table.marked_count == 1
        assert [t["title"] for t in table.marked_tracks()] == ["Local 1"]
        assert _glyphs(table) == "  ✓  "
        assert table.selection_generation == generation
        assert table.has_class("-marked")


async def test_a_play_of_the_marked_track_moves_its_mark_to_the_top():
    host = _Host()
    async with host.run_test(size=(100, 24)) as pilot:
        await host.workers.wait_for_complete()
        await pilot.pause()
        page = host.query_one("#page", RecentlyPlayedPage)
        table = page.query_one("#recent-table", TrackTable)
        table.focus()
        table.move_cursor(row=2)
        await page.handle_action(Action.MARK_TOGGLE)  # Local 2

        page.optimistic_add(_TAB_LOCAL, {"video_id": "loc2", "title": "Local 2", "artist": "A"})
        await pilot.pause()

        assert [t["video_id"] for t in table.visible_tracks] == ["loc2", "loc0", "loc1", "loc3"]
        assert _glyphs(table) == "✓   "


async def test_a_deliberate_load_starts_without_marks():
    host = _Host()
    async with host.run_test(size=(100, 24)) as pilot:
        await host.workers.wait_for_complete()
        await pilot.pause()
        page = host.query_one("#page", RecentlyPlayedPage)
        table = page.query_one("#recent-table", TrackTable)
        table.focus()
        await page.handle_action(Action.MARK_TOGGLE)
        assert table.marked_count == 1

        page._display_tracks(page._rows_for(_TAB_LOCAL))
        await pilot.pause()

        assert table.marked_count == 0
        assert not table.has_class("-marked")
