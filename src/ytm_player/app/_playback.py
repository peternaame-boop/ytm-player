"""Playback coordination mixin for YTMPlayerApp."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

from ytm_player.app._base import YTMHostBase
from ytm_player.ui.header_bar import HeaderBar
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.widgets.track_table import TrackTable
from ytm_player.utils.formatting import get_video_id, normalize_tracks

logger = logging.getLogger(__name__)

_MAX_CONSECUTIVE_FAILURES = 5

# Poll player.position with a timer instead of relying on UI position events:
# timers keep firing when the terminal window loses focus, while position stays
# frozen during pause. Best of both: focus-independent, pause-aware.
_YTM_HISTORY_POLL_SECONDS = 1.0


@dataclass
class _LocalHistoryClaim:
    """Ownership token for one play's local-history row.

    One object per committed play — a same-video replay gets a fresh object,
    so identity comparison can never alias two plays. The report timer chain,
    the insert worker, and finalize all hold the same object; the only shared
    app state is ``app._local_history_claim`` (which play is *current*), and
    only scheduling/replacement writes it.
    """

    video_id: str
    track: dict
    generation: int
    # SQLite row id once the insert worker lands it. None while the worker is
    # in flight (or if the insert failed).
    row_id: int | None = None
    # Final listen duration stashed by finalize while the insert worker is in
    # flight; the worker applies it once the row exists.
    pending_seconds: int | None = None
    # The report chain handed this play to the insert worker.
    insert_started: bool = False
    # A finalize consumed this claim: stops the report chain and blocks a
    # second finalize from double-logging.
    finalized: bool = False


class PlaybackMixin(YTMHostBase):
    """Playback coordination, player event callbacks, history logging, download."""

    async def play_track(self, track: dict | None, *, recovery_of: int | None = None) -> None:
        """Resolve a stream URL and start playback for a track.

        This is the main entry point for initiating playback from any
        page or action.  ``track`` may be ``None`` when callers pass
        ``QueueManager.current_track`` on an empty queue — in that case
        we simply no-op.  ``recovery_of`` is the attempt this call is
        the one retry of (see ``_on_player_error``); the retry itself
        gets no retry.
        """
        if track is None:
            return
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
            title = track.get("title", "Unknown")
            self.notify(
                f'Skipping "{title}" — no video ID (AI-generated streams are not supported).',
                severity="warning",
                timeout=3,
            )
            self._handle_play_failure(
                exhausted_message="Multiple tracks unplayable — check if your account has access.",
                clear_debounce=False,
            )
            return

        # This call is committed — supersede any in-flight play_track.
        # Older calls abort at their next generation check instead of
        # stealing playback back or pushing stale metadata.
        self._play_generation += 1
        generation = self._play_generation
        # The single retry a failed attempt gets. Any other committed play
        # is a new request and starts without one in flight.
        self._recovery_generation = generation if recovery_of is not None else None

        # Log listen time for the previous track.
        await self._log_current_listen()

        if generation != self._play_generation:
            return

        # Update UI immediately -- show track info before stream resolves.
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_track(track)
            bar.update_playback_state(is_playing=False, is_paused=False)
        except Exception:
            logger.debug("Playback bar not ready during play_track", exc_info=True)

        # Try local audio cache first (previously downloaded or replayed track).
        stream_info = None
        if self.cache:
            try:
                cached_path = await self.cache.get(video_id)
            except Exception:
                logger.debug("Cache lookup failed for %s", video_id, exc_info=True)
                cached_path = None

            if cached_path is not None:
                # Build a minimal StreamInfo pointing at the local file.
                # Downstream code (Discord, Last.fm, MPRIS) only reads
                # .url and .duration — duration comes from the track dict.
                from ytm_player.services.stream import StreamInfo

                stream_info = StreamInfo(
                    url=str(cached_path),
                    video_id=video_id,
                    format=cached_path.suffix.lstrip(".") or "opus",
                    bitrate=0,  # unknown for cached files
                    duration=track.get("duration") or 0,
                    expires_at=float("inf"),  # local files don't expire
                    thumbnail_url=track.get("thumbnail_url"),
                )
                logger.info("Cache hit for %s — playing from %s", video_id, cached_path)

        # Resolve via yt-dlp if no cache hit.
        if stream_info is None:
            try:
                stream_info = await self.stream_resolver.resolve(video_id)
            except Exception:
                logger.debug("Stream resolution raised for %s", video_id, exc_info=True)
                stream_info = None

        # A newer call may have landed while we awaited cache/resolve.
        # Its failure tail must not run either — it would toast, advance
        # the queue, or reset the resolver out from under the winner.
        if generation != self._play_generation:
            logger.debug("play_track for %s superseded during resolve", video_id)
            return

        if stream_info is None:
            title = track.get("title", video_id)
            self.notify(
                f'Couldn\'t play "{title}" — track may be unavailable or region-locked. '
                f"Skipping...",
                severity="error",
                timeout=4,
            )
            self._handle_play_failure(
                exhausted_message="Multiple tracks failed — stream resolver reset. Try playing again.",
                failure_kind="stream",
            )
            return

        # Start playback. The lock makes gen-check + mpv command atomic:
        # without it a superseded call could still issue its (threaded)
        # play command after the winner's, stealing playback at the mpv
        # level while the app state says otherwise.
        async with self._play_lock:
            if generation != self._play_generation:
                logger.debug("play_track for %s superseded before play()", video_id)
                return
            # Load and stream failures are not raised here: player.play()
            # reports each exactly once as an ERROR event carrying this
            # attempt, and _on_player_error applies recovery and the
            # failure policy. The consecutive-failure counter is reset
            # only once audio is actually advancing (_poll_position).
            await self.player.play(stream_info.url, track, attempt=generation)
        self._track_start_position = 0.0

        if generation != self._play_generation:
            return

        # Only arm history reporting when the load actually started: play()
        # swallows load failures (clears current_track and reports an ERROR
        # event), so reaching here doesn't mean playback is on.
        if self.player.current_track is not None:
            self._schedule_local_history_log(track, video_id, generation)
            self._schedule_ytm_history_report(track, video_id, generation)

        # Apply pending resume position if this play matches the resumed track.
        # Only clear on a match — if the user plays a different track first,
        # leave pending state intact so they can come back to the resumed
        # track later.
        if self._pending_resume_video_id is not None and self._pending_resume_video_id == video_id:
            if self._pending_resume_position > 0:
                try:
                    await self.player.seek_absolute(self._pending_resume_position)
                    self._track_start_position = self._pending_resume_position
                except Exception:
                    logger.debug("Failed to seek to resume position", exc_info=True)
            self._pending_resume_video_id = None
            self._pending_resume_position = 0.0

        # Metadata fan-out: re-check the generation between each block —
        # every await below is a window for a newer play_track to land.
        if generation != self._play_generation:
            return

        # Update Discord Rich Presence.
        if self.discord:
            await self.discord.update(
                title=track.get("title") or "",
                artist=track.get("artist") or "",
                album=track.get("album") or "",
                duration=stream_info.duration,
                thumbnail_url=track.get("thumbnail_url") or "",
            )

        if generation != self._play_generation:
            return

        # Send Last.fm "Now Playing".
        if self.lastfm and self.lastfm.is_connected:
            await self.lastfm.now_playing(
                title=track.get("title") or "",
                artist=track.get("artist") or "",
                album=track.get("album") or "",
                duration=stream_info.duration,
            )

        if generation != self._play_generation:
            return

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

        if generation != self._play_generation:
            return

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

    def _handle_play_failure(
        self,
        *,
        exhausted_message: str,
        failure_kind: str | None = None,
        clear_debounce: bool = True,
    ) -> None:
        """Shared tail of play_track's failure paths.

        Bumps the consecutive-failure counter, auto-advances to the next
        queue track below the threshold, and escalates at it.
        ``failure_kind`` names the failure in the resolver-reset warning;
        ``None`` skips the resolver reset (no-video-id path).
        """
        if clear_debounce:
            # Clear debounce so the user can immediately retry the track.
            self._last_play_video_id = ""
            self._last_play_time = 0.0
        self._consecutive_failures += 1
        if self._consecutive_failures < _MAX_CONSECUTIVE_FAILURES:
            next_track = self.queue.next_track()
            if next_track:
                self.call_later(lambda: self.run_worker(self.play_track(next_track)))
        else:
            if failure_kind is not None and self.stream_resolver:
                # Likely a systemic issue (stale session, network) — reset
                # the yt-dlp instance so the next attempt gets a fresh one.
                self.stream_resolver.clear_cache()
                logger.warning(
                    "Reset yt-dlp after %d consecutive %s failures",
                    self._consecutive_failures,
                    failure_kind,
                )
            self.notify(exhausted_message, severity="error", timeout=6)
            self._consecutive_failures = 0

    async def _on_player_error(self, event: Any = None) -> None:
        """Recover once from a play attempt whose stream failed to open or died.

        ``event`` is the Player's ERROR payload: the failed attempt's
        token and track. Only the current attempt is acted on — a late
        error for an attempt that a newer play, a stop, or a replay has
        superseded is dropped, and so is a duplicate for one already
        handled. The first failure of a request drops every cached
        stream URL and the yt-dlp instance (stream URLs are bound to the
        IP that fetched them, so after a network change none of them
        open) and replays the track once; if that replay fails too, the
        request goes through the failure policy.
        """
        if not isinstance(event, dict):
            return
        attempt = event.get("attempt")
        track = event.get("track")
        if not isinstance(attempt, int) or not isinstance(track, dict):
            return
        if attempt != self._play_generation or attempt <= self._handled_error_attempt:
            logger.debug("Ignoring stream error for superseded or handled attempt %d", attempt)
            return
        self._handled_error_attempt = attempt
        video_id = get_video_id(track)
        title = track.get("title", video_id)
        if attempt == self._recovery_generation:
            self._recovery_generation = None
            logger.warning(
                "Stream for %s failed again after a resolver reset (%s); skipping",
                video_id,
                event.get("error"),
            )
            self.notify(
                f'Couldn\'t play "{title}" — stream failed twice. Skipping...',
                severity="error",
                timeout=4,
            )
            self._handle_play_failure(
                exhausted_message="Multiple tracks failed — stream resolver reset. Try playing again.",
                failure_kind="stream",
            )
            return
        if not self.stream_resolver:
            return
        logger.warning(
            "Stream for %s failed to open (%s) — resetting the resolver and retrying once",
            video_id,
            event.get("error"),
        )
        self.stream_resolver.clear_cache()
        # The retry lands inside play_track's same-track debounce window.
        self._last_play_video_id = ""
        self._last_play_time = 0.0
        await self.play_track(track, recovery_of=attempt)

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
                await self._fetch_and_play_radio(seed_track=seed, append=True)
                first = self.queue.next_track()
                if first:
                    await self.play_track(first)
                else:
                    self.notify("End of queue.", timeout=2)
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

    async def _fetch_and_play_radio(
        self,
        seed_track: dict | list[dict],
        *,
        label: str | None = None,
        append: bool = False,
    ) -> None:
        """Fetch radio for one or more seed tracks and load into queue.

        When *append* is False (default), clears the queue first and starts
        playback — used for user-initiated "Start Radio" / discovery mix.
        When *append* is True, silently adds tracks — used for background
        queue refill.
        """
        if not self.ytmusic:
            return
        seeds = [seed_track] if isinstance(seed_track, dict) else seed_track
        video_ids = [get_video_id(t) for t in seeds if get_video_id(t)]
        if not video_ids:
            return

        if not append:
            self.notify("Loading radio...", timeout=3)

        try:
            tracks = await self.ytmusic.get_radio(video_ids)
        except Exception:
            logger.exception("Failed to fetch radio")
            tracks = []

        if not tracks:
            if not append:
                self.notify("No radio suggestions available.", severity="warning", timeout=3)
            return

        if append:
            self.queue.set_radio_tracks(tracks)
            self.queue.radio_seeds = seeds
            self._refresh_queue_page()
            return

        self.queue.clear()
        normalized_seeds = normalize_tracks(seeds)
        if normalized_seeds:
            self.queue.add_multiple(normalized_seeds)
        self.queue.set_radio_tracks(tracks)
        self.queue.radio_seeds = seeds
        # Track-seeded radio and discovery mix are ephemeral — clear any
        # prior context so a later shuffle toggle is not persisted to
        # the wrong key (TP-7).  Playlist-seeded radio uses its own
        # set_context() in _start_playlist_radio.
        self.queue.set_context(None)
        self._refresh_queue_page()
        if not label:
            label = f"Radio from {seeds[0].get('title', 'Unknown')}"
        # Fire before play_track(), not after: "Playing: X" means the radio
        # was fetched and queued, not that the first track's audio is
        # already flowing. play_track() awaits the full stream resolve, so
        # placing this after it delayed the confirmation by however long
        # that took, instead of confirming as soon as it was actually true.
        self.notify(f"Playing: {label}", timeout=4)
        first = self.queue.next_track()
        if first:
            await self.play_track(first)

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
            ended_track = event.get("track") if isinstance(event, dict) else None
            # A new play can commit between this end event and the callback
            # running (Player clears current_track BEFORE dispatching, so it
            # can only be non-None again if play() ran since). The whole
            # event is then stale: finalizing could consume the new play's
            # claim (same-video replay) or log with its position — while a
            # natural-EOF finalize writes nothing anyway (mpv reads position
            # 0.0 once idle) — and advancing would skip straight over the
            # track that is already playing.
            if self.player and self.player.current_track is not None:
                logger.debug("Ignoring stale track-end; a newer play is already current")
                return
            # Log listen time using the ended track passed in the event,
            # since player.current_track is already None by the time this
            # callback runs.
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
        pos = 0.0
        try:
            pos = self.player.position
            dur = self.player.duration
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_position(pos, dur)
        except Exception:
            logger.debug("Failed to poll playback position", exc_info=True)

        # Audio actually advancing is the only proof a play attempt
        # succeeded (TRACK_CHANGE fires before mpv has opened the stream),
        # so failure state is reset here and nowhere earlier.
        if (
            pos > 0
            and (self._consecutive_failures or self._recovery_generation is not None)
            and self.player.is_playing
        ):
            self._consecutive_failures = 0
            self._recovery_generation = None

        if self.mpris and self.player.is_playing:
            try:
                self.mpris.update_position(int(self.player.position * 1_000_000))
            except Exception:
                logger.exception("MPRIS position update failed")

        if self.mac_media and self.player.is_playing:
            try:
                self.mac_media.update_position(int(self.player.position * 1_000_000))
            except Exception:
                logger.exception("macOS Now Playing position update failed")

        # Check Last.fm scrobble threshold.
        if self.lastfm and self.lastfm.is_connected and self.player.is_playing:
            try:
                self.run_worker(
                    self.lastfm.check_scrobble(self.player.position),
                    group="scrobble",
                    exclusive=True,
                )
            except Exception:
                logger.exception("Last.fm scrobble check failed")

    def _on_track_change(self, track: dict) -> None:
        """Handle track change event from the player.

        Called on the event loop via call_soon_threadsafe -- safe to touch widgets.
        """
        self._refill_queue()

        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_track(track)
            bar.update_playback_state(is_playing=True, is_paused=False)
        except Exception:
            logger.debug("Failed to update playback bar on track change", exc_info=True)

        # Reflect the new track's like state on the playback bar's heart.
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_like_status(track.get("likeStatus"))
        except Exception:
            logger.debug("Failed to update like status on track change", exc_info=True)

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

    def _refill_queue(self) -> None:
        """Refill the queue in the background when tracks are running low."""
        if self.queue.repeat_mode != "off":
            return
        if not self.settings.playback.autoplay:
            return
        if self.queue.remaining_tracks > 3:
            return
        for worker in self.workers:
            if worker.group == "queue_extend" and worker.is_running:
                return

        all_tracks = self.queue.tracks
        current_idx = self.queue.real_index
        played = list(all_tracks[: current_idx + 1]) if current_idx >= 0 else []
        seeds = played[-5:]
        if not seeds:
            track = self.player.current_track if self.player else None
            if track:
                seeds = [track]
        if not seeds:
            return

        self.run_worker(
            self._fetch_and_play_radio(seeds, append=True),
            group="queue_extend",
            exclusive=True,
        )

    async def _start_discovery_mix(self) -> None:
        """Fetch a random discovery mix, replace the queue, and start playing."""
        if not self.ytmusic:
            return
        self.notify("Loading discovery mix...", timeout=3)
        seeds, source = await self.ytmusic.get_discovery_mix()
        if not seeds:
            self.notify("Discovery failed — no content available", severity="warning")
            return
        label = f"Discovery ({source})" if source else None
        await self._fetch_and_play_radio(seeds, label=label)
        if self._current_page != "queue":
            await self.navigate_to("queue")

    def _on_volume_change(self, volume: int) -> None:
        """Handle volume change events."""
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_volume(volume)
        except Exception:
            logger.debug("Failed to update volume display", exc_info=True)
        if self.mpris:
            try:
                self.mpris.update_volume(volume / 100)
            except Exception:
                logger.exception("MPRIS volume update failed")

    def _on_seek(self, position: float) -> None:
        """Push seek jumps to MPRIS (Seeked signal) so clients resync."""
        if self.mpris:
            try:
                self.mpris.emit_seeked(int(position * 1_000_000))
            except Exception:
                logger.exception("MPRIS Seeked emit failed")

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
                logger.exception("MPRIS playback status update failed")

        if self.mac_media:
            status = "Paused" if paused else "Playing"
            mac_media = self.mac_media
            try:
                self.call_later(
                    lambda s=status, svc=mac_media: self.run_worker(svc.update_playback_status(s))
                )
            except Exception:
                logger.exception("macOS Now Playing playback status update failed")

        # Update Discord presence on pause/resume.
        discord = self.discord
        if discord:
            try:
                if paused:
                    self.call_later(lambda d=discord: self.run_worker(d.clear()))
                elif self.player and self.player.current_track:
                    t = self.player.current_track
                    player = self.player
                    self.call_later(
                        lambda d=discord, p=player, track=t: self.run_worker(
                            d.update(
                                title=track.get("title", ""),
                                artist=track.get("artist", ""),
                                album=track.get("album", ""),
                                position=p.position,
                                thumbnail_url=track.get("thumbnail_url") or "",
                            )
                        )
                    )
            except Exception:
                logger.exception("Discord RPC presence update failed")

    # ── History logging ──────────────────────────────────────────────

    async def _log_current_listen(self) -> None:
        """Log the listen duration for the currently playing track."""
        if not self.player or not self.player.current_track:
            return
        await self._log_listen_for(self.player.current_track)

    async def _log_listen_for(self, track: dict) -> None:
        """Log listen duration for an explicit track dict.

        Used by ``_on_track_end`` where ``player.current_track`` has
        already been cleared by the time the callback executes.
        """
        if not self.history or not self.player:
            return
        await self._log_local_listen(track)

    async def _log_local_listen(self, track: dict) -> None:
        """Insert or finalize the local SQLite history row for this play."""
        if not self.history or not self.player:
            return

        video_id = get_video_id(track)
        claim = self._local_history_claim
        # Only a claim for THIS video belongs to the play being finalized; on
        # a mismatch another play owns it — leave it alone and direct-log.
        owned = claim if claim is not None and claim.video_id == video_id else None
        if owned is not None:
            if owned.finalized:
                # Already finalized (duplicate end-file or a concurrent
                # supersede) — a second write would duplicate the row.
                return
            # Consume before any await: stops the report timer chain and
            # blocks a concurrent finalize. Same-video replays get a fresh
            # claim object, so a consumed claim can't swallow a later play.
            owned.finalized = True

        listened = int(self.player.position - self._track_start_position)
        if listened <= 0:
            # Nothing to write (mpv reads position 0.0 once idle, so natural
            # EOF lands here) — but the claim above is already consumed: a
            # replay of the same video must get its own row, not merge into
            # this one.
            return
        try:
            if owned is not None and owned.row_id is not None:
                await self.history.update_play_listened_seconds(owned.row_id, listened)
            elif owned is not None and owned.insert_started:
                # Insert worker in flight: hand off the final duration for it
                # to apply once the row exists. If the worker is already dead
                # (insert failed, or cancelled at quit) this is a no-op — the
                # row keeps its threshold-time value rather than risking a
                # duplicate from a blind direct log.
                owned.pending_seconds = listened
            else:
                play_id = await self.history.log_play(
                    track=track,
                    listened_seconds=listened,
                    source="tui",
                    min_listen_seconds=self._history_min_listen_seconds(),
                )
                if play_id is not None:
                    self._drop_from_ytm_history_cache(video_id)
        except Exception:
            logger.exception("Failed to log play history")

    def _history_min_listen_seconds(self) -> int:
        """Configured minimum seconds before a play counts instead of a skip."""
        value = self.settings.playback.history_min_listen_seconds
        return max(0, int(value))

    def _history_timer_delay(self) -> float:
        """Initial arm delay for the report timers.

        Mirrors the listen threshold, but never returns 0: a threshold of 0
        "count any playback" is valid gating, yet ``set_timer(0)`` triggers a
        ZeroDivisionError inside Textual's timer. Fall back to the poll
        interval so the first check still runs promptly.
        """
        return max(float(self._history_min_listen_seconds()), _YTM_HISTORY_POLL_SECONDS)

    def _schedule_local_history_log(self, track: dict, video_id: str, generation: int) -> None:
        """Insert the current play into SQLite once it crosses the threshold."""
        if not self.history or not video_id:
            return
        claim = _LocalHistoryClaim(video_id=video_id, track=dict(track), generation=generation)
        self._local_history_claim = claim
        self.set_timer(
            self._history_timer_delay(),
            lambda: self._report_local_play(claim),
        )

    def _report_local_play(self, claim: _LocalHistoryClaim, idle_polls: int = 0) -> None:
        # Identity is the liveness check: every committed play replaces
        # ``_local_history_claim`` and finalize marks the claim consumed;
        # either ends this chain. We deliberately do NOT gate on
        # player.current_track — on natural advance a duplicate mpv end-file
        # can transiently clear current_track while the track keeps playing,
        # which would otherwise drop the play from history.
        if claim is not self._local_history_claim or claim.finalized:
            return
        if not self.history or not self.player:
            return
        listened = int(self.player.position - self._track_start_position)
        if listened <= self._history_min_listen_seconds():
            # End the chain once the player is idle for two consecutive
            # polls: stream errors, stop and end-of-queue never bump the
            # generation or replace the claim, so nothing else would stop it.
            # Two polls (not one) so the transient current_track clear from a
            # duplicate end-file on natural advance can't kill a live chain.
            if self.player.current_track is None:
                if idle_polls + 1 >= 2:
                    return
                next_idle = idle_polls + 1
            else:
                next_idle = 0
            self.set_timer(
                _YTM_HISTORY_POLL_SECONDS,
                lambda: self._report_local_play(claim, next_idle),
            )
            return
        claim.insert_started = True
        self.run_worker(
            self._insert_local_history_play(claim, listened),
            group="local-history-report",
        )

    async def _insert_local_history_play(self, claim: _LocalHistoryClaim, listened: int) -> None:
        # The play already crossed the listen threshold when this worker was
        # scheduled — the row is earned no matter what happened since (skip,
        # same-video replay, newer claim). Insert unconditionally; this
        # worker only ever writes fields of its own claim object, so it
        # cannot clobber a newer play's state.
        if not self.history:
            return
        try:
            play_id = await self.history.log_play(
                claim.track,
                listened,
                source="tui",
                min_listen_seconds=self._history_min_listen_seconds(),
            )
        except Exception:
            logger.exception("Failed to log play history")
            play_id = None
        claim.row_id = play_id
        if play_id is None:
            return
        # The play is now local history; keep the YT Music tab disjoint.
        self._drop_from_ytm_history_cache(claim.video_id)
        # Finalize may have stashed the final duration while the insert was
        # in flight. No await sits between publishing row_id above and
        # reading pending_seconds here, so a finalize either already saw
        # row_id (and updated the row itself) or its stash is visible now.
        pending = claim.pending_seconds
        if pending is not None:
            claim.pending_seconds = None
            if pending > listened:
                try:
                    await self.history.update_play_listened_seconds(play_id, pending)
                except Exception:
                    logger.exception("Failed to finalize play history")
            return
        # Reflect it live only if this play is still current (generation, not
        # current_track, which a duplicate end-file can transiently clear).
        if claim.generation == self._play_generation:
            self._optimistic_local_history_add(claim.track)

    def _optimistic_local_history_add(self, track: dict) -> None:
        """Prepend the just-logged play to the Local tab if it is open.

        The local cache is per-page (cheap SQLite reads), so this only
        matters while the Recently Played page is mounted on the Local tab.
        """
        from ytm_player.ui.pages.recently_played import _TAB_LOCAL, RecentlyPlayedPage

        page = self._get_current_page()
        if isinstance(page, RecentlyPlayedPage):
            page.optimistic_add(_TAB_LOCAL, track)

    def _drop_from_ytm_history_cache(self, video_id: str) -> None:
        """Evict a just-logged local play from the cached YT Music tab.

        The YT Music tab shows online-only plays: once a track enters the
        local history, a fresh ``get_history()`` fetch would filter it out,
        so the populated app-level cache must drop it too — otherwise the
        tabs stop being disjoint until the next refetch. Re-renders the tab
        live if it is showing.
        """
        cache = self._ytm_history
        if cache is None or not video_id:
            return
        pruned = [t for t in cache if get_video_id(t) != video_id]
        if len(pruned) == len(cache):
            return
        self._ytm_history = pruned

        from ytm_player.ui.pages.recently_played import _TAB_YTM, RecentlyPlayedPage

        page = self._get_current_page()
        if isinstance(page, RecentlyPlayedPage):
            page._refresh_tab_from_cache(_TAB_YTM)

    def _schedule_ytm_history_report(self, track: dict, video_id: str, generation: int) -> None:
        """Arm focus-independent history reporting for the current play.

        Position events can stop reaching the app when the terminal loses
        focus even though mpv keeps playing. A timer avoids that: poll
        ``player.position`` until the same play crosses the threshold. Skips
        are ignored because play generation changes.
        """
        if not self.settings.playback.sync_history_to_ytmusic:
            return
        if not self.ytmusic or not video_id:
            return
        self.set_timer(
            self._history_timer_delay(),
            lambda: self._report_ytm_play(track, video_id, generation),
        )

    def _report_ytm_play(
        self, track: dict, video_id: str, generation: int, idle_polls: int = 0
    ) -> None:
        """Timer callback: report the play if it is still current.

        Best-effort and non-blocking. If playback started late or was paused,
        ``position`` may still be below threshold when the timer fires; in that
        case keep polling. This means pause does not count, but reporting still
        works without window focus.
        """
        if generation != self._play_generation:
            return
        if self._ytm_reported_generation == generation:
            return
        if not self.settings.playback.sync_history_to_ytmusic or not self.ytmusic:
            return
        if not self.player:
            return
        # Measure listen time relative to where this track started, not the
        # raw player position. On resume-on-launch the track starts mid-file
        # (``_track_start_position`` > 0), so a bare ``position`` check would
        # report the play immediately even if the user only heard a second.
        listened = int(self.player.position - self._track_start_position)
        if listened <= self._history_min_listen_seconds():
            # Same stable-idle termination as _report_local_play: two
            # consecutive idle polls end the chain; one may be the transient
            # duplicate-end-file clear.
            if self.player.current_track is None:
                if idle_polls + 1 >= 2:
                    return
                next_idle = idle_polls + 1
            else:
                next_idle = 0
            self.set_timer(
                _YTM_HISTORY_POLL_SECONDS,
                lambda: self._report_ytm_play(track, video_id, generation, next_idle),
            )
            return
        self.run_worker(
            self._push_ytm_history_report(video_id, generation),
            group="ytm-history-report",
        )

    async def _push_ytm_history_report(self, video_id: str, generation: int) -> None:
        """Report the play to the account history (best-effort).

        ``add_history_item`` returns False on any failure (expired auth,
        missing playbackTracking, non-204) without raising — marking the
        play reported on failure would suppress the retry on the next poll.
        The YT Music tab deliberately does NOT reflect this play: it shows
        online-only plays and filters TUI plays out of the account feed.
        """
        if not self.ytmusic:
            return
        if not await self.ytmusic.add_history_item(video_id):
            return
        self._ytm_reported_generation = generation

    # ── Like toggle ──────────────────────────────────────────────────

    async def _toggle_like_current(self) -> None:
        """Toggle the like state on the currently-playing track.

        Cycles between LIKE and INDIFFERENT (no rating). Pressing this
        on a disliked track switches it to LIKE (clearing the dislike).
        Dislike state is left to the existing track-actions popup.
        """
        if not self.player or not self.player.current_track:
            return
        track = self.player.current_track
        video_id = track.get("video_id", "")
        if not video_id:
            return
        if not self.ytmusic:
            self.notify("Sign in to like songs", severity="warning", timeout=2)
            return

        current_status = (track.get("likeStatus") or "INDIFFERENT").upper()
        new_status = "INDIFFERENT" if current_status == "LIKE" else "LIKE"

        result = await self.ytmusic.rate_song(video_id, new_status)
        if result != "success":
            from ytm_player.services.ytmusic import mutation_failure_suffix

            self.notify(
                f"Couldn't update like — {mutation_failure_suffix(result)}",
                severity="error",
                timeout=3,
            )
            return

        # Update the track dict so subsequent reads reflect the new state.
        track["likeStatus"] = new_status
        # Notify the user of the change.
        msg = "Added to Liked songs" if new_status == "LIKE" else "Removed from Liked songs"
        self.notify(msg, timeout=2)
        # Push the new state to the playback bar.
        try:
            from ytm_player.ui.playback_bar import PlaybackBar

            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_like_status(new_status)
        except Exception:
            logger.debug("Failed to push like status to playback bar", exc_info=True)

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
