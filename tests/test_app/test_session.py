"""Tests for SessionMixin._restore_session_state crash-resistance.

Session state is loaded on startup. If session.json is corrupt or
missing we must fall back cleanly — a crash here means users can't
launch the app at all after one bad shutdown.
"""

from __future__ import annotations

import asyncio
import json
from unittest.mock import AsyncMock, MagicMock

import pytest

from ytm_player.app._mpris import MPRISMixin
from ytm_player.app._playback import PlaybackMixin
from ytm_player.app._session import SessionMixin
from ytm_player.services.queue import QueueManager, RepeatMode
from ytm_player.services.stream import StreamInfo


def _fresh_session_host():
    h = SessionMixin()
    # Every existing save test models a host whose restore already ran.
    h._session_restored = True
    h._loaded_resume = None
    h.player = MagicMock()
    h.player.set_volume = AsyncMock()
    h.queue = MagicMock()
    h.queue.set_repeat = MagicMock()
    h.queue.add_multiple = MagicMock()
    h.queue.jump_to = MagicMock()
    h.queue.toggle_shuffle = MagicMock()
    h.queue.set_context = MagicMock()
    h.queue.tracks = []
    h.queue.current_track = None
    h.queue.current_context_id = None
    h.settings = MagicMock()
    h.settings.playback.default_volume = 80
    h.query_one = MagicMock()
    h._sidebar_per_page = {}
    h._sidebar_default = True
    h._lyrics_sidebar_open = False
    h._active_library_playlist_id = None
    h._pending_resume_video_id = None
    h._pending_resume_position = 0.0
    h._first_run_hint_shown = False
    h._mpris_hint_shown = False
    return h


class TestRestoreSessionResilience:
    async def test_missing_file_uses_defaults(self, tmp_path, monkeypatch):
        h = _fresh_session_host()
        missing = tmp_path / "missing.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", missing, raising=False)
        # Should not raise.
        await h._restore_session_state()
        h.player.set_volume.assert_awaited_once_with(80)

    async def test_corrupt_json_does_not_raise(self, tmp_path, monkeypatch):
        h = _fresh_session_host()
        bad = tmp_path / "session.json"
        bad.write_text("{ this is not valid JSON", encoding="utf-8")
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", bad, raising=False)
        # Should not raise — bad JSON falls back to defaults.
        await h._restore_session_state()
        h.player.set_volume.assert_awaited_once_with(80)
        h.queue.set_repeat.assert_called_once_with(RepeatMode.OFF)

    async def test_invalid_repeat_value_falls_back_to_off(self, tmp_path, monkeypatch):
        h = _fresh_session_host()
        bad = tmp_path / "session.json"
        bad.write_text('{"repeat": "garbage"}', encoding="utf-8")
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", bad, raising=False)
        await h._restore_session_state()
        h.queue.set_repeat.assert_called_once_with(RepeatMode.OFF)

    async def test_valid_state_applied(self, tmp_path, monkeypatch):
        h = _fresh_session_host()
        good = tmp_path / "session.json"
        good.write_text(
            '{"schema_version": 1, "volume": 42, "repeat": "all", '
            '"shuffle": false, "queue_tracks": [], "queue_index": 0}',
            encoding="utf-8",
        )
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", good, raising=False)
        await h._restore_session_state()
        h.player.set_volume.assert_awaited_once_with(42)
        h.queue.set_repeat.assert_called_once_with(RepeatMode.ALL)

    async def test_garbage_volume_falls_back_to_default(self, tmp_path, monkeypatch):
        h = _fresh_session_host()
        bad = tmp_path / "session.json"
        bad.write_text('{"schema_version": 1, "volume": "loud"}', encoding="utf-8")
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", bad, raising=False)
        await h._restore_session_state()
        h.player.set_volume.assert_awaited_once_with(80)

    async def test_out_of_range_volume_is_clamped(self, tmp_path, monkeypatch):
        h = _fresh_session_host()
        bad = tmp_path / "session.json"
        bad.write_text('{"schema_version": 1, "volume": 500}', encoding="utf-8")
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", bad, raising=False)
        await h._restore_session_state()
        h.player.set_volume.assert_awaited_once_with(100)


