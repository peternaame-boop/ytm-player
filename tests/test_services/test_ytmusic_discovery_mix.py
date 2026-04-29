"""Tests for YTMusicService.get_discovery_mix."""

from __future__ import annotations

import asyncio
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ytm_player.services.ytmusic import YTMusicService


@pytest.fixture
def svc():
    s = YTMusicService.__new__(YTMusicService)
    s._auth_path = Path(__file__)
    s._auth_manager = None
    s._user = None
    s._consecutive_api_failures = 0
    s._order_lock = asyncio.Lock()
    s._last_discovery_source = -1
    s._client_init_lock = MagicMock()
    s._ytm = MagicMock()
    return s


class TestGetDiscoveryMix:
    async def test_returns_seeds_and_label(self, svc):
        """get_discovery_mix returns (seed_tracks, label) tuple."""

        def force_trending_first(lst):
            lst.sort(key=lambda x: 0 if x == 1 else 1)

        with (
            patch("random.shuffle", side_effect=force_trending_first),
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                side_effect=[
                    {"trending": {"playlist": "PLtrend"}},
                    {"tracks": [{"videoId": "t1", "title": "Hit Song"}]},
                ],
            ),
        ):
            seeds, label = await svc.get_discovery_mix()

        assert isinstance(seeds, list)
        assert len(seeds) > 0
        assert all("videoId" in s for s in seeds)
        assert label == "Radio: Hit Song"

    async def test_source_rotation_via_shuffle(self, svc):
        """Shuffled source list determines which source is tried first."""
        charts_data = {"videos": {"items": [{"videoId": "chart1", "title": "Hot Song"}]}}

        def force_charts_first(lst):
            lst.sort(key=lambda x: 0 if x == 3 else 1)

        with (
            patch("random.shuffle", side_effect=force_charts_first),
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                return_value=charts_data,
            ),
        ):
            seeds, label = await svc.get_discovery_mix()

        assert label == "Radio: Hot Song"
        assert len(seeds) > 0

    async def test_fallback_iterates_sources(self, svc):
        """If first source fails, iterates to the next in shuffled order."""
        call_count = 0

        async def mock_call(func, *args, **kwargs):
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                raise RuntimeError("charts API down")
            if call_count == 2:
                return {"trending": {"playlist": "PL999"}}
            return {"tracks": [{"videoId": "fb1", "title": "Fallback Song"}]}

        def force_charts_then_trending(lst):
            lst.sort(key=lambda x: {3: 0, 1: 1}.get(x, 2))

        with (
            patch("random.shuffle", side_effect=force_charts_then_trending),
            patch.object(svc, "_call", side_effect=mock_call),
        ):
            seeds, label = await svc.get_discovery_mix()

        assert label == "Radio: Fallback Song"
        assert len(seeds) > 0

    async def test_seeds_have_video_id(self, svc):
        """Returned seeds have videoId keys (raw API format, not normalized)."""

        def force_trending_first(lst):
            lst.sort(key=lambda x: 0 if x == 1 else 1)

        with (
            patch("random.shuffle", side_effect=force_trending_first),
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                side_effect=[
                    {"trending": {"playlist": "PL123"}},
                    {"tracks": [{"videoId": "norm1", "title": "Song"}]},
                ],
            ),
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

    async def test_source_rotation_excludes_last(self, svc):
        """The last-used source is excluded from the next call."""
        svc._last_discovery_source = 2

        captured_sources: list[int] = []

        def capture_shuffle(lst):
            captured_sources.extend(lst)

        with (
            patch("random.shuffle", side_effect=capture_shuffle),
            patch.object(
                svc,
                "_call",
                new_callable=AsyncMock,
                side_effect=[
                    {"trending": {"playlist": "PL1"}},
                    {"tracks": [{"videoId": "r1", "title": "Song"}]},
                ],
            ),
        ):
            await svc.get_discovery_mix()

        assert 2 not in captured_sources
