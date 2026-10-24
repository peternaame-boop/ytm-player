"""Track rows as the web app draws them: a small cover beside one line of text."""

from __future__ import annotations

import asyncio

import pytest
from rich.text import Text
from textual.app import App, ComposeResult

from ytm_player.ui.widgets import cover
from ytm_player.ui.widgets import track_table as tt
from ytm_player.ui.widgets.track_table import COVER_ROW_HEIGHT, TrackTable

TRACKS = [
    {"video_id": f"v{i}", "title": f"Song {i}", "artist": f"Artist {i}", "album": "Album",
     "duration": 180 + i, "thumbnail_url": f"https://example.invalid/{i}.jpg"}
    for i in range(30)
]  # fmt: skip


class _Host(App):
    def __init__(self, **table_kwargs) -> None:
        super().__init__()
        self.table_kwargs = table_kwargs
        self.picked: list[int] = []

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.setdefault("selected-item", "#333333")
        return variables

    def compose(self) -> ComposeResult:
        yield TrackTable(id="t", **self.table_kwargs)

    def on_track_table_track_selected(self, message: TrackTable.TrackSelected) -> None:
        self.picked.append(message.index)


@pytest.fixture
def loaded_urls(monkeypatch: pytest.MonkeyPatch) -> list[str]:
    urls: list[str] = []

    async def fake_load(url: str, cols: int, rows: int):
        urls.append(url)
        assert (cols, rows) == (tt.COVER_COLUMNS, COVER_ROW_HEIGHT)
        return Text("\n".join(["#" * cols] * rows))

    monkeypatch.setattr(tt, "load_cover_picture", fake_load)
    return urls


async def test_rows_carry_a_cover_column_and_three_lines(loaded_urls):
    app = _Host(show_covers=True)
    async with app.run_test(size=(100, 20)) as pilot:
        table = app.query_one(TrackTable)
        table.load_tracks(TRACKS)
        await pilot.pause(0.3)
        assert [c.key.value for c in table.ordered_columns][:2] == ["mark", "cover"]
        assert all(row.height == COVER_ROW_HEIGHT for row in table.rows.values())
        title = table.get_cell(table._row_keys[0], "title")
        assert title == "\nSong 0", "text sits on the middle line"
        assert not table.zebra_stripes


async def test_only_the_rows_in_view_load_their_covers(loaded_urls):
    app = _Host(show_covers=True)
    async with app.run_test(size=(100, 20)) as pilot:
        table = app.query_one(TrackTable)
        table.load_tracks(TRACKS)
        await pilot.pause(0.5)
        first, stop = table._visible_row_range()
        assert 0 < len(loaded_urls) <= stop - first
        assert loaded_urls[0].endswith("/0.jpg")
        shown = table.get_cell(table._row_keys[0], "cover")
        assert isinstance(shown, Text) and str(shown).startswith("######")


async def test_scrolling_loads_the_newly_visible_covers(loaded_urls):
    app = _Host(show_covers=True)
    async with app.run_test(size=(100, 20)) as pilot:
        table = app.query_one(TrackTable)
        table.load_tracks(TRACKS)
        await pilot.pause(0.5)
        before = len(loaded_urls)
        table.scroll_to(y=COVER_ROW_HEIGHT * 20, animate=False)
        await pilot.pause(tt.WARM_SCROLL_PAUSE + 0.4)
        assert len(loaded_urls) > before
        assert any(u.endswith("/20.jpg") for u in loaded_urls)


async def test_a_sort_reuses_loaded_covers(loaded_urls):
    app = _Host(show_covers=True)
    async with app.run_test(size=(100, 20)) as pilot:
        table = app.query_one(TrackTable)
        table.load_tracks(TRACKS[:4])
        await pilot.pause(0.5)
        loads = len(loaded_urls)
        table.sort_by("title")
        table.sort_by("title")  # descending
        await pilot.pause(0.3)
        assert len(loaded_urls) == loads
        last = table._tracks[0]
        assert isinstance(table.get_cell(table._row_keys[0], "cover"), Text), last


async def test_clicking_a_cover_row_plays_that_track(loaded_urls):
    app = _Host(show_covers=True)
    async with app.run_test(size=(100, 20)) as pilot:
        table = app.query_one(TrackTable)
        table.load_tracks(TRACKS)
        await pilot.pause(0.3)
        header = table.header_height if table.show_header else 0
        row_two_middle = header + COVER_ROW_HEIGHT * 2 + 1
        await pilot.click(TrackTable, offset=(20, row_two_middle))
        await pilot.click(TrackTable, offset=(20, row_two_middle))
        await pilot.pause()
        assert table.cursor_row == 2
        assert app.picked == [2]


async def test_marks_sit_on_the_middle_line_of_a_cover_row(loaded_urls):
    app = _Host(show_covers=True)
    async with app.run_test(size=(100, 20)) as pilot:
        table = app.query_one(TrackTable)
        table.load_tracks(TRACKS[:3])
        await pilot.pause()
        table._toggle_mark(1)
        assert table.get_cell(table._row_keys[1], "mark") == "\n✓"


async def test_albums_keep_numbers_and_no_covers(loaded_urls):
    app = _Host(show_covers=False)
    async with app.run_test(size=(100, 20)) as pilot:
        table = app.query_one(TrackTable)
        table.load_tracks(TRACKS[:3])
        await pilot.pause(0.3)
        assert "cover" not in [c.key.value for c in table.ordered_columns]
        assert all(row.height == 1 for row in table.rows.values())
        assert table.get_cell(table._row_keys[0], "index") == "1"
        assert loaded_urls == []


def test_load_cover_picture_returns_none_without_a_download(monkeypatch):
    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)
    assert asyncio.run(cover.load_cover_picture("https://example.invalid/x.jpg", 6, 3)) is None


async def test_a_layout_pass_still_loads_covers_after_the_title_refit(loaded_urls):
    """_invalidate_table schedules two post-refresh jobs: the settled title refit
    and the visible-cover load. A merge once kept only one of them."""
    app = _Host(show_covers=True)
    async with app.run_test(size=(100, 20)) as pilot:
        table = app.query_one(TrackTable)
        table.load_tracks(TRACKS)
        await pilot.pause(0.5)
        loaded_urls.clear()
        # Covers are cached by url, so drop the cache to force a real fetch.
        table._cover_pictures.clear()
        table._covers_pending.clear()
        table._invalidate_table()
        await pilot.pause(0.6)
        assert loaded_urls, "no cover loaded after a layout pass"
        assert not table._refill_pending
