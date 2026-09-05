"""Tests for playlist playback notifications."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock

import pytest

from ytm_player.app._track_actions import TrackActionsMixin
from ytm_player.services.queue import QueueManager


def _make_host(saved_pref: bool | None) -> MagicMock:
    host = MagicMock()
    host.ytmusic = MagicMock()
    host.ytmusic.get_playlist = AsyncMock(
        return_value={
            "tracks": [
                {"videoId": "v1", "title": "T1", "artists": [{"name": "A", "id": "a"}]},
                {"videoId": "v2", "title": "T2", "artists": [{"name": "A", "id": "a"}]},
            ],
            "trackCount": 2,
        }
    )
    host.queue = QueueManager()
    host.shuffle_prefs = MagicMock()
    host.shuffle_prefs.get = MagicMock(return_value=saved_pref)
    host.play_track = AsyncMock()
    host._refresh_queue_page = MagicMock()
    host._sync_shuffle_bar = MagicMock()
    host.notify = MagicMock()
    host.run_worker = MagicMock()
    host._replace_queue_and_play = TrackActionsMixin._replace_queue_and_play.__get__(host)
    return host


@pytest.mark.parametrize(
    ("saved_pref", "expected_message"),
    [(True, "Playing: Pangaea (shuffled)"), (None, "Playing: Pangaea")],
)
async def test_play_playlist_toast_reflects_shuffle_state(saved_pref, expected_message):
    host = _make_host(saved_pref)

    await TrackActionsMixin._play_playlist(host, "PL1", "Pangaea")

    host.notify.assert_called_once_with(expected_message, timeout=4)
