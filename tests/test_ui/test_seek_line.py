"""The web player's seek line: thin at rest, a thick band under the mouse, click to seek."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from textual.app import App, ComposeResult

from ytm_player.ui.widgets.progress_bar import SeekLine


class _Host(App):
    def __init__(self) -> None:
        super().__init__()
        self.player = MagicMock()
        self.player.seek_absolute = AsyncMock()

    def compose(self) -> ComposeResult:
        yield SeekLine(id="seek")


def _rows(widget) -> list[str]:
    return str(widget.render()).split("\n")


async def test_at_rest_it_is_a_thin_line_with_the_played_part_lit():
    app = _Host()
    async with app.run_test(size=(100, 4)) as pilot:
        seek = app.query_one(SeekLine)
        seek.update_position(50, 100)
        await pilot.pause()
        top, bottom = _rows(seek)
        assert top.strip() == "" and set(bottom) == {SeekLine.THIN}
        assert seek.size.height == 2


async def test_under_the_mouse_it_grows_into_a_band_and_shows_the_time():
    app = _Host()
    async with app.run_test(size=(100, 4)) as pilot:
        seek = app.query_one(SeekLine)
        seek.update_position(10, 200)
        await pilot.hover(SeekLine, offset=(50, 1))
        await pilot.pause()
        top, bottom = _rows(seek)
        assert set(top) == {SeekLine.BAND_TOP} and set(bottom) == {SeekLine.BAND_BOTTOM}
        assert seek.tooltip == "1:40"
        await pilot.hover(None, offset=(50, 3))
        await pilot.pause()
        assert set(_rows(seek)[1]) == {SeekLine.THIN}


async def test_a_click_seeks_to_that_point_on_either_row():
    app = _Host()
    async with app.run_test(size=(100, 4)) as pilot:
        seek = app.query_one(SeekLine)
        seek.update_position(0, 200)
        await pilot.click(SeekLine, offset=(25, 0))
        await pilot.pause()
        await pilot.click(SeekLine, offset=(75, 1))
        for _ in range(3):
            await pilot.pause()
        targets = [call.args[0] for call in app.player.seek_absolute.call_args_list]
        assert targets == [50.0, 150.0]
