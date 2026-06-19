"""Track action handling mixin for YTMPlayerApp."""

from __future__ import annotations

import logging
from typing import Any

from ytm_player.app._base import YTMHostBase
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.popups.actions import ActionsPopup
from ytm_player.ui.popups.playlist_picker import PlaylistPicker
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import copy_to_clipboard, get_video_id, normalize_tracks

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

    def _sync_shuffle_bar(self) -> None:
        try:
            bar = self.query_one("#playback-bar")
            bar.update_shuffle(self.queue.shuffle_enabled)  # type: ignore[attr-defined]
            bar.refresh_shuffle_lock_state()  # type: ignore[attr-defined]
        except Exception:
            pass

    async def _replace_queue_and_play(
        self,
        tracks: list[dict],
        *,
        entity_id: str | None = None,
        start_index: int = 0,
        shuffle: bool | None = None,
        autoplay: bool = True,
    ) -> None:
        if not tracks:
            self.notify("No tracks found", severity="warning", timeout=3)
            return
        if shuffle is True:
            import random

            tracks = list(tracks)
            random.shuffle(tracks)
        self.queue.clear()
        self.queue.add_multiple(tracks)
        self.queue.set_context(entity_id)
        if entity_id and self.shuffle_prefs.get(entity_id):
            if not self.queue.shuffle_enabled:
                self.queue.toggle_shuffle()
        elif shuffle is False:
            if self.queue.shuffle_enabled:
                self.queue.toggle_shuffle()
        self.queue.jump_to_real(start_index)
        self._refresh_queue_page()
        self._sync_shuffle_bar()
        if autoplay and self.queue.current_track is not None:
            await self.play_track(self.queue.current_track)

    def _append_to_queue(self, tracks: list[dict], label: str) -> None:
        self.queue.add_multiple(tracks)
        self._refresh_queue_page()
        self.notify(f"Added {label} ({len(tracks)} tracks) to queue", timeout=3)

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
            if track.get("_needs_reload_for_removal"):
                self.notify(
                    "Reload the playlist before removing a just-added track",
                    severity="warning",
                    timeout=3,
                )
            else:
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

    # ── Column-aware right-click handlers ─────────────────────────────

    def on_track_table_artist_right_clicked(self, message: TrackTable.ArtistRightClicked) -> None:
        """Handle right-click on Artist column -- open artist actions popup."""
        self._open_actions_for_artist(message.track)

    def on_track_table_album_right_clicked(self, message: TrackTable.AlbumRightClicked) -> None:
        """Handle right-click on Album column -- open album actions popup."""
        self._open_actions_for_album(message.track)

    def _open_actions_for_artist(self, track: dict) -> None:
        """Push ActionsPopup for an artist of a track.

        For multi-artist tracks, shows a picker first.
        """
        artists = track.get("artists", [])
        if not artists or not isinstance(artists, list):
            self.notify("No artist info available.", severity="warning", timeout=2)
            return
        valid = [a for a in artists if a.get("id") or a.get("browseId")]
        if not valid:
            self.notify("No artist info available.", severity="warning", timeout=2)
            return
        if len(valid) == 1:
            self._show_artist_actions(valid[0])
            return
        self._show_artist_picker(track, valid)

    def _show_artist_picker(self, track: dict, artists: list[dict]) -> None:
        """Show a picker popup for multi-artist tracks."""
        picker_actions = [(str(i), a.get("name", "Unknown Artist")) for i, a in enumerate(artists)]
        picker_item = {"title": "Select Artist"}

        def _on_pick(action_id: str | None) -> None:
            if action_id is None:
                return
            self._show_artist_actions(artists[int(action_id)], back_to=(track, artists))

        self.push_screen(ActionsPopup(picker_item, actions=picker_actions), _on_pick)

    def _show_artist_actions(
        self, artist: dict, back_to: tuple[dict, list[dict]] | None = None
    ) -> None:
        """Push the artist actions popup for a single artist dict."""
        browse_id = artist.get("id") or artist.get("browseId", "")
        if not browse_id:
            return
        item: dict[str, Any] = {
            "browseId": browse_id,
            "artist": artist.get("name", "Unknown Artist"),
        }

        def _handle(action_id: str | None) -> None:
            if action_id is None:
                return
            if action_id == "_back" and back_to:
                self._show_artist_picker(back_to[0], back_to[1])
            elif action_id == "toggle_subscribe":
                self.run_worker(self._toggle_artist_subscribe_simple(browse_id))
            elif action_id == "copy_link":
                link = f"https://music.youtube.com/browse/{browse_id}"
                if copy_to_clipboard(link):
                    self.notify("Link copied", timeout=2)
                else:
                    self.notify(link, timeout=5)
            else:
                self.run_worker(self._dispatch_entity_action(action_id, item, "artist"))

        actions: list[tuple[str, str]] | None = None
        if back_to:
            from ytm_player.ui.popups.actions import ARTIST_ACTIONS

            actions = list(ARTIST_ACTIONS) + [("_back", "← Back")]

        self.push_screen(ActionsPopup(item, item_type="artist", actions=actions), _handle)

    def _open_actions_for_album(self, track: dict) -> None:
        """Push ActionsPopup for the album of a track."""
        album = track.get("album", {})
        album_id = (
            track.get("album_id") or (album.get("id") if isinstance(album, dict) else None) or ""
        )
        if not album_id:
            self.notify("No album info available.", severity="warning", timeout=2)
            return
        album_name = album if isinstance(album, str) else album.get("name", "Unknown Album")
        item: dict[str, Any] = {
            "browseId": album_id,
            "title": album_name,
        }
        if track.get("artists"):
            item["artists"] = track["artists"]

        def _handle(action_id: str | None) -> None:
            if action_id is None:
                return
            if action_id == "copy_link":
                link = f"https://music.youtube.com/browse/{album_id}"
                if copy_to_clipboard(link):
                    self.notify("Link copied", timeout=2)
                else:
                    self.notify(link, timeout=5)
            else:
                self.run_worker(self._dispatch_entity_action(action_id, item, "album"))

        self.push_screen(ActionsPopup(item, item_type="album"), _handle)

    # ── Artist action methods (shared with SearchPage dispatch) ───────

    async def _start_artist_radio(self, browse_id: str) -> None:
        """Fetch artist data and start a radio from their radioId or top songs."""
        if not self.ytmusic:
            return
        self.notify("Loading radio...", timeout=3)
        data = await self.ytmusic.get_artist(browse_id)
        if not data:
            self.notify("Couldn't load artist data.", severity="warning", timeout=3)
            return
        artist_name = data.get("name", "Unknown Artist")
        radio_id = data.get("radioId")
        if radio_id:
            tracks = await self.ytmusic.get_watch_playlist(playlist_id=radio_id, radio=True)
            normalized = normalize_tracks(tracks)
            if normalized:
                self.queue.clear()
                self.queue.set_context(None)
                self.queue.set_radio_tracks(normalized)
                self._refresh_queue_page()
                first = self.queue.next_track()
                if first:
                    await self.play_track(first)
                self.notify(f"Playing: Radio from {artist_name}", timeout=4)
            else:
                self.notify("No radio suggestions available.", severity="warning", timeout=3)
        else:
            songs = data.get("songs", {})
            results = songs.get("results", []) if isinstance(songs, dict) else []
            seeds = [t for t in results if t.get("videoId")]
            if seeds:
                await self._fetch_and_play_radio(seeds, label=f"Radio from {artist_name}")
            else:
                self.notify("No songs to seed radio.", severity="warning", timeout=3)

    async def _play_artist_top_songs(self, browse_id: str) -> None:
        """Fetch artist top songs, queue them, and start playback."""
        if not self.ytmusic:
            return
        self.notify("Loading top songs...", timeout=3)
        data = await self.ytmusic.get_artist(browse_id)
        if not data:
            self.notify("Couldn't load artist data.", severity="warning", timeout=3)
            return
        songs_section = data.get("songs", {})
        top_tracks = normalize_tracks(
            songs_section.get("results", []) if isinstance(songs_section, dict) else []
        )
        if not top_tracks:
            self.notify("No songs found for this artist.", severity="warning", timeout=3)
            return
        artist_name = data.get("name", "Unknown Artist")
        await self._replace_queue_and_play(top_tracks, shuffle=None)
        self.notify(f"Playing top songs from {artist_name}", timeout=4)
        songs_browse_id = songs_section.get("browseId") if isinstance(songs_section, dict) else None
        if songs_browse_id:
            self.run_worker(
                self._fetch_remaining_artist_songs(songs_browse_id, top_tracks),
                name="fetch-artist-songs",
                exclusive=True,
            )

    async def _fetch_remaining_artist_songs(
        self, browse_id: str, initial_tracks: list[dict[str, Any]]
    ) -> None:
        """Background-fetch the full artist song list, enrich initial tracks, and append rest."""
        try:
            if not self.ytmusic:
                return
            pl = await self.ytmusic.get_playlist(browse_id)
            all_tracks = normalize_tracks(pl.get("tracks", []) if isinstance(pl, dict) else [])
            full_by_id = {t.get("video_id", ""): t for t in all_tracks if t.get("video_id")}
            existing_ids = {t.get("video_id", "") for t in initial_tracks}
            # Bail if the queue was replaced while we were fetching.
            queue_ids = {t.get("video_id", "") for t in self.queue.tracks}
            if not existing_ids & queue_ids:
                return
            for qt in self.queue.tracks:
                vid = qt.get("video_id", "")
                if vid in full_by_id:
                    qt.update(full_by_id[vid])
            for t in all_tracks:
                if t.get("video_id", "") not in existing_ids:
                    self.queue.add(t)
            self._refresh_queue_page()
        except Exception:
            logger.debug("Background artist songs fetch failed", exc_info=True)

    async def _toggle_artist_subscribe_simple(self, browse_id: str) -> None:
        """Subscribe/unsubscribe without cached state (used from track table context)."""
        from ytm_player.services.ytmusic import mutation_failure_suffix

        if not self.ytmusic:
            return
        data = await self.ytmusic.get_artist(browse_id)
        if not data or not data.get("channelId"):
            self.notify("Couldn't load artist data.", severity="warning", timeout=3)
            return
        channel_id = data["channelId"]
        is_subscribed = bool(data.get("subscribed"))
        if is_subscribed:
            result = await self.ytmusic.unsubscribe_artist(channel_id)
        else:
            result = await self.ytmusic.subscribe_artist(channel_id)
        if result == "success":
            label = "Unsubscribed" if is_subscribed else "Subscribed"
            self.notify(label, timeout=2)
        else:
            verb = "unsubscribe" if is_subscribed else "subscribe"
            self.notify(
                f"Couldn't {verb} — {mutation_failure_suffix(result)}",
                severity="error",
                timeout=3,
            )

    async def _add_album_to_library(self, album_id: str, album_name: str) -> None:
        """Add an album to the user's library."""
        from ytm_player.services.ytmusic import mutation_failure_suffix

        if not self.ytmusic:
            return
        album_data = await self.ytmusic.get_album(album_id)
        playlist_id = album_data.get("audioPlaylistId", "")
        if not playlist_id:
            self.notify("Couldn't add to library.", severity="warning", timeout=3)
            return
        result = await self.ytmusic.add_to_library(playlist_id)
        if result == "success":
            self.notify(f"Added {album_name} to library", timeout=2)
        else:
            self.notify(
                f"Couldn't add — {mutation_failure_suffix(result)}", severity="error", timeout=3
            )

    async def _play_album(self, album_id: str, album_name: str, *, shuffle: bool = False) -> None:
        """Fetch album tracks, replace queue, and start playback."""
        if not self.ytmusic:
            return
        self.notify("Loading album...", timeout=3)
        data = await self.ytmusic.get_album(album_id)
        tracks = normalize_tracks(data.get("tracks", []) if isinstance(data, dict) else [])
        await self._replace_queue_and_play(tracks, entity_id=album_id, shuffle=shuffle)
        action = "Shuffling" if shuffle else "Playing"
        self.notify(f"{action}: {album_name}", timeout=4)

    async def _add_album_to_queue(self, album_id: str, album_name: str) -> None:
        """Fetch album tracks and add them to the queue."""
        if not self.ytmusic:
            return
        data = await self.ytmusic.get_album(album_id)
        tracks = normalize_tracks(data.get("tracks", []) if isinstance(data, dict) else [])
        if not tracks:
            self.notify("No tracks found.", severity="warning", timeout=3)
            return
        self._append_to_queue(tracks, album_name)

    async def _dispatch_entity_action(self, action_id: str, item: dict, item_type: str) -> bool:
        """Dispatch an entity action to the correct method. Returns True if handled."""
        entity_id = item.get("browseId") or item.get("album_id") or item.get("playlistId") or ""
        entity_name = item.get("title") or item.get("name") or item.get("artist") or "Unknown"

        if action_id in ("play_all", "shuffle_play"):
            shuffle = action_id == "shuffle_play"
            if item_type == "album":
                if not entity_id:
                    self.notify("No ID available", severity="error", timeout=2)
                    return True
                await self._play_album(entity_id, entity_name, shuffle=shuffle)
            elif item_type == "playlist":
                if not entity_id:
                    self.notify("No ID available", severity="error", timeout=2)
                    return True
                await self._play_playlist(entity_id, entity_name, shuffle=shuffle)
            else:
                return False
            return True

        if action_id == "add_to_queue":
            if item_type == "album":
                if not entity_id:
                    self.notify("No ID available", severity="error", timeout=2)
                    return True
                await self._add_album_to_queue(entity_id, entity_name)
            elif item_type == "playlist":
                if not entity_id:
                    self.notify("No ID available", severity="error", timeout=2)
                    return True
                await self._add_playlist_to_queue(entity_id, entity_name)
            else:
                return False
            return True

        if action_id == "start_radio":
            if item_type == "artist":
                if not entity_id:
                    self.notify("No ID available", severity="error", timeout=2)
                    return True
                await self._start_artist_radio(entity_id)
            elif item_type == "playlist":
                await self._start_playlist_radio(item)
            else:
                return False
            return True

        if action_id == "play_top_songs":
            if item_type == "artist":
                if not entity_id:
                    self.notify("No ID available", severity="error", timeout=2)
                    return True
                await self._play_artist_top_songs(entity_id)
            else:
                return False
            return True

        if action_id in ("go_to_artist", "view_similar"):
            artists = item.get("artists") or []
            if isinstance(artists, list) and artists:
                artist_id = artists[0].get("id") or artists[0].get("browseId", "")
            else:
                artist_id = entity_id if item_type == "artist" else ""
            if not artist_id:
                self.notify("No ID available", severity="error", timeout=2)
                return True
            await self.navigate_to("context", context_type="artist", context_id=artist_id)
            return True

        if action_id == "go_to_album":
            album_id = item.get("album_id") or item.get("browseId") or ""
            if not album_id:
                self.notify("No ID available", severity="error", timeout=2)
                return True
            await self.navigate_to("context", context_type="album", context_id=album_id)
            return True

        if action_id == "add_to_library":
            if item_type == "album":
                if not entity_id:
                    self.notify("No ID available", severity="error", timeout=2)
                    return True
                await self._add_album_to_library(entity_id, entity_name)
            else:
                return False
            return True

        return False

    _PLAYLIST_FIRST_BATCH = 300

    async def _play_playlist(
        self,
        playlist_id: str,
        name: str,
        *,
        shuffle: bool | None = None,
        order: str | None = None,
    ) -> None:
        if not self.ytmusic:
            return
        try:
            data = await self.ytmusic.get_playlist(
                playlist_id, limit=self._PLAYLIST_FIRST_BATCH, order=order
            )
            raw_tracks = data.get("tracks", [])
            tracks = normalize_tracks(raw_tracks)
            if not tracks:
                self.notify("Playlist is empty", severity="warning")
                return
            await self._replace_queue_and_play(tracks, entity_id=playlist_id, shuffle=shuffle)
            self.notify(f"Playing: {name}", timeout=4)
            total_count = data.get("trackCount") or len(raw_tracks)
            if total_count > len(raw_tracks):
                self.run_worker(
                    self._fetch_remaining_for_queue(playlist_id, len(raw_tracks), order=order),
                    name="fetch-remaining-playlist",
                )
        except Exception:
            logger.exception("Failed to load playlist %s", playlist_id)
            self.notify("Failed to load playlist", severity="error")

    async def _add_playlist_to_queue(self, playlist_id: str, name: str) -> None:
        if not self.ytmusic:
            return
        try:
            data = await self.ytmusic.get_playlist(playlist_id)
            tracks = normalize_tracks(data.get("tracks", []))
            if tracks:
                self._append_to_queue(tracks, name)
            else:
                self.notify("Playlist is empty", severity="warning", timeout=2)
        except Exception:
            logger.debug("Failed to add playlist to queue", exc_info=True)
            self.notify("Failed to add to queue", severity="error", timeout=2)

    async def _start_playlist_radio(self, item: dict) -> None:
        """Start radio seeded from a playlist."""
        playlist_id = item.get("playlistId") or item.get("browseId")
        if not playlist_id or not self.ytmusic:
            return

        playlist_name = item.get("title", "playlist")
        self.notify(f"Starting radio from {playlist_name}...", timeout=3)
        try:
            radio_tracks = normalize_tracks(await self.ytmusic.get_playlist_radio(playlist_id))
        except Exception:
            logger.exception("Failed to start playlist radio for %r", playlist_id)
            self.notify("Failed to start radio", severity="error")
            return

        if radio_tracks:
            self.queue.clear()
            self.queue.set_radio_tracks(radio_tracks)
            self.queue.radio_seeds = [{"title": f"{playlist_name} Playlist"}]
            self.queue.set_context(playlist_id)
            if self.shuffle_prefs.get(playlist_id) and not self.queue.shuffle_enabled:
                self.queue.toggle_shuffle()
            self._refresh_queue_page()
            self._sync_shuffle_bar()
            first = self.queue.next_track()
            if first:
                await self.play_track(first)
                self.notify(f"Playing: Radio from {playlist_name}", timeout=4)
        else:
            self.notify("No radio tracks found", severity="warning", timeout=3)

    async def _fetch_remaining_for_queue(
        self, playlist_id: str, already_have: int, *, order: str | None = None
    ) -> None:
        """Background fetch remaining tracks and append to the queue."""
        if not self.ytmusic:
            return
        try:
            remaining = await self.ytmusic.get_playlist_remaining(
                playlist_id, already_have, order=order
            )
            if remaining:
                tracks = normalize_tracks(remaining)
                self.queue.add_multiple(tracks)
        except Exception:
            logger.debug("Failed to fetch remaining playlist tracks for queue", exc_info=True)

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
