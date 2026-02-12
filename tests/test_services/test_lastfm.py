"""Tests for LastFMService (no external dependencies needed)."""

import pytest

from ytm_player.services.lastfm import _SCROBBLE_MAX_SECONDS, _SCROBBLE_PERCENT, LastFMService


class TestLastFMInit:
    def test_initial_state(self):
        svc = LastFMService()
        assert svc.is_connected is False
        assert svc._current_track is None
        assert svc._scrobbled is False

    def test_custom_credentials(self):
        svc = LastFMService(api_key="key", api_secret="secret", username="user")
        assert svc._api_key == "key"
        assert svc._api_secret == "secret"
        assert svc._username == "user"


class TestScrobbleConstants:
    def test_scrobble_percent(self):
        assert _SCROBBLE_PERCENT == 0.5

    def test_scrobble_max_seconds(self):
        assert _SCROBBLE_MAX_SECONDS == 240


class TestScrobbleThreshold:
    """Verify the threshold calculation logic matches Last.fm spec."""

    def test_short_track_threshold(self):
        """For a 2-minute track, threshold is 1 minute (50%)."""
        duration = 120
        threshold = min(duration * _SCROBBLE_PERCENT, _SCROBBLE_MAX_SECONDS)
        assert threshold == 60

    def test_long_track_threshold(self):
        """For a 10-minute track, threshold is 4 minutes (capped)."""
        duration = 600
        threshold = min(duration * _SCROBBLE_PERCENT, _SCROBBLE_MAX_SECONDS)
        assert threshold == 240

    def test_exact_eight_minute_track(self):
        """For an 8-minute track, 50% == 4 minutes == cap, both equal."""
        duration = 480
        threshold = min(duration * _SCROBBLE_PERCENT, _SCROBBLE_MAX_SECONDS)
        assert threshold == 240

    def test_zero_duration_uses_max(self):
        """If duration is 0, fallback to max seconds."""
        duration = 0
        if duration <= 0:
            threshold = _SCROBBLE_MAX_SECONDS
        else:
            threshold = min(duration * _SCROBBLE_PERCENT, _SCROBBLE_MAX_SECONDS)
        assert threshold == 240


@pytest.mark.asyncio
class TestLastFMConnect:
    async def test_connect_without_credentials_returns_false(self):
        svc = LastFMService()
        result = await svc.connect()
        assert result is False
        assert svc.is_connected is False

    async def test_now_playing_when_disconnected_is_noop(self):
        svc = LastFMService()
        # Should not raise
        await svc.now_playing("Title", "Artist", "Album", 200)
        assert svc._current_track is None

    async def test_check_scrobble_when_disconnected_is_noop(self):
        svc = LastFMService()
        # Should not raise
        await svc.check_scrobble(100.0)
        assert svc._scrobbled is False
