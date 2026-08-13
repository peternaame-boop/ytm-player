"""Tests for YTMusicService.get_new_release_mix."""

from __future__ import annotations

from unittest.mock import AsyncMock, patch

import pytest

from tests.conftest import make_ytmusic_service


@pytest.fixture
def svc():
    return make_ytmusic_service()


class TestGetNewReleaseMix:
    async def test_returns_seeds_and_label(self, svc):
        release = {"title": "Dark Night", "audioPlaylistId": "OLAK5uy_abc"}

        with (
            patch.object(svc, "get_new_releases", new_callable=AsyncMock, return_value=[release]),
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                return_value={"tracks": [{"videoId": "t1", "title": "Track One"}]},
            ) as mock_call,
        ):
            seeds, label = await svc.get_new_release_mix()

        assert isinstance(seeds, list)
        assert len(seeds) > 0
        assert all("videoId" in s for s in seeds)
        assert label == "New Release Mix: Dark Night"
        mock_call.assert_called_once()
        _, call_kwargs = mock_call.call_args
        assert call_kwargs.get("playlistId") == "OLAK5uy_abc"

    async def test_returns_empty_when_no_releases(self, svc):
        with patch.object(svc, "get_new_releases", new_callable=AsyncMock, return_value=[]):
            seeds, label = await svc.get_new_release_mix()

        assert seeds == []
        assert label == ""

    async def test_returns_empty_when_release_has_no_playlist_id(self, svc):
        release = {"title": "No Playlist Here"}

        with patch.object(svc, "get_new_releases", new_callable=AsyncMock, return_value=[release]):
            seeds, label = await svc.get_new_release_mix()

        assert seeds == []
        assert label == ""

    async def test_returns_empty_when_watch_playlist_fails(self, svc):
        release = {"title": "Dark Night", "audioPlaylistId": "OLAK5uy_abc"}

        with (
            patch.object(svc, "get_new_releases", new_callable=AsyncMock, return_value=[release]),
            patch.object(
                svc, "_call", new_callable=AsyncMock, side_effect=RuntimeError("API down")
            ),
        ):
            seeds, label = await svc.get_new_release_mix()

        assert seeds == []
        assert label == ""

    async def test_returns_empty_when_no_playable_tracks(self, svc):
        release = {"title": "Dark Night", "audioPlaylistId": "OLAK5uy_abc"}

        with (
            patch.object(svc, "get_new_releases", new_callable=AsyncMock, return_value=[release]),
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                return_value={"tracks": [{"title": "No video id"}]},
            ),
        ):
            seeds, label = await svc.get_new_release_mix()

        assert seeds == []
        assert label == ""

    async def test_samples_at_most_three_seeds(self, svc):
        release = {"title": "Dark Night", "audioPlaylistId": "OLAK5uy_abc"}
        tracks = [{"videoId": f"t{i}", "title": f"Track {i}"} for i in range(10)]

        with (
            patch.object(svc, "get_new_releases", new_callable=AsyncMock, return_value=[release]),
            patch.object(svc, "_call", new_callable=AsyncMock, return_value={"tracks": tracks}),
        ):
            seeds, _ = await svc.get_new_release_mix()

        assert len(seeds) == 3
