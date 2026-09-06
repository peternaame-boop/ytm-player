"""Rendered-width regression test for the Browse tab bar.

Five tabs have to fit the page width left beside the 30-column playlist
sidebar in an 80-column terminal (50 columns, 48 of content inside the bar's
padding). The bar is rendered in a headless app next to a 30-column stand-in
for the sidebar, and every tab's painted columns are read back from the
compositor — a label the layout clips is one the user cannot click.
"""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult
from textual.containers import Horizontal
from textual.widgets import Static

from ytm_player.ui.pages import browse
from ytm_player.ui.pages.browse import _TABS, BrowsePage

SIDEBAR_WIDTH = 30


class _Host(App):
    CSS = f"#sidebar {{ width: {SIDEBAR_WIDTH}; height: 1fr; }} BrowsePage {{ width: 1fr; }}"

    def __init__(self, *, sidebar: bool) -> None:
        super().__init__()
        self._sidebar = sidebar
        self.ytmusic = MagicMock()
        self.ytmusic.get_home = AsyncMock(return_value=[])
        self.ytmusic.get_library_artists = AsyncMock(return_value=[])

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables["selected-item"] = "#3a3a3a"
        return variables

    def compose(self) -> ComposeResult:
        with Horizontal():
            if self._sidebar:
                yield Static("Playlists", id="sidebar")
            yield BrowsePage(id="page")


@pytest.fixture(autouse=True)
def _fixed_settings(monkeypatch: pytest.MonkeyPatch) -> None:
    settings = SimpleNamespace(ui=SimpleNamespace(home_shelves=3, region="ZZ"))
    monkeypatch.setattr(browse, "get_settings", lambda: settings)


def _painted_columns(host: App, width: int) -> list[tuple[int, int]]:
    """(painted, expected) column counts per tab on the tab bar's label row."""
    tabs = [host.query_one(f"#tab-{i}") for i in range(len(_TABS))]
    compositor = host.screen._compositor
    row = tabs[0].region.y + 1  # the label sits on the middle row of the 3-row tab
    hits: dict[object, int] = {}
    for x in range(width):
        try:
            widget, _region = compositor.get_widget_at(x, row)
        except Exception:
            continue
        hits[widget] = hits.get(widget, 0) + 1
    return [(hits.get(tab, 0), tab.outer_size.width) for tab in tabs]


@pytest.mark.parametrize(("width", "sidebar"), [(80, True), (80, False), (120, True)])
async def test_all_five_tabs_are_fully_painted(width: int, sidebar: bool) -> None:
    host = _Host(sidebar=sidebar)
    async with host.run_test(size=(width, 24)) as pilot:
        await host.workers.wait_for_complete()
        await pilot.pause()

        page_width = host.query_one("#page", BrowsePage).size.width
        assert page_width == (width - SIDEBAR_WIDTH if sidebar else width)
        columns = _painted_columns(host, width)

    assert all(expected > 0 for _painted, expected in columns)
    assert all(painted == expected for painted, expected in columns), columns
