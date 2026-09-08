"""Tests for the _ArtistAlbumList table inside ContextPage."""

from __future__ import annotations

import pytest
from rich.text import Text
from textual.app import App, ComposeResult

from ytm_player.ui.pages.context import _ArtistAlbumList


class _Host(App):
    """Minimal host that provides the theme variables _ArtistAlbumList's CSS needs."""

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables


@pytest.mark.parametrize("same_id", [True, False])
async def test_duplicate_album_occurrences_survive_loading_and_selection(same_id):
    albums = [{"title": "Same title", "year": "2024"}, {"title": "Same title", "year": "2025"}]
    if same_id:
        for album in albums:
            album["browseId"] = "MPRE_duplicate"

    class Host(_Host):
        def compose(self):
            yield _ArtistAlbumList()

    app = Host()
    async with app.run_test() as pilot:
        table = app.query_one(_ArtistAlbumList)
        for _ in range(2):
            table.load_albums(albums)
            await pilot.pause()
            assert table.row_count == 2
            table.move_cursor(row=1)
            assert table.selected_album is albums[1]
            table.move_cursor(row=0)
            assert table.selected_album is albums[0]
        table.load_albums([])
        assert table.row_count == 0
        assert table.selected_album is None


async def test_album_metadata_is_literal_text():
    class Host(_Host):
        def compose(self):
            yield _ArtistAlbumList()

    app = Host()
    async with app.run_test() as pilot:
        table = app.query_one(_ArtistAlbumList)
        table.load_albums([{"title": "[/] [bold]Album[/bold]", "year": "[/]"}])
        await pilot.pause()
        title, year = table.get_row_at(0)
        assert isinstance(title, Text) and title.plain == "[/] [bold]Album[/bold]"
        assert isinstance(year, Text) and year.plain == "[/]"


async def test_load_albums_immediately_after_construction():
    """Regression: see context._build_artist nested-mount path.

    Before the option-C fix on _ArtistAlbumList, this widget set up its
    columns inside on_mount. _build_artist mounts the album table inside
    a 3-deep nested-mount chain (container.mount(columns) →
    columns.mount(right) → right.mount(album_table)) and immediately
    calls load_albums synchronously — on_mount hasn't fired yet, so the
    table has 0 columns and add_row crashes with
    "More values provided than there are columns".

    This test reproduces that exact scenario: load_albums runs during
    compose, before on_mount fires. With columns set up in __init__, it
    succeeds.
    """

    captured: dict[str, int] = {}

    class _LoadDuringCompose(_Host):
        def compose(self) -> ComposeResult:
            table = _ArtistAlbumList()
            table.load_albums(
                [
                    {"title": "Album One", "year": "2024", "browseId": "MPREb_1"},
                    {"title": "Album Two", "year": "2023", "browseId": "MPREb_2"},
                ]
            )
            captured["row_count"] = table.row_count
            yield table

    app = _LoadDuringCompose()
    async with app.run_test():
        assert captured["row_count"] == 2


async def test_columns_set_up_at_construction_time():
    """Columns must exist immediately after __init__, before on_mount fires."""

    captured: dict[str, set[str]] = {}

    class _CaptureColumns(_Host):
        def compose(self) -> ComposeResult:
            table = _ArtistAlbumList()
            captured["keys"] = {c.value for c in table.columns if c.value is not None}
            yield table

    app = _CaptureColumns()
    async with app.run_test():
        assert captured["keys"] == {"title", "year"}
