"""Playback coordination mixin for YTMPlayerApp."""

from __future__ import annotations

import asyncio
import logging
import time
from typing import Any

from ytm_player.ui.header_bar import HeaderBar
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import get_video_id

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_FAILURES = 5


class PlaybackMixin:
    """Playback coordination, player event callbacks, history logging, download."""

    async def play_track(self, track: dict) -> None:
        """Resolve a stream URL and start playback for a track.

        This is the main entry point for initiating playback from any
        page or action.
        """
        if not self.player or not self.stream_resolver:
            self.notify(
                "Player is still starting up. Please try again in a moment.", severity="error"
            )
            return

        video_id = get_video_id(track)

        # Debounce rapid duplicate calls (e.g. double-click).
        now = time.monotonic()
        if video_id and video_id == self._last_play_video_id and (now - self._last_play_time) < 1.0:
            return
        if video_id:
            self._last_play_video_id = video_id
            self._last_play_time = now
        if not video_id:
            self._consecutive_failures += 1
            title = track.get("title", "Unknown")
            self.notify(
                f'Skipping "{title}" — no video ID (AI-generated streams are not supported).',
                severity="warning",
                timeout=3,
            )
            if self._consecutive_failures < _MAX_CONSECUTIVE_FAILURES:
                next_track = self.queue.next_track()
                if next_track:
                    self.call_later(lambda: self.run_worker(self.play_track(next_track)))
            else:
                self.notify(
                    "Multiple tracks unplayable — check if your account has access.",
                    severity="error",
                    timeout=6,
                )
                self._consecutive_failures = 0
            return

        # Log listen time for the previous track.
        await self._log_current_listen()

        # Update UI immediately -- show track info before stream resolves.
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_track(track)
            bar.update_playback_state(is_playing=False, is_paused=False)
        except Exception:
            logger.debug("Playback bar not ready during play_track", exc_info=True)

        # Resolve the stream URL.
        try:
            stream_info = await self.stream_resolver.resolve(video_id)
        except Exception:
            logger.debug("Stream resolution raised for %s", video_id, exc_info=True)
            stream_info = None

        if stream_info is None:
            self._consecutive_failures += 1
            title = track.get("title", video_id)
            self.notify(
                f'Couldn\'t play "{title}" — track may be unavailable or region-locked. '
                f"Skipping...",
                severity="error",
                timeout=4,
            )
            # Auto-advance to the next track unless we've failed too many times.
            if self._consecutive_failures < _MAX_CONSECUTIVE_FAILURES:
                next_track = self.queue.next_track()
                if next_track:
                    self.call_later(lambda: self.run_worker(self.play_track(next_track)))
            else:
                # Likely a systemic issue (stale session, network) -- reset
                # the yt-dlp instance so the next attempt gets a fresh one.
                self.stream_resolver.clear_cache()
                logger.warning(
                    "Reset yt-dlp after %d consecutive stream failures",
                    self._consecutive_failures,
                )
                self.notify(
                    "Multiple tracks failed — stream resolver reset. Try playing again.",
                    severity="error",
                    timeout=6,
                )
                self._consecutive_failures = 0
            return

        self._consecutive_failures = 0

        # Start playback.
        try:
            await self.player.play(stream_info.url, track)
        except Exception:
            logger.debug("player.play() failed for %s", video_id, exc_info=True)
            self._consecutive_failures += 1
            if self._consecutive_failures < _MAX_CONSECUTIVE_FAILURES:
                next_track = self.queue.next_track()
                if next_track:
                    self.call_later(lambda: self.run_worker(self.play_track(next_track)))
            else:
                self.stream_resolver.clear_cache()
                logger.warning(
                    "Reset yt-dlp after %d consecutive play failures",
                    self._consecutive_failures,
                )
                self.notify(
                    "Multiple tracks failed — stream resolver reset. Try playing again.",
                    severity="error",
                    timeout=6,
                )
                self._consecutive_failures = 0
            return
        self._track_start_position = 0.0

        # Update Discord Rich Presence.
        if self.discord and self.discord.is_connected:
            await self.discord.update(
                title=track.get("title") or "",
                artist=track.get("artist") or "",
                album=track.get("album") or "",
                duration=stream_info.duration,
            )

        # Send Last.fm "Now Playing".
        if self.lastfm and self.lastfm.is_connected:
            await self.lastfm.now_playing(
                title=track.get("title") or "",
                artist=track.get("artist") or "",
                album=track.get("album") or "",
                duration=stream_info.duration,
            )

        # Update MPRIS metadata.
        if self.mpris:
            duration_us = int((stream_info.duration or 0) * 1_000_000)
            await self.mpris.update_metadata(
                title=track.get("title") or "",
                artist=track.get("artist") or "",
                album=track.get("album") or "",
                art_url=track.get("thumbnail_url") or "",
                length_us=duration_us,
            )
            await self.mpris.update_playback_status("Playing")

        # Update macOS Now Playing metadata.
        if self.mac_media:
            duration_us = int((stream_info.duration or 0) * 1_000_000)
            await self.mac_media.update_metadata(
                title=track.get("title") or "",
                artist=track.get("artist") or "",
                album=track.get("album") or "",
                length_us=duration_us,
            )
            await self.mac_media.update_playback_status("Playing")

    async def _toggle_play_pause(self) -> None:
        """Toggle play/pause, starting playback from queue if player is idle."""
        if self.player and self.player.current_track is None and self.queue.current_track:
            await self.play_track(self.queue.current_track)
        elif self.player:
            await self.player.toggle_pause()

    async def _play_next(self, *, ended_track: dict | None = None) -> None:
        """Advance to the next track in the queue and play it."""
        track = self.queue.next_track()
        if track:
            await self.play_track(track)
        elif self.settings.playback.autoplay:
            # Use the ended track for radio seed when player.current_track
            # is already None (cleared by _on_end_file before we get here).
            seed = ended_track or (self.player.current_track if self.player else None)
            if seed:
                await self._fetch_and_play_radio(seed_track=seed)
            else:
                self.notify("End of queue.", timeout=2)
        else:
            self.notify("End of queue.", timeout=2)

    async def _play_previous(self) -> None:
        """Go back to the previous track in the queue."""
        # If we're more than 3 seconds into a track, restart it instead.
        if self.player and self.player.position > 3.0:
            await self.player.seek_start()
            return

        track = self.queue.previous_track()
        if track:
            await self.play_track(track)

    async def _fetch_and_play_radio(self, seed_track: dict | None = None) -> None:
        """Fetch radio suggestions and continue playback.

        *seed_track* is used as the radio seed.  Falls back to the player's
        current track if not provided.
        """
        if not self.ytmusic:
            return

        track = seed_track or (self.player.current_track if self.player else None)
        if not track:
            return

        video_id = track.get("video_id", "")
        if not video_id:
            return

        self.notify("Loading radio suggestions...", timeout=3)
        try:
            from ytm_player.utils.formatting import normalize_tracks

            radio_tracks = normalize_tracks(await self.ytmusic.get_radio(video_id))
            if radio_tracks:
                self.queue.set_radio_tracks(radio_tracks)
                next_track = self.queue.next_track()
                if next_track:
                    await self.play_track(next_track)
                    return
        except Exception:
            logger.exception("Failed to fetch radio tracks")

        self.notify("No more suggestions available. Add more tracks to your queue.", timeout=3)

    # ── Player event callbacks ───────────────────────────────────────

    async def _on_track_end(self, event: Any = None) -> None:
        """Handle track ending -- advance to next.

        Uses ``_advancing`` flag to prevent duplicate end-file events
        from advancing the queue twice.  The *event* dict may contain a
        ``track`` key with the ended track's info (for history logging).
        """
        if self._advancing:
            logger.debug("Ignoring duplicate track-end while already advancing")
            return
        self._advancing = True
        logger.debug("Track ended (event=%s), advancing to next", event)
        try:
            # Log listen time using the ended track passed in the event,
            # since player.current_track is already None by the time this
            # callback runs.
            ended_track = event.get("track") if isinstance(event, dict) else None
            if ended_track:
                await self._log_listen_for(ended_track)
            await self._play_next(ended_track=ended_track)
        except asyncio.CancelledError:
            logger.debug("_on_track_end task was cancelled")
        except Exception:
            logger.debug("Error in _on_track_end", exc_info=True)
        finally:
            self._advancing = False

    def _poll_position(self) -> None:
        """Timer callback: poll the player position and update the bar."""
        if not self.player:
            return
        try:
            pos = self.player.position
            dur = self.player.duration
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_position(pos, dur)
        except Exception:
            logger.debug("Failed to poll playback position", exc_info=True)

        if self.mpris and self.player.is_playing:
            try:
                self.mpris.update_position(int(self.player.position * 1_000_000))
            except Exception:
                logger.debug("Failed to update MPRIS position", exc_info=True)

        if self.mac_media and self.player.is_playing:
            try:
                self.mac_media.update_position(int(self.player.position * 1_000_000))
            except Exception:
                logger.debug("Failed to update macOS media position", exc_info=True)

        # Check Last.fm scrobble threshold.
        if self.lastfm and self.lastfm.is_connected and self.player.is_playing:
            try:
                self.run_worker(
                    self.lastfm.check_scrobble(self.player.position),
                    group="scrobble",
                    exclusive=True,
                )
            except Exception:
                logger.debug("Failed to check Last.fm scrobble", exc_info=True)

    def _on_track_change(self, track: dict) -> None:
        """Handle track change event from the player.

        Called on the event loop via call_soon_threadsafe -- safe to touch widgets.
        """
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_track(track)
            bar.update_playback_state(is_playing=True, is_paused=False)
        except Exception:
            logger.debug("Failed to update playback bar on track change", exc_info=True)

        # Un-dim the header lyrics toggle.
        try:
            header = self.query_one("#app-header", HeaderBar)
            header.set_lyrics_dimmed(False)
        except Exception:
            pass

        # Update playing indicator on any visible TrackTable.
        video_id = track.get("video_id", "")
        try:
            page = self._get_current_page()
            if page:
                for table in page.query(TrackTable):
                    table.set_playing(video_id)
        except Exception:
            logger.debug("Failed to update playing indicator on track table", exc_info=True)

        # Show track change notification if enabled.
        try:
            if self.settings.notifications.enabled:
                title = track.get("title", "Unknown")
                artist = track.get("artist", "Unknown")
                fmt = self.settings.notifications.format
                try:
                    msg = fmt.format(title=title, artist=artist, album=track.get("album", ""))
                except (KeyError, ValueError):
                    msg = f"{title} — {artist}"
                self.notify(msg, timeout=self.settings.notifications.timeout_seconds)
        except Exception:
            logger.debug("Failed to show track change notification", exc_info=True)

        # Prefetch the next track's stream URL so "next" is instant.
        try:
            self._prefetch_next_track()
        except Exception:
            logger.debug("Failed to prefetch next track", exc_info=True)

    def _prefetch_next_track(self) -> None:
        """Prefetch the next track's stream URL in the background.

        Called after a new track starts playing so that hitting "next"
        or reaching the end of the current track starts instantly.
        """
        if not self.stream_resolver:
            return
        next_track = self.queue.peek_next()
        if next_track:
            next_id = next_track.get("video_id", "")
            if next_id:
                self.run_worker(
                    self.stream_resolver.prefetch(next_id),
                    group="prefetch",
                    exclusive=True,
                )

    def _on_volume_change(self, volume: int) -> None:
        """Handle volume change events."""
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_volume(volume)
        except Exception:
            logger.debug("Failed to update volume display", exc_info=True)

    def _on_pause_change(self, paused: bool) -> None:
        """Handle pause/resume events."""
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_playback_state(is_playing=not paused, is_paused=paused)
        except Exception:
            logger.debug("Failed to update pause state display", exc_info=True)

        if self.mpris:
            status = "Paused" if paused else "Playing"
            mpris = self.mpris
            try:
                self.call_later(
                    lambda s=status, svc=mpris: self.run_worker(svc.update_playback_status(s))
                )
            except Exception:
                logger.debug("Failed to update MPRIS playback status", exc_info=True)

        if self.mac_media:
            status = "Paused" if paused else "Playing"
            mac_media = self.mac_media
            try:
                self.call_later(
                    lambda s=status, svc=mac_media: self.run_worker(svc.update_playback_status(s))
                )
            except Exception:
                logger.debug("Failed to update macOS media status", exc_info=True)

        # Update Discord presence on pause/resume.
        if self.discord and self.discord.is_connected:
            try:
                if paused:
                    self.call_later(lambda: self.run_worker(self.discord.clear()))
                elif self.player and self.player.current_track:
                    t = self.player.current_track
                    self.call_later(
                        lambda: self.run_worker(
                            self.discord.update(
                                title=t.get("title", ""),
                                artist=t.get("artist", ""),
                                album=t.get("album", ""),
                                position=self.player.position if self.player else 0,
                            )
                        )
                    )
            except Exception:
                logger.debug("Failed to update Discord presence", exc_info=True)

    # ── History logging ──────────────────────────────────────────────

    async def _log_current_listen(self) -> None:
        """Log the listen duration for the currently playing track."""
        if not self.history or not self.player or not self.player.current_track:
            return

        listened = int(self.player.position - self._track_start_position)
        if listened > 0:
            try:
                await self.history.log_play(
                    track=self.player.current_track,
                    listened_seconds=listened,
                    source="tui",
                )
            except Exception:
                logger.exception("Failed to log play history")

    async def _log_listen_for(self, track: dict) -> None:
        """Log listen duration for an explicit track dict.

        Used by ``_on_track_end`` where ``player.current_track`` has
        already been cleared by the time the callback executes.
        """
        if not self.history or not self.player:
            return

        listened = int(self.player.position - self._track_start_position)
        if listened > 0:
            try:
                await self.history.log_play(
                    track=track,
                    listened_seconds=listened,
                    source="tui",
                )
            except Exception:
                logger.exception("Failed to log play history")

    # ── Download ─────────────────────────────────────────────────────

    async def _download_track(self, track: dict) -> None:
        """Download a single track for offline playback."""
        video_id = get_video_id(track)
        if not video_id:
            self.notify("Track has no video ID.", severity="warning", timeout=2)
            return

        if self.downloader.is_downloaded(video_id):
            self.notify("Already downloaded.", timeout=2)
            return

        title = track.get("title", video_id)
        self.notify(f"Downloading: {title}", timeout=3)

        result = await self.downloader.download(video_id)
        if result.success:
            self.notify(f"Downloaded: {title}", timeout=3)
            # Index in cache if available.
            if self.cache and result.file_path:
                try:
                    fmt = result.file_path.suffix.lstrip(".")
                    await self.cache.put_file(video_id, result.file_path, fmt)
                except Exception:
                    logger.debug("Failed to index downloaded file in cache", exc_info=True)
        else:
            error = result.error or "Unknown error"
            self.notify(f"Download failed: {error}", severity="error", timeout=4)