class TestSchemaVersion:
    async def test_mismatched_schema_version_discards_state(self, tmp_path, monkeypatch):
        """A session.json with a different schema_version is discarded."""
        h = _fresh_session_host()
        bad = tmp_path / "session.json"
        bad.write_text(
            '{"schema_version": 99, "volume": 42, "repeat": "all"}',
            encoding="utf-8",
        )
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", bad, raising=False)
        await h._restore_session_state()
        # Defaults applied — volume 80, repeat OFF — not the file's 42 / ALL.
        h.player.set_volume.assert_awaited_once_with(80)
        h.queue.set_repeat.assert_called_once_with(RepeatMode.OFF)


class TestResumeOnLaunch:
    async def test_resume_on_launch_disabled_skips_restore(self, tmp_path, monkeypatch):
        """When resume_on_launch is False, the resume block is skipped."""
        h = _fresh_session_host()
        h.settings.playback.resume_on_launch = False
        good = tmp_path / "session.json"
        good.write_text(
            '{"schema_version": 1, "volume": 80, "repeat": "off", '
            '"shuffle": false, "queue_tracks": [{"video_id": "abc", "title": "X"}], '
            '"queue_index": 0, "resume": {"video_id": "abc", "position": 42.5}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", good, raising=False)
        h._pending_resume_video_id = None
        h._pending_resume_position = 0.0
        await h._restore_session_state()
        assert h._pending_resume_video_id is None
        assert h._pending_resume_position == 0.0

    async def test_resume_on_launch_enabled_sets_pending(self, tmp_path, monkeypatch):
        """When resume_on_launch is True (default), pending state is populated."""
        h = _fresh_session_host()
        h.settings.playback.resume_on_launch = True
        # Make queue.tracks behave like a real list with a matching video_id.
        sample_track = {"video_id": "abc", "title": "X", "duration": 200}
        h.queue.tracks = [sample_track]
        h.queue.current_track = sample_track
        h._pending_resume_video_id = None
        h._pending_resume_position = 0.0

        good = tmp_path / "session.json"
        good.write_text(
            '{"schema_version": 1, "volume": 80, "repeat": "off", '
            '"shuffle": false, "queue_tracks": [{"video_id": "abc", "title": "X"}], '
            '"queue_index": 0, "resume": {"video_id": "abc", "position": 42.5}}',
            encoding="utf-8",
        )
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", good, raising=False)
        await h._restore_session_state()
        assert h._pending_resume_video_id == "abc"
        assert h._pending_resume_position == 42.5


def _save_session_host(tmp_path):
    """Build a session host wired up enough to call _save_session_state."""
    h = _fresh_session_host()
    h.player.current_track = {"video_id": "abc", "title": "X"}
    h.player.position = 0.0
    h.player.volume = 80
    h.queue.tracks = []
    h.queue.current_index = 0
    h.queue.repeat_mode = RepeatMode.OFF
    h.queue.shuffle_enabled = False
    h.theme = "ytm-dark"
    # _get_transliteration_state reads from the lyrics sidebar via query_one;
    # _fresh_session_host already returns MagicMocks, which truthy-evaluate.
    # Override to a stable False so output is deterministic.
    h._get_transliteration_state = lambda: False
    return h


class TestSaveSessionResumeGuard:
    """_save_session_state must not overwrite a valid resume with position 0."""

    def test_resume_skipped_when_position_is_zero(self, tmp_path, monkeypatch):
        h = _save_session_host(tmp_path)
        h.player.position = 0.0
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        h._save_session_state()

        import json

        written = json.loads(target.read_text(encoding="utf-8"))
        assert written["resume"] is None

    def test_resume_skipped_when_position_below_threshold(self, tmp_path, monkeypatch):
        h = _save_session_host(tmp_path)
        h.player.position = 0.7
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        h._save_session_state()

        import json

        written = json.loads(target.read_text(encoding="utf-8"))
        assert written["resume"] is None

    def test_resume_skipped_at_exact_boundary(self, tmp_path, monkeypatch):
        """position == 1.0 is on the boundary and must NOT be saved (guard is > 1.0)."""
        h = _save_session_host(tmp_path)
        h.player.position = 1.0
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        h._save_session_state()

        import json

        written = json.loads(target.read_text(encoding="utf-8"))
        assert written["resume"] is None

    def test_resume_saved_when_position_above_threshold(self, tmp_path, monkeypatch):
        h = _save_session_host(tmp_path)
        h.player.position = 42.5
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        h._save_session_state()

        import json

        written = json.loads(target.read_text(encoding="utf-8"))
        assert written["resume"] is not None
        assert written["resume"]["video_id"] == "abc"
        assert written["resume"]["position"] == 42.5


_RESUME_A = {"video_id": "A", "position": 42.0, "playlist_id": "PL"}


def _track(video_id: str) -> dict:
    return {
        "video_id": video_id,
        "title": f"song-{video_id}",
        "artist": "Artist",
        "artists": [{"name": "Artist", "id": "1"}],
        "album": "",
        "album_id": None,
        "duration": 180,
        "thumbnail_url": None,
        "is_video": False,
    }


def _write_session(path, *, queue_ids=("A", "B"), resume=_RESUME_A) -> None:
    path.write_text(
        json.dumps(
            {
                "schema_version": 1,
                "volume": 80,
                "repeat": "off",
                "shuffle": False,
                "queue_tracks": [_track(v) for v in queue_ids],
                "queue_index": 0,
                "resume": resume,
            }
        ),
        encoding="utf-8",
    )


def _restore_host(tmp_path, monkeypatch, *, resume_on_launch: bool, queue_ids=("A", "B")):
    """A session host with a real QueueManager and a session file on disk."""
    h = _fresh_session_host()
    h._session_restored = False
    h.settings.playback.resume_on_launch = resume_on_launch
    h.queue = QueueManager()
    h.player.current_track = None
    h.player.position = 0.0
    h.player.volume = 80
    h._get_transliteration_state = lambda: False
    target = tmp_path / "session.json"
    _write_session(target, queue_ids=queue_ids)
    monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)
    return h, target


