"""TrackTable column widths: the table must never scroll sideways.

``_fill_title_column`` shrinks Title to fill exactly the width that is left
over. It has to measure the width the rows actually get, which is only known
once the layout has settled: the row-label gutter, the auto-grown Artist and
Album widths and the vertical scrollbar all take cells away from the table
after the first fill runs. Measuring too early pushes the rightmost column
("Duration") past the viewport, which is the v1.9.2 regression.
"""

from __future__ import annotations

from textual.app import App, ComposeResult

from ytm_player.ui.widgets.track_table import TrackTable


class _Host(App):
    """Minimal host that provides the theme variables TrackTable's CSS needs."""

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        yield TrackTable(id="t", show_index=True, show_album=True)


def _tracks(count: int, start: int = 0) -> list[dict]:
    return [
        {
            "video_id": f"vid{i:04d}",
            "title": f"A Reasonably Long Track Title Number {i}",
            "artist": f"Artist {i % 17}",
            "artists": [{"name": f"Artist {i % 17}", "id": "1"}],
            "album": f"Album Name {i % 9}",
            "album_id": None,
            "duration": 180 + i,
            "thumbnail_url": None,
            "is_video": False,
        }
        for i in range(start, start + count)
    ]


def _header_line(app: App) -> str:
    """The rendered header row, as text."""
    for strip in app.screen._compositor.render_strips():
        text = strip.text
        if "Durat" in text:
            return text
    return ""


async def settle(pilot) -> None:
    for _ in range(5):
        await pilot.pause()


async def test_table_does_not_scroll_sideways_with_a_vertical_scrollbar():
    """Enough rows to show a vertical scrollbar: Title absorbs its width."""

    app = _Host()
    async with app.run_test(size=(100, 24)) as pilot:
        table = app.query_one("#t", TrackTable)
        table.load_tracks(_tracks(200))
        await settle(pilot)

        assert table.show_vertical_scrollbar, "expected a vertical scrollbar at 200 rows"
        assert table.virtual_size.width <= table.scrollable_content_region.width
        assert "Duration" in _header_line(app)


async def test_table_does_not_scroll_sideways_without_a_vertical_scrollbar():
    """Few enough rows that no scrollbar appears: still no horizontal scroll."""

    app = _Host()
    async with app.run_test(size=(100, 24)) as pilot:
        table = app.query_one("#t", TrackTable)
        table.load_tracks(_tracks(3))
        await settle(pilot)

        assert not table.show_vertical_scrollbar
        assert table.virtual_size.width <= table.scrollable_content_region.width
        assert "Duration" in _header_line(app)


async def test_appending_rows_after_the_first_paint_refits_the_title_column():
    """A background refresh that appends past the viewport brings the
    scrollbar in with it; the fill has to run again for that layout."""

    app = _Host()
    async with app.run_test(size=(100, 24)) as pilot:
        table = app.query_one("#t", TrackTable)
        table.load_tracks(_tracks(3))
        await settle(pilot)

        table.append_tracks(_tracks(300, start=3))
        await settle(pilot)

        assert table.show_vertical_scrollbar
        assert table.virtual_size.width <= table.scrollable_content_region.width
        assert "Duration" in _header_line(app)
