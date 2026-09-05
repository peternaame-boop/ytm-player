"""Integration test: session save/restore round-trip + corrupt-JSON fallback.

Exercises three end-to-end flows through the full SessionMixin:

1. Valid round-trip — ``_save_session_state`` writes; ``_restore_session_state``
   re-reads; the host's queue/player state reflects the persisted values.
2. Corrupt JSON — a malformed ``session.json`` falls back to defaults
   silently (no exception propagates, no state is misapplied).
3. Save-failure toast — when the atomic write raises ``OSError`` the failure
   surfaces via ``self.notify(..., severity="warning")`` with a message
   containing "Could not save session state".

The third assertion is the retroactive integration coverage for Task 4.6
(session-save toast). Task 4.6 already shipped with unit tests in
``tests/test_app/test_session.py::TestSaveSessionFailureVisibility``;
this test exercises the same contract through the full
session-state-dict construction path rather than minimal unit
scaffolding.
"""

from __future__ import annotations

from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

from ytm_player.app._session import SessionMixin
from ytm_player.services.queue import RepeatMode


def _build_session_host():
    """Build a SessionMixin host wired with the attribute surface load+save touch.

    Mirrors the ``_fresh_session_host`` / ``_save_session_host`` pattern
    in ``tests/test_app/test_session.py`` — the SessionMixin reaches for
    queue, player, settings, sidebar maps, and a couple of private
    resume slots, all of which need to exist or the mixin raises
    AttributeError before any business logic runs.
    """
    h = SessionMixin()

    # Player surface for both load (set_volume) and save (volume / current_track / position).
    h.player = MagicMock()
    h.player.set_volume = AsyncMock()
    h.player.volume = 80
    h.player.current_track = {"video_id": "abc", "title": "X"}
    h.player.position = 0.0

    # Queue surface — load uses set_repeat/add_multiple/jump_to/toggle_shuffle,
    # save reads tracks/current_index/repeat_mode/shuffle_enabled.
    h.queue = MagicMock()
    h.queue.set_repeat = MagicMock()
    h.queue.add_multiple = MagicMock()
    h.queue.jump_to = MagicMock()
    h.queue.toggle_shuffle = MagicMock()
    h.queue.set_context = MagicMock()
    h.queue.tracks = []
    h.queue.current_track = None
    h.queue.current_index = 0
    h.queue.current_context_id = None
    h.queue.repeat_mode = RepeatMode.OFF
    h.queue.shuffle_enabled = False

    # Settings — load reads default_volume + resume_on_launch.
    h.settings = MagicMock()
    h.settings.playback.default_volume = 80
    h.settings.playback.resume_on_launch = True
    h.settings.ui.theme = "catppuccin-mocha"

    # Misc state the mixin pokes at on load + save.
    h.query_one = MagicMock()
    h._sidebar_per_page = {}
    h._sidebar_default = True
    h._lyrics_sidebar_open = False
    h._active_library_playlist_id = None
    h._pending_resume_video_id = None
    h._pending_resume_position = 0.0
    h._first_run_hint_shown = False
    h._mpris_hint_shown = False
    h.theme = "ytm-dark"

    # Override _get_transliteration_state so save serialises a clean bool
    # rather than chasing query_one's MagicMock chain.
    h._get_transliteration_state = lambda: False

    return h


# --------------------------------------------------------------------------- #
# 1. Valid round-trip                                                         #
# --------------------------------------------------------------------------- #


async def test_valid_round_trip_persists_and_restores(tmp_path, monkeypatch):
    """Save state via the mixin; reload via the mixin; assert restored values."""
    target = tmp_path / "session.json"
    monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

    # ---- Save side: configure the host so save serialises a known state. ----
    saver = _build_session_host()
    saver.player.volume = 42
    saver.player.position = 0.0  # < 1.0 so resume is intentionally None
    saver.queue.repeat_mode = RepeatMode.ALL
    saver.queue.shuffle_enabled = False
    saver.queue.tracks = []
    saver.queue.current_index = 0

    saver._save_session_state()
    assert target.exists()

    # Sanity-check the on-disk shape — schema_version + headline values.
    import json

    on_disk = json.loads(target.read_text(encoding="utf-8"))
    assert on_disk["schema_version"] == 1
    assert on_disk["volume"] == 42
    assert on_disk["repeat"] == "all"
    assert on_disk["shuffle"] is False

    # ---- Restore side: a fresh host loads the file we just wrote. -----------
    loader = _build_session_host()
    loader.theme = loader.settings.ui.theme
    await loader._restore_session_state()

    loader.player.set_volume.assert_awaited_once_with(42)
    loader.queue.set_repeat.assert_called_once_with(RepeatMode.ALL)
    # shuffle was False -> toggle_shuffle must NOT have been called.
    loader.queue.toggle_shuffle.assert_not_called()
    # The configured default theme wins on startup; session theme is only runtime state.
    assert loader.theme == "catppuccin-mocha"


# --------------------------------------------------------------------------- #
# 2. Corrupt JSON fallback                                                    #
# --------------------------------------------------------------------------- #


async def test_corrupt_json_falls_back_to_defaults_silently(tmp_path, monkeypatch):
    """Garbage in session.json must not crash startup — defaults apply."""
    bad = tmp_path / "session.json"
    bad.write_text("not valid json {{ ", encoding="utf-8")
    monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", bad, raising=False)

    h = _build_session_host()
    # Should not raise.
    await h._restore_session_state()

    # Defaults applied: settings.playback.default_volume (80) and RepeatMode.OFF.
    h.player.set_volume.assert_awaited_once_with(80)
    h.queue.set_repeat.assert_called_once_with(RepeatMode.OFF)
    # No queued tracks were materialised from the garbage file.
    h.queue.add_multiple.assert_not_called()
    # Resume slots stayed at their startup defaults.
    assert h._pending_resume_video_id is None
    assert h._pending_resume_position == 0.0


# --------------------------------------------------------------------------- #
# 3. Save-failure toast (retroactive Task 4.6 coverage)                       #
# --------------------------------------------------------------------------- #


def test_save_oserror_surfaces_warning_notify(tmp_path, monkeypatch):
    """OSError during atomic write must surface via ``self.notify(severity="warning")``.

    Retroactive integration coverage for Task 4.6 (commit 1d2e3d7) —
    previously the failure was swallowed with a logger.warning and the
    user lost their queue and position silently on next launch.
    """
    target = tmp_path / "session.json"
    monkeypatch.setattr("ytm_player.config.paths.SESSION_STATE_FILE", target, raising=False)

    h = _build_session_host()
    h.notify = MagicMock()
    # Give the saver something serialisable but trigger the failure via the
    # tmp-file write — same shape as a disk-full / read-only-fs scenario.
    h.player.position = 42.5  # > 1.0 so resume IS populated, fuller payload

    original_write_text = Path.write_text

    def _boom(self, *args, **kwargs):
        if self.name.endswith(".json.tmp"):
            raise OSError("No space left on device")
        return original_write_text(self, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", _boom)

    # Must not propagate — failure is caught and surfaced via notify.
    h._save_session_state()

    h.notify.assert_called_once()
    args, kwargs = h.notify.call_args
    message = args[0] if args else kwargs.get("message", "")
    assert "Could not save session state" in message
    assert kwargs.get("severity") == "warning"
