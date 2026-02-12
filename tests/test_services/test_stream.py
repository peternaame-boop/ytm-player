"""Tests for StreamResolver cache and expiry logic."""

import asyncio
import time
from unittest.mock import MagicMock, patch

import pytest

from ytm_player.services.stream import StreamInfo, StreamResolver


def _make_info(video_id: str = "test123", ttl: float = 18000) -> StreamInfo:
    return StreamInfo(
        url=f"https://stream.example.com/{video_id}",
        video_id=video_id,
        format="opus",
        bitrate=128,
        duration=200,
        expires_at=time.time() + ttl,
    )


class TestStreamInfoCache:
    def test_cache_hit(self):
        resolver = StreamResolver()
        info = _make_info("abc")
        resolver._put_cache(info)
        cached = resolver._get_cached("abc")
        assert cached is not None
        assert cached.video_id == "abc"

    def test_cache_miss(self):
        resolver = StreamResolver()
        assert resolver._get_cached("nonexistent") is None

    def test_cache_expired(self):
        resolver = StreamResolver()
        info = StreamInfo(
            url="https://stream.example.com/old",
            video_id="old",
            format="opus",
            bitrate=128,
            duration=200,
            expires_at=time.time() - 10,  # Already expired
        )
        resolver._put_cache(info)
        assert resolver._get_cached("old") is None

    def test_invalidate(self):
        resolver = StreamResolver()
        resolver._put_cache(_make_info("vid1"))
        resolver.invalidate("vid1")
        assert resolver._get_cached("vid1") is None

    def test_clear_cache(self):
        resolver = StreamResolver()
        resolver._put_cache(_make_info("a"))
        resolver._put_cache(_make_info("b"))
        resolver.clear_cache()
        assert resolver._get_cached("a") is None
        assert resolver._get_cached("b") is None


class TestStreamExpiry:
    def test_not_cached_is_expired(self):
        resolver = StreamResolver()
        assert resolver.is_expired("nothing") is True

    def test_fresh_is_not_expired(self):
        resolver = StreamResolver()
        resolver._put_cache(_make_info("fresh", ttl=18000))
        assert resolver.is_expired("fresh") is False

    def test_near_expiry_is_expired(self):
        resolver = StreamResolver()
        # Expires in 4 minutes (under the 5-minute buffer)
        info = StreamInfo(
            url="https://stream.example.com/soon",
            video_id="soon",
            format="opus",
            bitrate=128,
            duration=200,
            expires_at=time.time() + 240,
        )
        resolver._put_cache(info)
        assert resolver.is_expired("soon") is True

    def test_just_over_buffer_not_expired(self):
        resolver = StreamResolver()
        # Expires in 6 minutes (over the 5-minute buffer)
        info = StreamInfo(
            url="https://stream.example.com/ok",
            video_id="ok",
            format="opus",
            bitrate=128,
            duration=200,
            expires_at=time.time() + 360,
        )
        resolver._put_cache(info)
        assert resolver.is_expired("ok") is False


class TestStreamQuality:
    def test_default_quality(self):
        resolver = StreamResolver()
        assert resolver.quality == "high"

    def test_set_valid_quality(self):
        resolver = StreamResolver()
        resolver.quality = "low"
        assert resolver.quality == "low"

    def test_set_invalid_quality_raises(self):
        resolver = StreamResolver()
        with pytest.raises(ValueError):
            resolver.quality = "ultra"


class TestCacheEviction:
    def test_prune_expired(self):
        resolver = StreamResolver()
        # Add fresh + expired
        resolver._put_cache(_make_info("fresh", ttl=18000))
        expired = StreamInfo(
            url="https://stream.example.com/old",
            video_id="old",
            format="opus",
            bitrate=128,
            duration=200,
            expires_at=time.time() - 1,
        )
        # Bypass pruning by directly setting cache
        with resolver._cache_lock:
            resolver._cache["old"] = expired
        removed = resolver.prune_expired()
        assert removed == 1
        assert resolver._get_cached("fresh") is not None
        assert resolver._get_cached("old") is None


