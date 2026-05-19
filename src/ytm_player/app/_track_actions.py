"""Track action handling mixin for YTMPlayerApp."""

from __future__ import annotations

import logging
from typing import Any

from ytm_player.app._base import YTMHostBase
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.popups.actions import ActionsPopup
from ytm_player.ui.popups.playlist_picker import PlaylistPicker
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import copy_to_clipboard, get_video_id

logger = logging.getLogger(__name__)


class TrackActionsMixin(YTMHostBase):
    """Track table integration and actions popup wiring."""

    async def on_track_table_track_selected(self, message: TrackTable.TrackSelected) -> None:
        """Handle track selection from any TrackTable widget."""
        track = message.track
        index = message.index

        # Set the queue position and play.
        self.queue.jump_to_real(index)
        await self.play_track(track)

    def _get_focused_track(self) -> dict | None:
        """Try to get a track dict from the currently focused widget."""
        focused = self.focused
        if focused is None:
            return None

        # Walk up to find a TrackTable parent.
        widget = focused
        while widget is not None:
            if isinstance(widget, TrackTable):
                return widget.selected_track
            widget = widget.parent
        return None

    async def _open_add_to_playlist(self) -> None:
        """Open PlaylistPicker for the currently playing track."""
        track = None

        # Prefer the currently playing track.
        if self.player and self.player.current_track:
            track = self.player.current_track

        if not track:
            self.notify("No track is playing.", severity="warning", timeout=2)
            return

        video_id = get_video_id(track)
        if not video_id:
            self.notify("Track has no video ID.", severity="warning", timeout=2)
            return

        self.push_screen(PlaylistPicker(video_ids=[video_id], tracks=[track]))

    async def _open_track_actions(self) -> None:
        """Open ActionsPopup for the focused track."""
        track = self._get_focused_track()
        if not track:
            # Fall back to currently playing track.
            if self.player and self.player.current_track:
                track = self.player.current_track
            else:
                self.notify("No track selected.", severity="warning", timeout=2)
                return

        self._open_actions_for_track(track)

    def _open_actions_for_track(self, track: dict) -> None:
        """Push ActionsPopup for a specific track dict."""

        def _handle_action_result(action_id: str | None) -> None:
            """Callback when the user picks an action from the popup."""
            if action_id is None:
                return

            if action_id == "add_to_playlist":
                video_id = get_video_id(track)
                if video_id:
                    self.push_screen(PlaylistPicker(video_ids=[video_id], tracks=[track]))
                return

            if action_id == "play":
                self.run_worker(self.play_track(track))
            elif action_id == "download":
                self.run_worker(self._download_track(track))
            elif action_id == "play_next":
                self.queue.add_next(track)
                self._refresh_queue_page()
                self.notify("Playing next", timeout=2)
            elif action_id == "add_to_queue":
                self.queue.add(track)
                self._refresh_queue_page()
                self.notify("Added to queue", timeout=2)
            elif action_id == "remove_from_queue":
                video_id = get_video_id(track)
                if video_id:
                    for i, t in enumerate(self.queue.tracks):
                        if t.get("video_id") == video_id:
                            self.queue.remove(i)
                            self._refresh_queue_page()
                            self.notify("Removed from queue", timeout=2)
                            break
            elif action_id == "start_radio":
                self.run_worker(self._fetch_and_play_radio(track))
            elif action_id == "go_to_artist":
                artists = track.get("artists", [])
                if isinstance(artists, list) and artists:
                    artist = artists[0]
                    artist_id = artist.get("id") or artist.get("browseId", "")
                    if artist_id:
                        self.run_worker(
                            self.navigate_to("context", context_type="artist", context_id=artist_id)
                        )
            elif action_id == "go_to_album":
                album = track.get("album", {})
                album_id = (
                    track.get("album_id")
                    or (album.get("id") if isinstance(album, dict) else None)
                    or ""
                )
                if album_id:
                    self.run_worker(
                        self.navigate_to("context", context_type="album", context_id=album_id)
                    )
            elif action_id == "toggle_like":
                video_id = get_video_id(track)
                ytmusic = self.ytmusic
                if video_id and ytmusic is not None:
                    is_liked = track.get("likeStatus") == "LIKE" or track.get("liked", False)
                    rating = "INDIFFERENT" if is_liked else "LIKE"
                    label = "Unliked" if is_liked else "Liked"

                    async def _rate(vid: str, r: str, lbl: str) -> None:
                        from ytm_player.services.ytmusic import mutation_failure_suffix

                        result = await ytmusic.rate_song(vid, r)
                        if result == "success":
                            track["likeStatus"] = r
                            self.notify(lbl, timeout=2)
                        else:
                            self.notify(
                                f"Couldn't {lbl.lower()} — {mutation_failure_suffix(result)}",
                                severity="error",
                                timeout=3,
                            )

                    self.run_worker(_rate(video_id, rating, label))
            elif action_id == "copy_link":
                video_id = get_video_id(track)
                if video_id:
                    link = f"https://music.youtube.com/watch?v={video_id}"
                    if copy_to_clipboard(link):
                        self.notify("Link copied", timeout=2)
                    else:
                        self.notify(link, timeout=5)
            elif action_id == "remove_from_playlist":
                self.run_worker(self._remove_track_from_playlist(track))

        # Detect whether this track is currently in the queue so the popup
        # can swap "Add to Queue" for "Remove from Queue".
        track_vid = get_video_id(track)
        in_queue = bool(track_vid) and any(
            t.get("video_id") == track_vid for t in self.queue.tracks
        )
        # Detect whether we're viewing a playlist so "Remove from Playlist"
        # can be offered.
        in_playlist = self._current_page == "library" and bool(
            self._current_page_kwargs.get("playlist_id")
        )
        self.push_screen(
            ActionsPopup(track, item_type="track", in_queue=in_queue, in_playlist=in_playlist),
            _handle_action_result,
        )

    def _refresh_queue_page(self) -> None:
        """Refresh the queue page if it's currently displayed."""
        try:
            from ytm_player.ui.pages.queue import QueuePage

            queue_page = self.query_one(QueuePage)
            queue_page._refresh_queue()
        except Exception:
            pass

    async def _remove_track_from_playlist(self, track: dict) -> None:
        """Remove *track* from the currently open playlist."""
        playlist_id = self._current_page_kwargs.get("playlist_id", "")
        if not playlist_id or not self.ytmusic:
            self.notify("No playlist context", severity="error", timeout=2)
            return

        video_id = track.get("video_id", "")
        set_video_id = track.get("setVideoId", "")
        if not video_id or not set_video_id:
            self.notify("Track missing required IDs", severity="error", timeout=2)
            return

        from ytm_player.services.ytmusic import mutation_failure_suffix
        from ytm_player.ui.pages.library import LibraryPage
        from ytm_player.ui.sidebars.playlist_sidebar import LibraryPanel, PlaylistSidebar
        from ytm_player.utils.formatting import strip_vl_prefix

        result = await self.ytmusic.remove_playlist_items(
            strip_vl_prefix(playlist_id),
            [{"videoId": video_id, "setVideoId": set_video_id}],
        )
        if result != "success":
            suffix = mutation_failure_suffix(result)
            self.notify(f"Failed to remove track — {suffix}", severity="error", timeout=3)
            return

        self.notify("Track removed", timeout=2)

        # Remove from the visible TrackTable and update counts.
        try:
            library = self.query_one(LibraryPage)
            table = library.query_one("#library-tracks", TrackTable)
            removed = table.remove_track(video_id, set_video_id=set_video_id)
            if removed:
                library.update_track_count()
            # Update sidebar count.
            ps = self.query_one("#playlist-sidebar", PlaylistSidebar)
            panel = ps.query_one("#ps-playlists", LibraryPanel)
            panel.update_item_count(playlist_id, -1)
        except Exception:
            logger.exception("Failed to update UI after track removal")

    def on_track_table_track_right_clicked(self, message: TrackTable.TrackRightClicked) -> None:
        """Handle right-click on any TrackTable -- open actions popup."""
        self._open_actions_for_track(message.track)

    def on_playback_bar_track_right_clicked(self, message: PlaybackBar.TrackRightClicked) -> None:
        """Handle right-click on the playback bar -- open actions popup."""
        self._open_actions_for_track(message.track)

    def on_selection_changed(self, message: Any) -> None:
        """Relay SelectionChanged messages to the SelectionInfoBar.

        SelectionChanged bubbles up the DOM from descendants (sidebar items,
        TrackTable rows). The bar is a sibling of those widgets — it's NOT
        an ancestor — so the bubble never reaches it directly. The App is
        the common ancestor: catch the message here and push the text to
        the bar by id.
        """
        try:
            from ytm_player.ui.selection_info_bar import SelectionInfoBar

            bar = self.query_one("#selection-info-bar", SelectionInfoBar)
            bar.text = getattr(message, "text", "")
        except Exception:
            logger.debug("Failed to relay SelectionChanged to bar", exc_info=True)
