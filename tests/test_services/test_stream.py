"""Tests for StreamResolver cache and expiry logic."""

import asyncio
import logging
import time
from unittest.mock import MagicMock, patch

import pytest

from ytm_player.services.stream import StreamInfo, StreamResolver


@pytest.fixture(autouse=True)
def _no_real_cookie_detection(monkeypatch):
    """_build_ydl_opts() calls _detect_stream_cookies() for real whenever
    cookiefile isn't already configured (the default — see
    YtDlpSettings.cookies_file) — and that function checks whether
    AuthManager has already written a stream cookiejar file to disk.
    TestResolveSync/TestResolveAsync below exercise resolve_sync()/
    resolve() through the real _build_ydl_opts() path with only
    yt_dlp.YoutubeDL mocked, so without stubbing this out, a routine test
    run could pick up a real cookiejar file from the developer's actual
    ~/.config/ytm-player/ — the side effect this project's test suite is
    built to avoid.
    """
    monkeypatch.setattr("ytm_player.services.stream._detect_stream_cookies", lambda: None)


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

    def test_clear_cache_also_clears_stranded_missing_remote_components_entries(self):
        """A prefetched-then-skipped-without-playing video's flag is never
        consumed (only play_track()/the resolver-warmup path consume by
        video_id) -- clear_cache() is the natural "start fresh" point that
        sweeps those stranded entries, since accepting the remote_components
        prompt or a failure-recovery reset both make any pending diagnosis
        moot."""
        resolver = StreamResolver()
        resolver._resolving_video_id.value = "stranded_vid"
        resolver._flag_missing_remote_components()
        assert resolver._peek_missing_remote_components("stranded_vid") is True

        resolver.clear_cache()

        assert resolver._peek_missing_remote_components("stranded_vid") is False


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


class TestStaleCookieDiagnosticHint:
    """_try_resolve()'s DownloadError handler appends a diagnostic hint when
    a stream cookiejar exists and the error text looks auth-related. These
    tests call _try_resolve() directly (bypassing resolve_sync()'s retry
    loop and its real time.sleep() delays) and locally override the
    module's autouse _no_real_cookie_detection fixture via patch(), since
    that fixture always stubs _detect_stream_cookies() to return None."""

    def _resolver_raising(self, message: str) -> StreamResolver:
        import yt_dlp

        resolver = StreamResolver()
        mock_ydl = MagicMock()
        mock_ydl.extract_info.side_effect = yt_dlp.utils.DownloadError(message)
        resolver._get_ydl = MagicMock(return_value=mock_ydl)
        return resolver

    def test_hint_present_when_cookiejar_exists_and_error_is_auth_related(self, caplog):
        resolver = self._resolver_raising("Sign in to confirm you are not a bot")

        with (
            patch(
                "ytm_player.services.stream._detect_stream_cookies",
                return_value="/fake/config/stream_cookies.txt",
            ),
            caplog.at_level(logging.WARNING, logger="ytm_player.services.stream"),
        ):
            result = resolver._try_resolve("https://example.com/watch", "vid1", 0)

        assert result is None
        assert "stream cookiejar may be stale/revoked" in caplog.text

    def test_hint_absent_when_no_cookiejar(self, caplog):
        resolver = self._resolver_raising("Sign in to confirm you are not a bot")

        with (
            patch("ytm_player.services.stream._detect_stream_cookies", return_value=None),
            caplog.at_level(logging.WARNING, logger="ytm_player.services.stream"),
        ):
            result = resolver._try_resolve("https://example.com/watch", "vid2", 0)

        assert result is None
        assert "stream cookiejar may be stale/revoked" not in caplog.text

    def test_hint_absent_when_error_is_unrelated_to_auth(self, caplog):
        resolver = self._resolver_raising("video unavailable")

        with (
            patch(
                "ytm_player.services.stream._detect_stream_cookies",
                return_value="/fake/config/stream_cookies.txt",
            ),
            caplog.at_level(logging.WARNING, logger="ytm_player.services.stream"),
        ):
            result = resolver._try_resolve("https://example.com/watch", "vid3", 0)

        assert result is None
        assert "stream cookiejar may be stale/revoked" not in caplog.text


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

    async def test_cancelled_owner_does_not_orphan_waiter(self):
        """If the owning resolve() is cancelled mid-flight, a concurrent waiter
        on the same video must not hang on the orphaned pending future."""
        import contextlib
        import threading

        resolver = StreamResolver()
        release = threading.Event()

        def blocking_resolve(video_id: str) -> StreamInfo:
            release.wait(timeout=5)
            return _make_info(video_id)

        resolver._resolve_sync = blocking_resolve  # type: ignore[method-assign]

        owner = asyncio.create_task(resolver.resolve("hangVid01"))
        # Let the owner register the pending future and enter to_thread.
        await asyncio.sleep(0.05)
        assert "hangVid01" in resolver._pending

        waiter = asyncio.create_task(resolver.resolve("hangVid01"))
        await asyncio.sleep(0.05)

        owner.cancel()

        try:
            # Both tasks must settle promptly; a hang means the waiter was
            # orphaned on a never-resolved future.
            await asyncio.wait_for(
                asyncio.gather(owner, waiter, return_exceptions=True),
                timeout=2.0,
            )
        except asyncio.TimeoutError:
            release.set()
            pytest.fail("concurrent waiter hung on an orphaned pending future")

        release.set()
        assert owner.cancelled()
        # The waiter did not hang — it settled (cancelled/errored) instead.
        assert waiter.done()
        with contextlib.suppress(BaseException):
            await waiter

    async def test_cancelled_waiter_does_not_break_owner(self):
        """Cancelling a waiter must not cancel the shared pending future out
        from under the owner — Task.cancel() propagates into an awaited bare
        future, and the owner's set_result would raise InvalidStateError."""
        import threading

        resolver = StreamResolver()
        release = threading.Event()

        def blocking_resolve(video_id: str) -> StreamInfo:
            release.wait(timeout=5)
            return _make_info(video_id)

        resolver._resolve_sync = blocking_resolve  # type: ignore[method-assign]

        owner = asyncio.create_task(resolver.resolve("shieldVid1"))
        await asyncio.sleep(0.05)
        assert "shieldVid1" in resolver._pending

        waiter = asyncio.create_task(resolver.resolve("shieldVid1"))
        await asyncio.sleep(0.05)

        waiter.cancel()
        await asyncio.sleep(0.05)  # let the cancellation land on the waiter
        release.set()

        # The owner must complete its successful resolve untouched.
        info = await asyncio.wait_for(owner, timeout=2.0)
        assert info is not None
        assert info.video_id == "shieldVid1"
        assert waiter.cancelled()
