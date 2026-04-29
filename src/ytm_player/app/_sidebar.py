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

    async def on_playlist_sidebar_playlist_selected(
        self, message: PlaylistSidebar.PlaylistSelected
    ) -> None:
        """Navigate to library with the selected playlist."""
        item = message.item_data
        playlist_id = item.get("playlistId") or item.get("browseId")
        if playlist_id:
            await self.navigate_to("library", playlist_id=playlist_id)

    # First batch size for progressive playlist loading.
    _SIDEBAR_FIRST_BATCH = 300

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
            data = await self.ytmusic.get_playlist(
                playlist_id, limit=self._SIDEBAR_FIRST_BATCH, order="recently_added"
            )
            raw_tracks = data.get("tracks", [])
            tracks = normalize_tracks(raw_tracks)
            if not tracks:
                self.notify("Playlist is empty", severity="warning")
                return
            self.queue.clear()
            self.queue.add_multiple(tracks)
            self.queue.jump_to(0)
            self._active_library_playlist_id = playlist_id
            await self.play_track(self.queue.current_track)

            # Background-fetch remaining tracks and append to queue.
            total_count = data.get("trackCount") or len(raw_tracks)
            if total_count > len(raw_tracks):
                self.run_worker(
                    self._fetch_remaining_for_queue(playlist_id, len(raw_tracks)),
                    name="sidebar-fetch-remaining",
                )
        except Exception:
            logger.exception("Failed to load playlist %s for playback", playlist_id)
            self.notify("Failed to load playlist", severity="error")

    async def _fetch_remaining_for_queue(self, playlist_id: str, already_have: int) -> None:
        """Background fetch remaining tracks and append to the queue."""
        from ytm_player.utils.formatting import normalize_tracks

        if not self.ytmusic:
            return
        try:
            remaining = await self.ytmusic.get_playlist_remaining(
                playlist_id, already_have, order="recently_added"
            )
            if remaining:
                tracks = normalize_tracks(remaining)
                self.queue.add_multiple(tracks)
        except Exception:
            logger.debug("Failed to fetch remaining playlist tracks for queue", exc_info=True)

    async def _add_playlist_to_queue(self, playlist_id: str) -> None:
        """Fetch playlist tracks and add them to the queue."""
        from ytm_player.utils.formatting import normalize_tracks

        if not self.ytmusic:
            return
        try:
            data = await self.ytmusic.get_playlist(playlist_id)
            tracks = normalize_tracks(data.get("tracks", []))
            if tracks:
                self.queue.add_multiple(tracks)
                self._refresh_queue_page()
                self.notify(f"Added {len(tracks)} tracks to queue", timeout=2)
            else:
                self.notify("Playlist is empty", severity="warning", timeout=2)
        except Exception:
            logger.debug("Failed to add playlist to queue", exc_info=True)
            self.notify("Failed to add to queue", severity="error", timeout=2)

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
                if pid:
                    self.run_worker(self.navigate_to("library", playlist_id=pid))
            elif action_id == "add_to_queue":
                pid = item.get("playlistId") or item.get("browseId")
                if pid:
                    self.run_worker(self._add_playlist_to_queue(pid))
                else:
                    self.notify("No playlist ID found", severity="error", timeout=2)
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