def _saved(target) -> dict:
    return json.loads(target.read_text(encoding="utf-8"))


class TestUntouchedSessionPreserved:
    """The resume point read from disk is written back until a track's load
    is accepted this session; after that only the live rule applies."""

    @pytest.mark.parametrize("resume_on_launch", [True, False])
    async def test_save_without_playback_writes_the_disk_resume_back(
        self, tmp_path, monkeypatch, resume_on_launch
    ):
        h, target = _restore_host(tmp_path, monkeypatch, resume_on_launch=resume_on_launch)
        await h._restore_session_state()

        h._save_session_state()

        saved = _saved(target)
        assert saved["resume"] == _RESUME_A
        assert [t["video_id"] for t in saved["queue_tracks"]] == ["A", "B"]

    async def test_resume_track_missing_from_queue_is_still_preserved(self, tmp_path, monkeypatch):
        h, target = _restore_host(tmp_path, monkeypatch, resume_on_launch=True, queue_ids=("C",))
        await h._restore_session_state()
        assert h._pending_resume_video_id is None  # nothing staged

        h._save_session_state()

        assert _saved(target)["resume"] == _RESUME_A

    @pytest.mark.parametrize("resume_on_launch", [True, False])
    async def test_play_resumed_track_then_stop_saves_no_resume(
        self, tmp_path, monkeypatch, resume_on_launch
    ):
        h, target = _restore_host(tmp_path, monkeypatch, resume_on_launch=resume_on_launch)
        await h._restore_session_state()
        # What play_track does once A's load is accepted, then Stop.
        h._loaded_resume = None
        h._pending_resume_video_id = None
        h._pending_resume_position = 0.0
        h.player.current_track = None
        h.player.position = 0.0

        h._save_session_state()

        assert _saved(target)["resume"] is None

    @pytest.mark.parametrize("resume_on_launch", [True, False])
    async def test_play_other_track_then_stop_saves_no_resume(
        self, tmp_path, monkeypatch, resume_on_launch
    ):
        h, target = _restore_host(tmp_path, monkeypatch, resume_on_launch=resume_on_launch)
        await h._restore_session_state()
        h._loaded_resume = None  # B's load accepted; the staged A seek stays in-session only
        h.player.current_track = None
        h.player.position = 0.0

        h._save_session_state()

        assert _saved(target)["resume"] is None

    async def test_other_track_still_playing_saves_its_live_position(self, tmp_path, monkeypatch):
        h, target = _restore_host(tmp_path, monkeypatch, resume_on_launch=True)
        await h._restore_session_state()
        h._loaded_resume = None
        h.player.current_track = {"video_id": "B", "title": "song-B"}
        h.player.position = 30.0

        h._save_session_state()

        assert _saved(target)["resume"] == {"video_id": "B", "position": 30.0, "playlist_id": "PL"}


