"""Session state persistence mixin for YTMPlayerApp."""

from __future__ import annotations

import json
import logging

from ytm_player.app._base import YTMHostBase
from ytm_player.services.queue import RepeatMode
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar
from ytm_player.utils.formatting import get_video_id

logger = logging.getLogger(__name__)

_SESSION_SCHEMA_VERSION = 1


class SessionMixin(YTMHostBase):
    """Persist and restore session state (volume, shuffle, repeat, queue, etc.)."""

    async def _restore_session_state(self) -> None:
        """Restore volume, shuffle, and repeat from the last session."""
        from ytm_player.config.paths import SESSION_STATE_FILE

        state: dict = {}
        try:
            if SESSION_STATE_FILE.exists():
                state = json.loads(SESSION_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("Could not read session state", exc_info=True)

        # Schema version check: discard state from incompatible older/future formats.
        file_version = state.get("schema_version")
        if file_version != _SESSION_SCHEMA_VERSION:
            if file_version is not None:
                logger.warning(
                    "Discarding session state — schema_version %r != %d (expected). "
                    "Settings will reset to defaults.",
                    file_version,
                    _SESSION_SCHEMA_VERSION,
                )
            state = {}

        volume = state.get("volume", self.settings.playback.default_volume)
        if not isinstance(volume, (int, float)) or isinstance(volume, bool):
            volume = self.settings.playback.default_volume
        else:
            volume = int(max(0, min(100, volume)))
        if self.player:
            await self.player.set_volume(volume)

        repeat = state.get("repeat", "off")
        try:
            mode = RepeatMode(repeat)
        except ValueError:
            mode = RepeatMode.OFF
        self.queue.set_repeat(mode)

        # Restore queue from last session (before enabling shuffle so the
        # shuffle order is built from a populated queue).
        from ytm_player.utils.formatting import normalize_tracks

        saved_tracks = state.get("queue_tracks", [])
        if saved_tracks and isinstance(saved_tracks, list):
            normalized = normalize_tracks(saved_tracks)
            self.queue.add_multiple(normalized)
            saved_index = state.get("queue_index", 0)
            if isinstance(saved_index, int) and 0 <= saved_index < len(normalized):
                self.queue.jump_to(saved_index)

        if state.get("shuffle", False):
            self.queue.toggle_shuffle()

        # Restore the queue's collection identity so a post-restart
        # shuffle toggle persists to the right key (TP-7).
        saved_context = state.get("queue_context_id")
        if isinstance(saved_context, str) and saved_context:
            self.queue.set_context(saved_context)

        # Update the playback bar to reflect restored state.
        try:
            bar = self.query_one("#playback-bar", PlaybackBar)
            bar.update_volume(volume)
            bar.update_repeat(mode)
            bar.update_shuffle(self.queue.shuffle_enabled)
        except Exception:
            logger.debug(
                "Failed to update playback bar after restoring session state", exc_info=True
            )

        # Restore sidebar state.
        saved_sidebar = state.get("sidebar_per_page")
        if saved_sidebar and isinstance(saved_sidebar, dict):
            self._sidebar_per_page = saved_sidebar
        # Always start with lyrics sidebar closed regardless of previous session.
        self._lyrics_sidebar_open = False

        # Restore first-run hint flag — defaults False so legacy session.json
        # files (or fresh installs) trigger the toast on first launch.
        self._first_run_hint_shown = bool(state.get("first_run_hint_shown", False))
        self._mpris_hint_shown = bool(state.get("mpris_hint_shown", False))

        # Restore transliteration toggle state (session overrides config).
        if "transliteration_enabled" in state:
            try:
                self.query_one("#lyrics-sidebar", LyricsSidebar)._transliteration_enabled = state[
                    "transliteration_enabled"
                ]
            except Exception:
                pass

        # Restore last-playing track + position if user has resume enabled.
        # The track is shown paused; the first play_track call for this
        # video_id seeks to the saved position via _pending_resume_position
        # (handled in app/_playback.py).
        resumed_video_id: str | None = None
        if self.settings.playback.resume_on_launch:
            resume = state.get("resume")
            if resume and isinstance(resume, dict):
                video_id = resume.get("video_id", "")
                if video_id:
                    self._active_library_playlist_id = resume.get("playlist_id")
                    # Find the track in the restored queue and jump to it.
                    resumed = False
                    for i, t in enumerate(self.queue.tracks):
                        if t.get("video_id") == video_id:
                            self.queue.jump_to(i)
                            resumed = True
                            break

                    if resumed:
                        track = self.queue.current_track
                        if track:
                            # Stash the resume target so the first play_track
                            # call for this video_id seeks to the saved position.
                            self._pending_resume_video_id = video_id
                            resumed_video_id = video_id
                            try:
                                self._pending_resume_position = float(resume.get("position", 0))
                            except (TypeError, ValueError):
                                self._pending_resume_position = 0.0
                            # Show the track + saved position in the UI without
                            # starting playback.
                            try:
                                bar = self.query_one("#playback-bar", PlaybackBar)
                                bar.update_track(track)
                                bar.update_playback_state(is_playing=False, is_paused=False)
                                bar.update_position(
                                    self._pending_resume_position,
                                    track.get("duration") or 0,
                                )
                            except Exception:
                                logger.debug(
                                    "Playback bar not ready during resume restore",
                                    exc_info=True,
                                )

        def _start_resolver_warmup() -> None:
            # Warm the resolver in the background, now, while the user is
            # still looking at the library, instead of mid-interaction on
            # first play. Independent of resume_on_launch: that setting
            # controls whether we *auto-play* on launch, not whether it's
            # worth warming the resolver.
            if self.stream_resolver:
                self.run_worker(self._warm_stream_resolver(resumed_video_id))

        # Whether a JS challenge solver is available is a property of
        # yt-dlp's local config/cache, not of which client or cookies a
        # given resolve ends up using — so we can answer "will
        # remote_components be needed" instantly, before paying for any
        # cookie decryption at all, instead of waiting to find out via a
        # real (slow) resolve attempt.
        #
        # The warmup starts immediately, in parallel with this check/
        # prompt, rather than waiting for the prompt to be answered.
        # It used to wait — deliberately, as a defensive measure on top of
        # two real races this same concurrency produced (a UI freeze from
        # closing a live YoutubeDL instance mid-request, then a silently
        # lost remote_components update). Both got proper, independently
        # tested fixes at the StreamResolver level (a non-blocking reset,
        # and a generation counter that invalidates a build a reset landed
        # during) — so the wait stopped buying correctness and only cost
        # time, which is exactly what turned a several-second cookie
        # decrypt into a ~28s wait when a user accepted the prompt and
        # started playback within seconds of each other. Racing them is
        # safe now; waiting was papering over bugs that no longer exist.
        from ytm_player.services.stream import (
            REMOTE_COMPONENTS_PROMPT_BODY,
            looks_like_js_solver_ready,
        )

        if not looks_like_js_solver_ready():
            self._show_remote_components_prompt(
                message=f"Playback {REMOTE_COMPONENTS_PROMPT_BODY}",
                on_accept=self._notify_remote_components_enabled,
            )

        _start_resolver_warmup()

    def _notify_remote_components_enabled(self) -> None:
        """Toast shown once the user accepts the remote_components prompt.

        Shared by both call sites in this class — the startup warmup above
        and the post-prefetch recheck in
        ``_warm_resolver_and_check_remote_components`` — so the wording
        can't drift between them the way two independently defined
        closures risked.
        """
        self.notify("Enabled yt-dlp's JS challenge solver.", timeout=3)

    async def _warm_stream_resolver(self, preferred_video_id: str | None) -> None:
        """Pick a video_id to warm StreamResolver with, then resolve it silently.

        Priority: the actual resume target (most representative — it's
        what's about to be played) > the restored queue's current track
        (a session can have a queue without resume_on_launch enabled) >
        the most recently played track from local history (works on a
        returning user with no active queue, no extra network call needed
        just to pick one — get_recently_played reads the local history.db)
        > the account's YT Music home feed (last resort for a brand-new
        install with zero local history — costs a real network call, but
        "silently do nothing on first launch" is worse UX than that cost;
        reuses get_home() and get_video_id(), the exact call and parsing
        the Home page itself already uses, so no new untested API surface).
        If even that comes back empty (offline, fresh account with no
        recommendations yet), there's nothing safe to warm with; the first
        real play just pays the cost normally, same as before this feature
        existed.
        """
        video_id = preferred_video_id
        if not video_id and self.queue.current_track:
            video_id = self.queue.current_track.get("video_id")
        if not video_id and self.history:
            try:
                recent = await self.history.get_recently_played(limit=1)
            except Exception:
                logger.debug("Could not read history for resolver warmup", exc_info=True)
                recent = []
            if recent:
                video_id = recent[0].get("video_id")
        if not video_id and self.ytmusic:
            try:
                shelves = await self.ytmusic.get_home(limit=1)
            except Exception:
                logger.debug("Could not fetch home feed for resolver warmup", exc_info=True)
                shelves = None
            for shelf in shelves or []:
                for item in shelf.get("contents", []):
                    candidate = get_video_id(item)
                    if candidate:
                        video_id = candidate
                        break
                if video_id:
                    break
        if not video_id:
            return
        await self._warm_resolver_and_check_remote_components(video_id)

    async def _warm_resolver_and_check_remote_components(self, video_id: str) -> None:
        """Resolve the resume track early so the missing-remote-components
        signal (if any) surfaces during startup instead of on first play.

        Best-effort: prefetch() already swallows resolution errors, and if
        stream_resolver went away (e.g. app shutting down mid-startup) the
        guard at the call site skips this entirely.
        """
        if not self.stream_resolver:
            return

        from ytm_player.services.stream import REMOTE_COMPONENTS_PROMPT_BODY

        await self.stream_resolver.prefetch(video_id)
        if self.stream_resolver.consume_missing_remote_components(video_id):
            self._show_remote_components_prompt(
                message=f"Playback {REMOTE_COMPONENTS_PROMPT_BODY}",
                on_accept=self._notify_remote_components_enabled,
            )

    def _save_session_state(self) -> None:
        """Persist volume, shuffle, and repeat to disk."""
        from ytm_player.config.paths import SESSION_STATE_FILE

        volume = 80
        if self.player:
            try:
                volume = self.player.volume
            except Exception:
                logger.debug("Failed to read player volume for session save", exc_info=True)

        queue_tracks = list(self.queue.tracks)
        queue_index = self.queue.current_index

        # Always save current track + position on exit. Whether to RESTORE
        # on next launch is gated by settings.playback.resume_on_launch in
        # _restore_session_state.
        # Guard: only save resume if position > 1.0s, so a startup-crash
        # (or any premature exit) doesn't overwrite a valid prior resume
        # with "position 0".
        resume = None
        if self.player and self.player.current_track and self.player.position > 1.0:
            video_id = self.player.current_track.get("video_id", "")
            if video_id:
                resume = {
                    "video_id": video_id,
                    "position": self.player.position,
                    "playlist_id": self._active_library_playlist_id,
                }

        state = {
            "schema_version": _SESSION_SCHEMA_VERSION,
            "volume": volume,
            "repeat": self.queue.repeat_mode.value,
            "shuffle": self.queue.shuffle_enabled,
            "queue_tracks": queue_tracks,
            "queue_index": queue_index,
            "queue_context_id": self.queue.current_context_id,
            "resume": resume,
            "sidebar_per_page": self._sidebar_per_page,
            "transliteration_enabled": self._get_transliteration_state(),
            "first_run_hint_shown": self._first_run_hint_shown,
            "mpris_hint_shown": self._mpris_hint_shown,
        }
        try:
            import os

            from ytm_player.config.paths import SECURE_FILE_MODE, secure_chmod

            SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            tmp_path = SESSION_STATE_FILE.with_suffix(SESSION_STATE_FILE.suffix + ".tmp")
            try:
                tmp_path.write_text(json.dumps(state), encoding="utf-8")
                secure_chmod(tmp_path, SECURE_FILE_MODE)
                os.replace(tmp_path, SESSION_STATE_FILE)
            finally:
                if tmp_path.exists():
                    try:
                        tmp_path.unlink()
                    except OSError:
                        pass
        except (OSError, TypeError):
            logger.exception("Could not save session state")
            try:
                self.notify(
                    "Could not save session state — your queue and "
                    "position may not restore on next launch.",
                    severity="warning",
                    timeout=8,
                )
            except Exception:
                # If notify itself fails (e.g. app shutting down), log and move on.
                logger.exception("Failed to surface save-failure notify")

    def _get_transliteration_state(self) -> bool:
        """Read transliteration toggle from the lyrics sidebar."""
        try:
            return self.query_one("#lyrics-sidebar", LyricsSidebar)._transliteration_enabled
        except Exception:
            return False
