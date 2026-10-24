"""Session state persistence mixin for YTMPlayerApp."""

from __future__ import annotations

import json
import logging
import os
from pathlib import Path

from ytm_player.app._base import YTMHostBase
from ytm_player.services.queue import RepeatMode
from ytm_player.ui.playback_bar import PlaybackBar
from ytm_player.ui.sidebars.lyrics_sidebar import LyricsSidebar

logger = logging.getLogger(__name__)

_SESSION_SCHEMA_VERSION = 1
# Seconds between session saves while the app runs.
SESSION_AUTOSAVE_SECONDS = 5
_MISSING = object()


def _resume_file() -> Path:
    """resume.json, beside session.json wherever that file lives."""
    from ytm_player.config.paths import SESSION_STATE_FILE

    return SESSION_STATE_FILE.with_name("resume.json")


def _write_atomic(path: Path, text: str) -> None:
    """Write *text* to a temp file, then rename it over *path*."""
    from ytm_player.config.paths import SECURE_FILE_MODE, secure_chmod

    path.parent.mkdir(parents=True, exist_ok=True)
    tmp_path = path.with_suffix(path.suffix + ".tmp")
    try:
        tmp_path.write_text(text, encoding="utf-8")
        secure_chmod(tmp_path, SECURE_FILE_MODE)
        os.replace(tmp_path, path)
    finally:
        if tmp_path.exists():
            try:
                tmp_path.unlink()
            except OSError:
                pass


def _load_resume_state() -> object:
    """The resume point saved in resume.json, which may be None.

    Returns ``_MISSING`` when the file is absent or unusable. The caller then
    falls back to the ``resume`` key of an older session.json.
    """
    try:
        state = json.loads(_resume_file().read_text(encoding="utf-8"))
    except FileNotFoundError:
        return _MISSING
    except Exception:
        logger.debug("Could not read resume state", exc_info=True)
        return _MISSING
    if not isinstance(state, dict) or state.get("schema_version") != _SESSION_SCHEMA_VERSION:
        return _MISSING
    resume = state.get("resume")
    return resume if resume is None or isinstance(resume, dict) else _MISSING


