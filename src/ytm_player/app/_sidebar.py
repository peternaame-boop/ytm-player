"""Sidebar toggling and playlist sidebar event handling mixin for YTMPlayerApp."""

from __future__ import annotations

import logging

from ytm_player.app._base import YTMHostBase
from ytm_player.ui.header_bar import HeaderBar
from ytm_player.ui.popups.actions import ActionsPopup
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar
from ytm_player.ui.sidebars.playlist_sidebar import LibraryPanel, PlaylistSidebar

logger = logging.getLogger(__name__)


class SidebarMixin(YTMHostBase):
    """Sidebar toggling and playlist sidebar event handlers."""

    # ── Sidebar toggling ────────────────────────────────────────────

    def _toggle_playlist_sidebar(self) -> None:
        """Toggle the playlist sidebar for the current page."""
        page = self._current_page or "library"
        current = self._sidebar_per_page.get(page, self._sidebar_default)
        new_state = not current
        self._sidebar_per_page[page] = new_state
        self._apply_playlist_sidebar(new_state)

    def _toggle_lyrics_sidebar(self) -> None:
        """Toggle the lyrics sidebar globally."""
        self._lyrics_sidebar_open = not self._lyrics_sidebar_open
        self._apply_lyrics_sidebar(self._lyrics_sidebar_open)

    def _apply_playlist_sidebar(self, visible: bool) -> None:
        """Set playlist sidebar visibility and update header bar state."""
        try:
            ps = self.query_one("#playlist-sidebar", PlaylistSidebar)
            if visible:
                ps.remove_class("hidden")
            else:
                ps.add_class("hidden")
        except Exception:
            logger.debug("Failed to apply playlist sidebar visibility", exc_info=True)
        try:
            header = self.query_one("#app-header", HeaderBar)
            header.set_playlist_state(visible)
        except Exception:
            pass

    def _apply_lyrics_sidebar(self, visible: bool) -> None:
        """Set lyrics sidebar visibility and update header bar state."""
        try:
            ls = self.query_one("#lyrics-sidebar", LyricsSidebar)
            if visible:
                ls.remove_class("hidden")
                ls.activate()
            else:
                ls.add_class("hidden")
        except Exception:
            logger.debug("Failed to apply lyrics sidebar visibility", exc_info=True)
        # Toggle the screen-level "lyrics-open" class so app CSS rules
        # (e.g. ToastRack offset) can react to lyrics being visible.
        try:
            screen = self.screen
            if visible:
                screen.add_class("lyrics-open")
            else:
                screen.remove_class("lyrics-open")
        except Exception:
            logger.debug("Failed to toggle lyrics-open class on screen", exc_info=True)
        try:
            header = self.query_one("#app-header", HeaderBar)
            header.set_lyrics_state(visible)
        except Exception:
            pass

    def _toggle_album_art(self) -> None:
        """Toggle album art visibility in the playback bar."""
        try:
            from ytm_player.ui.widgets.album_art import AlbumArt

            art = self.query_one("#pb-art", AlbumArt)
            art.display = not art.display
        except Exception:
            logger.debug("Failed to toggle album art visibility", exc_info=True)

    # ── Sidebar message handlers ─────────────────────────────────────

    def on_header_bar_toggle_playlist_sidebar(
        self, message: HeaderBar.TogglePlaylistSidebar
    ) -> None:
        self._toggle_playlist_sidebar()

    def on_header_bar_toggle_lyrics_sidebar(self, message: HeaderBar.ToggleLyricsSidebar) -> None:
        self._toggle_lyrics_sidebar()

    async def on_header_bar_back_requested(self, message: HeaderBar.BackRequested) -> None:
        """Header back-button click → pop the nav stack."""
        await self.navigate_to("back")

    async def on_header_bar_forward_requested(self, message: HeaderBar.ForwardRequested) -> None:
        """Header forward-button click → pop the forward stack."""
        await self.navigate_to("forward")

    async def on_playlist_sidebar_playlist_selected(
        self, message: PlaylistSidebar.PlaylistSelected
    ) -> None:
        """Navigate to library with the selected playlist."""
        item = message.item_data
        playlist_id = item.get("playlistId") or item.get("browseId")
        if playlist_id:
            await self.navigate_to("library", playlist_id=playlist_id)

    async def on_playlist_sidebar_playlist_double_clicked(
        self, message: PlaylistSidebar.PlaylistDoubleClicked
    ) -> None:
        """Queue all tracks from double-clicked playlist and start playback."""
        item = message.item_data
        playlist_id = item.get("playlistId") or item.get("browseId")
        name = item.get("title", "playlist")
        if playlist_id:
            prev = self.queue.current_track
            await self._play_playlist(playlist_id, name, order="recently_added")
            if self.queue.current_track is not None and self.queue.current_track is not prev:
                self._active_library_playlist_id = playlist_id
                await self.navigate_to("queue")

    def on_playlist_sidebar_playlist_right_clicked(
        self, message: PlaylistSidebar.PlaylistRightClicked
    ) -> None:
        """Open context menu for right-clicked playlist."""
        item = message.item_data
        if item is not None:
            self._open_playlist_context_menu(item)
        else:
            self._prompt_create_playlist()

    async def on_playlist_sidebar_nav_item_clicked(
        self, message: PlaylistSidebar.NavItemClicked
    ) -> None:
        """Navigate to liked_songs or recently_played, or start discovery mix."""
        if message.nav_id == "discovery_mix":
            self.run_worker(self._start_discovery_mix(), exclusive=True)
        else:
            await self.navigate_to(message.nav_id)

    def _open_playlist_context_menu(self, item: dict) -> None:
        """Push ActionsPopup for a sidebar playlist item."""

        def _handle_action(action_id: str | None) -> None:
            if action_id is None:
                return
            if action_id in ("play_all", "shuffle_play"):
                pid = item.get("playlistId") or item.get("browseId")

                async def _play_and_navigate() -> None:
                    prev = self.queue.current_track
                    await self._dispatch_entity_action(action_id, item, "playlist")
                    if (
                        self.queue.current_track is not None
                        and self.queue.current_track is not prev
                    ):
                        self._active_library_playlist_id = pid
                        await self.navigate_to("queue")

                self.run_worker(_play_and_navigate())
            elif action_id == "delete":
                from ytm_player.ui.popups.confirm_popup import ConfirmPopup

                title = item.get("title", "this playlist")

                def _on_confirm(confirmed: bool | None) -> None:
                    # ``None`` means the popup was dismissed without an
                    # explicit choice — treat as "don't delete".
                    if confirmed:
                        self.run_worker(self._delete_sidebar_playlist(item))

                self.push_screen(
                    ConfirmPopup(f"Are you sure you want to delete '{title}'?"),
                    _on_confirm,
                )
            elif action_id == "copy_link":
                try:
                    ps = self.query_one("#playlist-sidebar", PlaylistSidebar)
                    ps.copy_item_link(item)
                except Exception:
                    pass
            else:
                self.run_worker(self._dispatch_entity_action(action_id, item, "playlist"))

        self.push_screen(ActionsPopup(item, item_type="playlist"), _handle_action)

    def _prompt_create_playlist(self) -> None:
        """Show an input screen to create a new playlist."""
        from ytm_player.ui.popups.input_popup import InputPopup

        def _on_name(name: str | None) -> None:
            if name and name.strip():
                self.run_worker(self._create_sidebar_playlist(name.strip()))

        self.push_screen(InputPopup("New Playlist", placeholder="Playlist name..."), _on_name)

    async def _create_sidebar_playlist(self, name: str) -> None:
        """Create a new playlist and refresh the sidebar."""
        if not self.ytmusic:
            return
        try:
            playlist_id = await self.ytmusic.create_playlist(name)
            if playlist_id:
                self.notify(f"Created '{name}'", timeout=2)
                ps = self.query_one("#playlist-sidebar", PlaylistSidebar)
                panel = ps.query_one("#ps-playlists", LibraryPanel)
                panel.prepend_item({"playlistId": playlist_id, "title": name})
            else:
                self.notify("Failed to create playlist", severity="error", timeout=3)
        except Exception:
            logger.exception("Failed to create playlist %r", name)
            self.notify("Failed to create playlist", severity="error", timeout=3)

    async def _delete_sidebar_playlist(self, item: dict) -> None:
        """Delete or remove a playlist and refresh the sidebar."""
        if not self.ytmusic:
            return
        playlist_id = item.get("playlistId") or item.get("browseId", "")
        title = item.get("title", "playlist")
        if not playlist_id:
            self.notify("Cannot determine playlist ID", severity="error", timeout=3)
            return
        raw_id = playlist_id[2:] if playlist_id.startswith("VL") else playlist_id
        from ytm_player.services.ytmusic import mutation_failure_suffix

        try:
            # Try delete first (owned playlists), fall back to remove from
            # library (subscribed playlists/albums).
            result = await self.ytmusic.delete_playlist(playlist_id)
            if result != "success":
                result = await self.ytmusic.remove_album_from_library(raw_id)
            if result == "success":
                self.notify(f"Removed '{title}'", timeout=2)
                ps = self.query_one("#playlist-sidebar", PlaylistSidebar)
                ps.query_one("#ps-playlists", LibraryPanel).remove_item(raw_id)
            else:
                suffix = mutation_failure_suffix(result)
                self.notify(f"Failed to remove playlist — {suffix}", severity="error", timeout=4)
        except Exception:
            logger.exception("Failed to remove playlist %r", playlist_id)
            self.notify("Failed to remove playlist", severity="error", timeout=3)
