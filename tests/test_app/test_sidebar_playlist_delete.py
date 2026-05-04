from __future__ import annotations

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

    def notify(self, message: str, **kwargs: object) -> None:
        self.notifications.append(message)

    def query_one(self, selector: str, widget_type: type[PlaylistSidebar]) -> FakePlaylistSidebar:
        assert selector == "#playlist-sidebar"
        assert widget_type is PlaylistSidebar
        return FakePlaylistSidebar(self.panel)

    async def navigate_to(self, page_name: str, **kwargs: object) -> None:
        self.navigation_calls.append((page_name, kwargs))

    def _open_edit_popup(self, item: dict, name: str, description: str, privacy: str) -> None:
        self.opened_edit_popups.append((item, name, description, privacy))


async def test_delete_current_playlist_navigates_when_active_id_has_vl_prefix():
    host = FakeSidebarHost()

    await host._delete_sidebar_playlist({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.panel.removed_ids == ["PL123"]
    assert host.navigation_calls == [("library", {"playlist_id": None})]


async def test_fetch_playlist_meta_failure_notifies_without_opening_edit_popup():
    host = FakeSidebarHost()

    await host._fetch_playlist_meta_for_edit({"playlistId": "PL123", "title": "Roadtrip"})

    assert host.notifications == ["Failed to load playlist details"]
    assert host.opened_edit_popups == []