class _SequenceHost(SessionMixin, PlaybackMixin, MPRISMixin):
    """Real mixin code end to end: restore → play_track → Stop handler → save."""


def _sequence_host(tmp_path, monkeypatch, *, resume_on_launch: bool):
    h = _SequenceHost()
    h.settings = MagicMock()
    h.settings.playback.default_volume = 80
    h.settings.playback.resume_on_launch = resume_on_launch
    h.settings.notifications.enabled = False
    h.player = MagicMock()
    h.player.set_volume = AsyncMock()
    h.player.seek_absolute = AsyncMock()
    h.player.current_track = None
    h.player.position = 0.0
    h.player.volume = 80

    async def _accept_load(url, track, attempt=None):
        h.player.current_track = track

    h.player.play = AsyncMock(side_effect=_accept_load)

    async def _stop():
        # Mirrors Player.stop(): nothing is current and mpv reads position 0.
        h.player.current_track = None
        h.player.position = 0.0

    h.player.stop = AsyncMock(side_effect=_stop)
    h.queue = QueueManager()
    h.stream_resolver = MagicMock()
    h.stream_resolver.clear_cache = MagicMock()

    async def _resolve(video_id):
        return StreamInfo(
            url=f"http://stream/{video_id}",
            video_id=video_id,
            format="opus",
            bitrate=0,
            duration=180,
            expires_at=float("inf"),
            thumbnail_url=None,
        )

    h.stream_resolver.resolve = AsyncMock(side_effect=_resolve)
    h.history = None
    h.cache = None
    h.discord = None
    h.lastfm = None
    h.mpris = None
    h.mac_media = None
    h.ytmusic = None
    h.notify = MagicMock()
    h.call_later = MagicMock()
    h.run_worker = MagicMock()
    h.set_timer = MagicMock()
    h.query_one = MagicMock(side_effect=Exception("no widget in test"))
    h._sidebar_per_page = {}
    h._sidebar_default = True
    h._lyrics_sidebar_open = False
    h._active_library_playlist_id = None
    h._pending_resume_video_id = None
    h._pending_resume_position = 0.0
    h._first_run_hint_shown = False
    h._mpris_hint_shown = False
    h._session_restored = False
    h._loaded_resume = None
    h._last_play_video_id = None
    h._last_play_time = 0.0
    h._consecutive_failures = 0
    h._track_start_position = 0.0
    h._advancing = False
    h._play_generation = 0
    h._recovery_generation = None
    h._handled_error_attempt = 0
    h._local_history_claim = None
    h._play_lock = asyncio.Lock()
    h._get_transliteration_state = lambda: False
    target = tmp_path / "session.json"
    _write_session(target)
    monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)
    return h, target


class TestRestorePlayStopSaveSequence:
    """End to end through the real mixins, not by clearing _loaded_resume by hand.

    Stop goes through the real ``MPRISMixin._mpris_stop``; only ``Player.stop``
    underneath it is mocked.
    """

    @pytest.mark.parametrize("resume_on_launch", [True, False])
    @pytest.mark.parametrize("played", ["A", "B"], ids=["resumed-track", "other-track"])
    async def test_restore_play_stop_save_sequence(
        self, tmp_path, monkeypatch, resume_on_launch, played
    ):
        h, target = _sequence_host(tmp_path, monkeypatch, resume_on_launch=resume_on_launch)
        await h._restore_session_state()
        assert h._loaded_resume == _RESUME_A
        assert (h._pending_resume_video_id == "A") is resume_on_launch

        await h.play_track(_track(played))
        h.player.play.assert_awaited_once()
        assert h._loaded_resume is None
        generation = h._play_generation

        await h._mpris_stop()
        h.player.stop.assert_awaited_once()
        assert h._play_generation == generation + 1

        h._save_session_state()

        saved = _saved(target)
        assert saved["resume"] is None
        assert [t["video_id"] for t in saved["queue_tracks"]] == ["A", "B"]

    @pytest.mark.parametrize("resume_on_launch", [True, False])
    async def test_restore_then_save_untouched(self, tmp_path, monkeypatch, resume_on_launch):
        h, target = _sequence_host(tmp_path, monkeypatch, resume_on_launch=resume_on_launch)
        await h._restore_session_state()

        h._save_session_state()

        saved = _saved(target)
        assert saved["resume"] == _RESUME_A
        assert [t["video_id"] for t in saved["queue_tracks"]] == ["A", "B"]
        h.player.play.assert_not_awaited()


