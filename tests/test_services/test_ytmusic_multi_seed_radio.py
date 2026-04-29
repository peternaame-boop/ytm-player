"""Tests for YTMusicService.get_radio."""

from __future__ import annotations

import asyncio
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from ytm_player.services.ytmusic import YTMusicService


@pytest.fixture
def svc():
    """Construct YTMusicService bypassing __init__."""
    s = YTMusicService.__new__(YTMusicService)
    s._auth_path = None
    s._auth_manager = None
    s._user = None
    s._consecutive_api_failures = 0
    s._order_lock = asyncio.Lock()
    s._client_init_lock = MagicMock()
    s._ytm = MagicMock()
    return s


def _watch_result(video_ids: list[str]) -> dict:
    return {"tracks": [{"videoId": vid, "title": f"Track {vid}"} for vid in video_ids]}


class TestGetSeededRadio:
    async def test_single_seed_returns_tracks(self, svc):
        with patch.object(
            svc,
            "_call",
            new_callable=AsyncMock,
            return_value=_watch_result(["a", "b", "c"]),
        ):
            result = await svc.get_radio(["v1"])

        assert len(result) > 0
        result_ids = {t["video_id"] for t in result}
        assert result_ids == {"a", "b", "c"}

    async def test_deduplication_across_seeds(self, svc):
        async def mock_call(func, **kwargs):
            vid = kwargs.get("videoId", "")
            results = {
                "v1": _watch_result(["a", "b", "c"]),
                "v2": _watch_result(["b", "c", "d"]),
                "v3": _watch_result(["e"]),
            }
            return results.get(vid, {"tracks": []})

        with patch.object(svc, "_call", side_effect=mock_call):
            result = await svc.get_radio(["v1", "v2", "v3"])

        result_ids = {t["video_id"] for t in result}
        assert result_ids == {"a", "b", "c", "d", "e"}

    async def test_no_seed_cap(self, svc):
        """All seeds are used — no artificial cap."""
        call_video_ids: list[str] = []

        async def mock_call(func, **kwargs):
            vid = kwargs.get("videoId", "")
            call_video_ids.append(vid)
            return _watch_result([f"{vid}_t"])

        with patch.object(svc, "_call", side_effect=mock_call):
            await svc.get_radio(["s1", "s2", "s3", "s4", "s5"])

        assert set(call_video_ids) == {"s1", "s2", "s3", "s4", "s5"}

    async def test_individual_seed_failure_does_not_abort(self, svc):
        async def mock_call(func, **kwargs):
            vid = kwargs.get("videoId", "")
            if vid == "bad":
                raise RuntimeError("API error")
            return _watch_result([f"{vid}_track"])

        with patch.object(svc, "_call", side_effect=mock_call):
            result = await svc.get_radio(["bad", "good"])

        result_ids = {t["video_id"] for t in result}
        assert "good_track" in result_ids

    async def test_result_trimmed_to_limit(self, svc):
        with patch.object(
            svc,
            "_call",
            new_callable=AsyncMock,
            return_value=_watch_result([f"v{i}" for i in range(40)]),
        ):
            result = await svc.get_radio(["seed1"], limit=10)

        assert len(result) == 10

    async def test_empty_seeds_returns_empty(self, svc):
        call_count = 0

        async def mock_call(func, **kwargs):
            nonlocal call_count
            call_count += 1
            return {"tracks": []}

        with patch.object(svc, "_call", side_effect=mock_call):
            result = await svc.get_radio([])

        assert result == []
        assert call_count == 0

    async def test_all_seeds_fail_returns_empty(self, svc):
        async def mock_call(func, **kwargs):
            raise RuntimeError("failure")

        with patch.object(svc, "_call", side_effect=mock_call):
            result = await svc.get_radio(["s1", "s2", "s3"])

        assert result == []

    async def test_handles_both_video_id_key_formats(self, svc):
        """Dedup works with both videoId and video_id keys."""

        async def mock_call(func, **kwargs):
            vid = kwargs.get("videoId", "")
            if vid == "v1":
                return {"tracks": [{"videoId": "shared"}]}
            return {"tracks": [{"video_id": "shared"}]}

        with patch.object(svc, "_call", side_effect=mock_call):
            result = await svc.get_radio(["v1", "v2"])

        ids = [t.get("video_id") for t in result]
        assert ids.count("shared") == 1