class SessionMixin(YTMHostBase):
    """Persist and restore session state (volume, shuffle, repeat, queue, etc.)."""

    def _load_session_state(self) -> dict:
        """Read session.json.

        Returns ``{}`` when the file is missing, unreadable, not valid JSON,
        not a JSON object, or written by another schema version.
        """
        from ytm_player.config.paths import SESSION_STATE_FILE

        state: object = {}
        try:
            if SESSION_STATE_FILE.exists():
                state = json.loads(SESSION_STATE_FILE.read_text(encoding="utf-8"))
        except Exception:
            logger.debug("Could not read session state", exc_info=True)

        if not isinstance(state, dict):
            logger.warning("Discarding session state — not a JSON object")
            return {}

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
            return {}
        return state

    async def _restore_session_state(self) -> None:
        """Restore volume, shuffle, repeat, queue and resume point from the last session.

        ``_session_restored`` is set only when this runs to completion;
        ``_save_session_state`` writes nothing before that, so a start that
        fails part-way can't overwrite the previous session.
        """
        state = self._load_session_state()
        # resume.json supersedes the resume key that older versions kept in session.json.
        saved_resume = _load_resume_state()
        if saved_resume is not _MISSING:
            state["resume"] = saved_resume

        # The resume point read from disk stays valid until a track's load
        # is accepted this session (play_track clears it). Until then
        # _save_session_state writes it back unchanged — whether or not
        # resume-on-launch stages it for a seek below.
        loaded_resume = state.get("resume")
        if isinstance(loaded_resume, dict) and loaded_resume.get("video_id"):
            self._loaded_resume = dict(loaded_resume)
        else:
            self._loaded_resume = None

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
        # The lyrics panel and album art reopen as the last run left them,
        # unless [ui] remember_layout is off.
        self._lyrics_sidebar_open = False
        if self.settings.ui.remember_layout:
            if state.get("lyrics_open") is True:
                self._lyrics_sidebar_open = True
                self._apply_lyrics_sidebar(True)
            if isinstance(state.get("album_art"), bool):
                try:
                    self.query_one("#pb-art").display = state["album_art"]
                except Exception:
                    logger.debug("Failed to restore album art visibility", exc_info=True)

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
            self._session_restored = True
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

        self._session_restored = True

    def _album_art_shown(self) -> bool:
        try:
            return bool(self.query_one("#pb-art").display)
        except Exception:
            return True

    def _start_session_autosave(self) -> None:
        """Save the session every few seconds, so a closed terminal or a kill keeps it.

        Before, the session was written only on a clean exit.
        """
        self.set_interval(SESSION_AUTOSAVE_SECONDS, self._save_session_state)

    def _save_session_state(self, force: bool = False) -> None:
        """Persist volume, shuffle, repeat, queue and resume point to disk.

        The resume point goes to its own small file, rewritten each autosave.
        session.json holds the queue and is rewritten only when it changes.
        *force* compares the full encoding instead of the cheap signature.
        """
        from ytm_player.config.paths import SESSION_STATE_FILE

        if not self._session_restored:
            # The restore never finished, so the file on disk is the user's real state.
            logger.warning("Session was never restored this run — not overwriting session.json")
            return

        volume = 80
        if self.player:
            try:
                volume = self.player.volume
            except Exception:
                logger.debug("Failed to read player volume for session save", exc_info=True)

        queue_tracks = list(self.queue.tracks)
        queue_index = self.queue.current_index

        # Resume point. The live track wins, but only past 1.0 s so a
        # premature exit can't record "position 0". Otherwise the value
        # read from disk at restore is written back: play_track clears it
        # the moment a track's load is accepted, so a session that played
        # nothing keeps its resume point and one that played and then
        # stopped saves none. Whether to RESTORE on next launch is gated by
        # settings.playback.resume_on_launch in _restore_session_state.
        resume = None
        if self.player and self.player.current_track and self.player.position > 1.0:
            video_id = self.player.current_track.get("video_id", "")
            if video_id:
                resume = {
                    "video_id": video_id,
                    "position": self.player.position,
                    "playlist_id": self._active_library_playlist_id,
                }
        if resume is None and self._loaded_resume is not None:
            resume = self._loaded_resume

        settings_part = {
            "schema_version": _SESSION_SCHEMA_VERSION,
            "volume": volume,
            "repeat": self.queue.repeat_mode.value,
            "shuffle": self.queue.shuffle_enabled,
            "queue_index": queue_index,
            "queue_context_id": self.queue.current_context_id,
            "sidebar_per_page": self._sidebar_per_page,
            "transliteration_enabled": self._get_transliteration_state(),
            "first_run_hint_shown": self._first_run_hint_shown,
            "mpris_hint_shown": self._mpris_hint_shown,
            "lyrics_open": self._lyrics_sidebar_open,
            "album_art": self._album_art_shown(),
        }
        try:
            # The queue is a few hundred kB. Encode it only when the small
            # fields or the track order differ from the last write.
            signature = (
                json.dumps(settings_part),
                tuple(t.get("video_id") for t in queue_tracks),
            )
            if force or signature != getattr(self, "_last_session_signature", None):
                encoded = json.dumps({**settings_part, "queue_tracks": queue_tracks})
                if encoded != getattr(self, "_last_saved_session", None):
                    _write_atomic(SESSION_STATE_FILE, encoded)
                    self._last_saved_session = encoded
                self._last_session_signature = signature

            encoded_resume = json.dumps(
                {"schema_version": _SESSION_SCHEMA_VERSION, "resume": resume}
            )
            if encoded_resume != getattr(self, "_last_saved_resume", None):
                _write_atomic(_resume_file(), encoded_resume)
                self._last_saved_resume = encoded_resume
        except (OSError, TypeError):
            logger.exception("Could not save session state")
            if getattr(self, "_session_save_warned", False):
                return  # autosave retries every few seconds: warn once per run
            self._session_save_warned = True
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
