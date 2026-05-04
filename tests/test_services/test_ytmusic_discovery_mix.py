"""Tests for YTMusicService.get_discovery_mix."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ytm_player.services.ytmusic import YTMusicService

# Source mapping: 1=charts, 2=trending, 3=home,
#                 4=liked_songs, 5=artist, 6=history
# Round-robin: start = (_last_discovery_source % 6) + 1


@pytest.fixture
def svc():
    s = YTMusicService.__new__(YTMusicService)
    s._auth_path = Path(__file__)
    s._auth_manager = None
    s._user = None
    s._consecutive_api_failures = 0
    s._order_lock = asyncio.Lock()
    s._last_discovery_source = 0
    s._last_chart_shelf = 0
    s._client_init_lock = MagicMock()
    s._ytm = MagicMock()
    return s


class TestGetDiscoveryMix:
    async def test_returns_seeds_and_label(self, svc):
        """get_discovery_mix returns (seed_tracks, label) tuple."""
        svc._last_discovery_source = 1  # → starts at source 2 (trending)

        with patch.object(
            svc,
            "_call",
            new_callable=AsyncMock,
            side_effect=[
                {"trending": {"playlist": "PLtrend"}},
                {"tracks": [{"videoId": "t1", "title": "Hit Song"}]},
            ],
        ):
            seeds, label = await svc.get_discovery_mix()

        assert isinstance(seeds, list)
        assert len(seeds) > 0
        assert all("videoId" in s for s in seeds)
        assert label == "Trending"

    async def test_round_robin_charts_first(self, svc):
        """First discovery call hits charts (source 1)."""
        svc._last_discovery_source = 0  # → starts at source 1 (charts)

        with (
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                return_value={"daily": [{"playlistId": "PLdaily", "title": "Daily Top 100"}]},
            ),
            patch.object(
                svc,
                "get_chart_shelf_tracks",
                new_callable=AsyncMock,
                return_value=[{"videoId": "c1", "title": "Hot Song"}],
            ),
        ):
            seeds, label = await svc.get_discovery_mix()

        assert "Daily Top 100" in label
        assert len(seeds) > 0

    async def test_fallback_skips_to_next_source(self, svc):
        """If first source fails, advances to the next in round-robin order."""
        svc._last_discovery_source = 5  # → order: 6(history), 1(charts), 2(trending)...
        call_count = 0

        async def mock_call(func, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("history API down")
            if call_count == 2:
                raise RuntimeError("charts API down")
            if call_count == 3:
                return {"trending": {"playlist": "PL999"}}
            return {"tracks": [{"videoId": "fb1", "title": "Fallback Song"}]}

        with patch.object(svc, "_call", side_effect=mock_call):
            seeds, label = await svc.get_discovery_mix()

        assert label == "Trending"
        assert len(seeds) > 0

    async def test_seeds_have_video_id(self, svc):
        """Returned seeds have videoId keys (raw API format, not normalized)."""
        svc._last_discovery_source = 1  # → starts at source 2 (trending)

        with patch.object(
            svc,
            "_call",
            new_callable=AsyncMock,
            side_effect=[
                {"trending": {"playlist": "PL123"}},
                {"tracks": [{"videoId": "norm1", "title": "Song"}]},
            ],
        ):
            seeds, _ = await svc.get_discovery_mix()

        assert all("videoId" in s for s in seeds)

    async def test_all_sources_fail_returns_empty(self, svc):
        """Returns ([], '') when all sources fail."""

        async def always_fail(func, *args, **kwargs):
            raise RuntimeError("API down")

        with patch.object(svc, "_call", side_effect=always_fail):
            seeds, label = await svc.get_discovery_mix()

        assert seeds == []
        assert label == ""

    async def test_last_source_tracked_for_rotation(self, svc):
        """_last_discovery_source advances after a successful call."""
        svc._last_discovery_source = 1  # → starts at source 2 (trending)

        with patch.object(
            svc,
            "_call",
            new_callable=AsyncMock,
            side_effect=[
                {"trending": {"playlist": "PL1"}},
                {"tracks": [{"videoId": "r1", "title": "Song"}]},
            ],
        ):
            await svc.get_discovery_mix()

        assert svc._last_discovery_source == 2

    async def test_charts_mix_reads_weekly_key(self, svc):
        """_charts_mix must read the 'weekly' key — some regions only serve
        chart shelves there (e.g. US/UK Top 100 Songs)."""
        svc._last_discovery_source = 0  # → starts at source 1 (charts)

        with (
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                return_value={"weekly": [{"playlistId": "PLweekly", "title": "Weekly Top Songs"}]},
            ),
            patch.object(
                svc,
                "get_chart_shelf_tracks",
                new_callable=AsyncMock,
                return_value=[{"videoId": "w1", "title": "Weekly Hit"}],
            ) as mock_tracks,
        ):
            seeds, label = await svc.get_discovery_mix()

        assert len(seeds) > 0
        assert "Weekly Top Songs" in label
        mock_tracks.assert_called_once_with("PLweekly")

    async def test_charts_mix_reads_videos_key(self, svc):
        """_charts_mix must read the 'videos' key — Spain and other regions
        return chart data exclusively under this key."""
        svc._last_discovery_source = 0  # → starts at source 1 (charts)

        with (
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                return_value={"videos": [{"playlistId": "PLvid", "title": "Top Music Videos"}]},
            ),
            patch.object(
                svc,
                "get_chart_shelf_tracks",
                new_callable=AsyncMock,
                return_value=[{"videoId": "v1", "title": "Video Hit"}],
            ) as mock_tracks,
        ):
            seeds, label = await svc.get_discovery_mix()

        assert len(seeds) > 0
        assert "Top Music Videos" in label
        mock_tracks.assert_called_once_with("PLvid")

    async def test_charts_mix_combines_all_chart_keys(self, svc):
        """_charts_mix should aggregate shelves from daily + weekly + videos,
        cycling through all of them in round-robin."""
        svc._last_discovery_source = 0  # → starts at source 1 (charts)

        chart_response = {
            "daily": [{"playlistId": "PL1", "title": "Daily Top 100 Songs"}],
            "weekly": [{"playlistId": "PL2", "title": "Weekly Top Songs"}],
            "videos": [{"playlistId": "PL3", "title": "Top Music Videos"}],
        }

        seen_labels: list[str] = []
        for i in range(3):
            svc._last_discovery_source = 0
            svc._last_chart_shelf = i - 1  # cycle through shelves 0, 1, 2

            with (
                patch.object(
                    svc,
                    "_call",
                    new_callable=AsyncMock,
                    return_value=chart_response,
                ),
                patch.object(
                    svc,
                    "get_chart_shelf_tracks",
                    new_callable=AsyncMock,
                    return_value=[{"videoId": f"t{i}", "title": f"Track {i}"}],
                ),
            ):
                _, label = await svc.get_discovery_mix()
                seen_labels.append(label)

        assert any("Daily Top 100" in lbl for lbl in seen_labels)
        assert any("Weekly Top Songs" in lbl for lbl in seen_labels)
        assert any("Top Music Videos" in lbl for lbl in seen_labels)
