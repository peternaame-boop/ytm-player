"""Tests for reporting TUI plays back to the YT Music account history.

``PlaybackMixin._maybe_report_ytm_play`` is the opt-out (default-on) hook
that fires a best-effort ``add_history_item`` when a play crosses a short
listen threshold, so tracks played in the TUI show up in the account
history like any other YT Music client.
"""

from __future__ import annotations

from unittest.mock import MagicMock

from ytm_player.app._playback import _YTM_HISTORY_MIN_SECONDS, PlaybackMixin

_TRACK = {"video_id": "vid1", "title": "Song"}


def _host(*, enabled: bool = True, ytmusic: object | None = "default") -> MagicMock:
    host = MagicMock()
    host.settings.playback.sync_history_to_ytmusic = enabled
    host.ytmusic = MagicMock() if ytmusic == "default" else ytmusic
    host.run_worker = MagicMock()
    return host


def _report(host, track=_TRACK, listened=_YTM_HISTORY_MIN_SECONDS) -> None:
    PlaybackMixin._maybe_report_ytm_play.__get__(host)(track, listened)


def test_qualifying_play_is_reported() -> None:
    host = _host()
    _report(host)
    host.ytmusic.add_history_item.assert_called_once_with("vid1")
    host.run_worker.assert_called_once()


def test_disabled_setting_skips_report() -> None:
    host = _host(enabled=False)
    _report(host)
    host.run_worker.assert_not_called()


def test_no_ytmusic_service_skips_report() -> None:
    host = _host(ytmusic=None)
    _report(host)
    host.run_worker.assert_not_called()


def test_below_threshold_skips_report() -> None:
    host = _host()
    _report(host, listened=_YTM_HISTORY_MIN_SECONDS - 1)
    host.ytmusic.add_history_item.assert_not_called()
    host.run_worker.assert_not_called()


def test_missing_video_id_skips_report() -> None:
    host = _host()
    _report(host, track={"title": "no id"})
    host.ytmusic.add_history_item.assert_not_called()
    host.run_worker.assert_not_called()


def test_none_track_skips_report() -> None:
    host = _host()
    _report(host, track=None)
    host.run_worker.assert_not_called()
