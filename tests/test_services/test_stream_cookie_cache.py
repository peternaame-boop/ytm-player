"""Tests for stream.py's cross-restart cookie caching, the remote_components
missing-solver detection, and the StreamResolver concurrency fixes around
resetting a live YoutubeDL instance mid-resolve.

All of these guard against bugs confirmed in practice during development:
- Re-decrypting a whole browser cookie store on every relaunch (and on
  every clear_cache()) when a valid cookiefile was already on disk.
- Two concurrent resolves both extracting cookies at once, contending for
  the OS keychain.
- A reset landing while a slow options-build was in flight being silently
  lost, permanently caching a stale YoutubeDL instance.
- Closing a live YoutubeDL instance out from under an in-flight resolve,
  freezing the UI thread.
- Missing remote_components being reported as a generic resolve failure
  instead of a diagnosable, fixable cause.
"""

from __future__ import annotations

import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ytm_player.services.stream as stream_mod
from ytm_player.services.stream import (
    StreamResolver,
    _extract_and_cache_cookiefile,
    _fresh_cached_cookiefile,
    _YtDlpLogger,
    claim_cookie_extraction_notification,
)


@pytest.fixture(autouse=True)
def _reset_stream_cookie_globals():
    """These are memoized at module scope for the process lifetime by
    design (see _detect_stream_cookies' docstring) — reset them around
    every test so tests don't leak state into each other."""
    stream_mod._stream_browser_probed = False
    stream_mod._cached_stream_browser = None
    stream_mod._cached_stream_cookiefile = None
    stream_mod._cookie_extraction_notify_claimed = False
    yield
    stream_mod._stream_browser_probed = False
    stream_mod._cached_stream_browser = None
    stream_mod._cached_stream_cookiefile = None
    stream_mod._cookie_extraction_notify_claimed = False


