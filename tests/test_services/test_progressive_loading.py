"""Tests for progressive playlist loading (two-phase fetch for large playlists)."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

from tests.conftest import make_ytmusic_service
from ytm_player.services.ytmusic import YTMusicService


def _make_playlist_data(num_tracks: int, track_count: int | None = None) -> dict:
    """Create a fake playlist response with *num_tracks* tracks."""
    tracks = [
        {
            "videoId": f"vid_{i:04d}",
            "title": f"Track {i}",
            "artists": [{"name": f"Artist {i}", "id": f"art_{i}"}],
            "album": {"name": f"Album {i}"},
            "duration_seconds": 180 + i,
            "thumbnails": [{"url": f"https://example.com/{i}.jpg"}],
        }
        for i in range(num_tracks)
    ]
    return {
        "title": "Big Playlist",
        "author": {"name": "Test Owner"},
        "trackCount": track_count if track_count is not None else num_tracks,
        "tracks": tracks,
    }


class TestCallTimeoutOverride:
    """Test that _call() respects the timeout parameter."""

    async def test_default_timeout_used(self):
        """_call() uses api_timeout from settings when no override is given."""
        svc = make_ytmusic_service()

        async def fake_wait_for(awaitable, **_kwargs):
            # Drain the to_thread coroutine so it doesn't warn at GC,
            # then return the func's value.
            return await awaitable

        func = MagicMock(return_value="result")
        with patch("ytm_player.services.ytmusic.get_settings") as mock_settings:
            mock_settings.return_value.playback.api_timeout = 15
            with patch("asyncio.wait_for", side_effect=fake_wait_for) as wf:
                result = await svc._call(func, "arg1")
                assert result == "result"
                _, kwargs = wf.call_args
                assert kwargs.get("timeout") == 15

    async def test_custom_timeout_override(self):
        """_call() uses the provided timeout when explicitly given."""
        svc = make_ytmusic_service()

        async def fake_wait_for(awaitable, **_kwargs):
            return await awaitable

        func = MagicMock(return_value="result")
        with patch("ytm_player.services.ytmusic.get_settings") as mock_settings:
            mock_settings.return_value.playback.api_timeout = 15
            with patch("asyncio.wait_for", side_effect=fake_wait_for) as wf:
                result = await svc._call(func, "arg1", timeout=120)
                assert result == "result"
                _, kwargs = wf.call_args
                assert kwargs.get("timeout") == 120


class TestGetPlaylistRemaining:
    """Test the get_playlist_remaining helper method."""

    async def test_returns_tracks_after_offset(self):
        """get_playlist_remaining slices off tracks we already have."""
        svc = make_ytmusic_service()

        full_data = _make_playlist_data(500)

        with patch.object(svc, "get_playlist", new_callable=AsyncMock, return_value=full_data):
            remaining = await svc.get_playlist_remaining("PL_test", already_have=300)
            assert len(remaining) == 200
            # Check the remaining tracks start from index 300
            assert remaining[0]["videoId"] == "vid_0300"
            assert remaining[-1]["videoId"] == "vid_0499"

    async def test_returns_empty_when_no_more(self):
        """get_playlist_remaining returns empty list when already_have >= total."""
        svc = make_ytmusic_service()

        full_data = _make_playlist_data(300)

        with patch.object(svc, "get_playlist", new_callable=AsyncMock, return_value=full_data):
            remaining = await svc.get_playlist_remaining("PL_test", already_have=300)
            assert remaining == []

    async def test_uses_extended_timeout(self):
        """get_playlist_remaining passes _LARGE_PLAYLIST_TIMEOUT to get_playlist."""
        svc = make_ytmusic_service()

        with patch.object(
            svc, "get_playlist", new_callable=AsyncMock, return_value={"tracks": []}
        ) as mock_gp:
            await svc.get_playlist_remaining("PL_test", already_have=0, order="recently_added")
            mock_gp.assert_called_once_with(
                "PL_test",
                limit=None,
                order="recently_added",
                timeout=YTMusicService._LARGE_PLAYLIST_TIMEOUT,
            )

    async def test_forwards_order_parameter(self):
        """get_playlist_remaining forwards the order parameter."""
        svc = make_ytmusic_service()

        with patch.object(
            svc, "get_playlist", new_callable=AsyncMock, return_value={"tracks": []}
        ) as mock_gp:
            await svc.get_playlist_remaining("PL_test", already_have=0, order="a_to_z")
            mock_gp.assert_called_once_with("PL_test", limit=None, order="a_to_z", timeout=120)


class TestGetPlaylistLimitForwarding:
    """Test that get_playlist forwards the limit and timeout parameters."""

    async def test_limit_forwarded_to_client(self):
        """get_playlist passes the limit parameter to the underlying client."""
        svc = make_ytmusic_service()
        svc._ytm.get_playlist = MagicMock(return_value={"tracks": []})

        with patch.object(svc, "_call", new_callable=AsyncMock, return_value={"tracks": []}) as mc:
            await svc.get_playlist("PL_test", limit=300)
            mc.assert_called_once_with(svc._ytm.get_playlist, "PL_test", timeout=None, limit=300)

    async def test_timeout_forwarded(self):
        """get_playlist passes the timeout parameter to _call."""
        svc = make_ytmusic_service()
        svc._ytm.get_playlist = MagicMock(return_value={"tracks": []})

        with patch.object(svc, "_call", new_callable=AsyncMock, return_value={"tracks": []}) as mc:
            await svc.get_playlist("PL_test", limit=None, timeout=120)
            mc.assert_called_once_with(svc._ytm.get_playlist, "PL_test", timeout=120, limit=None)


class TestGetLikedSongsTimeout:
    """Test that get_liked_songs forwards timeout parameter."""

    async def test_timeout_forwarded(self):
        """get_liked_songs passes timeout to _call."""
        svc = make_ytmusic_service()
        svc._ytm.get_liked_songs = MagicMock(return_value={"tracks": []})

        with patch.object(svc, "_call", new_callable=AsyncMock, return_value={"tracks": []}) as mc:
            await svc.get_liked_songs(limit=300, timeout=120)
            mc.assert_called_once_with(svc._ytm.get_liked_songs, timeout=120, limit=300)

    async def test_default_timeout_is_none(self):
        """get_liked_songs passes timeout=None by default (uses api_timeout)."""
        svc = make_ytmusic_service()
        svc._ytm.get_liked_songs = MagicMock(return_value={"tracks": []})

        with patch.object(svc, "_call", new_callable=AsyncMock, return_value={"tracks": []}) as mc:
            await svc.get_liked_songs()
            mc.assert_called_once_with(svc._ytm.get_liked_songs, timeout=None, limit=None)


class TestLargePlaylistTimeout:
    """Test the _LARGE_PLAYLIST_TIMEOUT constant."""

    def test_large_playlist_timeout_value(self):
        """Ensure _LARGE_PLAYLIST_TIMEOUT is sufficient for large playlists."""
        assert YTMusicService._LARGE_PLAYLIST_TIMEOUT >= 60
