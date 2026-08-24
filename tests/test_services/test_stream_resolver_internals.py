"""Tests for stream.py's stream-cookiejar existence check, the
remote_components missing-solver detection, and the StreamResolver
concurrency fixes around resetting a live YoutubeDL instance mid-resolve.

All of these guard against bugs confirmed in practice during development:
- A reset landing while a slow options-build was in flight being silently
  lost, permanently caching a stale YoutubeDL instance.
- Closing a live YoutubeDL instance out from under an in-flight resolve,
  freezing the UI thread.
- Missing remote_components being reported as a generic resolve failure
  instead of a diagnosable, fixable cause.
"""

from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

from ytm_player.config.settings import Settings
from ytm_player.services.stream import (
    StreamResolver,
    _detect_stream_cookies,
    _YtDlpLogger,
    looks_like_js_solver_ready,
)


class TestDetectStreamCookies:
    def test_returns_path_when_file_exists(self, tmp_path, monkeypatch):
        cookiefile = tmp_path / "stream_cookies.txt"
        cookiefile.write_text("# cookies\n")
        monkeypatch.setattr("ytm_player.config.paths.STREAM_COOKIES_FILE", cookiefile)
        assert _detect_stream_cookies() == str(cookiefile)

    def test_returns_none_when_file_missing(self, tmp_path, monkeypatch):
        cookiefile = tmp_path / "stream_cookies.txt"
        monkeypatch.setattr("ytm_player.config.paths.STREAM_COOKIES_FILE", cookiefile)
        assert _detect_stream_cookies() is None


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
    """_build_ydl_opts()'s cookie-injection logic had no direct test
    before this file; every existing test in this file targets
    _detect_stream_cookies, _YtDlpLogger, or _get_ydl/_reset_ydl in
    isolation, none of them assert what actually ends up in the opts
    dict."""

    def test_injects_cookiefile_when_cache_available(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch(
            "ytm_player.services.stream._detect_stream_cookies",
            return_value="/fake/path/stream_cookies.txt",
        ) as mock_detect:
            opts = StreamResolver()._build_ydl_opts()
        mock_detect.assert_called_once()
        assert opts["cookiefile"] == "/fake/path/stream_cookies.txt"
        assert "cookiesfrombrowser" not in opts

    def test_no_cookiejar_file_injects_nothing(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch(
            "ytm_player.services.stream._detect_stream_cookies",
            return_value=None,
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


class TestClientAndFormatSelection:
    """Regression guard for the client/format values settled on after
    three rounds of real-playback validation:

    1. Villoh's PR #136 review moved off ["default","android"]/bestaudio
       (4/12 success) to web_safari/web_embedded with a combined-format
       selector (24/24 success at the time).
    2. Villoh's later, more rigorous testing found web_safari/web_embedded
       erratic in practice (16/30) versus PR #137's tv_downgraded (30/30).
       Independent validation here (direct HTTP Range-request probing
       against a 25-track sample of real play history — the actual access
       pattern mpv uses, which yt-dlp's own downloader doesn't reproduce)
       confirmed it: web_safari/web_embedded resolves formats fine but GVS
       immediately 403s the Range request (1/25 succeeded); tv_downgraded
       needs tv_simply paired ahead of it to extract at all, but together
       they succeeded 25/25.
    3. tv_simply has SUPPORTS_COOKIES=False, so an authenticated request
       (real session cookies) gets it silently dropped by yt-dlp, leaving
       tv_downgraded alone — but this does NOT reproduce the unauthenticated
       tv_downgraded-alone failure, because yt-dlp auto-appends `web_music`
       for any authenticated music.youtube.com request. That auto-added
       client exposes real audio-only formats up to ~280kbps opus, so
       `bestaudio` was reinstated at the front of the selector — reversing
       a prior finding that held bestaudio always PO-Token-gated, which had
       never been tested against web_music specifically. Validated against
       18 real, distinct tracks from real play history with two Range
       probes each (immediate and 2MB into the file): 18/18 succeeded —
       under a YouTube Music Premium account specifically (Premium is
       exempt from web_music's PO-Token requirement; non-Premium
       authenticated accounts aren't covered by this validation and may
       see the same GVS-403 failures as web_safari/web_embedded above).
       See CHANGELOG.md for the full investigation.

    This client/format selection already regressed once before (commit
    8450505 -> 3efbd66) with nothing to catch it; these tests exist so a
    future refactor can't silently do the same."""

    def test_player_client_is_the_validated_pair(self, monkeypatch):
        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch("ytm_player.services.stream._detect_stream_cookies", return_value=None):
            opts = StreamResolver()._build_ydl_opts()
        assert opts["extractor_args"]["youtube"]["player_client"] == [
            "tv_simply",
            "tv_downgraded",
        ]

    def test_format_prefers_bestaudio_with_a_combined_fallback(self, monkeypatch):
        """bestaudio is deliberately first: validated as Range-safe for
        authenticated resolves on a Premium account (web_music auto-append
        supplies it; non-Premium authenticated accounts aren't covered —
        see class docstring), and a no-op when unauthenticated
        (tv_simply/tv_downgraded expose no audio-only format, so this
        alternative never matches and the combined fallback is used
        instead)."""
        from ytm_player.services.stream import _FORMAT

        assert _FORMAT == "bestaudio/best[vcodec!=none][acodec!=none]"

        settings = Settings()
        monkeypatch.setattr("ytm_player.services.stream.get_settings", lambda: settings)
        with patch("ytm_player.services.stream._detect_stream_cookies", return_value=None):
            opts = StreamResolver()._build_ydl_opts()
        assert opts["format"] == _FORMAT

    def test_format_is_valid_yt_dlp_selector_syntax(self):
        """Parsing through yt-dlp's own selector grammar catches composition
        mistakes (unbalanced brackets, bad operators) at negligible cost —
        no network I/O, pure syntax check."""
        import yt_dlp

        from ytm_player.services.stream import _FORMAT

        ydl = yt_dlp.YoutubeDL({"quiet": True})
        ydl.build_format_selector(_FORMAT)  # raises SyntaxError if malformed
