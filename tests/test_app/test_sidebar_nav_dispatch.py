"""Tests for SidebarMixin.on_playlist_sidebar_nav_item_clicked's routing."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._sidebar import SidebarMixin
from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar


def _make_host() -> MagicMock:
    host = MagicMock()
    host.navigate_to = AsyncMock()
    host._start_discovery_mix = MagicMock(name="_start_discovery_mix")
    host._start_new_release_mix = MagicMock(name="_start_new_release_mix")
    host.run_worker = MagicMock()
    return host


async def _dispatch(host: MagicMock, nav_id: str) -> None:
    message = PlaylistSidebar.NavItemClicked(nav_id)
    await SidebarMixin.on_playlist_sidebar_nav_item_clicked(host, message)


class TestNavDispatch:
    async def test_discovery_mix_runs_as_worker(self):
        host = _make_host()
        await _dispatch(host, "discovery_mix")

        host.run_worker.assert_called_once()
        host._start_discovery_mix.assert_called_once()
        host._start_new_release_mix.assert_not_called()
        host.navigate_to.assert_not_called()

    async def test_new_release_mix_runs_as_worker(self):
        host = _make_host()
        await _dispatch(host, "new_release_mix")

        host.run_worker.assert_called_once()
        host._start_new_release_mix.assert_called_once()
        host._start_discovery_mix.assert_not_called()
        host.navigate_to.assert_not_called()

    async def test_browse_navigates_directly(self):
        host = _make_host()
        await _dispatch(host, "browse")

        host.navigate_to.assert_awaited_once_with("browse")
        host.run_worker.assert_not_called()

    async def test_liked_songs_navigates_directly(self):
        host = _make_host()
        await _dispatch(host, "liked_songs")

        host.navigate_to.assert_awaited_once_with("liked_songs")
        host.run_worker.assert_not_called()