class TestFreshCachedCookiefile:
    def test_missing_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        assert _fresh_cached_cookiefile() is None

    def test_fresh_file_returns_path(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        cookiefile = tmp_path / "stream_cookies_cache.txt"
        cookiefile.write_text("# cookies\n")
        assert _fresh_cached_cookiefile() == str(cookiefile)

    def test_stale_file_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        cookiefile = tmp_path / "stream_cookies_cache.txt"
        cookiefile.write_text("# cookies\n")
        import os

        # Back-date mtime past the 1-hour freshness window.
        stale_time = time.time() - stream_mod._STREAM_COOKIES_MAX_AGE_SECONDS - 60
        os.utime(cookiefile, (stale_time, stale_time))
        assert _fresh_cached_cookiefile() is None

    def test_just_under_max_age_is_fresh(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        cookiefile = tmp_path / "stream_cookies_cache.txt"
        cookiefile.write_text("# cookies\n")
        import os

        recent_time = time.time() - stream_mod._STREAM_COOKIES_MAX_AGE_SECONDS + 60
        os.utime(cookiefile, (recent_time, recent_time))
        assert _fresh_cached_cookiefile() == str(cookiefile)


class TestClaimCookieExtractionNotification:
    def test_first_caller_claims_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        assert claim_cookie_extraction_notification() is True

    def test_second_caller_does_not_claim_it(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        assert claim_cookie_extraction_notification() is True
        assert claim_cookie_extraction_notification() is False

    def test_no_claim_once_already_probed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        stream_mod._stream_browser_probed = True
        assert claim_cookie_extraction_notification() is False

    def test_no_claim_when_fresh_cache_already_exists(self, tmp_path, monkeypatch):
        """Nothing slow is about to happen — _detect_stream_cookies() will
        just reuse the file — so there's nothing to warn about even though
        this is the first call this process has made."""
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        (tmp_path / "stream_cookies_cache.txt").write_text("# cookies\n")
        assert claim_cookie_extraction_notification() is False


class TestDetectStreamCookies:
    def test_reuses_fresh_cache_without_probing_browser(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        cookiefile = tmp_path / "stream_cookies_cache.txt"
        cookiefile.write_text("# cookies\n")

        with patch("ytm_player.services.auth.AuthManager._detect_browser") as mock_detect:
            browser, path = stream_mod._detect_stream_cookies()

        mock_detect.assert_not_called()
        assert browser is None  # not re-probed — nothing to report as "detected"
        assert path == str(cookiefile)

    def test_extracts_and_caches_when_no_fresh_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)

        with (
            patch(
                "ytm_player.services.auth.AuthManager._detect_browser",
                return_value="vivaldi",
            ) as mock_detect,
            patch(
                "ytm_player.services.stream._extract_and_cache_cookiefile",
                return_value=str(tmp_path / "stream_cookies_cache.txt"),
            ) as mock_extract,
        ):
            browser, path = stream_mod._detect_stream_cookies()

        mock_detect.assert_called_once()
        mock_extract.assert_called_once_with("vivaldi")
        assert browser == "vivaldi"
        assert path == str(tmp_path / "stream_cookies_cache.txt")

    def test_memoized_for_process_lifetime(self, tmp_path, monkeypatch):
        """A second call within the same process must not re-probe, even
        if the first call found no browser at all."""
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)

        with patch(
            "ytm_player.services.auth.AuthManager._detect_browser", return_value=None
        ) as mock_detect:
            stream_mod._detect_stream_cookies()
            stream_mod._detect_stream_cookies()

        mock_detect.assert_called_once()

    def test_browser_detection_failure_is_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        with patch(
            "ytm_player.services.auth.AuthManager._detect_browser",
            side_effect=RuntimeError("keychain locked"),
        ):
            browser, path = stream_mod._detect_stream_cookies()
        assert browser is None
        assert path is None


class TestExtractAndCacheCookiefile:
    def test_success_writes_secure_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        expected_path = tmp_path / "stream_cookies_cache.txt"
        mock_jar = MagicMock()
        # secure_chmod() needs a real file on disk to chmod — the real
        # jar.save() would have created it; simulate that here.
        mock_jar.save.side_effect = lambda path, **_kwargs: Path(path).write_text("# cookies\n")

        with patch(
            "yt_dlp.cookies.extract_cookies_from_browser", return_value=mock_jar
        ) as mock_extract:
            result = _extract_and_cache_cookiefile("vivaldi")

        assert result == str(expected_path)
        mock_extract.assert_called_once()
        # A logger was passed so yt-dlp's own diagnostics reach ytm.log
        # instead of being dropped by its default stdout-only logger.
        assert isinstance(mock_extract.call_args.kwargs["logger"], _YtDlpLogger)
        mock_jar.save.assert_called_once_with(
            str(expected_path), ignore_discard=True, ignore_expires=True
        )
        assert expected_path.exists()
        if sys.platform != "win32":
            assert oct(expected_path.stat().st_mode)[-3:] == "600"

    def test_failure_returns_none(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        with patch(
            "yt_dlp.cookies.extract_cookies_from_browser",
            side_effect=FileNotFoundError("no cookies db"),
        ):
            result = _extract_and_cache_cookiefile("vivaldi")
        assert result is None


class TestYtDlpLoggerMissingRemoteComponents:
    def test_remote_components_skipped_message_triggers_callback(self):
        callback = MagicMock()
        logger_obj = _YtDlpLogger(callback)
        logger_obj.warning(
            "Some remote components have not been downloaded and have been skipped. "
            "You can enable these downloads with --remote-components ejs:github"
        )
        callback.assert_called_once()

    def test_challenge_solving_failed_message_triggers_callback(self):
        callback = MagicMock()
        logger_obj = _YtDlpLogger(callback)
        logger_obj.warning("Signature challenge solving failed for player abc123")
        callback.assert_called_once()

    def test_unrelated_warning_does_not_trigger_callback(self):
        callback = MagicMock()
        logger_obj = _YtDlpLogger(callback)
        logger_obj.warning('Skipping client "android" since it does not support cookies')
        callback.assert_not_called()

    def test_no_callback_configured_does_not_raise(self):
        logger_obj = _YtDlpLogger()
        # Must not raise even though there's no callback to invoke.
        logger_obj.warning("remote components have not been downloaded, skipped")


class TestMissingRemoteComponentsFlag:
    def test_flag_starts_false(self):
        resolver = StreamResolver()
        assert resolver.missing_remote_components is False

    def test_flag_callback_sets_it(self):
        resolver = StreamResolver()
        resolver._flag_missing_remote_components()
        assert resolver.missing_remote_components is True

    def test_consume_returns_and_clears(self):
        resolver = StreamResolver()
        resolver._flag_missing_remote_components()
        assert resolver.consume_missing_remote_components() is True
        assert resolver.consume_missing_remote_components() is False
        assert resolver.missing_remote_components is False

    def test_peek_does_not_clear(self):
        resolver = StreamResolver()
        resolver._flag_missing_remote_components()
        assert resolver.missing_remote_components is True
        assert resolver.missing_remote_components is True  # still true — peek only


class TestResolveSyncShortCircuit:
    def test_stops_retrying_after_missing_remote_components(self, monkeypatch):
        """All 3 attempts would fail identically once remote_components is
        unset — retrying burns time for nothing, so the retry loop should
        stop after the first attempt flags the cause."""
        resolver = StreamResolver()
        monkeypatch.setattr("ytm_player.services.stream.time.sleep", lambda _: None)

        call_count = 0

        def fake_try_resolve(url, video_id, attempt):
            nonlocal call_count
            call_count += 1
            resolver._flag_missing_remote_components()
            return None

        resolver._try_resolve = fake_try_resolve

        result = resolver._resolve_sync("abc12345678")

        assert result is None
        assert call_count == 1

    def test_keeps_retrying_for_ordinary_failures(self, monkeypatch):
        """Ordinary failures (no missing-remote-components signal) still
        get the full retry budget — this must not regress."""
        resolver = StreamResolver()
        monkeypatch.setattr("ytm_player.services.stream.time.sleep", lambda _: None)

        call_count = 0

        def fake_try_resolve(url, video_id, attempt):
            nonlocal call_count
            call_count += 1
            return None

        resolver._try_resolve = fake_try_resolve

        result = resolver._resolve_sync("abc12345678")

        assert result is None
        assert call_count == 3


class TestResetYdlActiveResolvesGuard:
    """_reset_ydl() must not close a YoutubeDL instance a resolve is still
    using (confirmed in practice: closing mid-request froze the UI thread
    for however long the underlying socket/read timeout took)."""

    def test_does_not_close_while_resolve_active(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        resolver._ydl = fake_ydl
        resolver._active_resolves = 1

        resolver._reset_ydl()

        fake_ydl.close.assert_not_called()
        assert resolver._ydl is None  # still detached so the next build is fresh

    def test_closes_when_no_resolve_active(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        resolver._ydl = fake_ydl
        resolver._active_resolves = 0

        resolver._reset_ydl()

        fake_ydl.close.assert_called_once()
        assert resolver._ydl is None

    def test_bumps_generation_even_when_nothing_cached(self):
        """A reset landing while self._ydl is still None (nothing built
        yet) must not be silently lost — see _get_ydl's generation check."""
        resolver = StreamResolver()
        assert resolver._ydl is None
        generation_before = resolver._ydl_generation

        resolver._reset_ydl()

        assert resolver._ydl_generation == generation_before + 1

    def test_close_exception_is_swallowed(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        fake_ydl.close.side_effect = RuntimeError("boom")
        resolver._ydl = fake_ydl
        resolver._active_resolves = 0

        # Must not raise.
        resolver._reset_ydl()
        assert resolver._ydl is None


class TestGetYdlGenerationRace:
    """A reset landing while _build_ydl_opts() is still running (slow —
    it can trigger cookie extraction) must invalidate that build instead
    of caching stale settings permanently. See _get_ydl's docstring."""

    def test_reset_mid_build_forces_a_retry(self, monkeypatch):
        resolver = StreamResolver()
        build_calls = 0

        def fake_build_opts():
            nonlocal build_calls
            build_calls += 1
            if build_calls == 1:
                # Simulate a reset (e.g. accepting the remote_components
                # prompt) landing while this "slow" build was in flight.
                resolver._reset_ydl()
            return {"quiet": True}

        resolver._build_ydl_opts = fake_build_opts

        fake_ydl_instance = MagicMock()
        with patch("yt_dlp.YoutubeDL", return_value=fake_ydl_instance) as mock_ydl_class:
            result = resolver._get_ydl()

        # First build's result was discarded; the retry's opts got used.
        assert build_calls == 2
        assert result is fake_ydl_instance
        mock_ydl_class.assert_called_once_with({"quiet": True})

    def test_no_race_builds_once(self):
        resolver = StreamResolver()
        resolver._build_ydl_opts = MagicMock(return_value={"quiet": True})

        with patch("yt_dlp.YoutubeDL", return_value=MagicMock()):
            resolver._get_ydl()
            resolver._get_ydl()  # second call hits the cached instance

        resolver._build_ydl_opts.assert_called_once()


class TestActiveResolvesCounter:
    def test_try_resolve_increments_and_decrements_around_extract_info(self):
        resolver = StreamResolver()
        observed_during_call = []

        fake_ydl = MagicMock()

        def fake_extract_info(url, download=False):
            observed_during_call.append(resolver._active_resolves)
            return {
                "url": "https://example.com/stream",
                "acodec": "opus",
                "abr": 128,
                "duration": 200,
                "ext": "webm",
            }

        fake_ydl.extract_info = fake_extract_info
        resolver._get_ydl = MagicMock(return_value=fake_ydl)

        resolver._try_resolve("https://example.com", "abc12345678", 0)

        assert observed_during_call == [1]
        assert resolver._active_resolves == 0

    def test_active_resolves_decrements_even_on_exception(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        fake_ydl.extract_info.side_effect = RuntimeError("boom")
        resolver._get_ydl = MagicMock(return_value=fake_ydl)

        resolver._try_resolve("https://example.com", "abc12345678", 0)

        assert resolver._active_resolves == 0