class TestSaveGatedOnRestore:
    """No restore this run → nothing in memory is authoritative → no save."""

    def test_save_before_restore_leaves_the_file_untouched(self, tmp_path, monkeypatch, caplog):
        target = tmp_path / "session.json"
        _write_session(target)
        before = target.read_bytes()
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)
        h = _save_session_host(tmp_path)
        h._session_restored = False
        h.player.position = 42.0  # would have been saved as a live resume

        with caplog.at_level("WARNING"):
            h._save_session_state()

        assert target.read_bytes() == before
        assert not target.with_suffix(".json.tmp").exists()
        assert "never restored" in caplog.text

    async def test_deliberate_queue_clear_after_restore_is_saved(self, tmp_path, monkeypatch):
        h, target = _restore_host(tmp_path, monkeypatch, resume_on_launch=True)
        await h._restore_session_state()
        assert h.queue.length == 2

        h.queue.clear()
        h._save_session_state()

        assert _saved(target)["queue_tracks"] == []

    @pytest.mark.parametrize("resume_on_launch", [True, False])
    async def test_restore_sets_the_flag_on_both_exit_paths(
        self, tmp_path, monkeypatch, resume_on_launch
    ):
        h, _ = _restore_host(tmp_path, monkeypatch, resume_on_launch=resume_on_launch)
        assert h._session_restored is False

        await h._restore_session_state()

        assert h._session_restored is True

    async def test_restore_that_raises_leaves_the_flag_false(self, tmp_path, monkeypatch):
        h, _ = _restore_host(tmp_path, monkeypatch, resume_on_launch=True)
        h.queue = MagicMock()
        h.queue.add_multiple = MagicMock(side_effect=RuntimeError("boom"))

        with pytest.raises(RuntimeError, match="boom"):
            await h._restore_session_state()

        assert h._session_restored is False


class TestSessionShape:
    @pytest.mark.parametrize("content", ["[]", '"x"', "42"])
    async def test_non_object_json_is_treated_as_empty(self, tmp_path, monkeypatch, content):
        h = _fresh_session_host()
        h._session_restored = False
        bad = tmp_path / "session.json"
        bad.write_text(content, encoding="utf-8")
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", bad, raising=False)

        await h._restore_session_state()

        h.player.set_volume.assert_awaited_once_with(80)
        h.queue.set_repeat.assert_called_once_with(RepeatMode.OFF)
        assert h._loaded_resume is None
        assert h._session_restored is True


class TestMprisHintFlag:
    """The 'install dbus-fast' hint must show once, then persist (#110)."""

    async def test_restore_reads_saved_flag(self, tmp_path, monkeypatch):
        h = _fresh_session_host()
        good = tmp_path / "session.json"
        good.write_text('{"schema_version": 1, "mpris_hint_shown": true}', encoding="utf-8")
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", good, raising=False)
        await h._restore_session_state()
        assert h._mpris_hint_shown is True

    async def test_restore_defaults_false_when_absent(self, tmp_path, monkeypatch):
        h = _fresh_session_host()
        good = tmp_path / "session.json"
        good.write_text('{"schema_version": 1}', encoding="utf-8")
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", good, raising=False)
        await h._restore_session_state()
        assert h._mpris_hint_shown is False

    def test_save_persists_flag(self, tmp_path, monkeypatch):
        h = _save_session_host(tmp_path)
        h._mpris_hint_shown = True
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        h._save_session_state()

        import json

        written = json.loads(target.read_text(encoding="utf-8"))
        assert written["mpris_hint_shown"] is True


