"""Sidebar toggling and playlist sidebar event handling mixin for YTMPlayerApp."""

from __future__ import annotations

import asyncio
import logging

from ytm_player.ui.header_bar import HeaderBar
from ytm_player.ui.popups.actions import ActionsPopup
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar
from ytm_player.ui.sidebars.playlist_sidebar import PlaylistSidebar

logger = logging.getLogger(__name__)


class SidebarMixin:
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
        try:
            header = self.query_one("#app-header", HeaderBar)
            header.set_lyrics_state(visible)
        except Exception:
            pass

    # ── Sidebar message handlers ─────────────────────────────────────

    def on_header_bar_toggle_playlist_sidebar(
        self, message: HeaderBar.TogglePlaylistSidebar
    ) -> None:
        self._toggle_playlist_sidebar()

    def on_header_bar_toggle_lyrics_sidebar(self, message: HeaderBar.ToggleLyricsSidebar) -> None:
        self._toggle_lyrics_sidebar()

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
        from ytm_player.utils.formatting import normalize_tracks

        item = message.item_data
        playlist_id = item.get("playlistId") or item.get("browseId")
        if not playlist_id or not self.ytmusic:
            return
        try:
            data = await self.ytmusic.get_playlist(playlist_id, order="recently_added")
            tracks = normalize_tracks(data.get("tracks", []))
            if not tracks:
                self.notify("Playlist is empty", severity="warning")
                return
            self.queue.clear()
            self.queue.add_multiple(tracks)
            self.queue.jump_to(0)
            self._active_library_playlist_id = playlist_id
            await self.play_track(self.queue.current_track)
        except Exception:
            logger.exception("Failed to load playlist %s for playback", playlist_id)
            self.notify("Failed to load playlist", severity="error")

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
        """Navigate to liked_songs or recently_played from sidebar pinned nav."""
        await self.navigate_to(message.nav_id)

    def _open_playlist_context_menu(self, item: dict) -> None:
        """Push ActionsPopup for a sidebar playlist item."""

        def _handle_action(action_id: str | None) -> None:
            if action_id is None:
                return
            if action_id in ("play_all", "shuffle_play"):
                pid = item.get("playlistId") or item.get("browseId")
                if pid:
                    self.run_worker(self.navigate_to("library", playlist_id=pid))
            elif action_id == "add_to_queue":
                self.notify("Added to queue", timeout=2)
            elif action_id == "delete":
                from ytm_player.ui.popups.confirm_popup import ConfirmPopup

                title = item.get("title", "this playlist")

                def _on_confirm(confirmed: bool) -> None:
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
                await ps.refresh_playlists()
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
        try:
            # Try delete first (owned playlists), fall back to remove from library.
            success = False
            try:
                success = await self.ytmusic.delete_playlist(playlist_id)
            except Exception:
                pass
            if not success:
                success = await self.ytmusic.remove_album_from_library(raw_id)
            if success:
                self.notify(f"Removed '{title}'", timeout=2)
                await asyncio.sleep(1)
                ps = self.query_one("#playlist-sidebar", PlaylistSidebar)
                await ps.refresh_playlists()
            else:
                self.notify("Failed to remove playlist", severity="error", timeout=3)
        except Exception:
            logger.exception("Failed to remove playlist %r", playlist_id)
            self.notify("Failed to remove playlist", severity="error", timeout=3)