def _fake_info_dict(video_id: str = "dQw4w9WgXcQ") -> dict:
    """Return a fake yt-dlp info dict for mocking extract_info."""
    return {
        "url": f"https://rr1---sn-fake.googlevideo.com/videoplayback?id={video_id}",
        "acodec": "opus",
        "abr": 128,
        "duration": 213,
        "thumbnail": f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg",
        "ext": "webm",
    }


def _mock_ydl(info_dict: dict | None = None):
    """Create a mock YoutubeDL that returns info_dict from extract_info."""
    mock_instance = MagicMock()
    mock_instance.extract_info.return_value = info_dict
    mock_class = MagicMock()
    mock_class.return_value = mock_instance
    return mock_class, mock_instance


class TestResolveSync:
    """Test the resolve_sync() path through real StreamResolver code with mocked yt-dlp."""

    def test_returns_stream_info_with_correct_fields(self):
        info_dict = _fake_info_dict("abc12345678")
        mock_class, mock_inst = _mock_ydl(info_dict)
        resolver = StreamResolver()
        with patch("yt_dlp.YoutubeDL", mock_class):
            result = resolver.resolve_sync("abc12345678")
        assert result is not None
        assert isinstance(result, StreamInfo)
        assert result.video_id == "abc12345678"
        assert result.url == info_dict["url"]
        assert result.format == "opus"
        assert result.bitrate == 128
        assert result.duration == 213
        assert result.expires_at > time.time()

    def test_uses_cache_on_second_call(self):
        info_dict = _fake_info_dict("cached01")
        mock_class, mock_inst = _mock_ydl(info_dict)
        resolver = StreamResolver()
        with patch("yt_dlp.YoutubeDL", mock_class):
            first = resolver.resolve_sync("cached01")
            second = resolver.resolve_sync("cached01")
        assert first is not None
        assert second is not None
        assert first.url == second.url
        # yt-dlp should only be called once; second call served from cache.
        assert mock_inst.extract_info.call_count == 1

    def test_returns_none_on_download_error(self):
        import yt_dlp

        mock_class, mock_inst = _mock_ydl(None)
        mock_inst.extract_info.side_effect = yt_dlp.utils.DownloadError("video unavailable")
        resolver = StreamResolver()
        with patch("yt_dlp.YoutubeDL", mock_class):
            result = resolver.resolve_sync("failVideo01")
        assert result is None

    def test_invalid_video_id_returns_none(self):
        resolver = StreamResolver()
        # Characters not matching [a-zA-Z0-9_-] should be rejected.
        result = resolver.resolve_sync("../etc/passwd")
        assert result is None


class TestResolveAsync:
    """Test the async resolve() path with mocked yt-dlp."""

    async def test_returns_stream_info(self):
        info_dict = _fake_info_dict("asyncVid01")
        mock_class, mock_inst = _mock_ydl(info_dict)
        resolver = StreamResolver()
        with patch("yt_dlp.YoutubeDL", mock_class):
            result = await resolver.resolve("asyncVid01")
        assert result is not None
        assert isinstance(result, StreamInfo)
        assert result.video_id == "asyncVid01"
        assert result.url == info_dict["url"]

    async def test_deduplicates_concurrent_requests(self):
        info_dict = _fake_info_dict("dedup01")
        mock_class, mock_inst = _mock_ydl(info_dict)

        # Add a small delay to extract_info so both tasks overlap.
        def slow_extract(*args, **kwargs):
            import time as _time

            _time.sleep(0.1)
            return info_dict

        mock_inst.extract_info.side_effect = slow_extract

        resolver = StreamResolver()
        with patch("yt_dlp.YoutubeDL", mock_class):
            results = await asyncio.gather(
                resolver.resolve("dedup01"),
                resolver.resolve("dedup01"),
            )
        assert results[0] is not None
        assert results[1] is not None
        assert results[0].url == results[1].url
        # extract_info should only be called once despite two concurrent resolve() calls.
        assert mock_inst.extract_info.call_count == 1
