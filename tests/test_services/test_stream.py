"""Tests for StreamResolver cache and expiry logic."""

import time

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
