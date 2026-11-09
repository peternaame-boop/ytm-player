from __future__ import annotations

import asyncio

from ytm_player.app._sidebar import SidebarMixin
from ytm_player.ui.sidebars.playlist_sidebar import LibraryPanel, PlaylistSidebar


class FakeYTMusic:
    async def delete_playlist(self, playlist_id: str) -> str:
        return "success"

    async def remove_album_from_library(self, playlist_id: str) -> str:
        raise AssertionError(
            "remove_album_from_library should not be called after successful delete"
        )

    async def get_playlist(self, playlist_id: str, limit: int) -> dict:
        raise TimeoutError("network timeout")


class FakeLibraryPanel:
    def __init__(self) -> None:
        self.removed_ids: list[str] = []

    def remove_item(self, playlist_id: str) -> None:
        self.removed_ids.append(playlist_id)


class FakePlaylistSidebar:
    def __init__(self, panel: FakeLibraryPanel) -> None:
        self.panel = panel

    def query_one(self, selector: str, widget_type: type[LibraryPanel]) -> FakeLibraryPanel:
        assert selector == "#ps-playlists"
        assert widget_type is LibraryPanel
        return self.panel


class FakeSidebarHost(SidebarMixin):
    def __init__(self) -> None:
        self.ytmusic = FakeYTMusic()
        self._current_page = "library"
        self._current_page_kwargs = {"playlist_id": "VLPL123"}
        self.panel = FakeLibraryPanel()
        self.notifications: list[str] = []
        self.navigation_calls: list[tuple[str, dict]] = []
        self.opened_edit_popups: list[tuple[dict, str, str, str]] = []
        self.purged: list[set[str]] = []
        self.call_order: list[str] = []
        self._active_library_playlist_id: str | None = None
        self.active_id_at_navigate: str | None = "UNSET"
        self._nav_idle = asyncio.Event()
        self._nav_idle.set()

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append(message)

    def _purge_playlist_nav_state(self, dead_ids: set[str]) -> None:
        self.purged.append(set(dead_ids))
        self.call_order.append("purge")

    def query_one(self, selector: str, widget_type: type[PlaylistSidebar]) -> FakePlaylistSidebar:
        assert selector == "#playlist-sidebar"
        assert widget_type is PlaylistSidebar
        return FakePlaylistSidebar(self.panel)

    async def navigate_to(self, page_name: str, **kwargs: object) -> None:
        self.navigation_calls.append((page_name, kwargs))
        self.call_order.append("navigate")
        self.active_id_at_navigate = self._active_library_playlist_id

    def _open_edit_popup(self, item: dict, name: str, description: str, privacy: str) -> None:
        self.opened_edit_popups.append((item, name, description, privacy))


async def test_delete_current_playlist_navigates_when_active_id_has_vl_prefix():
    host = FakeSidebarHost()

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.panel.removed_ids == ["PL123"]
    assert host.navigation_calls == [("library", {"playlist_id": None})]


async def test_delete_purges_nav_state_with_all_id_forms():
    """T14: deleting a playlist must drop cached nav state under every id
    form (as-given, VL-stripped, VL-prefixed) or a later bare footer-nav
    to Library reopens the deleted playlist."""
    host = FakeSidebarHost()

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.purged == [{"PL123", "VLPL123"}]


async def test_delete_purges_after_navigation():
    """navigate_to refreshes the dying page's state back into the cache, so
    the purge must run AFTER the navigate-to-plain-library step."""
    host = FakeSidebarHost()

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.call_order == ["navigate", "purge"]


async def test_delete_purges_even_when_playlist_not_open():
    host = FakeSidebarHost()
    host._current_page_kwargs = {"playlist_id": "VLOTHER"}

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.navigation_calls == []
    assert host.purged == [{"PL123", "VLPL123"}]


async def test_delete_clears_last_played_fallback_before_navigating():
    """The plain LibraryPage mounted by the navigate-away falls back to
    app._active_library_playlist_id in on_mount — the dead id must be
    cleared BEFORE navigate_to or the deleted playlist auto-reloads."""
    host = FakeSidebarHost()
    host._active_library_playlist_id = "VLPL123"

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host._active_library_playlist_id is None
    assert host.active_id_at_navigate is None


async def test_delete_keeps_unrelated_last_played_fallback():
    host = FakeSidebarHost()
    host._active_library_playlist_id = "VLOTHER"

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host._active_library_playlist_id == "VLOTHER"


async def test_delete_navigates_away_from_open_playlist_context_page():
    """A playlist opened from search/browse mounts as a context page —
    deleting it must navigate away just like the library case."""
    host = FakeSidebarHost()
    host._current_page = "context"
    host._current_page_kwargs = {"context_type": "playlist", "context_id": "VLPL123"}

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.navigation_calls == [("library", {"playlist_id": None})]


async def test_delete_ignores_unrelated_context_page():
    host = FakeSidebarHost()
    host._current_page = "context"
    host._current_page_kwargs = {"context_type": "playlist", "context_id": "VLOTHER"}

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.navigation_calls == []


async def test_fetch_playlist_meta_failure_notifies_without_opening_edit_popup():
    host = FakeSidebarHost()

    await host._fetch_playlist_meta_for_edit({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.notifications == ["Failed to load playlist details"]
    assert host.opened_edit_popups == []
