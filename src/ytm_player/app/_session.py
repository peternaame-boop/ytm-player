"""Session state persistence mixin for YTMPlayerApp."""

from __future__ import annotations

import json
import logging

from ytm_player.app._base import YTMHostBase
from ytm_player.services.queue import RepeatMode
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar

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
        if not self.settings.playback.resume_on_launch:
            return

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
