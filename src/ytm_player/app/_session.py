"""Session state persistence mixin for YTMPlayerApp."""

from __future__ import annotations

import json
import logging

from ytm_player.services.queue import RepeatMode
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar

logger = logging.getLogger(__name__)


class SessionMixin:
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

        volume = state.get("volume", self.settings.playback.default_volume)
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

        # Restore transliteration toggle state (session overrides config).
        if "transliteration_enabled" in state:
            try:
                self.query_one("#lyrics-sidebar", LyricsSidebar)._transliteration_enabled = state[
                    "transliteration_enabled"
                ]
            except Exception:
                pass

        # Auto-resume playback if the previous session exited uncleanly.
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
                        # Show the track in the UI without starting playback.
                        try:
                            bar = self.query_one("#playback-bar", PlaybackBar)
                            bar.update_track(track)
                            bar.update_playback_state(is_playing=False, is_paused=False)
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

        # Serialize queue tracks (limit to 500 to keep file size reasonable).
        queue_tracks = list(self.queue.tracks)[:500]
        queue_index = self.queue.current_index

        # Build resume data: save current track + position on unclean exit,
        # explicitly clear on clean exit (q / C-q).
        resume = None
        if not self._clean_exit and self.player and self.player.current_track:
            video_id = self.player.current_track.get("video_id", "")
            if video_id:
                resume = {
                    "video_id": video_id,
                    "position": self.player.position,
                    "playlist_id": self._active_library_playlist_id,
                }

        state = {
            "volume": volume,
            "repeat": self.queue.repeat_mode.value,
            "shuffle": self.queue.shuffle_enabled,
            "queue_tracks": queue_tracks,
            "queue_index": queue_index,
            "resume": resume,
            "sidebar_per_page": self._sidebar_per_page,
            "lyrics_sidebar_open": self._lyrics_sidebar_open,
            "transliteration_enabled": self._get_transliteration_state(),
        }
        try:
            from ytm_player.config.paths import SECURE_FILE_MODE, secure_chmod

            SESSION_STATE_FILE.parent.mkdir(parents=True, exist_ok=True)
            SESSION_STATE_FILE.write_text(json.dumps(state), encoding="utf-8")
            secure_chmod(SESSION_STATE_FILE, SECURE_FILE_MODE)
        except Exception:
            logger.warning("Could not save session state", exc_info=True)

    def _get_transliteration_state(self) -> bool:
        """Read transliteration toggle from the lyrics sidebar."""
        try:
            return self.query_one("#lyrics-sidebar", LyricsSidebar)._transliteration_enabled
        except Exception:
            return False
