"""The web-style left menu (Home, Explore, Library) and the top-bar search box, clicked through Pilot."""

from __future__ import annotations

from unittest.mock import AsyncMock

import pytest
from textual.app import App, ComposeResult
from textual.widgets import Input

from ytm_player.app._sidebar import SidebarMixin
from ytm_player.ui.header_bar import HeaderBar
from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar


class _SidebarHost(App):
    def __init__(self) -> None:
        super().__init__()
        self.nav: list[str] = []

    def compose(self) -> ComposeResult:
        yield PlaylistSidebar(id="playlist-sidebar")

    def on_playlist_sidebar_nav_item_clicked(self, message: PlaylistSidebar.NavItemClicked) -> None:
        self.nav.append(message.nav_id)


@pytest.mark.parametrize(
    ("item_id", "page"),
    [
        ("ps-nav-home", "home"),
        ("ps-nav-explore", "explore"),
        ("ps-nav-library", "library"),
        ("ps-nav-liked", "liked_songs"),
        ("ps-nav-recent", "recently_played"),
    ],  # fmt: skip
)
async def test_each_menu_item_opens_its_page(item_id: str, page: str) -> None:
    app = _SidebarHost()
    async with app.run_test(size=(40, 30)) as pilot:
        await pilot.click(f"#{item_id}")
        await pilot.pause()
        assert app.nav == [page]


async def test_menu_order_matches_the_web_app() -> None:
    app = _SidebarHost()
    async with app.run_test(size=(40, 30)):
        labels = [str(w.render()) for w in app.query(".ps-pinned-item")]
        assert labels[:3] == ["⌂  Home", "◎  Explore", "▤  Library"]


async def test_active_page_is_highlighted_and_moves() -> None:
    app = _SidebarHost()
    async with app.run_test(size=(40, 30)):
        sidebar = app.query_one(PlaylistSidebar)
        sidebar.set_active_page("home")
        active = [w.id for w in app.query(".ps-pinned-item.active")]
        assert active == ["ps-nav-home"]
        sidebar.set_active_page("browse")
        assert [w.id for w in app.query(".ps-pinned-item.active")] == ["ps-nav-explore"]
        sidebar.set_active_page("search")  # a page with no menu item
        assert not app.query(".ps-pinned-item.active")


class _SearchHost(App):
    """Header bar plus the app's real search handler, with navigation faked."""

    on_header_bar_search_requested = SidebarMixin.on_header_bar_search_requested

    def __init__(self) -> None:
        super().__init__()
        self.navigate_to = AsyncMock(side_effect=self._show_search)

    def get_css_variables(self) -> dict[str, str]:
        variables = super().get_css_variables()
        variables.setdefault("playback-bar-bg", "#222222")
        return variables

    def compose(self) -> ComposeResult:
        yield HeaderBar(id="app-header")

    async def _show_search(self, page: str) -> None:
        await self.mount(Input(id="search-input"))


async def test_top_bar_search_opens_search_with_the_cursor_in_the_box() -> None:
    app = _SearchHost()
    async with app.run_test(size=(140, 10)) as pilot:
        box = app.query_one("#header-search")
        assert "Search songs, albums, artists, podcasts" in str(box.render())
        await pilot.click("#header-search")
        for _ in range(5):
            await pilot.pause()
        app.navigate_to.assert_awaited_once_with("search")
        assert app.focused is app.query_one("#search-input")


async def test_menu_icons_are_one_cell_and_items_are_spaced_like_the_playlists():
    from rich.cells import cell_len

    app = _SidebarHost()
    async with app.run_test(size=(40, 40)) as pilot:
        await pilot.pause()
        items = list(app.query(".ps-pinned-item"))
        assert all(cell_len(str(i.render())[0]) == 1 for i in items)
        ys = [i.region.y for i in items]
        assert {b - a for a, b in zip(ys, ys[1:])} == {2}


@pytest.mark.parametrize(
    ("item_id", "nav_id"), [("ps-nav-queue", "queue"), ("ps-nav-import", "spotify_import")]
)
async def test_queue_and_import_moved_into_the_menu(item_id: str, nav_id: str) -> None:
    app = _SidebarHost()
    async with app.run_test(size=(40, 40)) as pilot:
        await pilot.click(f"#{item_id}")
        await pilot.pause()
        assert app.nav[-1] == nav_id


async def test_import_from_the_menu_opens_the_spotify_import_popup() -> None:
    from types import SimpleNamespace
    from unittest.mock import MagicMock

    from ytm_player.app._sidebar import SidebarMixin
    from ytm_player.ui.popups.spotify_import import SpotifyImportPopup

    host = SimpleNamespace(push_screen=MagicMock(), run_worker=MagicMock())
    await SidebarMixin.on_playlist_sidebar_nav_item_clicked(
        host, PlaylistSidebar.NavItemClicked("spotify_import")
    )
    assert isinstance(host.push_screen.call_args.args[0], SpotifyImportPopup)
