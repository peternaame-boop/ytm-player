"""Tests for YTMusicService.get_playlist_radio()."""

from __future__ import annotations

from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ytm_player.services.ytmusic import YTMusicService


def _make_raw_track(video_id: str = "abc123", title: str = "Song") -> dict:
    return {
        "videoId": video_id,
        "title": title,
        "artists": [{"name": "Artist", "id": "UCxxx"}],
        "album": {"name": "Album", "id": "MPREb_xxx"},
        "duration_seconds": 180,
        "thumbnails": [{"url": "https://example.com/thumb.jpg"}],
        "isAvailable": True,
    }


@pytest.fixture
def service() -> YTMusicService:
    svc = YTMusicService.__new__(YTMusicService)
    svc._ytm = MagicMock()
    svc._consecutive_api_failures = 0
    svc._order_lock = None
    return svc


async def test_get_playlist_radio_strips_vl_prefix(service: YTMusicService) -> None:
    """VL-prefixed playlist IDs should be stripped before forming the RDAMPL key."""
    raw_tracks = [_make_raw_track()]
    result_dict = {"tracks": raw_tracks}

    with patch.object(service, "_call", new=AsyncMock(return_value=result_dict)) as mock_call:
        tracks = await service.get_playlist_radio("VLPLabc123")

    mock_call.assert_called_once_with(
        service.client.get_watch_playlist,
        playlistId="RDAMPLPLabc123",
        radio=True,
    )
    assert tracks == raw_tracks


async def test_get_playlist_radio_no_vl_prefix(service: YTMusicService) -> None:
    """Playlist IDs without VL prefix should be used as-is."""
    raw_tracks = [_make_raw_track("vid1"), _make_raw_track("vid2")]
    result_dict = {"tracks": raw_tracks}

    with patch.object(service, "_call", new=AsyncMock(return_value=result_dict)) as mock_call:
        tracks = await service.get_playlist_radio("PLabc123")

    mock_call.assert_called_once_with(
        service.client.get_watch_playlist,
        playlistId="RDAMPLPLabc123",
        radio=True,
    )
    assert tracks == raw_tracks


async def test_get_playlist_radio_returns_tracks(service: YTMusicService) -> None:
    """Returns the tracks list from the API response."""
    raw_tracks = [_make_raw_track("v1"), _make_raw_track("v2"), _make_raw_track("v3")]

    with patch.object(service, "_call", new=AsyncMock(return_value={"tracks": raw_tracks})):
        tracks = await service.get_playlist_radio("PLtest")

    assert tracks == raw_tracks
    assert len(tracks) == 3


async def test_get_playlist_radio_empty_response_logs_warning(
    service: YTMusicService, caplog: pytest.LogCaptureFixture
) -> None:
    """An empty track list from the API should emit a warning log."""
    with patch.object(service, "_call", new=AsyncMock(return_value={"tracks": []})):
        import logging

        with caplog.at_level(logging.WARNING, logger="ytm_player.services.ytmusic"):
            tracks = await service.get_playlist_radio("PLempty")

    assert tracks == []
    assert any("no tracks" in record.message.lower() for record in caplog.records)


async def test_get_playlist_radio_exception_returns_empty(service: YTMusicService) -> None:
    """On API failure, returns empty list without raising."""
    with patch.object(service, "_call", new=AsyncMock(side_effect=Exception("network error"))):
        tracks = await service.get_playlist_radio("PLfail")

    assert tracks == []


async def test_get_playlist_radio_non_dict_response_returns_empty(service: YTMusicService) -> None:
    """If the API returns a non-dict, returns empty list."""
    with patch.object(service, "_call", new=AsyncMock(return_value=None)):
        tracks = await service.get_playlist_radio("PLnone")

    assert tracks == []
