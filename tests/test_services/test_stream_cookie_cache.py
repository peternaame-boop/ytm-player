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

import http.cookiejar
import sys
import time
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

import ytm_player.services.stream as stream_mod
from ytm_player.config.settings import Settings
from ytm_player.services.stream import (
    StreamResolver,
    _extract_and_cache_cookiefile,
    _fresh_cached_cookiefile,
    _YtDlpLogger,
    claim_cookie_extraction_notification,
    looks_like_js_solver_ready,
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

    def test_exact_boundary_age_is_stale(self, tmp_path, monkeypatch):
        """age == _STREAM_COOKIES_MAX_AGE_SECONDS exactly — the >= boundary
        itself, not "well past" or "just under" — must be treated as
        stale. time.time() is pinned so the comparison is exact rather
        than depending on how fast this test executes after os.utime()."""
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        cookiefile = tmp_path / "stream_cookies_cache.txt"
        cookiefile.write_text("# cookies\n")
        import os

        fixed_now = 2_000_000_000.0
        monkeypatch.setattr("ytm_player.services.stream.time.time", lambda: fixed_now)
        boundary_time = fixed_now - stream_mod._STREAM_COOKIES_MAX_AGE_SECONDS
        os.utime(cookiefile, (boundary_time, boundary_time))
        assert _fresh_cached_cookiefile() is None


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

    def test_does_not_block_when_lock_contended(self, tmp_path, monkeypatch):
        """Must never block -- it's called synchronously from the Textual
        event loop, and blocking on a lock held by an in-progress
        background extraction would freeze the whole UI for the
        extraction's full duration. acquire(blocking=False) must return
        immediately (False) rather than waiting for the lock to free up."""
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        stream_mod._stream_cookies_lock.acquire()
        try:
            assert claim_cookie_extraction_notification() is False
        finally:
            stream_mod._stream_cookies_lock.release()


class TestDetectStreamCookies:
    def test_reuses_fresh_cache_without_probing_browser(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        cookiefile = tmp_path / "stream_cookies_cache.txt"
        cookiefile.write_text("# cookies\n")

        with patch("ytm_player.services.auth.AuthManager.detect_browser") as mock_detect:
            browser, path = stream_mod._detect_stream_cookies()

        mock_detect.assert_not_called()
        assert browser is None  # not re-probed — nothing to report as "detected"
        assert path == str(cookiefile)

    def test_extracts_and_caches_when_no_fresh_file(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)

        with (
            patch(
                "ytm_player.services.auth.AuthManager.detect_browser",
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
            "ytm_player.services.auth.AuthManager.detect_browser", return_value=None
        ) as mock_detect:
            stream_mod._detect_stream_cookies()
            stream_mod._detect_stream_cookies()

        mock_detect.assert_called_once()

    def test_browser_detection_failure_is_swallowed(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        with patch(
            "ytm_player.services.auth.AuthManager.detect_browser",
            side_effect=RuntimeError("keychain locked"),
        ):
            browser, path = stream_mod._detect_stream_cookies()
        assert browser is None
        assert path is None


def _cookie(domain: str, name: str = "cookie") -> http.cookiejar.Cookie:
    """Build a minimal real Cookie for a given domain (used to build real
    jars below — a MagicMock jar can't be iterated/filtered the way
    _extract_and_cache_cookiefile's domain scoping requires)."""
    return http.cookiejar.Cookie(
        version=0,
        name=name,
        value="value",
        port=None,
        port_specified=False,
        domain=domain,
        domain_specified=True,
        domain_initial_dot=domain.startswith("."),
        path="/",
        path_specified=True,
        secure=False,
        expires=None,
        discard=True,
        comment=None,
        comment_url=None,
        rest={},
    )


def _make_jar(*domains: str):
    from yt_dlp.cookies import YoutubeDLCookieJar

    jar = YoutubeDLCookieJar()
    for i, domain in enumerate(domains):
        jar.set_cookie(_cookie(domain, name=f"cookie{i}"))
    return jar


class TestExtractAndCacheCookiefile:
    def test_success_writes_secure_file_scoped_to_youtube_google(self, tmp_path, monkeypatch):
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        expected_path = tmp_path / "stream_cookies_cache.txt"
        jar = _make_jar(
            ".youtube.com", "accounts.google.com", ".chase.com", "unrelated-shop.example"
        )

        with patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=jar) as mock_extract:
            result = _extract_and_cache_cookiefile("vivaldi")

        assert result == str(expected_path)
        mock_extract.assert_called_once()
        # A logger was passed so yt-dlp's own diagnostics reach ytm.log
        # instead of being dropped by its default stdout-only logger.
        assert isinstance(mock_extract.call_args.kwargs["logger"], _YtDlpLogger)
        assert expected_path.exists()
        content = expected_path.read_text()
        # Persisted: youtube.com/google.com family, needed for yt-dlp auth.
        assert "youtube.com" in content
        assert "accounts.google.com" in content
        # NOT persisted: unrelated domains from the same browser profile —
        # the whole point of scoping before writing anything to disk.
        assert "chase.com" not in content
        assert "unrelated-shop.example" not in content
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

    def test_symlink_target_is_not_followed(self, tmp_path, monkeypatch):
        """A symlink planted at the target path must not be written
        through — proves O_NOFOLLOW is actually wired in, not just
        documented. Without it, jar.save() + secure_chmod() would follow
        the symlink and overwrite + chmod whatever it points at."""
        if sys.platform == "win32":
            pytest.skip("O_NOFOLLOW is a POSIX-only defense")
        monkeypatch.setattr("ytm_player.config.paths.CONFIG_DIR", tmp_path)
        target_path = tmp_path / "stream_cookies_cache.txt"
        victim = tmp_path / "victim.txt"
        victim.write_text("do not touch")
        target_path.symlink_to(victim)
        jar = _make_jar(".youtube.com")

        with patch("yt_dlp.cookies.extract_cookies_from_browser", return_value=jar):
            result = _extract_and_cache_cookiefile("vivaldi")

        # os.open(..., O_NOFOLLOW) raises OSError on a symlink target,
        # caught by the function's own broad except -> None, and the
        # victim file must be untouched either way.
        assert result is None
        assert victim.read_text() == "do not touch"


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
        assert resolver._peek_missing_remote_components("abc12345678") is False

    def test_flag_callback_sets_it(self):
        resolver = StreamResolver()
        resolver._resolving_video_id.value = "abc12345678"
        resolver._flag_missing_remote_components()
        assert resolver._peek_missing_remote_components("abc12345678") is True

    def test_consume_returns_and_clears(self):
        resolver = StreamResolver()
        resolver._resolving_video_id.value = "abc12345678"
        resolver._flag_missing_remote_components()
        assert resolver.consume_missing_remote_components("abc12345678") is True
        assert resolver.consume_missing_remote_components("abc12345678") is False
        assert resolver._peek_missing_remote_components("abc12345678") is False

    def test_peek_does_not_clear(self):
        resolver = StreamResolver()
        resolver._resolving_video_id.value = "abc12345678"
        resolver._flag_missing_remote_components()
        assert resolver._peek_missing_remote_components("abc12345678") is True
        assert resolver._peek_missing_remote_components("abc12345678") is True  # still true

    def test_different_video_ids_are_independent(self):
        """The whole point of keying by video_id: a flag raised for one
        concurrent resolve must not be consumable by, or leak into, a
        different video's resolve."""
        resolver = StreamResolver()
        resolver._resolving_video_id.value = "videoAAAAAA"
        resolver._flag_missing_remote_components()
        assert resolver.consume_missing_remote_components("videoBBBBBB") is False
        assert resolver.consume_missing_remote_components("videoAAAAAA") is True

    def test_flag_dropped_when_resolving_video_id_unset(self):
        """If the logger callback ever fires on a thread that never went
        through _try_resolve() (so _resolving_video_id was never set),
        there's nothing safe to attribute the flag to -- it must be
        dropped rather than guessed, and definitely not silently attached
        to some other video_id."""
        resolver = StreamResolver()
        # Deliberately not setting resolver._resolving_video_id.value.
        resolver._flag_missing_remote_components()
        assert resolver._missing_remote_components_video_ids == set()


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
            resolver._resolving_video_id.value = video_id
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

    def test_another_build_winning_mid_flight_is_reused_not_rebuilt(self):
        """The OTHER half of the "brief double-checked-locking race" the
        docstring calls out as harmless: a second caller's unlocked
        _build_ydl_opts() finishes and assigns self._ydl WHILE this
        caller's own (unlocked) build is also in flight. This caller must
        return the winner's instance and never construct its own -- not
        just "eventually get an instance" (test_reset_mid_build_forces_a_retry
        covers the generation-mismatch path; this covers the plain
        already-assigned path, which is a different branch in _get_ydl)."""
        resolver = StreamResolver()
        winner_instance = MagicMock()

        def fake_build_opts():
            # Simulate a second thread's build finishing first and
            # winning the race while THIS build was still in flight.
            resolver._ydl = winner_instance
            return {"quiet": True}

        resolver._build_ydl_opts = fake_build_opts

        with patch("yt_dlp.YoutubeDL") as mock_ydl_class:
            result = resolver._get_ydl()

        assert result is winner_instance
        mock_ydl_class.assert_not_called()

    def test_real_get_ydl_increments_active_resolves_on_each_call(self):
        """_get_ydl() owns the _active_resolves increment now (moved out of
        _try_resolve() to close a TOCTOU gap) -- assert it against the REAL
        method, not a fake that manually increments on its caller's behalf
        (see TestActiveResolvesCounter, which necessarily fakes _get_ydl
        itself to isolate _try_resolve)."""
        resolver = StreamResolver()
        resolver._build_ydl_opts = MagicMock(return_value={"quiet": True})

        with patch("yt_dlp.YoutubeDL", return_value=MagicMock()):
            resolver._get_ydl()
            assert resolver._active_resolves == 1
            resolver._get_ydl()  # cached-instance fast path also increments
            assert resolver._active_resolves == 2


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

        def fake_get_ydl():
            # _get_ydl() now increments _active_resolves itself, atomically
            # with handing out the reference — see _get_ydl's docstring.
            resolver._active_resolves += 1
            return fake_ydl

        resolver._get_ydl = fake_get_ydl

        resolver._try_resolve("https://example.com", "abc12345678", 0)

        assert observed_during_call == [1]
        assert resolver._active_resolves == 0

    def test_active_resolves_decrements_even_on_exception(self):
        resolver = StreamResolver()
        fake_ydl = MagicMock()
        fake_ydl.extract_info.side_effect = RuntimeError("boom")

        def fake_get_ydl():
            resolver._active_resolves += 1
            return fake_ydl

        resolver._get_ydl = fake_get_ydl

        resolver._try_resolve("https://example.com", "abc12345678", 0)

        assert resolver._active_resolves == 0

    def test_get_ydl_raising_leaves_active_resolves_untouched(self):
        """If _get_ydl() itself raises (opts build or the YoutubeDL()
        constructor failing) -- as opposed to extract_info() raising --
        no increment ever happened, so _try_resolve()'s finally must not
        attempt a decrement either. This is exactly the ordering
        _resolving_video_id.value is set before _get_ydl() to protect:
        nothing between the increment and its guaranteed decrement."""
        resolver = StreamResolver()
        resolver._get_ydl = MagicMock(side_effect=RuntimeError("boom"))

        result = resolver._try_resolve("https://example.com", "abc12345678", 0)

        assert result is None
        assert resolver._active_resolves == 0


class TestLooksLikeJsSolverReady:
    """looks_like_js_solver_ready() is a fast, local-only, no-network
    check — previously only ever exercised indirectly via
    monkeypatch.setattr replacements in test_session.py /
    test_session_round_trip.py. These tests invoke the real
    implementation directly."""

    def test_true_when_remote_components_already_configured(self, monkeypatch):
        settings = Settings()
        settings.yt_dlp.remote_components = "ejs:github"
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        assert looks_like_js_solver_ready() is True

    def test_true_when_solver_cache_dir_present_and_nonempty(self, tmp_path, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        solver_dir = tmp_path / "yt-dlp" / "challenge-solver"
        solver_dir.mkdir(parents=True)
        (solver_dir / "solver.js").write_text("// cached solver\n")
        assert looks_like_js_solver_ready() is True

    def test_false_when_solver_cache_dir_missing(self, tmp_path, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        assert looks_like_js_solver_ready() is False

    def test_false_when_solver_cache_dir_empty(self, tmp_path, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))
        solver_dir = tmp_path / "yt-dlp" / "challenge-solver"
        solver_dir.mkdir(parents=True)
        assert looks_like_js_solver_ready() is False

    def test_false_on_filesystem_error(self, tmp_path, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        monkeypatch.setenv("XDG_CACHE_HOME", str(tmp_path))

        def boom(self):
            raise OSError("permission denied")

        monkeypatch.setattr(Path, "is_dir", boom)
        assert looks_like_js_solver_ready() is False

    def test_uses_default_cache_home_when_env_var_unset(self, tmp_path, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        monkeypatch.delenv("XDG_CACHE_HOME", raising=False)
        fake_home = tmp_path / "fake_home"
        solver_dir = fake_home / ".cache" / "yt-dlp" / "challenge-solver"
        solver_dir.mkdir(parents=True)
        (solver_dir / "solver.js").write_text("// cached\n")
        if sys.platform == "win32":
            monkeypatch.setenv("USERPROFILE", str(fake_home))
        else:
            monkeypatch.setenv("HOME", str(fake_home))
        assert looks_like_js_solver_ready() is True

    def test_respects_custom_xdg_cache_home_over_default(self, tmp_path, monkeypatch):
        """A non-default XDG_CACHE_HOME must be honored. The *default*
        (~/.cache) home is pointed at an empty directory with no solver
        cache, so a True result can only be explained by actually reading
        XDG_CACHE_HOME rather than silently falling back to the default."""
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)

        fake_home = tmp_path / "fake_home"
        fake_home.mkdir()
        if sys.platform == "win32":
            monkeypatch.setenv("USERPROFILE", str(fake_home))
        else:
            monkeypatch.setenv("HOME", str(fake_home))

        custom_cache = tmp_path / "custom_cache"
        solver_dir = custom_cache / "yt-dlp" / "challenge-solver"
        solver_dir.mkdir(parents=True)
        (solver_dir / "solver.js").write_text("// cached\n")
        monkeypatch.setenv("XDG_CACHE_HOME", str(custom_cache))

        assert looks_like_js_solver_ready() is True


class TestBuildYdlOptsCookieInjection:
    """_build_ydl_opts()'s cookie-injection logic — this PR's headline
    change — had no direct test; every existing test in this file targets
    _detect_stream_cookies, _extract_and_cache_cookiefile,
    claim_cookie_extraction_notification, _YtDlpLogger, or
    _get_ydl/_reset_ydl in isolation, none of them assert what actually
    ends up in the opts dict."""

    def test_injects_cookiefile_when_cache_available(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch(
            "ytm_player.services.stream._detect_stream_cookies",
            return_value=("vivaldi", "/fake/path/stream_cookies_cache.txt"),
        ) as mock_detect:
            opts = StreamResolver()._build_ydl_opts()
        mock_detect.assert_called_once()
        assert opts["cookiefile"] == "/fake/path/stream_cookies_cache.txt"
        assert "cookiesfrombrowser" not in opts

    def test_falls_back_to_cookiesfrombrowser_when_no_cached_file(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch(
            "ytm_player.services.stream._detect_stream_cookies",
            return_value=("vivaldi", None),
        ) as mock_detect:
            opts = StreamResolver()._build_ydl_opts()
        mock_detect.assert_called_once()
        assert opts["cookiesfrombrowser"] == ("vivaldi", None, None, None)
        assert "cookiefile" not in opts

    def test_no_browser_found_injects_nothing(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch(
            "ytm_player.services.stream._detect_stream_cookies",
            return_value=(None, None),
        ) as mock_detect:
            opts = StreamResolver()._build_ydl_opts()
        mock_detect.assert_called_once()
        assert "cookiefile" not in opts
        assert "cookiesfrombrowser" not in opts

    def test_skips_detection_when_cookies_file_already_configured(self, monkeypatch):
        settings = Settings()
        settings.yt_dlp.cookies_file = "/home/user/manual_cookies.txt"
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch("ytm_player.services.stream._detect_stream_cookies") as mock_detect:
            opts = StreamResolver()._build_ydl_opts()
        mock_detect.assert_not_called()
        from ytm_player.services.yt_dlp_options import normalize_cookiefile

        assert opts["cookiefile"] == normalize_cookiefile("/home/user/manual_cookies.txt")
