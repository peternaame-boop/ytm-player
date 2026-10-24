"""Library grid: the web app's Library view, every playlist as a cover card, clicked through Pilot."""

from __future__ import annotations

from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest
from textual.app import App, ComposeResult

from ytm_player.app._sidebar import SidebarMixin
from ytm_player.ui.pages.home import HomeCard
from ytm_player.ui.pages.library import LibraryPage
from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar
from ytm_player.ui.widgets import cover

PLAYLISTS = [
    {"playlistId": "PLfav", "title": "Favorite Songs", "count": "23",
     "author": [{"name": "Shadow Sinz"}], "thumbnails": [{"url": "u1", "width": 226}]},
    {"playlistId": "PLone", "title": "Correspondence", "count": "1",
     "author": [{"name": "Shadow Sinz"}], "thumbnails": [{"url": "u2", "width": 226}]},
]  # fmt: skip


class _Host(App):
    def __init__(self, *, grid: bool, playlists=PLAYLISTS) -> None:
        super().__init__()
        self.ytmusic = MagicMock()
        self.ytmusic.get_library_playlists = AsyncMock(return_value=playlists)
        self.navigate_to = AsyncMock()
        self._replace_queue_and_play = AsyncMock()
        self._grid = grid

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.setdefault("selected-item", "#3a3a3a")
        return variables

    def compose(self) -> ComposeResult:
        yield LibraryPage(id="page-library", grid=self._grid)


@pytest.fixture(autouse=True)
def _offline(monkeypatch: pytest.MonkeyPatch) -> None:
    from ytm_player.ui.pages import library

    monkeypatch.setattr(cover, "fetch_thumbnail", lambda url: None)
    monkeypatch.setattr(library, "_grid_cache", None)


async def _cards(pilot) -> list[HomeCard]:
    for _ in range(20):
        await pilot.pause()
        cards = list(pilot.app.query(HomeCard))
        if cards:
            return cards
    return []


async def test_grid_shows_every_playlist_as_a_card_with_web_subtitles():
    app = _Host(grid=True)
    async with app.run_test(size=(140, 40)) as pilot:
        cards = await _cards(pilot)
        assert [str(c.query_one(".card-title").render()) for c in cards] == [
            "Favorite Songs",
            "Correspondence",
        ]
        assert [str(c.query_one(".card-sub").render()) for c in cards] == [
            "Playlist • Shadow Sinz • 23 songs",
            "Playlist • Shadow Sinz • 1 song",
        ]
        assert not app.query_one("#empty-state").display


async def test_click_a_playlist_card_opens_the_playlist():
    app = _Host(grid=True)
    async with app.run_test(size=(140, 40)) as pilot:
        cards = await _cards(pilot)
        await pilot.click(cards[1])
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_with("context", context_type="playlist", context_id="PLone")


async def test_grid_state_survives_back_navigation():
    app = _Host(grid=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await _cards(pilot)
        assert app.query_one(LibraryPage).get_nav_state() == {"grid": True}


async def test_without_grid_the_classic_page_is_unchanged():
    app = _Host(grid=False)
    async with app.run_test(size=(140, 40)) as pilot:
        await pilot.pause()
        assert not app.query_one("#library-grid-scroll").display
        assert app.query_one("#empty-state").display
        app.ytmusic.get_library_playlists.assert_not_awaited()


async def test_empty_library_says_so():
    app = _Host(grid=True, playlists=[])
    async with app.run_test(size=(140, 40)) as pilot:
        for _ in range(10):
            await pilot.pause()
        assert "No playlists yet." in str(app.query_one("#library-grid .home-status").render())


async def test_menu_library_opens_the_grid():
    host = SimpleNamespace(navigate_to=AsyncMock(), run_worker=MagicMock())
    await SidebarMixin.on_playlist_sidebar_nav_item_clicked(
        host, PlaylistSidebar.NavItemClicked("library")
    )
    host.navigate_to.assert_awaited_once_with("library", grid=True)


@pytest.mark.parametrize(("width", "columns"), [(80, 2), (120, 4)])
async def test_grid_fills_the_width_whatever_the_playlist_count(width: int, columns: int):
    """Regression: regular rows forced 1 column when 9 playlists did not divide evenly."""
    nine = [dict(PLAYLISTS[0], playlistId=f"P{i}", title=f"PL {i}") for i in range(9)]
    app = _Host(grid=True, playlists=nine)
    async with app.run_test(size=(width, 40)) as pilot:
        cards = await _cards(pilot)
        await pilot.pause(0.3)  # under load, the grid lays out after the cards exist
        assert len({c.region.x for c in cards}) == columns


async def test_grid_asks_for_a_screen_layout_pass_after_its_cards_mount(monkeypatch):
    """Cards sometimes stayed unpainted in kitty until a scroll. A layout pass fixes it."""
    from ytm_player.ui.pages import library

    calls = []
    monkeypatch.setattr(library, "relayout_soon", lambda widget: calls.append(widget))
    app = _Host(grid=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await _cards(pilot)
        assert calls == [app.query_one(LibraryPage)]


async def test_a_loading_message_shows_while_playlists_load():
    """Regression: the grid sat blank with no message while the API answered."""
    import asyncio

    gate = asyncio.Event()

    async def slow(limit=None):
        await gate.wait()
        return PLAYLISTS

    app = _Host(grid=True)
    app.ytmusic.get_library_playlists = slow
    async with app.run_test(size=(140, 40)) as pilot:
        for _ in range(5):
            await pilot.pause()
        assert "Loading your playlists" in str(app.query_one("#library-grid .home-status").render())
        gate.set()
        assert await _cards(pilot)


async def test_coming_back_to_library_uses_the_cache_until_invalidated():
    app = _Host(grid=True)
    async with app.run_test(size=(140, 40)) as pilot:
        await _cards(pilot)
        await app.query_one(LibraryPage).remove()
        await app.mount(LibraryPage(id="again", grid=True))
        await _cards(pilot)
        assert app.ytmusic.get_library_playlists.await_count == 1
        LibraryPage.invalidate_grid_cache()
        await app.query_one("#again").remove()
        await app.mount(LibraryPage(id="third", grid=True))
        await _cards(pilot)
        assert app.ytmusic.get_library_playlists.await_count == 2