class TestSaveSessionFailureVisibility:
    """When the session.json write fails, the user must be notified.

    Previously failures were swallowed with a logger.warning — the user's
    queue and position would silently reset on next launch with no
    signal anything went wrong. The narrowed catch surfaces the
    failure via self.notify and lets unexpected exceptions propagate.
    """

    def test_oserror_triggers_notify(self, tmp_path, monkeypatch):
        """Disk-full / permission-denied / read-only-fs all raise OSError."""
        h = _save_session_host(tmp_path)
        h.notify = MagicMock()
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        # Force the atomic-write to fail with OSError (simulates disk full).
        from pathlib import Path as _Path

        original_write_text = _Path.write_text

        def _boom(self, *args, **kwargs):
            if self.name.endswith(".json.tmp"):
                raise OSError("No space left on device")
            return original_write_text(self, *args, **kwargs)

        monkeypatch.setattr(_Path, "write_text", _boom)

        # Should NOT raise — failure is caught and surfaced via notify.
        h._save_session_state()

        h.notify.assert_called_once()
        args, kwargs = h.notify.call_args
        message = args[0] if args else kwargs.get("message", "")
        assert "Could not save session state" in message
        assert kwargs.get("severity") == "warning"

    def test_typeerror_triggers_notify(self, tmp_path, monkeypatch):
        """An unserialisable value in state raises TypeError from json.dumps."""
        h = _save_session_host(tmp_path)
        h.notify = MagicMock()
        # A custom object slipped into the persisted state won't serialise.
        h._sidebar_per_page = {"library": object()}
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        h._save_session_state()

        h.notify.assert_called_once()
        _, kwargs = h.notify.call_args
        assert kwargs.get("severity") == "warning"

    def test_unexpected_exception_propagates(self, tmp_path, monkeypatch):
        """Programming errors (RuntimeError, etc.) must NOT be swallowed."""
        import pytest

        h = _save_session_host(tmp_path)
        h.notify = MagicMock()
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        from pathlib import Path as _Path

        def _boom(self, *args, **kwargs):
            if self.name.endswith(".json.tmp"):
                raise RuntimeError("programming bug")
            return None

        monkeypatch.setattr(_Path, "write_text", _boom)

        with pytest.raises(RuntimeError, match="programming bug"):
            h._save_session_state()

        h.notify.assert_not_called()

    def test_notify_failure_does_not_crash_save(self, tmp_path, monkeypatch):
        """If notify itself raises (e.g. app shutting down), save still returns cleanly."""
        h = _save_session_host(tmp_path)
        h.notify = MagicMock(side_effect=RuntimeError("app shutting down"))
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        from pathlib import Path as _Path

        def _boom(self, *args, **kwargs):
            if self.name.endswith(".json.tmp"):
                raise OSError("No space left on device")
            return None

        monkeypatch.setattr(_Path, "write_text", _boom)

        # Even though notify raises, _save_session_state must not propagate.
        h._save_session_state()
        h.notify.assert_called_once()


class TestFirstRunHint:
    """Task 4.8: track first-run state in session.json so the
    'Press ? for help' toast fires only once."""

    async def test_first_run_default_is_false(self, tmp_path, monkeypatch):
        """A fresh install has no session.json, so first_run_hint_shown
        defaults to False."""
        h = _fresh_session_host()
        missing = tmp_path / "missing.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", missing, raising=False)
        await h._restore_session_state()
        assert h._first_run_hint_shown is False

    async def test_save_persists_first_run_flag(self, tmp_path, monkeypatch):
        """When the flag is True at save time, it round-trips through
        save → disk → restore."""
        h = _save_session_host(tmp_path)
        h._first_run_hint_shown = True
        target = tmp_path / "session.json"
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        h._save_session_state()

        # Build a fresh host with the saved file and verify the flag rehydrates.
        h2 = _fresh_session_host()
        await h2._restore_session_state()
        assert h2._first_run_hint_shown is True

    async def test_legacy_session_without_field_loads_as_false(self, tmp_path, monkeypatch):
        """A pre-4.8 session.json (no first_run_hint_shown key) loads
        without crashing; flag defaults to False so the hint shows once
        for users who upgrade from earlier versions."""
        import json

        legacy = {
            "schema_version": 1,
            "volume": 80,
            "repeat": "off",
            "shuffle": False,
            "queue_tracks": [],
            "queue_index": 0,
        }
        target = tmp_path / "session.json"
        target.write_text(json.dumps(legacy), encoding="utf-8")
        monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

        h = _fresh_session_host()
        await h._restore_session_state()
        assert h._first_run_hint_shown is False
